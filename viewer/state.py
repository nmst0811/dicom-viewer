"""ViewerState: 3断面で共有するスライス位置 (i, j, k) を一元管理する。

i = x（Sagittal の位置）、j = y（Coronal の位置）、k = z（Axial の位置）。いずれも 0 始まり。
位置が変わると changed(i, j, k) を出し、受け取った側（3ビュー）が再描画する。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal


class ViewerState(QObject):
    changed = Signal(int, int, int)  # (i, j, k)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._shape: Optional[tuple[int, int, int]] = None  # (nz, ny, nx)
        self._pos = [0, 0, 0]  # [i, j, k]

    @property
    def position(self) -> tuple[int, int, int]:
        return self._pos[0], self._pos[1], self._pos[2]

    def set_shape(self, shape: tuple[int, int, int]) -> None:
        """ボリュームの形 (nz, ny, nx) を設定し、位置を各軸の中央に戻す。必ず changed を出す。"""
        nz, ny, nx = shape
        self._shape = shape
        self._pos = [nx // 2, ny // 2, nz // 2]
        self.changed.emit(*self._pos)

    def set_position(
        self, i: Optional[int] = None, j: Optional[int] = None, k: Optional[int] = None
    ) -> None:
        """指定した軸だけ位置を変える（範囲外は端に丸める）。変化があったときだけ changed を出す。"""
        if self._shape is None:
            return
        nz, ny, nx = self._shape
        new = list(self._pos)
        for axis, (value, size) in enumerate(((i, nx), (j, ny), (k, nz))):
            if value is not None:
                new[axis] = min(max(int(value), 0), size - 1)
        if new != self._pos:
            self._pos = new
            self.changed.emit(*self._pos)
