"""Workflow scheduler: concurrency, file watching, and cron scheduling shell.

Sits atop ``PipelineExecutor``, adding three orthogonal capabilities:

* ``concurrency`` — parallel unit dispatch via ``ThreadPoolExecutor``.
* ``watch`` — filesystem change detection via ``watchdog``; respects ``recurse``.
* ``cron`` — periodic execution via ``croniter``.

No auto-start/daemon logic.  The scheduler blocks in wait loops and is
terminated when the session exits.  When none of the scheduling params are
active the scheduler falls back to the plain ``PipelineExecutor`` path with
zero overhead.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import croniter
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from .context import PipelineContext
from .exceptions import PipelineCancelledError, PipelineExecutionError
from .executor import (
    PreparedStep,
    _build_line_batches,
    _build_path_batches,
    _resolve_workflow_definition,
    _unit_display,
    build_units,
    prepare_context,
    prepare_steps,
    resolve_step_result,
)
from .files import WorkingCopier, units_from_plan
from .input import InputPlan, resolve_input
from .module_manager import ModuleDefinition, ModuleManager
from .runtime import PipelineRuntime
from .workflow_loader import WorkflowDefinition

LOGGER = logging.getLogger(__name__)


def _run_steps(
    ctx: PipelineContext,
    steps: list[PreparedStep],
    runtime: PipelineRuntime,
    *,
    cancel_fn: Callable[[], bool] | None = None,
) -> PipelineContext:
    """Execute a sequence of steps for a single unit in the current thread."""
    current = ctx
    for step in steps:
        if runtime.is_cancelled():
            raise PipelineCancelledError("execution cancelled")
        if cancel_fn is not None and cancel_fn():
            raise PipelineCancelledError("execution cancelled")
        result = step.module_definition.run(current, dict(step.params), runtime)
        current = resolve_step_result(step_name=step.name, result=result, fallback=current)
    return current


def _merge_summary(
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    output_dir: Path,
    workflow: WorkflowDefinition,
) -> dict[str, Any]:
    total = len(results) + len(errors)
    successful = len(results)
    success = not errors
    return {
        "success": success,
        "cancelled": False,
        "processed_units": total,
        "successful_units": successful,
        "failed_units": len(errors),
        "errors": errors,
        "results": results,
        "workflow": workflow.meta.name,
        "scope": workflow.scope,
        "output_dir": str(output_dir),
    }


# ---------------------------------------------------------------------------
# File-watch handler (watchdog)
# ---------------------------------------------------------------------------


class _ChangeHandler(FileSystemEventHandler):
    """Collect changed paths into a thread-safe set consumed by the watch loop."""

    def __init__(self, changed: set[Path], lock: threading.Lock) -> None:
        super().__init__()
        self._changed = changed
        self._lock = lock

    def on_modified(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            with self._lock:
                self._changed.add(Path(event.src_path))

    def on_created(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            with self._lock:
                self._changed.add(Path(event.src_path))

    def on_deleted(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            with self._lock:
                self._changed.add(Path(event.src_path))


# ---------------------------------------------------------------------------
# WorkflowScheduler
# ---------------------------------------------------------------------------


class WorkflowScheduler:
    """Scheduling shell for ``PipelineExecutor``.

    Parameters
    ----------
    module_manager:
        Pre-built ``ModuleManager`` (shared across all runs).
    concurrency:
        Number of parallel workers for per-unit execution.  ``1`` (default)
        means sequential — the existing ``PipelineExecutor`` path is used.
    watch:
        When ``True``, watch input files for changes and re-execute affected
        units.  Only meaningful for ``kind="path"`` plans.
    cron:
        Standard 5-field cron expression.  When set the scheduler enters a
        loop that wakes at each cron tick and executes the full workflow.
    """

    def __init__(
        self,
        module_manager: ModuleManager,
        *,
        concurrency: int = 1,
        watch: bool = False,
        cron: str | None = None,
    ) -> None:
        self._module_manager = module_manager
        self._concurrency = max(1, concurrency)
        self._watch = watch
        self._cron = cron
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """Signal all loops to stop at the next safe boundary."""
        self._cancel_event.set()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        *,
        output_dir: str | Path,
        files: list[str | Path] | None = None,
        recurse: bool = False,
        lines_text: str | None = None,
        lines_file: str | Path | None = None,
        direct_mode: bool = False,
        enable_log: bool = False,
        shared: Mapping[str, Any] | None = None,
        progress_callback: Any = None,
        event_listener: Any = None,
        workflows_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Execute workflow according to configured scheduling mode.

        Blocks until the run completes (once), until cancelled, or — for
        cron / watch modes — indefinitely.
        """
        plan = resolve_input(
            files=files,
            recurse=recurse,
            lines_text=lines_text,
            lines_file=lines_file,
        )

        if self._cron and self._watch:
            return self._run_cron_and_watch(
                workflow, plan, output_dir,
                direct_mode=direct_mode, enable_log=enable_log,
                shared=shared, recurse=recurse, files=files,
                progress_callback=progress_callback, event_listener=event_listener,
                workflows_dir=workflows_dir,
            )
        if self._cron:
            return self._run_cron_loop(
                workflow, plan, output_dir,
                direct_mode=direct_mode, enable_log=enable_log,
                shared=shared, recurse=recurse, files=files,
                progress_callback=progress_callback, event_listener=event_listener,
                workflows_dir=workflows_dir,
            )
        if self._watch:
            return self._run_watch_loop(
                workflow, plan, output_dir,
                direct_mode=direct_mode, enable_log=enable_log,
                shared=shared, recurse=recurse, files=files,
                progress_callback=progress_callback, event_listener=event_listener,
                workflows_dir=workflows_dir,
            )

        return self._run_once(
            workflow, plan, output_dir,
            direct_mode=direct_mode, enable_log=enable_log,
            shared=shared, recurse=recurse,
            progress_callback=progress_callback, event_listener=event_listener,
            workflows_dir=workflows_dir,
        )

    # ------------------------------------------------------------------
    # Single execution
    # ------------------------------------------------------------------

    def _run_once(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        direct_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        recurse: bool,
        progress_callback: Any = None,
        event_listener: Any = None,
        workflows_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        definition = _resolve_workflow_definition(workflow, workflows_dir=workflows_dir)
        prepared_steps = prepare_steps(definition, self._module_manager)
        copier = WorkingCopier(output_dir, direct_mode=direct_mode)
        units = build_units(definition, plan)

        if self._cancel_event.is_set():
            return {
                "success": False,
                "cancelled": True,
                "processed_units": len(units),
                "successful_units": 0,
                "failed_units": 0,
                "errors": [],
                "results": [],
                "workflow": definition.meta.name,
                "scope": definition.scope,
                "output_dir": str(copier.output_dir),
            }

        if self._concurrency <= 1 or len(units) <= 1:
            return self._run_sequential(
                definition, plan, copier, units, prepared_steps,
                output_dir=output_dir,
                enable_log=enable_log, shared=shared,
                progress_callback=progress_callback, event_listener=event_listener,
            )

        return self._run_parallel(
            definition, plan, copier, units, prepared_steps,
            output_dir=output_dir,
            enable_log=enable_log, shared=shared,
            event_listener=event_listener, progress_callback=progress_callback,
        )

    def _run_sequential(
        self,
        definition: WorkflowDefinition,
        plan: InputPlan,
        copier: WorkingCopier,
        units: list[dict[str, Any]],
        steps: list[PreparedStep],
        *,
        output_dir: str | Path,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        progress_callback: Any = None,
        event_listener: Any = None,
    ) -> dict[str, Any]:
        runtime = PipelineRuntime(
            enable_log=enable_log,
            output_dir=output_dir,
            workflow_slug=definition.meta.slug,
        )
        if event_listener is not None:
            runtime.subscribe(event_listener)

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        total = len(units)

        for idx, unit in enumerate(units, start=1):
            if self._cancel_event.is_set():
                break
            try:
                if definition.scope != 0:
                    runtime.replace_bus()
                ctx = prepare_context(definition, plan, copier, unit, shared=shared)
                final_ctx = _run_steps(ctx, steps, runtime)
                results.append({
                    "success": True,
                    "unit": _unit_display(unit),
                    "working_path": str(final_ctx.working_path),
                })
            except PipelineCancelledError:
                break
            except Exception as exc:
                errors.append({
                    "unit": _unit_display(unit),
                    "error": str(exc),
                    "type": type(exc).__name__,
                })

        runtime.close()
        cancelled = self._cancel_event.is_set()
        return _merge_summary(results, errors, copier.output_dir, definition) | {"cancelled": cancelled}

    def _run_parallel(
        self,
        definition: WorkflowDefinition,
        plan: InputPlan,
        copier: WorkingCopier,
        units: list[dict[str, Any]],
        steps: list[PreparedStep],
        *,
        output_dir: str | Path,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        event_listener: Any = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        # Pre-build all contexts in the main thread
        ctx_list: list[PipelineContext] = []
        for unit in units:
            ctx = prepare_context(definition, plan, copier, unit, shared=shared)
            ctx_list.append(ctx)

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            futures_to_idx: dict[Any, int] = {}
            for idx, (unit, ctx) in enumerate(zip(units, ctx_list)):
                slug = f"{definition.meta.slug}_w{idx:04d}" if definition.meta.slug else f"w{idx:04d}"
                future = pool.submit(
                    _execute_unit_parallel,
                    ctx,
                    steps,
                    enable_log,
                    output_dir,
                    slug,
                    self._cancel_event,
                    event_listener,
                )
                futures_to_idx[future] = idx

            for future in as_completed(futures_to_idx):
                idx = futures_to_idx[future]
                unit = units[idx]
                try:
                    result = future.result()
                    results.append(result)
                    if progress_callback is not None:
                        completed = len(results) + len(errors)
                        progress_callback({
                            "current": completed,
                            "total": len(units),
                            "percent": int(completed * 100 / len(units)) if units else 100,
                            "unit": _unit_display(unit),
                            "status": "completed",
                        })
                except PipelineCancelledError:
                    self._cancel_event.set()
                except Exception as exc:
                    errors.append({
                        "unit": _unit_display(unit),
                        "error": str(exc),
                        "type": type(exc).__name__,
                    })
                    if progress_callback is not None:
                        completed = len(results) + len(errors)
                        progress_callback({
                            "current": completed,
                            "total": len(units),
                            "percent": int(completed * 100 / len(units)) if units else 100,
                            "unit": _unit_display(unit),
                            "status": "failed",
                        })

        cancelled = self._cancel_event.is_set()
        return _merge_summary(results, errors, copier.output_dir, definition) | {"cancelled": cancelled}

    # ------------------------------------------------------------------
    # Cron loop
    # ------------------------------------------------------------------

    def _run_cron_loop(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        direct_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        recurse: bool,
        files: list[str | Path] | None,
        progress_callback: Any = None,
        event_listener: Any = None,
        workflows_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        try:
            schedule = croniter.croniter(self._cron or "* * * * *", datetime.now())
        except (ValueError, KeyError) as exc:
            raise PipelineExecutionError(f"invalid cron expression: {self._cron} — {exc}") from exc

        last_result: dict[str, Any] | None = None
        while not self._cancel_event.is_set():
            next_time = schedule.get_next(datetime)
            delay = (next_time - datetime.now()).total_seconds()
            if delay > 0:
                self._cancel_event.wait(timeout=delay)
            if self._cancel_event.is_set():
                break

            last_result = self._run_once(
                workflow, plan, output_dir,
                direct_mode=direct_mode, enable_log=enable_log,
                shared=shared, recurse=recurse,
                progress_callback=progress_callback, event_listener=event_listener,
                workflows_dir=workflows_dir,
            )

        return last_result or {
            "success": False, "cancelled": True, "processed_units": 0,
            "successful_units": 0, "failed_units": 0, "errors": [],
            "results": [], "workflow": "", "scope": 0, "output_dir": str(output_dir),
        }

    # ------------------------------------------------------------------
    # Watch loop
    # ------------------------------------------------------------------

    def _run_watch_loop(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        direct_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        recurse: bool,
        files: list[str | Path] | None,
        progress_callback: Any = None,
        event_listener: Any = None,
        workflows_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        if plan.kind != "path":
            LOGGER.warning("watch mode requires path input; running once and exiting watch.")
            return self._run_once(
                workflow, plan, output_dir,
                direct_mode=direct_mode, enable_log=enable_log,
                shared=shared, recurse=recurse,
                progress_callback=progress_callback, event_listener=event_listener,
                workflows_dir=workflows_dir,
            )

        raw_files = list(plan.files) if files is None else [Path(f) for f in files]

        last_result = self._run_once(
            workflow, plan, output_dir,
            direct_mode=direct_mode, enable_log=enable_log,
            shared=shared, recurse=recurse,
            progress_callback=progress_callback, event_listener=event_listener,
            workflows_dir=workflows_dir,
        )

        watch_dirs = _collect_watch_dirs(raw_files, recurse)
        if not watch_dirs:
            LOGGER.warning("no directories to watch; exiting watch loop.")
            return last_result

        changed: set[Path] = set()
        lock = threading.Lock()
        handler = _ChangeHandler(changed, lock)
        observer = Observer()
        try:
            for d in watch_dirs:
                observer.schedule(handler, str(d), recursive=recurse)
            observer.start()

            debounce_sec = 0.5
            while not self._cancel_event.is_set():
                self._cancel_event.wait(timeout=1.0)
                with lock:
                    if not changed:
                        continue
                    changed.clear()

                time.sleep(debounce_sec)
                if self._cancel_event.is_set():
                    break

                last_result = self._run_once(
                    workflow, plan, output_dir,
                    direct_mode=direct_mode, enable_log=enable_log,
                    shared=shared, recurse=recurse,
                    progress_callback=progress_callback, event_listener=event_listener,
                    workflows_dir=workflows_dir,
                )
        finally:
            observer.stop()
            observer.join(timeout=5)

        return last_result

    # ------------------------------------------------------------------
    # Cron + Watch combined
    # ------------------------------------------------------------------

    def _run_cron_and_watch(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        direct_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        recurse: bool,
        files: list[str | Path] | None,
        progress_callback: Any = None,
        event_listener: Any = None,
        workflows_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Both cron and watch active: cron-driven execution; file changes trigger immediate re-run."""
        try:
            schedule = croniter.croniter(self._cron or "* * * * *", datetime.now())
        except (ValueError, KeyError) as exc:
            raise PipelineExecutionError(f"invalid cron expression: {self._cron} — {exc}") from exc

        next_cron = schedule.get_next(datetime)

        raw_files = list(plan.files) if files is None else [Path(f) for f in files]
        watch_dirs = _collect_watch_dirs(raw_files, recurse) if plan.kind == "path" else []
        changed: set[Path] = set()
        lock = threading.Lock()
        observer: Observer | None = None

        if watch_dirs:
            handler = _ChangeHandler(changed, lock)
            observer = Observer()
            for d in watch_dirs:
                observer.schedule(handler, str(d), recursive=recurse)
            observer.start()

        last_result: dict[str, Any] | None = None
        try:
            while not self._cancel_event.is_set():
                delay = (next_cron - datetime.now()).total_seconds()

                if delay <= 0:
                    last_result = self._run_once(
                        workflow, plan, output_dir,
                        direct_mode=direct_mode, enable_log=enable_log,
                        shared=shared, recurse=recurse,
                        progress_callback=progress_callback, event_listener=event_listener,
                        workflows_dir=workflows_dir,
                    )
                    next_cron = schedule.get_next(datetime)
                    continue

                self._cancel_event.wait(timeout=max(0.1, min(delay, 1.0)))

                if observer is not None:
                    with lock:
                        has_changes = bool(changed)
                        changed.clear()
                    if has_changes:
                        time.sleep(0.5)
                        if self._cancel_event.is_set():
                            break
                        last_result = self._run_once(
                            workflow, plan, output_dir,
                            direct_mode=direct_mode, enable_log=enable_log,
                            shared=shared, recurse=recurse,
                            progress_callback=progress_callback, event_listener=event_listener,
                            workflows_dir=workflows_dir,
                        )
        finally:
            if observer is not None:
                observer.stop()
                observer.join(timeout=5)

        return last_result or {
            "success": False, "cancelled": True, "processed_units": 0,
            "successful_units": 0, "failed_units": 0, "errors": [],
            "results": [], "workflow": "", "scope": 0, "output_dir": str(output_dir),
        }


# ---------------------------------------------------------------------------
# Parallel worker (module-level, pickleable for ThreadPoolExecutor)
# ---------------------------------------------------------------------------


def _execute_unit_parallel(
    ctx: PipelineContext,
    steps: list[PreparedStep],
    enable_log: bool,
    output_dir: str | Path,
    workflow_slug: str,
    cancel_event: threading.Event,
    event_listener: Any = None,
) -> dict[str, Any]:
    """Execute a single unit's steps in a worker thread."""
    runtime = PipelineRuntime(
        enable_log=enable_log,
        output_dir=output_dir,
        workflow_slug=workflow_slug,
    )
    if event_listener is not None:
        runtime.subscribe(event_listener)
    try:
        final_ctx = _run_steps(ctx, steps, runtime, cancel_fn=cancel_event.is_set)
        return {
            "success": True,
            "unit": _unit_display({}),
            "working_path": str(final_ctx.working_path),
        }
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Watch helpers
# ---------------------------------------------------------------------------


def _collect_watch_dirs(paths: list[Path], recurse: bool) -> set[Path]:
    """Collect parent directories to watch.  With ``recurse`` we watch the
    input path itself so children are picked up.  Without recurse we watch
    the parent and filter manually — but for simplicity we always watch the
    input dir or the file's parent."""
    dirs: set[Path] = set()
    for p in paths:
        if not p.exists():
            continue
        if p.is_dir():
            dirs.add(p)
        else:
            dirs.add(p.parent)
    return dirs
