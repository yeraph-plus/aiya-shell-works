"""Execution controller: manages worker lifecycle, terminal windows, and state.

Extracted from MainWindow to keep the window focused on layout assembly
and signal routing between high-level widgets.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from core import (
    ModuleManager,
    PipelineEvent,
    PipelineExecutor,
    PipelineRuntime,
    WorkflowDefinition,
)

from .config_panel import ConfigPanel
from .input_panel import InputPanel
from .log_viewer import LogViewer
from .terminal_window import TerminalWindow


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
        if self.runtime is not None:
            self.runtime.request_cancel()

    @Slot()
    def run(self) -> None:
        input_results: list[dict[str, Any]] = []
        total_inputs = 0

        try:
            log_file = None
            if self.log_save and self.output_dir:
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


class ExecutionController(QObject):
    """Owns execution worker lifecycle, terminal windows, and execution state."""

    log_message = Signal(str)
    status_message = Signal(str)
    execution_state_changed = Signal(bool)

    def __init__(
        self,
        config_panel: ConfigPanel,
        input_panel: InputPanel,
        log_viewer: LogViewer,
        modules_dir: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_panel
        self._input = input_panel
        self._log_viewer = log_viewer
        self._modules_dir = modules_dir
        self._worker: ExecutionWorker | None = None
        self._worker_thread: QThread | None = None
        self._terminal_windows: dict[str, TerminalWindow] = {}

    @property
    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.isRunning()

    def start(self) -> None:
        workflow = self._config.get_current_workflow()
        if workflow is None:
            return

        output_dir = self._resolve_output_dir()
        if not output_dir:
            return

        input_paths = self._input.get_files()
        input_text = self._input.get_lines()
        atom = workflow.atom

        if atom in {"file", "folder"} and not input_paths:
            return
        if atom == "line" and not input_text.strip():
            return
        if atom is None and not input_paths and not input_text.strip():
            return

        self._log_viewer.append_message(
            f"准备执行工作流: {workflow.meta.name} (直接模式: {self._config.is_direct_mode()})"
        )

        self._input.reset_unit_badges()

        worker = ExecutionWorker(
            workflow=workflow,
            input_paths=input_paths,
            input_text=input_text,
            output_dir=output_dir,
            direct_mode=self._config.is_direct_mode(),
            modules_dir=self._modules_dir,
            log_save=self._config.is_log_save_enabled(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.log_message.connect(self._log_viewer.append_message)
        worker.progress_changed.connect(self._handle_progress)
        worker.unit_status.connect(self._input.set_unit_status)
        worker.terminal_event.connect(self._on_terminal_event)
        worker.finished.connect(self._handle_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup_worker)
        thread.finished.connect(thread.deleteLater)

        self._worker = worker
        self._worker_thread = thread
        self._set_widgets_running(True)
        self.execution_state_changed.emit(True)
        thread.start()

    def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.request_stop()
        self._log_viewer.append_message("已发送停止请求，等待当前安全边界退出。")
        self.status_message.emit("正在停止执行...")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_output_dir(self) -> str:
        text = self._config.get_output_dir().strip()
        if text:
            return str(Path(text).resolve())

        if self._config.is_direct_mode():
            workflow = self._config.get_current_workflow()
            if workflow and workflow.atom in {None, "file", "folder"}:
                inputs = self._input.get_files()
                if inputs:
                    return str(Path(inputs[0]).parent)
        return ""

    def _set_widgets_running(self, running: bool) -> None:
        self._config.set_running(running)
        self._input.set_running(running)

    def _handle_progress(self, payload: dict[str, Any]) -> None:
        percent = int(payload.get("percent", 0))
        status = str(payload.get("status", "running"))
        input_index = int(payload.get("input_index", 0))
        input_total = int(payload.get("input_total", 0))
        unit = payload.get("unit") or payload.get("input_path") or "<none>"

        self.status_message.emit(f"状态: {status} | 输入 {input_index}/{input_total} | 当前单元: {unit}")

    def _handle_finished(self, summary: dict[str, Any]) -> None:
        self._set_widgets_running(False)
        self.execution_state_changed.emit(False)

        if summary.get("error"):
            self.status_message.emit("执行失败")
            self.log_message.emit(f"[ERROR] {summary['error']}")
            return

        if summary.get("cancelled"):
            self.status_message.emit("执行已取消")
            self._log_viewer.append_message(
                f"执行已取消，已完成 {summary.get('finished_inputs', 0)} / {summary.get('total_inputs', 0)} 个输入。"
            )
            return

        if summary.get("success"):
            self.status_message.emit("执行完成")
            self._log_viewer.append_message(
                f"执行完成：工作流 {summary.get('workflow_name', '')} 处理了 {summary.get('finished_inputs', 0)} 个输入。"
            )
            return

        self.status_message.emit("执行结束，存在失败项")
        self._log_viewer.append_message(
            f"执行结束：失败输入 {summary.get('failed_inputs', 0)} / {summary.get('finished_inputs', 0)}。"
        )

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._worker_thread = None

    def _on_terminal_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type", "")
        session_id = payload.get("session_id", "")

        if event_type == "terminal:started":
            command = payload.get("command", "")
            runtime = self._worker.runtime if self._worker is not None else None
            win = TerminalWindow(session_id, command, runtime=runtime, parent=None)
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
