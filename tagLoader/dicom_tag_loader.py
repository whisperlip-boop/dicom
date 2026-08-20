import os
import sys
from typing import List, Optional, Tuple, cast

import numpy as np
import pydicom
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QMessageBox,
    QLineEdit,
    QSizePolicy,
    QSpacerItem,
)
from PyQt5.QtCore import QMimeData, Qt
from PyQt5.QtGui import (
    QClipboard,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QIntValidator,
)
from pydicom.dataelem import DataElement
from pydicom.dataset import FileDataset
from pydicom.misc import is_dicom


def get_resource_path(filename: str) -> str:
    """Return the absolute path to a resource, supporting PyInstaller bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(__file__), filename)

class DicomTagLoader(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.pixel_data_value: Optional[np.ndarray] = None
        self.initUI()

    def initUI(self) -> None:
        self.setWindowTitle("DICOM Tag Loader")
        self.setGeometry(100, 100, 1200, 900)
        self.setAcceptDrops(True)
        layout = QVBoxLayout()

        # File Open 버튼 및 File path 표시 레이블 가로 레이아웃
        h_layout = QHBoxLayout()
        self.open_button = QPushButton("File Open", self)
        self.open_button.setFixedWidth(100)
        self.open_button.clicked.connect(self.openFile)
        h_layout.addWidget(self.open_button)

        self.file_path = QLabel("File path will appear here")
        h_layout.addWidget(self.file_path)
        layout.addLayout(h_layout)

        # 검색 및 버튼 Layout 설정
        function_layout = QHBoxLayout()

        # Group/Element/Description 세트를 2개 배치: 세트 내부는 AND, 세트 간은 OR로 결합
        self.filter_groups: List[Tuple[QLineEdit, QLineEdit, QLineEdit]] = [
            self._create_filter_inputs(function_layout)
        ]
        function_layout.addWidget(QLabel("OR"))
        self.filter_groups.append(self._create_filter_inputs(function_layout))

        self.clear_button = QPushButton("X")
        self.clear_button.setFixedWidth(20)
        self.clear_button.clicked.connect(self.clearSearchFields)
        function_layout.addWidget(self.clear_button)
        function_layout.addStretch()

        function_layout.addSpacerItem(
            QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )

        self.copy_all_button = QPushButton("All Copy", self)
        self.copy_all_button.setFixedWidth(75)
        self.copy_value_button = QPushButton("Value Copy", self)
        self.copy_value_button.setFixedWidth(95)

        self.copy_all_button.clicked.connect(self.copyAll)
        self.copy_value_button.clicked.connect(
            lambda: self.copyValue(self._current_table())
        )

        function_layout.addStretch()
        function_layout.addWidget(self.copy_all_button)
        function_layout.addWidget(self.copy_value_button)
        layout.addLayout(function_layout)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.setLayout(layout)

        self.initEmptyTabs()

    def _create_filter_inputs(
        self, row_layout: QHBoxLayout
    ) -> Tuple[QLineEdit, QLineEdit, QLineEdit]:
        """Group/Element/Description 입력 세트 하나를 만들어 row_layout에 배치한다."""
        group_input = QLineEdit()
        group_input.setPlaceholderText("Group")
        group_input.setFixedWidth(60)
        group_input.setValidator(QIntValidator(0, 9999))

        element_input = QLineEdit()
        element_input.setPlaceholderText("Element")
        element_input.setFixedWidth(60)
        element_input.setValidator(QIntValidator(0, 9999))

        description_input = QLineEdit()
        description_input.setPlaceholderText("Description")
        description_input.setFixedWidth(200)

        row_layout.addWidget(group_input)
        row_layout.addWidget(element_input)
        row_layout.addWidget(description_input)

        group_input.textChanged.connect(self.filterTable)
        element_input.textChanged.connect(self.filterTable)
        description_input.textChanged.connect(self.filterTable)

        return group_input, element_input, description_input

    def initEmptyTabs(self) -> None:
        self.addTab(None, "All")
        self.addTab(None, "Patient")
        self.addTab(None, "Study/Series")
        self.addTab(None, "Image")

    def openFile(self, filepath: Optional[str] = None) -> None:
        if not filepath:
            file_dialog = QFileDialog.getOpenFileName(
                self, "Open DICOM File", "", "DICOM Files (*.dcm)"
            )
            filepath = file_dialog[0]
        if not filepath:
            return

        self.file_path.setText(filepath)
        try:
            if not is_dicom(filepath):
                QMessageBox.critical(
                    self, "Error", "The selected file is not a valid DICOM file."
                )
                return
            dicom_data = pydicom.dcmread(filepath, force=True)

            # 현재 선택된 탭 인덱스 저장
            current_tab_index = self.tabs.currentIndex()
            current_tab_name = self.tabs.tabText(current_tab_index) if current_tab_index >= 0 else "All"

            # 탭 유지 및 내용 갱신
            self.tabs.clear()
            self.pixel_data_value = None
            self.addTab(dicom_data, "All")
            self.addTab(dicom_data, "Patient")
            self.addTab(dicom_data, "Study/Series")
            self.addTab(dicom_data, "Image")

            # 이전 탭으로 복원
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == current_tab_name:
                    self.tabs.setCurrentIndex(i)
                    break
            else:
                self.tabs.setCurrentIndex(0)  # 탭 이름이 없으면 All 탭으로

            # 새로 만들어진 탭에도 기존 검색 필터를 재적용
            self.filterTable()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error reading DICOM file: {e}")

    def addTab(self, dicom_data: Optional[FileDataset], tag_type: str) -> None:
        table = QTableWidget()
        elements: List[DataElement] = []

        if tag_type == "All" and dicom_data is not None and hasattr(dicom_data, "file_meta"):
            for element in dicom_data.file_meta:
                elements.append(element)

        if dicom_data is not None:
            seen_tags = set()

            for element in dicom_data.iterall():
                # 최소 수정안:
                # 기존에는 (group, element)만 같으면 뒤의 항목을 모두 숨겼습니다.
                # 이제는 같은 tag라도 value가 다르면 별도로 보여주고,
                # value까지 같을 때만 중복으로 간주합니다.
                try:
                    value_key = repr(element.value)
                except Exception:
                    value_key = str(element.value)

                tag_key = (element.tag.group, element.tag.element, value_key)

                if tag_key not in seen_tags:
                    seen_tags.add(tag_key)
                    if element.name in ["PixelHeight", "PixelWidth"] and element.value == 0:
                        element.value = 1
                        print(f"Warning: Invalid value for {element.name}. Defaulting to 1.")
                    if element.tag.group == 0x7FE0 and element.tag.element == 0x0010:
                        self.pixel_data_value = np.copy(element.value)
                    if tag_type == "All":
                        elements.append(element)
                    elif tag_type == "Patient" and element.tag.group == 0x0010:
                        elements.append(element)
                    elif tag_type == "Study/Series" and (
                        "Study" in element.name or "Series" in element.name
                    ):
                        elements.append(element)
                    elif tag_type == "Image" and (
                        element.tag.group == 0x0028
                        or "Pixel" in element.name
                        or "Bit" in element.name
                        or "Image" in element.name
                        or "Window" in element.name
                    ):
                        elements.append(element)

        elements.sort(key=lambda x: (x.tag.group, x.tag.element))

        table.setRowCount(len(elements))
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(
            ["Group", "Element", "Description", "VR", "Size", "Value"]
        )

        table.setColumnWidth(0, 55)
        table.setColumnWidth(1, 55)
        table.setColumnWidth(2, 230)
        table.setColumnWidth(3, 25)
        table.setColumnWidth(4, 30)
        header = cast(QHeaderView, table.horizontalHeader())
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)

        for row, element in enumerate(elements):
            table.setItem(row, 0, QTableWidgetItem(f"{element.tag.group:04X}"))
            table.setItem(row, 1, QTableWidgetItem(f"{element.tag.element:04X}"))
            table.setItem(row, 2, QTableWidgetItem(element.name))
            table.setItem(row, 3, QTableWidgetItem(element.VR))
            size = len(str(element.value)) if element.value else 0
            table.setItem(row, 4, QTableWidgetItem(str(size)))
            if dicom_data is not None and element.tag.group == 0x0010 and element.tag.element == 0x0010:
                table.setItem(row, 5, QTableWidgetItem(str(dicom_data.PatientName)))
            elif element.tag.group == 0x7FE0 and element.tag.element == 0x0010:
                table.setItem(row, 5, QTableWidgetItem("Encoded graphical image data"))
            else:
                table.setItem(row, 5, QTableWidgetItem(str(element.value)))

            if row % 2 == 0:
                for col in range(table.columnCount()):
                    item = table.item(row, col)
                    if item:
                        item.setBackground(QColor(0xFB, 0xFA, 0xFB))

        self.tabs.addTab(table, tag_type)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime_data = cast(QMimeData, event.mimeData())
        if mime_data.hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        mime_data = cast(QMimeData, event.mimeData())
        if mime_data.hasUrls():
            file_url = mime_data.urls()[0]
            filepath = file_url.toLocalFile()
            try:
                if not is_dicom(filepath):
                    QMessageBox.warning(
                        self, "Error", "The dropped file is not a valid DICOM file."
                    )
                    return
                self.openFile(filepath)
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Error processing the dropped file: {e}"
                )
        else:
            QMessageBox.warning(
                self, "Error", "The dropped content is not a valid file."
            )

    def _current_table(self) -> QTableWidget:
        """탭에는 항상 addTab()에서 만든 QTableWidget만 들어있음을 전제로 반환한다."""
        return cast(QTableWidget, self.tabs.currentWidget())

    def _get_row_data(
        self, table: QTableWidget, selected_row: int, columns: Optional[int] = None
    ) -> List[str]:
        """
        행 데이터를 가져옴
        columns=None: 모든 열 복사
        columns=정수: 해당 열만 복사
        """
        row_data: List[str] = []

        if columns is None:
            cols_to_copy = range(table.columnCount())
        else:
            cols_to_copy = [columns]

        group_item = table.item(selected_row, 0)
        element_item = table.item(selected_row, 1)
        is_pixel_data = (
            group_item is not None
            and element_item is not None
            and group_item.text().strip().upper() == "7FE0"
            and element_item.text().strip().upper() == "0010"
        )

        for col in cols_to_copy:
            item = table.item(selected_row, col)
            if item:
                # PixelData (0x7FE0, 0x0010) 특수처리
                if col == 5 and is_pixel_data and self.pixel_data_value is not None:
                    row_data.append(np.array2string(self.pixel_data_value))
                else:
                    row_data.append(item.text())

        return row_data

    def copyValue(self, table: QTableWidget) -> None:
        """선택된 행의 Value 열(column 5)만 복사"""
        selected_row = table.currentRow()
        if selected_row >= 0:
            row_data = self._get_row_data(table, selected_row, columns=5)
            clipboard = cast(QClipboard, QApplication.clipboard())
            clipboard.setText("\t".join(row_data))
        else:
            print("No row selected.")

    def copyAll(self) -> None:
        """선택된 행의 모든 열 복사"""
        table = self._current_table()
        selected_row = table.currentRow()
        if selected_row >= 0:
            row_data = self._get_row_data(table, selected_row)  # columns=None
            clipboard = cast(QClipboard, QApplication.clipboard())
            clipboard.setText("\t".join(row_data))
        else:
            print("No row selected.")

    @staticmethod
    def _matches_condition(
        group_item: Optional[QTableWidgetItem],
        element_item: Optional[QTableWidgetItem],
        description_item: Optional[QTableWidgetItem],
        group_id: str,
        element_id: str,
        description_txt: str,
    ) -> bool:
        """세트 하나(Group/Element/Description)의 AND 조건을 만족하는지 확인."""
        group_match = not group_id or (
            group_item is not None and group_item.text().startswith(group_id)
        )
        element_match = not element_id or (
            element_item is not None and element_item.text().startswith(element_id)
        )
        description_match = not description_txt or (
            description_item is not None
            and description_txt in description_item.text().lower()
        )
        return group_match and element_match and description_match

    def filterTable(self) -> None:
        conditions = [
            (
                group_input.text().strip(),
                element_input.text().strip(),
                description_input.text().strip().lower(),
            )
            for group_input, element_input, description_input in self.filter_groups
        ]
        # 값이 하나도 입력되지 않은 세트는 OR 조건에서 제외한다.
        # (모든 세트가 비어 있을 때만 전체 표시로 취급)
        active_conditions = [c for c in conditions if any(c)]

        for i in range(self.tabs.count()):
            table = cast(QTableWidget, self.tabs.widget(i))
            for row in range(table.rowCount()):
                group_item = table.item(row, 0)
                element_item = table.item(row, 1)
                description_item = table.item(row, 2)
                matches = not active_conditions or any(
                    self._matches_condition(
                        group_item, element_item, description_item, *condition
                    )
                    for condition in active_conditions
                )
                table.setRowHidden(row, not matches)

    def clearSearchFields(self) -> None:
        for group_input, element_input, description_input in self.filter_groups:
            group_input.clear()
            element_input.clear()
            description_input.clear()
        self.filterTable()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    icon_path = get_resource_path("tag.ico")
    app.setWindowIcon(QIcon(icon_path))
    viewer = DicomTagLoader()
    viewer.show()
    sys.exit(app.exec_())
