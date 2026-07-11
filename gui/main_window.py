"""Main application window for running workflows with background execution."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core import ModuleManager, WorkflowDefinition
from gui.widgets import ConfigPanel, InputPanel, LogViewer
from gui.widgets.execution_controller import ExecutionController
from gui.workflow_editor import WorkflowEditor


class MainWindow(QMainWindow):
    """Desktop window for selecting workflows and running them safely."""

    def __init__(self, project_dir: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_dir = Path(project_dir).resolve()
        self.workflows_dir = self.project_dir / "workflows"
        self.modules_dir = self.project_dir / "modules"
        self._last_workflow_atom: str = "none"
        self._last_workflow_recurse: bool = False

        self.setWindowTitle("Shell Worker Platform")
        self.resize(1200, 800)

        self._build_ui()
        self._build_controller()
        self._bind_signals()
        self._reload_workflows()
        self.statusBar().showMessage("就绪")

    def _build_ui(self) -> None:
        central = QWidget(self)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        top_layer = QHBoxLayout()
        top_layer.setSpacing(8)

        left_column = QVBoxLayout()
        left_column.setSpacing(8)

        self.config_panel = ConfigPanel(self.workflows_dir)
        left_column.addWidget(self.config_panel)

        top_layer.addLayout(left_column, stretch=35)

        self.input_panel = InputPanel()
        top_layer.addWidget(self.input_panel, stretch=65)

        main_layout.addLayout(top_layer, stretch=2)

        self.log_viewer = LogViewer()
        main_layout.addWidget(self.log_viewer, stretch=1)

        self.setCentralWidget(central)

    def _build_controller(self) -> None:
        self._controller = ExecutionController(
            config_panel=self.config_panel,
            input_panel=self.input_panel,
            log_viewer=self.log_viewer,
            modules_dir=str(self.modules_dir),
            parent=self,
        )
        self._controller.status_message.connect(self.statusBar().showMessage)
        self._controller.execution_state_changed.connect(self._on_execution_state_changed)
        self._controller.log_message.connect(self._handle_error_log)
        self._controller.validation_failed.connect(self._on_validation_failed)

    def _handle_error_log(self, message: str) -> None:
        if message.startswith("[ERROR]"):
            QMessageBox.critical(self, "执行失败", message.removeprefix("[ERROR] "))

    def _bind_signals(self) -> None:
        self.config_panel.workflow_changed.connect(self._on_workflow_changed)
        self.config_panel.refresh_requested.connect(self._reload_workflows)
        self.config_panel.edit_requested.connect(self._open_workflow_editor)
        self.config_panel.output_dir_changed.connect(self._on_output_dir_changed)
        self.config_panel.log_save_changed.connect(self._on_log_save_changed)
        self.config_panel.execute_requested.connect(self._start_execution)
        self.config_panel.stop_requested.connect(self._controller.stop)
        self.config_panel.watch_state_changed.connect(self._on_watch_state_changed)

        self.input_panel.paths_changed.connect(self._update_execute_button)
        self.input_panel.status_message.connect(self.statusBar().showMessage)
        self.input_panel.warning.connect(self._on_input_warning)

    def _reload_workflows(self) -> None:
        selected_summary = self.config_panel.get_selected_summary()
        selected_path = selected_summary.path if selected_summary else None
        self.config_panel.load_workflows(selected_path)

    def _on_workflow_changed(self, workflow: WorkflowDefinition | None) -> None:
        if workflow is None:
            self._last_workflow_atom = "none"
            self._last_workflow_recurse = False
            self.input_panel.set_atom("none", False)
            self._update_execute_button()
            self.statusBar().showMessage("选择或新建工作流以开始执行")
            return

        atom = workflow.atom or "none"
        self._last_workflow_atom = atom
        self._last_workflow_recurse = workflow.recurse
        if self.config_panel.is_watch_enabled():
            self.input_panel.set_atom("none", False)
        else:
            self.input_panel.set_atom(atom, workflow.recurse)
        self._update_execute_button()
        self.statusBar().showMessage(f"已选择工作流: {workflow.meta.name}")

    def _on_watch_state_changed(self, enabled: bool) -> None:
        if enabled:
            self.input_panel.set_atom("none", False)
        else:
            self.input_panel.set_atom(self._last_workflow_atom, self._last_workflow_recurse)

    def _on_output_dir_changed(self, text: str) -> None:
        self._update_execute_button()

    def _on_log_save_changed(self, enabled: bool) -> None:
        status = "已启用" if enabled else "已禁用"
        self.statusBar().showMessage(f"日志保存{status}")

    def _on_execution_state_changed(self, running: bool) -> None:
        self._update_execute_button()

    def _open_workflow_editor(self, workflow: WorkflowDefinition | None) -> None:
        module_manager = ModuleManager(self.modules_dir)
        self._editor_window = WorkflowEditor(
            workflow_loader=self.config_panel.workflow_loader,
            module_manager=module_manager,
            workflow=workflow,
            parent=self,
        )
        self._editor_window.workflow_saved.connect(self._on_workflow_saved)
        self._editor_window.show()

    def _on_workflow_saved(self, saved_path: Path) -> None:
        self._reload_workflows()

    def _update_execute_button(self) -> None:
        running = self._controller.is_running
        self.config_panel.set_execute_enabled(not running and self._can_start_execution())
        self.config_panel.set_stop_enabled(running)

    def _can_start_execution(self) -> bool:
        workflow = self.config_panel.get_current_workflow()
        if workflow is None:
            return False

        if self.config_panel.is_watch_enabled():
            output_dir = self.config_panel.get_output_dir()
            if not output_dir and not self.config_panel.is_direct_mode():
                return False
            return bool(self.config_panel.get_watch_dir())

        atom = workflow.atom
        has_paths = self.input_panel.input_list.count() > 0
        has_text = bool(self.input_panel.text_editor.toPlainText().strip())
        output_dir = self.config_panel.get_output_dir()

        if not output_dir:
            if not self.config_panel.is_direct_mode() or atom in {"none", "line"}:
                return False

        if atom == "none":
            return True
        if atom == "line":
            return has_text
        if atom in {"file", "folder"}:
            return has_paths
        if atom is None:
            return has_paths or has_text
        return False

    def _start_execution(self) -> None:
        self._controller.start()

    def _on_validation_failed(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def _on_input_warning(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"{title}: {message}", 8000)
