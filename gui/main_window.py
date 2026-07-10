"""Main application window for running workflows with background execution."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core import (
    InputInspector,
    ModuleManager,
    PipelineEvent,
    PipelineExecutor,
    PipelineRuntime,
    WorkflowDefinition,
    WorkflowLoader,
)
from gui.widgets import ConfigPanel, InputPanel, LogViewer, TerminalWindow
from gui.workflow_editor import WorkflowEditor


class ExecutionWorker(QObject):
    """Run workflow execution in a worker thread and proxy updates via signals."""

    log_message = Signal(str)
    progress_changed = Signal(dict)
    finished = Signal(dict)
    unit_status = Signal(int, str)
    terminal_event = Signal(dict)

    def __init__(
        self,
        *,
        workflow: WorkflowDefinition,
        input_paths: list[str],
        input_text: str = "",
        output_dir: str,
        direct_mode: bool,
        modules_dir: str,
        log_save: bool = False,
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.input_paths = list(input_paths)
        self.input_text = input_text
        self.output_dir = output_dir
        self.direct_mode = direct_mode
        self.modules_dir = modules_dir
        self.log_save = log_save
        self.runtime: PipelineRuntime | None = None
        self._cancel_event = Event()

    def request_stop(self) -> None:
        """Ask the worker to stop at the next safe boundary."""
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        input_results: list[dict[str, Any]] = []
        total_inputs = 0

        try:
            log_file = None
            if self.log_save and self.output_dir:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                workflow_name = self.workflow.meta.name.replace(" ", "_")
                log_file = str(Path(self.output_dir) / f"{timestamp}_{workflow_name}.jsonl")

            self.runtime = PipelineRuntime(log_file=log_file)
            executor = PipelineExecutor(
                module_manager=self._build_module_manager(),
                runtime=self.runtime,
                event_listener=self._on_executor_event,
                progress_callback=self._forward_progress,
                cancel_requested=self._cancel_event.is_set,
            )

            for index in range(len(self.input_paths)):
                self.unit_status.emit(index, "processing")

            atom = self.workflow.atom
            if atom == "line":
                self.log_message.emit("开始处理文本输入。")
            elif atom == "none":
                self.log_message.emit("开始执行无输入工作流。")
            elif self.workflow.scope == 0:
                self.log_message.emit(f"开始处理共享路径输入，共 {len(self.input_paths)} 个输入。")
            elif self.input_paths:
                self.log_message.emit(f"开始处理路径输入，共 {len(self.input_paths)} 个输入。")
            else:
                self.log_message.emit("开始执行自动输入工作流。")

            summary = executor.execute(
                self.workflow,
                output_dir=self.output_dir,
                files=self.input_paths or None,
                recurse=self.workflow.recurse,
                lines_text=self.input_text if self.input_text.strip() else None,
                direct_mode=self.direct_mode,
            )
            input_results = [
                {
                    "input": list(self.input_paths) if self.input_paths else None,
                    "summary": summary,
                }
            ]
            cancelled = self._cancel_event.is_set() or summary.get("cancelled", False)
            final_status = "cancelled" if cancelled else "completed" if summary.get("success") else "failed"
            for index in range(len(self.input_paths)):
                self.unit_status.emit(index, final_status)

            finished_inputs = int(summary.get("successful_units", 0)) + int(summary.get("failed_units", 0))
            total_inputs = int(summary.get("processed_units", 0))
            failed_inputs = int(summary.get("failed_units", 0))
            success = bool(input_results) and not cancelled and failed_inputs == 0
            self.finished.emit(
                {
                    "success": success,
                    "cancelled": cancelled,
                    "error": "",
                    "input_results": input_results,
                    "finished_inputs": finished_inputs,
                    "total_inputs": total_inputs,
                    "failed_inputs": failed_inputs,
                    "workflow_name": self.workflow.meta.name,
                }
            )
        except Exception as exc:
            self.log_message.emit(f"执行失败: {exc}")
            self.finished.emit(
                {
                    "success": False,
                    "cancelled": self._cancel_event.is_set(),
                    "error": str(exc),
                    "input_results": input_results,
                    "finished_inputs": len(input_results),
                    "total_inputs": total_inputs,
                    "failed_inputs": len(input_results),
                    "workflow_name": self.workflow.meta.name,
                }
            )
        finally:
            if self.runtime is not None:
                self.runtime.close()
                self.runtime = None

    def _build_module_manager(self) -> ModuleManager:
        return ModuleManager(self.modules_dir)

    def _on_executor_event(self, event: PipelineEvent) -> None:
        if event.slug == "terminal" and event.text.startswith("terminal:"):
            self.terminal_event.emit({"type": event.text, **event.data})
            return

        prefix = {
            "success": "[OK]",
            "message": "[INFO]",
            "hint": "[HINT]",
            "warning": "[WARN]",
            "error": "[ERROR]",
        }.get(event.type, "[LOG]")
        self.log_message.emit(f"{prefix} [{event.slug}] {event.text}")

    def _forward_progress(self, payload: dict[str, Any]) -> None:
        forwarded = dict(payload)
        total_units = int(payload.get("total", 0))
        current_unit = int(payload.get("current", 0))
        forwarded["input_index"] = current_unit if total_units > 0 else 0
        forwarded["input_total"] = total_units
        forwarded["input_path"] = payload.get("unit")
        self.progress_changed.emit(forwarded)


class MainWindow(QMainWindow):
    """Desktop window for selecting workflows and running them safely."""

    def __init__(self, project_dir: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_dir = Path(project_dir).resolve()
        self.workflows_dir = self.project_dir / "workflows"
        self.modules_dir = self.project_dir / "modules"
        self._worker_thread: QThread | None = None
        self._worker: ExecutionWorker | None = None
        self._terminal_windows: dict[str, TerminalWindow] = {}

        self.setWindowTitle("Shell Worker Platform")
        self.resize(1200, 800)

        self._build_ui()
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

    def _bind_signals(self) -> None:
        self.config_panel.workflow_changed.connect(self._on_workflow_changed)
        self.config_panel.refresh_requested.connect(self._reload_workflows)
        self.config_panel.edit_requested.connect(self._open_workflow_editor)
        self.config_panel.output_dir_changed.connect(self._on_output_dir_changed)
        self.config_panel.log_save_changed.connect(self._on_log_save_changed)
        self.config_panel.execute_requested.connect(self._start_execution)
        self.config_panel.stop_requested.connect(self._request_stop)

        self.input_panel.paths_changed.connect(self._update_execute_button)
        self.input_panel.status_message.connect(self.statusBar().showMessage)
        self.input_panel.warning.connect(self._show_warning)

    def _reload_workflows(self) -> None:
        selected_summary = self.config_panel.get_selected_summary()
        selected_path = selected_summary.path if selected_summary else None
        self.config_panel.load_workflows(selected_path)

    def _on_workflow_changed(self, workflow: WorkflowDefinition | None) -> None:
        if workflow is None:
            self.input_panel.set_atom("none", False)
            self._update_execute_button()
            self.statusBar().showMessage("选择或新建工作流以开始执行")
            return

        atom = workflow.atom or "none"
        self.input_panel.set_atom(atom, workflow.recurse)
        self._update_execute_button()
        self.statusBar().showMessage(f"已选择工作流: {workflow.meta.name}")

    def _on_output_dir_changed(self, text: str) -> None:
        self._update_execute_button()

    def _on_log_save_changed(self, enabled: bool) -> None:
        status = "已启用" if enabled else "已禁用"
        self.statusBar().showMessage(f"日志保存{status}")

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
        running = self._worker_thread is not None and self._worker_thread.isRunning()
        self.config_panel.set_execute_enabled(not running and self._can_start_execution())
        self.config_panel.set_stop_enabled(running)

    def _can_start_execution(self) -> bool:
        workflow = self.config_panel.get_current_workflow()
        if workflow is None:
            return False

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

    def _resolve_output_dir(self) -> str:
        """Determine effective output_dir."""
        text = self.config_panel.get_output_dir().strip()
        if text:
            return str(Path(text).resolve())

        if self.config_panel.is_direct_mode():
            workflow = self.config_panel.get_current_workflow()
            if workflow and workflow.atom in {None, "file", "folder"}:
                inputs = self.input_panel.get_files()
                if inputs:
                    return str(Path(inputs[0]).parent)
        return ""

    def _start_execution(self) -> None:
        workflow = self.config_panel.get_current_workflow()

        if workflow is None:
            QMessageBox.warning(self, "无法执行", "请先选择一个有效工作流。")
            return

        output_dir = self._resolve_output_dir()
        if not output_dir:
            QMessageBox.warning(self, "无法执行", "请先选择产物目录。")
            return

        input_paths = self.input_panel.get_files()
        input_text = self.input_panel.get_lines()
        atom = workflow.atom

        if atom in {"file", "folder"} and not input_paths:
            QMessageBox.warning(self, "无法执行", "当前工作流需要至少一个路径输入。")
            return
        if atom == "line" and not input_text.strip():
            QMessageBox.warning(self, "无法执行", "请输入至少一行文本任务。")
            return
        if atom is None and not input_paths and not input_text.strip():
            QMessageBox.warning(self, "无法执行", "请至少提供一个路径输入或一行文本任务。")
            return

        self._append_log(f"准备执行工作流: {workflow.meta.name} (直接模式: {self.config_panel.is_direct_mode()})")

        self.input_panel.reset_unit_badges()

        worker = ExecutionWorker(
            workflow=workflow,
            input_paths=input_paths,
            input_text=input_text,
            output_dir=output_dir,
            direct_mode=self.config_panel.is_direct_mode(),
            modules_dir=str(self.modules_dir),
            log_save=self.config_panel.is_log_save_enabled(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.log_message.connect(self._append_log)
        worker.progress_changed.connect(self._handle_progress)
        worker.unit_status.connect(self._on_unit_status)
        worker.terminal_event.connect(self._on_terminal_event)
        worker.finished.connect(self._handle_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup_worker)
        thread.finished.connect(thread.deleteLater)

        self._worker = worker
        self._worker_thread = thread
        self._set_running_state(True)
        thread.start()

    def _request_stop(self) -> None:
        if self._worker is None:
            return
        self._worker.request_stop()
        self._append_log("已发送停止请求，等待当前安全边界退出。")
        self.statusBar().showMessage("正在停止执行...")

    def _set_running_state(self, running: bool) -> None:
        self.config_panel.set_running(running)
        self.input_panel.set_running(running)

    def _append_log(self, message: str) -> None:
        self.log_viewer.append_message(message)

    def _on_terminal_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type", "")
        session_id = payload.get("session_id", "")

        if event_type == "terminal:started":
            command = payload.get("command", "")
            runtime = self._worker.runtime if self._worker is not None else None
            win = TerminalWindow(session_id, command, runtime=runtime, parent=self)
            win.destroyed.connect(lambda sid=session_id: self._terminal_windows.pop(sid, None))
            win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            win.show()
            self._terminal_windows[session_id] = win

        elif event_type == "terminal:output":
            win = self._terminal_windows.get(session_id)
            if win is not None:
                text = payload.get("text", "")
                if text:
                    win.append_output(text)

        elif event_type == "terminal:finished":
            win = self._terminal_windows.get(session_id)
            if win is not None:
                exit_code = payload.get("exit_code", -1)
                win.notify_finished(exit_code)

        elif event_type == "terminal:close":
            win = self._terminal_windows.pop(session_id, None)
            if win is not None:
                win.close()

    def _handle_progress(self, payload: dict[str, Any]) -> None:
        percent = int(payload.get("percent", 0))
        status = str(payload.get("status", "running"))
        input_index = int(payload.get("input_index", 0))
        input_total = int(payload.get("input_total", 0))
        unit = payload.get("unit") or payload.get("input_path") or "<none>"

        self.statusBar().showMessage(f"状态: {status} | 输入 {input_index}/{input_total} | 当前单元: {unit}")

    def _on_unit_status(self, row: int, status: str) -> None:
        self.input_panel.set_unit_status(row, status)

    def _handle_finished(self, summary: dict[str, Any]) -> None:
        self._set_running_state(False)

        if summary.get("error"):
            self.statusBar().showMessage("执行失败")
            QMessageBox.critical(self, "执行失败", str(summary["error"]))
            return

        if summary.get("cancelled"):
            self.statusBar().showMessage("执行已取消")
            self._append_log(
                f"执行已取消，已完成 {summary.get('finished_inputs', 0)} / {summary.get('total_inputs', 0)} 个输入。"
            )
            return

        if summary.get("success"):
            self.statusBar().showMessage("执行完成")
            self._append_log(
                f"执行完成：工作流 {summary.get('workflow_name', '')} 处理了 {summary.get('finished_inputs', 0)} 个输入。"
            )
            return

        self.statusBar().showMessage("执行结束，存在失败项")
        self._append_log(
            f"执行结束：失败输入 {summary.get('failed_inputs', 0)} / {summary.get('finished_inputs', 0)}。"
        )

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._worker_thread = None
        self._update_execute_button()

    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)