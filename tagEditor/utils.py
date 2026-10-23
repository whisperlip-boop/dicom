import os
import sys
from typing import Optional

from pydicom.dataelem import DataElement
from pydicom.tag import Tag
from pydicom.valuerep import DA, DT, TM

VR_OPTIONS = [
    "Same", "AE", "AS", "AT", "CS", "DA", "DS", "DT", "FL", "FD", "IS", "LO", "LT",
    "OB", "OD", "OF", "OW", "PN", "SH", "SL", "SQ", "SS", "ST", "TM", "UI",
    "UL", "UN", "US", "UT"
]

VR_LABELS = {
    "AE": "Application Entity",
    "AS": "Age String",
    "AT": "Attribute Tag",
    "CS": "Code String",
    "DA": "Date",
    "DS": "Decimal String",
    "DT": "Date Time",
    "FL": "Floating Point Single",
    "FD": "Floating Point Double",
    "IS": "Integer String",
    "LO": "Long String",
    "LT": "Long Text",
    "OB": "Other Byte",
    "OD": "Other Double",
    "OF": "Other Float",
    "OW": "Other Word",
    "PN": "Person Name",
    "SH": "Short String",
    "SL": "Signed Long",
    "SQ": "Sequence of Items",
    "SS": "Signed Short",
    "ST": "Short Text",
    "TM": "Time",
    "UI": "Unique Identifier",
    "UL": "Unsigned Long",
    "UN": "Unknown",
    "US": "Unsigned Short",
    "UT": "Unlimited Text",
    "Same": "Use existing same VR"
}


def get_resource_path(relative_path: str) -> str:
    """Get the absolute path to a resource, supporting PyInstaller bundle."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def validate_hex_input(P: str) -> bool:
    """Validate that a proposed input is a valid hex string up to 4 characters."""
    p = P.strip()
    return len(p) <= 4 and all(c in "0123456789ABCDEFabcdef" for c in p)


def get_missing_required_paths(source_folder: str, source_file: str, output_folder: str) -> list[str]:
    """Return a list of required path fields that were left empty."""
    missing = []
    if not output_folder:
        missing.append("Output Folder")
    if not source_folder and not source_file:
        missing.append("Source Folder")
        missing.append("Source File")
    return missing


def is_output_overlapping(source_folder: str, source_file: str, output_folder: str) -> str | bool:
    """Check whether the output folder overlaps the source location."""
    if not output_folder:
        return False
    output_abs = os.path.abspath(output_folder)

    if source_folder:
        source_abs = os.path.abspath(source_folder)
        if output_abs == source_abs:
            return "same"
        try:
            if os.path.commonpath([output_abs, source_abs]) == source_abs:
                return "nested"
        except ValueError:
            pass

    if source_file:
        source_dir = os.path.abspath(os.path.dirname(source_file))
        if output_abs == source_dir:
            return "same"

    return False


def convert_value_for_vr(vr: str, value: str) -> object:
    """Convert a string to the appropriate Python type for the given DICOM VR."""
    def convert_single(single_value: str) -> object:
        single_value = single_value.strip()
        if vr in ("DS", "FL", "FD"):
            return float(single_value)
        if vr in ("IS", "SL", "SS", "UL", "US"):
            return int(single_value)
        if vr == "DA":
            return DA(single_value)
        if vr == "DT":
            return DT(single_value)
        if vr == "TM":
            return TM(single_value)
        if vr == "AT":
            tag_str = single_value.replace(",", "").replace(" ", "")
            if len(tag_str) != 8 or not all(c in "0123456789ABCDEFabcdef" for c in tag_str):
                raise ValueError("AT VR requires tag format GGGGEEEE or GGGG,EEEE")
            return Tag(int(tag_str[:4], 16), int(tag_str[4:], 16))
        if vr == "SQ":
            raise ValueError("SQ VR requires sequence input")
        return single_value

    if "\\" in value:
        return [convert_single(v) for v in value.split("\\")]
    return convert_single(value)
