"""결과 DICOM에서 검증 대상 태그를 추출하고 원본과의 페어 여부를 판별."""
from collections.abc import Callable
from typing import Any

import pydicom
from pydicom.tag import Tag, BaseTag

from evaluator import TagStatusEvaluator


def get_tag_str(ds: pydicom.Dataset, tag: BaseTag) -> str:
    """Dataset에서 태그 값을 문자열로 반환한다. 태그가 없으면 빈 문자열."""
    if tag in ds:
        return str(ds[tag].value).strip()
    return ""


def is_result_file(ds: pydicom.Dataset) -> bool:
    """(0008,0070)=='VUNO' 또는 Private Creator (1001,0010)=='VUNOMED'이면 결과 파일로 판별한다."""
    return (get_tag_str(ds, Tag(0x0008, 0x0070)) == "VUNO"
            or get_tag_str(ds, Tag(0x1001, 0x0010)) == "VUNOMED")


def verify_pair(original_ds: pydicom.Dataset | None, result_ds: pydicom.Dataset | None) -> bool:
    """원본과 결과 파일이 같은 Study에 속하는지 검증한다.

    (0020,000D) Study Instance UID → (0010,0020) Patient ID 순으로 시도한다.
    두 값 모두 없으면 False를 반환한다.

    Args:
        original_ds: 원본 DICOM Dataset. 미로딩 시 None.
        result_ds: 결과 DICOM Dataset. 미로딩 시 None.

    Returns:
        두 파일의 페어 키가 일치하면 True, 그 외 False.
    """
    if original_ds is None or result_ds is None:
        return False
    for tag in (Tag(0x0020, 0x000D), Tag(0x0010, 0x0020)):
        orig = get_tag_str(original_ds, tag)
        result = get_tag_str(result_ds, tag)
        if orig and result:
            return orig == result
    return False


def sorted_tags(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """태그 행을 (GroupID, ElementID) 오름차순으로 정렬해 반환한다."""
    return sorted(tags, key=lambda x: (int(x["group"], 16), int(x["element"], 16)))


def extract_tags(
    ds: pydicom.Dataset, original_ds: pydicom.Dataset | None = None
) -> list[dict[str, Any]]:
    """Dataset에서 검증 대상 태그를 추출하고 규칙 준수 여부를 평가한다.

    일반 태그와 Private Tag를 순서대로 처리하며,
    동일 태그 ID에 복수 값이 있으면 각각 별도 행으로 추가한다.
    original_ds가 제공되면 원본 비교가 필요한 태그((0008,0090), (0020,0010), (0008,1030))도 검증한다.
    (1001,1015)는 최상위와 (1001,1010) Findings Sequence 각 item을 모두 검증한다.

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
    instance_num_str = str(ds[Tag(0x0020, 0x0013)].value).strip() if (0x0020, 0x0013) in ds else ""
    original_sop_uid = (
        str(original_ds[Tag(0x0008, 0x0018)].value).strip()
        if original_ds is not None and (0x0008, 0x0018) in original_ds else ""
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
        (Tag(0x0008, 0x1030), "Study Description"),
    ]:
        try:
            val = str(ds[tag].value).strip() if tag in ds else ""
            ok, status = TagStatusEvaluator.evaluate_same_as_original(val, original_ds, tag)
            add(tag, desc, val, ok, status)
        except Exception as e:
            add(tag, desc, "ERROR", False, f"Fail (read error: {e})")

    tag_map = [
        (Tag(0x0020, 0x000E), "Series Instance UID",
         lambda v: TagStatusEvaluator.evaluate_series_uid(v, original_series_uid, series_num_str)),
        (Tag(0x0008, 0x0021), "Series Date", TagStatusEvaluator.evaluate_date),
        (Tag(0x0020, 0x0011), "Series Number", TagStatusEvaluator.evaluate_series_number),
        (Tag(0x0008, 0x0070), "Manufacturer", lambda v: TagStatusEvaluator.evaluate_fixed_value(v, "VUNO")),
        (Tag(0x0008, 0x1090), "Manufacturer's Model Name", TagStatusEvaluator.evaluate_model_name),
        (Tag(0x0018, 0x1020), "Software Versions", TagStatusEvaluator.evaluate_version),
        (Tag(0x0008, 0x0012), "Instance Creation Date", TagStatusEvaluator.evaluate_date),
        (Tag(0x0008, 0x0013), "Instance Creation Time", TagStatusEvaluator.evaluate_time),
        (Tag(0x0008, 0x0018), "SOP Instance UID",
         lambda v: TagStatusEvaluator.evaluate_sop_instance_uid(
             v, series_num_str, instance_num_str, original_sop_uid)),
        (Tag(0x0020, 0x0013), "Instance Number",
         lambda v: TagStatusEvaluator.evaluate_instance_number(v, series_num_str, original_instance)),
        (Tag(0x0020, 0x4000), "Image Comments", lambda v: (bool(v), "OK" if v else "Fail (empty)")),
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
    ]
    # 부재 자체가 의미를 갖는 태그: 빈 값으로 평가 함수를 호출해 Skip/Fail을 판정
    evaluate_when_absent = {Tag(0x1001, 0x1005), Tag(0x1001, 0x1011)}

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

    _add_finding_json_rows(ds, model, add)
    return tags


def _add_finding_json_rows(
    ds: pydicom.Dataset, model: str, add: Callable[[BaseTag, str, str, bool, str], None]
) -> None:
    """(1001,1015)를 최상위와 (1001,1010) Findings Sequence 각 item에서 검증해 행으로 추가한다.

    SC는 최상위와 item 양쪽에, GSPS·SC Report·PDF는 item에만 기록되므로
    item이 있으면 최상위 부재는 Skip으로 판정한다.
    """
    json_tag = Tag(0x1001, 0x1015)
    seq_tag = Tag(0x1001, 0x1010)
    desc = "Private JSON"

    def to_str(value: Any) -> str:
        return value.decode(errors="ignore") if isinstance(value, bytes) else str(value)

    items: list[pydicom.Dataset] = []
    try:
        if seq_tag in ds and isinstance(ds[seq_tag].value, pydicom.sequence.Sequence):
            items = list(ds[seq_tag].value)
    except Exception as e:
        add(seq_tag, "Findings Sequence", "ERROR", False, f"Fail (read error: {e})")

    try:
        if json_tag in ds:
            val = to_str(ds[json_tag].value)
            ok, status = TagStatusEvaluator.evaluate_json(val, model)
        elif items:
            val, ok, status = "", True, "Skip (in Findings Sequence items)"
        else:
            val = ""
            ok, status = TagStatusEvaluator.evaluate_json(val, model)
        add(json_tag, desc, val, ok, status)
    except Exception as e:
        add(json_tag, desc, "ERROR", False, f"Fail (read error: {e})")

    for i, item in enumerate(items, 1):
        item_desc = f"{desc} (1001,1010 item {i})"
        try:
            val = to_str(item[json_tag].value) if json_tag in item else ""
            ok, status = TagStatusEvaluator.evaluate_json(val, model)
            add(json_tag, item_desc, val, ok, status)
        except Exception as e:
            add(json_tag, item_desc, "ERROR", False, f"Fail (read error: {e})")
