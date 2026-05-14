"""DICOM + GSPS Viewer application.

Loads a grayscale DICOM image and an optional GSPS (Grayscale Softcopy
Presentation State) file, then renders the image with overlay annotations.
"""

import sys
import os
import ctypes
from typing import Optional

import numpy as np
import pydicom
from pydicom.dataelem import DataElement
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
    QWidget, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QStyledItemDelegate, QLineEdit, QAbstractItemView
)
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QIcon
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.patheffects as PathEffects

GSPS_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.11.1"
POLYLINE_COLOR_CURVE = '#F47378'
POLYLINE_COLOR_STRAIGHT = '#00ABF3'

TARGET_TAGS = [
    ('0010', '0010'), ('0010', '0020'), ('0008', '1090'), ('0020', '4000'),
    ('1001', '1001'), ('1001', '1008'), ('1001', '1009'), ('1001', '1011'), ('1001', '1015')
]


def is_straight_line(pts: np.ndarray, tolerance: float = 1.0) -> bool:
    """Determine whether a polyline is a straight line using collinearity check.

    Computes the cross product of the overall direction vector with each
    intermediate point's displacement from the start. A result within
    tolerance indicates collinearity (straight line).

    Args:
        pts: Array of shape (N, 2) containing polyline point coordinates.
        tolerance: Maximum allowed cross-product magnitude to still be
            considered collinear, in pixel units. Defaults to 1.0.

    Returns:
        True if all points are collinear (straight line), False otherwise.
    """
    if len(pts) <= 2:
        return True
    # Closed polylines (ROI contours) have start ≈ end, making direction ≈ [0,0].
    # The cross product would always be ~0, so treat closed polylines as curves directly.
    if np.allclose(pts[0], pts[-1], atol=1.0):
        return False
    direction = pts[-1] - pts[0]
    for p in pts[1:-1]:
        cross = abs(direction[0] * (p[1] - pts[0][1]) - direction[1] * (p[0] - pts[0][0]))
        if cross > tolerance:
            return False
    return True


def get_resource_path(relative_path: str) -> str:
    """Return absolute path to a bundled resource file.

    Works both in development and when packaged with PyInstaller,
    where files are extracted to sys._MEIPASS at runtime.

    Args:
        relative_path: Path relative to the application root directory.

    Returns:
        Absolute path string suitable for file I/O or Qt resource loading.
    """
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


class FullValueDelegate(QStyledItemDelegate):
    """Delegate that opens a read-only QLineEdit on double-click.

    Displays the full (un-truncated) tag value stored in Qt.UserRole,
    allowing text selection and Ctrl+C copy without enabling cell editing.
    """

    def createEditor(self, parent: QWidget, option, index) -> QLineEdit:
        """Create a read-only QLineEdit showing the full tag value.

        Args:
            parent: Parent widget for the editor.
            option: Style option (unused).
            index: Model index of the clicked cell.

        Returns:
            Read-only QLineEdit populated with the full value.
        """
        editor = QLineEdit(parent)
        editor.setReadOnly(True)
        return editor

    def setEditorData(self, editor: QLineEdit, index) -> None:
        """Populate the editor with the full value from UserRole data.

        Args:
            editor: The QLineEdit created by createEditor.
            index: Model index of the clicked cell.
        """
        full_value = index.data(256)  # 256 == Qt.UserRole (PyQt5 stubs do not expose this)
        editor.setText(full_value if full_value is not None else (index.data() or ''))

    def setModelData(self, editor: QLineEdit, model, index) -> None:
        """Intentionally no-op to prevent any cell value modification.

        Args:
            editor: The QLineEdit (ignored).
            model: The data model (ignored).
            index: Model index (ignored).
        """


class DicomCanvas(FigureCanvas):
    """Matplotlib canvas for rendering DICOM images with optional GSPS overlays."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize the canvas with an empty matplotlib figure.

        Args:
            parent: Parent Qt widget.
        """
        self.fig, self.ax = plt.subplots()
        self.fig.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05)
        super().__init__(self.fig)
        self.setParent(parent)
        self.image: Optional[np.ndarray] = None
        self.gsps_overlay: list[np.ndarray] = []
        self.gsps_texts: list[tuple] = []
        self.overlay_enabled: bool = True
        plt.rcParams['font.family'] = 'DejaVu Sans'

    def show_dicom(self, dicom_ds: pydicom.Dataset) -> None:
        """Render a DICOM image on the canvas.

        Args:
            dicom_ds: Loaded DICOM dataset containing pixel data.
        """
        self.ax.clear()
        self.image = dicom_ds.pixel_array
        self.ax.imshow(self.image, cmap='gray')
        h, w = self.image.shape
        self.ax.set_xlabel(f"{w}(c) × {h}(r) px")
        self.ax.set_ylabel('Pixel Y')
        self.ax.tick_params(axis='x', which='both', bottom=True, labelbottom=True)
        self.ax.tick_params(axis='y', which='both', left=True, labelleft=True)
        self.draw()

    def apply_gsps(self, gsps_ds: pydicom.Dataset) -> None:
        """Parse and apply GSPS annotations onto the current DICOM image.

        Extracts POLYLINE graphic objects and all text annotations.
        Position is resolved from Anchor Point when present, otherwise from
        Bounding Box top-left. Coordinate scaling respects the Anchor Point
        Annotation Units tag: DISPLAY (0–1 normalized) is scaled to pixel space,
        PIXEL units are used as-is.

        Args:
            gsps_ds: Loaded GSPS DICOM dataset.
        """
        if self.image is None:
            return
        self.gsps_overlay.clear()
        self.gsps_texts.clear()

        target_texts = []
        image_height, image_width = self.image.shape
        if "GraphicAnnotationSequence" in gsps_ds:
            for annotation in gsps_ds.GraphicAnnotationSequence:
                if "TextObjectSequence" in annotation:
                    for text in annotation.TextObjectSequence:
                        text_val_elem = text.get((0x0070, 0x0006), None)
                        if not text_val_elem:
                            continue

                        text_val = text_val_elem.value if isinstance(text_val_elem, DataElement) else str(text_val_elem)

                        anchor = text.get((0x0070, 0x0014), None)
                        bbox_top_left = text.get((0x0070, 0x0010), None)

                        # (0070,0004) Anchor Point Annotation Units: DISPLAY=normalized(0-1), PIXEL=pixel coords
                        anchor_units_elem = text.get((0x0070, 0x0004), None)
                        anchor_units = (
                            anchor_units_elem.value.strip().upper()
                            if isinstance(anchor_units_elem, DataElement) and anchor_units_elem.value
                            else 'DISPLAY'
                        )

                        if anchor:
                            pos = anchor.value
                            if anchor_units == 'PIXEL':
                                x, y = pos[0], pos[1]
                            else:
                                x = pos[0] * image_width
                                y = pos[1] * image_height
                        elif bbox_top_left:
                            x, y = bbox_top_left.value
                        else:
                            continue

                        justification_elem = text.get((0x0070, 0x0012), None)
                        justification = (
                            justification_elem.value if isinstance(justification_elem, DataElement)
                            else ('LEFT' if justification_elem else 'LEFT')
                        )
                        justification = justification.upper()

                        if justification == 'RIGHT':
                            ha = 'right'
                        elif justification == 'CENTER':
                            ha = 'center'
                        else:
                            ha = 'left'

                        color = 'white'
                        size = 12
                        weight = 'normal'
                        style = 'normal'

                        target_texts.append((text_val, [x, y], ha, color, size, weight, style))

                if "GraphicObjectSequence" in annotation:
                    for obj in annotation.GraphicObjectSequence:
                        if obj.GraphicType == 'POLYLINE':
                            pts = np.array(obj.GraphicData).reshape(-1, 2)
                            self.gsps_overlay.append(pts)

        self.gsps_texts = target_texts
        self.refresh_overlay()

    def refresh_overlay(self) -> None:
        """Redraw the canvas, applying GSPS overlays only when overlay_enabled is True."""
        self.ax.clear()
        if self.image is not None:
            self.ax.imshow(self.image, cmap='gray')
            h, w = self.image.shape
            self.ax.set_xlabel(f"{w}(c) × {h}(r) px")
            self.ax.set_ylabel('Pixel Y')
            self.ax.tick_params(axis='x', which='both', bottom=True, labelbottom=True)
            self.ax.tick_params(axis='y', which='both', left=True, labelleft=True)
            if self.overlay_enabled:
                for pts in self.gsps_overlay:
                    color = POLYLINE_COLOR_STRAIGHT if is_straight_line(pts) else POLYLINE_COLOR_CURVE
                    self.ax.plot(pts[:, 0], pts[:, 1], '-', color=color, linewidth=2)
                for txt, pos, align, color, size, weight, style in self.gsps_texts:
                    t = self.ax.text(pos[0], pos[1], txt, color=color, fontsize=size,
                                     weight=weight, style=style, ha=align)
                    t.set_path_effects([PathEffects.withStroke(linewidth=2, foreground='black')])
        self.draw()


class DicomGspsViewer(QMainWindow):
    """Main window for the DICOM + GSPS overlay viewer application."""

    def __init__(self) -> None:
        """Initialize the main window, canvas, overlay toggle button, and tag table."""
        super().__init__()
        self.setWindowTitle("DICOM + GSPS Viewer")
        self.setGeometry(100, 100, 900, 1300)
        self.setAcceptDrops(True)

        self.dicom_ds: Optional[pydicom.Dataset] = None
        self.gsps_ds: Optional[pydicom.Dataset] = None

        self.canvas = DicomCanvas(self)

        self.overlay_btn = QPushButton("On")
        self.overlay_btn.setFixedWidth(60)
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.setChecked(True)
        self.overlay_btn.clicked.connect(self._toggle_overlay)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.overlay_btn)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Group", "Element", "Value"])
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.table.setItemDelegateForColumn(2, FullValueDelegate(self.table))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        layout.addLayout(btn_row)
        layout.addWidget(self.canvas, stretch=5)
        layout.addWidget(self.table, stretch=2)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def _toggle_overlay(self) -> None:
        """Toggle GSPS overlay visibility and update button label to reflect state."""
        self.canvas.overlay_enabled = self.overlay_btn.isChecked()
        self.overlay_btn.setText("On" if self.overlay_btn.isChecked() else "Off")
        self.canvas.refresh_overlay()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept drag events that carry local file URLs.

        Args:
            event: Qt drag-enter event.
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        """Load DICOM or GSPS files dropped onto the window.

        GSPS files require the matching source DICOM to be loaded first.
        SOPInstanceUID matching is enforced before applying any overlay.

        Args:
            event: Qt drop event carrying file URLs.
        """
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            try:
                ds = pydicom.dcmread(file_path, force=True)
            except Exception as e:
                QMessageBox.warning(self, "File Error",
                                    f"Not a valid DICOM file: {file_path}\n{str(e)}")
                continue

            if str(ds.SOPClassUID) == GSPS_SOP_CLASS_UID:
                if self.dicom_ds is None:
                    QMessageBox.warning(self, "Load Order Error",
                                        "Please load the source DICOM image first.")
                    continue
                ref_uid = ds.ReferencedSeriesSequence[0].ReferencedImageSequence[0].ReferencedSOPInstanceUID
                if ref_uid != self.dicom_ds.SOPInstanceUID:
                    QMessageBox.warning(self, "GSPS Mismatch",
                                        "This GSPS does not match the currently loaded DICOM image.")
                    continue
                self.gsps_ds = ds
                self.canvas.apply_gsps(ds)
                self.populate_table(ds)
            else:
                self.dicom_ds = ds
                self.gsps_ds = None
                self.canvas.show_dicom(ds)
                self.setWindowTitle(f"DICOM + GSPS Viewer    [ {os.path.basename(file_path)} ]")

    def populate_table(self, ds: pydicom.Dataset) -> None:
        """Fill the tag table with TARGET_TAGS values extracted from the dataset.

        Displays up to 70 characters per value; full text is shown as a tooltip
        for truncated entries. Preserves the order defined in TARGET_TAGS.

        Args:
            ds: DICOM dataset to extract tag values from.
        """
        self.table.setRowCount(0)
        tag_elements: dict[tuple, list] = {}
        for element in ds.iterall():
            tag_key = (f"{element.tag.group:04x}", f"{element.tag.element:04x}")
            if tag_key in TARGET_TAGS:
                if tag_key not in tag_elements:
                    tag_elements[tag_key] = []
                tag_elements[tag_key].append(element)

        for tag_key in TARGET_TAGS:
            if tag_key not in tag_elements:
                continue
            for value_element in tag_elements[tag_key]:
                if isinstance(value_element, DataElement):
                    try:
                        value = str(value_element.value) if value_element.value is not None else 'None'
                    except Exception as e:
                        value = f"Error parsing: {str(e)}"
                else:
                    value = str(value_element) if value_element else 'None'

                display_value = value[:70] + '...' if len(value) > 73 else value
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(tag_key[0]))
                self.table.setItem(row, 1, QTableWidgetItem(tag_key[1]))
                value_item = QTableWidgetItem(display_value)
                value_item.setData(256, value)  # 256 == Qt.UserRole: store full text for delegate
                self.table.setItem(row, 2, value_item)


if __name__ == "__main__":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vuno.gsps_viewer")
    app = QApplication(sys.argv)
    icon_path = get_resource_path("document-viewer.ico")
    app.setWindowIcon(QIcon(icon_path))
    viewer = DicomGspsViewer()
    viewer.show()
    sys.exit(app.exec_())
