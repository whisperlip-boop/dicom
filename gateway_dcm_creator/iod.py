"""SOP Class(IOD) 규격 맞춤: 영상 형식 호환성 검사, 영상 변환, 필수 태그 보정.

허용 태그 목록(iod_tags.json)은 tools/gen_iod_tags.py로 DICOM 표준에서 생성한다.
"""
import json

import numpy as np
from pydicom.dataset import Dataset
from pydicom.pixels import apply_color_lut
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid
from pydicom.valuerep import DSfloat

from paths import get_resource_path
from rules import SOP_CLASS_BY_MODALITY

MONOCHROME = ("MONOCHROME1", "MONOCHROME2")
# VL Photographic Image(XC)에서 허용하는 Photometric Interpretation
XC_PHOTOMETRIC = ("MONOCHROME2", "RGB", "YBR_FULL_422", "YBR_PARTIAL_420", "YBR_ICT", "YBR_RCT")
LOSSY_TRANSFER_SYNTAXES = {
    "1.2.840.10008.1.2.4.50", "1.2.840.10008.1.2.4.51",   # JPEG Baseline / Extended
    "1.2.840.10008.1.2.4.81",                            # JPEG-LS Near-Lossless
    "1.2.840.10008.1.2.4.91", "1.2.840.10008.1.2.4.203",  # JPEG 2000 / HTJ2K
    "1.2.840.10008.1.2.4.100", "1.2.840.10008.1.2.4.101", "1.2.840.10008.1.2.4.102",
    "1.2.840.10008.1.2.4.103", "1.2.840.10008.1.2.4.104", "1.2.840.10008.1.2.4.105",
    "1.2.840.10008.1.2.4.106",                           # MPEG 계열
}

LOSSY_METHODS = {
    "1.2.840.10008.1.2.4.50": "ISO_10918_1", "1.2.840.10008.1.2.4.51": "ISO_10918_1",
    "1.2.840.10008.1.2.4.81": "ISO_14495_1",
    "1.2.840.10008.1.2.4.91": "ISO_15444_1", "1.2.840.10008.1.2.4.203": "ISO_15444_15",
}

# 흉부 X-ray 샘플 기준 기본값
BODY_PART = "CHEST"
VIEW_POSITION = "PA"
PATIENT_ORIENTATION = ["L", "F"]  # PA 흉부: 행 방향 → 환자 왼쪽, 열 방향 → 발쪽

def _load_iod_tags() -> dict[str, tuple[set[int], set[tuple[int, int]]]]:
    """IOD별 (허용 태그, 반복 그룹 태그). 반복 그룹은 '60xx0010'처럼 표기되며 (그룹 상위 바이트, element)로 둔다."""
    with open(get_resource_path("iod_tags.json"), encoding="utf-8") as f:
        iods = json.load(f)["iods"]
    result = {}
    for modality, tags in iods.items():
        exact = {int(t, 16) for t in tags if "x" not in t.lower()}
        repeating = {(int(t[:2], 16), int(t[4:], 16)) for t in tags if t[2:4].lower() == "xx"}
        result[modality] = (exact, repeating)
    return result


_IOD_TAGS = _load_iod_tags()


class IncompatibleImage(Exception):
    """영상 형식이 대상 IOD와 맞지 않는다(메시지에 환자정보를 넣지 않는다)."""


def _int_tag(ds: Dataset, keyword: str, default: int) -> int:
    """빈 값·숫자가 아닌 값이면 default. 헤더 하나가 깨져도 int(None)으로 배치 전체가 서지 않게 한다."""
    value = ds.get(keyword, default)
    try:
        return default if value is None or value == "" else int(value)
    except (TypeError, ValueError):
        return default


def _pixel_info(ds: Dataset) -> tuple[int, str, int, int, int, int]:
    return (
        _int_tag(ds, "SamplesPerPixel", 1),
        str(ds.get("PhotometricInterpretation", "")),
        _int_tag(ds, "BitsAllocated", 0),
        _int_tag(ds, "BitsStored", 0),
        _int_tag(ds, "PixelRepresentation", 0),
        _int_tag(ds, "NumberOfFrames", 1) or 1,
    )


def incompatibility(modality: str, ds: Dataset) -> str | None:
    """영상 형식이 대상 IOD와 맞지 않는 이유. 맞으면 None. 헤더만 읽은 Dataset으로도 판단한다."""
    spp, pi, ba, bs, pr, frames = _pixel_info(ds)
    if frames > 1:
        return "멀티프레임 미지원"
    if modality == "CR":
        if spp != 1 or pi not in MONOCHROME:
            return f"CR은 흑백만 허용 (현재 {pi})"
    elif modality == "DX":
        if spp != 1 or pi not in MONOCHROME:
            return f"DX는 흑백만 허용 (현재 {pi})"
        if ba not in (8, 16) or not 6 <= bs <= 16 or pr != 0:
            return f"DX는 8/16bit unsigned만 허용 (현재 {ba}bit, signed={pr})"
    elif modality == "XC":
        if pi not in XC_PHOTOMETRIC:
            return f"XC는 {pi} 불가"
        if ba != 8 or bs != 8 or pr != 0:
            return f"XC는 8bit unsigned만 허용 (현재 {bs}bit)"
    return None


def can_convert(ds: Dataset) -> bool:
    """영상 변환으로 해결할 수 있는 경우인지(멀티프레임은 불가)."""
    return (_int_tag(ds, "NumberOfFrames", 1) or 1) <= 1


def set_lossy_info(ds: Dataset) -> None:
    """압축된 Pixel Data가 lossy면 Lossy Image Compression Ratio/Method를 채운다(이미 있으면 유지)."""
    transfer_syntax = str(ds.file_meta.get("TransferSyntaxUID", ""))
    if transfer_syntax not in LOSSY_TRANSFER_SYNTAXES or "PixelData" not in ds:
        return
    ds.LossyImageCompression = "01"
    if "LossyImageCompressionRatio" not in ds:
        raw_size = int(ds.Rows) * int(ds.Columns) * int(ds.get("SamplesPerPixel", 1))             * int(ds.BitsAllocated) // 8 * int(ds.get("NumberOfFrames", 1) or 1)
        ds.LossyImageCompressionRatio = f"{raw_size / max(len(ds.PixelData), 1):.2f}"
    if "LossyImageCompressionMethod" not in ds and transfer_syntax in LOSSY_METHODS:
        ds.LossyImageCompressionMethod = LOSSY_METHODS[transfer_syntax]


def fix_ds_lengths(ds: Dataset) -> None:
    """최상위 DS 값 중 16자를 넘는 것을 16자 이내로 다시 쓴다(예: 0.14300000000000002)."""
    for elem in ds:
        if elem.VR != "DS" or elem.is_empty:
            continue
        values = elem.value if elem.VM > 1 else [elem.value]
        if any(len(str(v)) > 16 for v in values):
            fixed = [DSfloat(float(v), auto_format=True) for v in values]
            elem.value = fixed if elem.VM > 1 else fixed[0]


def _rescale(arr: np.ndarray, dtype) -> np.ndarray:
    """최소~최대 값을 dtype 전체 범위로 선형 변환한다."""
    arr = arr.astype(np.float64)
    lo, hi = arr.min(), arr.max()
    if hi <= lo:
        return np.zeros(arr.shape, dtype=dtype)
    return np.round((arr - lo) / (hi - lo) * np.iinfo(dtype).max).astype(dtype)


def convert_pixels(ds: Dataset, modality: str) -> None:
    """대상 IOD에 맞게 영상 데이터를 바꾼다. 압축 원본은 비압축으로 저장된다."""
    was_lossy = (
        str(ds.file_meta.get("TransferSyntaxUID", "")) in LOSSY_TRANSFER_SYNTAXES
        or ds.get("LossyImageCompression") == "01"
    )
    set_lossy_info(ds)  # 비압축으로 바뀌기 전에 원래 압축률을 기록
    arr = ds.pixel_array  # pydicom은 YBR을 RGB로 바꿔서 돌려준다
    spp, pi, _, bs, pr, _ = _pixel_info(ds)
    if pi == "PALETTE COLOR":
        arr = apply_color_lut(arr, ds)
        arr = arr if arr.dtype == np.uint8 else _rescale(arr, np.uint8)
        pi, bs, pr = "RGB", 8, 0
    is_color = arr.ndim == 3 and arr.shape[-1] == 3
    if is_color and pi.startswith("YBR"):
        pi = "RGB"

    if modality in ("CR", "DX"):
        if is_color:
            gray = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
            arr = np.round(gray).astype(arr.dtype)
            pi = "MONOCHROME2"
        if pr == 1 or arr.dtype.kind == "i":
            # int64로 두고 아래에서 범위를 확인한다. 바로 uint16으로 바꾸면 16bit 초과 값이 wrap된다
            arr = arr.astype(np.int64) - int(arr.min())
        if arr.dtype == np.uint8:
            bits = 8
        else:
            # 32bit 원본은 그대로 자르면 값이 넘치므로 16bit 범위로 다시 맞춘다
            arr = arr.astype(np.uint16) if int(arr.max()) <= 0xFFFF else _rescale(arr, np.uint16)
            bits = max(int(arr.max()).bit_length(), 8, min(bs or 16, 16))
    elif modality == "XC":
        if pi == "MONOCHROME1":
            arr = arr.max() - arr
            pi = "MONOCHROME2"
        if arr.dtype != np.uint8 or bs != 8:
            arr = _rescale(arr, np.uint8)
        if pi not in XC_PHOTOMETRIC:
            pi = "RGB" if arr.ndim == 3 else "MONOCHROME2"
        bits = 8
    else:
        return

    ds.set_pixel_data(arr, pi, bits, generate_instance_uid=False)
    # 원래 영상 값 기준이던 태그는 더 이상 맞지 않으므로 지운다
    for keyword in ("SmallestImagePixelValue", "LargestImagePixelValue", "WindowCenter", "WindowWidth",
                    "WindowCenterWidthExplanation", "VOILUTSequence", "ModalityLUTSequence",
                    "RescaleIntercept", "RescaleSlope", "RescaleType"):
        if keyword in ds:
            delattr(ds, keyword)
    if was_lossy:
        ds.LossyImageCompression = "01"


def _is_missing(ds: Dataset, keyword: str) -> bool:
    return keyword not in ds or ds.data_element(keyword).is_empty


def _set_if_missing(ds: Dataset, keyword: str, value: object) -> None:
    """Type 1 태그: 없거나 비어 있으면 value로 채운다."""
    if _is_missing(ds, keyword):
        setattr(ds, keyword, value)


def _add_if_missing(ds: Dataset, keyword: str, value="") -> None:
    """Type 2 태그: 없을 때만 (빈 값으로) 만든다."""
    if keyword not in ds:
        setattr(ds, keyword, value)


def _window_from_pixels(ds: Dataset) -> tuple[float, float]:
    try:
        arr = ds.pixel_array
        lo, hi = float(arr.min()), float(arr.max())
    except Exception:
        bits = int(ds.get("BitsStored", 8))
        lo, hi = 0.0, float(2 ** bits - 1)
    width = max(hi - lo + 1, 1)
    return round((lo + hi) / 2, 2), round(width, 2)


def _chest_code() -> Dataset:
    item = Dataset()
    item.CodeValue = "51185008"
    item.CodingSchemeDesignator = "SCT"
    item.CodeMeaning = "Chest"
    return item


def apply_iod(ds: Dataset, modality: str) -> None:
    """SOP Class를 modality에 맞추고 해당 IOD의 필수 태그를 채운 뒤, IOD에 없는 표준 태그를 지운다."""
    sop_class = SOP_CLASS_BY_MODALITY[modality]
    ds.Modality = modality
    ds.SOPClassUID = sop_class
    ds.file_meta.MediaStorageSOPClassUID = sop_class

    # 공통 Type 1 / Type 2
    for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"):
        if _is_missing(ds, keyword):  # 필요할 때만 생성(인자로 넘기면 매 파일마다 UID를 만들고 버린다)
            setattr(ds, keyword, generate_uid())
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    for keyword in ("PatientName", "PatientID", "PatientBirthDate", "PatientSex", "StudyDate", "StudyTime",
                    "ReferringPhysicianName", "StudyID", "AccessionNumber", "Manufacturer"):
        _add_if_missing(ds, keyword)
    _add_if_missing(ds, "SeriesNumber", "1")
    _add_if_missing(ds, "InstanceNumber", "1")
    _add_if_missing(ds, "PatientOrientation")
    set_lossy_info(ds)
    _add_if_missing(ds, "LossyImageCompression", "00")
    fix_ds_lengths(ds)

    if modality == "CR":
        _set_if_missing(ds, "BodyPartExamined", BODY_PART)
        _set_if_missing(ds, "ViewPosition", VIEW_POSITION)

    elif modality == "DX":
        ds.PresentationIntentType = "FOR PRESENTATION"
        ds.ImageType = ["ORIGINAL", "PRIMARY"]
        _set_if_missing(ds, "BodyPartExamined", BODY_PART)
        _set_if_missing(ds, "ViewPosition", VIEW_POSITION)
        _set_if_missing(ds, "ImageLaterality", "U")
        if "AnatomicRegionSequence" not in ds:
            ds.AnatomicRegionSequence = Sequence([_chest_code()])
        _add_if_missing(ds, "DetectorType")
        _add_if_missing(ds, "PositionerType")
        _set_if_missing(ds, "PatientOrientation", PATIENT_ORIENTATION)
        if "AcquisitionContextSequence" not in ds:
            ds.AcquisitionContextSequence = Sequence()
        ds.PixelIntensityRelationship = "LIN"
        ds.PixelIntensityRelationshipSign = 1
        ds.RescaleIntercept = "0"
        ds.RescaleSlope = "1"
        ds.RescaleType = "US"
        is_mono1 = ds.get("PhotometricInterpretation") == "MONOCHROME1"
        ds.PresentationLUTShape = "INVERSE" if is_mono1 else "IDENTITY"
        _set_if_missing(ds, "BurnedInAnnotation", "NO")
        if "ImagerPixelSpacing" not in ds:
            spacing = ds.get("PixelSpacing")  # 태그는 있는데 값이 비어 있으면(None) 기본값으로
            ds.ImagerPixelSpacing = list(spacing) if spacing else ["0.1", "0.1"]
        if "WindowCenter" not in ds or "WindowWidth" not in ds:
            ds.WindowCenter, ds.WindowWidth = _window_from_pixels(ds)
        # DX 영상은 BitsStored 바로 아래 비트가 High Bit여야 한다
        ds.HighBit = int(ds.BitsStored) - 1

    elif modality == "XC":
        _set_if_missing(ds, "ImageType", ["ORIGINAL", "PRIMARY"])
        _set_if_missing(ds, "ContentDate", ds.get("StudyDate", ""))
        _set_if_missing(ds, "ContentTime", ds.get("StudyTime", ""))
        if "AcquisitionContextSequence" not in ds:
            ds.AcquisitionContextSequence = Sequence()

    elif modality == "OT":
        _set_if_missing(ds, "ConversionType", "WSD")

    remove_unexpected(ds, modality)


def remove_unexpected(ds: Dataset, modality: str) -> None:
    """대상 IOD에 정의되지 않은 표준(public) 태그를 지운다. 사설 태그는 남긴다.

    Group Length(gggg,0000)는 태그를 넣고 빼면 값이 틀어지고 pydicom이 다시 계산하지 않으므로
    file meta(0002)를 제외하고 모두 지운다(DICOM 표준에서도 retired).
    """
    exact, repeating = _IOD_TAGS[modality]
    for elem in list(ds):
        tag = elem.tag
        if tag.group == 0x0002:
            continue
        if tag.element == 0x0000:
            del ds[tag]
        elif tag.is_private:
            continue
        elif int(tag) not in exact and (tag.group >> 8, tag.element) not in repeating:
            del ds[tag]
