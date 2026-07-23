"""Configuration panel for workflow execution settings.

Combines workflow selection, mode configuration, output directory,
and logging options into a single reusable panel.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import WorkflowDefinition, WorkflowLoader, WorkflowSummary


class ConfigPanel(QWidget):
    """Panel for workflow execution configuration."""

    workflow_changed = Signal(object)  # WorkflowDefinition | None
    refresh_requested = Signal()
    edit_requested = Signal(object)  # WorkflowDefinition | None
    output_dir_changed = Signal(str)
    watch_dir_changed = Signal(str)
    cron_changed = Signal(str)
    concurrency_changed = Signal(int)
    log_save_changed = Signal(bool)
    execute_requested = Signal()
    stop_requested = Signal()
    watch_state_changed = Signal(bool)

    def __init__(
        self,
        workflows_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workflows_dir = workflows_dir
        self.workflow_loader = WorkflowLoader(workflows_dir)
        self._settings = QSettings("ShellWorker", "ShellWorker")
        self._current_workflow: WorkflowDefinition | None = None
        self._summaries: list[WorkflowSummary] = []
        self._pending_output_dir: str = ""
        self._settings_timer = QTimer(singleShot=True, interval=300)
        self._settings_timer.timeout.connect(self._flush_settings)

        self._build_ui()
        self._restore_settings()
        self._bind_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        row_1 = QHBoxLayout()
        workflow_label = QLabel("工作流 ")
        self.workflow_combo = QComboBox()
        self.atom_info_label = QLabel("")
        self.atom_info_label.setStyleSheet("color: #2c3e50; font-size: 9pt;")
        row_1.addWidget(workflow_label)
        row_1.addWidget(self.workflow_combo, stretch=1)
        layout.addLayout(row_1)

        row_2 = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.edit_button = QPushButton("编辑")
        row_2.addWidget(self.atom_info_label)
        row_2.addStretch(1)
        row_2.addWidget(self.refresh_button)
        row_2.addWidget(self.edit_button)
        layout.addLayout(row_2)

        self.workflow_desc_label = QLabel("")
        self.workflow_desc_label.setWordWrap(True)
        self.workflow_desc_label.setStyleSheet("color: #7f8c8d; font-size: 9pt; padding: 4px;")
        layout.addWidget(self.workflow_desc_label)

        row_watch_toggle = QHBoxLayout()
        self.watch_checkbox = QCheckBox("启用文件监听")
        self.watch_checkbox.setToolTip("开启后监控指定目录的文件变化，自动重新执行。\n输入区将切换为无输入模式。")
        row_watch_toggle.addWidget(self.watch_checkbox)
        row_watch_toggle.addStretch(1)
        layout.addLayout(row_watch_toggle)

        self._watch_dir_container = QWidget()
        watch_dir_layout = QHBoxLayout(self._watch_dir_container)
        watch_dir_layout.setContentsMargins(0, 0, 0, 0)
        watch_dir_label = QLabel("监听目录 ")
        self.watch_dir_input = QLineEdit()
        self.watch_dir_input.setPlaceholderText("选择要监听的目录路径")
        self.watch_dir_button = QPushButton("浏览")
        watch_dir_layout.addWidget(watch_dir_label)
        watch_dir_layout.addWidget(self.watch_dir_input, stretch=1)
        watch_dir_layout.addWidget(self.watch_dir_button)
        self._watch_dir_container.hide()
        layout.addWidget(self._watch_dir_container)

        row_3 = QHBoxLayout()
        self.copy_mode_label = QLabel("拷贝模式 ")
        row_3.addWidget(self.copy_mode_label)
        self.mode_copy_radio = QRadioButton("开")
        self.mode_direct_radio = QRadioButton("关")
        self.mode_copy_radio.setChecked(True)
        self.mode_copy_radio.setToolTip("将文件复制到产物目录后操作副本，不修改原文件。")
        self.mode_direct_radio.setToolTip("直接在原始文件上操作，不会创建副本。")
        self._mode_button_group = QButtonGroup(self)
        self._mode_button_group.addButton(self.mode_copy_radio, 0)
        self._mode_button_group.addButton(self.mode_direct_radio, 1)
        self._mode_button_group.buttonClicked.connect(self._on_copy_mode_changed)
        row_3.addWidget(self.mode_copy_radio)
        row_3.addWidget(self.mode_direct_radio)
        row_3.addStretch(1)
        layout.addLayout(row_3)

        self._direct_warning_label = QLabel(
            '<span style="color:#e67e22;font-weight:bold;">警告：将直接修改原始文件！</span>'
        )
        self._direct_warning_label.setVisible(False)
        layout.addWidget(self._direct_warning_label)

        row_4 = QHBoxLayout()
        output_label = QLabel("产物目录 ")
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setPlaceholderText("选择或输入产物目录")
        self.output_dir_button = QPushButton("浏览")
        row_4.addWidget(output_label)
        row_4.addWidget(self.output_dir_input, stretch=1)
        row_4.addWidget(self.output_dir_button)
        layout.addLayout(row_4)

        row_concurrency = QHBoxLayout()
        concurrency_label = QLabel("并发数 ")
        self.concurrency_spinbox = QSpinBox()
        self.concurrency_spinbox.setRange(1, 99)
        self.concurrency_spinbox.setValue(1)
        self.concurrency_spinbox.setToolTip("并行 worker 数，1 为串行执行")
        row_concurrency.addWidget(concurrency_label)
        row_concurrency.addWidget(self.concurrency_spinbox)
        row_concurrency.addStretch(1)
        layout.addLayout(row_concurrency)

        row_cron = QHBoxLayout()
        cron_label = QLabel("定时(Cron) ")
        self.cron_input = QLineEdit()
        self.cron_input.setPlaceholderText("可选，如 */5 * * * *")
        self.cron_input.setToolTip("标准 5 字段 cron 表达式，留空表示不启用定时执行")
        row_cron.addWidget(cron_label)
        row_cron.addWidget(self.cron_input, stretch=1)
        layout.addLayout(row_cron)

        row_5 = QHBoxLayout()
        self.log_save_checkbox = QCheckBox("保存执行日志")
        self.log_save_checkbox.setToolTip("勾选后，每次执行会自动保存日志到产物目录")
        row_5.addWidget(self.log_save_checkbox)
        row_5.addStretch(1)
        layout.addLayout(row_5)

        exec_row = QHBoxLayout()
        self.execute_button = QPushButton("执行")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        exec_row.addStretch(1)
        exec_row.addWidget(self.execute_button)
        exec_row.addWidget(self.stop_button)
        layout.addLayout(exec_row)

        layout.addStretch(1)

    def _bind_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.edit_button.clicked.connect(self._on_edit_clicked)
        self.workflow_combo.currentIndexChanged.connect(self._on_workflow_changed)
        self.output_dir_button.clicked.connect(self._choose_output_dir)
        self.output_dir_input.textChanged.connect(self._on_output_dir_changed)
        self.log_save_checkbox.stateChanged.connect(self._on_log_save_changed)
        self.execute_button.clicked.connect(self.execute_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.watch_checkbox.stateChanged.connect(self._on_watch_state_changed)
        self.watch_dir_button.clicked.connect(self._choose_watch_dir)
        self.watch_dir_input.textChanged.connect(
            lambda t: (self._settings.setValue("watch_dir", t), self.watch_dir_changed.emit(t))
        )
        self.concurrency_spinbox.valueChanged.connect(
            lambda v: (self._settings.setValue("concurrency", v), self.concurrency_changed.emit(v))
        )
        self.cron_input.textChanged.connect(lambda t: (self._settings.setValue("cron", t), self.cron_changed.emit(t)))

    def _restore_settings(self) -> None:
        saved_dir = self._settings.value("output_dir", "")
        if saved_dir:
            self.output_dir_input.setText(saved_dir)

        log_save_enabled = self._settings.value("log_save_enabled", False, type=bool)
        self.log_save_checkbox.setChecked(log_save_enabled)

        direct_mode = self._settings.value("direct_mode", False, type=bool)
        if direct_mode:
            self.mode_direct_radio.setChecked(True)
            self._direct_warning_label.setVisible(True)

        watch_enabled = self._settings.value("watch_enabled", False, type=bool)
        self.watch_checkbox.setChecked(watch_enabled)
        self._watch_dir_container.setVisible(watch_enabled)

        saved_watch_dir = self._settings.value("watch_dir", "")
        if saved_watch_dir:
            self.watch_dir_input.setText(saved_watch_dir)

        concurrency = self._settings.value("concurrency", 1, type=int)
        self.concurrency_spinbox.setValue(max(1, concurrency))

        saved_cron = self._settings.value("cron", "")
        if saved_cron:
            self.cron_input.setText(saved_cron)
        self._update_copy_mode_text()

    def load_workflows(self, selected_path: Path | None = None) -> None:
        """Load workflows and populate the combo box."""
        self._summaries = self.workflow_loader.list_workflows(include_invalid=True)

        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()

        self.workflow_combo.addItem(" ---- 新建工作流 ---- ", None)
        for summary in self._summaries:
            label = self._build_workflow_label(summary)
            self.workflow_combo.addItem(label, summary)
        self.workflow_combo.blockSignals(False)

        if not self._summaries:
            self._current_workflow = None
            self._update_workflow_info()
            self.workflow_combo.setCurrentIndex(0)
            return

        restored_index = 0
        if selected_path is not None:
            for index, summary in enumerate(self._summaries):
                if summary.path == selected_path:
                    restored_index = index + 1
                    break
        else:
            saved_path = self._settings.value("last_workflow_path", "")
            if saved_path:
                for index, summary in enumerate(self._summaries):
                    if str(summary.path) == saved_path:
                        restored_index = index + 1
                        break
            else:
                for index, summary in enumerate(self._summaries):
                    if summary.is_valid:
                        restored_index = index + 1
                        break

        self.workflow_combo.blockSignals(True)
        self.workflow_combo.setCurrentIndex(restored_index)
        self.workflow_combo.blockSignals(False)
        self._on_workflow_changed(restored_index)

    def _build_workflow_label(self, summary: WorkflowSummary) -> str:
        if summary.is_valid:
            return f"{summary.name}"
        return f"[无效] {summary.filename}"

    def _on_workflow_changed(self, index: int) -> None:
        if index == 0:
            self._current_workflow = None
            self.edit_button.setText("新建")
            self._update_workflow_info()
            self.workflow_changed.emit(None)
            return

        summary = self.workflow_combo.itemData(index)
        if not isinstance(summary, WorkflowSummary):
            self._current_workflow = None
            self._update_workflow_info()
            self.workflow_changed.emit(None)
            return

        self.edit_button.setText("编辑")
        self._settings.setValue("last_workflow_path", str(summary.path))

        if not summary.is_valid:
            self._current_workflow = None
            self._update_workflow_info(summary=summary)
            self.workflow_changed.emit(None)
            return

        try:
            workflow = self.workflow_loader.load(summary.path)
            self._current_workflow = workflow
            self._update_workflow_info(summary=summary)
            self.workflow_changed.emit(workflow)
        except Exception as exc:
            self._current_workflow = None
            self._update_workflow_info(error=str(exc))
            self.workflow_changed.emit(None)

    def _update_workflow_info(
        self,
        summary: WorkflowSummary | None = None,
        error: str | None = None,
    ) -> None:
        if error:
            self.atom_info_label.setText("")
            self.workflow_desc_label.setText(f"加载失败: {error}")
            return

        if summary is None:
            self.atom_info_label.setText("")
            self.workflow_desc_label.setText("选择或新建工作流以开始执行")
            return

        if not summary.is_valid:
            self.atom_info_label.setText("")
            details = "；".join(summary.errors) if summary.errors else "未知校验错误"
            self.workflow_desc_label.setText(f"错误: {details}")
            return

        atom = summary.atom
        atom_labels = {
            "file": "逐文件执行",
            "folder": "逐文件夹执行",
            "line": "按行输入",
            "none": "无输入",
        }
        atom_label = atom_labels.get(atom, "自动识别") if atom else "自动识别"

        if summary.scope == 0:
            scope_label = "合并执行"
        elif summary.scope == 1:
            scope_label = "逐个执行"
        else:
            scope_label = f"分批执行({summary.scope})"
        self.atom_info_label.setText(f"{atom_label}，{scope_label}")
        self.workflow_desc_label.setText(summary.description or "暂无描述")

    def _on_edit_clicked(self) -> None:
        index = self.workflow_combo.currentIndex()
        if index == 0:
            self.edit_requested.emit(None)
        else:
            self.edit_requested.emit(self._current_workflow)

    def _on_copy_mode_changed(self, _button: QAbstractButton | None = None) -> None:
        is_direct = self.mode_direct_radio.isChecked()
        self._direct_warning_label.setVisible(is_direct)
        self._settings.setValue("direct_mode", is_direct)
        self._update_copy_mode_text()

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择产物目录")
        if selected:
            path = str(Path(selected).resolve())
            self.output_dir_input.setText(path)
            self._settings.setValue("output_dir", path)

    def _on_output_dir_changed(self, text: str) -> None:
        self._pending_output_dir = text
        self._settings_timer.start()
        self.output_dir_changed.emit(text)

    def _flush_settings(self) -> None:
        self._settings.setValue("output_dir", self._pending_output_dir)

    def _on_log_save_changed(self, state: int) -> None:
        enabled = state == 2
        self._settings.setValue("log_save_enabled", enabled)
        self.log_save_changed.emit(enabled)

    def _on_watch_state_changed(self, state: int) -> None:
        enabled = state == 2
        self._watch_dir_container.setVisible(enabled)
        self._settings.setValue("watch_enabled", enabled)
        self._update_copy_mode_text()
        self.watch_state_changed.emit(enabled)

    def _update_copy_mode_text(self) -> None:
        watching = self.watch_checkbox.isChecked()
        if watching:
            self.copy_mode_label.setText("监听文件传输 ")
            self.mode_copy_radio.setText("拷贝")
            self.mode_direct_radio.setText("移动")
            self.mode_copy_radio.setToolTip("把稳定的变化文件拷贝到产物目录后执行。")
            self.mode_direct_radio.setToolTip("把稳定的变化文件移出监听目录并送入产物目录。")
            self._direct_warning_label.setText(
                '<span style="color:#e67e22;font-weight:bold;">警告：监听文件将从原目录移走！</span>'
            )
        else:
            self.copy_mode_label.setText("拷贝模式 ")
            self.mode_copy_radio.setText("开")
            self.mode_direct_radio.setText("关")
            self.mode_copy_radio.setToolTip("将文件复制到产物目录后操作副本，不修改原文件。")
            self.mode_direct_radio.setToolTip("直接在原始文件上操作，不会创建副本。")
            self._direct_warning_label.setText(
                '<span style="color:#e67e22;font-weight:bold;">警告：将直接修改原始文件！</span>'
            )

    def _choose_watch_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择监听目录")
        if selected:
            path = str(Path(selected).resolve())
            self.watch_dir_input.setText(path)
            self._settings.setValue("watch_dir", path)

    def get_current_workflow(self) -> WorkflowDefinition | None:
        """Get the currently selected workflow."""
        return self._current_workflow

    def get_output_dir(self) -> str:
        """Get the output directory path."""
        return self.output_dir_input.text().strip()

    def is_direct_mode(self) -> bool:
        """Check if direct mode is enabled."""
        return self.mode_direct_radio.isChecked()

    def is_log_save_enabled(self) -> bool:
        """Check if log saving is enabled."""
        return self.log_save_checkbox.isChecked()

    def is_watch_enabled(self) -> bool:
        """Check if file watching is enabled."""
        return self.watch_checkbox.isChecked()

    def get_watch_dir(self) -> str:
        """Get the watch directory path."""
        return self.watch_dir_input.text().strip()

    def get_concurrency(self) -> int:
        """Get the concurrency value."""
        return self.concurrency_spinbox.value()

    def get_cron(self) -> str:
        """Get the cron expression."""
        return self.cron_input.text().strip()

    def set_running(self, running: bool) -> None:
        """Enable/disable controls based on execution state."""
        self.workflow_combo.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.edit_button.setEnabled(not running)
        self.output_dir_input.setEnabled(not running)
        self.output_dir_button.setEnabled(not running)
        self.mode_copy_radio.setEnabled(not running)
        self.mode_direct_radio.setEnabled(not running)
        self.log_save_checkbox.setEnabled(not running)
        self.watch_checkbox.setEnabled(not running)
        self.watch_dir_input.setEnabled(not running)
        self.watch_dir_button.setEnabled(not running)
        self.concurrency_spinbox.setEnabled(not running)
        self.cron_input.setEnabled(not running)
        self.execute_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def get_selected_summary(self) -> WorkflowSummary | None:
        """Get the selected workflow summary."""
        index = self.workflow_combo.currentIndex()
        if index == 0:
            return None
        data = self.workflow_combo.itemData(index)
        return data if isinstance(data, WorkflowSummary) else None

    def update_workflow_description(self, workflow: WorkflowDefinition) -> None:
        """Update the workflow description after editing."""
        self._current_workflow = workflow
        summary = self.get_selected_summary()
        if summary:
            self._update_workflow_info(summary=summary)
