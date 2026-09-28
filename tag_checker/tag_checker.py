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
from typing import Any

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QStackedWidget
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

    # (1001,1011)이 적용되는 CXR 제품군. Triage(VN-M-30)는 스펙 예시에 없어 있으면 검증, 없으면 Skip.
    CXR_FAMILY: set[str] = {"VN-M-02", "VN-M-30", "VN-M-31"}
    PRIVATE_1011_REQUIRED: set[str] = {"VN-M-02", "VN-M-31"}
    PRIVATE_1011_SERIES: set[str] = {"900010", "900090"}

    # (0008,0064) Conversion Type "WSD"가 필수인 Series Number
    CONVERSION_TYPE_SERIES: set[str] = {"900010", "900070"}

    # (0020,0013) Instance Number = 원본 × multiplier + offset(lo~hi). 900071~73은 SC Report 변형으로 취급.
    INSTANCE_RULES: dict[str, tuple[int, int, int]] = {
        "900010": (100, 1, 1),
        "900070": (1000, 1, 6), "900071": (1000, 1, 6), "900072": (1000, 1, 6), "900073": (1000, 1, 6),
        "900080": (10000, 1, 1),
        "900090": (100000, 1, 1),
    }

    @classmethod
    def evaluate_series_uid(cls, value: str, original_uid: str = "") -> tuple[bool, str]:
        """(0020,000E) Series Instance UID 규칙을 검증하고 어떤 형태인지 판별한다.

        3가지 규칙을 순서대로 시도한다:
        Rule 1 (VUNO_UID): 1.2.410.200123.{VERSION}.{LEVEL}.{SERIES_NUMBER}.{UNIX_TIME}.{RAND_NO}
        Rule 2 (Extend Ori): {original_uid}.{series_number} — 원본 ≤50자면 그대로, 초과면 57자 절단
        Rule 3 (Use Original): 원본 UID 그대로

        original_uid가 주어지면 Rule 2·3을 원본 값과 실제 비교하고,
        없으면 형식만으로 추정한다(Rule 3은 검증 불가이므로 OK 처리).

        Args:
            value: 검사할 UID 문자열.
            original_uid: 페어링된 원본의 Series Instance UID. 없으면 빈 문자열.

        Returns:
            (ok, status) 튜플. 예시: (True, "OK, series, GSPS, VUNO_UID").
        """
        if not value or not isinstance(value, str):
            return False, "Fail"

        if original_uid:
            return cls._evaluate_series_uid_with_original(value, original_uid)

        # Rule 1: VUNO base UID
        parts = value.split(".")
        if len(parts) >= 9 and parts[:4] == ["1", "2", "410", "200123"]:
            version, level, series_num, unix_time, rand_num = parts[4:9]
            if (version.isdigit() and level in ["1", "2", "3"]
                    and series_num in cls.SERIES_MAP
                    and unix_time.isdigit() and int(unix_time) > 0
                    and rand_num.isdigit() and 1 <= int(rand_num) <= 32767):
                level_txt = {"1": "study", "2": "series", "3": "instance SOP"}.get(level, level)
                series_txt = cls.SERIES_MAP.get(series_num, series_num)
                return True, f"OK, {level_txt}, {series_txt}, VUNO_UID"

        # Rule 2: Extend original UID — ends with .{series_number}
        # 원본이 51~56자면 57자 절단 없이 그대로 쓰이므로 prefix 길이는 최대 57
        for series_num, series_name in cls.SERIES_MAP.items():
            suffix = f".{series_num}"
            if value.endswith(suffix):
                prefix_len = len(value) - len(suffix)
                if prefix_len <= 57:
                    return True, f"OK, {series_name}, Extend Ori"
                return False, f"Fail, {series_name}, Extend Ori (prefix length {prefix_len})"

        # Rule 3: Use original UID as-is (cannot verify without original)
        return True, "OK, Use Original (unverified)"

    @classmethod
    def _evaluate_series_uid_with_original(cls, value: str, original_uid: str) -> tuple[bool, str]:
        """원본 Series UID가 있을 때 Rule 1~3 중 어느 형태인지 판별하고 값을 대조한다."""
        vuno_ok, vuno_status = cls.evaluate_series_uid(value)
        if vuno_status.endswith("VUNO_UID"):
            return vuno_ok, vuno_status

        # Rule 3: 원본과 완전히 동일
        if value == original_uid:
            return True, "OK, Use Original"

        # Rule 2: {원본 또는 57자 절단}.{series_number}
        base = original_uid if len(original_uid) <= 50 else original_uid[:57]
        for series_num, series_name in cls.SERIES_MAP.items():
            if value == f"{base}.{series_num}":
                return True, f"OK, {series_name}, Extend Ori"
            if value.endswith(f".{series_num}"):
                return False, f"Fail, {series_name}, Extend Ori (prefix != original)"

        return False, "Fail, not VUNO_UID / Extend Ori / Use Original"

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
            return False, "Fail (empty)"
        parts = value.split(".")
        if len(parts) < 10:
            return False, f"Fail (expected 10 components, got {len(parts)})"
        if parts[:5] != ["1", "2", "410", "200123", "100"]:
            return False, "Fail (prefix != 1.2.410.200123.100)"
        level, series_num, unix_time, rand_num, instance_num = parts[5:10]
        errors: list[str] = []
        if level not in ["1", "2", "3"]:
            errors.append(f"level {level}")
        if series_num not in cls.SERIES_MAP:
            errors.append(f"series number {series_num}")
        if not (unix_time.isdigit() and int(unix_time) > 0):
            errors.append(f"unix time {unix_time}")
        if not (rand_num.isdigit() and 1 <= int(rand_num) <= 32767):
            errors.append(f"rand {rand_num} not in 1~32767")
        if not instance_num.isdigit():
            errors.append(f"instance {instance_num}")
        level_txt = {"1": "study", "2": "series", "3": "instance SOP"}.get(level, level)
        series_txt = cls.SERIES_MAP.get(series_num, series_num)
        if errors:
            return False, f"Fail ({', '.join(errors)})"
        return True, f"OK, {level_txt}, {series_txt}"

    @classmethod
    def evaluate_date(cls, value: str) -> tuple[bool, str]:
        """DICOM 날짜 태그 값(YYYYMMDD)을 검증한다.

        Args:
            value: 검사할 날짜 문자열.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        ok = is_valid_date(value)
        return ok, "OK" if ok else "Fail (not YYYYMMDD)"

    @classmethod
    def evaluate_time(cls, value: str) -> tuple[bool, str]:
        """DICOM 시각 태그 값(HHMMSS 또는 HHMMSS.FFFFFF)을 검증한다.

        Args:
            value: 검사할 시각 문자열.

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        ok = is_valid_time(value)
        return ok, "OK" if ok else "Fail (not HHMMSS[.FFFFFF])"

    @classmethod
    def evaluate_series_number(cls, value: str) -> tuple[bool, str]:
        """(0020,0011) Series Number가 정의된 SERIES_MAP 코드인지 검증한다.

        Args:
            value: 검사할 Series Number 문자열.

        Returns:
            (ok, series_name) 튜플. 예시: (True, "GSPS") / (False, "Fail").
        """
        if not value:
            return False, "Fail (empty)"
        if value not in cls.SERIES_MAP:
            return False, f"Fail (unknown series number {value})"
        return True, cls.SERIES_MAP[value]

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
        series_name = cls.SERIES_MAP.get(series_num, series_num or "unknown series")
        if series_num in cls.CONVERSION_TYPE_SERIES:
            if value == "WSD":
                return True, "OK"
            return False, 'Fail (expected "WSD")' if value else 'Fail (missing, "WSD" required)'
        if value == "":
            return True, "Skip (SC / SC Report only)"
        return False, f"Fail (SC / SC Report only, found in {series_name})"

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
        return ok, "OK" if ok else f'Fail (expected "{expected}")'

    @classmethod
    def evaluate_model_name(cls, value: str) -> tuple[bool, str]:
        """(0008,1090) Manufacturer's Model Name이 정의된 모델 코드인지 검증한다.

        Args:
            value: 검사할 모델명 문자열.

        Returns:
            (ok, display_name) 튜플. 정의되지 않은 값이면 원래 값을 그대로 반환.
        """
        if not value:
            return False, "Fail (empty)"
        if value not in cls.MODEL_MAP:
            return False, f"Fail (unknown model {value})"
        return True, cls.MODEL_MAP[value]

    @classmethod
    def evaluate_version(cls, value: str) -> tuple[bool, str]:
        """태그 값에 x.x.x 형식의 버전 문자열이 포함되어 있는지 검증한다.

        Args:
            value: 검사할 문자열. 예시: "CXR Web 1.2.0".

        Returns:
            (ok, "OK"/"Fail") 튜플.
        """
        if not value or not isinstance(value, str):
            return False, "Fail (empty)"
        ok = bool(re.search(r"\d+\.\d+\.\d+", value))
        return ok, "OK" if ok else "Fail (no x.y.z version)"

    @classmethod
    def evaluate_instance_number(
        cls, value: str | None, series_num: str = "", original_instance: str = ""
    ) -> tuple[bool, str]:
        """(0020,0013) Instance Number를 검증하고 DICOM 종류와 SC Report 페이지를 판별한다.

        규칙:
            SC:               원본 × 100   + 1     → 끝 2자리 "01"
            SC Report:        원본 × 1000  + page  → 끝 3자리가 페이지 번호(1~6)
            Encap.PDF Report: 원본 × 10000 + 1     → 끝 4자리 "0001"
            GSPS:             원본 × 100000 + 1    → 끝 5자리 "00001"

        original_instance와 series_num이 모두 있으면 기대값을 계산해 정확히 대조하고,
        없으면 끝자리 패턴으로 종류만 추정한다(unverified).

        Args:
            value: 검사할 Instance Number 값.
            series_num: (0020,0011) Series Number 값. 없으면 빈 문자열.
            original_instance: 원본 Instance Number. 원본에 없으면 "1". 원본 미로딩 시 빈 문자열.

        Returns:
            (ok, status) 튜플. 예시: (True, "OK, SC Report (2page)"), (True, "SC (unverified)").
        """
        if value is None:
            return False, "Fail (missing)"
        s = str(value).strip()
        if not s or not s.isdigit():
            return False, f"Fail (not a number: {s})" if s else "Fail (empty)"

        if original_instance and series_num in cls.INSTANCE_RULES:
            return cls._evaluate_instance_number_exact(int(s), series_num, original_instance)

        # 원본 없음: Series Number를 알면 끝자리 offset만 검사, 모르면 패턴으로 종류 추정
        if series_num in cls.INSTANCE_RULES:
            mult, lo, hi = cls.INSTANCE_RULES[series_num]
            name = cls.SERIES_MAP[series_num]
            base, offset = divmod(int(s), mult)
            if lo <= offset <= hi and base > 0:
                page = f" ({offset}page)" if hi > 1 else ""
                return True, f"{name}{page} (unverified, no original)"
            rng = f"{lo}" if lo == hi else f"{lo}~{hi}"
            return False, f"Fail (expected Nx{mult}+{rng} for {name}, got {s})"

        ok, kind = cls._guess_instance_kind(s)
        if not ok:
            return False, f"Fail (no SC/SC Report/PDF/GSPS pattern: {s})"
        return True, f"{kind} (unverified, no original)"

    @classmethod
    def _evaluate_instance_number_exact(
        cls, number: int, series_num: str, original_instance: str
    ) -> tuple[bool, str]:
        """원본 Instance Number × multiplier + offset 규칙으로 정확히 대조한다."""
        if not original_instance.isdigit():
            return False, f"Fail (original instance not a number: {original_instance})"
        base = int(original_instance)
        mult, lo, hi = cls.INSTANCE_RULES[series_num]
        name = cls.SERIES_MAP[series_num]
        quotient, offset = divmod(number, mult)
        if quotient == base and lo <= offset <= hi:
            page = f" ({offset}page)" if hi > 1 else ""
            return True, f"OK, {name}{page}"
        rng = f"{lo}" if lo == hi else f"{lo}~{hi}"
        return False, f"Fail (expected {base}x{mult}+{rng}, got {number})"

    @classmethod
    def _guess_instance_kind(cls, s: str) -> tuple[bool, str]:
        """끝자리 패턴만으로 DICOM 종류를 추정한다."""
        if s.endswith("00001"):
            return True, "GSPS"
        if s.endswith("0001"):
            return True, "Encap. PDF Report"

        # SC Report: 끝 3자리가 페이지 번호
        # 3자리 전체이고 '0'으로 시작하거나, 4자리 이상이고 4번째 자리가 '0'이 아닌 경우
        if len(s) >= 3:
            last3 = s[-3:]
            if last3.isdigit():
                page = int(last3)
                is_short_zero = (len(s) == 3 and s[0] == '0')
                is_long_nonzero = (len(s) >= 4 and s[-4] != '0')
                if (is_short_zero or is_long_nonzero) and 1 <= page <= 999:
                    return True, f"SC Report ({page}page)"

        if s.endswith("01"):
            return True, "SC"

        return False, ""

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
        if not value:
            return False, "Fail (empty)"
        if not model:
            return False, "Fail (no Manufacturer's Model Name to cross-check)"
        model = model.strip()
        value = str(value).strip()
        if model not in cls.MODEL_MAP:
            return False, f"Fail (unknown model {model})"
        expected = "3" if model == "VN-M-02" else "2"
        if value == expected:
            return True, "OK"
        return False, f'Fail (expected "{expected}" for {cls.MODEL_MAP[model]})'

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
            return False, "Fail (empty)"
        s = value.strip()
        # '{ "key"' 형태를 '{"key"'로 정규화하여 공백 차이를 허용
        s = re.sub(r'^\{\s*"', '{"', s)
        ok = any(s.startswith(p) for p in prefix_list)
        return ok, "OK" if ok else f"Fail (must start with {prefix_list[0]})"

    @classmethod
    def evaluate_private_1005(cls, value: str) -> tuple[bool, str]:
        """(1001,1005) CXR Link 플래그를 검증한다. 없으면 Skip, 있으면 "1"이어야 한다.

        CXR Link 결과인지 판별할 정보가 없어 부재 시 Skip 처리한다.

        Args:
            value: 검사할 값. 태그 부재 시 빈 문자열.

        Returns:
            (ok, status) 튜플.
        """
        if not value:
            return True, "Skip (CXR Link only)"
        return (True, "OK") if value == "1" else (False, 'Fail (expected "1")')

    @classmethod
    def evaluate_private_1011(cls, value: str, model: str, series_num: str) -> tuple[bool, str]:
        """(1001,1011) 값을 검증한다. CXR 제품군의 SC·GSPS에만 존재해야 한다.

        Args:
            value: 검사할 값. 태그 부재 시 빈 문자열.
            model: (0008,1090) Manufacturer's Model Name 값.
            series_num: (0020,0011) Series Number 값.

        Returns:
            (ok, status) 튜플.
        """
        applicable = model in cls.CXR_FAMILY and series_num in cls.PRIVATE_1011_SERIES
        if not applicable:
            if value:
                where = f"{cls.MODEL_MAP.get(model, model or 'unknown model')} {cls.SERIES_MAP.get(series_num, series_num)}"
                return False, f"Fail (CXR SC/GSPS only, found in {where})"
            return True, "Skip (CXR SC/GSPS only)"
        if not value:
            if model in cls.PRIVATE_1011_REQUIRED:
                return False, "Fail (missing)"
            return True, f"Skip (optional for {cls.MODEL_MAP[model]})"
        return cls.evaluate_private_json_prefix(value, ['{"result":', '{"result" :'])

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
            return True, "Skip (empty allowed for CXR Assist)"
        if not value or not isinstance(value, str):
            return False, "Fail (empty)"
        try:
            json.loads(value)
            return True, "OK"
        except json.JSONDecodeError as e:
            return False, f"Fail (invalid JSON: {e.msg} at {e.pos})"

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
        return False, 'Fail (expected "VUNO Med Analysis Result")'

    @classmethod
    def evaluate_same_as_original(
        cls, result_val: str, original_ds: "pydicom.Dataset | None", tag: BaseTag
    ) -> tuple[bool, str]:
        """결과 파일의 태그 값이 원본과 동일한지 검증한다.

        원본에 태그 또는 값이 없으면 결과도 없어야 한다.

        Args:
            result_val: 결과 파일의 태그 값.
            original_ds: 원본 DICOM Dataset. None이면 원본 미로딩.
            tag: 비교 대상 태그.

        Returns:
            (ok, status) 튜플.
        """
        if original_ds is None:
            return False, "No original"
        orig_val = str(original_ds[tag].value).strip() if tag in original_ds else ""
        if result_val == orig_val:
            return True, "OK"
        if not orig_val:
            return False, "Fail (should be empty)"
        return False, f"Fail (original: {orig_val})"


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

        self.file_label = QLabel("No file loaded")
        self.load_button = QPushButton("Open DICOM")
        self.export_button = QPushButton("Export CSV")

        hbox = QHBoxLayout()
        hbox.addWidget(self.file_label)
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

    @staticmethod
    def _get_tag_str(ds: pydicom.Dataset, tag: BaseTag) -> str:
        """Dataset에서 태그 값을 문자열로 반환한다. 태그가 없으면 빈 문자열."""
        if tag in ds:
            return str(ds[tag].value).strip()
        return ""

    def _is_result_file(self, ds: pydicom.Dataset) -> bool:
        """(0008,0070) Manufacturer가 'VUNO'이면 결과 파일로 판별한다."""
        return self._get_tag_str(ds, Tag(0x0008, 0x0070)) == "VUNO"

    def _verify_pair(self) -> bool:
        """원본과 결과 파일이 같은 Study에 속하는지 검증한다.

        (0020,000D) Study Instance UID → (0010,0020) Patient ID 순으로 시도한다.
        두 값 모두 없으면 False를 반환한다.

        Returns:
            두 파일의 페어 키가 일치하면 True, 그 외 False.
        """
        if self.original_ds is None or self.result_ds is None:
            return False
        for tag in (Tag(0x0020, 0x000D), Tag(0x0010, 0x0020)):
            orig = self._get_tag_str(self.original_ds, tag)
            result = self._get_tag_str(self.result_ds, tag)
            if orig and result:
                return orig == result
        return False

    def _update_labels(self) -> None:
        """현재 로딩 상태에 따라 file_label과 윈도우 타이틀을 갱신한다."""
        orig_name = os.path.basename(self.original_path) if self.original_path else ""
        result_name = os.path.basename(self.result_path) if self.result_path else ""

        if orig_name and result_name:
            suffix = "" if self._verify_pair() else " (mismatch)"
            label = f"Original: {orig_name}{suffix} | Result: {result_name}"
            title = f"Tag Checker - {result_name}"
            self.stack.setCurrentIndex(1)
        elif result_name:
            label = f"Result: {result_name} | No original"
            title = f"Tag Checker - {result_name}"
            self.stack.setCurrentIndex(1)
        elif orig_name:
            label = f"Original: {orig_name} | No result"
            title = f"Tag Checker - {orig_name}"
            self.stack.setCurrentIndex(0)
        else:
            label = "No file loaded"
            title = "Tag Checker - No file loaded"
            self.stack.setCurrentIndex(1)

        self.file_label.setText(label)
        self.setWindowTitle(title)

    def load_dicom_from_path(self, path: str) -> None:
        """지정된 경로의 DICOM 파일을 읽고 태그를 추출하여 테이블을 갱신한다.

        (0008,0070)=="VUNO" 여부로 결과/원본을 판별하여 상태를 갱신한다.
        두 파일이 모두 로딩되면 Study UID(또는 Patient ID)로 페어를 검증하고,
        일치하는 경우에만 원본 비교 검증((0008,0090), (0020,0010))을 수행한다.

        Args:
            path: 로드할 DICOM 파일의 절대 경로.
        """
        try:
            ds = pydicom.dcmread(path, force=True)
        except Exception as e:
            self.file_label.setText(f"Error loading DICOM: {e}")
            return

        if self._is_result_file(ds):
            self.result_ds = ds
            self.result_path = path
        else:
            self.original_ds = ds
            self.original_path = path

        if self.result_ds is not None:
            paired = self._verify_pair() if self.original_ds is not None else False
            self.tags = self.extract_tags(self.result_ds, self.original_ds if paired else None)
        else:
            self.tags = []

        self._update_labels()
        self.populate_table()

    def extract_tags(
        self, ds: pydicom.Dataset, original_ds: pydicom.Dataset | None = None
    ) -> list[dict[str, Any]]:
        """Dataset에서 검증 대상 태그를 추출하고 규칙 준수 여부를 평가한다.

        일반 태그와 Private Tag를 순서대로 처리하며,
        동일 태그 ID에 복수 값이 있으면 각각 별도 행으로 추가한다.
        original_ds가 제공되면 원본 비교가 필요한 태그((0008,0090), (0020,0010))도 검증한다.

        Args:
            ds: 로드된 pydicom Dataset 객체.
            original_ds: 원본 DICOM Dataset. None이면 원본 비교 검증 불가.

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
        original_series_uid = (
            str(original_ds[Tag(0x0020, 0x000E)].value).strip()
            if original_ds is not None and (0x0020, 0x000E) in original_ds else ""
        )
        # 원본에 Instance Number가 없으면 스펙상 1로 간주. 원본 미로딩 시 빈 문자열.
        original_instance = ""
        if original_ds is not None:
            original_instance = (
                str(original_ds[Tag(0x0020, 0x0013)].value).strip()
                if (0x0020, 0x0013) in original_ds else ""
            ) or "1"

        conv_tag = Tag(0x0008, 0x0064)
        conv_val = str(ds[conv_tag].value).strip() if conv_tag in ds else ""
        ok, status = TagStatusEvaluator.evaluate_conversion_type(conv_val, series_num_str)
        add(conv_tag, "Conversion Type", conv_val, ok, status)

        for tag, desc in [
            (Tag(0x0008, 0x0090), "Referring Physician Name"),
            (Tag(0x0020, 0x0010), "Study ID"),
        ]:
            try:
                val = str(ds[tag].value).strip() if tag in ds else ""
                ok, status = TagStatusEvaluator.evaluate_same_as_original(val, original_ds, tag)
                add(tag, desc, val, ok, status)
            except Exception as e:
                add(tag, desc, "ERROR", False, f"Fail (read error: {e})")

        tag_map = [
            (Tag(0x0020, 0x000E), "Series Instance UID",
             lambda v: TagStatusEvaluator.evaluate_series_uid(v, original_series_uid)),
            (Tag(0x0008, 0x0021), "Series Date", TagStatusEvaluator.evaluate_date),
            (Tag(0x0020, 0x0011), "Series Number", TagStatusEvaluator.evaluate_series_number),
            (Tag(0x0008, 0x0070), "Manufacturer", lambda v: TagStatusEvaluator.evaluate_fixed_value(v, "VUNO")),
            (Tag(0x0008, 0x1090), "Manufacturer's Model Name", TagStatusEvaluator.evaluate_model_name),
            (Tag(0x0018, 0x1020), "Software Versions", TagStatusEvaluator.evaluate_version),
            (Tag(0x0008, 0x0012), "Instance Creation Date", TagStatusEvaluator.evaluate_date),
            (Tag(0x0008, 0x0013), "Instance Creation Time", TagStatusEvaluator.evaluate_time),
            (Tag(0x0008, 0x0018), "SOP Instance UID", TagStatusEvaluator.evaluate_sop_instance_uid),
            (Tag(0x0020, 0x0013), "Instance Number",
             lambda v: TagStatusEvaluator.evaluate_instance_number(v, series_num_str, original_instance)),
            (Tag(0x0020, 0x4000), "Image Comments", lambda v: (bool(v), "OK" if v else "Fail (empty)")),
            (Tag(0x0008, 0x1030), "Study Description", TagStatusEvaluator.evaluate_study_description),
            (Tag(0x0008, 0x103E), "Series Description", TagStatusEvaluator.evaluate_series_description),
        ]

        private_tag_map = [
            (Tag(0x1001, 0x1002), "Private Version", TagStatusEvaluator.evaluate_version),
            (Tag(0x1001, 0x1004), "Private Flag 0/1",
             lambda v: (v in ["0", "1"], "OK" if v in ["0", "1"] else 'Fail (expected "0" or "1")')),
            (Tag(0x1001, 0x1005), "Private Flag 1", TagStatusEvaluator.evaluate_private_1005),
            (Tag(0x1001, 0x1008), "Private Report", lambda v: TagStatusEvaluator.evaluate_private_json_prefix(v, ['{"report": "', '{"report":"'])),
            (Tag(0x1001, 0x1009), "Private Type", lambda v: TagStatusEvaluator.evaluate_private_1009(model, v)),
            (Tag(0x1001, 0x1011), "Private Report/RADS",
             lambda v: TagStatusEvaluator.evaluate_private_1011(v, model, series_num_str)),
            (Tag(0x1001, 0x1015), "Private JSON", lambda v: TagStatusEvaluator.evaluate_json(v, model)),
        ]
        # 부재 자체가 의미를 갖는 태그: 빈 값으로 평가 함수를 호출해 Skip/Fail을 판정
        evaluate_when_absent = {Tag(0x1001, 0x1005), Tag(0x1001, 0x1011), Tag(0x1001, 0x1015)}

        for tag, desc, fn in tag_map:
            try:
                if tag in ds:
                    value = ds[tag].value
                    val = str(value) if not isinstance(value, bytes) else value.decode(errors="ignore")
                    ok, status = fn(val)
                    add(tag, desc, val, ok, status)
                else:
                    add(tag, desc, "", False, "Fail (missing)")
            except Exception as e:
                add(tag, desc, "ERROR", False, f"Fail (read error: {e})")

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
                elif tag in evaluate_when_absent:
                    ok, status = fn("")
                    add(tag, desc, "", ok, status)
                else:
                    add(tag, desc, "", False, "Fail (missing)")
            except Exception as e:
                add(tag, desc, "ERROR", False, f"Fail (read error: {e})")

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
        self.table.setColumnWidth(3, 710)
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
    extend_dicom_dictionary()
    app = QApplication(sys.argv)
    icon_path = get_resource_path("checker.ico")
    app.setWindowIcon(QIcon(icon_path))
    window = TagCheckerApp()
    window.setWindowIcon(QIcon(icon_path))
    window.show()
    sys.exit(app.exec_())
