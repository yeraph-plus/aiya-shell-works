"""WorkflowScheduler: concurrency, cron, watch, cancellation, isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from core import (
    ModuleManager,
    WorkflowLoader,
    WorkflowScheduler,
)
from core.exceptions import PipelineExecutionError

# ---------------------------------------------------------------------------
# Shared helper modules
# ---------------------------------------------------------------------------

TOUCH_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "sched-touch",
    "name": "Sched Touch",
    "core_version": "2.0.0",
    "tags": ["test"],
    "is_file_module": True,
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "suffix": {"type": "str", "default": ".touched"},
    },
}

def run(ctx, cfg, runtime):
    import time
    time.sleep(0.02)
    suffix = cfg["suffix"]
    new = Path(str(ctx.working_path) + suffix)
    Path(ctx.working_path).rename(new)
    return ctx.clone(working_path=new)
"""

NONE_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "sched-none",
    "name": "Sched None",
    "core_version": "2.0.0",
    "tags": ["test"],
    "is_file_module": False,
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "none_out.txt"},
    },
}

def run(ctx, cfg, runtime):
    fp = Path(ctx.output_dir) / cfg["filename"]
    fp.write_text("none_output", encoding="utf-8")
    return ctx.clone(working_path=fp)
"""

SHARED_COUNT = """
from pathlib import Path

MODULE_META = {
    "slug": "sched-count",
    "name": "Sched Count",
    "core_version": "2.0.0",
    "tags": ["test"],
    "is_file_module": True,
    "scope": 0,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    files = sorted(p.name for p in Path(ctx.working_path).rglob("*") if p.is_file())
    runtime.log("sched-count", "success", f"seen={len(files)}")
    report = Path(ctx.working_path) / "report.txt"
    report.write_text(f"count={len(files)}\\n", encoding="utf-8")
    return ctx.clone(extra_files=[*ctx.extra_files, report])
"""


@pytest.fixture()
def modules_dir(tmp_path: Path) -> Path:
    d = tmp_path / "modules"
    d.mkdir()
    (d / "touch.py").write_text(TOUCH_MODULE, encoding="utf-8")
    (d / "none.py").write_text(NONE_MODULE, encoding="utf-8")
    (d / "count.py").write_text(SHARED_COUNT, encoding="utf-8")
    return d


def _make_wf(
    workflows_dir: Path,
    name: str,
    atom: str,
    scope: int,
    recurse: bool,
    steps: list[dict],
    meta_name: str = "WF",
    meta_slug: str = "",
) -> Path:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    path = workflows_dir / name
    doc: dict[str, Any] = {
        "meta": {"name": meta_name, "slug": meta_slug, "version": "1.0.0"},
        "atom": atom,
        "scope": scope,
        "recurse": recurse,
        "steps": steps,
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
    return path


# ---------------------------------------------------------------------------
# Concurrency — parallel unit execution
# ---------------------------------------------------------------------------


def test_concurrent_units_execute_in_parallel(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-touch", "params": {"suffix": "_done"}}],
        meta_slug="test",
    )

    a = tmp_path / "a.txt"; a.write_text("1", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("2", encoding="utf-8")
    c = tmp_path / "c.txt"; c.write_text("3", encoding="utf-8")
    d = tmp_path / "d.txt"; d.write_text("4", encoding="utf-8")

    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr, concurrency=3)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        files=[a, b, c, d],
    )
    assert summary["success"]
    assert summary["successful_units"] == 4
    assert summary["processed_units"] == 4
    assert not summary["errors"]


def test_concurrent_scope_none_runs_single_unit(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {"filename": "hi.txt"}}],
        meta_slug="test",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr, concurrency=2)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
    )
    assert summary["success"]
    assert (out / "hi.txt").read_text(encoding="utf-8") == "none_output"


def test_shared_scope_runs_sequentially_even_with_concurrency(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=0,
        recurse=True,
        steps=[{"module": "sched-count", "params": {}}],
        meta_slug="test",
    )
    a = tmp_path / "a.txt"; a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("y", encoding="utf-8")

    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr, concurrency=4)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        files=[a, b],
    )
    assert summary["success"]
    assert summary["successful_units"] == 1


def test_concurrent_each_thread_isolated_no_event_bleeding(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="file",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-touch", "params": {"suffix": "_x"}}],
        meta_slug="test",
    )
    a = tmp_path / "a.txt"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("b", encoding="utf-8")
    c = tmp_path / "c.txt"; c.write_text("c", encoding="utf-8")

    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr, concurrency=3)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        files=[a, b, c],
    )
    assert summary["success"]
    touched = sorted(out.rglob("*_x"))
    assert len(touched) == 3


# ---------------------------------------------------------------------------
# Concurrency — cancellation
# ---------------------------------------------------------------------------


def test_cancel_before_run_returns_cancelled(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {}}],
        meta_slug="test",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr)
    scheduler.request_cancel()
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
    )
    assert summary["cancelled"]
    assert summary["successful_units"] == 0


# ---------------------------------------------------------------------------
# Invalid cron expression
# ---------------------------------------------------------------------------


def test_invalid_cron_expression_raises(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {}}],
        meta_slug="test",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr, cron="not-a-cron-expression")
    scheduler.request_cancel()
    try:
        scheduler.run(
            WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
            output_dir=out,
        )
    except PipelineExecutionError as exc:
        assert "cron" in str(exc).lower()
    else:
        raise AssertionError("expected PipelineExecutionError for invalid cron")


# ---------------------------------------------------------------------------
# Watch mode — non-path input
# ---------------------------------------------------------------------------


def test_watch_with_none_input_runs_once_and_returns(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {"filename": "ok.txt"}}],
        meta_slug="test",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr, watch=True)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
    )
    assert summary["success"]
    assert (out / "ok.txt").exists()


# ---------------------------------------------------------------------------
# No scheduler params — direct executor path
# ---------------------------------------------------------------------------


def test_no_scheduler_params_uses_executor_directly(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {"filename": "direct.txt"}}],
        meta_slug="test",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
    )
    assert summary["success"]
    assert (out / "direct.txt").exists()


# ---------------------------------------------------------------------------
# Log output — enable_log
# ---------------------------------------------------------------------------


def test_enable_log_writes_jsonl_to_output_dir(modules_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {"filename": "log_test.txt"}}],
        meta_slug="test-wf",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr)
    summary = scheduler.run(
        WorkflowLoader(tmp_path / "workflows").load("wf.yaml"),
        output_dir=out,
        enable_log=True,
    )
    assert summary["success"]
    logs = list(out.glob("*.jsonl"))
    assert logs, "expected a log file in output_dir"


# ---------------------------------------------------------------------------
# Unit helpers (_prepare_steps, _build_units)
# ---------------------------------------------------------------------------


def test_prepare_steps_validates_params(modules_dir: Path, tmp_path: Path) -> None:
    wf_dir = tmp_path / "workflows"
    _make_wf(
        wf_dir,
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {"filename": 999}}],
        meta_slug="test",
    )
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr)
    with pytest.raises(PipelineExecutionError):
        scheduler.run(WorkflowLoader(wf_dir).load("wf.yaml"), output_dir=tmp_path / "out")


def test_prepare_steps_unknown_module_raises(modules_dir: Path, tmp_path: Path) -> None:
    mgr = ModuleManager(modules_dir)
    scheduler = WorkflowScheduler(mgr)
    _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "nonexistent-module", "params": {}}],
        meta_slug="test",
    )
    with pytest.raises(PipelineExecutionError):
        scheduler.run(WorkflowLoader(tmp_path / "workflows").load("wf.yaml"), output_dir=tmp_path / "out")
