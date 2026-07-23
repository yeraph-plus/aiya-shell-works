"""Main application window — thin layout shell for ExecutionController."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from gui.project import GuiProjectSettings, ProjectPaths
from gui.widgets.execution_controller import ExecutionController


class MainWindow(QMainWindow):
    """Desktop window that lays out the three controller-owned panels."""

    def __init__(
        self,
        project_paths: ProjectPaths,
        *,
        project_settings: GuiProjectSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_settings = project_settings or GuiProjectSettings()
        self.project_paths = project_paths

        self.resize(1200, 800)

        self._build_menu()
        self._build_ui()
        self.statusBar().showMessage("就绪")

    def _build_menu(self) -> None:
        project_menu = self.menuBar().addMenu("项目")
        switch_action = project_menu.addAction("切换项目目录")
        switch_action.triggered.connect(self._choose_project)

    def _build_ui(self) -> None:
        central = QWidget(self)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        top_layer = QHBoxLayout()
        top_layer.setSpacing(8)

        left_column = QVBoxLayout()
        left_column.setSpacing(8)

        self._controller = ExecutionController(
            workflows_dir=self.project_paths.workflows_dir,
            modules_dir=str(self.project_paths.modules_dir),
            parent=central,
        )

        left_column.addWidget(self._controller.config_panel)
        top_layer.addLayout(left_column, stretch=35)
        top_layer.addWidget(self._controller.input_panel, stretch=65)

        main_layout.addLayout(top_layer, stretch=2)
        main_layout.addWidget(self._controller.log_viewer, stretch=1)

        self.setCentralWidget(central)
        self.setWindowTitle(f"AIYA Shell Worker Platform - {self.project_paths.root.name}")

        self._controller.status_message.connect(self.statusBar().showMessage)

    def _choose_project(self) -> None:
        if self._controller.is_running:
            QMessageBox.warning(self, "任务运行中", "请先停止当前任务，再切换项目目录。")
            return
        selected = QFileDialog.getExistingDirectory(self, "选择 Shell Worker 项目目录", str(self.project_paths.root))
        if not selected:
            return
        try:
            paths = ProjectPaths.from_root(selected)
        except ValueError as exc:
            QMessageBox.warning(self, "项目目录无效", str(exc))
            return

        old_central = self.centralWidget()
        self.project_paths = paths
        self._project_settings.remember(paths)
        self._build_ui()
        if old_central is not None:
            old_central.deleteLater()
        self.statusBar().showMessage(f"已切换项目: {paths.root}")
