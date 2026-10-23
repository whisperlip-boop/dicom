"""Token-to-tag mapping table widget.

The table owns one row per filename token: the raw sample value, the target
DICOM tag, the transform rule and a live preview of the resulting value.
"""

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from utils import (
    AGE_UNITS,
    DATE_FORMATS,
    TRANSFORM_AGE,
    TRANSFORM_DATE,
    TRANSFORM_OPTIONS,
    FieldMapping,
    apply_transform,
    describe_tag,
    format_tag,
    parse_tag,
)

COL_INDEX = 0
COL_SAMPLE = 1
COL_TAG = 2
COL_NAME = 3
COL_TRANSFORM = 4
COL_OPTION = 5
COL_RESULT = 6

COLUMN_LABELS = [
    "#",
    "Sample token",
    "Tag (gggg,eeee)",
    "Tag name",
    "Transform",
    "Option",
    "Result",
]

DEFAULT_ROWS = 4


class NoWheelComboBox(QComboBox):
    """Combo box that never changes its value on a mouse wheel turn.

    Inside a scrollable table a wheel turn over a plain QComboBox silently
    selects another item instead of scrolling the table, which would change
    a transform rule without the user noticing. The event is forwarded to
    the surrounding view instead, so the rows scroll as expected. Clicks,
    keyboard input and the open drop-down list keep working normally.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the combo box with wheel focus disabled.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        # WheelFocus is the QComboBox default and would hand focus to the
        # widget the pointer merely passes over.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Scroll the surrounding view instead of changing the value.

        Qt does not propagate an ignored wheel event up to the scroll area
        on its own, so the event is handed to that view explicitly.

        Args:
            event: Incoming wheel event.
        """
        event.ignore()
        scroll_area = self._scroll_area()
        if scroll_area is not None:
            QApplication.sendEvent(scroll_area.viewport(), event)

    def _scroll_area(self) -> QAbstractScrollArea | None:
        """Find the scroll area this combo box is embedded in.

        Returns:
            The nearest QAbstractScrollArea ancestor, or None when the
            combo box is not inside one.
        """
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QAbstractScrollArea):
                return widget
            widget = widget.parentWidget()
        return None


@dataclass
class RowWidgets:
    """Editable widgets that belong to one mapping row.

    Attributes:
        tag_edit: Tag input; empty means the token is skipped.
        transform_combo: Transform rule selector.
        option_combo: Age unit or date format selector.
    """

    tag_edit: QLineEdit
    transform_combo: NoWheelComboBox
    option_combo: NoWheelComboBox


class MappingTable(QTableWidget):
    """Table that maps filename token positions onto DICOM tags."""

    def __init__(
        self,
        tokens_provider: Callable[[], list[str]],
        parent: QWidget | None = None,
    ) -> None:
        """Build an empty mapping table.

        Args:
            tokens_provider: Callable returning the tokens of the current
                sample file name; used to keep the preview in sync when the
                user edits a row.
            parent: Optional Qt parent widget.
        """
        super().__init__(0, len(COLUMN_LABELS), parent)
        self._tokens_provider = tokens_provider
        self._rows: list[RowWidgets] = []
        self._refreshing = False

        self.setHorizontalHeaderLabels(COLUMN_LABELS)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        header = self.horizontalHeader()
        stretched = {COL_SAMPLE, COL_NAME, COL_RESULT}
        for column in range(len(COLUMN_LABELS)):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if column in stretched
                else QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(column, mode)

        self.set_row_count(DEFAULT_ROWS)

    # ------------------------------------------------------------------
    # Row management
    # ------------------------------------------------------------------
    @property
    def row_widgets(self) -> list[RowWidgets]:
        """Return the per-row editor widgets, in token order."""
        return self._rows

    def set_row_count(self, count: int) -> None:
        """Grow or shrink the table, keeping the settings of kept rows.

        Args:
            count: Desired number of token rows; at least one row is kept.
        """
        count = max(1, count)
        while len(self._rows) > count:
            self.removeRow(len(self._rows) - 1)
            self._rows.pop()
        while len(self._rows) < count:
            self._add_row()
        self.refresh_preview()

    def _add_row(self) -> None:
        """Append one mapping row with its editor widgets."""
        row = self.rowCount()
        self.insertRow(row)

        index_item = QTableWidgetItem(str(row + 1))
        index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setItem(row, COL_INDEX, index_item)
        for column in (COL_SAMPLE, COL_NAME, COL_RESULT):
            self.setItem(row, column, QTableWidgetItem(""))

        tag_edit = QLineEdit()
        tag_edit.setPlaceholderText("skip")
        tag_edit.setMaximumWidth(130)
        tag_edit.textChanged.connect(lambda _text: self.refresh_preview())
        self.setCellWidget(row, COL_TAG, tag_edit)

        transform_combo = NoWheelComboBox()
        transform_combo.addItems(TRANSFORM_OPTIONS)
        transform_combo.currentTextChanged.connect(
            lambda _text: self._on_transform_changed()
        )
        self.setCellWidget(row, COL_TRANSFORM, transform_combo)

        option_combo = NoWheelComboBox()
        option_combo.setEnabled(False)
        option_combo.currentTextChanged.connect(lambda _text: self.refresh_preview())
        self.setCellWidget(row, COL_OPTION, option_combo)

        self._rows.append(RowWidgets(tag_edit, transform_combo, option_combo))

    def _cell(self, row: int, column: int) -> QTableWidgetItem:
        """Return the item of a read-only cell, creating it when missing.

        Args:
            row: Row index.
            column: Column index.

        Returns:
            The existing or newly attached table item.
        """
        item = self.item(row, column)
        if item is None:
            item = QTableWidgetItem("")
            self.setItem(row, column, item)
        return item

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def _on_transform_changed(self) -> None:
        """Repopulate the option combo boxes, then refresh the preview."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            for widgets in self._rows:
                transform = widgets.transform_combo.currentText()
                combo = widgets.option_combo
                if transform == TRANSFORM_AGE:
                    options = AGE_UNITS
                elif transform == TRANSFORM_DATE:
                    options = list(DATE_FORMATS)
                else:
                    options = []
                current = combo.currentText()
                # Rebuild only on a real change so the user's pick survives
                # edits made in other rows.
                if [combo.itemText(i) for i in range(combo.count())] != options:
                    combo.clear()
                    combo.addItems(options)
                    if current in options:
                        combo.setCurrentText(current)
                combo.setEnabled(bool(options))
        finally:
            self._refreshing = False
        self.refresh_preview()

    def refresh_preview(self) -> None:
        """Update the sample, tag name and result columns of every row."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            tokens = self._tokens_provider()
            for row, widgets in enumerate(self._rows):
                token = tokens[row] if row < len(tokens) else ""
                self._cell(row, COL_SAMPLE).setText(token)

                tag_text = widgets.tag_edit.text().strip()
                if not tag_text:
                    self._cell(row, COL_NAME).setText("(skipped)")
                    self._cell(row, COL_RESULT).setText("")
                    continue
                try:
                    group, element = parse_tag(tag_text)
                except ValueError:
                    self._cell(row, COL_NAME).setText("invalid tag")
                    self._cell(row, COL_RESULT).setText("")
                    continue

                self._cell(row, COL_NAME).setText(describe_tag(group, element))
                if row >= len(tokens):
                    self._cell(row, COL_RESULT).setText("no token")
                    continue

                mapping = self.mapping_for(row, group, element)
                try:
                    self._cell(row, COL_RESULT).setText(apply_transform(token, mapping))
                except ValueError as exc:
                    self._cell(row, COL_RESULT).setText(f"error: {exc}")
        finally:
            self._refreshing = False

    # ------------------------------------------------------------------
    # Mapping extraction
    # ------------------------------------------------------------------
    def mapping_for(self, row: int, group: int, element: int) -> FieldMapping:
        """Build the mapping described by one row.

        Args:
            row: Row index, which is also the token index.
            group: Parsed tag group.
            element: Parsed tag element.

        Returns:
            Mapping carrying the row's transform options.
        """
        widgets = self._rows[row]
        transform = widgets.transform_combo.currentText()
        option = widgets.option_combo.currentText()
        return FieldMapping(
            index=row,
            group=group,
            element=element,
            transform=transform,
            age_unit=option if transform == TRANSFORM_AGE else "Y",
            date_format=option if transform == TRANSFORM_DATE else "YYYYMMDD",
        )

    def collect_mappings(self) -> list[FieldMapping]:
        """Collect every filled-in row as a mapping.

        Returns:
            List of mappings in token order.

        Raises:
            ValueError: If a tag is malformed or two rows target one tag.
        """
        mappings: list[FieldMapping] = []
        seen: dict[tuple[int, int], int] = {}
        for row, widgets in enumerate(self._rows):
            tag_text = widgets.tag_edit.text().strip()
            if not tag_text:
                continue
            try:
                group, element = parse_tag(tag_text)
            except ValueError as exc:
                raise ValueError(f"Row {row + 1}: {exc}") from exc
            if (group, element) in seen:
                raise ValueError(
                    f"Row {row + 1} and row {seen[(group, element)] + 1} both write "
                    f"tag ({format_tag(group, element)})."
                )
            seen[(group, element)] = row
            mappings.append(self.mapping_for(row, group, element))
        return mappings
