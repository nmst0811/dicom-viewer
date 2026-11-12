"""メインウィンドウ。"""
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DicomViewer")
        self.setMinimumSize(1200, 600)
        # 中央の表示領域（ステップ2以降で3断面パネルを配置する）
        self.setCentralWidget(QWidget())
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

    def _on_open_folder(self) -> None:
        # ステップ2で DICOM 読み込みに置き換える（現状はフォルダ選択のみ）
        folder = QFileDialog.getExistingDirectory(self, "DICOMフォルダを選択")
        if folder:
            self.statusBar().showMessage(f"選択されたフォルダ: {folder}")
