"""観点7: 抽出済みの肺領域データの読み込み（Qt 非依存）。

対応形式: DICOM フォルダ / PNG 連番フォルダ / .npy
読み込んだデータは、3D 表示用の 8bit スカラー（0 = 背景＝透明、1〜255 = 濃度）に変換する。

  - 二値マスク（0/1 など2値）      → 前景を 255 にする（kind = "mask"）
  - HU が残っているデータ（DICOM） → 肺野向けの窓 [-1000, 400] HU を 1〜255 にする（kind = "intensity"）
  - 8bit の濃度画像（PNG など）     → 値をそのまま使う（0 が背景）。第5回の PNG は、
                                    HU の窓 [-1000, 400] を 0〜255 にしたものだった
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image

from . import dicom_loader
from .dicom_loader import DicomLoadError, LoadCancelled, ProgressCallback, SeriesInfo, Volume

# 肺野向けの窓 [HU]。HU → 8bit スカラーの変換と、PNG の値の解釈に使う
LUNG_HU_MIN, LUNG_HU_MAX = -1000.0, 400.0

__all__ = [
    "LoadCancelled",
    "LungData",
    "LungLoadError",
    "RawLung",
    "build_lung_data",
    "detect_flip_with_ct",
    "find_png_files",
    "load_dicom_lung",
    "load_npy",
    "load_png_series",
    "looks_like_mask",
]


class LungLoadError(DicomLoadError):
    """肺領域データの読み込みに失敗した（メッセージはそのまま画面に表示できる日本語）。"""


@dataclass
class RawLung:
    """読み込んだ直後のデータ。向き（z の順序）と画素間隔は、まだ確定していない。"""

    array: np.ndarray  # (nz, ny, nx)。保存されていた順序のまま
    source: str  # "DICOM" / "PNG" / "npy"
    description: str  # 例: "PNG 133 枚（512×512）"
    spacing: Optional[tuple[float, float, float]] = None  # (x, y, z) [mm]。DICOM のみタグから得られる
    is_hu: bool = False  # 画素値が HU（DICOM）か
    default_flip: bool = False  # 先頭スライスが頭側として扱うのが既定か


@dataclass
class LungData:
    """3D 表示に渡すデータ。"""

    volume: np.ndarray  # uint8 (nz, ny, nx)。z は昇順（足側 → 頭側）。0 = 背景
    spacing: tuple[float, float, float]  # (x, y, z) [mm]
    kind: str  # "mask"（二値マスク）または "intensity"（濃度値）
    label: str  # 表示用の説明

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.volume.shape


# ---------------------------------------------------------------- 読み込み
def _natural_key(path: str) -> list:
    """slice_2 < slice_10 になる並べ方（数字部分を数として比較）。"""
    name = os.path.basename(path).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def find_png_files(folder: str) -> list[str]:
    """フォルダ直下の PNG を番号順に返す。無ければ、サブフォルダのうち PNG が最も多いフォルダの PNG。"""

    def pngs_in(directory: str) -> list[str]:
        try:
            names = os.listdir(directory)
        except OSError:
            return []
        return [
            os.path.join(directory, n)
            for n in names
            if n.lower().endswith(".png") and os.path.isfile(os.path.join(directory, n))
        ]

    found = pngs_in(folder)
    if not found:
        best: list[str] = []
        for root, _dirs, _names in os.walk(folder):
            candidates = pngs_in(root)
            if len(candidates) > len(best):
                best = candidates
        found = best
    return sorted(found, key=_natural_key)


def _png_to_array(image: Image.Image) -> np.ndarray:
    if image.mode in ("I;16", "I;16B", "I;16L", "I"):
        return np.asarray(image).astype(np.uint16)  # 16bit はそのまま。後で 8bit に正規化する
    return np.asarray(image.convert("L"))  # 1bit / パレット / RGB(A) などは 8bit グレーにする


def load_png_series(folder: str, progress: Optional[ProgressCallback] = None) -> RawLung:
    """PNG 連番（1ファイル = 1スライス）を読み込む。"""
    paths = find_png_files(folder)
    if not paths:
        raise LungLoadError("選択したフォルダに PNG ファイルが見つかりませんでした。")
    n = len(paths)
    vol: Optional[np.ndarray] = None
    for i, path in enumerate(paths):
        if progress:
            progress(i, n)  # 中止（LoadCancelled）はここから送出される
        try:
            with Image.open(path) as image:
                arr = _png_to_array(image)
        except Exception as exc:
            raise LungLoadError(
                f"{os.path.basename(path)} を画像として読み込めませんでした。\n{exc}"
            ) from exc
        if vol is None:
            vol = np.empty((n,) + arr.shape, dtype=arr.dtype)
        elif arr.shape != vol.shape[1:] or arr.dtype != vol.dtype:
            raise LungLoadError(
                f"{os.path.basename(path)} の大きさ・形式が他のファイルと異なります"
                f"（{arr.shape[1]}×{arr.shape[0]} / 他は {vol.shape[2]}×{vol.shape[1]}）。"
            )
        vol[i] = arr
    if progress:
        progress(n, n)
    return RawLung(
        array=vol,
        source="PNG",
        description=f"PNG {n} 枚（{vol.shape[2]}×{vol.shape[1]}）",
        default_flip=True,  # 第5回のデータは、先頭（slice_001）が頭側の並び
    )


def load_npy(path: str) -> RawLung:
    """.npy（3次元配列 [z, y, x]）を読み込む。"""
    try:
        arr = np.load(path, allow_pickle=False)  # pickle は読み込まない（任意コード実行の防止）
    except Exception as exc:
        raise LungLoadError(f"{os.path.basename(path)} を読み込めませんでした。\n{exc}") from exc
    if arr.ndim != 3:
        raise LungLoadError(
            f"{os.path.basename(path)} は 3 次元配列ではありません（形状: {arr.shape}）。\n"
            "[スライス, 縦, 横] の 3 次元配列が必要です。"
        )
    if arr.dtype == np.bool_:
        arr = arr.astype(np.uint8)
    if arr.dtype.kind not in "uif":
        raise LungLoadError(f"数値以外の配列は読み込めません（dtype: {arr.dtype}）。")
    if arr.dtype.kind == "f":
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    nz, ny, nx = arr.shape
    return RawLung(array=arr, source="npy", description=f".npy {nx}×{ny}×{nz}（{arr.dtype}）")


def load_dicom_lung(series: SeriesInfo, progress: Optional[ProgressCallback] = None) -> RawLung:
    """DICOM（メインのローダーを流用）。間隔はタグから得る。"""
    vol = dicom_loader.load_volume(series, progress)
    return RawLung(
        array=vol.data,
        source="DICOM",
        description=f"DICOM {series.count} 枚（{series.cols}×{series.rows}）",
        spacing=vol.spacing,
        is_hu=True,
    )


# ---------------------------------------------------------------- 変換
def _is_binary(arr: np.ndarray) -> bool:
    """2値（最小値と最大値だけ）か。0/1 でも 0/255 でも、前景は最大値側とする。"""
    lo, hi = arr.min(), arr.max()
    return bool(lo != hi and np.all((arr == lo) | (arr == hi)))


def looks_like_mask(raw: RawLung) -> bool:
    """読み込んだデータが二値マスク（2値のみ）か。"""
    return _is_binary(raw.array)


def _background_value(arr: np.ndarray):
    """最も多い値（間引いて数える）。全体の 3 割以上を占めるときだけ背景とみなす。無ければ None。"""
    sub = arr[::4, ::4, ::4]
    values, counts = np.unique(sub, return_counts=True)
    top = counts.argmax()
    return values[top] if counts[top] >= 0.3 * sub.size else None


def _hu_to_scalar(hu: np.ndarray, background) -> np.ndarray:
    """肺野の窓 [-1000, 400] HU を 1〜255 にする。背景は 0。スライスごとに処理して一時メモリを抑える。"""
    out = np.empty(hu.shape, dtype=np.uint8)
    scale = 255.0 / (LUNG_HU_MAX - LUNG_HU_MIN)
    for z in range(hu.shape[0]):
        x = (hu[z].astype(np.float32) - LUNG_HU_MIN) * scale
        np.clip(x, 1.0, 255.0, out=x)
        out[z] = x.astype(np.uint8)
        if background is not None:
            out[z][hu[z] == background] = 0
    return out


def _to_scalar(raw_array: np.ndarray, is_hu: bool) -> np.ndarray:
    """濃度値のデータを、0 = 背景の 8bit スカラーにする。"""
    bg = _background_value(raw_array)
    if is_hu or (raw_array.dtype != np.uint8 and raw_array.min() < -100):
        return _hu_to_scalar(raw_array, bg)
    if raw_array.dtype == np.uint8:
        return np.ascontiguousarray(raw_array)  # 8bit の濃度画像は、値をそのまま使う（0 が背景）
    # それ以外（16bit PNG・非負の float など）は、最大値が 255 になるよう正規化する。背景は 0
    peak = float(raw_array.max())
    x = np.clip(raw_array.astype(np.float32) * (255.0 / peak), 1.0, 255.0).astype(np.uint8)
    x[raw_array == (0 if bg is None else bg)] = 0
    return x


def detect_flip_with_ct(raw: RawLung, ct: Volume) -> Optional[bool]:
    """メインで開いた CT と大きさが同じなら、CT と照合して z を反転すべきかを判定する。

    マスク（前景）の内側の CT 値の平均は、正しい向きなら肺の値（約 -800 HU）になり、
    ずれていれば軟部組織などが混ざって高くなる。判定できなければ None。
    """
    arr = raw.array
    if arr.shape != ct.shape:
        return None
    bg = _background_value(arr)
    fg = (arr != bg) if bg is not None else (arr > arr.min())
    if fg.sum() < 1000:
        return None
    mean_as_is = float(ct.data[fg].mean())
    mean_flipped = float(ct.data[fg[::-1]].mean())
    if abs(mean_as_is - mean_flipped) < 50.0:  # 差が小さいときは判断しない
        return None
    return mean_flipped < mean_as_is


def build_lung_data(
    raw: RawLung,
    spacing: tuple[float, float, float],
    flip_z: bool,
    ct: Optional[Volume] = None,
    use_ct: bool = False,
) -> LungData:
    """3D 表示用のデータにする。

    flip_z: 先頭スライスが頭側のとき True（z を反転して、足側 → 頭側の昇順にそろえる）。
    use_ct: 二値マスクのとき、メインで開いた CT の濃度でマスク内を描画する（マスク × CT）。
    """
    arr = raw.array[::-1] if flip_z else raw.array
    if _is_binary(arr):
        foreground = arr == arr.max()
        if use_ct and ct is not None and ct.shape == arr.shape:
            scalar = _hu_to_scalar(ct.data, None)
            scalar[~foreground] = 0
            kind, note = "intensity", "マスク × CT"
        else:
            scalar = np.where(foreground, 255, 0).astype(np.uint8)
            kind, note = "mask", "二値マスク"
    else:
        scalar = _to_scalar(arr, raw.is_hu)
        kind, note = "intensity", "濃度値"
    if not scalar.any():
        raise LungLoadError("肺領域（背景以外の画素）が見つかりませんでした。")
    return LungData(
        volume=np.ascontiguousarray(scalar),
        spacing=spacing,
        kind=kind,
        label=f"{raw.description} / {note}",
    )
