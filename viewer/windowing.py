"""ウィンドウ処理（WL/WW による HU → 8bit 輝度変換）。"""
import numpy as np

# 初期値（肺野条件）
DEFAULT_WL = -600
DEFAULT_WW = 1500


def apply_window(hu: np.ndarray, wl: float, ww: float) -> np.ndarray:
    """HU の2D配列を uint8 の輝度画像に変換する。

    I = clip((HU - (WL - WW/2)) / WW * 255, 0, 255)
    """
    ww = max(float(ww), 1.0)  # 0 除算を避ける（WW の最小値は 1）
    lower = float(wl) - ww / 2.0
    img = (hu.astype(np.float32) - lower) / ww * 255.0
    np.clip(img, 0.0, 255.0, out=img)
    return img.astype(np.uint8)
