"""Gateway DCM Creator.

폴더 안 DICOM의 날짜·시간·환자정보·Image Comments·Modality 등을 규칙에 따라 랜덤 값으로 바꾸는
게이트웨이 테스트용 PySide6 GUI 애플리케이션.
"""
import ctypes
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QDate, QSize, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDateEdit, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

import rules
from paths import get_resource_path, get_settings_path
from processor import Processor, Summary, safe_traceback, same_dir

ICON_FILE = "creator.ico"
LOG_FILE = "gateway_dcm_creator.log"
APP_USER_MODEL_ID = "vuno.gateway_dcm_creator"
logger = logging.getLogger(__name__)

TAG_ROWS: list[tuple[str, str]] = [
    ("study_date", "(0008,0020) Study Date"),
    ("series_date", "(0008,0021) Series Date"),
    ("study_time", "(0008,0030) Study Time"),
    ("series_time", "(0008,0031) Series Time"),
    ("accession", "(0008,0050) Accession Number"),
    ("image_comments", "(0020,4000) Image Comments"),
    ("patient_name", "(0010,0010) Patient's Name"),
    ("patient_id", "(0010,0020) Patient ID"),
    ("birth_date", "(0010,0030) Patient's Birth Date"),
    ("sex", "(0010,0040) Patient's Sex"),
    ("age", "(0010,1010) Patient's Age"),
    ("modality", "(0008,0060) Modality"),
    ("sop_class", "(0008,0016) SOP Class UID\n+ (0002,0002) Media Storage"),
]

# 위→아래 gradient + 아래쪽 진한 테두리로 입체감, pressed는 gradient 반전으로 눌린 느낌
APP_STYLE = """
QPushButton {
    padding: 4px 12px; border-radius: 4px; color: #202020;
    border: 1px solid #9a9a9a; border-bottom-color: #6f6f6f;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #d9d9d9);
}
QPushButton:hover {
    border-color: #5b8ad6;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e1eaf7);
}
QPushButton:pressed {
    padding-top: 5px; padding-bottom: 3px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #cdcdcd, stop:1 #f2f2f2);
}
QPushButton:disabled { color: #a0a0a0; border-color: #c8c8c8; background: #ececec; }

QPushButton#primary {
    color: white; font-weight: bold; border: 1px solid #1f4f9c; border-bottom-color: #163a73;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5c9ded, stop:1 #2d6cc7);
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #72adf2, stop:1 #3a7ad4);
}
QPushButton#primary:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2a62b3, stop:1 #5c9ded);
}
QPushButton#primary:disabled { color: #e8e8e8; border-color: #9fb4d6; background: #a9bfe0; }

QPushButton[toggle="true"] { min-width: 52px; padding: 3px 8px; font-weight: bold; color: #555555; }
QPushButton[toggle="true"]:checked {
    color: white; border: 1px solid #1b5e20; border-bottom-color: #0f3d14;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5cb860, stop:1 #2e7d32);
}
QPushButton[toggle="true"]:checked:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #6cc870, stop:1 #388e3c);
}
QPushButton[toggle="true"]:checked:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2e7d32, stop:1 #5cb860);
}
QPushButton[toggle="true"]:checked:disabled { color: #eeeeee; border-color: #8fb592; background: #a5c8a7; }

QFrame#tagRow { background: #ffffff; border: 1px solid #d4d4d4; border-radius: 4px; }
QFrame#tagRow[alt="true"] { background: #eef3fa; }
QLabel#tagLabel { font-weight: bold; }
"""
HINT_STYLE = "color: #666666;"


class ToggleButton(QPushButton):
    """ON/OFF 표시 toggle 버튼. on_text/off_text로 문구를 바꿀 수 있다."""

    def __init__(self, checked: bool, on_text: str = "ON", off_text: str = "OFF") -> None:
        super().__init__()
        self._on_text, self._off_text = on_text, off_text
        self.setCheckable(True)
        self.setProperty("toggle", True)  # APP_STYLE의 QPushButton[toggle="true"] 선택자용
        self.toggled.connect(self._refresh)
        self.setChecked(checked)
        self._refresh(checked)

    def _refresh(self, checked: bool) -> None:
        self.setText(self._on_text if checked else self._off_text)


def _date_edit(value: str) -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    edit.setMinimumDate(QDate(1900, 1, 1))
    date = QDate.fromString(value, "yyyyMMdd")
    edit.setDate(date if date.isValid() else QDate.currentDate())
    return edit


def _time_edit(value: str) -> QLineEdit:
    edit = QLineEdit(value)
    edit.setInputMask("99:99;_")
    edit.setFixedWidth(60)
    edit.setToolTip("hh:mm (00:00 ~ 24:00)")
    return edit


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(HINT_STYLE)
    return label


class FitScrollArea(QScrollArea):
    """내용 전체 높이를 선호 크기로 알려, 공간이 충분하면 스크롤 없이 보이게 하는 QScrollArea."""

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        content = self.widget()
        if content is not None:
            hint.setHeight(content.sizeHint().height() + 2 * self.frameWidth())
        return hint


class Worker(QThread):
    log = Signal(str)
    progress = Signal(int, int)

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self.settings = settings
        self._cancel = False
        self.summary = Summary()

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        processor = Processor(self.settings, self.log.emit, self.progress.emit, lambda: self._cancel)
        try:
            processor.run()
        except Exception as e:  # 예상 못 한 오류도 GUI가 멈추지 않도록
            self.log.emit(f"[ERROR] 처리 중단 — {type(e).__name__}")
            logger.error("처리 중단 — %s\n%s", type(e).__name__, safe_traceback(e))
        self.summary = processor.summary  # finished 시그널(스레드 종료 후)에서 읽는다


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Gateway DCM Creator")
        self.settings_path = get_settings_path()
        self.s = self._load_settings()
        self.worker: Worker | None = None
        self.toggles: dict[str, ToggleButton] = {}
        self.option_widgets: dict[str, QWidget] = {}

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._build_paths())
        # 태그 영역은 내용 높이만큼 먼저 확보하고 남는 높이를 로그 영역이 가진다.
        # 창이 작아지면 둘 다 줄어들고 태그 영역은 스크롤된다.
        layout.addWidget(self._build_tags_area())
        layout.addLayout(self._build_actions())
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(20000)
        self.log_view.setMinimumHeight(60)
        layout.addWidget(self.log_view, stretch=1)
        self.setCentralWidget(central)
        # 폭은 가장 넓은 태그 행이 잘리지 않는 최소 폭 + 여유 10px (글꼴·DPI가 달라도 같은 여유 유지)
        self.resize(self.minimumSizeHint().width() + 10, 980)

        for key, toggle in self.toggles.items():
            toggle.toggled.connect(lambda _=False, k=key: self._refresh_row(k))
            self._refresh_row(key)

    # ------------------------------------------------------------ 설정
    def _load_settings(self) -> dict:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                return rules.merge_settings(json.load(f))
        except FileNotFoundError:
            return rules.default_settings()
        except (OSError, ValueError) as e:
            logger.warning("설정 파일을 읽지 못해 기본값 사용: %s", type(e).__name__)
            return rules.default_settings()

    def _save_settings(self) -> None:
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self._collect(), f, ensure_ascii=False, indent=1)
        except OSError as e:
            self.log_view.appendPlainText(f"설정 저장 실패: {e.strerror}")
            logger.error("설정 저장 실패: %s", e.strerror)

    def _collect(self) -> dict:
        """화면 값을 settings dict로 모은다."""
        def date_range(start: QDateEdit, end: QDateEdit) -> dict:
            return {"start": start.date().toString("yyyyMMdd"), "end": end.date().toString("yyyyMMdd")}

        return {
            "input_dir": self.input_edit.text().strip(),
            "output_dir": self.output_edit.text().strip(),
            "include_subfolders": self.subfolder_check.isChecked(),
            "regenerate_uids": self.regen_toggle.isChecked(),
            "convert_pixels": self.convert_toggle.isChecked(),
            "enabled": {key: t.isChecked() for key, t in self.toggles.items()},
            "study_date": date_range(self.study_date_start, self.study_date_end),
            "series_date": {"same": self.series_date_same.isChecked(),
                            **date_range(self.series_date_start, self.series_date_end)},
            "study_time": {"start": self.study_time_start.text(), "end": self.study_time_end.text()},
            "series_time": {"same": self.series_time_same.isChecked(),
                            "start": self.series_time_start.text(), "end": self.series_time_end.text()},
            "drid": self.drid_edit.text(),
            "preg": {p: c.isChecked() for p, c in self.preg_checks.items()},
            "birth_date": date_range(self.birth_start, self.birth_end),
            "sex": {x: c.isChecked() for x, c in self.sex_checks.items()},
            "modality": {m: c.isChecked() for m, c in self.modality_checks.items()},
        }

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "처리 중", "처리가 끝난 뒤(또는 중지한 뒤) 닫아 주세요.")
            event.ignore()
            return
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------ 화면 구성
    def _build_paths(self) -> QGroupBox:
        box = self.paths_box = QGroupBox("경로 / 옵션")
        grid = QGridLayout(box)
        s = self.s

        self.input_edit = QLineEdit(s["input_dir"])
        in_btn = QPushButton("열기")
        in_btn.clicked.connect(lambda: self._pick_dir(self.input_edit))
        self.subfolder_check = QCheckBox("하위 폴더 포함")
        self.subfolder_check.setChecked(s["include_subfolders"])
        grid.addWidget(QLabel("입력 폴더"), 0, 0)
        grid.addWidget(self.input_edit, 0, 1)
        grid.addWidget(in_btn, 0, 2)
        grid.addWidget(self.subfolder_check, 0, 3)

        self.output_edit = QLineEdit(s["output_dir"])
        out_btn = QPushButton("열기")
        out_btn.clicked.connect(lambda: self._pick_dir(self.output_edit))
        grid.addWidget(QLabel("출력 폴더"), 1, 0)
        grid.addWidget(self.output_edit, 1, 1)
        grid.addWidget(out_btn, 1, 2)
        grid.addWidget(_hint("입력 폴더와 같으면 원본을 덮어씁니다. 하위 폴더 구조는 유지합니다."), 2, 1, 1, 3)

        self.regen_toggle = ToggleButton(s["regenerate_uids"])
        self.convert_toggle = ToggleButton(s["convert_pixels"])
        opt = QHBoxLayout()
        opt.addWidget(self.regen_toggle)
        opt.addWidget(QLabel("Instance UID 새로 생성 (Study/Series/SOP)"))
        opt.addSpacing(24)
        opt.addWidget(self.convert_toggle)
        opt.addWidget(QLabel("영상 형식 불일치 시 자동 변환"))
        self.convert_toggle.setToolTip(
            "OFF: 선택한 Modality 규격에 맞지 않는 영상(예: 컬러 → CR/DX)은 건너뜁니다.\n"
            "ON: 흑백/8bit 등으로 영상을 변환합니다. 압축 원본은 비압축으로 저장됩니다.")
        opt.addStretch()
        grid.addLayout(opt, 3, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        return box

    def _pick_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "폴더 선택", edit.text() or os.path.expanduser("~"))
        if path:
            edit.setText(os.path.normpath(path))

    def _build_tags_area(self) -> FitScrollArea:
        box = self.tags_box = QGroupBox("태그 (ON인 태그만 변경, 태그가 없으면 생성)")
        rows = QVBoxLayout(box)
        rows.setSpacing(4)
        builders = {
            "study_date": self._opt_study_date, "series_date": self._opt_series_date,
            "study_time": self._opt_study_time, "series_time": self._opt_series_time,
            "accession": lambda: _hint("대문자+숫자 또는 숫자만, 6~16자 랜덤 (실행 내 중복 없음)"),
            "image_comments": self._opt_image_comments,
            "patient_name": lambda: _hint("영문 랜덤 이름 LAST^FIRST (Sex에 맞춰 남/여 이름)"),
            "patient_id": lambda: _hint("랜덤 8자리 숫자 (실행 내 중복 없음)"),
            "birth_date": self._opt_birth_date, "sex": self._opt_sex,
            "age": lambda: _hint("Birth Date와 Study Date로 만 나이 계산 → 000Y"),
            "modality": self._opt_modality,
            "sop_class": lambda: _hint("Modality에 맞춰 자동 (DX는 For Presentation)\n"
                                       "규격(IOD) 필수 태그 자동 보정, 규격에 없는 표준 태그 제거"),
        }
        # 행을 별도 frame으로 나누므로 label 열 정렬은 가장 긴 label 폭으로 맞춘다
        bold = box.font()
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        label_width = max(metrics.horizontalAdvance(line) for _, text in TAG_ROWS for line in text.split("\n")) + 16
        top = Qt.AlignmentFlag.AlignTop
        for row, (key, text) in enumerate(TAG_ROWS):
            toggle = ToggleButton(self.s["enabled"][key])
            widget = builders[key]()
            self.toggles[key] = toggle
            self.option_widgets[key] = widget
            label = QLabel(text)
            label.setObjectName("tagLabel")
            label.setFixedWidth(label_width)
            label.setMinimumHeight(toggle.sizeHint().height())  # 1줄 label이 toggle·입력칸과 같은 높이에 오도록
            frame = QFrame()
            frame.setObjectName("tagRow")
            frame.setProperty("alt", row % 2 == 1)
            lay = QHBoxLayout(frame)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(10)
            lay.addWidget(toggle, 0, top)
            lay.addWidget(label, 0, top)
            lay.addWidget(widget, 1)
            rows.addWidget(frame)
        rows.addStretch()
        area = FitScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 가로 스크롤이 없으므로 창을 줄여도 가장 넓은 행(Series Date)이 잘리지 않게 최소 폭을 둔다
        area.setMinimumWidth(box.minimumSizeHint().width() + area.verticalScrollBar().sizeHint().width()
                             + 2 * area.frameWidth())
        area.setWidget(box)
        return area

    @staticmethod
    def _hbox(*widgets: QWidget | str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for item in widgets:
            if isinstance(item, str):
                lay.addWidget(QLabel(item))
            else:
                lay.addWidget(item)
        lay.addStretch()
        return w

    def _opt_study_date(self) -> QWidget:
        self.study_date_start = _date_edit(self.s["study_date"]["start"])
        self.study_date_end = _date_edit(self.s["study_date"]["end"])
        return self._hbox("start", self.study_date_start, "~ end", self.study_date_end, _hint("(end 포함)"))

    def _opt_series_date(self) -> QWidget:
        sd = self.s["series_date"]
        self.series_date_same = ToggleButton(sd["same"], "Same Study Date ✔", "Same Study Date")
        self.series_date_start = _date_edit(sd["start"])
        self.series_date_end = _date_edit(sd["end"])
        self.series_date_same.toggled.connect(lambda _: self._refresh_row("series_date"))
        return self._hbox(self.series_date_same, "start", self.series_date_start, "~ end", self.series_date_end)

    def _opt_study_time(self) -> QWidget:
        self.study_time_start = _time_edit(self.s["study_time"]["start"])
        self.study_time_end = _time_edit(self.s["study_time"]["end"])
        hint = _hint("(end - 1초까지)")
        hint.setToolTip("예: 10:00 ~ 12:00 → 10:00:00 ~ 11:59:59")
        return self._hbox("start", self.study_time_start, "~ end", self.study_time_end, hint)

    def _opt_series_time(self) -> QWidget:
        st = self.s["series_time"]
        self.series_time_same = ToggleButton(st["same"], "Same Study Time ✔", "Same Study Time")
        self.series_time_start = _time_edit(st["start"])
        self.series_time_end = _time_edit(st["end"])
        self.series_time_same.toggled.connect(lambda _: self._refresh_row("series_time"))
        return self._hbox(self.series_time_same, "start", self.series_time_start, "~ end", self.series_time_end)

    def _opt_image_comments(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.drid_edit = QLineEdit(self.s["drid"])
        self.drid_edit.setPlaceholderText("예: DR001, DR002 | DR003  (쉼표 또는 | 로 구분, 고른 비율로 랜덤)")
        self.preg_checks = {p: QCheckBox(p) for p in rules.PREG_OPTIONS}
        for p, check in self.preg_checks.items():
            check.setChecked(self.s["preg"][p])
        lay.addWidget(self._hbox("DRID", self.drid_edit))
        self.drid_edit.setMinimumWidth(360)
        lay.addWidget(self._hbox("PREG", *self.preg_checks.values(), _hint("(체크한 것 중 랜덤, Null=빈 값)")))
        fmt = _hint("형식: CAUERRCT/1;DRID={DRID};KTAS=3;PREG={PREG};VSID={Study Date}{Study Time};")
        fmt.setWordWrap(True)
        lay.addWidget(fmt)
        return w

    def _opt_birth_date(self) -> QWidget:
        self.birth_start = _date_edit(self.s["birth_date"]["start"])
        self.birth_end = _date_edit(self.s["birth_date"]["end"])
        return self._hbox("start", self.birth_start, "~ end", self.birth_end, _hint("(end 포함)"))

    def _opt_sex(self) -> QWidget:
        self.sex_checks = {x: QCheckBox(x) for x in rules.SEXES}
        for x, check in self.sex_checks.items():
            check.setChecked(self.s["sex"][x])
        return self._hbox(*self.sex_checks.values(), _hint("(체크한 것 중 랜덤)"))

    def _opt_modality(self) -> QWidget:
        self.modality_checks = {m: QCheckBox(m) for m in rules.MODALITIES}
        for m, check in self.modality_checks.items():
            check.setChecked(self.s["modality"][m])
        hint = _hint("(체크한 것 중 랜덤)")
        hint.setToolTip("SOP Class UID가 ON이고 자동 변환이 OFF면, 영상 형식에 맞는 Modality 중에서 고릅니다.\n"
                        "예: 컬러 영상 → CR/DX 제외, 16bit 영상 → XC 제외")
        return self._hbox(*self.modality_checks.values(), hint)

    def _refresh_row(self, key: str) -> None:
        on = self.toggles[key].isChecked()
        self.option_widgets[key].setEnabled(on)
        if key == "series_date":
            for w in (self.series_date_start, self.series_date_end):
                w.setEnabled(on and not self.series_date_same.isChecked())
        elif key == "series_time":
            for w in (self.series_time_start, self.series_time_end):
                w.setEnabled(on and not self.series_time_same.isChecked())

    def _build_actions(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        self.run_btn = QPushButton("실행")
        self.run_btn.setObjectName("primary")
        self.run_btn.setMinimumHeight(34)
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("중지")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m")
        lay.addWidget(self.run_btn)
        lay.addWidget(self.stop_btn)
        lay.addWidget(self.progress_bar, stretch=1)
        return lay

    # ------------------------------------------------------------ 실행
    def _run(self) -> None:
        settings = self._collect()
        errors = []
        input_dir, output_dir = settings["input_dir"], settings["output_dir"]
        if not input_dir or not os.path.isdir(input_dir):
            errors.append("입력 폴더가 없습니다.")
        if not output_dir:
            errors.append("출력 폴더를 지정하세요.")
        errors += rules.validate(settings)
        if errors:
            QMessageBox.warning(self, "입력 확인", "\n".join(errors))
            return

        input_dir, output_dir = os.path.abspath(input_dir), os.path.abspath(output_dir)
        settings["input_dir"], settings["output_dir"] = input_dir, output_dir
        if same_dir(input_dir, output_dir):
            answer = QMessageBox.question(self, "덮어쓰기 확인",
                                          "입력 폴더와 출력 폴더가 같아 원본 파일을 덮어씁니다.\n계속할까요?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "출력 폴더", f"출력 폴더를 만들 수 없습니다: {e.strerror}")
            return

        self.log_view.clear()
        self._save_settings()  # clear 뒤에 저장해야 실패 메시지가 지워지지 않는다
        logger.info("실행 시작: input=%s output=%s", input_dir, output_dir)
        self.progress_bar.setValue(0)
        self._set_running(True)
        self.worker = Worker(settings)
        self.worker.log.connect(self.log_view.appendPlainText)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.stop_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)

    def _on_done(self) -> None:
        summary = self.worker.summary
        self._set_running(False)
        text = (f"전체 {summary.total}개 — 성공 {summary.ok}, 건너뜀 {summary.skipped}, 실패 {summary.failed}"
                + (" (중지됨)" if summary.cancelled else ""))
        self.log_view.appendPlainText("\n" + text)
        QMessageBox.information(self, "완료", text)

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.paths_box.setEnabled(not running)
        self.tags_box.setEnabled(not running)


def _setup_logging() -> None:
    """settings.json 옆에 회전 로그 파일을 둔다. 만들 수 없으면 파일 기록 없이 진행한다."""
    log_path = os.path.join(os.path.dirname(get_settings_path()), LOG_FILE)
    try:
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> None:
    _setup_logging()
    # 소스 실행 시 작업표시줄이 python.exe 아이콘으로 묶이지 않도록 한다.
    # exe에서는 설정하지 않는다(설정 시 exe 내장 아이콘 대신 기본 아이콘이 표시됨, tag_checker 81c359e 참고)
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # 기본 windows11 스타일은 QSS gradient 버튼과 충돌해 Fusion으로 통일
    app.setStyleSheet(APP_STYLE)
    icon = QIcon(get_resource_path(ICON_FILE))
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
