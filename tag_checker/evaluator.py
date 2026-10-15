"""결과 DICOM 태그 값의 사내 규칙 준수 여부 평가."""
import datetime
import json
import re

import pydicom
from pydicom.tag import BaseTag


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

    # VUNO UID = 1.2.410.200123.{VERSION}.{LEVEL}.... (Series LEVEL=2, SOP LEVEL=3)
    VUNO_UID_ROOT: list[str] = ["1", "2", "410", "200123"]
    VUNO_UID_VERSION: str = "100"

    # (0020,0013) Instance Number = 원본 × multiplier + offset(lo~hi). 900071~73은 SC Report 변형으로 취급.
    # SC는 Fundus가 소견마다 1장씩 만들어 +1+idx가 되므로 offset 1~99 허용.
    INSTANCE_RULES: dict[str, tuple[int, int, int]] = {
        "900010": (100, 1, 99),
        "900070": (1000, 1, 6), "900071": (1000, 1, 6), "900072": (1000, 1, 6), "900073": (1000, 1, 6),
        "900080": (10000, 1, 1),
        "900090": (100000, 1, 1),
    }
    # offset이 페이지 번호를 뜻하는 SC Report 계열
    PAGED_SERIES: set[str] = {"900070", "900071", "900072", "900073"}

    @classmethod
    def _vuno_uid_errors(
        cls, parts: list[str], level: str, series_num: str
    ) -> list[str]:
        """VUNO UID 공통 요소(VERSION.LEVEL.SERIES_NUMBER.UNIX_TIME.RAND_NO)의 오류 목록을 반환한다."""
        version, uid_level, uid_series, unix_time, rand_num = parts[4:9]
        errors: list[str] = []
        if version != cls.VUNO_UID_VERSION:
            errors.append(f"version {version} != {cls.VUNO_UID_VERSION}")
        if uid_level != level:
            errors.append(f"level {uid_level} != {level}")
        if series_num and uid_series != series_num:
            errors.append(f"series number {uid_series} != (0020,0011) {series_num}")
        elif uid_series not in cls.SERIES_MAP:
            errors.append(f"unknown series number {uid_series}")
        if not (unix_time.isdigit() and int(unix_time) > 0):
            errors.append(f"unix time {unix_time}")
        if not (rand_num.isdigit() and 1 <= int(rand_num) <= 32767):
            errors.append(f"rand {rand_num} not in 1~32767")
        return errors

    @classmethod
    def evaluate_series_uid(
        cls, value: str, original_uid: str = "", series_num: str = ""
    ) -> tuple[bool, str]:
        """(0020,000E) Series Instance UID 규칙을 검증하고 어떤 형태인지 판별한다.

        3가지 규칙을 순서대로 시도한다:
        Rule 1 (VUNO_UID): 1.2.410.200123.100.2.{SERIES_NUMBER}.{UNIX_TIME}.{RAND_NO}
        Rule 2 (Extend Ori): {original_uid[:57]}.{series_number}
        Rule 3 (Use Original): 원본 UID 그대로

        original_uid가 주어지면 Rule 2·3을 원본 값과 실제 비교하고,
        없으면 형식만으로 추정한다(Rule 3은 검증 불가이므로 OK 처리).

        Args:
            value: 검사할 UID 문자열.
            original_uid: 페어링된 원본의 Series Instance UID. 없으면 빈 문자열.
            series_num: 결과 파일의 (0020,0011) Series Number. 없으면 빈 문자열.

        Returns:
            (ok, status) 튜플. 예시: (True, "OK, GSPS, VUNO_UID").
        """
        if not value or not isinstance(value, str):
            return False, "Fail (empty)"

        # Rule 1: VUNO UID. 루트가 일치하면 VUNO UID로 간주하고 나머지 요소를 엄격히 검사
        parts = value.split(".")
        if parts[:4] == cls.VUNO_UID_ROOT:
            if len(parts) != 9:
                return False, f"Fail, VUNO_UID (expected 9 components, got {len(parts)})"
            errors = cls._vuno_uid_errors(parts, "2", series_num)
            if errors:
                return False, f"Fail, VUNO_UID ({', '.join(errors)})"
            return True, f"OK, {cls.SERIES_MAP[parts[6]]}, VUNO_UID"

        if original_uid:
            return cls._evaluate_series_uid_with_original(value, original_uid, series_num)

        # Rule 2: Extend original UID — ends with .{series_number}, prefix는 원본 앞 57자 이하
        candidates = [series_num] if series_num else list(cls.SERIES_MAP)
        for sn in candidates:
            suffix = f".{sn}"
            if value.endswith(suffix):
                name = cls.SERIES_MAP.get(sn, sn)
                prefix_len = len(value) - len(suffix)
                if prefix_len <= 57:
                    return True, f"OK, {name}, Extend Ori (unverified)"
                return False, f"Fail, {name}, Extend Ori (prefix length {prefix_len} > 57)"

        # Rule 3: Use original UID as-is (cannot verify without original)
        return True, "OK, Use Original (unverified)"

    @classmethod
    def _evaluate_series_uid_with_original(
        cls, value: str, original_uid: str, series_num: str
    ) -> tuple[bool, str]:
        """원본 Series UID가 있을 때 Rule 2·3 중 어느 형태인지 판별하고 값을 대조한다."""
        # Rule 3: 원본과 완전히 동일
        if value == original_uid:
            return True, "OK, Use Original"

        # Rule 2: {원본 앞 57자}.{series_number}
        candidates = [series_num] if series_num else list(cls.SERIES_MAP)
        for sn in candidates:
            name = cls.SERIES_MAP.get(sn, sn)
            if value == f"{original_uid[:57]}.{sn}":
                return True, f"OK, {name}, Extend Ori"
            if value.endswith(f".{sn}"):
                return False, f"Fail, {name}, Extend Ori (prefix != original[:57])"

        return False, "Fail, not VUNO_UID / Extend Ori / Use Original"

    @classmethod
    def evaluate_sop_instance_uid(
        cls, value: str, series_num: str = "", instance_num: str = "", original_sop: str = ""
    ) -> tuple[bool, str]:
        """(0008,0018) SOP Instance UID 규칙을 검증한다.

        형식: 1.2.410.200123.100.3.{SERIES_NUMBER}.{UNIX_TIME}.{RAND_NO}.{INSTANCE_NUMBER}
        스펙상 무조건 VUNO UID이므로 원본 기반 UID 옵션({원본[:51]}.{INSTANCE_NUMBER})은 Fail.

        Args:
            value: 검사할 UID 문자열.
            series_num: 결과 파일의 (0020,0011) Series Number. 없으면 빈 문자열.
            instance_num: 결과 파일의 (0020,0013) Instance Number. 없으면 빈 문자열.
            original_sop: 페어링된 원본의 SOP Instance UID. 없으면 빈 문자열.

        Returns:
            (ok, status) 튜플. 예시: (True, "OK, GSPS").
        """
        if not value or not isinstance(value, str):
            return False, "Fail (empty)"
        parts = value.split(".")
        if parts[:4] != cls.VUNO_UID_ROOT:
            if original_sop and instance_num and value == f"{original_sop[:51]}.{instance_num}":
                return False, "Fail (original-based UID option, VUNO UID required)"
            return False, "Fail (prefix != 1.2.410.200123.100.3)"
        if len(parts) != 10:
            return False, f"Fail (expected 10 components, got {len(parts)})"
        errors = cls._vuno_uid_errors(parts, "3", series_num)
        uid_instance = parts[9]
        if instance_num and uid_instance != instance_num:
            errors.append(f"instance {uid_instance} != (0020,0013) {instance_num}")
        elif not uid_instance.isdigit():
            errors.append(f"instance {uid_instance}")
        if errors:
            return False, f"Fail ({', '.join(errors)})"
        return True, f"OK, {cls.SERIES_MAP[parts[6]]}"

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
            SC:               원본 × 100   + 1(+idx) → Fundus는 소견마다 +1씩 증가
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
                page = f" ({offset}page)" if series_num in cls.PAGED_SERIES else ""
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
            page = f" ({offset}page)" if series_num in cls.PAGED_SERIES else ""
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
        if s == "{}":
            return False, 'Fail (empty JSON "{}")'
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
        """(1001,1015) Private JSON 값이 유효한 JSON 객체인지 검증한다.

        VN-M-31 모델은 이 태그가 비어 있는 것이 정상이다.
        '"P"'처럼 JSON이지만 객체가 아닌 값(Triage GSPS 문자 단위 분해 버그)은 Fail.

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
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            return False, f"Fail (invalid JSON: {e.msg} at {e.pos})"
        if not isinstance(parsed, dict):
            return False, f"Fail (not a JSON object: {type(parsed).__name__})"
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
