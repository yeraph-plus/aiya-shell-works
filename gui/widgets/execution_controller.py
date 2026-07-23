"""Execution controller: central hub for all GUI signal wiring, button management,
input mode synchronisation, and worker lifecycle.

The controller owns ConfigPanel, InputPanel, and LogViewer internally.
MainWindow only imports the controller and lays out the three public panels.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from core import (
    ModuleManager,
    PipelineEvent,
    WorkflowDefinition,
    WorkflowScheduler,
)
from gui.workflow_store import WorkflowAuthoringStore

from .config_panel import ConfigPanel
from .input_panel import InputPanel
from .log_viewer import LogViewer


@dataclass(frozen=True, slots=True)
class GuiExecutionRequest:
    workflow_path: Path
    workflow_name: str
    recurse: bool
    input_paths: tuple[str, ...]
    input_text: str
    output_dir: str
    direct_mode: bool
    modules_dir: str
    log_save: bool = False
    concurrency: int = 1
    watch: bool = False
    watch_dir: str = ""
    cron: str = ""


class ExecutionWorker(QObject):
    """Run workflow execution in a worker thread and proxy updates via signals."""

    log_message = Signal(str)
    progress_changed = Signal(dict)
    finished = Signal(dict)
    unit_status = Signal(int, str)

    def __init__(self, request: GuiExecutionRequest) -> None:
        super().__init__()
        self.request = request
        self._cancel_event = Event()
        self._scheduler: WorkflowScheduler | None = None

    def request_stop(self) -> None:
        """Ask the worker to stop at the next safe boundary."""
        self._cancel_event.set()
        if self._scheduler is not None:
            self._scheduler.request_cancel()

    @Slot()
    def run(self) -> None:
        input_results: list[dict[str, Any]] = []
        total_inputs = 0
        request = self.request
        files = list(request.input_paths)
        if request.watch and request.watch_dir:
            files = [request.watch_dir]

        try:
            self._scheduler = WorkflowScheduler(
                ModuleManager(request.modules_dir),
                concurrency=request.concurrency,
                watch=request.watch,
                cron=request.cron or None,
            )
            if self._cancel_event.is_set():
                self._scheduler.request_cancel()
            if request.watch:
                self.log_message.emit(f"开始文件监听模式: {request.watch_dir}")
            elif request.cron:
                self.log_message.emit(f"开始定时执行: {request.cron}")
            elif request.concurrency > 1:
                self.log_message.emit(f"开始并发执行，worker 数: {request.concurrency}")
            else:
                self.log_message.emit("开始执行工作流。")

            for index in range(len(request.input_paths)):
                self.unit_status.emit(index, "processing")

            summary = self._scheduler.run(
                request.workflow_path,
                output_dir=request.output_dir,
                files=files or None,
                recurse=request.recurse,
                lines_text=request.input_text if request.input_text.strip() else None,
                direct_mode=request.direct_mode,
                enable_log=request.log_save,
                event_listener=self._on_executor_event,
                progress_callback=self._forward_progress,
            )

            input_results = [
                {
                    "input": list(files) if files else None,
                    "summary": summary,
                }
            ]
            cancelled = self._cancel_event.is_set() or summary.get("cancelled", False)
            final_status = "cancelled" if cancelled else "completed" if summary.get("success") else "failed"
            for index in range(len(files)):
                self.unit_status.emit(index, final_status)

            finished_inputs = int(summary.get("successful_units", 0)) + int(summary.get("failed_units", 0))
            total_inputs = int(summary.get("processed_units", 0))
            failed_inputs = int(summary.get("failed_units", 0))
            success = bool(summary.get("success")) and not cancelled
            self.finished.emit(
                {
                    "success": success,
                    "cancelled": cancelled,
                    "error": "",
                    "input_results": input_results,
                    "finished_inputs": finished_inputs,
                    "total_inputs": total_inputs,
                    "failed_inputs": failed_inputs,
                    "workflow_name": request.workflow_name,
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
                    "workflow_name": request.workflow_name,
                }
            )
        finally:
            self._scheduler = None

    def _on_executor_event(self, event: PipelineEvent) -> None:
        if event.slug == "terminal" and event.text.startswith("terminal:"):
            if event.text == "terminal:started":
                self.log_message.emit(f"[TOOL] 启动: {event.data.get('command', '')}")
            elif event.text == "terminal:output":
                output = str(event.data.get("text", "")).rstrip()
                if output:
                    self.log_message.emit(f"[TOOL] {output}")
            elif event.text == "terminal:finished":
                self.log_message.emit(f"[TOOL] 结束，退出码: {event.data.get('exit_code', -1)}")
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


class ExecutionController(QObject):
    """Central hub owning all GUI panels, signal wiring, and worker lifecycle.

    Create once, wire once: controller builds its panels internally and
    connects every signal to the correct slot.  MainWindow only needs to
    lay out ``.config_panel`` / ``.input_panel`` / ``.log_viewer``.
    """

    status_message = Signal(str)

    def __init__(
        self,
        workflows_dir: Path,
        modules_dir: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._workflows_dir = workflows_dir
        self._modules_dir = modules_dir

        self.config_panel = ConfigPanel(workflows_dir)
        self.input_panel = InputPanel()
        self.log_viewer = LogViewer()

        self._worker: ExecutionWorker | None = None
        self._worker_thread: QThread | None = None

        self._wire_signals()
        self._load_initial_workflows()

    # ------------------------------------------------------------------
    # Internal signal wiring (one-time, never disconnected)
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        cp = self.config_panel
        ip = self.input_panel

        cp.workflow_changed.connect(self._on_workflow_changed)
        cp.output_dir_changed.connect(self._on_config_changed)
        cp.watch_state_changed.connect(self._on_watch_state_changed)
        cp.watch_dir_changed.connect(self._on_config_changed)
        cp.cron_changed.connect(self._on_config_changed)
        cp.concurrency_changed.connect(self._on_config_changed)
        cp.log_save_changed.connect(self._on_log_save_changed)
        cp.execute_requested.connect(self._start)
        cp.stop_requested.connect(self.stop)
        cp.refresh_requested.connect(self._reload_workflows)
        cp.edit_requested.connect(self._open_editor)

        ip.paths_changed.connect(self._on_config_changed)
        ip.status_message.connect(self.status_message.emit)
        ip.warning.connect(lambda title, msg: self.status_message.emit(f"{title}: {msg}"))

    def _load_initial_workflows(self) -> None:
        self.config_panel.load_workflows()

    # ------------------------------------------------------------------
    # Input mode synchronisation
    # ------------------------------------------------------------------

    def _sync_input_mode(self) -> None:
        """Determine effective atom from workflow + watch state → input panel."""
        if self.config_panel.is_watch_enabled():
            self.input_panel.set_atom("none", False)
            return

        workflow = self.config_panel.get_current_workflow()
        if workflow is None:
            self.input_panel.set_atom("none", False)
            return

        self.input_panel.set_atom(workflow.atom, workflow.recurse)

    # ------------------------------------------------------------------
    # Event handlers for upstream signals
    # ------------------------------------------------------------------

    def _on_workflow_changed(self, workflow: WorkflowDefinition | None) -> None:
        if workflow is not None:
            self.status_message.emit(f"已选择工作流: {workflow.meta.name}")
        else:
            self.status_message.emit("选择或新建工作流以开始执行")

        self._sync_input_mode()
        self._refresh_execute_button()

    def _on_watch_state_changed(self, enabled: bool) -> None:
        self._sync_input_mode()
        self._refresh_execute_button()

    def _on_config_changed(self, _value: object = None) -> None:
        self._refresh_execute_button()

    def _on_log_save_changed(self, enabled: bool) -> None:
        self.status_message.emit(f"日志保存{'已启用' if enabled else '已禁用'}")

    # ------------------------------------------------------------------
    # Button mutual-exclusion logic
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.isRunning()

    def _refresh_execute_button(self) -> None:
        """Mutual exclusion: when idle, execute depends on prerequisites."""
        if self.is_running:
            return
        self.config_panel.execute_button.setEnabled(self._can_start_execution())
        self.config_panel.stop_button.setEnabled(False)

    def _can_start_execution(self) -> bool:
        """Lightweight prerequisite check — no dialogs, just True/False."""
        workflow = self.config_panel.get_current_workflow()
        if workflow is None:
            return False

        if self.config_panel.is_watch_enabled():
            output_dir = self.config_panel.get_output_dir()
            if not output_dir and not self.config_panel.is_direct_mode():
                return False
            return bool(self.config_panel.get_watch_dir())

        atom = self.input_panel.current_atom
        output_dir = self.config_panel.get_output_dir()

        if not output_dir:
            if not self.config_panel.is_direct_mode() or atom in {"none", "line"}:
                return False
        return self.input_panel.has_input()

    # ------------------------------------------------------------------
    # Execution start / stop
    # ------------------------------------------------------------------

    def _start(self) -> None:
        workflow = self.config_panel.get_current_workflow()
        summary = self.config_panel.get_selected_summary()
        if workflow is None or summary is None:
            self._show_validation_error("无法执行", "请先选择一个有效工作流。")
            return

        output_dir = self._resolve_output_dir()
        if not output_dir:
            self._show_validation_error("无法执行", "请先选择产物目录。")
            return

        input_paths = self.input_panel.get_files()
        input_text = self.input_panel.get_lines()
        atom = self.input_panel.current_atom
        watch_enabled = self.config_panel.is_watch_enabled()
        watch_dir = self.config_panel.get_watch_dir() if watch_enabled else ""

        if watch_enabled:
            if not watch_dir:
                self._show_validation_error("无法执行", "请选择要监听的目录。")
                return
        elif atom in {"file", "folder"} and not input_paths:
            self._show_validation_error("无法执行", "当前工作流需要至少一个路径输入。")
            return
        elif atom == "line" and not input_text.strip():
            self._show_validation_error("无法执行", "请输入至少一行文本任务。")
            return
        elif not self.input_panel.has_input():
            self._show_validation_error("无法执行", "请提供当前输入模式所需的数据。")
            return

        self.log_viewer.append_message(
            f"准备执行工作流: {workflow.meta.name} (直接模式: {self.config_panel.is_direct_mode()})"
        )

        self.input_panel.reset_unit_badges()

        worker = ExecutionWorker(
            GuiExecutionRequest(
                workflow_path=summary.path,
                workflow_name=workflow.meta.name,
                recurse=workflow.recurse,
                input_paths=tuple(input_paths),
                input_text=input_text,
                output_dir=output_dir,
                direct_mode=self.config_panel.is_direct_mode(),
                modules_dir=self._modules_dir,
                log_save=self.config_panel.is_log_save_enabled(),
                concurrency=self.config_panel.get_concurrency(),
                watch=watch_enabled,
                watch_dir=watch_dir,
                cron=self.config_panel.get_cron(),
            )
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.log_message.connect(self.log_viewer.append_message)
        worker.progress_changed.connect(self._handle_progress)
        worker.unit_status.connect(self.input_panel.set_unit_status)
        worker.finished.connect(self._handle_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup_worker)
        thread.finished.connect(thread.deleteLater)

        self._worker = worker
        self._worker_thread = thread
        self._set_widgets_running(True)
        self.status_message.emit("正在执行...")
        thread.start()

    def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.request_stop()
        self.log_viewer.append_message("已发送停止请求，等待当前安全边界退出。")
        self.status_message.emit("正在停止执行...")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_output_dir(self) -> str:
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

    def _set_widgets_running(self, running: bool) -> None:
        self.config_panel.set_running(running)
        self.input_panel.set_running(running)

    def _handle_progress(self, payload: dict[str, Any]) -> None:
        status = str(payload.get("status", "running"))
        input_index = int(payload.get("input_index", 0))
        input_total = int(payload.get("input_total", 0))
        unit = payload.get("unit") or payload.get("input_path") or "<none>"
        self.status_message.emit(f"状态: {status} | 输入 {input_index}/{input_total} | 当前单元: {unit}")

    def _handle_finished(self, summary: dict[str, Any]) -> None:
        self._set_widgets_running(False)
        self._refresh_execute_button()

        error_msg = summary.get("error")
        if error_msg:
            self.status_message.emit("执行失败")
            self.log_viewer.append_message(f"[ERROR] {error_msg}")
            self._show_error_dialog("执行失败", str(error_msg))
            return

        if summary.get("cancelled"):
            self.status_message.emit("执行已取消")
            self.log_viewer.append_message(
                f"执行已取消，已完成 {summary.get('finished_inputs', 0)} / {summary.get('total_inputs', 0)} 个输入。"
            )
            return

        if summary.get("success"):
            self.status_message.emit("执行完成")
            self.log_viewer.append_message(
                f"执行完成：工作流 {summary.get('workflow_name', '')} "
                f"处理了 {summary.get('finished_inputs', 0)} 个输入。"
            )
            return

        self.status_message.emit("执行结束，存在失败项")
        self.log_viewer.append_message(
            f"执行结束：失败输入 {summary.get('failed_inputs', 0)} / {summary.get('finished_inputs', 0)}。"
        )

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._worker_thread = None

    def _show_validation_error(self, title: str, message: str) -> None:
        parent_widget = self.config_panel.window()
        if parent_widget:
            QMessageBox.warning(parent_widget, title, message)

    def _show_error_dialog(self, title: str, message: str) -> None:
        parent_widget = self.config_panel.window()
        if parent_widget:
            QMessageBox.critical(parent_widget, title, message)

    # ------------------------------------------------------------------
    # Workflow management
    # ------------------------------------------------------------------

    def _reload_workflows(self) -> None:
        selected_summary = self.config_panel.get_selected_summary()
        selected_path = selected_summary.path if selected_summary else None
        self.config_panel.load_workflows(selected_path)

    def _open_editor(self, workflow: WorkflowDefinition | None) -> None:
        from gui.workflow_editor import WorkflowEditor

        module_manager = ModuleManager(self._modules_dir)
        editor = WorkflowEditor(
            workflow_store=WorkflowAuthoringStore(self._workflows_dir),
            module_manager=module_manager,
            workflow=workflow,
            parent=self.config_panel.window(),
        )
        editor.workflow_saved.connect(lambda _saved_path: self._reload_workflows())
        editor.show()
