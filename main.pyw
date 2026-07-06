"""Application entry point for the Shell Worker platform."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from PySide6.QtWidgets import QApplication

from gui import MainWindow


def _hide_console() -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def main() -> int:
    """Launch the desktop application."""
    _hide_console()
    app = QApplication(sys.argv)
    project_dir = Path(__file__).resolve().parent
    window = MainWindow(project_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
