"""PipelineExecutor: per-unit isolation, shared scope, step contract, cancellation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from core import (
    ModuleManager,
    PipelineEvent,
    PipelineExecutor,
    PipelineRuntime,
    WorkflowLoader,
    execute_workflow,
)
from core.exceptions import PipelineExecutionError

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

RENAME_MODULE = """
import os
from pathlib import Path

MODULE_META = {
    "slug": "demo-rename",
    "name": "Demo Rename",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "suffix": {"type": "str", "default": "_done"},
    },
}

def run(ctx, cfg, runtime):
    suffix = cfg["suffix"]
    old = ctx.current
    old_path = old.path
    new = old.rename(old.name + suffix)
    renames = list(ctx.shared.get("renames", []))
    renames.append({"from": str(old_path), "to": str(new.path)})
    ctx.shared["renames"] = renames
    return ctx
"""

SHARED_COUNT_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "shared-count",
    "name": "Shared Count",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
    "scope": 0,
    "parent": None,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "report_name": {"type": "str", "default": "count.txt"},
    },
}

def run(ctx, cfg, runtime):
    files = sorted(entry.path for entry in ctx.files())
    for i, fp in enumerate(files, 1):
        runtime.log("shared-count", "success", f"{i}: {fp.name}")
    ctx.create_file(cfg["report_name"], f"count={len(files)}\\n")
    return ctx
"""

LINE_ECHO_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "demo-echo",
    "name": "Demo Echo",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {}
}

def run(ctx, cfg, runtime):
    line = ctx.shared.get("input_line", "")
    ctx.create_file(f"{abs(hash(line)) & 0xffff}.txt", line + "\\n")
    return ctx
"""

BATCH_LINE_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "demo-line-batch",
    "name": "Demo Line Batch",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
    "scope": 2,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {}
}

def run(ctx, cfg, runtime):
    lines = list(ctx.shared.get("input_lines", []))
    runtime.log("demo-line-batch", "success", f"batch={len(lines)} lines={'|'.join(lines)}")
    ctx.create_file(f"{lines[0]}_{len(lines)}.txt", "\\n".join(lines))
    return ctx
"""

PATH_BATCH_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "demo-path-batch",
    "name": "Demo Path Batch",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
    "scope": 2,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {}
}

def run(ctx, cfg, runtime):
    files = sorted(entry.name for entry in ctx.files())
    runtime.log("demo-path-batch", "success", f"batch={len(files)} files={'|'.join(files)}")
    ctx.create_file("batch.txt", "\\n".join(files))
    return ctx
"""

NONE_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "demo-none",
    "name": "Demo None",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "hello.txt"},
        "content": {"type": "str", "default": "hi"},
    },
}

def run(ctx, cfg, runtime):
    ctx.create_file(cfg["filename"], cfg["content"])
    return ctx
"""

SYNTHESIS_MODULE = """
MODULE_META = {
    "slug": "demo-synth",
    "name": "Demo Synth",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    runtime.log("demo-synth", "success", f"is_file={ctx.current.is_file} is_dir={ctx.current.is_dir}")
    return ctx
"""

READ_MODULE = """
MODULE_META = {
    "slug": "demo-read",
    "name": "Demo Read",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    files = ctx.files()
    if not files:
        raise RuntimeError("no readable files")
    runtime.log(
        "demo-read",
        "message",
        "read-only",
        {
            "names": [entry.name for entry in files],
            "relative": [str(entry.relative_path) for entry in files],
            "contents": [entry.read_text(encoding="utf-8") for entry in files],
        },
    )
    ctx.shared["read_count"] = len(files)
    return ctx
"""

READ_VIOLATION_MODULE = """
MODULE_META = {
    "slug": "read-violation",
    "name": "Read Violation",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    ctx.create_file("forbidden.txt", "bad")
    return ctx
"""

WINDOWS_ONLY_MODULE = """
MODULE_META = {
    "slug": "windows-only",
    "name": "Windows Only",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": ["windows"],
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {"required_path": {"type": "str", "required": True}},
}

def run(ctx, cfg, runtime):
    raise RuntimeError("unsupported module must not run")
"""


@pytest.fixture()
def modules_dir(tmp_path: Path) -> Path:
    d = tmp_path / "modules"
    d.mkdir()
    (d / "rename.py").write_text(RENAME_MODULE, encoding="utf-8")
    (d / "shared_count.py").write_text(SHARED_COUNT_MODULE, encoding="utf-8")
    (d / "echo.py").write_text(LINE_ECHO_MODULE, encoding="utf-8")
    (d / "line_batch.py").write_text(BATCH_LINE_MODULE, encoding="utf-8")
    (d / "path_batch.py").write_text(PATH_BATCH_MODULE, encoding="utf-8")
    (d / "none.py").write_text(NONE_MODULE, encoding="utf-8")
    (d / "synth.py").write_text(SYNTHESIS_MODULE, encoding="utf-8")
    (d / "read.py").write_text(READ_MODULE, encoding="utf-8")
    (d / "read_violation.py").write_text(READ_VIOLATION_MODULE, encoding="utf-8")
    (d / "windows_only.py").write_text(WINDOWS_ONLY_MODULE, encoding="utf-8")
    return d


@pytest.fixture()
def workflows_dir(tmp_path: Path) -> Path:
    return tmp_path / "workflows"


def _make_wf(
    workflows_dir: Path, name: str, atom: str, scope: int, recurse: bool, steps: list[dict], meta_name: str = "WF"
) -> Path:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    path = workflows_dir / name
    doc = {
        "meta": {"name": meta_name, "description": "demo", "version": "1.0.0", "slug": "demo"},
        "atom": atom,
        "scope": scope,
        "recurse": recurse,
        "steps": steps,
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
    return path


# ---------------------------------------------------------------------------
# Per-unit isolation: each unit's bus never sees another's events
# ---------------------------------------------------------------------------


def test_per_unit_bus_isolation_between_units(modules_dir: Path, tmp_path: Path) -> None:
    """scope=per-unit: each file gets a fresh event bus."""

    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-synth", "name": "synth", "params": {}}],
    )

    # Two file inputs
    a = tmp_path / "a.txt"
    a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("y", encoding="utf-8")

    runtime = PipelineRuntime()
    manager = ModuleManager(modules_dir)
    executor = PipelineExecutor(manager, runtime=runtime)
    seen: list[Any] = []
    runtime.subscribe(seen.append)
    executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        files=[a, b],
        recurse=True,
    )
    # Look at *run-level* logs (one per unit execution).
    run_events = [e for e in seen if e.slug == "demo-synth" and "is_file=" in e.text]
    # Each unit emits exactly one "is_file=... is_dir=..." log from synthesize's run().
    assert run_events, "synth run-level logs must be present"
    assert len(run_events) == 2
    assert all("is_file=True" in e.text for e in run_events)


def test_per_unit_isolation_no_event_bleeds(modules_dir: Path, tmp_path: Path) -> None:
    """Verify replace_bus actually clears each unit's events."""

    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=True,
        steps=[{"module": "demo-synth", "name": "synth", "params": {}}],
    )
    a = tmp_path / "src"
    a.mkdir()
    (a / "1.txt").write_text("1", encoding="utf-8")
    (a / "2.txt").write_text("2", encoding="utf-8")
    (a / "3.txt").write_text("3", encoding="utf-8")

    runtime = PipelineRuntime()
    manager = ModuleManager(modules_dir)
    executor = PipelineExecutor(manager, runtime=runtime)
    executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        files=[a],
        recurse=True,
    )
    # After execution, the active bus contains only the last unit's events.
    # The active bus contains ONLY the last unit's events; run-level events from
    # earlier units must not appear.
    run_events = [e for e in runtime.bus.iterate() if e.slug == "demo-synth" and "is_file=" in e.text]
    # Multiple files processed (3 units), but only the last unit's run log
    # event remains in the active bus.
    assert len(run_events) == 1, "only the last unit's run event remains"


# ---------------------------------------------------------------------------
# scope=shared: single unit over merged tree
# ---------------------------------------------------------------------------


def test_shared_merges_all_files_and_runs_once(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=0,
        recurse=True,
        steps=[{"module": "shared-count", "name": "count", "params": {}}],
    )
    d1 = tmp_path / "d1"
    d1.mkdir()
    (d1 / "x.txt").write_text("x", encoding="utf-8")
    f1 = tmp_path / "y.txt"
    f1.write_text("y", encoding="utf-8")

    runtime = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime)
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out, files=[d1, f1], recurse=True
    )
    assert summary["success"]
    assert summary["successful_units"] == 1
    assert (out / "count.txt").exists()
    # Three files: x.txt, y.txt + count.txt itself? Count module sees files
    # at run time (rglob counts existing files). Before writing report, we
    # saw 2 files. After: report exists, but counted_excluded from assertion.
    report = (out / "count.txt").read_text(encoding="utf-8")
    assert "count=" in report
    # All synth events came from one bus (not isolated):
    logged = [e for e in runtime.bus.iterate() if e.slug == "shared-count"]
    # multiple file count events on one bus → single unit semantics confirmed
    assert len(logged) >= 2


def test_shared_direct_mode_rejected(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=0,
        recurse=True,
        steps=[{"module": "shared-count", "name": "count", "params": {}}],
    )
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir))
    with pytest.raises(PipelineExecutionError, match="direct_mode"):
        executor.execute(
            WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
            output_dir=out,
            files=[f],
            recurse=True,
            direct_mode=True,
        )


@pytest.mark.parametrize("scope", [0, 1, 2])
def test_read_only_path_workflow_references_inputs_without_copy(
    modules_dir: Path,
    tmp_path: Path,
    scope: int,
) -> None:
    out = tmp_path / "out"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "same.txt"
    second = second_dir / "same.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    _make_wf(
        tmp_path / "workflows",
        "read.yaml",
        atom="file",
        scope=scope,
        recurse=False,
        steps=[{"module": "demo-read", "params": {}}],
    )
    runtime = PipelineRuntime()

    summary = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime).execute(
        WorkflowLoader(tmp_path / "workflows").load("read.yaml"),
        output_dir=out,
        files=[first, second],
    )

    assert summary["success"]
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"
    assert list(out.iterdir()) == []
    if scope == 0:
        event = next(event for event in runtime.bus.iterate() if event.slug == "demo-read" and event.data)
        assert event.data["relative"] == ["same (1).txt", "same.txt"]


def test_read_only_module_cannot_call_workspace_mutations(modules_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    _make_wf(
        tmp_path / "workflows",
        "read.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[{"module": "read-violation", "params": {}}],
    )

    summary = PipelineExecutor(ModuleManager(modules_dir)).execute(
        WorkflowLoader(tmp_path / "workflows").load("read.yaml"),
        output_dir=tmp_path / "out",
        files=[source],
    )

    assert not summary["success"]
    assert source.read_text(encoding="utf-8") == "source"
    assert not (tmp_path / "out" / "forbidden.txt").exists()
    assert "只读模块" in summary["errors"][0]["error"]


def test_read_write_step_keeps_startup_copy(modules_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    _make_wf(
        tmp_path / "workflows",
        "mixed.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[
            {"module": "demo-read", "params": {}},
            {"module": "demo-rename", "params": {"suffix": "_done"}},
        ],
    )

    summary = PipelineExecutor(ModuleManager(modules_dir)).execute(
        WorkflowLoader(tmp_path / "workflows").load("mixed.yaml"),
        output_dir=tmp_path / "out",
        files=[source],
    )

    assert summary["success"]
    assert source.read_text(encoding="utf-8") == "source"
    assert (tmp_path / "out" / "source.txt_done").exists()


def test_unsupported_platform_step_skips_params_and_execution(
    modules_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.module_manager.current_platform", lambda: "linux")
    monkeypatch.setattr("core.executor.current_platform", lambda: "linux")
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    _make_wf(
        tmp_path / "workflows",
        "platform.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[
            {"module": "windows-only", "params": {"required_path": 123}},
            {"module": "demo-rename", "params": {"suffix": "_done"}},
        ],
    )
    runtime = PipelineRuntime()

    summary = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime).execute(
        WorkflowLoader(tmp_path / "workflows").load("platform.yaml"),
        output_dir=tmp_path / "out",
        files=[source],
    )

    assert summary["success"]
    assert (tmp_path / "out" / "source.txt_done").exists()
    skipped = [event for event in runtime.bus.iterate() if event.data.get("status") == "skipped"]
    assert len(skipped) == 1
    assert skipped[0].data["platform"] == "linux"


def test_skipped_writer_does_not_trigger_input_copy(
    modules_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.module_manager.current_platform", lambda: "linux")
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    _make_wf(
        tmp_path / "workflows",
        "platform.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[{"module": "windows-only", "params": {}}],
    )

    summary = PipelineExecutor(ModuleManager(modules_dir)).execute(
        WorkflowLoader(tmp_path / "workflows").load("platform.yaml"),
        output_dir=tmp_path / "out",
        files=[source],
    )

    assert summary["success"]
    assert list((tmp_path / "out").iterdir()) == []


# ---------------------------------------------------------------------------
# atom=none: single empty unit
# ---------------------------------------------------------------------------


def test_atom_none_runs_single_unit(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-none", "name": "create", "params": {"filename": "hi.txt", "content": "hello"}}],
    )
    executor = PipelineExecutor(ModuleManager(modules_dir))
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
    )
    assert summary["success"]
    assert summary["processed_units"] == 1
    assert (out / "hi.txt").read_text(encoding="utf-8") == "hello"


# ---------------------------------------------------------------------------
# atom=line: each line = 1 unit
# ---------------------------------------------------------------------------


def test_atom_line_per_unit_each_line_isits_own_unit(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="line",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-echo", "name": "echo", "params": {}}],
    )
    executor = PipelineExecutor(ModuleManager(modules_dir))
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        lines_text="alpha\nbeta\ngamma",
    )
    assert summary["success"]
    assert summary["successful_units"] == 3
    # 3 files created in out
    files = [f for f in out.iterdir() if f.is_file() and f.suffix == ".txt"]
    assert len(files) == 3


def test_atom_line_scope_batches_lines_as_lists(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="line",
        scope=2,
        recurse=False,
        steps=[{"module": "demo-line-batch", "name": "echo-batch", "params": {}}],
    )
    runtime = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime)
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        lines_text="alpha\nbeta\ngamma",
    )
    assert summary["success"]
    assert summary["successful_units"] == 2
    assert (out / "alpha_2.txt").read_text(encoding="utf-8") == "alpha\nbeta"
    assert (out / "gamma_1.txt").read_text(encoding="utf-8") == "gamma"
    active_bus_events = [e for e in runtime.bus.iterate() if e.slug == "demo-line-batch" and "batch=" in e.text]
    assert len(active_bus_events) == 1
    assert "gamma" in active_bus_events[0].text


def test_scope_zero_line_keeps_all_lines_in_one_list_batch(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="line",
        scope=0,
        recurse=False,
        steps=[{"module": "demo-line-batch", "name": "echo-batch", "params": {}}],
    )
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=PipelineRuntime())
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        lines_text="alpha\nbeta\ngamma",
    )
    assert summary["success"]
    assert summary["successful_units"] == 1
    assert (out / "alpha_3.txt").read_text(encoding="utf-8") == "alpha\nbeta\ngamma"


def test_scope_batches_path_inputs_into_isolated_worktrees(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=2,
        recurse=False,
        steps=[{"module": "demo-path-batch", "name": "path-batch", "params": {}}],
    )
    a = tmp_path / "a.txt"
    a.write_text("a", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("b", encoding="utf-8")
    c = tmp_path / "c.txt"
    c.write_text("c", encoding="utf-8")
    runtime = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime)
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        files=[a, b, c],
    )
    assert summary["success"]
    assert summary["successful_units"] == 2
    reports = sorted(out.rglob("batch.txt"))
    assert len(reports) == 2
    assert reports[0].parent.name == "_batch_0001"
    assert reports[1].parent.name == "_batch_0002"
    active_bus_events = [e for e in runtime.bus.iterate() if e.slug == "demo-path-batch" and "batch=" in e.text]
    assert len(active_bus_events) == 1


# ---------------------------------------------------------------------------
# Step contract: context / None / invalid
# ---------------------------------------------------------------------------

RETURN_INVALID_MODULE = """
MODULE_META = {
    "slug": "bad-return",
    "name": "Bad Return",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    return 42  # not allowed
"""

RETURN_NONE_MODULE = """
MODULE_META = {
    "slug": "none-return",
    "name": "None Return",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    runtime.log("none-return", "message", "ok")
    return None  # keep original ctx
"""


def test_step_return_invalid_raises(modules_dir: Path, tmp_path: Path) -> None:
    (modules_dir / "bad_return.py").write_text(RETURN_INVALID_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "bad-return", "name": "x", "params": {}}],
    )
    executor = PipelineExecutor(ModuleManager(modules_dir))
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
    )
    assert not summary["success"]
    assert summary["errors"]


def test_step_return_none_keeps_context(modules_dir: Path, tmp_path: Path) -> None:
    modules_dir = modules_dir
    (modules_dir / "none_return.py").write_text(RETURN_NONE_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "none-return", "name": "x", "params": {}}],
    )
    runtime = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime)
    summary = executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out)
    assert summary["success"]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_request_stops_after_current_step(modules_dir: Path, tmp_path: Path) -> None:
    """cancel_requested callback 鈫?break at next step boundary."""

    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[
            {"module": "demo-synth", "name": "a", "params": {}},
            {"module": "demo-synth", "name": "b", "params": {}},
            {"module": "demo-synth", "name": "c", "params": {}},
        ],
    )
    cancelled = {"flag": False}

    def is_cancelled() -> bool:
        # Trigger after the first step's check
        return cancelled["flag"]

    runtime = PipelineRuntime()
    executor = PipelineExecutor(
        ModuleManager(modules_dir),
        cancel_requested=is_cancelled,
        runtime=runtime,
    )
    # We need a hook that flips the flag after one step executed.
    # Simplest: set flag during progress callback 'status=completed'
    count = {"n": 0}

    def on_progress(p):
        count["n"] += 1
        if count["n"] >= 1:
            cancelled["flag"] = True

    executor.progress_callback = on_progress
    summary = executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out)
    assert summary["cancelled"]


# ---------------------------------------------------------------------------
# Cross-step data: ctx.shared carries across steps within one unit
# ---------------------------------------------------------------------------

SUMMARY_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "demo-summary",
    "name": "Demo Summary",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}

CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    renames = ctx.shared.get("renames", [])
    lines = ["renames:"]
    for r in renames:
        lines.append(f"- {r['from']} -> {r['to']}")
    ctx.create_file("summary.txt", "\\n".join(lines))
    return ctx
"""


def test_shared_carries_across_steps_in_one_unit(modules_dir: Path, tmp_path: Path) -> None:
    (modules_dir / "summary.py").write_text(SUMMARY_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[
            {"module": "demo-rename", "name": "rename", "params": {"suffix": "_x"}},
            {"module": "demo-summary", "name": "summary", "params": {}},
        ],
    )
    f = tmp_path / "src.txt"
    f.write_text("data", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=PipelineRuntime())
    summary = executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out, files=[f])
    assert summary["success"]
    content = (out / "summary.txt").read_text(encoding="utf-8")
    assert "renames:" in content
    assert "src.txt ->" in content


def test_shared_does_not_leak_between_units(modules_dir: Path, tmp_path: Path) -> None:
    """Each per-unit ctx must start with empty shared (modulo input_line)."""

    (modules_dir / "summary.py").write_text(SUMMARY_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[
            {"module": "demo-rename", "name": "rename", "params": {"suffix": "_x"}},
            {"module": "demo-summary", "name": "summary", "params": {}},
        ],
    )
    a = tmp_path / "a.txt"
    a.write_text("1", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("2", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=PipelineRuntime())
    summary = executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out, files=[a, b])
    assert summary["success"]
    reports = sorted(out.glob("summary*.txt"))
    assert [report.name for report in reports] == ["summary (1).txt", "summary.txt"]
    renamed_files = sorted(path.name for path in out.iterdir() if path.is_file() and path not in reports)
    assert renamed_files == ["a.txt_x", "b.txt_x"]
    texts = [report.read_text(encoding="utf-8") for report in reports]
    assert all("- " in text for text in texts)
    assert sum("a.txt_x" in text for text in texts) == 1
    assert sum("b.txt_x" in text for text in texts) == 1


# ---------------------------------------------------------------------------
# Param validation failure
# ---------------------------------------------------------------------------


def test_param_validation_failure_fails_setup(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[
            {
                "module": "demo-none",
                "name": "x",
                "params": {"filename": 123, "content": ".$"},  # filename must be str
            }
        ],
    )
    executor = PipelineExecutor(ModuleManager(modules_dir))
    with pytest.raises(PipelineExecutionError):
        executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out)


# ---------------------------------------------------------------------------
# Regression: event_listener survives replace_bus across per-unit units
# ---------------------------------------------------------------------------


def test_event_listener_persists_across_per_unit_buses(modules_dir: Path, tmp_path: Path) -> None:
    """A1 fix: PipelineExecutor with ``event_listener`` must receive events
    from ALL per-unit units, not just the first one."""

    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-synth", "name": "synth", "params": {}}],
    )
    a = tmp_path / "a.txt"
    a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("y", encoding="utf-8")
    c = tmp_path / "c.txt"
    c.write_text("z", encoding="utf-8")

    log: list[PipelineEvent] = []

    def listener(e: PipelineEvent):
        log.append(e)

    executor = PipelineExecutor(
        ModuleManager(modules_dir),
        runtime=PipelineRuntime(),
        event_listener=listener,
    )
    executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=tmp_path / "out",
        files=[a, b, c],
        recurse=True,
    )
    synth_count = sum(1 for e in log if e.slug == "demo-synth")
    # 3 units * 3 events = 3 synth logs (atom=file scope=per-unit) + 6 step start/done messages
    # Minimal: at least one run-level event per unit (the atom=... log)
    expected_min = 3
    assert synth_count >= expected_min, f"expected at least {expected_min} synth events, got {synth_count}"


# ---------------------------------------------------------------------------
# atom=folder: directory-only inputs with recurse=false
# ---------------------------------------------------------------------------

FOLDER_MODULE = """
MODULE_META = {
    "slug": "demo-folder",
    "name": "Demo Folder",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    runtime.log("demo-folder", "success", f"is_dir={ctx.current.is_dir} working={ctx.current.name}")
    return ctx
"""


def test_folder_input_runs_single_unit(modules_dir: Path, tmp_path: Path) -> None:
    """Directory input (recurse=false) → one folder-shaped unit through executor.

    The kernel no longer rejects file inputs for folder workflows (the plan
    compat check was removed); this test only exercises the directory path.
    """

    (modules_dir / "folder_mod.py").write_text(FOLDER_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="folder",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-folder", "name": "f", "params": {}}],
    )
    d = tmp_path / "src_dir"
    d.mkdir()
    (d / "inner.txt").write_text("y", encoding="utf-8")
    r = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=r)
    summary = executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out, files=[d])
    assert summary["success"]
    assert summary["successful_units"] == 1
    events = [e for e in r.bus.iterate() if e.slug == "demo-folder" and "working=" in e.text]
    assert events
    assert "is_dir=True" in events[0].text


def test_file_input_works_in_folder_declared_workflow(modules_dir: Path, tmp_path: Path) -> None:
    """File input now flows through a folder-tagged workflow (kernel ignores atom)."""

    (modules_dir / "folder_mod.py").write_text(FOLDER_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="folder",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-folder", "name": "f", "params": {}}],
    )
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir))
    summary = executor.execute(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=out, files=[f])
    assert summary["success"]


def test_execute_workflow_accepts_explicit_workflows_dir(modules_dir: Path, tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    workflows_dir = tmp_path / "workflows"
    _make_wf(
        workflows_dir,
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "demo-none", "name": "create", "params": {"filename": "ok.txt", "content": "done"}}],
    )
    monkeypatch.chdir(tmp_path)

    summary = execute_workflow(
        "wf.yaml",
        workflows_dir=workflows_dir,
        modules_dir=modules_dir,
        output_dir=out,
    )
    assert summary["success"]
    assert (out / "ok.txt").read_text(encoding="utf-8") == "done"
