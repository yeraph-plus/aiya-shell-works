"""Main application window — thin layout shell for ExecutionController."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.execution_controller import ExecutionController


class MainWindow(QMainWindow):
    """Desktop window that lays out the three controller-owned panels."""

    def __init__(self, project_dir: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_dir = Path(project_dir).resolve()
        self.workflows_dir = self.project_dir / "workflows"
        self.modules_dir = self.project_dir / "modules"

        self.setWindowTitle("AIYA Shell Worker Platform")
        self.resize(1200, 800)

        self._build_ui()
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

        self._controller = ExecutionController(
            workflows_dir=self.workflows_dir,
            modules_dir=str(self.modules_dir),
            parent=self,
        )

        left_column.addWidget(self._controller.config_panel)
        top_layer.addLayout(left_column, stretch=35)
        top_layer.addWidget(self._controller.input_panel, stretch=65)

        main_layout.addLayout(top_layer, stretch=2)
        main_layout.addWidget(self._controller.log_viewer, stretch=1)

        self.setCentralWidget(central)

        self._controller.status_message.connect(self.statusBar().showMessage)
