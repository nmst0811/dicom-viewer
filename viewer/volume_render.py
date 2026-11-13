"""観点7: 肺領域の 3D 表示（VTK ボリュームレンダリング）。

LungScene      : VTK のパイプライン（Qt 非依存）。ボリューム・伝達関数・カメラ。
VolumeWindow   : QVTKRenderWindowInteractor を載せた別ウィンドウ。回転・拡大・平行移動ができる。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# VTK は必要なモジュールだけを import する（描画クラスの登録のため、*OpenGL2 系も明示的に読み込む）
import vtkmodules.vtkInteractionStyle  # noqa: F401  (インタラクタースタイルの登録)
import vtkmodules.vtkRenderingFreeType  # noqa: F401
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.util import numpy_support
from vtkmodules.vtkCommonCore import VTK_UNSIGNED_CHAR
from vtkmodules.vtkCommonDataModel import vtkImageData, vtkPiecewiseFunction
from vtkmodules.vtkImagingGeneral import vtkImageGaussianSmooth
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingCore import (
    vtkColorTransferFunction,
    vtkRenderer,
    vtkVolume,
    vtkVolumeProperty,
)
from vtkmodules.vtkRenderingVolumeOpenGL2 import vtkSmartVolumeMapper

from .lung_loader import LungData

BACKGROUND = (0.07, 0.08, 0.11)

# 肺野向けの伝達関数（スカラー 0〜255。0 は背景で常に透明）
# 濃度値: 肺野の窓 [-1000, 400] HU を 0〜255 にしたもの（実質 HU = 5.49 × 値 - 1000）
#   実質部（約 -870 HU）はごく薄く、血管・気管支壁（約 -500 HU 以上）ほど濃く見せる
INTENSITY_OPACITY = [(0, 0.0), (3, 0.0), (8, 0.0025), (24, 0.006), (45, 0.018),
                     (70, 0.07), (100, 0.25), (140, 0.65), (255, 0.90)]
INTENSITY_COLOR = [(0, (0.55, 0.68, 0.90)), (35, (0.62, 0.74, 0.92)), (70, (0.93, 0.66, 0.62)),
                   (110, (0.97, 0.82, 0.72)), (170, (1.0, 0.96, 0.90)), (255, (1.0, 1.0, 1.0))]
# 二値マスク: 表面が見えるよう、内部をほぼ不透明にする
MASK_OPACITY = [(0, 0.0), (90, 0.0), (150, 0.85), (255, 0.95)]
MASK_COLOR = [(0, (0.86, 0.56, 0.52)), (255, (0.92, 0.64, 0.58))]


def make_image_data(lung: LungData) -> vtkImageData:
    """(nz, ny, nx) の uint8 配列と間隔から vtkImageData を作る。x が最も速く並ぶ並びで渡す。"""
    nz, ny, nx = lung.shape
    data = np.ascontiguousarray(lung.volume)
    image = vtkImageData()
    image.SetDimensions(nx, ny, nz)
    image.SetSpacing(*lung.spacing)
    scalars = numpy_support.numpy_to_vtk(data.ravel(), deep=True, array_type=VTK_UNSIGNED_CHAR)
    image.GetPointData().SetScalars(scalars)
    return image


def _smooth_mask(image: vtkImageData, spacing: tuple[float, float, float]):
    """二値マスクの階段状の表面をなめらかにする（シェーディングの法線が安定する）。"""
    sx, _sy, sz = spacing
    smooth = vtkImageGaussianSmooth()
    smooth.SetInputData(image)
    smooth.SetDimensionality(3)
    sigma_xy = 1.5  # ボクセル単位
    smooth.SetStandardDeviations(sigma_xy, sigma_xy, max(sigma_xy * sx / sz, 0.3))
    smooth.Update()
    return smooth.GetOutput()


class LungScene:
    """ボリューム・伝達関数・カメラ（Qt 非依存）。renderer をどの描画ウィンドウにも載せられる。"""

    def __init__(self, lung: LungData) -> None:
        self.lung = lung
        self.opacity_scale = 1.0
        self.renderer = vtkRenderer()
        self.renderer.SetBackground(*BACKGROUND)

        image = make_image_data(lung)
        if lung.kind == "mask":
            image = _smooth_mask(image, lung.spacing)
        self._image = image  # 描画中は参照を保持する

        self.mapper = vtkSmartVolumeMapper()
        self.mapper.SetInputData(image)
        self.mapper.SetBlendModeToComposite()

        self._color = vtkColorTransferFunction()
        self._opacity = vtkPiecewiseFunction()
        self._color_points = MASK_COLOR if lung.kind == "mask" else INTENSITY_COLOR
        self._opacity_points = MASK_OPACITY if lung.kind == "mask" else INTENSITY_OPACITY
        for x, (r, g, b) in self._color_points:
            self._color.AddRGBPoint(x, r, g, b)
        self._update_opacity()

        prop = vtkVolumeProperty()
        prop.SetColor(self._color)
        prop.SetScalarOpacity(self._opacity)
        prop.SetInterpolationTypeToLinear()
        # 濃度値はシェーディングなし（スライス間隔が粗いと、法線が階段状になって横縞が出るため）。
        # 二値マスクは内部が一様で、陰影が無いと平らなシルエットになるので、シェーディングを使う。
        if lung.kind == "mask":
            prop.ShadeOn()
        else:
            prop.ShadeOff()
        prop.SetAmbient(0.25)
        prop.SetDiffuse(0.85)
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(20.0)
        prop.SetScalarOpacityUnitDistance(float(max(lung.spacing[0], lung.spacing[1])) * 2.0)
        self.property = prop

        self.volume = vtkVolume()
        self.volume.SetMapper(self.mapper)
        self.volume.SetProperty(prop)
        self.renderer.AddVolume(self.volume)
        self.reset_camera()

    def _update_opacity(self) -> None:
        self._opacity.RemoveAllPoints()
        for x, alpha in self._opacity_points:
            self._opacity.AddPoint(x, min(1.0, alpha * self.opacity_scale))

    def set_opacity_scale(self, scale: float) -> None:
        """不透明度の倍率（1.0 が既定）。"""
        self.opacity_scale = float(scale)
        self._update_opacity()

    def reset_camera(self) -> None:
        """正面（前方）から、頭側を上にして全体が入るように見る。

        患者座標は x = 患者の左、y = 後方、z = 頭側。前方（y が小さい側）から +y 方向を見て、
        上を +z にすると、患者の左が画面の右に来る（通常の正面像）。
        """
        x0, x1, y0, y1, z0, z1 = self._foreground_bounds()
        center = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        camera = self.renderer.GetActiveCamera()
        camera.SetFocalPoint(*center)
        camera.SetPosition(center[0], center[1] - 1000.0, center[2])
        camera.SetViewUp(0.0, 0.0, 1.0)
        camera.SetParallelProjection(False)
        camera.SetViewAngle(30.0)  # 拡大（Zoom）を繰り返しても累積しないよう、毎回戻す
        self.renderer.ResetCamera(x0, x1, y0, y1, z0, z1)  # 向きは保ったまま、肺が入る距離に調整する
        camera.Zoom(1.3)  # 外接球に合わせるため余白が大きいので、少し拡大して画面を使う

    def _foreground_bounds(self) -> tuple[float, float, float, float, float, float]:
        """背景以外（肺）の範囲 [mm]。ボリューム全体ではなく、肺に合わせてカメラを置くために使う。"""
        vol = self.lung.volume
        sx, sy, sz = self.lung.spacing
        bounds = []
        for axis, step in ((2, sx), (1, sy), (0, sz)):  # x, y, z の順
            other = tuple(a for a in range(3) if a != axis)
            idx = np.flatnonzero(vol.any(axis=other))
            bounds += [(idx[0] - 0.5) * step, (idx[-1] + 0.5) * step]
        return tuple(bounds)


class VolumeWindow(QMainWindow):
    """肺領域の 3D 表示ウィンドウ。左ドラッグで回転、右ドラッグ／ホイールで拡大縮小、
    中ドラッグ（または Shift+左ドラッグ）で平行移動。"""

    def __init__(self, lung: LungData, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"肺領域の3D表示 — {lung.label}")
        self.resize(900, 760)
        self.setMinimumSize(520, 460)

        self.scene = LungScene(lung)
        self.vtk_widget = QVTKRenderWindowInteractor(self)
        render_window = self.vtk_widget.GetRenderWindow()
        render_window.AddRenderer(self.scene.renderer)
        self.interactor = render_window.GetInteractor()
        self.interactor.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self.interactor.SetDesiredUpdateRate(15.0)  # 操作中は描画品質を下げて滑らかに動かす

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(10, 300)  # 倍率 [%]
        self.opacity_slider.setValue(100)
        self.opacity_label = QLabel("100 %")
        self.opacity_label.setMinimumWidth(self.opacity_label.fontMetrics().horizontalAdvance("300 %") + 4)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        reset_button = QPushButton("視点をリセット")
        reset_button.clicked.connect(self.reset_view)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("不透明度"))
        controls.addWidget(self.opacity_slider, 1)
        controls.addWidget(self.opacity_label)
        controls.addWidget(reset_button)
        hint = QLabel(
            "左ドラッグ: 回転　／　右ドラッグ・ホイール: 拡大縮小　／　"
            "中ドラッグ または Shift＋左ドラッグ: 平行移動"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.vtk_widget, 1)
        layout.addLayout(controls)
        layout.addWidget(hint)
        self.setCentralWidget(central)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.interactor.GetInitialized():
            self.interactor.Initialize()
        self.vtk_widget.GetRenderWindow().Render()

    def _on_opacity_changed(self, value: int) -> None:
        self.opacity_label.setText(f"{value} %")
        self.scene.set_opacity_scale(value / 100.0)
        self.vtk_widget.GetRenderWindow().Render()

    def reset_view(self) -> None:
        self.scene.reset_camera()
        self.vtk_widget.GetRenderWindow().Render()

    def closeEvent(self, event) -> None:
        # VTK の描画ウィンドウは、Qt のウィジェットが破棄される前に後始末する必要がある
        self.vtk_widget.Finalize()
        super().closeEvent(event)
