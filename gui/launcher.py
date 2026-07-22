"""Installed GUI entry point; PySide6 remains an optional dependency."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from gui import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow(Path.cwd())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
