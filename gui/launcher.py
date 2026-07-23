"""Installed GUI entry point; PySide6 remains an optional dependency."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")


def main(argv: list[str] | None = None, *, default_project_dir: str | Path | None = None) -> int:
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from gui import MainWindow
    from gui.project import GuiProjectSettings, ProjectPaths

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project-dir")
    arguments, qt_arguments = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0], *qt_arguments])

    settings = GuiProjectSettings()
    paths = settings.resolve(
        explicit_root=arguments.project_dir,
        default_root=default_project_dir or Path.cwd(),
    )
    if paths is None:
        selected = QFileDialog.getExistingDirectory(None, "选择 Shell Worker 项目目录", str(Path.cwd()))
        if not selected:
            return 0
        try:
            paths = ProjectPaths.from_root(selected)
        except ValueError as exc:
            QMessageBox.critical(None, "项目目录无效", str(exc))
            return 2

    settings.remember(paths)
    window = MainWindow(paths, project_settings=settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
