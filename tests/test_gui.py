from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QSettings, QThread, QTimer
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.project import GuiProjectSettings, ProjectPaths
from gui.widgets.execution_controller import ExecutionWorker, GuiExecutionRequest
from gui.widgets.input_panel import InputPanel

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _project(root: Path) -> ProjectPaths:
    (root / "modules").mkdir(parents=True)
    (root / "workflows").mkdir()
    return ProjectPaths.from_root(root)


def test_project_settings_priority_and_persistence(tmp_path: Path, app: QApplication) -> None:
    default = _project(tmp_path / "default")
    explicit = _project(tmp_path / "explicit")
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    project_settings = GuiProjectSettings(settings)

    assert project_settings.resolve(default_root=default.root) == default
    project_settings.remember(default)
    assert project_settings.remembered_root() == default.root
    assert project_settings.resolve(explicit_root=explicit.root, default_root=default.root) == explicit


def test_input_panel_supports_auto_and_explicit_atoms(tmp_path: Path, app: QApplication) -> None:
    panel = InputPanel()
    source_file = tmp_path / "input.txt"
    source_file.write_text("x", encoding="utf-8")
    source_dir = tmp_path / "folder"
    source_dir.mkdir()

    panel.set_atom(None, False)
    assert panel.auto_mode_combo.isVisibleTo(panel)
    assert panel.current_atom == "file"
    panel.add_paths([str(source_file), str(source_dir)])
    assert panel.get_files() == [str(source_file.resolve())]

    panel.auto_mode_combo.setCurrentIndex(panel.auto_mode_combo.findData("folder"))
    panel.add_paths([str(source_file), str(source_dir)])
    assert panel.current_atom == "folder"
    assert panel.get_files() == [str(source_dir.resolve())]

    panel.auto_mode_combo.setCurrentIndex(panel.auto_mode_combo.findData("line"))
    panel.text_editor.setPlainText("alpha\nbeta")
    assert panel.current_atom == "line"
    assert panel.get_files() == []
    assert panel.get_lines() == "alpha\nbeta"

    panel.set_atom("file", False)
    assert panel.add_files_button.isVisibleTo(panel)
    assert not panel.add_folder_button.isVisibleTo(panel)
    panel.close()


def test_input_panel_path_constraints_follow_atom_and_recurse(tmp_path: Path, app: QApplication) -> None:
    panel = InputPanel()
    source_file = tmp_path / "input.txt"
    source_file.write_text("x", encoding="utf-8")
    source_dir = tmp_path / "folder"
    source_dir.mkdir()

    panel.set_atom("file", False)
    panel.add_paths([str(source_file), str(source_dir)])
    assert panel.get_files() == [str(source_file.resolve())]

    panel.set_atom("file", True)
    panel.add_paths([str(source_file), str(source_dir)])
    assert panel.get_files() == [str(source_file.resolve()), str(source_dir.resolve())]

    panel.set_atom("file", False)
    assert panel.get_files() == []

    panel.set_atom("folder", False)
    panel.add_paths([str(source_file), str(source_dir)])
    assert panel.get_files() == [str(source_dir.resolve())]

    panel.set_atom("none", False)
    assert panel.has_input()
    assert panel.get_files() == []
    assert panel.get_lines() == ""
    panel.close()


def test_main_window_constructs_with_project_paths(tmp_path: Path, app: QApplication) -> None:
    paths = _project(tmp_path / "project")
    settings = GuiProjectSettings(QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat))
    window = MainWindow(paths, project_settings=settings)

    assert window.project_paths == paths
    assert not window._controller.is_running
    assert window._controller.parent() is window.centralWidget()
    window.close()


def test_execution_worker_runs_yaml_in_qthread_and_merges_terminal_output(
    tmp_path: Path,
    app: QApplication,
) -> None:
    project = _project(tmp_path / "run-project")
    module_path = project.modules_dir / "gui_demo.py"
    module_path.write_text(
        """
import sys
MODULE_META = {
    "slug": "gui-demo", "name": "GUI Demo", "core_version": "2.0.0",
    "tags": ["test"], "access": "read_write", "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}
def run(ctx, cfg, runtime):
    runtime.spawn([sys.executable, "-c", "print('gui-tool-output')"])
    ctx.create_file("gui-result.txt", "ok")
    return ctx
""",
        encoding="utf-8",
    )
    workflow_path = project.workflows_dir / "gui.yaml"
    workflow_path.write_text(
        "meta:\n  name: GUI Test\natom: none\nscope: 1\nrecurse: false\nsteps:\n  - module: gui-demo\n    params: {}\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    request = GuiExecutionRequest(
        workflow_path=workflow_path,
        workflow_name="GUI Test",
        recurse=False,
        input_paths=(),
        input_text="",
        output_dir=str(output_dir),
        direct_mode=False,
        modules_dir=str(project.modules_dir),
    )
    worker = ExecutionWorker(request)
    thread = QThread()
    worker.moveToThread(thread)
    summaries: list[dict] = []
    logs: list[str] = []
    loop = QEventLoop()

    thread.started.connect(worker.run)
    worker.log_message.connect(logs.append)
    worker.finished.connect(summaries.append)
    worker.finished.connect(thread.quit)
    thread.finished.connect(loop.quit)
    thread.start()
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    thread.wait(2_000)

    assert not thread.isRunning()
    assert summaries and summaries[0]["success"] is True
    assert (output_dir / "gui-result.txt").read_text(encoding="utf-8") == "ok"
    assert any("gui-tool-output" in message for message in logs)
