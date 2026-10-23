# dicom_viewer_final_v13.py
import sys, os, json, math, warnings, time
import numpy as np, pydicom
import matplotlib
matplotlib.use('Qt5Agg')

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QIcon
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def get_estimated_pixel_spacing_from_dimensions(columns: int, rows: int) -> float:
    """Estimate pixel spacing from image dimensions using linear regression.

    Coefficients are fitted via least-squares on a chest X-ray A-group dataset
    (340 files, CR/DX, all with verified PixelSpacing tags).
    Replaces the earlier hand-tuned coefficients; abs-mean error on the
    training set dropped from 6.54% to 4.21%.

    Args:
        columns: Image width in pixels.
        rows: Image height in pixels.

    Returns:
        Estimated pixel spacing in mm, clamped to [0.01, 1.0].
    """
    spacing = (-1.543927e-05 * columns
               - 3.119261e-05 * rows
               + 2.768459e-01)
    return float(max(0.01, min(spacing, 1.0)))


def get_uncertainty_pct(columns: int) -> int:
    """Return the expected estimation uncertainty (%) for a given image width.

    Derived from per-resolution-bucket abs-mean errors measured on the
    chest X-ray A-group dataset after coefficient update.

    Args:
        columns: Image width in pixels.

    Returns:
        Uncertainty percentage as an integer.
    """
    if columns <= 1536:
        return 17
    if columns <= 2048:
        return 11
    if columns <= 3000:
        return 5
    return 10

class DicomLength(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DICOM Distance Measurer")
        self.resize(1500, 1400)

        # 초기 변수
        self.dicom_file = None
        self.ds = None
        self.pixel_array = None
        self.pixel_spacing: float | None = None
        self.pixel_spacing_source: str | None = None
        self.rows = self.columns = 0
        self.ps_estimated = 0.0

        # 상태 변수
        self.points = []
        self.temp_point = None
        self.current_mouse = None

        # Zoom / Pan
        self.zoom_factor = 1.0
        self.center_x = 0
        self.center_y = 0
        self.dragging_mid = False
        self.drag_start: QPoint | None = None

        # Window Level
        self.window_center = 127
        self.window_width = 255
        self.original_wc_ww = (127, 255)  # DICOM 로드 후 갱신됨
        self.right_click_start = None
        self.right_click_time = 0

        self.init_ui()

    # ------------------- UI 초기화 -------------------
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()
        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet("font-weight: bold; padding: 5px;")
        self.json_btn = QPushButton("Save JSON")
        self.json_btn.clicked.connect(self.save_json)
        self.json_btn.setEnabled(False)
        top_bar.addWidget(self.file_label)
        top_bar.addStretch()
        top_bar.addWidget(self.json_btn)
        layout.addLayout(top_bar)

        self.figure = Figure()
        self.figure.patch.set_facecolor("white")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.axis("off")
        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        layout.addWidget(self.canvas)

        self.setAcceptDrops(True)

        # 이벤트 연결
        self.canvas.mousePressEvent = self.mousePressEvent
        self.canvas.mouseMoveEvent = self.mouseMoveEvent
        self.canvas.mouseReleaseEvent = self.mouseReleaseEvent
        self.canvas.wheelEvent = self.wheelEvent

        # 키보드 이벤트 활성화
        self.setFocusPolicy(Qt.StrongFocus)

    # =========================================================
    # NEW: ESC 키로 초기 상태 복원
    # =========================================================
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reset_to_initial_state()
        super().keyPressEvent(event)

    def reset_to_initial_state(self):
        """모든 상태를 DICOM 로드 직후 상태로 초기화"""
        if self.pixel_array is None:
            return

        # 1. 측정 선 모두 삭제
        self.points.clear()
        self.temp_point = None
        self.current_mouse = None

        # 2. Zoom & Pan 초기화
        self.zoom_factor = 1.0
        self.center_x = self.columns / 2
        self.center_y = self.rows / 2

        # 3. WW/WC 원본 값 복원
        self.window_center, self.window_width = self.original_wc_ww

        # 4. 드래그 상태 초기화
        self.dragging_mid = False
        self.drag_start = None
        self.right_click_start = None

        # 5. 화면 갱신
        self.display_image()
        print("[ESC] All states reset to initial load.")

    # ------------------- Drag & Drop -------------------
    def dragEnterEvent(self, e):
        e.accept() if e.mimeData().hasUrls() else e.ignore()

    def dropEvent(self, e):
        path = e.mimeData().urls()[0].toLocalFile()
        self.file_label.setText("Loading...")
        QApplication.processEvents()
        self.load_dicom(path)

    # ------------------- DICOM 로드 -------------------
    def load_dicom(self, path):
        try:
            self.ds = pydicom.dcmread(path, force=True)
            self.pixel_array = self.ds.pixel_array
        except Exception as e:
            QMessageBox.warning(self, "Load Failed", f"Not a valid DICOM file.\n{e}")
            return

        self.dicom_file = path
        self.file_label.setText(os.path.basename(path))
        self.json_btn.setEnabled(True)

        self.rows = int(self.ds.Rows)
        self.columns = int(self.ds.Columns)
        self.center_x = self.columns / 2
        self.center_y = self.rows / 2

        # Pixel Spacing 계산
        self.pixel_spacing = None
        if "PixelSpacing" in self.ds:
            self.pixel_spacing = float(self.ds.PixelSpacing[0])
            self.pixel_spacing_source = "PixelSpacing(0028,0030)"
        elif "ImagerPixelSpacing" in self.ds:
            self.pixel_spacing = float(self.ds.ImagerPixelSpacing[0])
            self.pixel_spacing_source = "ImagerPixelSpacing(0018,1164)"
        else:
            self.pixel_spacing = get_estimated_pixel_spacing_from_dimensions(self.columns, self.rows)
            self.pixel_spacing_source = "Estimated from dimensions"

        # WC/WW (원본 저장 + 현재 적용)
        if "WindowCenter" in self.ds and "WindowWidth" in self.ds:
            wc = self.ds.WindowCenter
            ww = self.ds.WindowWidth
            wc_val = float(wc[0] if isinstance(wc, (list, tuple)) else wc)
            ww_val = float(ww[0] if isinstance(ww, (list, tuple)) else ww)
            self.original_wc_ww = (wc_val, ww_val)
        else:
            self.original_wc_ww = (127, 255)

        self.window_center, self.window_width = self.original_wc_ww

        # 상태 초기화
        self.points.clear()
        self.temp_point = None
        self.current_mouse = None
        self.zoom_factor = 1.0
        self.display_image()

    # ------------------- 이미지 표시 -------------------
    def display_image(self):
        if self.pixel_array is None:
            return

        img = self.pixel_array.astype(np.float32)
        min_val = self.window_center - self.window_width / 2
        max_val = self.window_center + self.window_width / 2
        img = np.clip(img, min_val, max_val)
        img = (img - min_val) / (max_val - min_val + 1e-8) * 255
        img = img.astype(np.uint8)

        self.ax.clear()
        zoom = self.zoom_factor
        half_w = (self.columns / 2) / zoom
        half_h = (self.rows / 2) / zoom

        self.ax.imshow(img, cmap="gray", origin="upper", extent=[0, self.columns, self.rows, 0])
        self.ax.set_xlim(self.center_x - half_w, self.center_x + half_w)
        self.ax.set_ylim(self.center_y + half_h, self.center_y - half_h)
        self.ax.axis("off")

        # 거리선
        is_estimated = (self.pixel_spacing_source == "Estimated from dimensions")
        for pt1, pt2, length in self.points:
            self.ax.plot([pt1[0], pt2[0]], [pt1[1], pt2[1]], "r-", linewidth=2.5)
            self.ax.plot(pt1[0], pt1[1], "ro", markersize=8)
            self.ax.plot(pt2[0], pt2[1], "ro", markersize=8)
            mx, my = (pt1[0] + pt2[0]) / 2, (pt1[1] + pt2[1]) / 2
            if is_estimated:
                unc = get_uncertainty_pct(self.columns)
                label = f"~{length:.2f} mm (+/-{unc}%)"
                txt_color = "orange"
            else:
                label = f"{length:.2f} mm"
                txt_color = "yellow"
            self.ax.text(mx, my, label, color=txt_color, fontsize=12,
                         fontweight="bold", ha="center", va="center",
                         bbox=dict(boxstyle="round,pad=0.3", facecolor="black", alpha=0.8))

        if self.temp_point and self.current_mouse:
            x1, y1 = self.temp_point
            x2, y2 = self.current_mouse
            self.ax.plot([x1, x2], [y1, y2], "g--", linewidth=2, alpha=0.7)
            dist = self.calculate_distance(self.temp_point, self.current_mouse)
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if is_estimated:
                unc = get_uncertainty_pct(self.columns)
                tmp_label = f"~{dist:.2f} mm (+/-{unc}%)"
                tmp_color = "orange"
            else:
                tmp_label = f"{dist:.2f} mm"
                tmp_color = "lime"
            self.ax.text(mx, my, tmp_label, color=tmp_color, fontsize=11,
                         ha="center", va="center",
                         bbox=dict(boxstyle="round", facecolor="black", alpha=0.7))

        # WW/WC 표시
        self.ax.text(0.98, 0.02, f"WW/WC : {int(self.window_width)}/{int(self.window_center)}",
                     transform=self.ax.transAxes, color="yellow", fontsize=13, fontweight="bold",
                     ha="right", va="bottom",
                     bbox=dict(boxstyle="round,pad=0.3", facecolor="black", alpha=0.6))
        self.canvas.draw_idle()

    # ------------------- 마우스 이벤트 -------------------
    def mousePressEvent(self, e):
        if self.pixel_array is None:
            return
        img_x, img_y = self.to_data_coordinates(e)
        if e.button() == Qt.LeftButton:
            if self.temp_point is None:
                self.temp_point = (img_x, img_y)
            else:
                pt1, pt2 = self.temp_point, (img_x, img_y)
                length = self.calculate_distance(pt1, pt2)
                self.points.append((pt1, pt2, length))
                self.temp_point = None
                self.display_image()
        elif e.button() == Qt.RightButton:
            self.right_click_start = e.pos()
            self.right_click_time = time.time()
        elif e.button() == Qt.MidButton:
            self.dragging_mid = True
            self.drag_start = e.pos()

    def mouseMoveEvent(self, e):
        if self.pixel_array is None:
            return
        img_x, img_y = self.to_data_coordinates(e)
        if self.temp_point:
            self.current_mouse = (img_x, img_y)
            self.display_image()
            return

        if e.buttons() & Qt.RightButton and self.right_click_start:
            delta = e.pos() - self.right_click_start
            self.window_center += delta.y() * 0.5
            self.window_width += delta.x() * 1.0
            self.window_width = max(1, self.window_width)
            self.right_click_start = e.pos()
            self.display_image()

        if e.buttons() & Qt.MidButton and self.dragging_mid and self.drag_start is not None:
            delta = e.pos() - self.drag_start
            scale = 1.0 / self.zoom_factor
            self.center_x -= delta.x() * scale
            self.center_y -= delta.y() * scale
            self.drag_start = e.pos()
            self.display_image()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            if time.time() - self.right_click_time < 0.25 and self.temp_point:
                self.temp_point = None
                self.current_mouse = None
                self.display_image()
            self.right_click_start = None
        elif e.button() == Qt.MidButton:
            self.dragging_mid = False
            self.drag_start = None

    # ------------------- 휠 줌 -------------------
    def wheelEvent(self, e):
        if self.pixel_array is None:
            return
        delta = e.angleDelta().y()
        if delta:
            factor = 1.05 if delta > 0 else 0.95
            self.zoom_factor = max(0.2, min(self.zoom_factor * factor, 10))
            self.display_image()

    # ------------------- 좌표 변환 -------------------
    def to_data_coordinates(self, e):
        # 화면 좌표를 Figure 좌표로 변환
        inv = self.ax.transData.inverted()
        x, y = e.pos().x(), e.pos().y()

        # PyQt의 좌표계(QPoint)는 위쪽이 0, Matplotlib은 아래쪽이 0
        # 이를 보정하기 위해 y축을 반전시켜준다.
        y = self.canvas.height() - y

        # 이벤트 좌표를 데이터 좌표로 변환
        data_x, data_y = inv.transform((x, y))
        return data_x, data_y

    # ------------------- 거리 계산 -------------------
    def calculate_distance(self, pt1: tuple[float, float], pt2: tuple[float, float]) -> float:
        """Calculate the real-world distance between two image points.

        Args:
            pt1: First point in image (pixel) coordinates.
            pt2: Second point in image (pixel) coordinates.

        Returns:
            Distance in millimeters.

        Raises:
            RuntimeError: If called before a DICOM file has been loaded.
        """
        if self.pixel_spacing is None:
            raise RuntimeError("calculate_distance() called before a DICOM file was loaded.")
        dx, dy = pt2[0] - pt1[0], pt2[1] - pt1[1]
        return math.hypot(dx, dy) * self.pixel_spacing

    # ------------------- JSON 저장 -------------------
    def save_json(self):
        if not self.dicom_file:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save JSON", "", "JSON Files (*.json)")
        if not path:
            return
        data = {
            "filename": os.path.basename(self.dicom_file),
            "dicom_data": {
                "rows": self.rows,
                "columns": self.columns,
                "pixel_spacing": self.pixel_spacing,
                "pixel_spacing_source": self.pixel_spacing_source
            },
            "point": []
        }
        for i, (pt1, pt2, length) in enumerate(self.points, 1):
            data["point"].append({
                "point_number": i,
                "contour": [{"x": pt1[0], "y": pt1[1]}, {"x": pt2[0], "y": pt2[1]}],
                "length": length
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        QMessageBox.information(self, "Success", "JSON saved!")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    icon_path = get_resource_path("measure.ico")
    app.setWindowIcon(QIcon(icon_path))
    viewer = DicomLength()
    viewer.show()
    sys.exit(app.exec_())
