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
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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

from . import dicom_loader, lung_loader
from .dicom_loader import DicomLoadError, LoadCancelled, SeriesInfo, Volume
from .lung_loader import LungData, LungLoadError, RawLung
from .slice_panel import SlicePanel
from .state import ViewerState
from .windowing import DEFAULT_WL, DEFAULT_WW


class _Progress:
    """QProgressDialog を loader の progress コールバックとして使うラッパー。"""

    def __init__(self, parent: QMainWindow, text: str, title: str = "DICOM 読み込み") -> None:
        self._dialog = QProgressDialog(text, "キャンセル", 0, 100, parent)
        self._dialog.setWindowTitle(title)
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


class _LungSettingsDialog(QDialog):
    """PNG 連番・.npy は、画素間隔と z の向きの情報を持たないので、読み込み時に指定してもらう。"""

    def __init__(
        self,
        summary: str,
        spacing: tuple[float, float, float],
        spacing_note: str,
        flip: bool,
        flip_note: str,
        offer_ct: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("肺領域データの設定")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(summary))

        form = QFormLayout()
        self._spins = []
        for label, value in zip(("画素間隔 X（横）", "画素間隔 Y（縦）", "スライス間隔 Z"), spacing):
            spin = QDoubleSpinBox()
            spin.setRange(0.001, 100.0)
            spin.setDecimals(6)  # CT の間隔（例: 0.78125 mm）を、丸めずにそのまま既定値にするため
            spin.setSingleStep(0.1)
            spin.setSuffix(" mm")
            spin.setValue(float(value))
            form.addRow(label, spin)
            self._spins.append(spin)
        layout.addLayout(form)
        layout.addWidget(self._note(spacing_note))

        self.flip_check = QCheckBox("先頭のスライスが頭側（z の順序を反転して読み込む）")
        self.flip_check.setChecked(flip)
        layout.addWidget(self.flip_check)
        layout.addWidget(self._note(flip_note))

        self.ct_check: Optional[QCheckBox] = None
        if offer_ct:  # 二値マスクで、メインの CT と大きさが同じときだけ選べる（任意機能）
            self.ct_check = QCheckBox("メインで開いた CT の濃度で描画する（マスク × CT）")
            layout.addWidget(self.ct_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("3D 表示")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _note(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def values(self) -> tuple[tuple[float, float, float], bool, bool]:
        """(画素間隔 (x, y, z) [mm], z を反転するか, マスク × CT にするか)"""
        spacing = tuple(spin.value() for spin in self._spins)
        use_ct = self.ct_check is not None and self.ct_check.isChecked()
        return spacing, self.flip_check.isChecked(), use_ct


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DicomViewer")
        self.setMinimumSize(1200, 600)

        self.volume: Optional[Volume] = None
        self._volume_window = None  # 観点7: 肺領域の 3D 表示ウィンドウ（開いている間だけ保持）
        # 横3列: 左から Axial・Coronal・Sagittal
        self.axial_panel = SlicePanel("Axial", "z")
        self.coronal_panel = SlicePanel("Coronal", "y")
        self.sagittal_panel = SlicePanel("Sagittal", "x")
        central = QWidget()
        layout = QHBoxLayout(central)
        for panel in (self.axial_panel, self.coronal_panel, self.sagittal_panel):
            layout.addWidget(panel, 1)
        self.setCentralWidget(central)

        # スライス位置 (i, j, k) は ViewerState が一元管理し、変化したら3ビューすべてを再描画する
        self.state = ViewerState(self)
        self.state.changed.connect(self._show_position)
        # スライダー操作 → state（i = Sagittal、j = Coronal、k = Axial のスライダー）
        self.sagittal_panel.slice_changed.connect(lambda i: self.state.set_position(i=i))
        self.coronal_panel.slice_changed.connect(lambda j: self.state.set_position(j=j))
        self.axial_panel.slice_changed.connect(lambda k: self.state.set_position(k=k))
        # 画像クリック → state（任意機能）。画像上の (列, 行) → (i, j, k) は Volume が変換する
        self.axial_panel.point_clicked.connect(
            lambda col, row: self.state.set_position(*self.volume.axial_pick(col, row))
        )
        self.coronal_panel.point_clicked.connect(
            lambda col, row: self.state.set_position(*self.volume.coronal_pick(col, row))
        )
        self.sagittal_panel.point_clicked.connect(
            lambda col, row: self.state.set_position(*self.volume.sagittal_pick(col, row))
        )

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

        # 3D表示 → 肺領域データを開く → フォルダ（DICOM / PNG 連番）／ファイル（.npy）
        view3d_menu = self.menuBar().addMenu("3D表示")
        lung_menu = view3d_menu.addMenu("肺領域データを開く")
        self.open_lung_folder_action = QAction("フォルダ（DICOM / PNG 連番）...", self)
        self.open_lung_folder_action.triggered.connect(self._on_open_lung_folder)
        lung_menu.addAction(self.open_lung_folder_action)
        self.open_lung_npy_action = QAction("ファイル（.npy）...", self)
        self.open_lung_npy_action.triggered.connect(self._on_open_lung_npy)
        lung_menu.addAction(self.open_lung_npy_action)

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
        """(i, j, k) = (x, y, z) の位置で3断面と十字線を表示する（state.changed の受け口）。"""
        v = self.volume
        nz, ny, nx = v.shape
        self.axial_panel.show_slice(*v.axial(k), k, nz)
        self.coronal_panel.show_slice(*v.coronal(j), j, ny)
        self.sagittal_panel.show_slice(*v.sagittal(i), i, nx)
        # 各ビューには「他の2断面の位置」を示す線を引く
        self.axial_panel.view.set_crosshair(*v.axial_cursor(i, j, k))  # j: 横線、i: 縦線
        self.coronal_panel.view.set_crosshair(*v.coronal_cursor(i, j, k))  # k: 横線、i: 縦線
        self.sagittal_panel.view.set_crosshair(*v.sagittal_cursor(i, j, k))  # k: 横線、j: 縦線

    def _set_volume(self, volume: Volume) -> None:
        self.volume = volume
        nz, ny, nx = volume.shape
        sx, sy, sz = volume.spacing
        for panel in (self.axial_panel, self.coronal_panel, self.sagittal_panel):
            panel.set_window(DEFAULT_WL, DEFAULT_WW)
        self.state.set_shape(volume.shape)  # 位置は各軸の中央に戻り、全ビューが再描画される
        self.statusBar().showMessage(
            f"サイズ: {nx}×{ny}  |  枚数: {nz}  |  "
            f"ピクセル間隔: {sx:.3f}×{sy:.3f} mm（スライス間隔: {sz:.2f} mm）"
        )

    # ---- 観点7: 肺領域データの 3D 表示 ----
    def _on_open_lung_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "肺領域データのフォルダを選択（DICOM / PNG 連番）"
        )
        if folder:
            self._open_lung(folder=folder)

    def _on_open_lung_npy(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "肺領域データ（.npy）を選択", "", "NumPy 配列 (*.npy)"
        )
        if path:
            self._open_lung(npy_path=path)

    def _open_lung(self, folder: Optional[str] = None, npy_path: Optional[str] = None) -> None:
        """読み込み → （PNG / .npy は）間隔と向きの設定 → 3D 表示。失敗しても、開いているものは残す。"""
        try:
            raw = self._read_lung(folder, npy_path)
            if raw is None:
                return
            lung = self._build_lung(raw)
            if lung is None:
                return
            window = self._create_volume_window(lung)
        except LoadCancelled:
            self.statusBar().showMessage("読み込みをキャンセルしました")
            return
        except DicomLoadError as e:  # LungLoadError を含む
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
        self._show_volume_window(window)

    def _read_lung(self, folder: Optional[str], npy_path: Optional[str]) -> Optional[RawLung]:
        """データを読み込む。キャンセルされたら None。"""
        if npy_path is not None:
            return lung_loader.load_npy(npy_path)

        # フォルダ: DICOM があれば DICOM（メインのローダーを流用）、無ければ PNG 連番
        progress = _Progress(self, "肺領域データを検索しています...", "肺領域データの読み込み")
        try:
            try:
                series_list = dicom_loader.scan_series(folder, progress)
            except DicomLoadError:
                series_list = None  # DICOM が無い → PNG として読む（中止は LoadCancelled なのでここに来ない）
        finally:
            progress.close()

        if series_list is not None:
            series = self._choose_series(series_list)
            if series is None:
                return None
            progress = _Progress(
                self, f"{series.count} 枚を読み込んでいます...", "肺領域データの読み込み"
            )
            try:
                return lung_loader.load_dicom_lung(series, progress)
            finally:
                progress.close()

        if not lung_loader.find_png_files(folder):
            raise LungLoadError(
                "選択したフォルダに、DICOM 画像も PNG ファイルも見つかりませんでした。"
            )
        progress = _Progress(self, "PNG を読み込んでいます...", "肺領域データの読み込み")
        try:
            return lung_loader.load_png_series(folder, progress)
        finally:
            progress.close()

    def _build_lung(self, raw: RawLung) -> Optional[LungData]:
        """3D 表示用のデータにする。DICOM は間隔をタグから得る。PNG / .npy はダイアログで指定する。"""
        if raw.spacing is not None:  # DICOM: 間隔も向き（z の昇順）も確定している
            return lung_loader.build_lung_data(raw, raw.spacing, flip_z=False)

        ct = self.volume
        same_shape = ct is not None and ct.shape == raw.array.shape
        is_mask = lung_loader.looks_like_mask(raw)

        if ct is not None:
            spacing = ct.spacing
            spacing_note = "既定値は、メインで開いた CT の画素間隔です。"
        else:
            spacing = (1.0, 1.0, 1.0)
            spacing_note = (
                "PNG / .npy には間隔の情報がありません。メインで DICOM を開いておくと、"
                "その間隔が既定値になります。必要に応じて入力してください。"
            )

        detected = lung_loader.detect_flip_with_ct(raw, ct) if ct is not None else None
        if detected is not None:
            flip = detected
            flip_note = "メインで開いた CT と照合して、向きを自動で判定しました。"
        else:
            flip = raw.default_flip
            flip_note = (
                "既定値です（第5回の PNG 連番は、先頭の slice_001 が頭側）。"
                "上下が逆に見えるときは、チェックを切り替えてください。"
                if raw.source == "PNG"
                else "z が足側から頭側へ並んでいるなら、チェックを外してください。"
            )

        kind_text = "二値マスク" if is_mask else "濃度値（肺野の画素値）"
        dialog = _LungSettingsDialog(
            f"{raw.description}\n種類: {kind_text}",
            spacing,
            spacing_note,
            flip,
            flip_note,
            offer_ct=is_mask and same_shape,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        spacing, flip, use_ct = dialog.values()
        return lung_loader.build_lung_data(raw, spacing, flip, ct=ct, use_ct=use_ct)

    def _create_volume_window(self, lung: LungData):
        try:
            # VTK は 3D 表示のときだけ読み込む（読み込めなくても、3断面ビューアは使えるように）
            from .volume_render import VolumeWindow
        except ImportError as e:
            raise LungLoadError(
                "3D 表示に必要な VTK を読み込めませんでした。\n"
                "README のトラブルシュートを確認してください。\n"
                f"{e}"
            ) from e
        return VolumeWindow(lung, parent=self)

    def _show_volume_window(self, window) -> None:
        self._close_volume_window()  # 前の 3D 表示ウィンドウは閉じる
        self._volume_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _close_volume_window(self) -> None:
        if self._volume_window is not None:
            self._volume_window.close()  # VTK の後始末（Finalize）は closeEvent で行う
            self._volume_window.deleteLater()
            self._volume_window = None

    def closeEvent(self, event) -> None:
        self._close_volume_window()
        super().closeEvent(event)
