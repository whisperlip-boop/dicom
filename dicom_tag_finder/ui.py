"""DICOM 태그 검색 메인 창(PyQt5)."""
import logging
import os
from collections import defaultdict

from PyQt5.QtWidgets import (QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
                             QLabel, QLineEdit, QPushButton, QCheckBox,
                             QTextEdit, QGroupBox, QMessageBox, QProgressBar)
from PyQt5.QtCore import QRegExp
from PyQt5.QtGui import QCloseEvent, QRegExpValidator

from app_paths import APP_NAME, RESULTS_FILE_NAME, get_output_dirs
from indexer import IndexingWorker, make_index_key
from search import TagCondition, describe_groups, make_tag_key, search_files, split_groups


logger = logging.getLogger(APP_NAME).getChild(__name__)

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
        self.dicom_tag_values: dict[str, dict[str, list[str]]] = {}
        self.all_dicom_files: list[str] = []
        # 각 항목: (row_widget, group_edit, elem_edit, value_edit, toggle_btn)
        # 첫 번째 행의 toggle_btn은 None
        self.tag_widgets: list[tuple] = []
        self.indexing_worker: IndexingWorker | None = None
        # 현재 인덱스의 (정규화된 절대경로, recursive). 인덱스가 없으면 None
        self._index_key: tuple[str, bool] | None = None

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
        self.reindex_btn = QPushButton("Re-index")
        self.reindex_btn.setToolTip("Rebuild the index even if the path and options are unchanged")
        self.reindex_btn.clicked.connect(self.reindex_dicom_files)
        self.subdir_cb = QCheckBox("하위 폴더 포함")
        self.subdir_cb.setChecked(True)
        path_layout.addWidget(QLabel("경로:"))
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.index_btn)
        path_layout.addWidget(self.reindex_btn)
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

        # 인덱스 상태 1줄
        self.status_label = QLabel("Index: none")
        layout.addWidget(self.status_label)

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

        # 인덱스는 SQ 내부 요소도 같은 태그 키로 합쳐 저장한다(indexer._collect_elements)
        tag_tooltip = "Matches this tag at any depth: includes values inside sequences (SQ)."
        group_edit.setToolTip(tag_tooltip)
        elem_edit.setToolTip(tag_tooltip)

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
        """'검색 시작' 버튼 처리. 경로·옵션이 직전 인덱스와 같으면 재인덱싱 없이 검색한다."""
        self._start_search(force_reindex=False)

    def reindex_dicom_files(self) -> None:
        """'Re-index' 버튼 처리. 경로·옵션과 무관하게 인덱스를 다시 만든다."""
        self._start_search(force_reindex=True)

    def _start_search(self, force_reindex: bool) -> None:
        """태그 유효성 및 경로를 검증한 후 검색 또는 백그라운드 인덱싱을 시작한다.

        태그 미입력 또는 경로 오류 시 경고를 표시하고 종료한다.
        (정규화된 절대경로, recursive)가 직전 인덱스와 같고 강제 재인덱싱이 아니면
        기존 인덱스로 바로 검색한다. 이전 워커가 실행 중이면 취소 후 종료를 기다린다.

        Args:
            force_reindex: True이면 기존 인덱스를 무시하고 재인덱싱한다.
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

        if not os.path.isdir(search_path):
            QMessageBox.warning(self, "Invalid Path", "Path is not a folder. Enter a folder that contains DICOM files.")
            return

        recursive = self.subdir_cb.isChecked()
        index_key = make_index_key(search_path, recursive)

        if not force_reindex and index_key == self._index_key:
            logger.info("Reusing existing index (%d files)", len(self.all_dicom_files))
            self.status_label.setText(f"Index: {len(self.all_dicom_files)} files (reused)")
            self.search_and_display()
            return

        self._stop_indexing_worker()

        self._set_indexing_ui(True)
        self.result_text.setText("Scanning for DICOM files...")
        self.status_label.setText("Index: building...")
        logger.info("Indexing started (recursive=%s, forced=%s)", recursive, force_reindex)

        self.indexing_worker = IndexingWorker(search_path, recursive)
        self.indexing_worker.progress.connect(self._on_indexing_progress)
        self.indexing_worker.indexed.connect(self._on_indexing_finished)
        self.indexing_worker.error.connect(self._on_indexing_error)
        self.indexing_worker.start()

    def _set_indexing_ui(self, running: bool) -> None:
        """인덱싱 중 여부에 맞춰 버튼·진행 바 상태를 바꾼다."""
        self.index_btn.setEnabled(not running)
        self.reindex_btn.setEnabled(not running)
        if running:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(running)

    def _stop_indexing_worker(self) -> None:
        """이전 인덱싱 워커의 신호 연결을 끊고, 실행 중이면 취소 후 스레드 종료까지 기다린다.

        이미 끝난 워커라도 큐에 남은 지연 신호가 새 인덱스를 덮어쓰지 않도록 항상 연결을 끊는다.
        """
        worker = self.indexing_worker
        if worker is None:
            return
        for signal in (worker.progress, worker.indexed, worker.error):
            try:
                signal.disconnect()
            except TypeError:
                pass
        if not worker.isRunning():
            return
        logger.info("Cancelling running indexing worker")
        worker.cancel()
        worker.wait()
        logger.info("Indexing worker stopped")

    def closeEvent(self, event: QCloseEvent) -> None:
        """창 닫기 시 인덱싱 워커를 취소·대기한 뒤 닫는다.

        Args:
            event: Qt 닫기 이벤트.
        """
        self._stop_indexing_worker()
        super().closeEvent(event)

    def _on_indexing_progress(self, current: int, total: int, message: str) -> None:
        """인덱싱 진행상황을 업데이트한다.

        Args:
            current: 처리 완료된 파일 수.
            total: 전체 파일 수 (0이면 탐색 중 — indeterminate 유지).
            message: 표시할 상태 텍스트.
        """
        if not self._is_current_worker_signal():
            return
        if total > 0:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.result_text.setText(message)

    def _on_indexing_finished(self, result: dict) -> None:
        """인덱싱 완료 후 결과를 저장하고 자동 검색을 실행한다.

        Args:
            result: 'dicom_index', 'dicom_tag_values', 'total_files',
                'failed_count', 'failed_by_class' 키를 가진 딕셔너리.
        """
        if not self._is_current_worker_signal():
            return
        self._set_indexing_ui(False)

        if result['total_files'] == 0:
            # 파일 추가 후 재검색 시 다시 스캔하도록 인덱스를 비운다
            self._clear_index()
            self.status_label.setText("Index: no DICOM files found")
            self.result_text.clear()
            QMessageBox.information(self, "No Files Found", "No DICOM files were found in the specified path.")
            return

        self.dicom_index = defaultdict(list, result['dicom_index'])
        self.dicom_tag_values = result['dicom_tag_values']
        self.all_dicom_files = list(result['dicom_tag_values'].keys())
        self._index_key = self.indexing_worker.index_key

        failed_count: int = result['failed_count']
        summary = f"{len(self.all_dicom_files)} files indexed, {failed_count} failed to read"
        self.status_label.setText(f"Index: {summary}")
        if failed_count:
            failure_lines = "\n".join(
                f"  {name}: {count}" for name, count in sorted(result['failed_by_class'].items())
            )
            QMessageBox.warning(
                self, "Indexing Complete",
                f"{summary}.\n\nFailure types:\n{failure_lines}"
            )
        else:
            QMessageBox.information(self, "Indexing Complete", f"{summary}.")

        if self.has_valid_tag_condition():
            self.search_and_display()

    def _on_indexing_error(self, error_msg: str) -> None:
        """인덱싱 에러를 처리한다.

        Args:
            error_msg: 표시할 에러 메시지.
        """
        if not self._is_current_worker_signal():
            return
        self._set_indexing_ui(False)
        self._clear_index()
        self.status_label.setText("Index: failed")
        QMessageBox.critical(self, "에러", error_msg)

    def _is_current_worker_signal(self) -> bool:
        """신호를 보낸 워커가 현재 워커인지 확인한다(교체된 구 워커의 지연 신호 무시용)."""
        return self.indexing_worker is not None and self.sender() is self.indexing_worker

    def _clear_index(self) -> None:
        """현재 인덱스와 재사용 키를 비운다."""
        self.dicom_index = defaultdict(list)
        self.dicom_tag_values = {}
        self.all_dicom_files = []
        self._index_key = None

    def has_valid_tag_condition(self) -> bool:
        """유효한 태그 조건(4자리 Group + Element)이 하나 이상 입력되었는지 확인한다.

        Returns:
            유효한 태그가 하나 이상 존재하면 True, 아니면 False.
        """
        return any(
            make_tag_key(group_edit.text(), elem_edit.text()) is not None
            for _, group_edit, elem_edit, _, _ in self.tag_widgets
        )

    def search_and_display(self) -> None:
        """현재 태그 조건으로 DICOM 파일을 검색하고 결과를 표시한다.

        And/Or 토글 상태를 해석하여 DNF(선언적 정규형) 구조로 검색한다.
        연속된 And 행은 하나의 AND 그룹을 형성하고, Or 토글은 새 그룹을 시작한다.
        최종 결과는 모든 AND 그룹의 합집합(OR)이다.

        예) Row1, Row2[And], Row3[And], Row4[Or], Row5[And]
            → (Row1 AND Row2 AND Row3) OR (Row4 AND Row5)
        """
        conditions: list[TagCondition] = []
        for _, group_edit, elem_edit, value_edit, toggle_btn in self.tag_widgets:
            tag = make_tag_key(group_edit.text(), elem_edit.text())
            if tag is not None:
                is_or = (toggle_btn is not None and toggle_btn.isChecked())
                conditions.append(TagCondition(tag, value_edit.text().strip(), is_or))

        if not conditions:
            QMessageBox.warning(self, "경고", "유효한 태그를 입력하세요 (4자리 16진수).")
            return

        groups = split_groups(conditions)
        final_results = search_files(groups, self.dicom_index, self.dicom_tag_values, self.all_dicom_files)
        logic_str = describe_groups(groups)

        header = [
            "=== DICOM 태그 정밀 검색 결과 ===",
            f"조건: {logic_str}",
            f"총 {len(final_results)}개 파일",
        ]

        if not final_results:
            self.result_text.setText("\n".join(header) + "\n")
            QMessageBox.information(self, "결과 없음", "조건에 맞는 파일이 없습니다.")
            return

        body = "".join(f"{file_path}\n" for file_path in sorted(final_results))
        saved = self._save_results(header, body)

        if saved is not None:
            save_path, report = saved
            self.result_text.setText(report)
            QMessageBox.information(self, "Saved", f"Results saved to:\n{save_path}")
        else:
            self.result_text.setText("\n".join(header + ["Saved to: (save failed - see log)"]) + "\n\n" + body)
            tried = "\n".join(os.path.join(d, RESULTS_FILE_NAME) for d in get_output_dirs())
            QMessageBox.warning(
                self, "Save Failed",
                f"Could not save the results to:\n{tried}\n\nThe results are still shown on screen."
            )

    @staticmethod
    def _save_results(header: list[str], body: str) -> tuple[str, str] | None:
        """검색 결과를 프로그램 폴더에 저장하고, 쓸 수 없으면 LOCALAPPDATA 폴더로 폴백한다.

        머리글의 'Saved to:' 줄에는 실제로 저장된 경로를 넣는다.

        Args:
            header: 결과 머리글 줄 목록('Saved to:' 제외).
            body: 결과 파일 경로 목록 텍스트.

        Returns:
            (저장 경로, 저장한 전체 텍스트). 모든 후보 폴더에서 OSError면 None(상세는 로그에만 기록).
        """
        out_dirs = get_output_dirs()
        for i, out_dir in enumerate(out_dirs):
            save_path = os.path.join(out_dir, RESULTS_FILE_NAME)
            report = "\n".join(header + [f"Saved to: {save_path}"]) + "\n\n" + body
            try:
                os.makedirs(out_dir, exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(report)
            except OSError as e:
                # 저장 경로는 앱 고정 경로라 환자정보가 없지만, 결과 본문은 기록하지 않음
                if i + 1 < len(out_dirs):
                    logger.warning(
                        "Cannot write search results to %s: %s (%s); falling back to %s",
                        save_path, type(e).__name__, e.strerror, out_dirs[i + 1]
                    )
                else:
                    logger.error(
                        "Failed to save search results to %s: %s (%s)",
                        save_path, type(e).__name__, e.strerror
                    )
                continue
            logger.info("Search results saved to %s (%d chars)", save_path, len(report))
            return save_path, report
        return None
