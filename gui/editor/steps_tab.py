"""Workflow steps tab — module picker, step list, and parameter form.

Migrated from the monolithic WorkflowEditor.  Reads the shared draft and
the module manager; emits ``dirty_changed`` whenever the step sequence or
parameters change so the editor window can update its dirty flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from core import ModuleDefinition, ModuleManager
from gui.widgets.dynamic_form import DynamicParameterForm
from gui.editor.state import WorkflowDraft, filter_modules

if TYPE_CHECKING:
    pass


class StepsTab(QWidget):
    """Available modules + ordered steps + parameter form."""

    dirty_changed = Signal()

    def __init__(self, module_manager: ModuleManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.module_manager = module_manager
        self.modules: dict[str, ModuleDefinition] = module_manager.get_modules()
        self._draft: WorkflowDraft | None = None
        self._is_refreshing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        body = QHBoxLayout()
        body.addWidget(self._build_available_panel())
        body.addLayout(self._build_action_buttons())
        body.addWidget(self._build_combined_panel(), stretch=1)
        root.addLayout(body, stretch=1)

        self._bind_signals()

    def _build_available_panel(self) -> QWidget:
        group = QGroupBox("可用模块")
        layout = QVBoxLayout(group)
        self.available_modules_list = QListWidget()
        layout.addWidget(self.available_modules_list, stretch=1)
        group.setFixedWidth(260)
        return group

    def _build_action_buttons(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        self.add_step_button = QPushButton("添加 ->")
        self.remove_step_button = QPushButton("<- 移除")
        self.move_up_button = QPushButton("上移")
        self.move_down_button = QPushButton("下移")
        layout.addStretch(1)
        layout.addWidget(self.add_step_button)
        layout.addWidget(self.remove_step_button)
        layout.addSpacing(20)
        layout.addWidget(self.move_up_button)
        layout.addWidget(self.move_down_button)
        layout.addStretch(1)
        return layout

    def _build_combined_panel(self) -> QWidget:
        group = QGroupBox("步骤配置")
        layout = QHBoxLayout(group)

        steps_widget = QWidget()
        steps_layout = QVBoxLayout(steps_widget)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        self.steps_list = QListWidget()
        self.step_summary_label = QLabel("尚未添加步骤")
        steps_layout.addWidget(self.steps_list, stretch=1)
        steps_layout.addWidget(self.step_summary_label)
        steps_widget.setFixedWidth(260)

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        form_layout = QFormLayout()
        self.step_name_input = QLineEdit()
        self.step_module_label = QLabel("未选择步骤")
        self.step_module_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form_layout.addRow("步骤名称", self.step_name_input)
        form_layout.addRow("模块", self.step_module_label)
        detail_layout.addLayout(form_layout)

        self.step_description_label = QLabel("选择一个步骤后即可编辑参数。")
        self.step_description_label.setWordWrap(True)
        detail_layout.addWidget(self.step_description_label)

        self.parameter_form = DynamicParameterForm()
        detail_layout.addWidget(self.parameter_form, stretch=1)

        layout.addWidget(steps_widget)
        layout.addWidget(detail_widget, stretch=1)
        return group

    def _bind_signals(self) -> None:
        self.add_step_button.clicked.connect(self._add_selected_module)
        self.remove_step_button.clicked.connect(self._remove_selected_step)
        self.move_up_button.clicked.connect(lambda: self._move_selected_step(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected_step(1))
        self.steps_list.currentRowChanged.connect(self._on_step_selection_changed)
        self.steps_list.itemDoubleClicked.connect(lambda item: self.steps_list.setCurrentItem(item))
        self.step_name_input.textEdited.connect(self._on_step_name_changed)
        self.parameter_form.values_changed.connect(self._on_step_params_changed)
        self.available_modules_list.itemDoubleClicked.connect(lambda _item: self._add_selected_module())

    # ------------------------------------------------------------------
    # Draft + modules
    # ------------------------------------------------------------------

    def set_draft(self, draft: WorkflowDraft) -> None:
        self._draft = draft
        self.refresh_available_modules()
        self._refresh_steps()

    def refresh_modules(self) -> None:
        self.modules = self.module_manager.get_modules()
        self.refresh_available_modules()

    def refresh_available_modules(self) -> None:
        self.available_modules_list.clear()
        if self._draft is None:
            return
        atom = self._draft.atom
        expected_is_file_module = atom in {"file", "folder"} if atom else None
        for module_definition in filter_modules(
            self.modules,
            active_tags=None,
            expected_is_file_module=expected_is_file_module,
        ):
            name = str(module_definition.module_meta.get("name", module_definition.slug))
            tag_text = "  ".join(f"[{t}]" for t in module_definition.tags)
            display = f"{name}  {tag_text}" if tag_text else name
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, module_definition.slug)
            item.setToolTip(str(module_definition.module_meta.get("description", module_definition.slug)))
            self.available_modules_list.addItem(item)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _refresh_steps(self, *, selected_index: int | None = None) -> None:
        self.steps_list.clear()
        if self._draft is None:
            return
        for index, step in enumerate(self._draft.steps, start=1):
            module_definition = self.modules.get(step.module)
            module_name = (
                str(module_definition.module_meta.get("name", step.module))
                if module_definition is not None
                else step.module
            )
            title = step.name or module_name
            item = QListWidgetItem(f"{index}. {title}")
            item.setData(Qt.UserRole, index - 1)
            item.setToolTip(step.module)
            self.steps_list.addItem(item)

        if self._draft.steps:
            self.step_summary_label.setText(f"共 {len(self._draft.steps)} 个步骤")
            idx = selected_index if selected_index is not None else 0
            idx = max(0, min(idx, len(self._draft.steps) - 1))
            self.steps_list.setCurrentRow(idx)
        else:
            self.step_summary_label.setText("尚未添加步骤")
            self._clear_step_editor()

    def _add_selected_module(self) -> None:
        if self._draft is None:
            return
        current_item = self.available_modules_list.currentItem()
        if current_item is None:
            return
        slug = current_item.data(Qt.UserRole)
        module_definition = self.modules.get(slug)
        if module_definition is None:
            return
        self._draft.add_step(module_definition)
        self._refresh_steps(selected_index=len(self._draft.steps) - 1)
        self.dirty_changed.emit()

    def _remove_selected_step(self) -> None:
        if self._draft is None:
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        removed = self._draft.remove_step(index)
        next_index = min(index, len(self._draft.steps) - 1)
        self._refresh_steps(selected_index=next_index if next_index >= 0 else None)
        self.dirty_changed.emit()

    def _move_selected_step(self, offset: int) -> None:
        if self._draft is None:
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        new_index = max(0, min(index + offset, len(self._draft.steps) - 1))
        if new_index == index:
            return
        step_slug = self._draft.steps[index].module
        other_slug = self._draft.steps[new_index].module
        mod_step = self.modules.get(step_slug)
        mod_other = self.modules.get(other_slug)
        if offset < 0 and mod_other and mod_other.parent == step_slug:
            return
        if offset > 0 and mod_step and mod_step.parent == other_slug:
            return
        new_index = self._draft.move_step(index, offset)
        self._refresh_steps(selected_index=new_index)
        self.dirty_changed.emit()

    def _on_step_selection_changed(self, index: int) -> None:
        if self._draft is None or index < 0 or index >= len(self._draft.steps):
            self._clear_step_editor()
            return
        step = self._draft.steps[index]
        module_definition = self.modules.get(step.module)
        try:
            self._is_refreshing = True
            self.step_name_input.setText(step.name)
        finally:
            self._is_refreshing = False
        self.step_module_label.setText(step.module)
        if module_definition is None:
            self.step_description_label.setText("该步骤引用的模块当前不可用。")
            self.parameter_form.set_schema({}, {})
            return
        self.step_description_label.setText(
            str(module_definition.module_meta.get("description", "")) or "该模块未提供额外说明。"
        )
        self.parameter_form.set_schema(module_definition.config_schema, dict(step.params))

    def _clear_step_editor(self) -> None:
        try:
            self._is_refreshing = True
            self.step_name_input.clear()
        finally:
            self._is_refreshing = False
        self.step_module_label.setText("未选择步骤")
        self.step_description_label.setText("选择一个步骤后即可编辑参数。")
        self.parameter_form.set_schema({}, {})

    def _on_step_name_changed(self, value: str) -> None:
        if self._is_refreshing or self._draft is None:
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        self._draft.update_step_name(index, value)
        self._refresh_steps(selected_index=index)
        self.dirty_changed.emit()

    def _on_step_params_changed(self, values: dict) -> None:
        if self._is_refreshing or self._draft is None:
            return
        index = self.steps_list.currentRow()
        if index < 0 or index >= len(self._draft.steps):
            return
        self._draft.update_step_params(index, values)
        self.dirty_changed.emit()
