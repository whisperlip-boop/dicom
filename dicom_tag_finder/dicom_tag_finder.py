import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
                             QLabel, QLineEdit, QPushButton, QRadioButton, QCheckBox,
                             QTextEdit, QGroupBox, QButtonGroup, QMessageBox, QProgressBar)
from PyQt5.QtCore import Qt, QRegExp, QThread, pyqtSignal
from PyQt5.QtGui import (QRegExpValidator, QIcon)
import pydicom
import glob
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed


def get_resource_path(relative_path):
    """
    스크립트 위치를 기준으로 리소스 파일의 절대 경로를 반환
    (일반 실행 시와 PyInstaller 빌드 시 모두 동작)
    """
    try:
        # PyInstaller로 빌드된 경우
        base_path = sys._MEIPASS
    except AttributeError:
        # 일반 Python 실행
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


class IndexingWorker(QThread):
    """백그라운드에서 DICOM 파일을 탐색하고 인덱싱하는 워커 스레드"""
    # current: 처리 완료 수, total: 전체 수 (0이면 탐색 중 — indeterminate), message: 상태 텍스트
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, search_path: str, recursive: bool, max_workers: int = 4):
        """
        Args:
            search_path: DICOM 파일을 탐색할 루트 경로
            recursive: 하위 폴더 포함 여부
            max_workers: 병렬 파일 읽기 스레드 수
        """
        super().__init__()
        self.search_path = search_path
        self.recursive = recursive
        self.max_workers = max_workers

    def run(self) -> None:
        """
        파일 탐색 → 인덱싱 두 단계로 실행.

        탐색 단계에서는 total=0 신호로 indeterminate 진행 바를 표시하고,
        인덱싱 단계에서는 정확한 진행률을 전달한다.
        """
        try:
            # Phase 1: 파일 탐색 — 완료 전까지 개수를 알 수 없으므로 total=0으로 신호
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

            # Phase 2: 인덱싱
            dicom_index = defaultdict(list)
            dicom_tag_values = defaultdict(dict)

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

    def _read_dicom_file(self, file_path):
        """단일 DICOM 파일을 읽고 태그 정보를 반환"""
        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True)
            file_tags = {}
            for elem in ds:
                tag = elem.tag
                full_tag = f"{tag.group:04X},{tag.element:04X}"
                file_tags[full_tag] = str(elem.value) if elem.value is not None else ""
            return file_tags
        except Exception:
            return None


class DICOMTagSearcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DICOM 태그 정밀 검색기")
        self.setGeometry(100, 100, 1000, 900)

        self.dicom_index = defaultdict(list)  # tag: [file_paths]
        self.dicom_tag_values = defaultdict(dict)  # file_path: {tag: value}
        self.all_dicom_files = []
        self.tag_widgets = []  # (widget, group_edit, elem_edit, value_edit)
        self.indexing_worker = None  # 백그라운드 워커

        self.init_ui()

    def init_ui(self):
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
        self.add_tag_row()  # 첫 번째 행
        self.tag_group.setLayout(self.tag_layout)
        layout.addWidget(self.tag_group)

        # AND / OR
        logic_group = QGroupBox("검색 로직")
        logic_layout = QHBoxLayout()
        self.logic_group = QButtonGroup()
        self.and_radio = QRadioButton("AND (모든 조건 만족)")
        self.and_radio.setChecked(True)
        self.or_radio = QRadioButton("OR (하나 이상 만족)")
        self.logic_group.addButton(self.and_radio)
        self.logic_group.addButton(self.or_radio)
        logic_layout.addWidget(self.and_radio)
        logic_layout.addWidget(self.or_radio)
        logic_group.setLayout(logic_layout)
        layout.addWidget(logic_group)

        # 프로그레스 바 (숨김 상태)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 결과 출력
        self.result_text = QTextEdit()
        self.result_text.setPlaceholderText("검색 결과가 여기에 표시됩니다...")
        layout.addWidget(self.result_text)

    def add_tag_row(self):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        # Group ID
        group_edit = QLineEdit()
        group_edit.setPlaceholderText("0010")
        group_edit.setMaxLength(4)
        group_edit.setFixedWidth(60)  # 4자 고정

        # Element ID
        elem_edit = QLineEdit()
        elem_edit.setPlaceholderText("0010")
        elem_edit.setMaxLength(4)
        elem_edit.setFixedWidth(60)

        # Value 입력 박스 (가변 너비)
        value_edit = QLineEdit()
        value_edit.setPlaceholderText("값 (비워두면 Tag 존재 여부만 체크)")

        # 16진수 제한 (Group, Element)
        hex_validator = QRegExpValidator(QRegExp("[0-9A-Fa-f]*"))
        group_edit.setValidator(hex_validator)
        elem_edit.setValidator(hex_validator)

        # + / - 버튼 (반으로 줄임)
        plus_btn = QPushButton("+")
        plus_btn.setFixedWidth(30)
        plus_btn.clicked.connect(self.add_tag_row)
        minus_btn = QPushButton("-")
        minus_btn.setFixedWidth(30)
        minus_btn.clicked.connect(lambda: self.remove_tag_row(row_widget))

        # 레이아웃 배치
        row_layout.addWidget(QLabel("Group:"))
        row_layout.addWidget(group_edit)
        row_layout.addWidget(QLabel("Element:"))
        row_layout.addWidget(elem_edit)
        row_layout.addWidget(QLabel("Value:"))
        row_layout.addWidget(value_edit, 1)  # 남은 공간 모두 사용
        row_layout.addWidget(plus_btn)
        row_layout.addWidget(minus_btn)

        self.tag_layout.addWidget(row_widget)
        self.tag_widgets.append((row_widget, group_edit, elem_edit, value_edit))

    def remove_tag_row(self, widget):
        if len(self.tag_widgets) <= 1:
            QMessageBox.warning(self, "경고", "최소 1개의 태그 입력은 유지해야 합니다.")
            return
        for i, (w, _, _, _) in enumerate(self.tag_widgets):
            if w == widget:
                self.tag_layout.removeWidget(w)
                w.deleteLater()
                self.tag_widgets.pop(i)
                break

    def index_dicom_files(self):
        search_path = self.path_input.text().strip()

        # 1️⃣ 태그 유효성 검사 (가장 먼저)
        if not self.has_valid_tag_condition():
            QMessageBox.warning(
                self,
                "경고",
                "태그 ID를 입력한 후 검색을 실행하세요."
            )
            return

        # 2️⃣ 검색 경로 유효성 검사
        if not search_path or not os.path.exists(search_path):
            QMessageBox.warning(self, "경고", "유효한 검색 경로를 입력하세요.")
            return

        # 이전 워커 종료
        if self.indexing_worker and self.indexing_worker.isRunning():
            self.indexing_worker.quit()
            self.indexing_worker.wait()

        # 버튼 비활성화 및 indeterminate 진행 바를 즉시 표시
        self.index_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)  # indeterminate 모드
        self.progress_bar.setVisible(True)
        self.result_text.setText("Scanning for DICOM files...")

        # 백그라운드 워커 시작 (파일 탐색도 워커 안에서 수행)
        self.indexing_worker = IndexingWorker(search_path, self.subdir_cb.isChecked())
        self.indexing_worker.progress.connect(self._on_indexing_progress)
        self.indexing_worker.finished.connect(self._on_indexing_finished)
        self.indexing_worker.error.connect(self._on_indexing_error)
        self.indexing_worker.start()

    def _on_indexing_progress(self, current: int, total: int, message: str) -> None:
        """
        인덱싱 진행상황 업데이트.

        Args:
            current: 처리 완료된 파일 수
            total: 전체 파일 수 (0이면 탐색 중 — indeterminate 유지)
            message: 표시할 상태 텍스트
        """
        if total == 0:
            # 탐색 단계: indeterminate 유지 (setRange는 이미 (0,0)으로 설정됨)
            pass
        else:
            # 인덱싱 단계: determinate 모드로 전환하고 진행률 표시
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.result_text.setText(message)

    def _on_indexing_finished(self, result: dict) -> None:
        """
        인덱싱 완료 처리.

        Args:
            result: 'dicom_index', 'dicom_tag_values', 'total_files' 키를 가진 딕셔너리
        """
        # UI 상태 복구 (진행 바를 determinate 초기 상태로 되돌림)
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

        # 태그가 있을 때만 자동 검색
        if self.has_valid_tag_condition():
            self.search_and_display()

    def _on_indexing_error(self, error_msg: str) -> None:
        """인덱싱 에러 처리"""
        self.index_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "에러", error_msg)

    def has_valid_tag_condition(self):
        for _, group_edit, elem_edit, _ in self.tag_widgets:
            g = group_edit.text().strip()
            e = elem_edit.text().strip()
            if g and e and len(g) == 4 and len(e) == 4:
                return True
        return False

    def value_matches(self, stored_value, query_value):
        """
        SQL LIKE 스타일 비교
        *1234  → endswith
        1234*  → startswith
        *1234* → contains
        1234   → exact match
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

    def search_and_display(self):
        conditions = []
        for _, group_edit, elem_edit, value_edit in self.tag_widgets:
            g = group_edit.text().strip().upper()
            e = elem_edit.text().strip().upper()
            v = value_edit.text().strip()
            if g and e and len(g) == 4 and len(e) == 4:
                conditions.append((f"{g},{e}", v))

        if not conditions:
            QMessageBox.warning(self, "경고", "유효한 태그를 입력하세요 (4자리 16진수).")
            return

        is_and = self.and_radio.isChecked()

        # AND 검색 최적화: 가장 적은 파일을 가진 조건부터 처리
        if is_and:
            conditions.sort(key=lambda x: len(self.dicom_index.get(x[0], [])))

        if is_and:
            results = set(self.all_dicom_files)
        else:
            results = set()

        for tag, value in conditions:
            matching_files = set()
            for file_path in self.dicom_index.get(tag, []):
                file_tags = self.dicom_tag_values.get(file_path, {})
                stored_value = file_tags.get(tag, "")
                if value:
                    if self.value_matches(stored_value, value):
                        matching_files.add(file_path)
                else:
                    matching_files.add(file_path)

            if is_and:
                results = results.intersection(matching_files)
            else:
                results = results.union(matching_files)

            # AND 검색에서 결과가 없으면 즉시 종료
            if is_and and not results:
                break

        # 결과 출력
        txt = "=== DICOM 태그 정밀 검색 결과 ===\n"
        txt += f"조건: {', '.join([f'{t}={v}' if v else t for t, v in conditions])}\n"
        txt += f"로직: {'AND' if is_and else 'OR'}\n"
        txt += f"총 {len(results)}개 파일\n\n"
        for f in sorted(results):
            txt += f"{f}\n"

        self.result_text.setText(txt)

        # 자동 저장
        if results:
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
    window.setWindowIcon(QIcon(icon_path))  # 메인 윈도우에도 아이콘 설정
    window.show()
    sys.exit(app.exec_())
