"""1断面分のパネル（ラベル + SliceView + スライダー3本）。

スライダーはスライス位置・WL・WW。WL/WW はこのパネルの SliceView だけに反映する。
スライス位置が変わったときは slice_changed を出し、画像の差し替えは呼び出し側が行う。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QSlider, QVBoxLayout, QWidget

from .slice_view import SliceView
from .windowing import DEFAULT_WL, DEFAULT_WW, WL_MAX, WL_MIN, WW_MAX, WW_MIN


class SlicePanel(QWidget):
    slice_changed = Signal(int)  # スライス位置（0 始まり）

    def __init__(self, title: str, axis: str, parent: Optional[QWidget] = None) -> None:
        """title: 断面名（Axial など）、axis: スライス方向の軸名（z など）。"""
        super().__init__(parent)
        self._title = title
        self._axis = axis

        self.label = QLabel(title)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view = SliceView()

        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.wl_slider = QSlider(Qt.Orientation.Horizontal)
        self.wl_slider.setRange(WL_MIN, WL_MAX)
        self.ww_slider = QSlider(Qt.Orientation.Horizontal)
        self.ww_slider.setRange(WW_MIN, WW_MAX)
        self.wl_value = self._make_value_label("-1000 HU")
        self.ww_value = self._make_value_label("4000 HU")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("スライス"), 0, 0)
        grid.addWidget(self.slice_slider, 0, 1, 1, 2)
        grid.addWidget(QLabel("WL"), 1, 0)
        grid.addWidget(self.wl_slider, 1, 1)
        grid.addWidget(self.wl_value, 1, 2)
        grid.addWidget(QLabel("WW"), 2, 0)
        grid.addWidget(self.ww_slider, 2, 1)
        grid.addWidget(self.ww_value, 2, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.label)
        layout.addWidget(self.view, 1)
        layout.addLayout(grid)

        self.slice_slider.valueChanged.connect(self.slice_changed)
        self.wl_slider.valueChanged.connect(self._on_window_changed)
        self.ww_slider.valueChanged.connect(self._on_window_changed)

        self.set_window(DEFAULT_WL, DEFAULT_WW)
        for slider in (self.slice_slider, self.wl_slider, self.ww_slider):
            slider.setEnabled(False)  # データを読み込むまでは操作できない

    def _make_value_label(self, widest_text: str) -> QLabel:
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumWidth(label.fontMetrics().horizontalAdvance(widest_text) + 4)
        return label

    # ---- 公開 API ----
    def show_slice(
        self, hu: np.ndarray, spacing: tuple[float, float], index: int, count: int
    ) -> None:
        """画像・ラベル・スライスのスライダー位置を更新する。index は 0 始まり。

        スライダーの更新では slice_changed を出さない（連鎖を防ぐ）。
        """
        self.view.set_slice(hu, spacing)
        self.label.setText(f"{self._title}  {self._axis} = {index + 1} / {count}")
        self.slice_slider.blockSignals(True)
        self.slice_slider.setRange(0, count - 1)
        self.slice_slider.setValue(index)
        self.slice_slider.blockSignals(False)
        for slider in (self.slice_slider, self.wl_slider, self.ww_slider):
            slider.setEnabled(True)

    def set_window(self, wl: int, ww: int) -> None:
        """WL/WW をスライダーと SliceView に反映する。"""
        for slider, value in ((self.wl_slider, wl), (self.ww_slider, ww)):
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
        self._on_window_changed()

    def window(self) -> tuple[int, int]:
        return self.wl_slider.value(), self.ww_slider.value()

    # ---- 内部処理 ----
    def _on_window_changed(self, _value: int = 0) -> None:
        wl, ww = self.window()
        self.wl_value.setText(f"{wl} HU")
        self.ww_value.setText(f"{ww} HU")
        self.view.set_window(wl, ww)
