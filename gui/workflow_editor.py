"""Workflow editor window — single-page new/edit for workflow YAML files.

Layout: meta info + atom/scope at top, steps editor below, action buttons
right-aligned at the bottom.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import ModuleManager, WorkflowDefinition
from gui.editor.info_tab import InfoTab
from gui.editor.state import WorkflowDraft
from gui.editor.steps_tab import StepsTab
from gui.workflow_store import WorkflowAuthoringStore


class WorkflowEditor(QMainWindow):
    """Standalone editor window for creating and saving workflow YAML files."""

    workflow_saved = Signal(object)

    def __init__(
        self,
        workflow_store: WorkflowAuthoringStore,
        module_manager: ModuleManager,
        workflow: WorkflowDefinition | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workflow_store = workflow_store
        self.module_manager = module_manager
        self._is_new = workflow is None
        self.draft = WorkflowDraft.from_workflow(workflow) if workflow is not None else WorkflowDraft.new()
        self._dirty = False

        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("AIYA Workflow Editor")
        self.resize(1200, 760)

        self._build_ui()
        self._load_draft()
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 8)

        self.info_tab = InfoTab(self)
        self.steps_tab = StepsTab(self.module_manager, self)

        root_layout.addWidget(self.info_tab)
        root_layout.addWidget(self.steps_tab, stretch=1)

        button_row = QHBoxLayout()
        self.open_button = QPushButton("导入")
        self.save_as_button = QPushButton("另存为")
        self.save_button = QPushButton("保存")
        button_row.addStretch(1)
        button_row.addWidget(self.open_button)
        button_row.addWidget(self.save_as_button)
        button_row.addWidget(self.save_button)
        root_layout.addLayout(button_row)

        self._bottom_warning_label = QLabel()
        self._bottom_warning_label.setStyleSheet(
            "background: #fdf2f2; color: #c0392b; padding: 6px 12px;border-radius: 4px; font-size: 10pt;"
        )
        self._bottom_warning_label.setWordWrap(True)
        self._bottom_warning_label.hide()
        root_layout.addWidget(self._bottom_warning_label)

        self.setCentralWidget(central)

        self.save_button.clicked.connect(self.save_workflow)
        self.save_as_button.clicked.connect(self.save_workflow_as)
        self.open_button.clicked.connect(self.open_workflow)
        self.info_tab.atom_scope_changed.connect(self._on_atom_scope_changed)
        self.info_tab.meta_changed.connect(lambda: self._set_dirty(True))
        self.steps_tab.dirty_changed.connect(lambda: self._set_dirty(True))

    def _load_draft(self) -> None:
        self.info_tab.set_draft(self.draft)
        self.info_tab.set_edit_mode(self._is_new)
        self.steps_tab.set_draft(self.draft)
        self._set_dirty(False)

    # ------------------------------------------------------------
    # Atom / scope change
    # ------------------------------------------------------------

    def _on_atom_scope_changed(self) -> None:
        self.steps_tab.refresh_available_modules()

    # ------------------------------------------------------------
    # File ops
    # ------------------------------------------------------------

    def open_workflow(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开工作流",
            str(self.workflow_store.workflows_dir),
            "Workflow Files (*.yaml *.yml)",
        )
        if not selected_path:
            return
        workflow = self.workflow_store.import_workflow(Path(selected_path))
        self._is_new = True
        self.draft = WorkflowDraft.from_workflow(workflow)
        self._load_draft()
        self.statusBar().showMessage(f"已打开 {Path(selected_path).name}")

    def save_workflow(self) -> Path | None:
        if self.draft.source_path is None:
            return self.save_workflow_as()
        return self._save_to_target(self.draft.source_path)

    def save_workflow_as(self) -> Path | None:
        self.info_tab.sync_to_draft()
        default_name = self.workflow_store.default_filename(self.draft.name or "workflow")
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "另存为",
            str(self.workflow_store.workflows_dir / default_name),
            "Workflow Files (*.yaml *.yml)",
        )
        if not selected_path:
            return None
        return self._save_to_target(Path(selected_path))

    def _save_to_target(self, target_path: Path) -> Path | None:
        self.info_tab.sync_to_draft()
        workflow = self.draft.to_workflow_definition()
        try:
            saved_path = self.workflow_store.save(workflow, target_path)
        except Exception as exc:  # pragma: no cover - UI feedback path
            self._show_bottom_warning(f"保存失败: {exc}")
            self.statusBar().showMessage("保存失败", 3000)
            return None
        self.draft.source_path = saved_path
        self._is_new = False
        self.info_tab.set_edit_mode(False)
        self.info_tab.load_draft()
        self._set_dirty(False)
        self.workflow_saved.emit(saved_path)
        self.statusBar().showMessage(f"已保存到 {saved_path.name}")
        return saved_path

    # ------------------------------------------------------------
    # Dirty + close
    # ------------------------------------------------------------

    def _show_bottom_warning(self, text: str, timeout_ms: int = 0) -> None:
        self._bottom_warning_label.setText(text)
        self._bottom_warning_label.show()
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, self._bottom_warning_label.hide)

    def _clear_bottom_warning(self) -> None:
        self._bottom_warning_label.clear()
        self._bottom_warning_label.hide()

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
                self,
                "未保存的更改",
                "工作流已修改。是否在关闭前保存？",
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
