"""自作の画像表示ウィジェット（観点1）。

HU の2D配列を WL/WW で uint8 化 → QImage → paintEvent で描画する。
背景は黒で、物理アスペクト比（ピクセル間隔の比）を保って中央に表示する。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .windowing import DEFAULT_WL, DEFAULT_WW, apply_window


class SliceView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hu: Optional[np.ndarray] = None  # 表示中の2D配列（HU）
        self._spacing = (1.0, 1.0)  # (横, 縦) 1ピクセルの物理サイズ [mm]
        self._wl = float(DEFAULT_WL)
        self._ww = float(DEFAULT_WW)
        self._gray: Optional[np.ndarray] = None  # QImage が参照する uint8 配列（保持が必要）
        self._qimage: Optional[QImage] = None
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- 公開 API ----
    def set_slice(self, hu: np.ndarray, spacing: tuple[float, float] = (1.0, 1.0)) -> None:
        """表示する2D配列（HU）と、横・縦のピクセル間隔 [mm] を設定する。"""
        self._hu = hu
        self._spacing = spacing
        self._rebuild_image()
        self.update()

    def set_window(self, wl: float, ww: float) -> None:
        self._wl = float(wl)
        self._ww = float(ww)
        if self._hu is not None:
            self._rebuild_image()
        self.update()

    def clear(self) -> None:
        self._hu = self._gray = self._qimage = None
        self.update()

    def image_rect(self) -> QRectF:
        """画像が描画される矩形（ウィジェット座標）。画像が無ければ空。"""
        if self._hu is None or self.width() <= 0 or self.height() <= 0:
            return QRectF()
        h, w = self._hu.shape
        phys_w = w * self._spacing[0]
        phys_h = h * self._spacing[1]
        scale = min(self.width() / phys_w, self.height() / phys_h)
        draw_w, draw_h = phys_w * scale, phys_h * scale
        return QRectF(
            (self.width() - draw_w) / 2, (self.height() - draw_h) / 2, draw_w, draw_h
        )

    # ---- 内部処理 ----
    def _rebuild_image(self) -> None:
        gray = apply_window(self._hu, self._wl, self._ww)  # 連続した uint8 配列
        h, w = gray.shape
        self._gray = gray
        self._qimage = QImage(gray.data, w, h, gray.strides[0], QImage.Format.Format_Grayscale8)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._qimage is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(self.image_rect(), self._qimage)
        painter.end()
