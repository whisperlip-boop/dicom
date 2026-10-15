"""Tag Checker 메인 윈도우."""
import csv
import os
from typing import Any

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QStackedWidget, QSizePolicy,
    QMessageBox
)
from PyQt5.QtGui import QColor, QDragEnterEvent, QDropEvent
from PyQt5.QtCore import Qt

import pydicom

from extractor import extract_tags, is_result_file, sorted_tags, verify_pair
from tooltips import tag_tooltip

ORIGINAL_BADGE = ("Original", "#00af54", "#ffffff")
RESULT_BADGE = ("Result", "#fbaf00", "#ffffff")


def status_color(ok: bool) -> QColor:
    """OK/Fail 여부에 따른 Status 셀 배경색을 반환한다.

    Args:
        ok: True이면 OK 색상, False이면 Fail 색상.

    Returns:
        OK이면 #a8f28a, Fail이면 #f28a8c QColor 객체.
    """
    return QColor("#a8f28a") if ok else QColor("#f28a8c")


def alternate_row_color(row: int) -> QColor:
    """행 번호에 따른 교대 배경색을 반환한다.

    Args:
        row: 0-based 행 번호.

    Returns:
        짝수 행이면 #FBFAFB, 홀수 행이면 #FFFFFF QColor 객체.
    """
    return QColor("#FBFAFB") if row % 2 == 0 else QColor("#FFFFFF")


def make_badge(text: str, background: str, foreground: str) -> QLabel:
    """텍스트를 겨우 감싸는 크기의 둥근 모서리 Bold 배지 라벨을 만든다.

    Args:
        text: 배지 문구.
        background: 배지 배경색(#RRGGBB).
        foreground: 배지 텍스트색(#RRGGBB).

    Returns:
        고정 크기 정책이 적용된 QLabel.
    """
    badge = QLabel(text)
    badge.setStyleSheet(
        f"background-color: {background}; color: {foreground}; font-weight: bold;"
        " border-radius: 4px; padding: 1px 10px;"
    )
    badge.setAlignment(Qt.AlignCenter)
    # 가로는 그리드 열 너비(가장 긴 배지)까지 늘어나 배지 너비가 같아지도록 Preferred
    badge.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    return badge


class TagCheckerApp(QMainWindow):
    """VUNO DICOM 결과 태그 검증 GUI 애플리케이션.

    DICOM 파일을 로드하여 각 태그의 값이 사내 규칙을 준수하는지
    테이블 형태로 표시하고 CSV로 내보내는 기능을 제공한다.
    파일 열기 버튼과 드래그 앤 드롭 두 가지 방식으로 파일을 로드할 수 있다.
    """

    def __init__(self) -> None:
        """메인 윈도우 레이아웃과 위젯을 초기화한다."""
        super().__init__()
        self.setGeometry(100, 100, 1490, 1150)
        self.setAcceptDrops(True)

        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.main_layout = QVBoxLayout(self.central)

        self.original_label = QLabel()
        self.result_label = QLabel()
        file_grid = QGridLayout()
        file_grid.setHorizontalSpacing(6)
        file_grid.setVerticalSpacing(4)
        for row, (badge_spec, name_label) in enumerate(
            [(ORIGINAL_BADGE, self.original_label), (RESULT_BADGE, self.result_label)]
        ):
            file_grid.addWidget(make_badge(*badge_spec), row, 0, Qt.AlignVCenter)
            file_grid.addWidget(name_label, row, 1, Qt.AlignLeft | Qt.AlignVCenter)

        self.load_button = QPushButton("Open DICOM")
        self.export_button = QPushButton("Export CSV")

        hbox = QHBoxLayout()
        hbox.addLayout(file_grid)
        hbox.addStretch()
        hbox.addWidget(self.load_button)
        hbox.addWidget(self.export_button)

        self.main_layout.addLayout(hbox)

        self.placeholder_label = QLabel("Original loaded")
        self.placeholder_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)  # type: ignore[operator]
        self.placeholder_label.setStyleSheet("font-size: 36px; color: #555555; background-color: white;")

        self.table = QTableWidget()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.placeholder_label)  # index 0: original-only state
        self.stack.addWidget(self.table)              # index 1: result loaded
        self.stack.setCurrentIndex(1)
        self.main_layout.addWidget(self.stack)

        self.load_button.clicked.connect(self.load_dicom)
        self.export_button.clicked.connect(self.export_csv)
        self.tags: list[dict[str, Any]] = []
        self.original_ds: pydicom.Dataset | None = None
        self.result_ds: pydicom.Dataset | None = None
        self.original_path: str = ""
        self.result_path: str = ""
        self._update_labels()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """드래그된 항목이 URL(파일)인 경우에만 드롭을 허용한다.

        Args:
            event: 드래그 진입 이벤트.
        """
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """드롭된 첫 번째 파일을 DICOM으로 로드한다.

        Args:
            event: 드롭 이벤트.
        """
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            filepath = mime.urls()[0].toLocalFile()
            if filepath:
                self.load_dicom_from_path(filepath)

    def load_dicom(self) -> None:
        """파일 다이얼로그를 열어 DICOM 파일을 선택하고 로드한다."""
        path, _ = QFileDialog.getOpenFileName(self, "Open DICOM", "", "DICOM files (*.dcm)")
        if path:
            self.load_dicom_from_path(path)

    def _update_labels(self) -> None:
        """현재 로딩 상태에 따라 파일명 라벨, 화면(테이블/Original loaded), 윈도우 타이틀을 갱신한다."""
        orig_name = os.path.basename(self.original_path) if self.original_path else ""
        result_name = os.path.basename(self.result_path) if self.result_path else ""

        orig_text = orig_name or "No original"
        if orig_name and result_name and not verify_pair(self.original_ds, self.result_ds):
            orig_text += " (mismatch)"
        self.original_label.setText(orig_text)
        self.result_label.setText(result_name or "No result")

        # 원본 재로딩 시 결과가 초기화되므로 "원본만 있음"이 곧 Original loaded 화면 조건
        self.stack.setCurrentIndex(0 if orig_name and not result_name else 1)
        self.setWindowTitle(f"Tag Checker - {result_name or orig_name or 'No file loaded'}")

    def load_dicom_from_path(self, path: str) -> None:
        """지정된 경로의 DICOM 파일을 읽고 태그를 추출하여 테이블을 갱신한다.

        (0008,0070)=="VUNO" 또는 (1001,0010)=="VUNOMED" 여부로 결과/원본을 판별하여 상태를 갱신한다.
        원본이 이미 있는 상태에서 원본을 다시 로딩하면 결과를 초기화해 처음 원본을 로딩한 상태로 되돌린다.
        두 파일이 모두 로딩되면 Study UID(또는 Patient ID)로 페어를 검증하고,
        일치하는 경우에만 원본 비교 검증((0008,0090), (0020,0010), (0008,1030))을 수행한다.

        Args:
            path: 로드할 DICOM 파일의 절대 경로.
        """
        try:
            ds = pydicom.dcmread(path, force=True)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Failed to load DICOM file.\n{e}")
            return
        # force=True는 DICOM이 아닌 파일도 빈 Dataset으로 읽으므로 원본으로 오인해 결과가 지워지지 않게 거부
        if len(ds) == 0:
            QMessageBox.warning(self, "Load Error", "The file is not a valid DICOM file.")
            return

        if is_result_file(ds):
            self.result_ds = ds
            self.result_path = path
        else:
            if self.original_ds is not None:
                # 원본 교체 = 새 비교 시작. 이전 결과는 다른 Study일 가능성이 높아 초기화
                self.result_ds = None
                self.result_path = ""
            self.original_ds = ds
            self.original_path = path

        if self.result_ds is not None:
            paired = verify_pair(self.original_ds, self.result_ds)
            self.tags = extract_tags(self.result_ds, self.original_ds if paired else None)
        else:
            self.tags = []

        self._update_labels()
        self.populate_table()

    def populate_table(self) -> None:
        """추출된 태그 정보를 GroupID 오름차순으로 정렬하여 테이블에 표시한다."""
        self.table.clear()
        self.table.setRowCount(len(self.tags))
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["GroupID", "ElementID", "Description", "Value", "Status"])
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 300)
        self.table.setColumnWidth(3, 710)
        self.table.setColumnWidth(4, 300)

        for row, tag in enumerate(sorted_tags(self.tags)):
            header = QTableWidgetItem(str(row + 1))
            header.setToolTip(tag_tooltip(tag["group"], tag["element"]))
            self.table.setVerticalHeaderItem(row, header)
            for col, key in enumerate(["group", "element", "desc", "value", "status"]):
                item = QTableWidgetItem(tag[key])
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
                if key == "status":
                    item.setBackground(status_color(tag["ok"]))
                else:
                    item.setBackground(alternate_row_color(row))
                self.table.setItem(row, col, item)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

    def export_csv(self) -> None:
        """현재 테이블 내용을 CSV 파일로 저장한다.

        태그가 없으면 아무 동작도 하지 않는다.
        파일 다이얼로그를 통해 저장 경로를 선택한다.
        """
        if not self.tags:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, mode="w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["GroupID", "ElementID", "Description", "Value", "Status"])
                for tag in sorted_tags(self.tags):
                    writer.writerow([tag["group"], tag["element"], tag["desc"], tag["value"], tag["status"]])
        except OSError as e:
            # 엑셀에서 열려 있는 파일 덮어쓰기 등 사용자 실수 대비
            QMessageBox.warning(self, "Export Error", f"Failed to save CSV file.\n{e}")
