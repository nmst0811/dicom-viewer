"""自作の画像表示ウィジェット（観点1）。

HU の2D配列を WL/WW で uint8 化 → QImage → paintEvent で描画する。
背景は黒で、物理アスペクト比（ピクセル間隔の比）を保って中央に表示する。
他の2断面の位置を示す十字線（緑・1px）も paintEvent で描く。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .windowing import DEFAULT_WL, DEFAULT_WW, apply_window

CROSSHAIR_COLOR = QColor(0, 255, 0)  # 緑


class SliceView(QWidget):
    # 画像上のクリック / ドラッグ位置 (列, 行)。画像の範囲内に丸めた整数。
    clicked = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hu: Optional[np.ndarray] = None  # 表示中の2D配列（HU）
        self._spacing = (1.0, 1.0)  # (横, 縦) 1ピクセルの物理サイズ [mm]
        self._wl = float(DEFAULT_WL)
        self._ww = float(DEFAULT_WW)
        self._gray: Optional[np.ndarray] = None  # QImage が参照する uint8 配列（保持が必要）
        self._qimage: Optional[QImage] = None
        self._crosshair: Optional[tuple[int, int]] = None  # 画像上の (列, 行)
        self._dragging = False
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

    def set_crosshair(self, col: Optional[int], row: Optional[int]) -> None:
        """十字線の位置を画像上の (列, 行) で指定する。None で非表示。"""
        self._crosshair = None if col is None or row is None else (int(col), int(row))
        self.update()

    def clear(self) -> None:
        self._hu = self._gray = self._qimage = self._crosshair = None
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

    def image_to_widget(self, col: float, row: float) -> QPointF:
        """画像上のピクセル (列, 行) の中心を、ウィジェット座標に変換する。"""
        rect = self.image_rect()
        h, w = self._hu.shape
        return QPointF(
            rect.left() + (col + 0.5) * rect.width() / w,
            rect.top() + (row + 0.5) * rect.height() / h,
        )

    def widget_to_image(self, pos: QPointF, clamp: bool) -> Optional[tuple[int, int]]:
        """ウィジェット座標 → 画像上のピクセル (列, 行)。

        clamp=False のとき画像の外なら None、True のとき画像の端に丸める。
        """
        rect = self.image_rect()
        if rect.isEmpty() or (not clamp and not rect.contains(pos)):
            return None
        h, w = self._hu.shape
        col = math.floor((pos.x() - rect.left()) / rect.width() * w)
        row = math.floor((pos.y() - rect.top()) / rect.height() * h)
        return min(max(col, 0), w - 1), min(max(row, 0), h - 1)

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
            if self._crosshair is not None:
                self._draw_crosshair(painter)
        painter.end()

    def _draw_crosshair(self, painter: QPainter) -> None:
        rect = self.image_rect()
        center = self.image_to_widget(*self._crosshair)
        pen = QPen(CROSSHAIR_COLOR)
        pen.setWidth(1)
        pen.setCosmetic(True)  # 拡大率に関係なく、常に 1 デバイスピクセル
        painter.setPen(pen)
        painter.drawLine(QLineF(rect.left(), center.y(), rect.right(), center.y()))  # 横線
        painter.drawLine(QLineF(center.x(), rect.top(), center.x(), rect.bottom()))  # 縦線

    # ---- マウス操作（クリック / ドラッグでその点へ移動）----
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hu is not None:
            if self.widget_to_image(event.position(), clamp=False) is not None:
                self._dragging = True
                self._emit_clicked(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._emit_clicked(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def _emit_clicked(self, pos: QPointF) -> None:
        cell = self.widget_to_image(pos, clamp=True)  # ドラッグ中は画像の外に出ても端に丸める
        if cell is not None:
            self.clicked.emit(*cell)
