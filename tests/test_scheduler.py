"""WorkflowScheduler: concurrency, cron, watch, cancellation, isolation."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import core.scheduler as scheduler_module
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
    "access": "read_write",
    "platforms": None,
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
    renamed = ctx.current.rename(ctx.current.name + suffix)
    return ctx
"""

NONE_MODULE = """
from pathlib import Path

MODULE_META = {
    "slug": "sched-none",
    "name": "Sched None",
    "core_version": "2.0.0",
    "tags": ["test"],
    "access": "read_write",
    "platforms": None,
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "none_out.txt"},
    },
}

def run(ctx, cfg, runtime):
    ctx.create_file(cfg["filename"], "none_output")
    return ctx
"""

SHARED_COUNT = """
from pathlib import Path

MODULE_META = {
    "slug": "sched-count",
    "name": "Sched Count",
    "core_version": "2.0.0",
    "tags": ["test"],
    "access": "read_write",
    "platforms": None,
    "scope": 0,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    files = sorted(entry.name for entry in ctx.files())
    runtime.log("sched-count", "success", f"seen={len(files)}")
    ctx.create_file("report.txt", f"count={len(files)}\\n")
    return ctx
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


def test_change_handler_preserves_order_and_supports_requeue() -> None:
    handler = scheduler_module._ChangeHandler()
    handler.on_modified(SimpleNamespace(is_directory=True, src_path="ignored"))
    handler.on_modified(SimpleNamespace(is_directory=False, src_path="a.txt"))
    handler.on_created(SimpleNamespace(src_path="b.txt"))
    handler.on_moved(SimpleNamespace(dest_path="c.txt"))
    assert handler.changed.is_set()
    assert handler.drain() == [Path("a.txt"), Path("b.txt"), Path("c.txt")]
    assert not handler.changed.is_set()

    handler.requeue([Path("a.txt"), Path("b.txt")])
    handler.discard([Path("a.txt")])
    assert handler.changed.is_set()
    assert handler.drain() == [Path("b.txt")]
    handler.requeue([Path("b.txt")])
    handler.discard([Path("b.txt")])
    assert not handler.changed.is_set()


def test_watch_path_helpers_filter_and_stabilize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    direct = root / "direct.txt"
    direct.write_text("direct", encoding="utf-8")
    nested_directory = root / "nested"
    nested_directory.mkdir()
    nested = nested_directory / "nested.txt"
    nested.write_text("nested", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    assert scheduler_module._collect_watch_dirs([root, outside]) == {root.resolve(), tmp_path.resolve()}
    filtered = scheduler_module._filter_watch_paths(
        [direct, nested, outside],
        [root],
        recurse=False,
    )
    assert filtered == [direct.resolve()]
    recursive = scheduler_module._filter_watch_paths([nested_directory], [root], recurse=True)
    assert recursive == [nested.resolve()]
    assert scheduler_module._filter_watch_paths([outside], [root], recurse=True) == []

    monkeypatch.setattr(scheduler_module, "_STABILITY_POLL_SECONDS", 0.001)
    monkeypatch.setattr(scheduler_module, "_STABILITY_TIMEOUT_SECONDS", 0.2)
    stable, unstable = scheduler_module._wait_for_stable_files(
        [direct, tmp_path / "missing.txt"],
        threading.Event(),
    )
    assert stable == [direct]
    assert unstable == []

    monkeypatch.setattr(scheduler_module, "_STABILITY_TIMEOUT_SECONDS", 0.0)
    stable, unstable = scheduler_module._wait_for_stable_files([direct], threading.Event())
    assert stable == []
    assert unstable == [direct]


def test_scheduler_reentry_cancel_and_session_termination(modules_dir: Path, tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(ModuleManager(modules_dir))
    definition_path = _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {}}],
        meta_slug="test",
    )
    definition = WorkflowLoader(tmp_path / "workflows").load(definition_path.name)

    scheduler._running = True
    with pytest.raises(PipelineExecutionError, match="并发重入"):
        scheduler.run(definition, output_dir=tmp_path / "out")

    class Session:
        terminated = False

        def terminate(self) -> None:
            self.terminated = True

    session = Session()

    class Sessions:
        def get(self, session_id: str):
            return session if session_id == "known" else None

    class Runtime:
        sessions = Sessions()
        cancelled = False

        def request_cancel(self) -> None:
            self.cancelled = True

    runtime = Runtime()
    scheduler._active_runtime = runtime
    assert scheduler.terminate_session("known")
    assert session.terminated
    assert not scheduler.terminate_session("missing")
    scheduler.request_cancel()
    assert runtime.cancelled
    scheduler._active_runtime = None
    assert not scheduler.terminate_session("known")


def test_cron_loop_runs_once_then_returns_last_result(
    modules_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition_path = _make_wf(
        tmp_path / "workflows",
        "wf.yaml",
        atom="none",
        scope=1,
        recurse=False,
        steps=[{"module": "sched-none", "params": {}}],
        meta_slug="cron-test",
    )
    definition = WorkflowLoader(tmp_path / "workflows").load(definition_path.name)
    scheduler = WorkflowScheduler(ModuleManager(modules_dir), cron="* * * * *")

    class Schedule:
        def get_next(self, _kind):
            return datetime.now()

    monkeypatch.setattr(scheduler_module.croniter, "croniter", lambda *args, **kwargs: Schedule())
    expected = {"success": True, "processed_units": 1}

    def run_once(*args, **kwargs):
        scheduler._cancel_event.set()
        return expected

    monkeypatch.setattr(scheduler, "_run_once", run_once)
    result = scheduler._run_cron(
        definition,
        scheduler_module.InputPlan(kind="none"),
        tmp_path / "out",
        direct_mode=False,
        enable_log=False,
        shared=None,
        progress_callback=None,
        event_listener=None,
    )
    assert result is expected
