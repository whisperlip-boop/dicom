import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
                             QLabel, QLineEdit, QPushButton, QCheckBox,
                             QTextEdit, QGroupBox, QMessageBox, QProgressBar)
from PyQt5.QtCore import QRegExp, QThread, pyqtSignal
from PyQt5.QtGui import QRegExpValidator, QIcon
import pydicom
import glob
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed


_AND_STYLE = (
    "QPushButton {"
    "  background-color: #2563EB;"
    "  color: #FFFFFF;"
    "  border: none;"
    "  border-radius: 3px;"
    "  font-weight: bold;"
    "  padding: 2px;"
    "}"
)

_OR_STYLE = (
    "QPushButton {"
    "  background-color: #F59E0B;"
    "  color: #111111;"
    "  border: none;"
    "  border-radius: 3px;"
    "  font-weight: bold;"
    "  padding: 2px;"
    "}"
)


def get_resource_path(relative_path: str) -> str:
    """스크립트 위치를 기준으로 리소스 파일의 절대 경로를 반환한다.

    PyInstaller 빌드 시에는 sys._MEIPASS, 일반 실행 시에는 스크립트 디렉토리를 기준으로 한다.

    Args:
        relative_path: 애플리케이션 루트 기준 상대 경로.

    Returns:
        리소스 파일의 절대 경로.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class IndexingWorker(QThread):
    """백그라운드에서 DICOM 파일을 탐색하고 인덱싱하는 워커 스레드.

    파일 탐색(Phase 1)과 인덱싱(Phase 2)을 순차 수행한다.
    탐색 단계에서는 total=0 신호로 indeterminate 진행 바를 표시하고,
    인덱싱 단계에서는 정확한 진행률을 전달한다.
    """

    # current: 처리 완료 수, total: 전체 수 (0이면 탐색 중 — indeterminate), message: 상태 텍스트
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, search_path: str, recursive: bool, max_workers: int = 4) -> None:
        """워커를 초기화한다.

        Args:
            search_path: DICOM 파일을 탐색할 루트 경로.
            recursive: 하위 폴더 포함 여부.
            max_workers: 병렬 파일 읽기 스레드 수.
        """
        super().__init__()
        self.search_path = search_path
        self.recursive = recursive
        self.max_workers = max_workers

    def run(self) -> None:
        """파일 탐색 → 인덱싱 두 단계로 실행한다.

        탐색 단계에서는 total=0 신호로 indeterminate 진행 바를 표시하고,
        인덱싱 단계에서는 정확한 진행률을 전달한다.
        """
        try:
            self.progress.emit(0, 0, "Scanning for DICOM files...")
            pattern = os.path.join(
                self.search_path,
                "**/*.dcm" if self.recursive else "*.dcm"
            )
            files = glob.glob(pattern, recursive=self.recursive)
            total_files = len(files)

            if total_files == 0:
                self.finished.emit({'dicom_index': {}, 'dicom_tag_values': {}, 'total_files': 0})
                return

            dicom_index: defaultdict[str, list[str]] = defaultdict(list)
            dicom_tag_values: defaultdict[str, dict[str, str]] = defaultdict(dict)

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self._read_dicom_file, file_path): file_path
                    for file_path in files
                }

                for idx, future in enumerate(as_completed(futures)):
                    file_path = futures[future]
                    try:
                        file_tags = future.result()
                        if file_tags is not None:
                            dicom_tag_values[file_path] = file_tags
                            for tag in file_tags.keys():
                                dicom_index[tag].append(file_path)
                    except Exception as e:
                        print(f"읽기 실패 {file_path}: {e}")

                    self.progress.emit(idx + 1, total_files, f"{idx + 1} / {total_files} files indexed")

            self.finished.emit({
                'dicom_index': dict(dicom_index),
                'dicom_tag_values': dicom_tag_values,
                'total_files': total_files
            })
        except Exception as e:
            self.error.emit(f"인덱싱 중 오류 발생: {str(e)}")

    def _read_dicom_file(self, file_path: str) -> dict[str, str] | None:
        """단일 DICOM 파일을 읽고 태그 정보를 반환한다.

        Args:
            file_path: 읽을 DICOM 파일의 절대 경로.

        Returns:
            태그 키('GGGG,EEEE')와 값의 딕셔너리. 읽기 실패 시 None.
        """
        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True)
            file_tags: dict[str, str] = {}
            for elem in ds:
                tag = elem.tag
                full_tag = f"{tag.group:04X},{tag.element:04X}"
                file_tags[full_tag] = str(elem.value) if elem.value is not None else ""
            return file_tags
        except Exception:
            return None


class DICOMTagSearcher(QMainWindow):
    """DICOM 파일 인덱싱 및 태그 검색 GUI 애플리케이션.

    지정된 폴더의 DICOM 파일을 백그라운드에서 인덱싱한 후,
    복수 태그 조건을 And/Or 그룹(DNF) 방식으로 검색한다.
    각 태그 입력 행의 And/Or 토글로 조건 그룹을 구성한다:
    - 연속된 And 행은 하나의 AND 그룹을 형성한다.
    - Or 토글은 새 그룹을 시작하며, 최종 결과는 모든 그룹의 합집합이다.
    """

    _TOGGLE_WIDTH: int = 44

    def __init__(self) -> None:
        """애플리케이션을 초기화한다."""
        super().__init__()
        self.setWindowTitle("DICOM 태그 정밀 검색기")
        self.setGeometry(100, 100, 1000, 900)

        self.dicom_index: defaultdict[str, list[str]] = defaultdict(list)
        self.dicom_tag_values: defaultdict[str, dict[str, str]] = defaultdict(dict)
        self.all_dicom_files: list[str] = []
        # 각 항목: (row_widget, group_edit, elem_edit, value_edit, toggle_btn)
        # 첫 번째 행의 toggle_btn은 None
        self.tag_widgets: list[tuple] = []
        self.indexing_worker: IndexingWorker | None = None

        self.init_ui()

    def init_ui(self) -> None:
        """메인 UI 레이아웃을 구성한다."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # 검색 경로
        path_group = QGroupBox("검색 경로")
        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("예: C:/DICOM_DATA")
        self.index_btn = QPushButton("검색 시작")
        self.index_btn.clicked.connect(self.index_dicom_files)
        self.subdir_cb = QCheckBox("하위 폴더 포함")
        self.subdir_cb.setChecked(True)
        path_layout.addWidget(QLabel("경로:"))
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.index_btn)
        path_layout.addWidget(self.subdir_cb)
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

        # 태그 입력 그룹
        self.tag_group = QGroupBox("태그 입력 (Group, Element, Value)")
        self.tag_layout = QVBoxLayout()
        self.add_tag_row(is_first=True)
        self.tag_group.setLayout(self.tag_layout)
        layout.addWidget(self.tag_group)

        # 프로그레스 바 (숨김 상태)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 결과 출력
        self.result_text = QTextEdit()
        self.result_text.setPlaceholderText("검색 결과가 여기에 표시됩니다...")
        layout.addWidget(self.result_text)

    @staticmethod
    def _apply_toggle_style(btn: QPushButton, checked: bool) -> None:
        """And/Or 토글 버튼의 텍스트와 색상을 상태에 맞게 업데이트한다.

        Args:
            btn: 업데이트할 토글 QPushButton.
            checked: True이면 Or 상태, False이면 And 상태.
        """
        btn.setText("Or" if checked else "And")
        btn.setStyleSheet(_OR_STYLE if checked else _AND_STYLE)

    def add_tag_row(self, is_first: bool = False) -> None:
        """태그 입력 행을 추가한다.

        첫 번째 행(is_first=True)은 And/Or 토글 버튼 대신 동일 너비의 여백을 배치하며
        삭제 버튼도 추가하지 않는다. 이후 행에는 이전 조건과의 AND/OR 관계를 설정하는
        토글 버튼(기본값 And)과 삭제(-) 버튼이 포함된다.

        Args:
            is_first: True이면 토글/삭제 없이 여백만 배치한다.
        """
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        if is_first:
            spacer = QWidget()
            spacer.setFixedWidth(self._TOGGLE_WIDTH)
            row_layout.addWidget(spacer)
            toggle_btn = None
        else:
            toggle_btn = QPushButton("And")
            toggle_btn.setCheckable(True)
            toggle_btn.setFixedWidth(self._TOGGLE_WIDTH)
            toggle_btn.setStyleSheet(_AND_STYLE)
            toggle_btn.toggled.connect(
                lambda checked, btn=toggle_btn: self._apply_toggle_style(btn, checked)
            )
            row_layout.addWidget(toggle_btn)

        group_edit = QLineEdit()
        group_edit.setPlaceholderText("0010")
        group_edit.setMaxLength(4)
        group_edit.setFixedWidth(60)

        elem_edit = QLineEdit()
        elem_edit.setPlaceholderText("0010")
        elem_edit.setMaxLength(4)
        elem_edit.setFixedWidth(60)

        value_edit = QLineEdit()
        value_edit.setPlaceholderText("값 (비워두면 Tag 존재 여부만 체크)")

        hex_validator = QRegExpValidator(QRegExp("[0-9A-Fa-f]*"))
        group_edit.setValidator(hex_validator)
        elem_edit.setValidator(hex_validator)

        plus_btn = QPushButton("+")
        plus_btn.setFixedWidth(30)
        plus_btn.clicked.connect(lambda: self.add_tag_row(is_first=False))

        row_layout.addWidget(QLabel("Group:"))
        row_layout.addWidget(group_edit)
        row_layout.addWidget(QLabel("Element:"))
        row_layout.addWidget(elem_edit)
        row_layout.addWidget(QLabel("Value:"))
        row_layout.addWidget(value_edit, 1)
        row_layout.addWidget(plus_btn)

        if not is_first:
            minus_btn = QPushButton("-")
            minus_btn.setFixedWidth(30)
            minus_btn.clicked.connect(lambda: self.remove_tag_row(row_widget))
            row_layout.addWidget(minus_btn)

        self.tag_layout.addWidget(row_widget)
        self.tag_widgets.append((row_widget, group_edit, elem_edit, value_edit, toggle_btn))

    def remove_tag_row(self, widget: QWidget) -> None:
        """지정된 태그 입력 행을 제거한다.

        첫 번째 행은 삭제 버튼이 없으므로 이 메서드로 제거될 수 없다.
        전체 행 수가 1개이면 삭제를 막고 경고를 표시한다.

        Args:
            widget: 제거할 행의 QWidget.
        """
        if len(self.tag_widgets) <= 1:
            QMessageBox.warning(self, "경고", "최소 1개의 태그 입력은 유지해야 합니다.")
            return
        for i, (w, _, _, _, _) in enumerate(self.tag_widgets):
            if w == widget:
                self.tag_layout.removeWidget(w)
                w.deleteLater()
                self.tag_widgets.pop(i)
                break

    def index_dicom_files(self) -> None:
        """태그 유효성 및 경로를 검증한 후 백그라운드 인덱싱 워커를 시작한다.

        태그 미입력 또는 경로 오류 시 경고를 표시하고 종료한다.
        이전 워커가 실행 중이면 먼저 종료시킨다.
        """
        search_path = self.path_input.text().strip()

        if not self.has_valid_tag_condition():
            QMessageBox.warning(
                self,
                "경고",
                "태그 ID를 입력한 후 검색을 실행하세요."
            )
            return

        if not search_path or not os.path.exists(search_path):
            QMessageBox.warning(self, "경고", "유효한 검색 경로를 입력하세요.")
            return

        if self.indexing_worker and self.indexing_worker.isRunning():
            self.indexing_worker.quit()
            self.indexing_worker.wait()

        self.index_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.result_text.setText("Scanning for DICOM files...")

        self.indexing_worker = IndexingWorker(search_path, self.subdir_cb.isChecked())
        self.indexing_worker.progress.connect(self._on_indexing_progress)
        self.indexing_worker.finished.connect(self._on_indexing_finished)
        self.indexing_worker.error.connect(self._on_indexing_error)
        self.indexing_worker.start()

    def _on_indexing_progress(self, current: int, total: int, message: str) -> None:
        """인덱싱 진행상황을 업데이트한다.

        Args:
            current: 처리 완료된 파일 수.
            total: 전체 파일 수 (0이면 탐색 중 — indeterminate 유지).
            message: 표시할 상태 텍스트.
        """
        if total > 0:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.result_text.setText(message)

    def _on_indexing_finished(self, result: dict) -> None:
        """인덱싱 완료 후 결과를 저장하고 자동 검색을 실행한다.

        Args:
            result: 'dicom_index', 'dicom_tag_values', 'total_files' 키를 가진 딕셔너리.
        """
        self.index_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)

        if result['total_files'] == 0:
            self.result_text.clear()
            QMessageBox.information(self, "No Files Found", "No DICOM files were found in the specified path.")
            return

        self.dicom_index = defaultdict(list, result['dicom_index'])
        self.dicom_tag_values = result['dicom_tag_values']
        self.all_dicom_files = list(result['dicom_tag_values'].keys())

        QMessageBox.information(
            self, "Indexing Complete",
            f"{result['total_files']} DICOM files indexed."
        )

        if self.has_valid_tag_condition():
            self.search_and_display()

    def _on_indexing_error(self, error_msg: str) -> None:
        """인덱싱 에러를 처리한다.

        Args:
            error_msg: 표시할 에러 메시지.
        """
        self.index_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "에러", error_msg)

    def has_valid_tag_condition(self) -> bool:
        """유효한 태그 조건(4자리 Group + Element)이 하나 이상 입력되었는지 확인한다.

        Returns:
            유효한 태그가 하나 이상 존재하면 True, 아니면 False.
        """
        for _, group_edit, elem_edit, _, _ in self.tag_widgets:
            g = group_edit.text().strip()
            e = elem_edit.text().strip()
            if g and e and len(g) == 4 and len(e) == 4:
                return True
        return False

    def value_matches(self, stored_value: str, query_value: str) -> bool:
        """SQL LIKE 스타일 와일드카드 비교를 수행한다.

        Args:
            stored_value: 인덱스에 저장된 태그 값.
            query_value: 검색 입력값. '*'를 와일드카드로 사용한다.

        Returns:
            값이 조건에 부합하면 True.

        Examples:
            *abc  → endswith 'abc'
            abc*  → startswith 'abc'
            *abc* → contains 'abc'
            abc   → exact match
        """
        if not query_value:
            return True

        if query_value.startswith("*") and query_value.endswith("*"):
            return query_value.strip("*") in stored_value

        if query_value.startswith("*"):
            return stored_value.endswith(query_value.lstrip("*"))

        if query_value.endswith("*"):
            return stored_value.startswith(query_value.rstrip("*"))

        return stored_value == query_value

    def search_and_display(self) -> None:
        """현재 태그 조건으로 DICOM 파일을 검색하고 결과를 표시한다.

        And/Or 토글 상태를 해석하여 DNF(선언적 정규형) 구조로 검색한다.
        연속된 And 행은 하나의 AND 그룹을 형성하고, Or 토글은 새 그룹을 시작한다.
        최종 결과는 모든 AND 그룹의 합집합(OR)이다.

        예) Row1, Row2[And], Row3[And], Row4[Or], Row5[And]
            → (Row1 AND Row2 AND Row3) OR (Row4 AND Row5)
        """
        # 유효한 조건 수집: (tag_key, value, is_or)
        raw_conditions: list[tuple[str, str, bool]] = []
        for _, group_edit, elem_edit, value_edit, toggle_btn in self.tag_widgets:
            g = group_edit.text().strip().upper()
            e = elem_edit.text().strip().upper()
            v = value_edit.text().strip()
            if g and e and len(g) == 4 and len(e) == 4:
                is_or = (toggle_btn is not None and toggle_btn.isChecked())
                raw_conditions.append((f"{g},{e}", v, is_or))

        if not raw_conditions:
            QMessageBox.warning(self, "경고", "유효한 태그를 입력하세요 (4자리 16진수).")
            return

        # Or 위치에서 AND 그룹으로 분할
        groups: list[list[tuple[str, str]]] = []
        current_group: list[tuple[str, str]] = []
        for tag, value, is_or in raw_conditions:
            if is_or and current_group:
                groups.append(current_group)
                current_group = []
            current_group.append((tag, value))
        if current_group:
            groups.append(current_group)

        # 각 AND 그룹의 교집합 → 모든 그룹의 합집합
        final_results: set[str] = set()
        for group in groups:
            # AND 최적화: 후보 파일이 가장 적은 태그부터 처리
            group_sorted = sorted(group, key=lambda x: len(self.dicom_index.get(x[0], [])))
            group_results: set[str] = set(self.all_dicom_files)
            for tag, value in group_sorted:
                matching: set[str] = {
                    fp for fp in self.dicom_index.get(tag, [])
                    if not value or self.value_matches(
                        self.dicom_tag_values.get(fp, {}).get(tag, ""), value
                    )
                }
                group_results &= matching
                if not group_results:
                    break
            final_results |= group_results

        # 사람이 읽을 수 있는 조건 표현식 구성
        group_strs = [
            " AND ".join(f"{t}={v}" if v else t for t, v in g)
            for g in groups
        ]
        logic_str = (
            " OR ".join(f"({s})" for s in group_strs)
            if len(groups) > 1
            else group_strs[0]
        )

        txt = "=== DICOM 태그 정밀 검색 결과 ===\n"
        txt += f"조건: {logic_str}\n"
        txt += f"총 {len(final_results)}개 파일\n\n"
        for file_path in sorted(final_results):
            txt += f"{file_path}\n"

        self.result_text.setText(txt)

        if final_results:
            save_path = "dicom_search_results.txt"
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(txt)
            QMessageBox.information(self, "저장 완료", f"결과가 '{save_path}'에 저장되었습니다.")
        else:
            QMessageBox.information(self, "결과 없음", "조건에 맞는 파일이 없습니다.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    icon_path = get_resource_path("searcher.ico")
    app.setWindowIcon(QIcon(icon_path))
    window = DICOMTagSearcher()
    window.setWindowIcon(QIcon(icon_path))
    window.show()
    sys.exit(app.exec_())
