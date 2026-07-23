"""GUI entry point for the Shell Worker platform.

PySide6 is an optional dependency.  If unavailable this module raises an
import error early so users will fall back to ``main.py`` instead.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")


def main() -> int:
    """Launch the desktop application."""
    from gui.launcher import main as launch_gui

    return launch_gui(default_project_dir=Path(__file__).resolve().parent)


if __name__ == "__main__":
    raise SystemExit(main())
