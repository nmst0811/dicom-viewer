"""メインウィンドウ。"""
from __future__ import annotations

import time
import traceback
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import dicom_loader
from .dicom_loader import DicomLoadError, LoadCancelled, SeriesInfo, Volume
from .slice_panel import SlicePanel
from .windowing import DEFAULT_WL, DEFAULT_WW


class _Progress:
    """QProgressDialog を loader の progress コールバックとして使うラッパー。"""

    def __init__(self, parent: QMainWindow, text: str) -> None:
        self._dialog = QProgressDialog(text, "キャンセル", 0, 100, parent)
        self._dialog.setWindowTitle("DICOM 読み込み")
        self._dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialog.setMinimumDuration(0)
        self._dialog.setAutoReset(False)
        self._dialog.setAutoClose(False)
        self._last = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - self._last < 0.03:  # 描画更新は 30ms 間隔に間引く
            return
        self._last = now
        self._dialog.setMaximum(max(total, 1))
        self._dialog.setValue(done)
        QApplication.processEvents()
        if self._dialog.wasCanceled():
            raise LoadCancelled()

    def close(self) -> None:
        self._dialog.close()


class _SeriesDialog(QDialog):
    """複数シリーズがあるとき、枚数の多い順に並べて選ばせる。"""

    def __init__(self, series_list: list[SeriesInfo], parent: Optional[QMainWindow] = None):
        super().__init__(parent)
        self.setWindowTitle("シリーズを選択")
        self.resize(680, 320)
        self._series_list = series_list

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("複数のシリーズが見つかりました。読み込むシリーズを選択してください。")
        )

        self.table = QTableWidget(len(series_list), 4)
        self.table.setHorizontalHeaderLabels(["枚数", "モダリティ", "シリーズ説明", "画像サイズ"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, s in enumerate(series_list):
            values = [str(s.count), s.modality, s.description, f"{s.cols}×{s.rows}"]
            for col, text in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.selectRow(0)
        self.table.doubleClicked.connect(self.accept)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("読み込む")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_series(self) -> SeriesInfo:
        return self._series_list[max(self.table.currentRow(), 0)]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DicomViewer")
        self.setMinimumSize(1200, 600)

        self.volume: Optional[Volume] = None
        # 横3列: 左から Axial・Coronal・Sagittal
        self.axial_panel = SlicePanel("Axial", "z")
        self.coronal_panel = SlicePanel("Coronal", "y")
        self.sagittal_panel = SlicePanel("Sagittal", "x")
        central = QWidget()
        layout = QHBoxLayout(central)
        for panel in (self.axial_panel, self.coronal_panel, self.sagittal_panel):
            layout.addWidget(panel, 1)
        self.setCentralWidget(central)

        self._create_menus()
        self.statusBar().showMessage(
            "ファイル → DICOMフォルダを開く (Ctrl+O) でデータを選択してください"
        )

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("ファイル")

        self.open_action = QAction("DICOMフォルダを開く", self)
        self.open_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_action.triggered.connect(self._on_open_folder)
        file_menu.addAction(self.open_action)

        file_menu.addSeparator()

        self.quit_action = QAction("終了", self)
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.close)
        file_menu.addAction(self.quit_action)

    # ---- DICOM 読み込み ----
    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "DICOMフォルダを選択")
        if folder:
            self._load_folder(folder)

    def _choose_series(self, series_list: list[SeriesInfo]) -> Optional[SeriesInfo]:
        if len(series_list) == 1:
            return series_list[0]
        dialog = _SeriesDialog(series_list, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_series()

    def _load_folder(self, folder: str) -> None:
        """フォルダを読み込んで表示する。失敗しても、表示中のデータはそのまま残す。"""
        try:
            progress = _Progress(self, "DICOM ファイルを検索しています...")
            try:
                series_list = dicom_loader.scan_series(folder, progress)
            finally:
                progress.close()

            series = self._choose_series(series_list)
            if series is None:
                return

            progress = _Progress(self, f"{series.count} 枚を読み込んでいます...")
            try:
                volume = dicom_loader.load_volume(series, progress)
            finally:
                progress.close()
        except LoadCancelled:
            self.statusBar().showMessage("読み込みをキャンセルしました")
            return
        except DicomLoadError as e:
            QMessageBox.critical(self, "読み込みエラー", str(e))
            return
        except Exception as e:  # 想定外の失敗でもアプリは落とさない
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "読み込みエラー",
                f"予期しないエラーが発生しました。\n{type(e).__name__}: {e}",
            )
            return

        self._set_volume(volume)

    def _show_position(self, i: int, j: int, k: int) -> None:
        """(i, j, k) = (x, y, z) の位置で3断面を表示する。"""
        nz, ny, nx = self.volume.shape
        self.axial_panel.show_slice(*self.volume.axial(k), k, nz)
        self.coronal_panel.show_slice(*self.volume.coronal(j), j, ny)
        self.sagittal_panel.show_slice(*self.volume.sagittal(i), i, nx)

    def _set_volume(self, volume: Volume) -> None:
        self.volume = volume
        nz, ny, nx = volume.shape
        sx, sy, sz = volume.spacing
        for panel in (self.axial_panel, self.coronal_panel, self.sagittal_panel):
            panel.view.set_window(DEFAULT_WL, DEFAULT_WW)
        # スライス位置は各軸の中央（ステップ5で ViewerState に移す）
        self._show_position(nx // 2, ny // 2, nz // 2)
        self.statusBar().showMessage(
            f"サイズ: {nx}×{ny}  |  枚数: {nz}  |  "
            f"ピクセル間隔: {sx:.3f}×{sy:.3f} mm（スライス間隔: {sz:.2f} mm）"
        )
