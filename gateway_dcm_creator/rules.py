"""태그별 값 생성 규칙, 설정 기본값, 입력 검증.

GUI와 분리된 순수 로직만 둔다(테스트와 CLI 재사용 목적).
"""
import datetime
import random
import re
import string

# GUI 행 순서와 동일. settings["enabled"]의 키로도 쓴다.
TAG_KEYS: list[str] = [
    "study_date", "series_date", "study_time", "series_time", "accession",
    "image_comments", "patient_name", "patient_id", "birth_date", "sex", "age",
    "modality", "sop_class",
]

MODALITIES: list[str] = ["DX", "CR", "XC", "OT"]
SEXES: list[str] = ["M", "F", "O"]
PREG_OPTIONS: list[str] = ["Null", "N", "Y"]

# Modality → SOP Class UID. DX는 For Presentation으로 고정.
SOP_CLASS_BY_MODALITY: dict[str, str] = {
    "CR": "1.2.840.10008.5.1.4.1.1.1",          # Computed Radiography Image Storage
    "DX": "1.2.840.10008.5.1.4.1.1.1.1",        # Digital X-Ray Image Storage - For Presentation
    "XC": "1.2.840.10008.5.1.4.1.1.77.1.4",     # VL Photographic Image Storage
    "OT": "1.2.840.10008.5.1.4.1.1.7",          # Secondary Capture Image Storage
}

ACCESSION_MIN_LEN = 6
ACCESSION_MAX_LEN = 16  # SH 최대 길이

MALE_FIRST = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles",
    "Daniel", "Matthew", "Anthony", "Mark", "Steven", "Paul", "Andrew", "Joshua", "Kevin", "Brian",
    "George", "Edward", "Ryan", "Jacob", "Eric", "Jason", "Justin", "Brandon", "Samuel", "Henry",
]
FEMALE_FIRST = [
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen",
    "Lisa", "Nancy", "Betty", "Sandra", "Ashley", "Emily", "Donna", "Michelle", "Carol", "Amanda",
    "Melissa", "Deborah", "Laura", "Rebecca", "Sharon", "Cynthia", "Kathleen", "Amy", "Anna", "Emma",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Anderson", "Taylor",
    "Moore", "Martin", "Jackson", "Thompson", "White", "Harris", "Clark", "Lewis", "Walker", "Hall",
    "Allen", "Young", "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Carter",
    "Mitchell", "Roberts", "Turner", "Phillips", "Campbell", "Parker", "Evans", "Edwards", "Collins", "Stewart",
]

_TIME_RE = re.compile(r"^(\d{2}):(\d{2})$")
_DATE_FMT = "%Y%m%d"


def default_settings() -> dict:
    """처음 실행할 때 쓰는 설정. 모든 태그 toggle은 on."""
    today = datetime.date.today().strftime(_DATE_FMT)
    return {
        "input_dir": "",
        "output_dir": "",
        "include_subfolders": True,
        "regenerate_uids": False,
        "convert_pixels": False,
        "enabled": {key: True for key in TAG_KEYS},
        "study_date": {"start": today, "end": today},
        "series_date": {"same": True, "start": today, "end": today},
        "study_time": {"start": "09:00", "end": "18:00"},
        "series_time": {"same": True, "start": "09:00", "end": "18:00"},
        "drid": "A012345",
        "preg": {"Null": False, "N": True, "Y": False},
        "birth_date": {"start": "19700101", "end": "19991231"},
        "sex": {sex: True for sex in SEXES},
        "modality": {m: True for m in MODALITIES},
    }


def merge_settings(saved: dict) -> dict:
    """저장된 설정을 기본값 위에 덮는다. 모르는 키와 타입이 다른 값은 버린다."""
    merged = default_settings()
    if not isinstance(saved, dict):
        return merged
    for key, default in merged.items():
        if key not in saved:
            continue
        value = saved[key]
        if isinstance(default, dict):
            if isinstance(value, dict):
                for sub_key, sub_default in default.items():
                    if sub_key in value and isinstance(value[sub_key], type(sub_default)):
                        default[sub_key] = value[sub_key]
        elif isinstance(value, type(default)):
            merged[key] = value
    return merged


# ---------------------------------------------------------------- 파싱 / 검증

def parse_date(value: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime(value, _DATE_FMT).date()
    except (TypeError, ValueError):
        return None


def parse_hhmm(value: str) -> int | None:
    """'hh:mm'을 자정 기준 초로 바꾼다. 24:00까지 허용."""
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    hh, mm = int(m[1]), int(m[2])
    if mm > 59 or hh > 24 or (hh == 24 and mm != 0):
        return None
    return hh * 3600 + mm * 60


def parse_drid(text: str) -> list[str]:
    """쉼표나 | 로 나누고 앞뒤 공백과 빈 항목을 버린다."""
    return [item.strip() for item in re.split(r"[,|]", text) if item.strip()]


def _check_date_range(errors: list[str], label: str, rng: dict) -> tuple[datetime.date, datetime.date] | None:
    start, end = parse_date(rng["start"]), parse_date(rng["end"])
    if start is None or end is None:
        errors.append(f"{label}: 날짜 형식이 올바르지 않습니다.")
        return None
    if start > end:
        errors.append(f"{label}: start가 end보다 늦습니다.")
        return None
    return start, end


def _check_time_range(errors: list[str], label: str, rng: dict) -> None:
    start, end = parse_hhmm(rng["start"]), parse_hhmm(rng["end"])
    if start is None or end is None:
        errors.append(f"{label}: 시간은 hh:mm 형식(00:00~24:00)이어야 합니다.")
    elif start >= end:
        errors.append(f"{label}: start가 end보다 빨라야 합니다. (자정을 넘는 범위는 지원하지 않습니다)")


def validate(settings: dict) -> list[str]:
    """실행 전 검증. 오류 메시지 목록을 돌려준다(비어 있으면 통과)."""
    errors: list[str] = []
    on = settings["enabled"]

    study_rng = None
    if on["study_date"]:
        study_rng = _check_date_range(errors, "Study Date", settings["study_date"])
    if on["series_date"] and not settings["series_date"]["same"]:
        _check_date_range(errors, "Series Date", settings["series_date"])
    if on["study_time"]:
        _check_time_range(errors, "Study Time", settings["study_time"])
    if on["series_time"] and not settings["series_time"]["same"]:
        _check_time_range(errors, "Series Time", settings["series_time"])

    if on["image_comments"]:
        drids = parse_drid(settings["drid"])
        if not drids:
            errors.append("Image Comments: DRID 값을 하나 이상 입력하세요.")
        elif any(ch in item for item in drids for ch in ";=\\"):
            errors.append("Image Comments: DRID 값에는 ; = \\ 문자를 쓸 수 없습니다.")
        if not any(settings["preg"].values()):
            errors.append("Image Comments: PREG를 하나 이상 체크하세요.")

    if on["birth_date"]:
        birth_rng = _check_date_range(errors, "Patient's Birth Date", settings["birth_date"])
        if birth_rng and study_rng and birth_rng[1] > study_rng[0]:
            errors.append("Patient's Birth Date: end가 Study Date start보다 늦으면 나이가 음수가 될 수 있습니다.")
    if on["sex"] and not any(settings["sex"].values()):
        errors.append("Patient's Sex: 하나 이상 체크하세요.")
    if on["modality"] and not any(settings["modality"].values()):
        errors.append("Modality: 하나 이상 체크하세요.")
    return errors


# ---------------------------------------------------------------- 값 생성

def random_date(rng: random.Random, start: str, end: str) -> str:
    d0, d1 = parse_date(start), parse_date(end)
    return (d0 + datetime.timedelta(days=rng.randint(0, (d1 - d0).days))).strftime(_DATE_FMT)


def random_time(rng: random.Random, start: str, end: str) -> str:
    """[start, end - 1초] 범위의 hhmmss."""
    sec = rng.randint(parse_hhmm(start), parse_hhmm(end) - 1)
    return f"{sec // 3600:02d}{sec % 3600 // 60:02d}{sec % 60:02d}"


def random_accession(rng: random.Random) -> str:
    """대문자+숫자 또는 숫자만, 6~16자."""
    length = rng.randint(ACCESSION_MIN_LEN, ACCESSION_MAX_LEN)
    if rng.random() < 0.5:
        return "".join(rng.choices(string.digits, k=length))
    # 숫자만인 경우와 구분되도록 영문자·숫자가 최소 하나씩 들어갈 때까지 다시 뽑는다
    while True:
        value = "".join(rng.choices(string.ascii_uppercase + string.digits, k=length))
        if any(c.isalpha() for c in value) and any(c.isdigit() for c in value):
            return value


def random_name(rng: random.Random, sex: str) -> str:
    """DICOM PN 형식 'LAST^FIRST' 대문자. 성별에 맞는 이름을 고른다."""
    if sex == "M":
        pool = MALE_FIRST
    elif sex == "F":
        pool = FEMALE_FIRST
    else:
        pool = MALE_FIRST + FEMALE_FIRST
    return f"{rng.choice(LAST_NAMES).upper()}^{rng.choice(pool).upper()}"


def unique_values(rng: random.Random, count: int, make) -> list[str]:
    """make(rng)로 서로 겹치지 않는 값을 count개 만든다(한 번 실행 범위)."""
    values: set[str] = set()
    result: list[str] = []
    while len(result) < count:
        value = make(rng)
        if value not in values:
            values.add(value)
            result.append(value)
    return result


def random_patient_ids(rng: random.Random, count: int) -> list[str]:
    return [f"{n:08d}" for n in rng.sample(range(10 ** 8), count)]


def balanced_choices(rng: random.Random, options: list[str], count: int) -> list[str]:
    """options를 최대한 같은 비율로 count개 배분한 뒤 섞는다."""
    values = [options[i % len(options)] for i in range(count)]
    rng.shuffle(values)
    return values


def calc_age(birth: str, study: str) -> str | None:
    """만 나이를 AS 형식 '000Y'로. 계산할 수 없거나 음수면 None."""
    b, s = parse_date(birth), parse_date(study)
    if b is None or s is None:
        return None
    age = s.year - b.year - ((s.month, s.day) < (b.month, b.day))
    if age < 0 or age > 999:
        return None
    return f"{age:03d}Y"


def normalize_time(value: str) -> str:
    """TM 값에서 hhmmss 6자리만 남긴다(소수 초 제거)."""
    digits = re.sub(r"\D", "", str(value).split(".")[0])
    return digits[:6]


def build_image_comments(drid: str, preg: str, study_date: str, study_time: str) -> str:
    preg_value = "" if preg == "Null" else preg
    vsid = f"{study_date}{normalize_time(study_time)}"
    return f"CAUERRCT/1;DRID={drid};KTAS=3;PREG={preg_value};VSID={vsid};"
