"""Scheduling triggers layered on the shared :class:`PipelineExecutor`."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

import croniter
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .exceptions import PipelineExecutionError
from .executor import PipelineExecutor, _resolve_workflow_definition
from .files import validate_output_separation
from .input import InputPlan, resolve_input
from .module_manager import ModuleManager
from .runtime import PipelineRuntime
from .workflow_loader import WorkflowDefinition

LOGGER = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 0.5
_STABILITY_POLL_SECONDS = 0.25
_STABILITY_TIMEOUT_SECONDS = 5.0


class _ChangeHandler(FileSystemEventHandler):
    """Collect created, modified and moved destinations in arrival order."""

    def __init__(self) -> None:
        super().__init__()
        self._paths: OrderedDict[Path, None] = OrderedDict()
        self._lock = threading.Lock()
        self.changed = threading.Event()

    def _add(self, path: str) -> None:
        with self._lock:
            self._paths[Path(path)] = None
        self.changed.set()

    def on_modified(self, event: Any) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._add(event.src_path)

    def on_created(self, event: Any) -> None:  # type: ignore[override]
        self._add(event.src_path)

    def on_moved(self, event: Any) -> None:  # type: ignore[override]
        self._add(event.dest_path)

    def drain(self) -> list[Path]:
        with self._lock:
            paths = list(self._paths)
            self._paths.clear()
            self.changed.clear()
        return paths

    def discard(self, paths: list[Path]) -> None:
        resolved = {path.resolve() for path in paths}
        with self._lock:
            for queued in list(self._paths):
                if queued.resolve() in resolved:
                    self._paths.pop(queued, None)
            if self._paths:
                self.changed.set()
            else:
                self.changed.clear()


class WorkflowScheduler:
    """Drive immediate, cron and watch triggers through one executor."""

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
        self._active_runtime: PipelineRuntime | None = None

    def request_cancel(self) -> None:
        self._cancel_event.set()
        if self._active_runtime is not None:
            self._active_runtime.request_cancel()

    def terminate_session(self, session_id: str) -> bool:
        runtime = self._active_runtime
        if runtime is None:
            return False
        session = runtime.sessions.get(session_id)
        if session is None:
            return False
        session.terminate()
        return True

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
        definition = _resolve_workflow_definition(workflow, workflows_dir=workflows_dir)
        plan = resolve_input(files=files, recurse=recurse, lines_text=lines_text, lines_file=lines_file)

        if self._cron:
            self._validate_cron()

        if plan.kind == "path" and not direct_mode:
            validate_output_separation(list(plan.files), output_dir, strict=self._watch)

        if self._watch and plan.kind != "path":
            LOGGER.warning("watch mode requires path input; running once and exiting watch")
            return self._run_once(
                definition,
                plan,
                output_dir,
                direct_mode=direct_mode,
                move_mode=False,
                enable_log=enable_log,
                shared=shared,
                progress_callback=progress_callback,
                event_listener=event_listener,
            )

        if self._watch:
            validate_output_separation(list(plan.files), output_dir, strict=True)
            return self._run_watch(
                definition,
                plan,
                output_dir,
                move_mode=direct_mode,
                enable_log=enable_log,
                shared=shared,
                progress_callback=progress_callback,
                event_listener=event_listener,
            )

        if self._cron:
            return self._run_cron(
                definition,
                plan,
                output_dir,
                direct_mode=direct_mode,
                enable_log=enable_log,
                shared=shared,
                progress_callback=progress_callback,
                event_listener=event_listener,
            )

        return self._run_once(
            definition,
            plan,
            output_dir,
            direct_mode=direct_mode,
            move_mode=False,
            enable_log=enable_log,
            shared=shared,
            progress_callback=progress_callback,
            event_listener=event_listener,
        )

    def _run_once(
        self,
        definition: WorkflowDefinition,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        direct_mode: bool,
        move_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        progress_callback: Any,
        event_listener: Any,
    ) -> dict[str, Any]:
        runtime = PipelineRuntime(
            enable_log=enable_log,
            output_dir=output_dir,
            workflow_slug=definition.meta.slug,
        )
        self._active_runtime = runtime
        executor = PipelineExecutor(
            self._module_manager,
            runtime=runtime,
            progress_callback=progress_callback,
            cancel_requested=self._cancel_event.is_set,
            event_listener=event_listener,
            concurrency=self._concurrency,
        )
        try:
            return executor.execute(
                definition,
                output_dir=output_dir,
                input_plan=plan,
                direct_mode=direct_mode,
                move_mode=move_mode,
                shared=shared,
            )
        finally:
            runtime.close()
            if self._active_runtime is runtime:
                self._active_runtime = None

    def _run_cron(
        self,
        definition: WorkflowDefinition,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        direct_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        progress_callback: Any,
        event_listener: Any,
    ) -> dict[str, Any]:
        schedule = croniter.croniter(self._cron or "* * * * *", datetime.now())
        last_result: dict[str, Any] | None = None
        while not self._cancel_event.is_set():
            next_time = schedule.get_next(datetime)
            delay = max(0.0, (next_time - datetime.now()).total_seconds())
            if self._cancel_event.wait(delay):
                break
            last_result = self._run_once(
                definition,
                plan,
                output_dir,
                direct_mode=direct_mode,
                move_mode=False,
                enable_log=enable_log,
                shared=shared,
                progress_callback=progress_callback,
                event_listener=event_listener,
            )
        return last_result or self._cancelled_summary(definition, output_dir)

    def _run_watch(
        self,
        definition: WorkflowDefinition,
        plan: InputPlan,
        output_dir: str | Path,
        *,
        move_mode: bool,
        enable_log: bool,
        shared: Mapping[str, Any] | None,
        progress_callback: Any,
        event_listener: Any,
    ) -> dict[str, Any]:
        watch_dirs = _collect_watch_dirs(list(plan.files))
        handler = _ChangeHandler()
        observer = Observer()
        for directory in watch_dirs:
            observer.schedule(handler, str(directory), recursive=plan.recurse)
        observer.start()

        schedule = croniter.croniter(self._cron, datetime.now()) if self._cron else None
        next_cron = schedule.get_next(datetime) if schedule is not None else None
        last_result: dict[str, Any] | None = None
        try:
            while not self._cancel_event.is_set():
                if next_cron is not None and datetime.now() >= next_cron:
                    last_result = self._run_once(
                        definition,
                        plan,
                        output_dir,
                        direct_mode=False,
                        move_mode=False,
                        enable_log=enable_log,
                        shared=shared,
                        progress_callback=progress_callback,
                        event_listener=event_listener,
                    )
                    next_cron = schedule.get_next(datetime) if schedule is not None else None
                    continue

                wait_for = 0.2
                if next_cron is not None:
                    wait_for = max(0.05, min(wait_for, (next_cron - datetime.now()).total_seconds()))
                if not handler.changed.wait(timeout=wait_for):
                    continue
                if self._cancel_event.wait(_DEBOUNCE_SECONDS):
                    break

                changed = _filter_watch_paths(handler.drain(), list(plan.files), recurse=plan.recurse)
                stable = _wait_for_stable_files(changed, self._cancel_event)
                if not stable:
                    continue
                handler.discard(stable)
                batch_plan = InputPlan(kind="path", recurse=False, files=tuple(stable))
                last_result = self._run_once(
                    definition,
                    batch_plan,
                    output_dir,
                    direct_mode=False,
                    move_mode=move_mode,
                    enable_log=enable_log,
                    shared=shared,
                    progress_callback=progress_callback,
                    event_listener=event_listener,
                )
        finally:
            observer.stop()
            observer.join(timeout=5)
        return last_result or self._cancelled_summary(definition, output_dir)

    def _validate_cron(self) -> None:
        try:
            croniter.croniter(self._cron or "* * * * *", datetime.now())
        except (ValueError, KeyError) as exc:
            raise PipelineExecutionError(f"invalid cron expression: {self._cron} — {exc}") from exc

    @staticmethod
    def _cancelled_summary(definition: WorkflowDefinition, output_dir: str | Path) -> dict[str, Any]:
        return {
            "success": False,
            "cancelled": True,
            "processed_units": 0,
            "successful_units": 0,
            "failed_units": 0,
            "errors": [],
            "results": [],
            "workflow": definition.meta.name,
            "scope": definition.scope,
            "output_dir": str(Path(output_dir).resolve()),
        }


def _collect_watch_dirs(paths: list[Path]) -> set[Path]:
    directories: set[Path] = set()
    for path in paths:
        directories.add(path.resolve() if path.is_dir() else path.resolve().parent)
    return directories


def _filter_watch_paths(paths: list[Path], roots: list[Path], *, recurse: bool) -> list[Path]:
    accepted: OrderedDict[Path, None] = OrderedDict()
    resolved_roots = [root.resolve() for root in roots]
    for raw_path in paths:
        path = raw_path.resolve()
        for root in resolved_roots:
            if not root.is_dir():
                allowed = path == root
            else:
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    allowed = False
                else:
                    allowed = recurse or len(relative.parts) == 1
            if allowed:
                if path.is_dir() and recurse:
                    for child in sorted(path.rglob("*")):
                        if child.is_file():
                            accepted[child.resolve()] = None
                elif path.is_file():
                    accepted[path] = None
                break
    return list(accepted)


def _wait_for_stable_files(paths: list[Path], cancel_event: threading.Event) -> list[Path]:
    pending = OrderedDict((path, None) for path in paths)
    previous: dict[Path, tuple[int, int]] = {}
    unchanged: dict[Path, int] = {}
    stable: list[Path] = []
    deadline = monotonic() + _STABILITY_TIMEOUT_SECONDS

    while pending and monotonic() < deadline and not cancel_event.is_set():
        for path in list(pending):
            try:
                stat = path.stat()
            except OSError:
                pending.pop(path, None)
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            if previous.get(path) == signature:
                unchanged[path] = unchanged.get(path, 0) + 1
            else:
                previous[path] = signature
                unchanged[path] = 0
            if unchanged[path] >= 2:
                stable.append(path)
                pending.pop(path, None)
        if pending:
            cancel_event.wait(_STABILITY_POLL_SECONDS)

    for path in pending:
        LOGGER.warning("watch path did not become stable before timeout: %s", path)
    return stable
