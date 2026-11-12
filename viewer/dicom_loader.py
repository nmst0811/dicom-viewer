"""DICOM フォルダの走査とボリューム読み込み（Qt 非依存）。

流れ: scan_series() でフォルダ内の DICOM をシリーズごとに分類
      → load_volume() で選んだシリーズを vol[z, y, x] の HU 配列にする。
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pydicom

# 進捗コールバック: progress(完了数, 全体数)。中止したい場合は LoadCancelled を送出する
ProgressCallback = Callable[[int, int], None]


class DicomLoadError(Exception):
    """読み込みに失敗した（メッセージはそのまま画面に表示できる日本語）。"""


class LoadCancelled(Exception):
    """ユーザーが読み込みを中止した。"""


@dataclass
class SliceEntry:
    """1枚分のヘッダ情報（画素は読まない）。"""

    path: str
    z: Optional[float]  # ImagePositionPatient の z
    instance: Optional[int]  # InstanceNumber
    rows: int
    cols: int
    slope: float
    intercept: float
    pixel_spacing: Optional[tuple[float, float]]  # (行方向=y, 列方向=x)
    slice_thickness: Optional[float]


@dataclass
class SeriesInfo:
    uid: str
    modality: str
    description: str
    entries: list[SliceEntry]  # スライス順に並べ済み

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def rows(self) -> int:
        return self.entries[0].rows

    @property
    def cols(self) -> int:
        return self.entries[0].cols


@dataclass
class Volume:
    data: np.ndarray  # vol[z, y, x]、単位は HU（int16 または float32）
    spacing: tuple[float, float, float]  # (x, y, z) [mm]

    @property
    def shape(self) -> tuple[int, int, int]:
        """(nz, ny, nx)"""
        return self.data.shape


_HEADER_TAGS = [
    "SeriesInstanceUID",
    "Modality",
    "SeriesDescription",
    "Rows",
    "Columns",
    "ImagePositionPatient",
    "InstanceNumber",
    "RescaleSlope",
    "RescaleIntercept",
    "PixelSpacing",
    "SliceThickness",
]


def _to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_header(path: str) -> Optional[tuple[str, str, str, SliceEntry]]:
    """画像 DICOM ならヘッダ情報を返す。DICOM 以外・画像でないものは None。"""
    try:
        ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_HEADER_TAGS)
    except Exception:
        return None  # DICOM ではない（または壊れている）ファイルは無視する

    rows, cols = ds.get("Rows"), ds.get("Columns")
    if not rows or not cols:
        return None  # DICOMDIR など画像ではない DICOM

    ipp = ds.get("ImagePositionPatient")
    z = _to_float(ipp[2]) if ipp is not None and len(ipp) == 3 else None

    instance = ds.get("InstanceNumber")
    try:
        instance = int(instance) if instance is not None else None
    except (TypeError, ValueError):
        instance = None

    ps = ds.get("PixelSpacing")
    pixel_spacing = None
    if ps is not None and len(ps) == 2:
        a, b = _to_float(ps[0]), _to_float(ps[1])
        if a and b and a > 0 and b > 0:
            pixel_spacing = (a, b)

    entry = SliceEntry(
        path=path,
        z=z,
        instance=instance,
        rows=int(rows),
        cols=int(cols),
        slope=_to_float(ds.get("RescaleSlope"), 1.0),
        intercept=_to_float(ds.get("RescaleIntercept"), 0.0),
        pixel_spacing=pixel_spacing,
        slice_thickness=_to_float(ds.get("SliceThickness")),
    )
    uid = str(ds.get("SeriesInstanceUID", "")) or "(SeriesInstanceUID なし)"
    return uid, str(ds.get("Modality", "")), str(ds.get("SeriesDescription", "")), entry


def _sort_entries(entries: list[SliceEntry]) -> list[SliceEntry]:
    """z（ImagePositionPatient）の昇順。無ければ InstanceNumber、それも無ければファイル名順。"""
    if all(e.z is not None for e in entries):
        return sorted(entries, key=lambda e: e.z)
    if all(e.instance is not None for e in entries):
        return sorted(entries, key=lambda e: e.instance)
    return sorted(entries, key=lambda e: e.path)


def scan_series(
    folder: str, progress: Optional[ProgressCallback] = None
) -> list[SeriesInfo]:
    """フォルダ（サブフォルダ含む）の DICOM をシリーズごとに分類する。枚数の多い順。"""
    paths = sorted(
        os.path.join(root, name)
        for root, _dirs, names in os.walk(folder)
        for name in names
    )
    groups: dict[str, list] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    total = len(paths)
    for i, path in enumerate(paths):
        if progress:
            progress(i, total)
        header = _read_header(path)
        if header is None:
            continue
        uid, modality, description, entry = header
        groups[uid].append(entry)
        meta.setdefault(uid, (modality, description))
    if progress:
        progress(total, total)

    if not groups:
        raise DicomLoadError(
            "選択したフォルダに DICOM 画像が見つかりませんでした。\n"
            "（サブフォルダも検索しています）"
        )
    series = [
        SeriesInfo(uid, meta[uid][0], meta[uid][1], _sort_entries(entries))
        for uid, entries in groups.items()
    ]
    series.sort(key=lambda s: s.count, reverse=True)
    return series


def _compute_spacing(entries: list[SliceEntry]) -> tuple[float, float, float]:
    """(x, y, z) の間隔 [mm]。z は隣接スライスの z 座標の差を優先する。"""
    pixel_spacing = next((e.pixel_spacing for e in entries if e.pixel_spacing), None)
    sy, sx = pixel_spacing if pixel_spacing else (1.0, 1.0)

    sz = None
    zs = [e.z for e in entries]
    if len(zs) >= 2 and all(z is not None for z in zs):
        diffs = np.abs(np.diff(zs))
        diffs = diffs[diffs > 1e-6]
        if diffs.size:
            sz = float(np.median(diffs))
    if sz is None:
        sz = next((e.slice_thickness for e in entries if e.slice_thickness), 1.0)
    return float(sx), float(sy), float(sz)


def load_volume(
    series: SeriesInfo, progress: Optional[ProgressCallback] = None
) -> Volume:
    """シリーズの全スライスを読み込み、HU に変換した Volume を返す。"""
    entries = series.entries
    n = len(entries)
    rows, cols = entries[0].rows, entries[0].cols
    if any(e.rows != rows or e.cols != cols for e in entries):
        raise DicomLoadError(
            "このシリーズはスライスごとに画像サイズが異なるため読み込めません。"
        )

    # HU = pixel * RescaleSlope + RescaleIntercept。整数のスケールなら int16 で保持する
    integer_rescale = all(
        float(e.slope).is_integer() and float(e.intercept).is_integer()
        for e in entries
    )
    vol = np.empty((n, rows, cols), dtype=np.int16 if integer_rescale else np.float32)

    for idx, e in enumerate(entries):
        if progress:
            progress(idx, n)  # 中止（LoadCancelled）はここから送出される
        try:
            arr = pydicom.dcmread(e.path).pixel_array
        except Exception as exc:
            raise DicomLoadError(
                f"{os.path.basename(e.path)} の画素データを読み込めませんでした。\n{exc}"
            ) from exc
        if arr.shape != (rows, cols):
            raise DicomLoadError(
                f"{os.path.basename(e.path)} は 2D のグレースケール画像ではありません"
                "（マルチフレーム・カラー画像には未対応です）。"
            )
        hu = arr.astype(np.float32) * e.slope + e.intercept
        if vol.dtype == np.int16:
            hu = np.rint(hu)
            if hu.min() < -32768 or hu.max() > 32767:
                vol = vol.astype(np.float32)  # int16 に収まらない場合のみ float32 へ
        vol[idx] = hu
    if progress:
        progress(n, n)

    return Volume(data=vol, spacing=_compute_spacing(entries))
