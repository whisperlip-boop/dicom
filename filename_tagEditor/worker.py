"""Background worker that writes filename tokens into DICOM tags."""

import logging
import os
from pathlib import Path

import pydicom
from PySide6.QtCore import QThread, Signal
from pydicom.dataelem import DataElement

from utils import (
    FieldMapping,
    apply_transform,
    convert_value_for_vr,
    format_tag,
    resolve_output_path,
    resolve_vr,
    split_filename,
)

logger = logging.getLogger(__name__)

# Character set used when a token carries non-ASCII text; UTF-8 is the only
# single-value charset that can represent arbitrary filename characters.
UTF8_CHARSET = "ISO_IR 192"


class DicomTagWorker(QThread):
    """Applies filename-derived tag edits to a list of DICOM files.

    Signals:
        progress: (processed count, total count) after every file.
        log: (level, message) where level is "info", "warning" or "error".
        finished_summary: (updated, skipped, failed) once the run ends.
    """

    progress = Signal(int, int)
    log = Signal(str, str)
    finished_summary = Signal(int, int, int)

    def __init__(
        self,
        files: list[Path],
        source_root: Path,
        mappings: list[FieldMapping],
        delimiter: str,
        overwrite: bool,
        output_root: Path | None,
        dry_run: bool = False,
        parent: object | None = None,
    ) -> None:
        """Initialise the worker.

        Args:
            files: DICOM files to process.
            source_root: Folder that was scanned, used to rebuild the tree.
            mappings: Token-to-tag rules to apply.
            delimiter: Filename delimiter; empty means the whole stem.
            overwrite: True to edit files in place, False to copy to output.
            output_root: Destination folder when overwrite is False.
            dry_run: True to validate every file without writing anything.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._files = files
        self._source_root = source_root
        self._mappings = mappings
        self._delimiter = delimiter
        self._overwrite = overwrite
        self._output_root = output_root
        self._dry_run = dry_run
        self._cancelled = False

    def cancel(self) -> None:
        """Request a graceful stop after the current file."""
        self._cancelled = True

    def run(self) -> None:
        """Process every file, emitting progress and log messages."""
        total = len(self._files)
        updated = 0
        skipped = 0
        failed = 0

        required_tokens = max((m.index for m in self._mappings), default=-1) + 1
        logger.info(
            "Run start: %d files, delimiter=%r, mode=%s, mappings=%s",
            total,
            self._delimiter,
            "dry-run" if self._dry_run else ("overwrite" if self._overwrite else "copy"),
            [(m.index, format_tag(m.group, m.element), m.transform) for m in self._mappings],
        )

        for processed, file_path in enumerate(self._files, start=1):
            if self._cancelled:
                self.log.emit("warning", "Cancelled by user.")
                logger.warning("Run cancelled after %d files.", processed - 1)
                break

            outcome = self._process_file(file_path, required_tokens)
            if outcome == "updated":
                updated += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1
            self.progress.emit(processed, total)

        logger.info(
            "Run finished: updated=%d skipped=%d failed=%d", updated, skipped, failed
        )
        self.finished_summary.emit(updated, skipped, failed)

    def _process_file(self, file_path: Path, required_tokens: int) -> str:
        """Apply the mappings to one file.

        Args:
            file_path: DICOM file to edit.
            required_tokens: Minimum token count the mappings need.

        Returns:
            "updated", "skipped" or "failed".
        """
        name = file_path.name
        tokens = split_filename(file_path.stem, self._delimiter)

        # A token shortage means the mapping cannot be trusted for this
        # file, so nothing is written at all.
        if len(tokens) < required_tokens:
            message = (
                f"SKIP {name}: filename has {len(tokens)} token(s) but the mapping "
                f"needs {required_tokens}."
            )
            self.log.emit("warning", message)
            logger.warning(message)
            return "skipped"

        try:
            dataset = pydicom.dcmread(str(file_path))
        except Exception as exc:  # pydicom raises a wide range of errors
            message = f"FAIL {name}: cannot read DICOM ({exc})."
            self.log.emit("error", message)
            logger.error(message)
            return "failed"

        try:
            applied = self._apply_mappings(dataset, tokens, name)
        except ValueError as exc:
            message = f"FAIL {name}: {exc}"
            self.log.emit("error", message)
            logger.error(message)
            return "failed"

        # Dry run stops here: every value was transformed and validated
        # against its VR, but nothing is written to disk.
        if self._dry_run:
            message = f"DRY {name}: {', '.join(applied)}"
            self.log.emit("info", message)
            logger.info("DRY %s | %s", file_path, ", ".join(applied))
            return "updated"

        try:
            destination = self._destination_for(file_path)
        except ValueError as exc:
            message = f"FAIL {name}: {exc}"
            self.log.emit("error", message)
            logger.error(message)
            return "failed"

        try:
            self._save_dataset(dataset, destination)
        except Exception as exc:  # OSError and pydicom write errors
            message = f"FAIL {name}: cannot save to {destination} ({exc})."
            self.log.emit("error", message)
            logger.error(message)
            return "failed"

        summary = ", ".join(applied)
        message = f"OK {name}: {summary}"
        self.log.emit("info", message)
        logger.info("%s -> %s | %s", file_path, destination, summary)
        return "updated"

    def _apply_mappings(
        self, dataset: pydicom.Dataset, tokens: list[str], name: str
    ) -> list[str]:
        """Write every mapped token into the dataset.

        Args:
            dataset: Dataset loaded from disk.
            tokens: Tokens split from the filename.
            name: Filename used in messages.

        Returns:
            Human readable list of the edits that were applied.

        Raises:
            ValueError: If any single mapping cannot be applied; the whole
                file is then left untouched.
        """
        applied: list[str] = []
        needs_utf8 = False

        for mapping in self._mappings:
            raw = tokens[mapping.index]
            tag_text = format_tag(mapping.group, mapping.element)
            try:
                value = apply_transform(raw, mapping)
            except ValueError as exc:
                raise ValueError(f"token {mapping.index + 1} ({tag_text}): {exc}") from exc

            if mapping.tag in dataset:
                vr = dataset[mapping.tag].VR
            else:
                vr = resolve_vr(mapping.group, mapping.element)

            try:
                converted = convert_value_for_vr(vr, value)
            except ValueError as exc:
                raise ValueError(
                    f"token {mapping.index + 1} ({tag_text}) value '{value}' "
                    f"is invalid for VR {vr}: {exc}"
                ) from exc

            if mapping.tag in dataset:
                dataset[mapping.tag].value = converted
            else:
                dataset.add(DataElement(mapping.tag, vr, converted))

            if not value.isascii():
                needs_utf8 = True
            applied.append(f"({tag_text})='{value}'")

        if needs_utf8:
            current = dataset.get("SpecificCharacterSet", None)
            if current != UTF8_CHARSET:
                dataset.SpecificCharacterSet = UTF8_CHARSET
                warning = (
                    f"NOTE {name}: non-ASCII value written, SpecificCharacterSet "
                    f"set to {UTF8_CHARSET}."
                )
                self.log.emit("warning", warning)
                logger.warning(warning)

        return applied

    def _destination_for(self, file_path: Path) -> Path:
        """Resolve where the edited dataset must be written.

        Args:
            file_path: Source file.

        Returns:
            Destination path, creating parent folders in copy mode.

        Raises:
            ValueError: If the destination cannot be resolved safely.
        """
        if self._overwrite or self._output_root is None:
            return file_path
        destination = resolve_output_path(self._source_root, self._output_root, file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def _save_dataset(self, dataset: pydicom.Dataset, destination: Path) -> None:
        """Write the dataset through a temporary file, then move it in place.

        The temporary file keeps a crash or a write error from leaving a
        truncated DICOM behind, which matters most in overwrite mode.

        Args:
            dataset: Dataset to write.
            destination: Final path of the file.

        Raises:
            OSError: If writing or replacing the file fails.
        """
        temp_path = destination.with_name(destination.name + ".tmp_fte")
        try:
            dataset.save_as(str(temp_path))
            os.replace(temp_path, destination)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    logger.warning("Could not remove temporary file %s", temp_path)
