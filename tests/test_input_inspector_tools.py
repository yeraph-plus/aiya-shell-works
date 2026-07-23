from __future__ import annotations

import sys
from pathlib import Path

from core.context import PipelineContext
from core.files import ExecutionWorkspace
from core.input_inspector import InputInspector
from core.planning import ExecutionUnit
from core.tools import collect_file_targets, ensure_pty_available, parse_extension_set


def _context(tmp_path: Path) -> PipelineContext:
    prepared = ExecutionWorkspace(tmp_path / "out").prepare_unit(1, ExecutionUnit(kind="none"))
    return PipelineContext(workspace=prepared.workspace)


def test_input_inspector_validates_files_directories_and_missing(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("value", encoding="utf-8")
    directory = tmp_path / "directory"
    directory.mkdir()
    missing = tmp_path / "missing"

    assert InputInspector.validate_file(file_path).is_valid
    assert "文件不存在" in InputInspector.validate_file(missing).error
    assert "不是文件" in InputInspector.validate_file(directory).error
    assert InputInspector.validate_directory(directory).is_valid
    assert "目录不存在" in InputInspector.validate_directory(missing).error
    assert "不是目录" in InputInspector.validate_directory(file_path).error

    valid, invalid = InputInspector.validate_path_input([file_path, directory, missing])
    assert valid == [file_path, directory]
    assert [result.path for result in invalid] == [missing]


def test_collect_file_targets_and_extension_parsing(tmp_path: Path) -> None:
    context = _context(tmp_path)
    directory = context.create_directory("inputs")
    text = context.create_file(directory.path / "a.TXT", "a")
    context.create_file(directory.path / "b.jpg", "b")
    nested = context.create_directory(directory.path / "nested")
    context.create_file(nested.path / "hidden.txt", "hidden")
    context.set_current(directory.path)

    extensions = parse_extension_set(" .TXT jpg  ")
    assert extensions == frozenset({".txt", ".jpg"})
    targets = collect_file_targets(context, extensions=frozenset({"txt"}))
    assert targets == [text]
    assert collect_file_targets(context, extensions=extensions) == [text, context.file(directory.path / "b.jpg")]

    context.set_current(text.path)
    assert collect_file_targets(context) == [text]


def test_ensure_pty_available_on_posix() -> None:
    class Runtime:
        def log(self, *args, **kwargs):
            raise AssertionError("POSIX PTY should not emit fallback warning")

    if sys.platform != "win32":
        assert ensure_pty_available(Runtime(), "test")
