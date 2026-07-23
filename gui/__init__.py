"""GUI package for desktop application views and controllers."""

from typing import Any

__all__ = ["MainWindow", "WorkflowEditor"]


def __getattr__(name: str) -> Any:
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    if name == "WorkflowEditor":
        from .workflow_editor import WorkflowEditor

        return WorkflowEditor
    raise AttributeError(name)
