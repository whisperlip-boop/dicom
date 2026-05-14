"""VUNO DICOM 결과 태그 검증 도구.

원본 DICOM을 분석하여 재생성한 결과 DICOM의 태그 값이 사내 규칙에 따라
올바르게 들어 있는지 확인하는 PyQt5 GUI 애플리케이션.
"""
import sys
import os
import csv
import json
import re
import datetime
import ctypes
from typing import Any

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtGui import QColor, QIcon, QDragEnterEvent, QDropEvent
from PyQt5.QtCore import Qt

import pydicom
from pydicom.tag import Tag, BaseTag
from pydicom.datadict import add_private_dict_entries


def get_resource_path(relative_path: str) -> str:
    """PyInstaller 번들 및 개발 환경 모두에서 리소스 절대 경로를 반환한다.

    Args:
        relative_path: 리소스 파일의 상대 경로.

    Returns:
        실행 환경에 맞게 계산된 절대 경로 문자열.
    """
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def extend_dicom_dictionary() -> None:
    """pydicom 사전에 VUNO 전용 Private Tag 항목을 등록한다.

    그룹 0x1001 범위의 Private Tag를 'VUNO Private Tags' 블록 오너로
    등록하여 pydicom이 해당 태그를 인식할 수 있도록 한다.
    """
    private_entries: dict[tuple[int, int], tuple[str, str, str, str]] = {
        (0x1001, 0x1002): ('CS', '1', 'Private Version', ''),
        (0x1001, 0x1004): ('CS', '1', 'Private Flag 0/1', ''),
        (0x1001, 0x1005): ('CS', '1', 'Private Flag 1', ''),
        (0x1001, 0x1008): ('UT', '1', 'Private Report', ''),
        (0x1001, 0x1009): ('CS', '1', 'Private Type', ''),
        (0x1001, 0x1011): ('UT', '1', 'Private Report/RADS', ''),
        (0x1001, 0x1015): ('UT', '1', 'Private JSON', ''),
    }
    tag_dict = {
        (group << 16) | element: entry
        for (group, element), entry in private_entries.items()
    }
    add_private_dict_entries('VUNO Private Tags', tag_dict)


def is_valid_date(value: str) -> bool:
    """DICOM 날짜 형식(YYYYMMDD) 유효성을 검사한다.

    Args:
        value: 검사할 날짜 문자열.

    Returns:
        YYYYMMDD 형식의 유효한 날짜이면 True, 그 외에는 False.
    """
    if not value or not isinstance(value, str):
        return False
    try:
        datetime.datetime.strptime(value, "%Y%m%d")
        return True
    except ValueError:
        return False


def is_valid_time(value: str) -> bool:
    """DICOM 시각 형식(HHMMSS 또는 HHMMSS.FFFFFF) 유효성을 검사한다.

    Args:
        value: 검사할 시각 문자열.

    Returns:
        HHMMSS 또는 HHMMSS.FFFFFF 형식의 유효한 시각이면 True, 그 외에는 False.
    """
    if not value or not isinstance(value, str):
        return False
    pattern = r"^[0-2][0-9][0-5][0-9][0-5][0-9](\.\d{1,6})?$"
    if not re.match(pattern, value):
        return False
    try:
        hours = int(value[:2])
        minutes = int(value[2:4])
        seconds = int(value[4:6])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
            return False
        if '.' in value:
            micro = int(value.split('.')[1])
            if not (0 <= micro <= 999999):
                return False
        return True
    except ValueError:
        return False


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


class TagStatusEvaluator:
    """DICOM 태그 값의 규칙 준수 여부를 평가하는 클래스메서드 모음.

    각 classmethod는 태그 값을 받아 (ok: bool, status: str) 튜플을 반환한다.
    SERIES_MAP과 MODEL_MAP은 코드값-표시명 변환에 사용된다.
    """

    SERIES_MAP: dict[str, str] = {
        "900010": "SC", "900090": "GSPS", "900070": "SC Report",
        "900071": "T1+T2 or BA환자용", "900072": "T1 or BA의사용",
        "900073": "T2", "900080": "Encap. PDF Report"
    }

    MODEL_MAP: dict[str, str] = {
        "VN-M-01": "BA", "VN-M-02": "CXR", "VN-M-03": "FAI", "VN-M-04": "LCT",
        "VN-M-06": "DB", "VN-M-07": "DB AD", "VN-M-30": "CXR Triage", "VN-M-31": "CXR Assist"
    }

    @classmethod
    def evaluate_series_uid(cls, value: str) -> tuple[bool, str]:
        """(0020,000E) Series Instance UID 규칙을 검증한다.

        형식: {VUNO_OID}.{VERSION}.{LEVEL}.{SERIES_NUMBER}.{UNIX_TIME}.{RAND_NO}
        VUNO_OID는 '1.2.410.200123' 고정, VERSION은 정수, LEVEL은 1/2/3,
        SERIES_NUMBER는 SERIES_MAP 정의값, UNIX_TIME은 양의 정수, RAND_NO는 1~32767.

        Args:
            value: 검사할 UID 문자열.

        Returns:
            (ok, status) 튜플. 예시: (True, "OK, series, GSPS").
        """
        if not value or not isinstance(value, str):
            return False, "Fail, study, Encap. PDF Report"
        parts = value.split(".")
        if len(parts) < 9 or parts[:4] != ["1", "2", "410", "200123"]:
            return False, "Fail, study, Encap. PDF Report"
        version, level, series_num, unix_time, rand_num = parts[4:9]
        version_valid = version.isdigit()
        level_valid = level in ["1", "2", "3"]
        series_num_valid = series_num in cls.SERIES_MAP
        unix_time_valid = unix_time.isdigit() and int(unix_time) > 0
        rand_num_valid = rand_num.isdigit() and 1 <= int(rand_num) <= 32767
        level_txt = {"1": "study", "2": "series", "3": "instance SOP"}.get(level, "study")
        series_txt = cls.SERIES_MAP.get(series_num, "Encap. PDF Report")
        ok = version_valid and level_valid and series_num_valid and unix_time_valid and rand_num_valid
        return ok, f"{'OK' if ok else 'Fail'}, {level_txt}, {series_txt}"

    @classmethod
    def evaluate_sop_instance_uid(cls, value: str) -> tuple[bool, str]:
        """(0008,0018) SOP Instance UID 규칙을 검증한다.

        형식: 1.2.410.200123.100.{LEVEL}.{SERIES_NUMBER}.{UNIX_TIME}.{RAND_NO}.{INSTANCE_NUMBER}
        앞 5개 요소 '1.2.410.200123.100'은 고정값.

        Args:
            value: 검사할 UID 문자열.

        Returns:
            (ok, status) 튜플. 예시: (True, "OK, series, GSPS").
        """
        if not value or not isinstance(value, str):
            return False, "Fail, study, Encap. PDF Report"
        parts = value.split(".")
        if len(parts) >= 10 and parts[:5] == ["1", "2", "410", "200123", "100"]:
            level, series_num, unix_time, rand_num, instance_num = parts[5:10]
            level_valid = level in ["1", "2", "3"]
            series_num_valid = series_num in cls.SERIES_MAP
            unix_time_valid = unix_time.isdigit() and int(unix_time) > 0
            rand_num_valid = rand_num.isdigit() and 1 <= int(rand_num) <= 32767
            instance_valid = instance_num.isdigit()
            level_txt = {"1": "study", "2": "series", "3": "instance SOP"}.get(level, "study")
            series_txt = cls.SERIES_MAP.get(series_num, "Encap. PDF Report")
            ok = level_valid and series_num_valid and unix_time_valid and rand_num_valid and instance_valid
            return ok, f"{'OK' if ok else 'Fail'}, {level_txt}, {series_txt}"
        return False, "Fail, study, Encap. PDF Report"

    @classmethod
    def evaluate_date(cls, value: str) -> tuple[bool, str]:
        """DICOM 날짜 태그 값(YYYYMMDD)을 검증한다.

        Args:
            value: 검사할 날짜 문자열.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        ok = is_valid_date(value)
        return ok, "OK" if ok else "Fail"

    @classmethod
    def evaluate_time(cls, value: str) -> tuple[bool, str]:
        """DICOM 시각 태그 값(HHMMSS 또는 HHMMSS.FFFFFF)을 검증한다.

        Args:
            value: 검사할 시각 문자열.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        ok = is_valid_time(value)
        return ok, "OK" if ok else "Fail"

    @classmethod
    def evaluate_series_number(cls, value: str) -> tuple[bool, str]:
        """(0020,0011) Series Number가 정의된 SERIES_MAP 코드인지 검증한다.

        Args:
            value: 검사할 Series Number 문자열.

        Returns:
            (ok, series_name) 튜플. 예시: (True, "GSPS") / (False, "Fail").
        """
        if not value:
            return False, "Fail"
        return value in cls.SERIES_MAP, cls.SERIES_MAP.get(value, "Fail")

    @classmethod
    def evaluate_conversion_type(cls, value: str, series_num: str) -> tuple[bool, str]:
        """(0008,0064) Conversion Type 규칙을 검증한다.

        SC(900010)와 SC Report(900070)는 "WSD"가 필수이고,
        그 외 타입은 값이 없는 것이 정상이다.

        Args:
            value: 검사할 Conversion Type 값. 태그 부재 시 빈 문자열.
            series_num: (0020,0011) Series Number 값.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if series_num in {"900010", "900070"}:
            ok = value == "WSD"
            return ok, "OK" if ok else 'Fail (No "WSD")'
        else:
            ok = value == ""
            return ok, "OK (not SC or SC Report)" if ok else "Fail"

    @classmethod
    def evaluate_fixed_value(cls, value: str, expected: str) -> tuple[bool, str]:
        """태그 값이 고정된 기대값과 일치하는지 검증한다.

        Args:
            value: 검사할 태그 값.
            expected: 기대하는 고정 값.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        ok = value == expected
        return ok, "OK" if ok else "Fail"

    @classmethod
    def evaluate_model_name(cls, value: str) -> tuple[bool, str]:
        """(0008,1090) Manufacturer's Model Name이 정의된 모델 코드인지 검증한다.

        Args:
            value: 검사할 모델명 문자열.

        Returns:
            (ok, display_name) 튜플. 정의되지 않은 값이면 원래 값을 그대로 반환.
        """
        if not value:
            return False, "Fail"
        return value in cls.MODEL_MAP, cls.MODEL_MAP.get(value, value)

    @classmethod
    def evaluate_version(cls, value: str) -> tuple[bool, str]:
        """태그 값에 x.x.x 형식의 버전 문자열이 포함되어 있는지 검증한다.

        Args:
            value: 검사할 문자열. 예시: "CXR Web 1.2.0".

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if not value or not isinstance(value, str):
            return False, "Fail"
        ok = bool(re.search(r"\d+\.\d+\.\d+", value))
        return ok, "OK" if ok else "Fail"

    @classmethod
    def evaluate_instance_number(cls, value: str | None) -> tuple[bool, str]:
        """(0020,0013) Instance Number 끝자리 패턴으로 DICOM 종류를 판별한다.

        끝자리 규칙: 00001=GSPS, 0001=Encap.PDF Report, 001=SC Report,
        01=SC (ROI), 02=SC (Heatmap), 03=SC (Merge).

        Args:
            value: 검사할 Instance Number 값.

        Returns:
            (ok, dicom_type) 튜플. 예시: (True, "GSPS") / (False, "Fail").
        """
        if value is None:
            return False, "Fail"
        suffix = str(value).strip()
        if suffix.endswith("00001"):
            return True, "GSPS"
        elif suffix.endswith("0001"):
            return True, "Encap.PDF Report"
        elif suffix.endswith("001"):
            return True, "SC Report"
        elif suffix.endswith("01"):
            return True, "SC (ROI)"
        elif suffix.endswith("02"):
            return True, "SC (Heatmap)"
        elif suffix.endswith("03"):
            return True, "SC (Merge)"
        return False, "Fail"

    @classmethod
    def evaluate_private_1009(cls, model: str, value: str) -> tuple[bool, str]:
        """(1001,1009) Private Type 값을 Manufacturer's Model Name과 교차 검증한다.

        VN-M-02(CXR) 모델은 값 '3', 그 외 모델은 값 '2'가 올바르다.

        Args:
            model: (0008,1090) Manufacturer's Model Name 값.
            value: (1001,1009) Private Type 값.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if not value or not model:
            return False, "Fail"
        model = str(model).strip().encode().decode(errors="ignore") if isinstance(model, bytes) else model.strip()
        value = str(value).strip()
        valid_models = {"VN-M-01", "VN-M-03", "VN-M-04", "VN-M-06", "VN-M-07", "VN-M-30", "VN-M-31"}
        if model == "VN-M-02" and value == "3":
            return True, "OK"
        elif model in valid_models and value == "2":
            return True, "OK"
        return False, "Fail"

    @classmethod
    def evaluate_private_json_prefix(cls, value: str, prefix_list: list[str]) -> tuple[bool, str]:
        """Private Tag 값이 지정된 JSON prefix 중 하나로 시작하는지 검증한다.

        시작 부분의 공백을 정규화한 뒤 prefix를 비교한다.

        Args:
            value: 검사할 문자열.
            prefix_list: 허용되는 JSON prefix 목록.
                예시: ['{"report":"', '{"case_lung_RADS":"'].

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if not value or not isinstance(value, str):
            return False, "Fail"
        s = value.strip()
        # '{ "key"' 형태를 '{"key"'로 정규화하여 공백 차이를 허용
        s = re.sub(r'^\{\s*"', '{"', s)
        ok = any(s.startswith(p) for p in prefix_list)
        return ok, "OK" if ok else "Fail"

    @classmethod
    def evaluate_json(cls, value: str, model: str) -> tuple[bool, str]:
        """(1001,1015) Private JSON 값이 유효한 JSON인지 검증한다.

        VN-M-31 모델은 이 태그가 비어 있는 것이 정상이다.

        Args:
            value: 검사할 JSON 문자열.
            model: (0008,1090) Manufacturer's Model Name 값.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if model == "VN-M-31" and not value:
            return True, "OK"
        if not value or not isinstance(value, str):
            return False, "Fail"
        try:
            json.loads(value)
            return True, "OK"
        except json.JSONDecodeError:
            return False, "Fail"

    @classmethod
    def evaluate_study_description(cls, value: str) -> tuple[bool, str]:
        """(0008,1030) Study Description을 검증한다. 비어 있어도 OK.

        Args:
            value: 검사할 Study Description 값.

        Returns:
            항상 (True, "OK") 튜플.
        """
        return True, "OK"

    @classmethod
    def evaluate_series_description(cls, value: str) -> tuple[bool, str]:
        """(0008,103E) Series Description이 'VUNO Med Analysis Result'인지 검증한다.

        Args:
            value: 검사할 Series Description 값.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if value in ["VUNO Med Analysis Result", "VUNO MED Analysis Result"]:
            return True, "OK"
        return False, "Fail"


class TagCheckerApp(QMainWindow):
    """VUNO DICOM 결과 태그 검증 GUI 애플리케이션.

    DICOM 파일을 로드하여 각 태그의 값이 사내 규칙을 준수하는지
    테이블 형태로 표시하고 CSV로 내보내는 기능을 제공한다.
    파일 열기 버튼과 드래그 앤 드롭 두 가지 방식으로 파일을 로드할 수 있다.
    """

    def __init__(self) -> None:
        """메인 윈도우 레이아웃과 위젯을 초기화한다."""
        super().__init__()
        self.setGeometry(100, 100, 1520, 1100)
        self.setAcceptDrops(True)

        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.main_layout = QVBoxLayout(self.central)

        self.file_label = QLabel("No file loaded")
        self.load_button = QPushButton("Open DICOM")
        self.export_button = QPushButton("Export CSV")

        hbox = QHBoxLayout()
        hbox.addWidget(self.file_label)
        hbox.addStretch()
        hbox.addWidget(self.load_button)
        hbox.addWidget(self.export_button)

        self.main_layout.addLayout(hbox)

        self.table = QTableWidget()
        self.main_layout.addWidget(self.table)

        self.load_button.clicked.connect(self.load_dicom)
        self.export_button.clicked.connect(self.export_csv)
        self.tags: list[dict[str, Any]] = []
        self.setWindowTitle("Tag Checker - No file loaded")

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
            self.load_dicom_from_path(filepath)

    def load_dicom(self) -> None:
        """파일 다이얼로그를 열어 DICOM 파일을 선택하고 로드한다."""
        path, _ = QFileDialog.getOpenFileName(self, "Open DICOM", "", "DICOM files (*.dcm)")
        if path:
            self.load_dicom_from_path(path)

    def load_dicom_from_path(self, path: str) -> None:
        """지정된 경로의 DICOM 파일을 읽고 태그를 추출하여 테이블을 갱신한다.

        Args:
            path: 로드할 DICOM 파일의 절대 경로.
        """
        filename = os.path.basename(path)
        self.file_label.setText(filename)
        self.setWindowTitle(f"Tag Checker - {filename}")
        try:
            ds = pydicom.dcmread(path, force=True)
            self.tags = self.extract_tags(ds)
            self.populate_table()
        except Exception as e:
            self.file_label.setText(f"Error loading DICOM: {str(e)}")
            self.tags = []
            self.populate_table()

    def extract_tags(self, ds: pydicom.Dataset) -> list[dict[str, Any]]:
        """Dataset에서 검증 대상 태그를 추출하고 규칙 준수 여부를 평가한다.

        일반 태그와 Private Tag를 순서대로 처리하며,
        동일 태그 ID에 복수 값이 있으면 각각 별도 행으로 추가한다.

        Args:
            ds: 로드된 pydicom Dataset 객체.

        Returns:
            각 태그 정보를 담은 딕셔너리 리스트.
            딕셔너리 키: "group", "element", "desc", "value", "ok", "status".
        """
        tags: list[dict[str, Any]] = []

        def add(tag: BaseTag, desc: str, value: str, ok: bool, status: str) -> None:
            group = f"{int(tag.group):04X}"
            element = f"{int(tag.element):04X}"
            tags.append({
                "group": group, "element": element,
                "desc": desc, "value": value, "ok": ok, "status": status
            })

        model = str(ds[Tag(0x0008, 0x1090)].value).strip() if (0x0008, 0x1090) in ds else ""
        series_num_str = str(ds[Tag(0x0020, 0x0011)].value).strip() if (0x0020, 0x0011) in ds else ""

        conv_tag = Tag(0x0008, 0x0064)
        conv_val = str(ds[conv_tag].value).strip() if conv_tag in ds else ""
        ok, status = TagStatusEvaluator.evaluate_conversion_type(conv_val, series_num_str)
        add(conv_tag, "Conversion Type", conv_val, ok, status)

        tag_map = [
            (Tag(0x0020, 0x000E), "Series Instance UID", TagStatusEvaluator.evaluate_series_uid),
            (Tag(0x0008, 0x0021), "Series Date", TagStatusEvaluator.evaluate_date),
            (Tag(0x0020, 0x0011), "Series Number", TagStatusEvaluator.evaluate_series_number),
            (Tag(0x0008, 0x0070), "Manufacturer", lambda v: TagStatusEvaluator.evaluate_fixed_value(v, "VUNO")),
            (Tag(0x0008, 0x1090), "Manufacturer's Model Name", TagStatusEvaluator.evaluate_model_name),
            (Tag(0x0018, 0x1020), "Software Versions", TagStatusEvaluator.evaluate_version),
            (Tag(0x0008, 0x0012), "Instance Creation Date", TagStatusEvaluator.evaluate_date),
            (Tag(0x0008, 0x0013), "Instance Creation Time", TagStatusEvaluator.evaluate_time),
            (Tag(0x0008, 0x0018), "SOP Instance UID", TagStatusEvaluator.evaluate_sop_instance_uid),
            (Tag(0x0020, 0x0013), "Instance Number", TagStatusEvaluator.evaluate_instance_number),
            (Tag(0x0020, 0x4000), "Image Comments", lambda v: (bool(v), "OK" if v else "Fail")),
            (Tag(0x0008, 0x1030), "Study Description", TagStatusEvaluator.evaluate_study_description),
            (Tag(0x0008, 0x103E), "Series Description", TagStatusEvaluator.evaluate_series_description),
        ]

        private_tag_map = [
            (Tag(0x1001, 0x1002), "Private Version", TagStatusEvaluator.evaluate_version),
            (Tag(0x1001, 0x1004), "Private Flag 0/1", lambda v: (v in ["0", "1"], "OK" if v in ["0", "1"] else "Fail")),
            (Tag(0x1001, 0x1005), "Private Flag 1", lambda v: (v == "1", "OK" if v == "1" else "Fail")),
            (Tag(0x1001, 0x1008), "Private Report", lambda v: TagStatusEvaluator.evaluate_private_json_prefix(v, ['{"report": "', '{"report":"'])),
            (Tag(0x1001, 0x1009), "Private Type", lambda v: TagStatusEvaluator.evaluate_private_1009(model, v)),
            (Tag(0x1001, 0x1011), "Private Report/RADS", lambda v: TagStatusEvaluator.evaluate_private_json_prefix(v, ['{"result":', '{"result" :'])),
            (Tag(0x1001, 0x1015), "Private JSON", lambda v: TagStatusEvaluator.evaluate_json(v, model)),
        ]

        for tag, desc, fn in tag_map:
            try:
                if tag in ds:
                    value = ds[tag].value
                    val = str(value) if not isinstance(value, bytes) else value.decode(errors="ignore")
                    ok, status = fn(val)
                    add(tag, desc, val, ok, status)
                else:
                    add(tag, desc, "", False, "Fail")
            except Exception:
                add(tag, desc, "ERROR", False, "Fail")

        for tag, desc, fn in private_tag_map:
            try:
                if tag in ds:
                    element = ds[tag]
                    if isinstance(element.value, pydicom.multival.MultiValue) or isinstance(element.value, pydicom.sequence.Sequence):
                        for val in element.value:
                            str_val = str(val) if not isinstance(val, bytes) else val.decode(errors="ignore")
                            ok, status = fn(str_val)
                            add(tag, desc, str_val, ok, status)
                    else:
                        value = element.value
                        val = str(value) if not isinstance(value, bytes) else value.decode(errors="ignore")
                        ok, status = fn(val)
                        add(tag, desc, val, ok, status)
                else:
                    add(tag, desc, "", False, "Fail")
            except Exception:
                add(tag, desc, "ERROR", False, "Fail")

        return tags

    def populate_table(self) -> None:
        """추출된 태그 정보를 GroupID 오름차순으로 정렬하여 테이블에 표시한다."""
        self.table.clear()
        self.table.setRowCount(len(self.tags))
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["GroupID", "ElementID", "Description", "Value", "Status"])
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 300)
        self.table.setColumnWidth(3, 800)
        self.table.setColumnWidth(4, 300)

        for row, tag in enumerate(sorted(self.tags, key=lambda x: (int(x["group"], 16), int(x["element"], 16)))):
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
        with open(path, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["GroupID", "ElementID", "Description", "Value", "Status"])
            for tag in sorted(self.tags, key=lambda x: (int(x["group"], 16), int(x["element"], 16))):
                writer.writerow([tag["group"], tag["element"], tag["desc"], tag["value"], tag["status"]])


if __name__ == "__main__":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vuno.tag_checker")
    extend_dicom_dictionary()
    app = QApplication(sys.argv)
    window = TagCheckerApp()
    app.setWindowIcon(QIcon(get_resource_path("checker.ico")))
    window.show()
    sys.exit(app.exec_())
