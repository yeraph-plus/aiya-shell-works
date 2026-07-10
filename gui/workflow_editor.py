"""Workflow editor window — single-page new/edit for workflow YAML files.

Layout: meta info + atom/scope at top, steps editor below, action buttons
right-aligned at the bottom.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import ModuleManager, WorkflowDefinition, WorkflowLoader
from gui.editor.info_tab import InfoTab
from gui.editor.steps_tab import StepsTab
from gui.editor.state import WorkflowDraft


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
        self._is_new = workflow is None
        self.draft = WorkflowDraft.from_workflow(workflow or self.workflow_loader.new_workflow())
        self._dirty = False

        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Workflow Editor")
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
            str(self.workflow_loader.workflows_dir),
            "Workflow Files (*.yaml *.yml)",
        )
        if not selected_path:
            return
        workflow = self.workflow_loader.load(Path(selected_path))
        self._is_new = False
        self.draft = WorkflowDraft.from_workflow(workflow)
        self._load_draft()
        self.statusBar().showMessage(f"已打开 {Path(selected_path).name}")

    def save_workflow(self) -> Path | None:
        if self.draft.source_path is None:
            return self.save_workflow_as()
        return self._save_to_target(self.draft.source_path)

    def save_workflow_as(self) -> Path | None:
        self.info_tab.sync_to_draft()
        default_name = self.workflow_loader.default_filename(self.draft.name or "workflow")
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "另存为",
            str(self.workflow_loader.workflows_dir / default_name),
            "Workflow Files (*.yaml *.yml)",
        )
        if not selected_path:
            return None
        return self._save_to_target(Path(selected_path))

    def _save_to_target(self, target_path: Path) -> Path | None:
        self.info_tab.sync_to_draft()
        workflow = self.draft.to_workflow_definition()
        try:
            relative_target = target_path.resolve().relative_to(self.workflow_loader.workflows_dir)
            saved_path = self.workflow_loader.save(workflow, relative_target)
        except Exception as exc:  # pragma: no cover - UI feedback path
            QMessageBox.critical(self, "保存失败", str(exc))
            self.statusBar().showMessage("保存失败")
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
