"""Main application window for running workflows with background execution."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import (
    InputInspector,
    ModuleManager,
    PipelineEvent,
    PipelineExecutor,
    WorkflowDefinition,
    WorkflowLoader,
    WorkflowSummary,
)
from gui.widgets import DropZoneWidget
from gui.widgets.terminal_window import TerminalWindow
from gui.workflow_editor import WorkflowEditor


class UIColors:
    WARNING = "#e67e22"
    SUCCESS = "#27ae60"
    ERROR = "#e74c3c"
    MUTED = "#95a5a6"
    INFO = "#7f8c8d"
    DARK = "#2c3e50"


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
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.input_paths = list(input_paths)
        self.input_text = input_text
        self.output_dir = output_dir
        self.direct_mode = direct_mode
        self.modules_dir = modules_dir
        self._cancel_event = Event()
        self._current_input_index = 0
        self._current_input_path: str | None = None

    def request_stop(self) -> None:
        """Ask the worker to stop at the next safe boundary."""
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        input_results: list[dict[str, Any]] = []
        total_inputs = max(len(self.input_paths), 1)

        try:
            executor = PipelineExecutor(
                module_manager=self._build_module_manager(),
                event_callback=self._on_executor_event,
                progress_callback=self._forward_progress,
                cancel_requested=self._cancel_event.is_set,
            )

            if self.workflow.mode == "none":
                self._current_input_index = 0
                self._current_input_path = None
                input_results.append(
                    {
                        "input": None,
                        "summary": executor.execute(
                            self.workflow,
                            output_dir=self.output_dir,
                            direct_mode=self.direct_mode,
                        ),
                    }
                )
            elif self.workflow.mode == "input":
                self._current_input_index = 0
                self._current_input_path = None
                self.log_message.emit("开始处理文本输入行")
                result = executor.execute(
                    self.workflow,
                    output_dir=self.output_dir,
                    input_text=self.input_text,
                    direct_mode=self.direct_mode,
                )
                input_results.append({"input": None, "summary": result})
            elif self.workflow.mode == "cycle":
                self._current_input_index = 0
                self._current_input_path = (
                    self.input_paths[0] if len(self.input_paths) == 1 else None
                )
                for index in range(len(self.input_paths)):
                    self.unit_status.emit(index, "processing")
                self.log_message.emit(
                    f"开始处理循环输入，共 {len(self.input_paths)} 个输入。"
                )
                result = executor.execute(
                    self.workflow,
                    input_paths=self.input_paths,
                    output_dir=self.output_dir,
                    direct_mode=self.direct_mode,
                )
                input_results.append({"input": list(self.input_paths), "summary": result})
                final_status = (
                    "cancelled"
                    if result.get("cancelled")
                    else "completed"
                    if result.get("success")
                    else "failed"
                )
                for index in range(len(self.input_paths)):
                    self.unit_status.emit(index, final_status)
            else:
                for index, input_path in enumerate(self.input_paths, start=1):
                    if self._cancel_event.is_set():
                        break

                    self._current_input_index = index - 1
                    self._current_input_path = input_path
                    self.unit_status.emit(index - 1, "processing")
                    self.log_message.emit(
                        f"开始处理输入 [{index}/{len(self.input_paths)}]: {input_path}"
                    )
                    result = executor.execute(
                        self.workflow,
                        input_path=input_path,
                        output_dir=self.output_dir,
                        direct_mode=self.direct_mode,
                    )
                    input_results.append({"input": input_path, "summary": result})
                    if result.get("cancelled"):
                        self.unit_status.emit(index - 1, "cancelled")
                        break
                    elif result.get("success"):
                        self.unit_status.emit(index - 1, "completed")
                    else:
                        self.unit_status.emit(index - 1, "failed")

            cancelled = self._cancel_event.is_set() or any(
                item["summary"].get("cancelled", False) for item in input_results
            )
            if self.workflow.mode == "cycle":
                failed_inputs = 0 if not input_results or input_results[0]["summary"].get("success", False) else total_inputs
                finished_inputs = total_inputs if input_results else 0
            else:
                failed_inputs = sum(
                    1
                    for item in input_results
                    if not item["summary"].get("success", False)
                )
                finished_inputs = len(input_results)
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

    def _build_module_manager(self) -> ModuleManager:
        return ModuleManager(self.modules_dir)

    def _on_executor_event(self, event: PipelineEvent) -> None:
        if event.slug == "terminal":
            if event.text.startswith("terminal:"):
                self.terminal_event.emit(
                    {"type": event.text, **event.data}
                )
            return  # suppress terminal events from the log panel

        prefix = {
            "success": "[OK]", "message": "[INFO]", "hint": "[HINT]",
            "warning": "[WARN]", "error": "[ERROR]",
        }.get(event.type, "[LOG]")
        self.log_message.emit(f"{prefix} [{event.slug}] {event.text}")

    def _forward_progress(self, payload: dict[str, Any]) -> None:
        total_inputs = max(len(self.input_paths), 1)
        if self.workflow.mode == "cycle":
            forwarded = dict(payload)
        elif total_inputs == 1:
            forwarded = dict(payload)
        else:
            input_percent = float(payload.get("percent", 0))
            overall_percent = int(
                ((self._current_input_index + input_percent / 100.0) / total_inputs) * 100
            )
            forwarded = dict(payload)
            forwarded["percent"] = overall_percent

        forwarded["input_index"] = 1 if self.workflow.mode == "cycle" else self._current_input_index + 1
        forwarded["input_total"] = total_inputs
        forwarded["input_path"] = self._current_input_path
        self.progress_changed.emit(forwarded)


class MainWindow(QMainWindow):
    """Desktop window for selecting workflows and running them safely."""

    def __init__(self, project_dir: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_dir = Path(project_dir).resolve()
        self.workflows_dir = self.project_dir / "workflows"
        self.modules_dir = self.project_dir / "modules"
        self.workflow_loader = WorkflowLoader(self.workflows_dir)
        self._settings = QSettings("ShellWorker", "ShellWorker")

        self._restored_workflow_path: Path | None = None
        self._current_workflow: WorkflowDefinition | None = None
        self._worker_thread: QThread | None = None
        self._worker: ExecutionWorker | None = None
        self._direct_mode: bool = False
        self._terminal_windows: dict[str, TerminalWindow] = {}
        self._editor_window: WorkflowEditor | None = None
        self._cached_module_manager: ModuleManager | None = None
        self._log_buffer: list[str] = []
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(50)
        self._log_timer.timeout.connect(self._flush_log_buffer)

        self.setWindowTitle("Shell Worker Platform")
        self.resize(680, 920)

        self._build_ui()
        self._restore_settings()
        self._bind_signals()
        self._reload_workflows()
        self.statusBar().showMessage("就绪")

    def _build_ui(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)

        # === 执行配置区 ===
        config_group = QGroupBox("执行配置")
        config_layout = QVBoxLayout(config_group)

        workflow_row = QHBoxLayout()
        workflow_label = QLabel("工作流")
        self.workflow_combo = QComboBox()
        self.refresh_workflows_button = QPushButton("刷新")
        self.editor_button = QPushButton("编辑")
        workflow_row.addWidget(workflow_label)
        workflow_row.addWidget(self.workflow_combo, stretch=1)
        workflow_row.addWidget(self.refresh_workflows_button)
        workflow_row.addWidget(self.editor_button)
        config_layout.addLayout(workflow_row)

        self.workflow_mode_label = QLabel("模式：-")
        self.workflow_steps_label = QLabel("步骤：-")
        self.workflow_desc_label = QLabel("")
        self.workflow_desc_label.setWordWrap(True)
        config_layout.addWidget(self.workflow_mode_label)
        config_layout.addWidget(self.workflow_steps_label)
        config_layout.addWidget(self.workflow_desc_label)

        # --- 运行模式 ---
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("运行模式"))
        self.mode_copy_radio = QRadioButton("拷贝模式")
        self.mode_direct_radio = QRadioButton("直接模式")
        self.mode_copy_radio.setChecked(True)
        self.mode_copy_radio.setToolTip("安全模式：将文件复制到产物目录后操作副本，不修改原文件。")
        self.mode_direct_radio.setToolTip("危险模式：直接在原始文件上操作，不会创建副本。")
        self._mode_button_group = QButtonGroup(self)
        self._mode_button_group.addButton(self.mode_copy_radio, 0)
        self._mode_button_group.addButton(self.mode_direct_radio, 1)
        self._mode_button_group.buttonClicked.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_copy_radio)
        mode_row.addWidget(self.mode_direct_radio)
        mode_row.addStretch(1)
        config_layout.addLayout(mode_row)

        self._direct_warning_label = QLabel(
            f'<span style="color:{UIColors.WARNING};font-weight:bold;">'
            '警告：将直接修改原始文件！</span>'
        )
        self._direct_warning_label.setVisible(False)
        config_layout.addWidget(self._direct_warning_label)

        # --- 产物目录 ---
        output_row = QHBoxLayout()
        output_label = QLabel("产物目录")
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setPlaceholderText("选择或输入产物目录")
        self.output_dir_button = QPushButton("浏览")
        output_row.addWidget(output_label)
        output_row.addWidget(self.output_dir_input, stretch=1)
        output_row.addWidget(self.output_dir_button)
        config_layout.addLayout(output_row)

        # --- 执行 / 停止 ---
        action_row = QHBoxLayout()
        self.execute_button = QPushButton("执行")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        action_row.addStretch(1)
        action_row.addWidget(self.execute_button)
        action_row.addWidget(self.stop_button)
        config_layout.addLayout(action_row)

        root_layout.addWidget(config_group)

        # === 输入区 ===
        root_layout.addWidget(self._build_input_panel())

        # === 日志区 ===
        root_layout.addWidget(self._build_log_panel(), stretch=1)

        self.setCentralWidget(central)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(260)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _restore_settings(self) -> None:
        saved_dir = self._settings.value("output_dir", "")
        if saved_dir:
            self.output_dir_input.setText(saved_dir)

        restored_path = self._settings.value("last_workflow_path", "")
        if restored_path:
            self._restored_workflow_path = Path(restored_path)

    def _build_input_panel(self) -> QWidget:
        container = QGroupBox("输入")
        layout = QVBoxLayout(container)

        self.input_hint_label = QLabel("拖入文件或文件夹，或使用下方按钮添加输入。")
        self.input_hint_label.setWordWrap(True)
        layout.addWidget(self.input_hint_label)

        self.drop_zone = DropZoneWidget()
        layout.addWidget(self.drop_zone)

        self.input_text_editor = QPlainTextEdit()
        self.input_text_editor.setPlaceholderText("每行一个任务，空行自动忽略。")
        self.input_text_editor.setMinimumHeight(100)
        self.input_text_editor.hide()
        layout.addWidget(self.input_text_editor)

        button_row = QHBoxLayout()
        self.add_files_button = QPushButton("添加文件")
        self.add_folder_button = QPushButton("添加文件夹")
        self.remove_input_button = QPushButton("移除选中")
        self.clear_inputs_button = QPushButton("清空")
        button_row.addWidget(self.add_files_button)
        button_row.addWidget(self.add_folder_button)
        button_row.addStretch(1)
        button_row.addWidget(self.remove_input_button)
        button_row.addWidget(self.clear_inputs_button)
        layout.addLayout(button_row)

        self.input_list = QListWidget()
        layout.addWidget(self.input_list, stretch=1)
        return container

    def _build_log_panel(self) -> QWidget:
        container = QGroupBox("日志")
        layout = QVBoxLayout(container)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("执行日志会显示在这里。")
        self.log_output.document().setMaximumBlockCount(5000)
        layout.addWidget(self.log_output, stretch=1)
        return container

    def _bind_signals(self) -> None:
        self.refresh_workflows_button.clicked.connect(self._reload_workflows)
        self.editor_button.clicked.connect(self._open_workflow_editor)
        self.workflow_combo.currentIndexChanged.connect(self._on_workflow_changed)
        self.output_dir_button.clicked.connect(self._choose_output_dir)
        self.output_dir_input.textChanged.connect(self._on_output_dir_changed)
        self.add_files_button.clicked.connect(self._choose_files)
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_input_button.clicked.connect(self._remove_selected_inputs)
        self.clear_inputs_button.clicked.connect(self._clear_inputs)
        self.drop_zone.paths_dropped.connect(self._add_input_paths)
        self.execute_button.clicked.connect(self._start_execution)
        self.stop_button.clicked.connect(self._request_stop)

    def _on_mode_changed(self, button: QAbstractButton | None = None) -> None:
        self._direct_mode = self.mode_direct_radio.isChecked()
        self._direct_warning_label.setVisible(self._direct_mode)
        self._update_mode_styling()
        self._update_action_buttons()

    def _update_mode_styling(self) -> None:
        """Apply visual warning indicators when in direct mode."""
        # The input list gets a warning border in direct mode
        pass  # reserved for future styling

    def _reload_workflows(self) -> None:
        selected_path = self._restored_workflow_path or self._selected_workflow_path()
        self._restored_workflow_path = None
        summaries = self.workflow_loader.list_workflows(include_invalid=True)

        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for summary in summaries:
            label = self._build_workflow_label(summary)
            self.workflow_combo.addItem(label, summary)
        self.workflow_combo.blockSignals(False)

        if not summaries:
            self._current_workflow = None
            self.workflow_mode_label.setText("模式：-")
            self.workflow_steps_label.setText("未发现可用工作流")
            self.workflow_desc_label.setText("请在 `workflows/` 目录下添加 YAML 文件。")
            self._update_input_controls()
            self._update_action_buttons()
            return

        restored_index = 0
        first_valid_index = -1
        for index, summary in enumerate(summaries):
            if first_valid_index < 0 and summary.is_valid:
                first_valid_index = index
            if selected_path is not None and summary.path == selected_path:
                restored_index = index
                break
        else:
            if selected_path is None:
                restored_index = first_valid_index if first_valid_index >= 0 else 0

        self.workflow_combo.setCurrentIndex(restored_index)
        self._on_workflow_changed(restored_index)

    def _build_workflow_label(self, summary: WorkflowSummary) -> str:
        if summary.is_valid:
            return f"{summary.name} ({summary.filename})"
        return f"[无效] {summary.filename}"

    def _selected_workflow_path(self) -> Path | None:
        summary = self.workflow_combo.currentData()
        if isinstance(summary, WorkflowSummary):
            return summary.path
        return None

    def _on_workflow_changed(self, _index: int) -> None:
        summary = self.workflow_combo.currentData()
        self._current_workflow = None

        if not isinstance(summary, WorkflowSummary):
            self.workflow_mode_label.setText("模式：-")
            self.workflow_steps_label.setText("步骤：-")
            self.workflow_desc_label.setText("")
            self._update_input_controls()
            self._update_action_buttons()
            return

        if not summary.is_valid:
            details = "；".join(summary.errors) if summary.errors else "未知校验错误"
            self.workflow_mode_label.setText("模式：无效")
            self.workflow_steps_label.setText(summary.filename)
            self.workflow_desc_label.setText(f"错误：{details}")
            self._update_input_controls()
            self._update_action_buttons()
            return

        try:
            workflow = self.workflow_loader.load(summary.path)
        except Exception as exc:
            self.workflow_mode_label.setText("模式：-")
            self.workflow_steps_label.setText(f"加载失败：{exc}")
            self.workflow_desc_label.setText("")
            self._update_input_controls()
            self._update_action_buttons()
            return

        self._current_workflow = workflow
        self._settings.setValue("last_workflow_path", str(summary.path))
        self.workflow_mode_label.setText(f"模式：{workflow.mode}")
        self.workflow_steps_label.setText(f"步骤数：{len(workflow.steps)} | 文件：{summary.filename}")
        self.workflow_desc_label.setText(workflow.meta.description or "暂无描述")
        self._update_input_controls()
        self._update_action_buttons()
        self.statusBar().showMessage(f"已选择工作流: {workflow.meta.name}")

    def _open_workflow_editor(self) -> None:
        if self._editor_window is not None:
            self._editor_window.close()
            self._editor_window = None
        if self._cached_module_manager is None:
            self._cached_module_manager = ModuleManager(self.modules_dir)
        else:
            self._cached_module_manager.rescan_modules()
        module_manager = self._cached_module_manager
        workflow = self._current_workflow
        self._editor_window = WorkflowEditor(
            workflow_loader=self.workflow_loader,
            module_manager=module_manager,
            workflow=workflow,
            parent=self,
        )
        self._editor_window.workflow_saved.connect(self._reload_workflows)
        self._editor_window.show()

    def _current_mode(self) -> str | None:
        if self._current_workflow is None:
            return None
        return self._current_workflow.mode

    def _update_input_controls(self) -> None:
        mode = self._current_mode()
        is_file_input = mode in {"file", "cycle"}
        is_folder_input = mode in {"file", "folder", "cycle"}
        requires_file_input = is_file_input or is_folder_input
        is_text_input = mode == "input"

        self.drop_zone.setVisible(requires_file_input)
        self.input_list.setVisible(requires_file_input)
        self.input_list.setEnabled(requires_file_input)
        self.remove_input_button.setVisible(requires_file_input)
        self.remove_input_button.setEnabled(requires_file_input)
        self.clear_inputs_button.setVisible(requires_file_input)
        self.clear_inputs_button.setEnabled(requires_file_input)
        self.add_files_button.setVisible(requires_file_input)
        self.add_files_button.setEnabled(is_file_input)
        self.add_folder_button.setVisible(requires_file_input)
        self.add_folder_button.setEnabled(is_folder_input)

        self.input_text_editor.setVisible(is_text_input)
        self.input_text_editor.setEnabled(is_text_input)

        if mode == "file":
            self.input_hint_label.setText("当前模式支持文件或文件夹输入。")
        elif mode == "folder":
            self.input_hint_label.setText("当前模式仅支持文件夹输入。")
        elif mode == "cycle":
            self.input_hint_label.setText("循环模式：支持文件或文件夹输入，所有文件共用上下文。")
        elif mode == "input":
            self.input_hint_label.setText("文本输入模式：每行一个任务，空行自动忽略，各任务独立上下文。")
        else:
            self.input_hint_label.setText("当前工作流不需要输入路径。")
            self._clear_inputs()

    def _update_action_buttons(self) -> None:
        running = self._worker_thread is not None and self._worker_thread.isRunning()
        self.execute_button.setEnabled(not running and self._can_start_execution())
        self.stop_button.setEnabled(running)

    def _can_start_execution(self) -> bool:
        if self._current_workflow is None:
            return False
        if not self.output_dir_input.text().strip():
            if not self._direct_mode or self._current_workflow.mode in ("none", "input"):
                return False
        mode = self._current_workflow.mode
        if mode in {"file", "folder", "cycle"} and self.input_list.count() == 0:
            return False
        if mode == "input" and not self.input_text_editor.toPlainText().strip():
            return False
        return True

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择产物目录")
        if selected:
            path = str(Path(selected).resolve())
            self.output_dir_input.setText(path)
            self._settings.setValue("output_dir", path)
            self._update_action_buttons()

    def _on_output_dir_changed(self, text: str) -> None:
        self._settings.setValue("output_dir", text)
        self._update_action_buttons()

    def _choose_files(self) -> None:
        if self._current_mode() not in {"file", "cycle"}:
            return
        selected, _ = QFileDialog.getOpenFileNames(self, "选择输入文件")
        if selected:
            self._add_input_paths(selected)

    def _choose_folder(self) -> None:
        if self._current_mode() not in {"file", "folder", "cycle"}:
            return
        selected = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if selected:
            self._add_input_paths([selected])

    def _add_input_paths(self, paths: list[str]) -> None:
        mode = self._current_mode()
        if mode not in {"file", "folder", "cycle"}:
            self.statusBar().showMessage("当前工作流不需要输入。")
            return

        added_count = 0
        invalid_paths: list[str] = []
        existing = {
            self.input_list.item(index).data(Qt.UserRole)
            for index in range(self.input_list.count())
        }

        if mode == "folder":
            for raw_path in paths:
                p = Path(raw_path).resolve()
                result = InputInspector.validate_folder_input(p)
                if not result.is_valid:
                    invalid_paths.append(f"{result.path}: {result.error}")
                    continue
                normalized = str(p)
                if normalized in existing:
                    continue
                item = QListWidgetItem(f"[等待] {normalized}")
                item.setData(Qt.UserRole, normalized)
                self.input_list.addItem(item)
                existing.add(normalized)
                added_count += 1
        else:
            resolved_paths = [Path(raw_path).resolve() for raw_path in paths]
            valid, invalid = InputInspector.validate_path_input(resolved_paths)
            for inv in invalid:
                invalid_paths.append(f"{inv.path}: {inv.error}")
            for v in valid:
                normalized = str(v)
                if normalized in existing:
                    continue
                item = QListWidgetItem(f"[等待] {normalized}")
                item.setData(Qt.UserRole, normalized)
                self.input_list.addItem(item)
                existing.add(normalized)
                added_count += 1

        if added_count:
            self.statusBar().showMessage(f"已添加 {added_count} 个输入。")
        if invalid_paths:
            QMessageBox.warning(
                self, "部分输入未添加",
                f"以下 {len(invalid_paths)} 个输入无效已跳过：\n" + "\n".join(invalid_paths[:10])
                + ("\n…" if len(invalid_paths) > 10 else ""),
            )
        self._update_action_buttons()

    def _remove_selected_inputs(self) -> None:
        rows = sorted(
            (self.input_list.row(item) for item in self.input_list.selectedItems()),
            reverse=True,
        )
        for row in rows:
            self.input_list.takeItem(row)
        self._update_action_buttons()

    def _clear_inputs(self) -> None:
        self.input_list.clear()
        self.input_text_editor.clear()
        self._update_action_buttons()

    def _collect_inputs(self) -> list[str]:
        return [
            self.input_list.item(index).data(Qt.UserRole)
            for index in range(self.input_list.count())
        ]

    def _resolve_output_dir(self) -> str:
        """Determine effective output_dir."""
        text = self.output_dir_input.text().strip()
        if text:
            return str(Path(text).resolve())

        if self._direct_mode and self._current_workflow:
            mode = self._current_workflow.mode
            if mode in {"file", "folder", "cycle"}:
                inputs = self._collect_inputs()
                if inputs:
                    return str(Path(inputs[0]).parent)
        return ""

    def _start_execution(self) -> None:
        self.execute_button.setEnabled(False)
        workflow = self._current_workflow

        if workflow is None:
            QMessageBox.warning(self, "无法执行", "请先选择一个有效工作流。")
            return

        output_dir = self._resolve_output_dir()
        if not output_dir:
            QMessageBox.warning(self, "无法执行", "请先选择产物目录。")
            return

        input_paths = self._collect_inputs()
        input_text = self.input_text_editor.toPlainText()
        if workflow.mode in {"file", "folder", "cycle"} and not input_paths:
            QMessageBox.warning(self, "无法执行", "当前工作流模式需要至少一个输入。")
            return
        if workflow.mode == "input" and not input_text.strip():
            QMessageBox.warning(self, "无法执行", "请输入至少一行文本任务。")
            return

        self.output_dir_input.setText(output_dir)
        self.log_output.clear()
        self.progress_bar.setValue(0)
        self._append_log(f"准备执行工作流: {workflow.meta.name} (直接模式: {self._direct_mode})")

        for idx in range(self.input_list.count()):
            item = self.input_list.item(idx)
            if item:
                path = item.data(Qt.UserRole)
                item.setText(f"[等待] {path}")

        worker = ExecutionWorker(
            workflow=workflow,
            input_paths=input_paths,
            input_text=input_text,
            output_dir=output_dir,
            direct_mode=self._direct_mode,
            modules_dir=str(self.modules_dir),
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
        mode = self._current_mode()
        is_file_mode = mode in {"file", "cycle"}
        is_folder_mode = mode in {"file", "folder", "cycle"}
        requires_file_input = is_file_mode or is_folder_mode

        self.workflow_combo.setEnabled(not running)
        self.refresh_workflows_button.setEnabled(not running)
        self.editor_button.setEnabled(not running)
        self.output_dir_input.setEnabled(not running)
        self.output_dir_button.setEnabled(not running)
        self.mode_copy_radio.setEnabled(not running)
        self.mode_direct_radio.setEnabled(not running)
        self.add_files_button.setEnabled(not running and is_file_mode)
        self.add_folder_button.setEnabled(not running and is_folder_mode)
        self.remove_input_button.setEnabled(not running and self.input_list.isEnabled())
        self.clear_inputs_button.setEnabled(not running and self.input_list.isEnabled())
        self.drop_zone.set_drop_enabled(not running and requires_file_input)
        self.input_list.setEnabled(not running and requires_file_input)
        self.input_text_editor.setEnabled(not running and mode == "input")
        self.execute_button.setEnabled(not running and self._can_start_execution())
        self.stop_button.setEnabled(running)

    def _append_log(self, message: str) -> None:
        self._log_buffer.append(message)
        if not self._log_timer.isActive():
            self._log_timer.start()

    def _flush_log_buffer(self) -> None:
        buffer = self._log_buffer
        self._log_buffer = []
        for message in buffer:
            self._render_log_message(message)

    def _render_log_message(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if message.startswith("[ERROR]"):
            accent = UIColors.ERROR
            extra_style = "font-weight:bold;"
        elif message.startswith("[WARN]"):
            accent = UIColors.WARNING
            extra_style = "font-weight:bold;"
        elif message.startswith("[OK]"):
            accent = UIColors.SUCCESS
            extra_style = ""
        elif message.startswith("[HINT]"):
            accent = UIColors.MUTED
            extra_style = "font-style:italic;"
        elif message.startswith("[INFO]"):
            accent = UIColors.INFO
            extra_style = ""
        else:
            accent = UIColors.DARK
            extra_style = ""

        html = (
            f'<div style="margin:0;padding:1px 6px;white-space:pre-wrap;'
            f'border-left:3px solid {accent};">'
            f'<span style="color:#95a5a6;font-size:9pt;">[{timestamp}]</span> '
            f'<span style="color:{accent};{extra_style}">{escaped}</span>'
            f'</div>'
        )
        self.log_output.append(html)
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum()
        )

    def _on_terminal_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type", "")
        session_id = payload.get("session_id", "")

        if event_type == "terminal:started":
            command = payload.get("command", "")
            win = TerminalWindow(session_id, command, parent=self)
            win.destroyed.connect(
                lambda sid=session_id: self._terminal_windows.pop(sid, None)
            )
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
        input_index = int(payload.get("input_index", 1))
        input_total = int(payload.get("input_total", 1))
        unit = payload.get("unit") or payload.get("input_path") or "<none>"

        self.progress_bar.setValue(max(0, min(percent, 100)))
        self.statusBar().showMessage(
            f"状态: {status} | 输入 {input_index}/{input_total} | 当前单元: {unit}"
        )

    def _on_unit_status(self, row: int, status: str) -> None:
        if row < 0 or row >= self.input_list.count():
            return
        item = self.input_list.item(row)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        badge = {
            "processing": "[处理中]",
            "completed": "[完成]",
            "failed": "[失败]",
            "cancelled": "[取消]",
        }.get(status, "[等待]")
        item.setText(f"{badge} {path}")

    def _handle_finished(self, summary: dict[str, Any]) -> None:
        self._set_running_state(False)

        if summary.get("error"):
            self.progress_bar.setValue(
                0 if summary.get("cancelled") else self.progress_bar.value()
            )
            self.statusBar().showMessage("执行失败")
            QMessageBox.critical(self, "执行失败", str(summary["error"]))
            return

        if summary.get("cancelled"):
            self.statusBar().showMessage("执行已取消")
            self._append_log(
                f"执行已取消，已完成 {summary.get('finished_inputs', 0)} / "
                f"{summary.get('total_inputs', 0)} 个输入。"
            )
            return

        if summary.get("success"):
            self.progress_bar.setValue(100)
            self.statusBar().showMessage("执行完成")
            self._append_log(
                f"执行完成：工作流 {summary.get('workflow_name', '')} "
                f"处理了 {summary.get('finished_inputs', 0)} 个输入。"
            )
            return

        self.statusBar().showMessage("执行结束，存在失败项")
        self._append_log(
            f"执行结束：失败输入 {summary.get('failed_inputs', 0)} / "
            f"{summary.get('finished_inputs', 0)}。"
        )

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._worker_thread = None
        self._update_action_buttons()
