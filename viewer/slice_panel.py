"""1断面分のパネル（ラベル + SliceView）。スライダーはステップ4で追加する。"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .slice_view import SliceView


class SlicePanel(QWidget):
    def __init__(self, title: str, axis: str, parent: Optional[QWidget] = None) -> None:
        """title: 断面名（Axial など）、axis: スライス方向の軸名（z など）。"""
        super().__init__(parent)
        self._title = title
        self._axis = axis

        self.label = QLabel(title)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view = SliceView()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.label)
        layout.addWidget(self.view, 1)

    def show_slice(
        self, hu: np.ndarray, spacing: tuple[float, float], index: int, count: int
    ) -> None:
        """index は 0 始まり。ラベルには 1 始まりで表示する。"""
        self.view.set_slice(hu, spacing)
        self.label.setText(f"{self._title}  {self._axis} = {index + 1} / {count}")
