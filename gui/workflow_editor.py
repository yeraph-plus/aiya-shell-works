"""Workflow editor window with module selection and schema-driven step forms."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core import ModuleDefinition, ModuleManager, WorkflowDefinition, WorkflowLoader
from gui.widgets.dynamic_form import DynamicParameterForm
from gui.workflow_editor_state import WorkflowDraft, filter_modules


_ATOM_OPTIONS = [
    ("file", "文件原子", "每个文件作为 1 个任务单元（recurse 控制是否展开文件夹）。"),
    ("folder", "文件夹原子", "整个文件夹作为 1 个任务单元（recurse 必须为 false）。"),
    ("line", "文本行原子", "每行文本作为 1 个任务单元（--lines 输入）。"),
    ("none", "无输入原子", "无输入，从空白直接产出文件。"),
]

_SCOPE_OPTIONS = [
    (1, "每单元独立", "每个输入/行作为独立的任务，事件与上下文不共享。"),
    (0, "全部合并为 1 任务", "所有输入合并到产物目录，模块在 1 个上下文里完成遍历。"),
]


class _AtomDialog(QDialog):
    """Choose atom + scope + recurse when creating a new workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建工作流")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        hint = QLabel("选择工作流的原子粒度与上下文范围（创建后不可更改）：")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.atom_group = QButtonGroup(self)
        for i, (atom_value, atom_name, atom_desc) in enumerate(_ATOM_OPTIONS):
            radio = QRadioButton(f"{atom_name} —— {atom_desc}")
            radio.setProperty("atom_value", atom_value)
            self.atom_group.addButton(radio)
            if i == 0:
                radio.setChecked(True)
            layout.addWidget(radio)

        layout.addSpacing(8)
        scope_row_label = QLabel("上下文范围 (scope)：")
        layout.addWidget(scope_row_label)
        self.scope_combo = QComboBox()
        for value, name, _desc in _SCOPE_OPTIONS:
            self.scope_combo.addItem(name, value)
        layout.addWidget(self.scope_combo)

        self.recurse_check = QCheckBox("递归展开文件夹 (--recurse；仅 atom=file 有效)")
        layout.addWidget(self.recurse_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_atom(self) -> str:
        btn = self.atom_group.checkedButton()
        return btn.property("atom_value") if btn is not None else "file"

    def selected_scope(self) -> int:
        return self.scope_combo.currentData() or 1

    def selected_recurse(self) -> bool:
        return self.recurse_check.isChecked()


class WorkflowEditor(QMainWindow):
    """Standalone editor window for creating and saving workflow YAML files."""

    workflow_saved = Signal(object)

    def __init__(
        self,
        workflow_loader: WorkflowLoader,
        module_manager: ModuleManager,
        workflow: WorkflowDefinition | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workflow_loader = workflow_loader
        self.module_manager = module_manager
        self.modules: dict[str, ModuleDefinition] = self.module_manager.get_modules()
        self._is_existing_workflow = workflow is not None
        self.draft = WorkflowDraft.from_workflow(
            workflow or self.workflow_loader.new_workflow()
        )
        self._is_refreshing = False
        self._dirty = False

        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Workflow Editor")
        self.resize(1200, 760)

        self._build_ui()
        self._load_draft_into_widgets()
        self._refresh_available_modules()
        self._refresh_steps()
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------
    # Toolbar ops
    # ------------------------------------------------------------

    def new_workflow(self) -> None:
        dialog = _AtomDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        atom = dialog.selected_atom()
        scope = dialog.selected_scope()
        recurse = dialog.selected_recurse()
        self._is_existing_workflow = False
        template = self.workflow_loader.new_workflow(
            atom=atom, scope=scope, recurse=recurse,
        )
        if template.atom == "file" and not template.recurse:
            # folder unit implied: rename to "folder" hint is in UI only;
            # executor maps the plan.atom at runtime.
            pass
        self.draft = WorkflowDraft.from_workflow(template)
        self._load_draft_into_widgets()
        self._refresh_available_modules()
        self._refresh_steps()
        self._set_dirty(False)
        self.statusBar().showMessage(
            f"已创建新工作流（atom={atom}, scope={scope}, recurse={recurse}）"
        )

    def open_workflow(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self, "打开工作流",
            str(self.workflow_loader.workflows_dir),
            "Workflow Files (*.yaml *.yml)",
        )
        if not selected_path:
            return
        workflow = self.workflow_loader.load(Path(selected_path))
        self._is_existing_workflow = True
        self.draft = WorkflowDraft.from_workflow(workflow)
        self._load_draft_into_widgets()
        self._refresh_available_modules()
        self._refresh_steps()
        self._set_dirty(False)
        self.statusBar().showMessage(f"已打开 {Path(selected_path).name}")

    def save_workflow(self) -> Path | None:
        if self.draft.source_path is None:
            return self.save_workflow_as()
        return self._save_to_target(self.draft.source_path)

    def save_workflow_as(self) -> Path | None:
        default_name = self.workflow_loader._default_filename(  # noqa: SLF001
            self.name_input.text() or "workflow"
        )
        selected_path, _ = QFileDialog.getSaveFileName(
            self, "另存为",
            str(self.workflow_loader.workflows_dir / default_name),
            "Workflow Files (*.yaml *.yml)",
        )
        if not selected_path:
            return None
        return self._save_to_target(Path(selected_path))

    def clone_workflow(self) -> None:
        self._sync_meta_from_widgets()
        workflow = self.draft.to_workflow_definition()
        if self.draft.source_path is not None:
            base_name = self.draft.source_path.stem
        else:
            base_name = self.workflow_loader._default_filename(  # noqa: SLF001
                self.name_input.text() or "workflow"
            ).replace(".yaml", "").replace(".yml", "")
        clone_stem = f"{base_name}-副本"
        target = self.workflow_loader.workflows_dir / f"{clone_stem}.yaml"
        counter = 2
        while target.exists():
            target = self.workflow_loader.workflows_dir / f"{clone_stem}-{counter}.yaml"
            counter += 1
        try:
            self.workflow_loader.save(workflow, target.name)
        except Exception as exc:
            QMessageBox.critical(self, "创建副本失败", str(exc))
            self.statusBar().showMessage("创建副本失败")
            return
        self.workflow_saved.emit(target)
        self.statusBar().showMessage(f"已创建副本: {target.name}")

    # ------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)

        toolbar_layout = QHBoxLayout()
        self.new_button = QPushButton("新建")
        self.open_button = QPushButton("打开")
        self.save_button = QPushButton("保存")
        self.save_as_button = QPushButton("另存为")
        self.clone_button = QPushButton("副本")
        toolbar_layout.addWidget(self.new_button)
        toolbar_layout.addWidget(self.open_button)
        toolbar_layout.addWidget(self.clone_button)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self.save_button)
        toolbar_layout.addWidget(self.save_as_button)
        root_layout.addLayout(toolbar_layout)

        meta_group = QGroupBox("工作流信息")
        meta_layout = QFormLayout(meta_group)
        self.name_input = QLineEdit()
        self.description_input = QPlainTextEdit()
        self.description_input.setPlaceholderText("工作流说明")
        self.description_input.setFixedHeight(70)

        self.atom_label = QLabel("atom: -")
        self.scope_label = QLabel("scope: -")
        self.recurse_label = QLabel("recurse: -")
        atom_hint = QLabel("（atom/scope/recurse 在新建时选定，编辑模式下不可更改）")
        atom_hint.setStyleSheet("color: #888; font-size: 11px;")
        meta_layout.addRow("名称", self.name_input)
        meta_layout.addRow("atom", self.atom_label)
        meta_layout.addRow("scope", self.scope_label)
        meta_layout.addRow("recurse", self.recurse_label)
        meta_layout.addRow("", atom_hint)
        meta_layout.addRow("描述", self.description_input)
        root_layout.addWidget(meta_group)

        body_layout = QHBoxLayout()
        body_layout.addWidget(self._build_available_panel(), stretch=1)
        body_layout.addLayout(self._build_action_buttons())
        body_layout.addWidget(self._build_combined_panel(), stretch=3)
        root_layout.addLayout(body_layout, stretch=1)

        self.setCentralWidget(central)
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

    # ------------------------------------------------------------
    # Signal binding
    # ------------------------------------------------------------

    def _bind_signals(self) -> None:
        self.new_button.clicked.connect(self.new_workflow)
        self.open_button.clicked.connect(self.open_workflow)
        self.save_button.clicked.connect(self.save_workflow)
        self.save_as_button.clicked.connect(self.save_workflow_as)
        self.clone_button.clicked.connect(self.clone_workflow)
        self.add_step_button.clicked.connect(self._add_selected_module)
        self.remove_step_button.clicked.connect(self._remove_selected_step)
        self.move_up_button.clicked.connect(lambda: self._move_selected_step(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected_step(1))
        self.steps_list.currentRowChanged.connect(self._on_step_selection_changed)
        self.steps_list.itemDoubleClicked.connect(
            lambda item: self.steps_list.setCurrentItem(item)
        )
        self.step_name_input.textEdited.connect(self._on_step_name_changed)
        self.parameter_form.values_changed.connect(self._on_step_params_changed)
        self.name_input.textEdited.connect(self._sync_meta_from_widgets)
        self.description_input.textChanged.connect(self._sync_meta_from_widgets)
        self.available_modules_list.itemDoubleClicked.connect(
            lambda _item: self._add_selected_module()
        )

    # ------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------

    def _load_draft_into_widgets(self) -> None:
        try:
            self._is_refreshing = True
            self.name_input.setText(self.draft.name)
            self.atom_label.setText(f"atom: {self.draft.atom}")
            self.scope_label.setText(f"scope: {self.draft.scope}")
            self.recurse_label.setText(f"recurse: {self.draft.recurse}")
            self.description_input.setPlainText(self.draft.description)
        finally:
            self._is_refreshing = False

    def _sync_meta_from_widgets(self) -> None:
        if self._is_refreshing:
            return
        self.draft.name = self.name_input.text()
        self.draft.description = self.description_input.toPlainText()
        self._set_dirty()

    # ------------------------------------------------------------
    # Available modules
    # ------------------------------------------------------------

    def _refresh_available_modules(self) -> None:
        self.available_modules_list.clear()
        atom = self.draft.atom
        scope = self.draft.scope
        for module_definition in filter_modules(
            self.modules,
            active_tags=None,
            active_atom=atom,
            active_scope=scope,
        ):
            name = str(module_definition.module_meta.get("name", module_definition.slug))
            tag_text = "  ".join(f"[{t}]" for t in module_definition.tags)
            display = f"{name}  {tag_text}" if tag_text else name
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, module_definition.slug)
            item.setToolTip(
                str(module_definition.module_meta.get("description", module_definition.slug))
            )
            self.available_modules_list.addItem(item)

    # ------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------

    def _refresh_steps(self, *, selected_index: int | None = None) -> None:
        self.steps_list.clear()
        for index, step in enumerate(self.draft.steps, start=1):
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

        if self.draft.steps:
            self.step_summary_label.setText(f"共 {len(self.draft.steps)} 个步骤")
            idx = selected_index if selected_index is not None else 0
            idx = max(0, min(idx, len(self.draft.steps) - 1))
            self.steps_list.setCurrentRow(idx)
        else:
            self.step_summary_label.setText("尚未添加步骤")
            self._clear_step_editor()

    def _add_selected_module(self) -> None:
        current_item = self.available_modules_list.currentItem()
        if current_item is None:
            return
        slug = current_item.data(Qt.UserRole)
        module_definition = self.modules.get(slug)
        if module_definition is None:
            return
        self.draft.add_step(module_definition)
        self._refresh_steps(selected_index=len(self.draft.steps) - 1)
        self._set_dirty()
        self.statusBar().showMessage(f"已添加模块 {slug}")

    def _remove_selected_step(self) -> None:
        index = self.steps_list.currentRow()
        if index < 0:
            return
        removed = self.draft.remove_step(index)
        next_index = min(index, len(self.draft.steps) - 1)
        self._refresh_steps(selected_index=next_index if next_index >= 0 else None)
        self._set_dirty()
        self.statusBar().showMessage(f"已移除步骤 {removed.module}")

    def _move_selected_step(self, offset: int) -> None:
        index = self.steps_list.currentRow()
        if index < 0:
            return
        new_index = max(0, min(index + offset, len(self.draft.steps) - 1))
        if new_index == index:
            return
        step_slug = self.draft.steps[index].module
        other_slug = self.draft.steps[new_index].module
        mod_step = self.modules.get(step_slug)
        mod_other = self.modules.get(other_slug)
        if offset < 0 and mod_other and mod_other.parent == step_slug:
            self.statusBar().showMessage(f"无法上移：{mod_other.slug} 依赖 {step_slug}")
            return
        if offset > 0 and mod_step and mod_step.parent == other_slug:
            self.statusBar().showMessage(f"无法下移：{mod_step.slug} 依赖 {other_slug}")
            return
        new_index = self.draft.move_step(index, offset)
        self._refresh_steps(selected_index=new_index)
        self._set_dirty()

    def _on_step_selection_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.draft.steps):
            self._clear_step_editor()
            return
        step = self.draft.steps[index]
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
            str(module_definition.module_meta.get("description", ""))
            or "该模块未提供额外说明。"
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
        if self._is_refreshing:
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        self.draft.update_step_name(index, value)
        self._refresh_steps(selected_index=index)
        self._set_dirty()

    def _on_step_params_changed(self, values: dict) -> None:
        if self._is_refreshing:
            return
        index = self.steps_list.currentRow()
        if index < 0 or index >= len(self.draft.steps):
            return
        self.draft.update_step_params(index, values)
        self._set_dirty()

    # ------------------------------------------------------------
    # Save
    # ------------------------------------------------------------

    def _save_to_target(self, target_path: Path) -> Path | None:
        self._sync_meta_from_widgets()
        workflow = self.draft.to_workflow_definition()
        try:
            relative_target = target_path.resolve().relative_to(
                self.workflow_loader.workflows_dir
            )
            saved_path = self.workflow_loader.save(workflow, relative_target)
        except Exception as exc:  # pragma: no cover - UI feedback path
            QMessageBox.critical(self, "保存失败", str(exc))
            self.statusBar().showMessage("保存失败")
            return None
        self.draft.source_path = saved_path
        self._set_dirty(False)
        self.workflow_saved.emit(saved_path)
        self.statusBar().showMessage(f"已保存到 {saved_path.name}")
        return saved_path

    def _set_dirty(self, dirty: bool = True) -> None:
        self._dirty = dirty
        title = "Workflow Editor"
        if self.draft.source_path is not None:
            title += f" - {self.draft.source_path.name}"
        if dirty:
            title += " *"
        self.setWindowTitle(title)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._dirty:
            reply = QMessageBox.question(
                self, "未保存的更改", "工作流已修改。是否在关闭前保存？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Save:
                saved = self.save_workflow()
                if saved is None:
                    event.ignore()
                    return
                event.accept()
            elif reply == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
            return
        super().closeEvent(event)