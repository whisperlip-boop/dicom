"""Group-box panels used by the filename tag editor window.

Each panel owns one section of the window and exposes the few properties
and signals the main window needs, so the window itself only wires panels
together instead of holding every widget.
"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

# Number of scanned files offered in the sample combo box; enough to spot
# irregular names without turning the combo into a file browser.
SAMPLE_LIMIT = 200

LOG_PREFIXES = {"info": "", "warning": "[WARN] ", "error": "[ERROR] "}


class SourcePanel(QGroupBox):
    """Source folder, recursion switch and scan trigger.

    Signals:
        scanRequested: Emitted when the user presses Scan Files.
    """

    scanRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the source panel.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__("1. Source", parent)
        grid = QGridLayout(self)

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            "Folder that contains the DICOM files (drag and drop is supported)"
        )
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)

        self.recursive_check = QCheckBox("Include subfolders")
        self.recursive_check.setChecked(True)

        self.scan_button = QPushButton("Scan Files")
        self.scan_button.clicked.connect(self.scanRequested.emit)

        self.found_label = QLabel("No scan yet.")

        grid.addWidget(QLabel("Source Folder"), 0, 0)
        grid.addWidget(self.folder_edit, 0, 1)
        grid.addWidget(browse, 0, 2)
        grid.addWidget(self.recursive_check, 1, 1)
        grid.addWidget(self.scan_button, 1, 2)
        grid.addWidget(self.found_label, 2, 1, 1, 2)
        grid.setColumnStretch(1, 1)

    @property
    def folder(self) -> str:
        """Return the trimmed source folder text."""
        return self.folder_edit.text().strip()

    @folder.setter
    def folder(self, value: str) -> None:
        """Set the source folder text.

        Args:
            value: Folder path to show.
        """
        self.folder_edit.setText(value)

    @property
    def recursive(self) -> bool:
        """Return whether sub-folders are included."""
        return self.recursive_check.isChecked()

    def set_found(self, count: int) -> None:
        """Show how many DICOM files the last scan found.

        Args:
            count: Number of files found.
        """
        self.found_label.setText(f"Found {count} DICOM file(s).")

    def set_inputs_enabled(self, enabled: bool) -> None:
        """Enable or disable every input of this panel.

        Args:
            enabled: True to unlock the inputs.
        """
        self.folder_edit.setEnabled(enabled)
        self.recursive_check.setEnabled(enabled)
        self.scan_button.setEnabled(enabled)

    def _browse(self) -> None:
        """Ask for the source folder."""
        folder = QFileDialog.getExistingDirectory(self, "Select source folder")
        if folder:
            self.folder_edit.setText(folder)


class ParsingPanel(QGroupBox):
    """Delimiter, sample file name and row count controls.

    Signals:
        delimiterChanged: Emitted after the delimiter text changed.
        sampleChanged: Emitted after the sample file name changed.
        addRowRequested: Emitted when one more token row is wanted.
        removeRowRequested: Emitted when the last token row is dropped.
    """

    delimiterChanged = Signal()
    sampleChanged = Signal()
    addRowRequested = Signal()
    removeRowRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the parsing panel.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__("2. Filename parsing", parent)
        grid = QGridLayout(self)

        self.delimiter_edit = QLineEdit("_")
        self.delimiter_edit.setMaximumWidth(80)
        self.delimiter_edit.setToolTip(
            "Delimiter used to split the file name, e.g. _ or - or __ .\n"
            "Leave empty to use the whole file name as a single token."
        )
        self.delimiter_edit.textChanged.connect(lambda _text: self.delimiterChanged.emit())

        self.sample_combo = QComboBox()
        self.sample_combo.setEditable(True)
        self.sample_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.sample_combo.setToolTip(
            "File name used for the preview. Pick a scanned file or type one."
        )
        self.sample_combo.currentTextChanged.connect(lambda _text: self.sampleChanged.emit())

        add_row = QPushButton("Add Row")
        add_row.clicked.connect(self.addRowRequested.emit)
        remove_row = QPushButton("Remove Row")
        remove_row.clicked.connect(self.removeRowRequested.emit)

        grid.addWidget(QLabel("Delimiter"), 0, 0)
        grid.addWidget(self.delimiter_edit, 0, 1)
        grid.addWidget(QLabel("Sample file name"), 0, 2)
        grid.addWidget(self.sample_combo, 0, 3)
        grid.addWidget(add_row, 0, 4)
        grid.addWidget(remove_row, 0, 5)
        grid.setColumnStretch(3, 1)

    @property
    def delimiter(self) -> str:
        """Return the delimiter exactly as typed, without trimming."""
        return self.delimiter_edit.text()

    @property
    def sample(self) -> str:
        """Return the trimmed sample file name."""
        return self.sample_combo.currentText().strip()

    def set_sample(self, name: str) -> None:
        """Show a file name in the sample combo box.

        Args:
            name: File name to preview.
        """
        self.sample_combo.setCurrentText(name)

    def set_sample_choices(self, names: list[str]) -> None:
        """Replace the sample choices without triggering a preview rebuild.

        Args:
            names: Scanned file names; only the first SAMPLE_LIMIT are kept.
        """
        self.sample_combo.blockSignals(True)
        self.sample_combo.clear()
        self.sample_combo.addItems(names[:SAMPLE_LIMIT])
        self.sample_combo.blockSignals(False)

    def set_inputs_enabled(self, enabled: bool) -> None:
        """Enable or disable every input of this panel.

        Args:
            enabled: True to unlock the inputs.
        """
        self.delimiter_edit.setEnabled(enabled)
        self.sample_combo.setEnabled(enabled)


class OutputPanel(QGroupBox):
    """Destination mode, output folder and dry-run switch.

    Signals:
        modeChanged: Emitted when the copy/overwrite choice changed.
    """

    modeChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the output panel.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__("4. Output", parent)
        grid = QGridLayout(self)

        self.copy_radio = QRadioButton("Save to output folder (keep originals)")
        self.copy_radio.setChecked(True)
        self.copy_radio.toggled.connect(lambda _checked: self._on_mode_changed())
        self.overwrite_radio = QRadioButton("Overwrite original files")

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Destination folder for the edited copies")
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse)

        self.dry_run_check = QCheckBox("Dry run (validate only, write nothing)")

        grid.addWidget(self.copy_radio, 0, 0, 1, 2)
        grid.addWidget(self.overwrite_radio, 0, 2)
        grid.addWidget(QLabel("Output Folder"), 1, 0)
        grid.addWidget(self.folder_edit, 1, 1)
        grid.addWidget(self.browse_button, 1, 2)
        grid.addWidget(self.dry_run_check, 2, 1, 1, 2)
        grid.setColumnStretch(1, 1)

        self.set_inputs_enabled(True)

    @property
    def overwrite(self) -> bool:
        """Return whether the originals are edited in place."""
        return self.overwrite_radio.isChecked()

    @property
    def dry_run(self) -> bool:
        """Return whether the run only validates without writing."""
        return self.dry_run_check.isChecked()

    @property
    def folder(self) -> str:
        """Return the trimmed output folder text."""
        return self.folder_edit.text().strip()

    def set_inputs_enabled(self, enabled: bool) -> None:
        """Enable or disable every input of this panel.

        The output folder widgets stay disabled in overwrite mode, where a
        destination folder has no meaning.

        Args:
            enabled: True to unlock the inputs.
        """
        self.copy_radio.setEnabled(enabled)
        self.overwrite_radio.setEnabled(enabled)
        self.dry_run_check.setEnabled(enabled)
        copy_mode = self.copy_radio.isChecked()
        self.folder_edit.setEnabled(enabled and copy_mode)
        self.browse_button.setEnabled(enabled and copy_mode)

    def _on_mode_changed(self) -> None:
        """Keep the output folder widgets in step with the chosen mode."""
        self.set_inputs_enabled(self.copy_radio.isEnabled())
        self.modeChanged.emit()

    def _browse(self) -> None:
        """Ask for the output folder."""
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.folder_edit.setText(folder)


class LogPanel(QGroupBox):
    """Read-only log pane with clear and save actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the log panel.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__("5. Log", parent)
        box = QVBoxLayout(self)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        # Bound the buffer so a folder with many files cannot grow the pane
        # without limit.
        self.log_view.setMaximumBlockCount(5000)

        buttons = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log_view.clear)
        save = QPushButton("Save Log...")
        save.clicked.connect(self._save)
        buttons.addStretch(1)
        buttons.addWidget(clear)
        buttons.addWidget(save)

        box.addWidget(self.log_view)
        box.addLayout(buttons)

    def append(self, level: str, message: str) -> None:
        """Add one line to the log pane.

        Args:
            level: "info", "warning" or "error".
            message: Text to show.
        """
        self.log_view.appendPlainText(f"{LOG_PREFIXES.get(level, '')}{message}")

    def _save(self) -> None:
        """Write the log pane content to a text file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", "filename_tag_editor_run.log", "Log files (*.log *.txt)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.log_view.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Cannot write the log: {exc}")
            return
        self.append("info", f"Log saved to {path}")
