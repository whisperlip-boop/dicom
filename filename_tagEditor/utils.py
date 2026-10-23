"""Pure helper logic for the Filename-to-DICOM-Tag Editor.

This module holds every side-effect-free helper used by the GUI and the
worker thread: filename tokenizing, token value transforms, DICOM tag
parsing and DICOM file discovery. Keeping the rules here makes them
verifiable without a running Qt event loop.
"""

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

from pydicom.datadict import dictionary_VR, dictionary_description
from pydicom.tag import Tag
from pydicom.valuerep import DA, DT, TM

# Transform identifiers. The GUI shows these strings verbatim in the
# per-token combo box, so they double as the rule names.
TRANSFORM_ASIS = "As-is"
TRANSFORM_UPPER = "UPPERCASE"
TRANSFORM_LOWER = "lowercase"
TRANSFORM_AGE = "Age -> AS (017Y)"
TRANSFORM_SEX = "Sex -> M/F/O"
TRANSFORM_DATE = "Date -> DA (YYYYMMDD)"

TRANSFORM_OPTIONS = [
    TRANSFORM_ASIS,
    TRANSFORM_UPPER,
    TRANSFORM_LOWER,
    TRANSFORM_AGE,
    TRANSFORM_SEX,
    TRANSFORM_DATE,
]

AGE_UNITS = ["Y", "M", "W", "D"]

# Input date layouts offered per token; values are strptime patterns.
DATE_FORMATS: dict[str, str] = {
    "YYYYMMDD": "%Y%m%d",
    "YYYY-MM-DD": "%Y-%m-%d",
    "YYYY/MM/DD": "%Y/%m/%d",
    "YYYY.MM.DD": "%Y.%m.%d",
    "YYMMDD": "%y%m%d",
    "DDMMYYYY": "%d%m%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "MMDDYYYY": "%m%d%Y",
    "MM-DD-YYYY": "%m-%d-%Y",
}

# Accepted spellings for PatientSex (0010,0040); DICOM allows only M/F/O.
SEX_MAP: dict[str, str] = {
    "M": "M", "MALE": "M", "MAN": "M", "BOY": "M", "1": "M",
    "F": "F", "FEMALE": "F", "WOMAN": "F", "GIRL": "F", "2": "F",
    "O": "O", "OTHER": "O", "U": "O", "UNKNOWN": "O", "0": "O",
}

# Extensions treated as DICOM without reading the file header.
DICOM_SUFFIXES = {".dcm", ".dicom", ".dic", ".ima", ".img"}

_AGE_PATTERN = re.compile(r"^(\d{1,3})\s*([YMWDymwd])?$")
_TAG_PATTERN = re.compile(r"^([0-9A-Fa-f]{4})[,\s]?([0-9A-Fa-f]{4})$")

LOG_FILE_NAME = "filename_tag_editor.log"


@dataclass(frozen=True)
class FieldMapping:
    """One filename token mapped onto one DICOM tag.

    Attributes:
        index: Zero-based token position inside the split filename.
        group: DICOM tag group number.
        element: DICOM tag element number.
        transform: One of the TRANSFORM_* constants.
        age_unit: Suffix used by TRANSFORM_AGE when the token has no unit.
        date_format: Key of DATE_FORMATS used by TRANSFORM_DATE.
    """

    index: int
    group: int
    element: int
    transform: str = TRANSFORM_ASIS
    age_unit: str = "Y"
    date_format: str = "YYYYMMDD"

    @property
    def tag(self) -> Tag:
        """Return the pydicom Tag addressed by this mapping."""
        return Tag(self.group, self.element)


def get_resource_path(relative_path: str) -> str:
    """Get the absolute path to a bundled resource.

    Args:
        relative_path: Path relative to the application root.

    Returns:
        Absolute path valid both in source checkouts and PyInstaller bundles.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def get_log_path() -> Path:
    """Return the audit log path, creating its parent directory.

    The log lives under the user profile so the frozen executable keeps
    working when started from a read-only location such as a network share.

    Returns:
        Absolute path of the rotating log file.
    """
    log_dir = Path.home() / ".filename_tag_editor"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / LOG_FILE_NAME


def setup_logging() -> Path:
    """Configure the root logger with a rotating file handler.

    Returns:
        Path of the log file that was attached.
    """
    log_path = get_log_path()
    handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Re-running setup must not stack duplicate handlers.
    for existing in list(root.handlers):
        if isinstance(existing, RotatingFileHandler):
            root.removeHandler(existing)
            existing.close()
    root.addHandler(handler)
    return log_path


def parse_tag(text: str) -> tuple[int, int]:
    """Parse a user-entered DICOM tag into group and element numbers.

    Accepts "0010,0020", "00100020", "(0010,0020)" and "0010 0020".

    Args:
        text: Raw text from a tag input field.

    Returns:
        Tuple of (group, element) as integers.

    Raises:
        ValueError: If the text is not a valid 8 hex digit DICOM tag.
    """
    cleaned = text.strip().strip("()").replace("0x", "").replace("0X", "")
    match = _TAG_PATTERN.match(cleaned)
    if not match:
        raise ValueError(
            f"Invalid tag '{text}'. Use the format gggg,eeee (e.g. 0010,0020)."
        )
    return int(match.group(1), 16), int(match.group(2), 16)


def format_tag(group: int, element: int) -> str:
    """Format a tag pair as the canonical gggg,eeee string.

    Args:
        group: Tag group number.
        element: Tag element number.

    Returns:
        Upper-case hexadecimal tag string.
    """
    return f"{group:04X},{element:04X}"


def describe_tag(group: int, element: int) -> str:
    """Return the DICOM dictionary name of a tag.

    Args:
        group: Tag group number.
        element: Tag element number.

    Returns:
        Human readable tag name, or "Private/Unknown" when not in the
        standard dictionary.
    """
    try:
        name = dictionary_description(Tag(group, element))
    except (KeyError, ValueError):
        return "Private/Unknown"
    return name or "Private/Unknown"


def resolve_vr(group: int, element: int) -> str:
    """Look up the DICOM dictionary VR for a tag.

    Args:
        group: Tag group number.
        element: Tag element number.

    Returns:
        Two-letter VR string.

    Raises:
        ValueError: If the tag is absent from the DICOM dictionary, which
            means a new element cannot be created with a trustworthy VR.
    """
    try:
        return dictionary_VR(Tag(group, element))
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"VR for tag ({format_tag(group, element)}) is unknown; "
            "the tag must already exist in the file."
        ) from exc


def split_filename(stem: str, delimiter: str) -> list[str]:
    """Split a filename stem into tokens.

    Args:
        stem: Filename without directory and without extension.
        delimiter: Delimiter string; an empty string means "do not split".

    Returns:
        List of whitespace-stripped tokens. Empty tokens produced by
        repeated delimiters are kept so positional indices stay aligned
        with the visible filename.
    """
    if not delimiter:
        return [stem.strip()]
    return [token.strip() for token in stem.split(delimiter)]


def to_age_string(value: str, default_unit: str) -> str:
    """Convert a numeric token into a DICOM Age String (VR AS).

    "17" becomes "017Y". A unit written inside the token wins over
    default_unit so filenames such as "3M" stay correct.

    Args:
        value: Raw token value.
        default_unit: Unit applied when the token carries none (Y, M, W, D).

    Returns:
        Four character age string such as "017Y".

    Raises:
        ValueError: If the token is not 1-3 digits with an optional unit,
            or the unit is not one of Y, M, W, D.
    """
    match = _AGE_PATTERN.match(value.strip())
    if not match:
        raise ValueError(
            f"Age token '{value}' must be 1-3 digits with an optional Y/M/W/D suffix."
        )
    unit = (match.group(2) or default_unit).upper()
    if unit not in AGE_UNITS:
        raise ValueError(f"Invalid age unit '{unit}'. Use Y, M, W or D.")
    return f"{int(match.group(1)):03d}{unit}"


def to_sex_string(value: str) -> str:
    """Normalise a token into the DICOM PatientSex vocabulary.

    Args:
        value: Raw token value such as "male" or "F".

    Returns:
        "M", "F" or "O".

    Raises:
        ValueError: If the token cannot be mapped.
    """
    key = value.strip().upper()
    if key not in SEX_MAP:
        raise ValueError(f"Sex token '{value}' cannot be mapped to M, F or O.")
    return SEX_MAP[key]


def to_date_string(value: str, date_format: str) -> str:
    """Convert a token into a DICOM Date string (VR DA).

    Args:
        value: Raw token value.
        date_format: Key of DATE_FORMATS describing the token layout.

    Returns:
        Date formatted as YYYYMMDD.

    Raises:
        ValueError: If the format key is unknown or the token does not match.
    """
    pattern = DATE_FORMATS.get(date_format)
    if pattern is None:
        raise ValueError(f"Unknown date format '{date_format}'.")
    try:
        parsed = datetime.strptime(value.strip(), pattern)
    except ValueError as exc:
        raise ValueError(
            f"Date token '{value}' does not match '{date_format}'."
        ) from exc
    return parsed.strftime("%Y%m%d")


def apply_transform(value: str, mapping: FieldMapping) -> str:
    """Apply a mapping's transform rule to a raw filename token.

    Args:
        value: Raw token value.
        mapping: Mapping carrying the transform and its options.

    Returns:
        Transformed string ready to be stored in the DICOM element.

    Raises:
        ValueError: If the token does not satisfy the transform rule, or
            the transform name is unknown.
    """
    if mapping.transform == TRANSFORM_ASIS:
        return value.strip()
    if mapping.transform == TRANSFORM_UPPER:
        return value.strip().upper()
    if mapping.transform == TRANSFORM_LOWER:
        return value.strip().lower()
    if mapping.transform == TRANSFORM_AGE:
        return to_age_string(value, mapping.age_unit)
    if mapping.transform == TRANSFORM_SEX:
        return to_sex_string(value)
    if mapping.transform == TRANSFORM_DATE:
        return to_date_string(value, mapping.date_format)
    raise ValueError(f"Unknown transform '{mapping.transform}'.")


def convert_value_for_vr(vr: str, value: str) -> object:
    """Convert a string into the Python type expected by a DICOM VR.

    Args:
        vr: Two-letter DICOM value representation.
        value: Already transformed string value.

    Returns:
        Value converted to the type pydicom stores for that VR.

    Raises:
        ValueError: If the value cannot be converted, or the VR is a
            sequence, which cannot be produced from a filename token.
    """
    text = value.strip()
    if vr in ("DS", "FL", "FD"):
        return float(text)
    if vr in ("IS", "SL", "SS", "UL", "US"):
        return int(text)
    if vr == "DA":
        return DA(text)
    if vr == "DT":
        return DT(text)
    if vr == "TM":
        return TM(text)
    if vr == "SQ":
        raise ValueError("SQ tags cannot be filled from a filename token.")
    return text


def is_dicom_file(path: Path) -> bool:
    """Decide whether a file should be treated as DICOM.

    Files with a known DICOM extension are accepted directly; any other
    file is accepted only when it carries the DICM magic at offset 128, so
    extension-less DICOM exports still work without misreading unrelated
    files.

    Args:
        path: File to inspect.

    Returns:
        True if the file looks like DICOM.
    """
    if path.suffix.lower() in DICOM_SUFFIXES:
        return True
    try:
        with path.open("rb") as handle:
            handle.seek(128)
            return handle.read(4) == b"DICM"
    except OSError:
        return False


def scan_dicom_files(root: Path, recursive: bool) -> list[Path]:
    """Collect DICOM files under a folder.

    Args:
        root: Folder to scan.
        recursive: Whether sub-folders are included.

    Returns:
        Sorted list of DICOM file paths.

    Raises:
        ValueError: If the folder does not exist.
    """
    if not root.is_dir():
        raise ValueError(f"Source folder does not exist: {root}")
    candidates: Iterator[Path] = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in candidates if p.is_file() and is_dicom_file(p)]
    return sorted(files)


def check_output_overlap(source: Path, output: Path) -> str:
    """Report how an output folder relates to the source folder.

    Args:
        source: Source folder.
        output: Output folder.

    Returns:
        "same" when both point at one folder, "nested" when the output sits
        inside the source, otherwise an empty string.
    """
    try:
        source_abs = source.resolve()
        output_abs = output.resolve()
    except OSError:
        return ""
    if source_abs == output_abs:
        return "same"
    if source_abs in output_abs.parents:
        return "nested"
    return ""


def resolve_output_path(source_root: Path, output_root: Path, file_path: Path) -> Path:
    """Map a source file onto its destination inside the output folder.

    The relative sub-folder layout of the source tree is preserved, and the
    result is verified to stay inside output_root so no path can escape the
    chosen destination.

    Args:
        source_root: Folder that was scanned.
        output_root: Folder chosen for the copies.
        file_path: File found under source_root.

    Returns:
        Absolute destination path.

    Raises:
        ValueError: If the file is not under source_root, or the
            destination would fall outside output_root.
    """
    source_abs = source_root.resolve()
    output_abs = output_root.resolve()
    try:
        relative = file_path.resolve().relative_to(source_abs)
    except ValueError as exc:
        raise ValueError(f"File {file_path} is not located under {source_root}.") from exc
    destination = (output_abs / relative).resolve()
    if output_abs != destination and output_abs not in destination.parents:
        raise ValueError(f"Refusing to write outside the output folder: {destination}")
    return destination
