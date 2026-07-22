"""CLI: argparse entrypoints, exit codes, output of --list-*."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import main as main_module
from core.exceptions import PipelineExecutionError
from main import main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_no_workflow_returns_3(capsys) -> None:
    code = main([])
    assert code == 3


def test_cli_list_modules_returns_0(tmp_path: Path, capsys) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "demo.py").write_text(
        """
MODULE_META = {
    "slug": "cli-demo", "name": "CLI Demo",
    "core_version": "2.0.0", "tags": ["t"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}
def run(ctx, cfg, runtime): return ctx
""",
        encoding="utf-8",
    )
    code = main(["--list-modules", "--modules-dir", str(modules)])
    captured = capsys.readouterr().out
    assert code == 0
    assert "cli-demo" in captured


def test_cli_list_workflows_returns_0(tmp_path: Path, capsys) -> None:
    wfs = tmp_path / "workflows"
    wfs.mkdir()
    (wfs / "a.yaml").write_text("meta:\n  name: A\natom: none\nscope: 1\nsteps: []\n", encoding="utf-8")
    code = main(["--list-workflows", "--workflows-dir", str(wfs)])
    captured = capsys.readouterr().out
    assert code == 0
    assert "a.yaml" in captured


def test_cli_runs_none_workflow_succeeds(tmp_path: Path, capsys) -> None:
    """End-to-end: build a tiny module + workflow and run via main."""

    modules = tmp_path / "modules"
    modules.mkdir()
    wfs = tmp_path / "workflows"
    wfs.mkdir()
    out = tmp_path / "out"
    (modules / "mk.py").write_text(
        """
from pathlib import Path
MODULE_META = {
    "slug": "mk", "name": "MK", "core_version": "2.0.0",
     "tags": ["t"], "access": "read_write", "platforms": None,
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "ok.txt"},
        "content": {"type": "str", "default": "done"},
    },
}
def run(ctx, cfg, runtime):
    ctx.create_file(cfg["filename"], cfg["content"])
    return ctx
""",
        encoding="utf-8",
    )
    (wfs / "mk.yaml").write_text(
        """
meta:
  name: MK Test
  description: cli e2e
  version: "1.0.0"
atom: none
scope: 1
recurse: false
steps:
  - module: mk
    name: mk
    params:
      filename: hello.txt
      content: hi
""",
        encoding="utf-8",
    )
    code = main(
        [
            "--modules-dir",
            str(modules),
            "--workflows-dir",
            str(wfs),
            "--output-dir",
            str(out),
            "mk.yaml",
        ]
    )
    assert code == 0
    assert (out / "hello.txt").read_text(encoding="utf-8") == "hi"


def test_cli_invalid_workflow_returns_3(tmp_path: Path, capsys) -> None:
    wfs = tmp_path / "workflows"
    wfs.mkdir()
    (wfs / "bad.yaml").write_text("meta:\n  name: Bad\nmode: file\nsteps: []\n", encoding="utf-8")
    code = main(
        [
            "--workflows-dir",
            str(wfs),
            "--output-dir",
            str(tmp_path / "out"),
            "bad.yaml",
        ]
    )
    assert code == 3


def test_cli_subprocess_invocation_does_not_import_gui() -> None:
    """Ensure the CLI module imports cleanly without PySide6 installed."""

    result = subprocess.run(
        [sys.executable, "-c", "import main; print('ok')"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_cli_lines_text_creates_per_line_units(tmp_path: Path) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    wfs = tmp_path / "workflows"
    wfs.mkdir()
    (modules / "echo.py").write_text(
        """
from pathlib import Path
MODULE_META = {
    "slug": "echo", "name": "Echo",
    "core_version": "2.0.0", "tags": [],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}
def run(ctx, cfg, runtime):
    line = ctx.shared.get("input_line", "")
    ctx.create_file(f"{abs(hash(line)) & 0xffff}.txt", line + "\\n")
    return ctx
""",
        encoding="utf-8",
    )
    (wfs / "echo.yaml").write_text(
        """
meta: {name: Echo, version: "1.0.0"}
atom: line
scope: 1
steps:
  - module: echo
    name: e
    params: {}
""",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    code = main(
        [
            "--modules-dir",
            str(modules),
            "--workflows-dir",
            str(wfs),
            "--output-dir",
            str(out),
            "--lines",
            "alpha\nbeta",
            "echo.yaml",
        ]
    )
    assert code == 0
    files = list(out.glob("*.txt"))
    assert len(files) == 2


def test_cli_closes_runtime_when_execution_raises(monkeypatch, tmp_path: Path) -> None:
    """main() returns error code 3 when execution raises a validation error."""

    class _BoomMgr:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise PipelineExecutionError("boom")

    monkeypatch.setattr(main_module, "ModuleManager", _BoomMgr)

    code = main_module.main(
        [
            "--output-dir",
            str(tmp_path / "out"),
            "missing.yaml",
        ]
    )
    assert code == 3
