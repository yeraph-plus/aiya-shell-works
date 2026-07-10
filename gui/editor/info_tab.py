"""Workflow info tab — meta fields plus atom/scope definition.

Holds the name/description editors, the atom radio group (4 options in a row
with a description label below), and the scope spin box.  In edit mode the
atom and scope controls are read-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from gui.editor.state import WorkflowDraft


_ATOM_OPTIONS: tuple[tuple[str, str, bool, str], ...] = (
    ("file", "文件", True, "每个文件作为 1 个任务单元，文件夹递归展开。"),
    ("folder", "文件夹", False, "整个文件夹作为 1 个任务单元。"),
    ("line", "文本行", False, "每行文本作为 1 个任务单元（--lines 输入）。"),
    ("none", "空", False, "无输入，从空白直接产出文件。"),
)


class InfoTab(QWidget):
    """Workflow metadata + atom/scope definition tab."""

    atom_scope_changed = Signal()
    meta_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._draft: WorkflowDraft | None = None
        self._is_new = True
        self._refreshing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        meta_group = QGroupBox("工作流信息")
        meta_form = QFormLayout(meta_group)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("工作流显示名称")
        self.description_input = QPlainTextEdit()
        self.description_input.setPlaceholderText("工作流说明")
        self.description_input.setFixedHeight(70)
        meta_form.addRow("名称", self.name_input)
        meta_form.addRow("描述", self.description_input)
        root.addWidget(meta_group)

        atom_group = QGroupBox("原子定义")
        atom_layout = QVBoxLayout(atom_group)

        radio_row = QHBoxLayout()
        radio_row.setContentsMargins(0, 0, 0, 0)
        self.atom_button_group = QButtonGroup(self)
        self.atom_button_group.setExclusive(True)
        for atom_value, label, recurse, desc in _ATOM_OPTIONS:
            radio = QRadioButton(label)
            radio.setProperty("atom_value", atom_value)
            radio.setProperty("recurse_value", recurse)
            radio.setProperty("desc", desc)
            self.atom_button_group.addButton(radio)
            radio_row.addWidget(radio)
        radio_row.addStretch(1)
        atom_layout.addLayout(radio_row)

        self.atom_desc_label = QLabel()
        self.atom_desc_label.setStyleSheet("color: #888; font-size: 11px;")
        self.atom_desc_label.setWordWrap(True)
        atom_layout.addWidget(self.atom_desc_label)

        self.atom_button_group.buttonClicked.connect(self._on_atom_changed)
        root.addWidget(atom_group)

        scope_group = QGroupBox("分批范围 (scope)")
        scope_layout = QVBoxLayout(scope_group)
        scope_row = QHBoxLayout()
        self.scope_spin = QSpinBox()
        self.scope_spin.setRange(0, 2147483647)
        self.scope_spin.setValue(1)
        self.scope_spin.valueChanged.connect(self._on_scope_changed)
        scope_hint = QLabel("仅限制为整数输入；0 = 全部合并为 1 任务（shared） | 1 = 每单元独立（per-unit）")
        scope_hint.setStyleSheet("color: #888; font-size: 11px;")
        scope_row.addWidget(self.scope_spin)
        scope_row.addWidget(scope_hint)
        scope_row.addStretch(1)
        scope_layout.addLayout(scope_row)
        root.addWidget(scope_group)

        self.name_input.textEdited.connect(self._on_meta_edited)
        self.description_input.textChanged.connect(self._on_meta_edited)

    # ------------------------------------------------------------------
    # Draft binding
    # ------------------------------------------------------------------

    def set_draft(self, draft: WorkflowDraft, *, is_new: bool) -> None:
        self._draft = draft
        self._is_new = is_new
        self.load_draft()

    def load_draft(self) -> None:
        if self._draft is None:
            return
        try:
            self._refreshing = True
            self.name_input.setText(self._draft.name)
            self.description_input.setPlainText(self._draft.description)
            self._select_atom_radio(self._draft.atom, self._draft.recurse)
            self.scope_spin.setValue(self._draft.scope)
        finally:
            self._refreshing = False
        self._apply_mode()

    def set_edit_mode(self, is_new: bool) -> None:
        self._is_new = is_new
        self._apply_mode()

    def _apply_mode(self) -> None:
        editable = self._is_new
        for button in self.atom_button_group.buttons():
            button.setEnabled(editable)
        self.scope_spin.setReadOnly(not editable)
        self.scope_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
            if editable
            else QAbstractSpinBox.ButtonSymbols.NoButtons
        )

    # ------------------------------------------------------------------
    # Sync widgets -> draft
    # ------------------------------------------------------------------

    def sync_to_draft(self) -> None:
        if self._draft is None or self._refreshing:
            return
        self._draft.name = self.name_input.text()
        self._draft.description = self.description_input.toPlainText()
        if self._is_new:
            button = self.atom_button_group.checkedButton()
            if button is not None:
                self._draft.atom = str(button.property("atom_value") or "file")
                recurse = button.property("recurse_value")
                self._draft.recurse = bool(recurse)
            self._draft.scope = self.scope_spin.value()

    def _on_meta_edited(self, *_args: object) -> None:
        if self._refreshing or self._draft is None:
            return
        self._draft.name = self.name_input.text()
        self._draft.description = self.description_input.toPlainText()
        self.meta_changed.emit()

    def _on_atom_changed(self, *_args: object) -> None:
        button = self.atom_button_group.checkedButton()
        if button is not None:
            desc = str(button.property("desc") or "")
            self.atom_desc_label.setText(desc)
        if self._refreshing or self._draft is None or not self._is_new:
            return
        if button is not None:
            self._draft.atom = str(button.property("atom_value") or "file")
            self._draft.recurse = bool(button.property("recurse_value"))
        self.atom_scope_changed.emit()

    def _on_scope_changed(self, value: int) -> None:
        if self._refreshing or self._draft is None or not self._is_new:
            return
        self._draft.scope = value
        self.atom_scope_changed.emit()

    def _select_atom_radio(self, atom: str, recurse: bool) -> None:
        for button in self.atom_button_group.buttons():
            if (
                button.property("atom_value") == atom
                and bool(button.property("recurse_value")) == recurse
            ):
                button.setChecked(True)
                self.atom_desc_label.setText(str(button.property("desc") or ""))
                return
        for button in self.atom_button_group.buttons():
            if button.property("atom_value") == atom:
                button.setChecked(True)
                self.atom_desc_label.setText(str(button.property("desc") or ""))
                return
