"""Main application window — atom × scope × recurse driven.

Adapted to the new core.  ExecutionWorker has a single uniform execute()
call (no per-mode branching) that uses an InputPlan naturally.  The runtime
owns event-bus + terminal-session registry, so terminal windows can find
their sessions via ``self._worker_runtime.sessions``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal, Slot
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
    PipelineRuntime,
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
    terminal_event = Signal(dict, object)  # payload + runtime reference

    def __init__(
        self,
        *,
        workflow: WorkflowDefinition,
        files: list[str],
        recurse: bool,
        lines_text: str,
        output_dir: str,
        direct_mode: bool,
        log_file: str,
        modules_dir: str,
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.files = list(files)
        self.recurse = recurse
        self.lines_text = lines_text
        self.output_dir = output_dir
        self.direct_mode = direct_mode
        self.log_file = log_file
        self.modules_dir = modules_dir
        self._cancel_event = Event()
        self._current_input_index = 0
        self._current_input_path: str | None = None
        self._runtime: PipelineRuntime | None = None

    def request_stop(self) -> None:
        self._cancel_event.set()
        if self._runtime is not None:
            self._runtime.request_cancel()

    @Slot()
    def run(self) -> None:
        runtime = PipelineRuntime(log_file=self.log_file or None)
        self._runtime = runtime
        try:
            module_manager = ModuleManager(self.modules_dir)
            executor = PipelineExecutor(
                module_manager,
                runtime=runtime,
                progress_callback=self._forward_progress,
                cancel_requested=self._cancel_event.is_set,
                event_listener=self._on_executor_event,
            )
            summary = executor.execute(
                self.workflow,
                output_dir=self.output_dir,
                files=self.files or None,
                recurse=self.recurse,
                lines_text=self.lines_text or None,
                direct_mode=self.direct_mode,
            )
            self.finished.emit(summary)
        except Exception as exc:
            self.log_message.emit(f"执行失败: {exc}")
            self.finished.emit(
                {
                    "success": False,
                    "cancelled": False,
                    "errors": [{"error": str(exc)}],
                    "error": str(exc),
                }
            )
        finally:
            runtime.close()

    def _on_executor_event(self, event: PipelineEvent) -> None:
        if event.slug == "executor":
            idx = 0
            try:
                bracket = event.text.find("[")
                slash = event.text.find("/", bracket)
                if bracket >= 0 and slash > bracket:
                    idx = int(event.text[bracket + 1 : slash]) - 1
            except (ValueError, IndexError):
                pass
            if event.text.startswith("start unit ["):
                self.unit_status.emit(idx, "processing")
            elif event.text.startswith("unit ok ["):
                self.unit_status.emit(idx, "completed")
            elif event.text.startswith("unit failed ["):
                self.unit_status.emit(idx, "failed")
        if event.slug == "terminal":
            if event.text.startswith("terminal:"):
                self.terminal_event.emit({"type": event.text, **event.data}, self._runtime)
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
        self._worker_runtime: PipelineRuntime | None = None
        self._direct_mode: bool = False
        self._terminal_windows: dict[str, TerminalWindow] = {}
        self._editor_window: WorkflowEditor | None = None
        self._cached_module_manager: ModuleManager | None = None
        self._log_buffer: list[str] = []
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(50)
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self._watchdog_timer: QTimer | None = None

        self.setWindowTitle("Shell Worker Platform")
        self.resize(680, 920)

        self._build_ui()
        self._restore_settings()
        self._bind_signals()
        self._reload_workflows()
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

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

        self.workflow_atom_label = QLabel("atom：-")
        self.workflow_steps_label = QLabel("步骤：-")
        self.workflow_desc_label = QLabel("")
        self.workflow_desc_label.setWordWrap(True)
        config_layout.addWidget(self.workflow_atom_label)
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
            f'<span style="color:{UIColors.WARNING};font-weight:bold;">警告：将直接修改原始文件！</span>'
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

        # --- 日志文件 ---
        log_row = QHBoxLayout()
        log_label = QLabel("日志文件")
        self.log_file_input = QLineEdit()
        self.log_file_input.setPlaceholderText("（可选）保留本次执行的 JSONL 日志")
        self.log_file_button = QPushButton("浏览")
        log_row.addWidget(log_label)
        log_row.addWidget(self.log_file_input, stretch=1)
        log_row.addWidget(self.log_file_button)
        config_layout.addLayout(log_row)

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
        self.log_file_button.clicked.connect(self._choose_log_file)
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
        self._update_action_buttons()

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
            self.workflow_atom_label.setText("atom：-")
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
            self.workflow_atom_label.setText("atom：-")
            self.workflow_steps_label.setText("步骤：-")
            self.workflow_desc_label.setText("")
            self._update_input_controls()
            self._update_action_buttons()
            return
        if not summary.is_valid:
            details = "；".join(summary.errors) if summary.errors else "未知校验错误"
            self.workflow_atom_label.setText("atom：无效")
            self.workflow_steps_label.setText(summary.filename)
            self.workflow_desc_label.setText(f"错误：{details}")
            self._update_input_controls()
            self._update_action_buttons()
            return
        try:
            workflow = self.workflow_loader.load(summary.path)
        except Exception as exc:
            self.workflow_atom_label.setText("atom：-")
            self.workflow_steps_label.setText(f"加载失败：{exc}")
            self.workflow_desc_label.setText("")
            self._update_input_controls()
            self._update_action_buttons()
            return

        self._current_workflow = workflow
        self._settings.setValue("last_workflow_path", str(summary.path))
        self.workflow_atom_label.setText(
            f"atom：{workflow.atom} | scope：{workflow.scope} | recurse：{workflow.recurse}"
        )
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

    def _current_atom(self) -> str | None:
        if self._current_workflow is None:
            return None
        return self._current_workflow.atom

    def _current_recurse(self) -> bool:
        if self._current_workflow is None:
            return False
        return self._current_workflow.recurse

    def _update_input_controls(self) -> None:
        atom = self._current_atom() or "none"
        wf_recurse = self._current_recurse()

        wants_path = atom in {"file", "folder"}
        wants_text = atom == "line"
        wants_files_button = atom == "file" and wf_recurse
        wants_folder_button = atom in {"file", "folder"}

        self.drop_zone.setVisible(wants_path)
        self.input_list.setVisible(wants_path)
        self.input_list.setEnabled(wants_path)
        self.remove_input_button.setVisible(wants_path)
        self.remove_input_button.setEnabled(wants_path)
        self.clear_inputs_button.setVisible(wants_path)
        self.clear_inputs_button.setEnabled(wants_path)
        self.add_files_button.setVisible(wants_path)
        self.add_files_button.setEnabled(wants_files_button)
        self.add_folder_button.setVisible(wants_path)
        self.add_folder_button.setEnabled(wants_folder_button)

        self.input_text_editor.setVisible(wants_text)
        self.input_text_editor.setEnabled(wants_text)

        if atom == "file" and wf_recurse:
            self.input_hint_label.setText("atom=file + recurse=true：每个文件作为一个任务，文件夹会被递归展开。")
        elif atom == "file" and not wf_recurse:
            self.input_hint_label.setText("atom=file + recurse=false：文件夹作为整体单元（folder 模式）。")
        elif atom == "line":
            self.input_hint_label.setText("atom=line：每行文本作为一个独立任务，空行自动忽略。")
        elif atom == "none":
            self.input_hint_label.setText("atom=none：无需输入，从空白直接产出。")
            self._clear_inputs()
        else:
            self.input_hint_label.setText("")

    def _update_action_buttons(self) -> None:
        running = self._worker_thread is not None and self._worker_thread.isRunning()
        self.execute_button.setEnabled(not running and self._can_start_execution())
        self.stop_button.setEnabled(running)

    def _can_start_execution(self) -> bool:
        if self._current_workflow is None:
            return False
        if not self.output_dir_input.text().strip():
            if not self._direct_mode or self._current_workflow.atom in ("none", "line"):
                return False
        atom = self._current_workflow.atom
        if atom in {"file", "folder"} and self.input_list.count() == 0:
            return False
        if atom == "line" and not self.input_text_editor.toPlainText().strip():
            return False
        return True

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择产物目录")
        if selected:
            path = str(Path(selected).resolve())
            self.output_dir_input.setText(path)
            self._settings.setValue("output_dir", path)
            self._update_action_buttons()

    def _choose_log_file(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(self, "选择日志文件", "", "JSONL Files (*.jsonl *.log)")
        if selected:
            self.log_file_input.setText(str(Path(selected).resolve()))

    def _on_output_dir_changed(self, text: str) -> None:
        self._settings.setValue("output_dir", text)
        self._update_action_buttons()

    def _choose_files(self) -> None:
        if self._current_atom() != "file" or not self._current_recurse():
            return
        selected, _ = QFileDialog.getOpenFileNames(self, "选择输入文件")
        if selected:
            self._add_input_paths(selected)

    def _choose_folder(self) -> None:
        if self._current_atom() not in {"file", "folder"}:
            return
        selected = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if selected:
            self._add_input_paths([selected])

    def _add_input_paths(self, paths: list[str]) -> None:
        atom = self._current_atom()
        if atom not in {"file", "folder"}:
            self.statusBar().showMessage("当前工作流不需要输入。")
            return

        existing = {self.input_list.item(index).data(Qt.UserRole) for index in range(self.input_list.count())}
        added_count = 0
        invalid_paths: list[str] = []

        # GUI keeps raw paths; directories get expanded by the executor
        # (so `source_root` is preserved).  Only validate existence.
        resolved = [Path(p).resolve() for p in paths]
        valid, invalid = InputInspector.validate_path_input(resolved)
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
                self,
                "部分输入未添加",
                "以下输入无效已跳过：\n" + "\n".join(invalid_paths[:10]) + ("\n…" if len(invalid_paths) > 10 else ""),
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
        return [self.input_list.item(index).data(Qt.UserRole) for index in range(self.input_list.count())]

    def _start_execution(self) -> None:
        workflow = self._current_workflow
        if workflow is None:
            QMessageBox.warning(self, "无法执行", "请先选择一个有效工作流。")
            return
        output_dir = self._resolve_output_dir()
        if not output_dir:
            QMessageBox.warning(self, "无法执行", "请先选择产物目录。")
            return
        files = self._collect_inputs()
        lines_text = self.input_text_editor.toPlainText()
        if workflow.atom in {"file", "folder"} and not files:
            QMessageBox.warning(self, "无法执行", "当前工作流需要至少一个文件/文件夹输入。")
            return
        if workflow.atom == "line" and not lines_text.strip():
            QMessageBox.warning(self, "无法执行", "请输入至少一行文本任务。")
            return

        if self._direct_mode:
            reply = QMessageBox.question(
                self,
                "直接模式确认",
                "直接模式将直接操作原始文件（不可逆）。\n\n确定要继续执行吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.execute_button.setEnabled(False)

        self.output_dir_input.setText(output_dir)
        self.log_output.clear()
        self.progress_bar.setValue(0)
        self._append_log(
            f"准备执行工作流: {workflow.meta.name} "
            f"(atom={workflow.atom}, scope={workflow.scope}, "
            f"recurse={workflow.recurse}, 直接模式: {self._direct_mode})"
        )
        for idx in range(self.input_list.count()):
            item = self.input_list.item(idx)
            if item:
                path = item.data(Qt.UserRole)
                item.setText(f"[等待] {path}")

        worker = ExecutionWorker(
            workflow=workflow,
            files=files,
            recurse=workflow.recurse,
            lines_text=lines_text,
            output_dir=output_dir,
            direct_mode=self._direct_mode,
            log_file=self.log_file_input.text().strip(),
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

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self._on_watchdog_timeout)
        self._watchdog_timer.start(5 * 60 * 1000)

    def _resolve_output_dir(self) -> str:
        text = self.output_dir_input.text().strip()
        if text:
            return str(Path(text).resolve())
        if self._direct_mode and self._current_workflow:
            atom = self._current_workflow.atom
            if atom in {"file", "folder"}:
                inputs = self._collect_inputs()
                if inputs:
                    return str(Path(inputs[0]).parent)
        return ""

    def _request_stop(self) -> None:
        if self._worker is None:
            return
        self._worker.request_stop()
        self._append_log("已发送停止请求，等待当前安全边界退出。")
        self.statusBar().showMessage("正在停止执行...")

    def _set_running_state(self, running: bool) -> None:
        atom = self._current_atom() or "none"
        is_file_atom = atom == "file" and self._current_recurse()
        is_path_input = atom in {"file", "folder"}

        self.workflow_combo.setEnabled(not running)
        self.refresh_workflows_button.setEnabled(not running)
        self.editor_button.setEnabled(not running)
        self.output_dir_input.setEnabled(not running)
        self.output_dir_button.setEnabled(not running)
        self.log_file_input.setEnabled(not running)
        self.log_file_button.setEnabled(not running)
        self.mode_copy_radio.setEnabled(not running)
        self.mode_direct_radio.setEnabled(not running)
        self.add_files_button.setEnabled(not running and is_file_atom)
        self.add_folder_button.setEnabled(not running and is_path_input)
        self.remove_input_button.setEnabled(not running and self.input_list.isEnabled())
        self.clear_inputs_button.setEnabled(not running and self.input_list.isEnabled())
        self.drop_zone.set_drop_enabled(not running and is_path_input)
        self.input_list.setEnabled(not running and is_path_input)
        self.input_text_editor.setEnabled(not running and atom == "line")
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
            accent, style = UIColors.ERROR, "font-weight:bold;"
        elif message.startswith("[WARN]"):
            accent, style = UIColors.WARNING, "font-weight:bold;"
        elif message.startswith("[OK]"):
            accent, style = UIColors.SUCCESS, ""
        elif message.startswith("[HINT]"):
            accent, style = UIColors.MUTED, "font-style:italic;"
        elif message.startswith("[INFO]"):
            accent, style = UIColors.INFO, ""
        else:
            accent, style = UIColors.DARK, ""
        html = (
            f'<div style="margin:0;padding:1px 6px;white-space:pre-wrap;'
            f'border-left:3px solid {accent};">'
            f'<span style="color:#95a5a6;font-size:9pt;">[{timestamp}]</span> '
            f'<span style="color:{accent};{style}">{escaped}</span>'
            f"</div>"
        )
        self.log_output.append(html)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def _on_terminal_event(self, payload: dict, runtime: object) -> None:
        event_type = payload.get("type", "")
        session_id = payload.get("session_id", "")
        rt = runtime if isinstance(runtime, PipelineRuntime) else self._worker_runtime
        if rt is not None:
            self._worker_runtime = rt

        if event_type == "terminal:started":
            command = payload.get("command", "")
            win = TerminalWindow(session_id, command, runtime=self._worker_runtime, parent=self)
            win.destroyed.connect(lambda sid=session_id: self._terminal_windows.pop(sid, None))
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
        unit = payload.get("unit") or payload.get("input_path") or "<none>"
        self.progress_bar.setValue(max(0, min(percent, 100)))
        self.statusBar().showMessage(f"状态: {status} | 当前单元: {unit}")

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
            self.progress_bar.setValue(0 if summary.get("cancelled") else self.progress_bar.value())
            self.statusBar().showMessage("执行失败")
            QMessageBox.critical(self, "执行失败", str(summary["error"]))
            return
        if summary.get("cancelled"):
            self.statusBar().showMessage("执行已取消")
            self._append_log("执行已取消。")
            return
        if summary.get("success"):
            self.progress_bar.setValue(100)
            self.statusBar().showMessage("执行完成")
            self._append_log(f"执行完成：处理了 {summary.get('processed_units', 0)} 个单元。")
            return
        self.statusBar().showMessage("执行结束，存在失败项")
        self._append_log(
            f"执行结束：成功 {summary.get('successful_units', 0)} / "
            f"{summary.get('processed_units', 0)}，失败 "
            f"{summary.get('failed_units', 0)}。"
        )

    def _on_watchdog_timeout(self) -> None:
        if self._worker is not None:
            self._append_log("执行超时 (5分钟)，强制终止工作线程。")
            self._worker.request_stop()
        thread = self._worker_thread
        if thread is not None and thread.isRunning():
            thread.terminate()
            thread.wait(3000)

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._worker_thread = None
        self._worker_runtime = None
        if self._watchdog_timer is not None:
            self._watchdog_timer.stop()
            self._watchdog_timer = None
        self._update_action_buttons()
