"""PipelineExecutor: atom x scope behavior, per-unit isolation, step contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest
import yaml

from core import (
    EventBus, ModuleManager, PipelineEvent, PipelineExecutor, PipelineRuntime,
    WorkflowDefinition, WorkflowLoader, WorkflowMeta, WorkflowStep,
    resolve_input,
)
from core.exceptions import PipelineExecutionError


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

RENAME_MODULE = '''
import os
from pathlib import Path

MODULE_META = {
    "slug": "demo-rename",
    "name": "Demo Rename",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["file", "folder"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "suffix": {"type": "str", "default": "_done"},
    },
}

def run(ctx, cfg, runtime):
    suffix = cfg["suffix"]
    new = Path(str(ctx.working_path) + suffix)
    Path(ctx.working_path).rename(new)
    renames = list(ctx.shared.get("renames", []))
    renames.append({"from": str(ctx.working_path), "to": str(new)})
    updated = ctx.clone(working_path=new, shared={**ctx.shared, "renames": renames})
    return updated
'''

SHARED_COUNT_MODULE = '''
from pathlib import Path

MODULE_META = {
    "slug": "shared-count",
    "name": "Shared Count",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["file"],
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
    files = sorted(p for p in Path(ctx.working_path).rglob("*") if p.is_file())
    for i, fp in enumerate(files, 1):
        runtime.log("shared-count", "success", f"{i}: {fp.name}")
    report = Path(ctx.working_path) / cfg["report_name"]
    report.write_text(f"count={len(files)}\\n", encoding="utf-8")
    return ctx.clone(extra_files=[*ctx.extra_files, report])
'''

LINE_ECHO_MODULE = '''
from pathlib import Path

MODULE_META = {
    "slug": "demo-echo",
    "name": "Demo Echo",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["line"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {}
}

def run(ctx, cfg, runtime):
    line = ctx.shared.get("input_line", "")
    fp = Path(ctx.output_dir) / f"{abs(hash(line)) & 0xffff}.txt"
    fp.write_text(line + "\\n", encoding="utf-8")
    return ctx.clone(working_path=fp, extra_files=[*ctx.extra_files, fp])
'''

NONE_MODULE = '''
from pathlib import Path

MODULE_META = {
    "slug": "demo-none",
    "name": "Demo None",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["none"],
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "hello.txt"},
        "content": {"type": "str", "default": "hi"},
    },
}

def run(ctx, cfg, runtime):
    fp = Path(ctx.output_dir) / cfg["filename"]
    fp.write_text(cfg["content"], encoding="utf-8")
    return ctx.clone(working_path=fp, extra_files=[*ctx.extra_files, fp])
'''

SYNTHESIS_MODULE = '''
MODULE_META = {
    "slug": "demo-synth",
    "name": "Demo Synth",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["file", "folder", "line", "none"],
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    runtime.log("demo-synth", "success", f"atom={ctx.atom}")
    return ctx
'''


@pytest.fixture()
def modules_dir(tmp_path: Path) -> Path:
    d = tmp_path / "modules"
    d.mkdir()
    (d / "rename.py").write_text(RENAME_MODULE, encoding="utf-8")
    (d / "shared_count.py").write_text(SHARED_COUNT_MODULE, encoding="utf-8")
    (d / "echo.py").write_text(LINE_ECHO_MODULE, encoding="utf-8")
    (d / "none.py").write_text(NONE_MODULE, encoding="utf-8")
    (d / "synth.py").write_text(SYNTHESIS_MODULE, encoding="utf-8")
    return d


@pytest.fixture()
def workflows_dir(tmp_path: Path) -> Path:
    return tmp_path / "workflows"


def _make_wf(workflows_dir: Path, name: str, atom: str, scope: int, recurse: bool,
             steps: list[dict], meta_name: str = "WF") -> Path:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    path = workflows_dir / name
    doc = {
        "meta": {"name": meta_name, "description": "demo",
                  "version": "1.0.0", "slug": "demo"},
        "atom": atom, "scope": scope, "recurse": recurse,
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
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="file", scope=1, recurse=False,
             steps=[{"module": "demo-synth", "name": "synth", "params": {}}])

    # Two file inputs
    a = tmp_path / "a.txt"; a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("y", encoding="utf-8")

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
    run_events = [e for e in seen if e.slug == "demo-synth"
                  and "atom=" in e.text]
    # Each unit emits exactly one "atom=... scope=..." log from synthesize's run().
    assert run_events, "synth run-level logs must be present"
    assert len(run_events) == 2
    assert all("atom=file" in e.text for e in run_events)


def test_per_unit_isolation_no_event_bleeds(modules_dir: Path, tmp_path: Path) -> None:
    """Verify replace_bus actually clears each unit's events."""

    out = tmp_path / "out"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="file", scope=1, recurse=True,
             steps=[{"module": "demo-synth", "name": "synth", "params": {}}])
    a = tmp_path / "src"; a.mkdir()
    (a / "1.txt").write_text("1", encoding="utf-8")
    (a / "2.txt").write_text("2", encoding="utf-8")
    (a / "3.txt").write_text("3", encoding="utf-8")

    runtime = PipelineRuntime()
    manager = ModuleManager(modules_dir)
    executor = PipelineExecutor(manager, runtime=runtime)
    executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, files=[a], recurse=True,
    )
    # After execution, the active bus contains only the last unit's events.
    # The active bus contains ONLY the last unit's events; run-level events from
    # earlier units must not appear.
    run_events = [e for e in runtime.bus.iterate()
                  if e.slug == "demo-synth" and "atom=" in e.text]
    # Multiple files processed (3 units), but only the last unit's "atom=..."
    # event remains in the active bus.
    assert len(run_events) == 1, "only the last unit's run event remains"


# ---------------------------------------------------------------------------
# scope=shared: single unit over merged tree
# ---------------------------------------------------------------------------

def test_shared_merges_all_files_and_runs_once(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="file", scope=0, recurse=True,
             steps=[{"module": "shared-count", "name": "count", "params": {}}])
    d1 = tmp_path / "d1"; d1.mkdir()
    (d1 / "x.txt").write_text("x", encoding="utf-8")
    f1 = tmp_path / "y.txt"; f1.write_text("y", encoding="utf-8")

    runtime = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime)
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, files=[d1, f1], recurse=True
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
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="file", scope=0, recurse=True,
             steps=[{"module": "shared-count", "name": "count", "params": {}}])
    f = tmp_path / "a.txt"; f.write_text("x", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir))
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, files=[f], recurse=True,
        direct_mode=True,
    )
    # Per-unit isolation swallows the exception; the recorded error carries
    # the FileHandlingError type so GUI/CLI callers can surface the cause.
    assert not summary["success"]
    assert summary["errors"]
    assert "FileHandlingError" in summary["errors"][0]["type"]


# ---------------------------------------------------------------------------
# atom=none: single empty unit
# ---------------------------------------------------------------------------

def test_atom_none_runs_single_unit(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="none", scope=1, recurse=False,
             steps=[{"module": "demo-none", "name": "create",
                     "params": {"filename": "hi.txt", "content": "hello"}}])
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
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="line", scope=1, recurse=False,
             steps=[{"module": "demo-echo", "name": "echo", "params": {}}])
    executor = PipelineExecutor(ModuleManager(modules_dir))
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, lines_text="alpha\nbeta\ngamma",
    )
    assert summary["success"]
    assert summary["successful_units"] == 3
    # 3 files created in out
    files = [f for f in out.iterdir() if f.is_file() and f.suffix == ".txt"]
    assert len(files) == 3


# ---------------------------------------------------------------------------
# Step contract: context / None / dict-with-context / invalid
# ---------------------------------------------------------------------------

RETURN_INVALID_MODULE = '''
MODULE_META = {
    "slug": "bad-return",
    "name": "Bad Return",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["none"],
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    return 42  # not allowed
'''

RETURN_NONE_MODULE = '''
MODULE_META = {
    "slug": "none-return",
    "name": "None Return",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["none"],
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    runtime.log("none-return", "message", "ok")
    return None  # keep original ctx
'''


def test_step_return_invalid_raises(modules_dir: Path, tmp_path: Path) -> None:
    (modules_dir / "bad_return.py").write_text(RETURN_INVALID_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="none", scope=1, recurse=False,
             steps=[{"module": "bad-return", "name": "x", "params": {}}])
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
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="none", scope=1, recurse=False,
             steps=[{"module": "none-return", "name": "x", "params": {}}])
    runtime = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=runtime)
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out)
    assert summary["success"]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancel_request_stops_after_current_step(modules_dir: Path, tmp_path: Path) -> None:
    """cancel_requested callback 鈫?break at next step boundary."""

    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="none", scope=1, recurse=False,
             steps=[
                 {"module": "demo-synth", "name": "a", "params": {}},
                 {"module": "demo-synth", "name": "b", "params": {}},
                 {"module": "demo-synth", "name": "c", "params": {}},
             ])
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
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out)
    assert summary["cancelled"]


# ---------------------------------------------------------------------------
# Cross-step data: ctx.shared carries across steps within one unit
# ---------------------------------------------------------------------------

SUMMARY_MODULE = '''
from pathlib import Path

MODULE_META = {
    "slug": "demo-summary",
    "name": "Demo Summary",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["file"],
}

CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    renames = ctx.shared.get("renames", [])
    fp = Path(ctx.output_dir) / "summary.txt"
    lines = ["renames:"]
    for r in renames:
        lines.append(f"- {r['from']} -> {r['to']}")
    fp.write_text("\\n".join(lines), encoding="utf-8")
    return ctx.clone(extra_files=[*ctx.extra_files, fp])
'''


def test_shared_carries_across_steps_in_one_unit(modules_dir: Path, tmp_path: Path) -> None:
    (modules_dir / "summary.py").write_text(SUMMARY_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="file", scope=1, recurse=False,
             steps=[
                 {"module": "demo-rename", "name": "rename", "params": {"suffix": "_x"}},
                 {"module": "demo-summary", "name": "summary", "params": {}},
             ])
    f = tmp_path / "src.txt"; f.write_text("data", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=PipelineRuntime())
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, files=[f])
    assert summary["success"]
    content = (out / "summary.txt").read_text(encoding="utf-8")
    assert "renames:" in content
    assert "src.txt ->" in content


def test_shared_does_not_leak_between_units(modules_dir: Path, tmp_path: Path) -> None:
    """Each per-unit ctx must start with empty shared (modulo input_line)."""

    (modules_dir / "summary.py").write_text(SUMMARY_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="file", scope=1, recurse=False,
             steps=[
                 {"module": "demo-rename", "name": "rename", "params": {"suffix": "_x"}},
                 {"module": "demo-summary", "name": "summary", "params": {}},
             ])
    a = tmp_path / "a.txt"; a.write_text("1", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("2", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=PipelineRuntime())
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, files=[a, b])
    assert summary["success"]
    # Two summary.txt files would both be the same path 鈥?we expect 1 file
# We expect both inputs to have been renamed into output.
    renamed_files = [f for f in out.iterdir()
                     if f.is_file() and f.name != "summary.txt"]
    assert len(renamed_files) == 2
    # The summary.txt (path-collided under out/root) reflects only the LAST
    # unit's shared dict — proving that per-unit scopes do not leak shared.
    text = (out / "summary.txt").read_text(encoding="utf-8")
    assert "- " in text
    # Critical: must NOT contain BOTH a.txt and b.txt at once (would indicate leakage).
    assert not ("a.txt_x" in text and "b.txt_x" in text)


# ---------------------------------------------------------------------------
# Param validation failure
# ---------------------------------------------------------------------------

def test_param_validation_failure_fails_setup(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    wf_path = tmp_path / "wf.yaml"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="none", scope=1, recurse=False,
             steps=[
                 {"module": "demo-none", "name": "x",
                  "params": {"filename": 123, "content": ".$"}  # filename must be str
                  }])
    executor = PipelineExecutor(ModuleManager(modules_dir))
    with pytest.raises(PipelineExecutionError):
        executor.execute(
            WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
            output_dir=out)


# ---------------------------------------------------------------------------
# Unsupported atom / scope mismatch
# ---------------------------------------------------------------------------

def test_step_atom_not_supported_by_module_rejected(modules_dir: Path, tmp_path: Path) -> None:
    # demo-rename supports atom ["file","folder"]; asking it to run with atom=line must fail.
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="line", scope=1, recurse=False,
             steps=[{"module": "demo-rename", "name": "x", "params": {}}])
    executor = PipelineExecutor(ModuleManager(modules_dir))
    with pytest.raises(PipelineExecutionError):
        executor.execute(
            WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
            output_dir=tmp_path / "out", lines_text="a")


# ---------------------------------------------------------------------------
# Regression: event_listener survives replace_bus across per-unit units
# ---------------------------------------------------------------------------

def test_event_listener_persists_across_per_unit_buses(modules_dir: Path, tmp_path: Path) -> None:
    """A1 fix: PipelineExecutor with ``event_listener`` must receive events
    from ALL per-unit units, not just the first one."""

    _make_wf(tmp_path / "workflows", "wf.yaml",
             atom="file", scope=1, recurse=False,
             steps=[{"module": "demo-synth", "name": "synth", "params": {}}])
    a = tmp_path / "a.txt"; a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("y", encoding="utf-8")
    c = tmp_path / "c.txt"; c.write_text("z", encoding="utf-8")

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

FOLDER_MODULE = '''
MODULE_META = {
    "slug": "demo-folder",
    "name": "Demo Folder",
    "core_version": "2.0.0",
    "tags": ["demo"],
    "atom": ["folder"],
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    runtime.log("demo-folder", "success", f"atom={ctx.atom} working={ctx.working_path.name}")
    return ctx
'''


def test_atom_folder_rejects_file_input(modules_dir: Path, tmp_path: Path) -> None:
    """atom=folder workflow must reject file inputs."""

    (modules_dir / "folder_mod.py").write_text(FOLDER_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="folder", scope=1, recurse=False,
             steps=[{"module": "demo-folder", "name": "f", "params": {}}])
    f = tmp_path / "a.txt"; f.write_text("x", encoding="utf-8")
    executor = PipelineExecutor(ModuleManager(modules_dir))
    with pytest.raises(PipelineExecutionError):
        executor.execute(
            WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
            output_dir=out, files=[f])


def test_atom_folder_with_dir_input_runs_single_unit(modules_dir: Path, tmp_path: Path) -> None:
    """atom=folder + directory input → one folder unit through executor."""

    (modules_dir / "folder_mod.py").write_text(FOLDER_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    _make_wf(tmp_path / "workflows", "wf.yaml", atom="folder", scope=1, recurse=False,
             steps=[{"module": "demo-folder", "name": "f", "params": {}}])
    d = tmp_path / "src_dir"; d.mkdir()
    (d / "inner.txt").write_text("y", encoding="utf-8")
    r = PipelineRuntime()
    executor = PipelineExecutor(ModuleManager(modules_dir), runtime=r)
    summary = executor.execute(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out, files=[d])
    assert summary["success"]
    assert summary["successful_units"] == 1
    events = [e for e in r.bus.iterate() if e.slug == "demo-folder" and "working=" in e.text]
    assert events
    assert "working=" in events[0].text

