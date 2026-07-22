"""Regression coverage for Linux migration and scheduler/executor integration."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from core import ModuleManager, PipelineExecutor, PipelineRuntime, WorkflowScheduler
from core.exceptions import FileHandlingError, TerminalSpawnError
from core.input import resolve_input

MODULE_SOURCE = """
from pathlib import Path

MODULE_META = {
    "slug": "repair-fixture",
    "name": "Repair Fixture",
    "core_version": "2.0.0",
    "tags": ["test"],
    "is_file_module": True,
}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "str", "default": "pass"},
    },
}

def run(ctx, cfg, runtime):
    action = cfg["action"]
    if action == "log-error":
        runtime.log("repair-fixture", "error", "diagnostic only")
        return ctx
    if action == "raise":
        raise RuntimeError("fixture failure")
    if action == "count":
        count = len([path for path in Path(ctx.working_path).rglob("*") if path.is_file()])
        report = Path(ctx.output_dir) / "count.txt"
        report.write_text(f"count={count}\\n", encoding="utf-8")
        return ctx.clone(extra_files=[*ctx.extra_files, report])
    if action == "terminal":
        delay = "0.1" if Path(ctx.working_path).name.startswith("fast") else "0.8"
        result = runtime.spawn(["/bin/sh", "-c", f"sleep {delay}; printf done"])
        if not result.is_success:
            raise RuntimeError(f"terminal exit={result.exit_code}")
        return ctx
    return ctx
"""


@pytest.fixture()
def module_manager(tmp_path: Path) -> ModuleManager:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "repair_fixture.py").write_text(MODULE_SOURCE, encoding="utf-8")
    return ModuleManager(modules_dir)


def _workflow(*, action: str, scope: int = 1) -> dict:
    return {
        "meta": {"slug": "repair", "name": "Repair"},
        "scope": scope,
        "recurse": False,
        "steps": [{"module": "repair-fixture", "params": {"action": action}}],
    }


def test_error_event_is_diagnostic_but_exception_fails(module_manager: ModuleManager, tmp_path: Path) -> None:
    logged = PipelineExecutor(module_manager).execute(
        _workflow(action="log-error"),
        output_dir=tmp_path / "logged",
    )
    failed = PipelineExecutor(module_manager).execute(
        _workflow(action="raise"),
        output_dir=tmp_path / "failed",
    )

    assert logged["success"]
    assert not failed["success"]
    assert failed["errors"][0]["type"] == "ModuleExecutionError"


def test_shared_repeated_output_uses_clean_workspace(module_manager: ModuleManager, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("input", encoding="utf-8")
    output = tmp_path / "output"
    executor = PipelineExecutor(module_manager)

    first = executor.execute(_workflow(action="count", scope=0), output_dir=output, files=[source])
    first_count = (output / "count.txt").read_text(encoding="utf-8")
    (output / "old-unrelated.txt").write_text("keep", encoding="utf-8")
    second = executor.execute(_workflow(action="count", scope=0), output_dir=output, files=[source])

    assert first["success"] and second["success"]
    assert first_count == "count=1\n"
    assert (output / "count.txt").read_text(encoding="utf-8") == "count=1\n"
    assert (output / "old-unrelated.txt").read_text(encoding="utf-8") == "keep"


def test_nested_output_is_rejected_before_creation(module_manager: ModuleManager, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("x", encoding="utf-8")
    output = source / "output"

    with pytest.raises(FileHandlingError):
        PipelineExecutor(module_manager).execute(
            _workflow(action="pass"),
            output_dir=output,
            files=[source],
        )
    assert not output.exists()


def test_copy_input_inside_output_is_also_rejected(module_manager: ModuleManager, tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    source = output / "input.txt"
    source.write_text("x", encoding="utf-8")

    with pytest.raises(FileHandlingError):
        PipelineExecutor(module_manager).execute(
            _workflow(action="pass"),
            output_dir=output,
            files=[source],
        )


def test_watch_rejects_overlapping_output_before_observer_starts(
    module_manager: ModuleManager,
    tmp_path: Path,
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()

    with pytest.raises(FileHandlingError):
        WorkflowScheduler(module_manager, watch=True).run(
            _workflow(action="pass"),
            output_dir=watched / "output",
            files=[watched],
            recurse=True,
        )


def test_move_failure_still_lands_input_in_output(module_manager: ModuleManager, tmp_path: Path) -> None:
    source = tmp_path / "watched.txt"
    source.write_text("important", encoding="utf-8")
    output = tmp_path / "output"

    summary = PipelineExecutor(module_manager).execute(
        _workflow(action="raise"),
        output_dir=output,
        files=[source],
        move_mode=True,
    )

    assert not summary["success"]
    assert not source.exists()
    assert (output / "watched.txt").read_text(encoding="utf-8") == "important"


def test_globs_expand_recursively_deduplicate_and_reject_unmatched(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    visible = nested / "visible.txt"
    hidden = nested / ".hidden.txt"
    visible.write_text("v", encoding="utf-8")
    hidden.write_text("h", encoding="utf-8")

    plan = resolve_input(files=[str(source / "**" / "*.txt"), visible])
    hidden_plan = resolve_input(files=[str(source / "**" / ".*.txt")])

    assert plan.files == (visible,)
    assert hidden_plan.files == (hidden,)
    with pytest.raises(Exception, match="未匹配"):
        resolve_input(files=[str(source / "*.missing")])


def test_scheduler_reports_unit_and_single_unit_progress(module_manager: ModuleManager, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    progress: list[dict] = []

    summary = WorkflowScheduler(module_manager, concurrency=2).run(
        _workflow(action="pass"),
        output_dir=tmp_path / "output",
        files=[source],
        progress_callback=progress.append,
    )

    assert summary["results"][0]["unit"] == str(source)
    assert progress[0]["status"] == "starting"
    assert progress[-1]["status"] == "done"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process command")
def test_parallel_runtime_close_does_not_terminate_other_sessions(
    module_manager: ModuleManager,
    tmp_path: Path,
) -> None:
    fast = tmp_path / "fast.txt"
    slow = tmp_path / "slow.txt"
    fast.write_text("fast", encoding="utf-8")
    slow.write_text("slow", encoding="utf-8")

    summary = WorkflowScheduler(module_manager, concurrency=2).run(
        _workflow(action="terminal"),
        output_dir=tmp_path / "output",
        files=[fast, slow],
    )

    assert summary["success"]
    assert summary["successful_units"] == 2


def test_watch_has_no_initial_run_and_processes_new_file(module_manager: ModuleManager, tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    existing = watched / "existing.txt"
    existing.write_text("old", encoding="utf-8")
    output = tmp_path / "output"
    scheduler = WorkflowScheduler(module_manager, watch=True)
    result: list[dict] = []

    thread = threading.Thread(
        target=lambda: result.append(
            scheduler.run(
                _workflow(action="pass"),
                output_dir=output,
                files=[watched],
                recurse=True,
            )
        ),
        daemon=True,
    )
    thread.start()
    time.sleep(0.4)
    assert not output.exists()

    created = watched / "created.txt"
    created.write_text("new", encoding="utf-8")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (output / "created.txt").exists():
        time.sleep(0.1)
    scheduler.request_cancel()
    thread.join(timeout=5)

    assert (output / "created.txt").read_text(encoding="utf-8") == "new"
    assert not (output / "existing.txt").exists()
    assert result and result[0]["successful_units"] == 1


def test_async_terminal_shell_and_missing_command() -> None:
    runtime = PipelineRuntime()
    session = runtime.start("printf shell-ok", shell=True)
    result = session.wait(timeout=5)

    assert result.is_success
    assert "shell-ok" in result.output_text
    assert len(runtime.sessions) == 0
    with pytest.raises(TerminalSpawnError):
        runtime.spawn(["/definitely/not/a/real/executable"])
