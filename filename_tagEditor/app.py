"""GUI application that fills DICOM tags from DICOM file names.

The user picks a source folder, a filename delimiter and a tag for each
token position; every DICOM file under the folder is then updated so the
chosen tags carry the values taken from its file name.
"""

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mapping_table import MappingTable
from panels import LogPanel, OutputPanel, ParsingPanel, SourcePanel
from utils import (
    TRANSFORM_ASIS,
    FieldMapping,
    check_output_overlap,
    describe_tag,
    format_tag,
    get_resource_path,
    scan_dicom_files,
    setup_logging,
    split_filename,
)
from worker import DicomTagWorker

logger = logging.getLogger(__name__)


class FilenameTagEditor(QWidget):
    """Main window of the filename-to-tag editor."""

    def __init__(self, log_path: Path) -> None:
        """Build the window and wire the panels together.

        Args:
            log_path: Path of the audit log, reported in the log pane.
        """
        super().__init__()
        self._log_path = log_path
        self._files: list[Path] = []
        self._worker: DicomTagWorker | None = None

        self.setWindowTitle("DICOM Filename Tag Editor")
        self.setMinimumSize(940, 780)
        self.setAcceptDrops(True)

        self.source_panel = SourcePanel()
        self.parsing_panel = ParsingPanel()
        self.table = MappingTable(self._sample_tokens)
        self.output_panel = OutputPanel()
        self.log_panel = LogPanel()

        self.source_panel.scanRequested.connect(self._scan_files)
        self.parsing_panel.delimiterChanged.connect(self._on_tokens_changed)
        self.parsing_panel.sampleChanged.connect(self._on_tokens_changed)
        self.parsing_panel.addRowRequested.connect(
            lambda: self.table.set_row_count(len(self.table.row_widgets) + 1)
        )
        self.parsing_panel.removeRowRequested.connect(
            lambda: self.table.set_row_count(len(self.table.row_widgets) - 1)
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.source_panel)
        layout.addWidget(self.parsing_panel)
        layout.addWidget(self._build_mapping_group(), stretch=1)
        layout.addWidget(self.output_panel)
        layout.addLayout(self._build_action_row())
        layout.addWidget(self.log_panel, stretch=1)

        self.log_panel.append("info", f"Log file: {log_path}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_mapping_group(self) -> QGroupBox:
        """Wrap the mapping table in its titled group box.

        Returns:
            Group box holding the mapping table.
        """
        group = QGroupBox("3. Token to tag mapping (leave Tag empty to skip a token)")
        box = QVBoxLayout(group)
        box.addWidget(self.table)
        return group

    def _build_action_row(self) -> QHBoxLayout:
        """Create the run/cancel row with the progress bar.

        Returns:
            Layout holding the run controls.
        """
        row = QHBoxLayout()
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self._run)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.status_label = QLabel("Ready.")

        row.addWidget(self.run_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.progress_bar, stretch=1)
        row.addWidget(self.status_label)
        return row

    # ------------------------------------------------------------------
    # Token handling
    # ------------------------------------------------------------------
    def _sample_tokens(self) -> list[str]:
        """Split the sample file name with the current delimiter.

        Returns:
            Token list, empty when no sample name is set.
        """
        sample = self.parsing_panel.sample
        if not sample:
            return []
        return split_filename(Path(sample).stem, self.parsing_panel.delimiter)

    def _on_tokens_changed(self) -> None:
        """Re-split the sample name and refresh the mapping preview."""
        tokens = self._sample_tokens()
        if tokens and len(tokens) > len(self.table.row_widgets):
            self.table.set_row_count(len(tokens))
        else:
            self.table.refresh_preview()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def _scan_files(self) -> None:
        """Scan the source folder and prime the preview."""
        source = self.source_panel.folder
        if not source:
            QMessageBox.warning(self, "Source required", "Select a source folder first.")
            return
        try:
            files = scan_dicom_files(Path(source), self.source_panel.recursive)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Scan failed", str(exc))
            logger.error("Scan failed for %s: %s", source, exc)
            return

        self._files = files
        self.source_panel.set_found(len(files))
        self.log_panel.append("info", f"Scanned {source}: {len(files)} DICOM file(s).")
        logger.info(
            "Scanned %s (recursive=%s): %d files",
            source,
            self.source_panel.recursive,
            len(files),
        )

        self.parsing_panel.set_sample_choices([f.name for f in files])
        if not files:
            QMessageBox.information(
                self, "No files", "No DICOM files were found in that folder."
            )
            return

        self._report_token_counts(files)
        self._on_tokens_changed()

    def _report_token_counts(self, files: list[Path]) -> None:
        """Log how many tokens the scanned file names produce.

        Args:
            files: Scanned DICOM files.
        """
        delimiter = self.parsing_panel.delimiter
        counts: dict[int, int] = {}
        for path in files:
            count = len(split_filename(path.stem, delimiter))
            counts[count] = counts.get(count, 0) + 1
        summary = ", ".join(
            f"{tokens} token(s): {number} file(s)"
            for tokens, number in sorted(counts.items())
        )
        level = "info" if len(counts) == 1 else "warning"
        self.log_panel.append(
            level, f"Token counts with delimiter '{delimiter}' -> {summary}"
        )
        if len(counts) > 1:
            self.log_panel.append(
                "warning",
                "File names do not all split into the same number of tokens; "
                "files with too few tokens will be skipped.",
            )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def _run(self) -> None:
        """Validate the settings, confirm, then start the worker."""
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._files:
            QMessageBox.warning(self, "No files", "Scan a source folder first.")
            return

        try:
            mappings = self.table.collect_mappings()
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid mapping", str(exc))
            return
        if not mappings:
            QMessageBox.warning(self, "No mapping", "Enter at least one tag to write.")
            return

        source_root = Path(self.source_panel.folder)
        overwrite = self.output_panel.overwrite
        dry_run = self.output_panel.dry_run

        output_root: Path | None = None
        if not overwrite:
            output_root = self._validated_output_root(source_root)
            if output_root is None:
                return

        if not self._confirm_run(len(self._files), mappings, overwrite, dry_run):
            return

        self._start_worker(source_root, mappings, overwrite, output_root, dry_run)

    def _validated_output_root(self, source_root: Path) -> Path | None:
        """Check the output folder and create it when needed.

        Args:
            source_root: Folder that was scanned.

        Returns:
            The output folder, or None when the run must not start.
        """
        output_text = self.output_panel.folder
        if not output_text:
            QMessageBox.warning(self, "Output required", "Select an output folder.")
            return None

        output_root = Path(output_text)
        overlap = check_output_overlap(source_root, output_root)
        if overlap == "same":
            QMessageBox.critical(
                self,
                "Invalid output folder",
                "The output folder is the same as the source folder. "
                "Choose a different folder or use overwrite mode.",
            )
            return None
        if overlap == "nested":
            answer = QMessageBox.question(
                self,
                "Output inside source",
                "The output folder is inside the source folder. The copies "
                "will appear in the source tree. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None

        try:
            output_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self, "Output folder", f"Cannot create the output folder: {exc}"
            )
            return None
        return output_root

    def _confirm_run(
        self,
        file_count: int,
        mappings: list[FieldMapping],
        overwrite: bool,
        dry_run: bool,
    ) -> bool:
        """Show the final confirmation dialog.

        Args:
            file_count: Number of files that will be processed.
            mappings: Mappings that will be applied.
            overwrite: Whether originals are edited in place.
            dry_run: Whether nothing will be written.

        Returns:
            True if the user confirmed.
        """
        lines = [
            f"Token {m.index + 1} -> ({format_tag(m.group, m.element)}) "
            f"{describe_tag(m.group, m.element)}"
            + ("" if m.transform == TRANSFORM_ASIS else f"  [{m.transform}]")
            for m in mappings
        ]
        if dry_run:
            mode = "DRY RUN - no file will be written."
        elif overwrite:
            mode = "OVERWRITE - the original files will be modified in place."
        else:
            mode = "COPY - edited files are written to the output folder."

        answer = QMessageBox.question(
            self,
            "Confirm run",
            f"{file_count} file(s) will be processed.\n\n{mode}\n\n"
            + "\n".join(lines)
            + "\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _start_worker(
        self,
        source_root: Path,
        mappings: list[FieldMapping],
        overwrite: bool,
        output_root: Path | None,
        dry_run: bool,
    ) -> None:
        """Launch the background worker and lock the inputs.

        Args:
            source_root: Scanned folder.
            mappings: Mappings to apply.
            overwrite: Whether to edit in place.
            output_root: Destination folder in copy mode.
            dry_run: Whether to validate only.
        """
        self.progress_bar.setMaximum(len(self._files))
        self.progress_bar.setValue(0)
        self.status_label.setText("Working...")
        self._set_inputs_enabled(False)

        self._worker = DicomTagWorker(
            files=list(self._files),
            source_root=source_root,
            mappings=mappings,
            delimiter=self.parsing_panel.delimiter,
            overwrite=overwrite,
            output_root=output_root,
            dry_run=dry_run,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self.log_panel.append)
        self._worker.finished_summary.connect(self._on_finished)
        self._worker.start()

    def _cancel(self) -> None:
        """Ask the worker to stop after the current file."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.status_label.setText("Cancelling...")

    def _on_progress(self, processed: int, total: int) -> None:
        """Update the progress bar.

        Args:
            processed: Files handled so far.
            total: Total file count.
        """
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(processed)
        self.status_label.setText(f"{processed}/{total}")

    def _on_finished(self, updated: int, skipped: int, failed: int) -> None:
        """Report the run summary and unlock the inputs.

        Args:
            updated: Files written, or validated in dry-run mode.
            skipped: Files skipped because of a token shortage.
            failed: Files that could not be read, converted or written.
        """
        self._set_inputs_enabled(True)
        self.status_label.setText("Done.")
        summary = f"Finished. updated={updated}, skipped={skipped}, failed={failed}"
        self.log_panel.append("info", summary)
        icon = QMessageBox.Icon.Information if failed == 0 else QMessageBox.Icon.Warning
        box = QMessageBox(icon, "Run finished", summary, parent=self)
        box.setInformativeText(f"Details were written to {self._log_path}")
        box.exec()
        self._worker = None

    def _set_inputs_enabled(self, enabled: bool) -> None:
        """Enable or disable every input while the worker runs.

        Args:
            enabled: True to unlock the inputs.
        """
        self.source_panel.set_inputs_enabled(enabled)
        self.parsing_panel.set_inputs_enabled(enabled)
        self.output_panel.set_inputs_enabled(enabled)
        self.table.setEnabled(enabled)
        self.run_button.setEnabled(enabled)
        self.cancel_button.setEnabled(not enabled)

    # ------------------------------------------------------------------
    # Drag and drop
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept dragged folders and files.

        Args:
            event: Incoming drag event.
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        """Use a dropped folder as the source, or a file's parent folder.

        Args:
            event: Drop event carrying local paths.
        """
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                self.source_panel.folder = str(path)
                event.acceptProposedAction()
                return
            if path.is_file():
                self.source_panel.folder = str(path.parent)
                self.parsing_panel.set_sample(path.name)
                event.acceptProposedAction()
                return

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop a running worker before the window closes.

        Args:
            event: Qt close event.
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
        event.accept()


def main() -> None:
    """Start the Qt application."""
    log_path = setup_logging()
    logger.info("Application started.")
    app = QApplication(sys.argv)
    icon_path = get_resource_path("editor.ico")
    if Path(icon_path).exists():
        app.setWindowIcon(QIcon(icon_path))
    window = FilenameTagEditor(log_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
