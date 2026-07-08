"""GUI entry point for the Shell Worker platform.

PySide6 is an optional dependency.  If unavailable this module raises an
import error early so users will fall back to ``main.py`` instead.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from PySide6.QtWidgets import QApplication

from gui import MainWindow


def main() -> int:
    """Launch the desktop application."""
    app = QApplication(sys.argv)
    project_dir = Path(__file__).resolve().parent
    window = MainWindow(project_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())