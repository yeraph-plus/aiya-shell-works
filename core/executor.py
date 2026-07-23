"""Workflow executor: input-plan x scope x recurse driven unit dispatch.

Execution uses per-unit event-bus isolation and optional worker threads. Scope
values control how inputs are batched into tasks:

* ``scope=1`` (per-unit) — every listed file / folder / line is its own
  ctx.  Executor calls ``runtime.replace_bus()`` before each unit so
  each one's events never bleed into the next.
* ``scope=0`` (shared) — all inputs share one context. Read/write workflows
  import a merged output tree; all-read workflows expose a reference manifest.
* ``scope>1`` (batched) — inputs are sliced into fixed-size batches.  Each
  batch runs with its own fresh bus, matching ``scope=1`` isolation.  Path
  read/write path batches get isolated worktrees; all-read path batches use
  reference manifests; line batches are exposed via
  ``ctx.shared["input_lines"]``.

Cancellation is checked at step boundaries.  Module return-value contract
is ``PipelineContext | None``; a returned context must retain the current
unit workspace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from .config_schema import (
    ConfigSchemaValidationError,
    ConfigValidationError,
    normalize_config_params,
)
from .context import PipelineContext
from .exceptions import (
    FileHandlingError,
    ModuleExecutionError,
    PipelineCancelledError,
    PipelineExecutionError,
)
from .files import ExecutionWorkspace, PreparedWorkspaceUnit, UnitWorkspace, units_from_plan, validate_output_separation
from .input import InputPlan, resolve_input
from .module_manager import ModuleDefinition, ModuleManager, current_platform
from .planning import ExecutionUnit, PathInput
from .runtime import PipelineRuntime
from .workflow_loader import WorkflowDefinition, resolve_workflow_definition

EventCallback = Callable[[Any], None]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


class UnitResult(TypedDict, total=False):
    success: bool
    unit: str | None
    working_path: str
    original_input: str | None
    error: str
    type: str


class ExecutionSummary(TypedDict):
    success: bool
    cancelled: bool
    processed_units: int
    successful_units: int
    failed_units: int
    errors: list[dict[str, Any]]
    results: list[dict[str, Any]]
    workflow: str
    scope: int
    output_dir: str


@dataclass(frozen=True, slots=True)
class PreparedStep:
    index: int
    name: str
    module_slug: str
    module_definition: ModuleDefinition
    params: dict[str, Any]
    supported: bool = True


# ---------------------------------------------------------------------------
# Module-level step/unit helpers (shared by PipelineExecutor and scheduler)
# ---------------------------------------------------------------------------


def prepare_steps(
    workflow: WorkflowDefinition,
    module_manager: ModuleManager,
) -> list[PreparedStep]:
    """Validate step params and build ``PreparedStep`` list."""
    modules = module_manager.get_modules()
    prepared: list[PreparedStep] = []
    for idx, step in enumerate(workflow.steps, start=1):
        definition = modules.get(step.module)
        if definition is None:
            raise PipelineExecutionError(f"module not found: {step.module}")
        if not definition.supports_platform():
            prepared.append(
                PreparedStep(
                    index=idx,
                    name=step.name or step.module,
                    module_slug=step.module,
                    module_definition=definition,
                    params={},
                    supported=False,
                )
            )
            continue
        try:
            params = normalize_config_params(definition.config_schema, step.params)
        except ConfigValidationError as exc:
            raise PipelineExecutionError(
                f"step {idx} ({step.module}) param validation failed: {'; '.join(exc.errors)}"
            ) from exc
        except ConfigSchemaValidationError as exc:
            raise PipelineExecutionError(
                f"module {step.module} CONFIG_SCHEMA invalid: {'; '.join(exc.errors)}"
            ) from exc
        prepared.append(
            PreparedStep(
                index=idx,
                name=step.name or step.module,
                module_slug=step.module,
                module_definition=definition,
                params=params,
            )
        )
    return prepared


def build_units(
    workflow: WorkflowDefinition,
    plan: InputPlan,
) -> list[ExecutionUnit]:
    """Build unit dicts from a plan."""
    if workflow.scope == 0:
        if plan.kind == "none":
            return [ExecutionUnit(kind="none")]
        if plan.kind == "line":
            return [ExecutionUnit(kind="line", layout="shared", lines=plan.lines)]
        paths = tuple(PathInput(path=path) for path in plan.files)
        return [ExecutionUnit(kind="path", layout="shared", paths=paths)]
    if workflow.scope == 1:
        return _single_units(plan)
    if plan.kind == "line":
        return _build_line_batches(list(plan.lines), workflow.scope)
    if plan.kind == "none":
        return [ExecutionUnit(kind="none")]
    return _build_path_batches(_single_units(plan), workflow.scope)


def prepare_context(
    plan: InputPlan,
    workspace: ExecutionWorkspace,
    unit: ExecutionUnit,
    *,
    shared: Mapping[str, Any] | None,
    direct_mode: bool = False,
    move_mode: bool = False,
    reference_mode: bool = False,
    unit_workspace: UnitWorkspace | None = None,
    unit_index: int = 1,
) -> PipelineContext:
    """Build a ``PipelineContext`` for a unit."""
    prepared = workspace.prepare_unit(
        unit_index,
        unit,
        direct_mode=direct_mode,
        move_mode=move_mode,
        reference_mode=reference_mode,
        shared=shared,
        unit_workspace=unit_workspace,
    )
    return _context_from_prepared(prepared, shared)


def _context_from_prepared(
    prepared: PreparedWorkspaceUnit,
    shared: Mapping[str, Any] | None,
) -> PipelineContext:
    payload = dict(shared or {})
    if prepared.input_lines:
        payload["input_lines"] = list(prepared.input_lines)
        if len(prepared.input_lines) == 1:
            payload["input_line"] = prepared.input_lines[0]
    return PipelineContext(
        workspace=prepared.workspace,
        original_input=prepared.original_input,
        shared=payload,
        source_root=prepared.source_root,
    )


def resolve_step_result(
    *,
    step_name: str,
    result: Any,
    fallback: PipelineContext,
) -> PipelineContext:
    """Resolve a module's return value to a ``PipelineContext``."""
    if result is None:
        return fallback
    if isinstance(result, PipelineContext):
        if result.workspace is not fallback.workspace:
            raise PipelineExecutionError(f"step {step_name} returned a context from another workspace")
        return result
    raise PipelineExecutionError(f"step {step_name} returned invalid value: must be PipelineContext or None")


class PipelineExecutor:
    """Execute a workflow definition; one instance, one execution."""

    def __init__(
        self,
        module_manager: ModuleManager,
        *,
        runtime: PipelineRuntime | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
        event_listener: EventCallback | None = None,
        concurrency: int = 1,
    ) -> None:
        self.module_manager = module_manager
        self.runtime = runtime or PipelineRuntime()
        self.progress_callback = progress_callback
        self.cancel_requested = cancel_requested
        self.event_listener = event_listener
        self.concurrency = max(1, concurrency)

    def execute(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        *,
        output_dir: str | Path,
        input_plan: InputPlan | None = None,
        direct_mode: bool = False,
        shared: Mapping[str, Any] | None = None,
        files: list[str | Path] | None = None,
        recurse: bool = False,
        lines_text: str | None = None,
        lines_file: str | Path | None = None,
        move_mode: bool = False,
    ) -> ExecutionSummary:
        """Run a workflow.  Returns a summary dict."""

        active_runtime = self.runtime
        if self.event_listener is not None:
            active_runtime.subscribe(self.event_listener)

        definition = resolve_workflow_definition(workflow)
        plan = input_plan or resolve_input(
            files=files,
            recurse=recurse,
            lines_text=lines_text,
            lines_file=lines_file,
        )

        if plan.kind == "path" and (move_mode or not direct_mode):
            validate_output_separation(list(plan.files), output_dir, strict=True)

        steps = self._prepare_steps(definition)
        reference_mode = (
            plan.kind == "path"
            and not direct_mode
            and not move_mode
            and not any(step.supported and step.module_definition.access == "read_write" for step in steps)
        )
        units = build_units(definition, plan)
        if direct_mode and plan.kind == "path" and definition.scope != 1:
            raise FileHandlingError("direct_mode 仅支持 scope=1 的路径单元")
        total = len(units)
        errors: list[dict[str, Any]] = []
        successful = 0
        results: list[dict[str, Any]] = []
        cancelled = False

        active_runtime.log(
            "executor",
            "message",
            f"start workflow: {definition.meta.name} "
            f"(scope={definition.scope}, recurse={definition.recurse}, units={total}, "
            f"direct={direct_mode}, move={move_mode}, reference={reference_mode}, "
            f"concurrency={self.concurrency})",
        )
        self._report_progress(0, total, None, "starting")

        for warning in self.module_manager.warnings:
            active_runtime.log("executor", "warning", f"module scan warning: {warning}")

        with ExecutionWorkspace(output_dir) as workspace:
            if self.concurrency > 1 and total > 1 and definition.scope != 0:
                successful, cancelled = self._execute_parallel(
                    plan=plan,
                    steps=steps,
                    units=units,
                    workspace=workspace,
                    direct_mode=direct_mode,
                    move_mode=move_mode,
                    reference_mode=reference_mode,
                    shared=shared,
                    results=results,
                    errors=errors,
                )
            else:
                successful, cancelled = self._execute_sequential(
                    definition=definition,
                    plan=plan,
                    steps=steps,
                    units=units,
                    workspace=workspace,
                    direct_mode=direct_mode,
                    move_mode=move_mode,
                    reference_mode=reference_mode,
                    shared=shared,
                    results=results,
                    errors=errors,
                )

        completed = successful + len(errors)
        success = not errors and not cancelled
        summary: ExecutionSummary = {
            "success": success,
            "cancelled": cancelled,
            "processed_units": total,
            "successful_units": successful,
            "failed_units": len(errors),
            "errors": errors,
            "results": results,
            "workflow": definition.meta.name,
            "scope": definition.scope,
            "output_dir": str(workspace.output_dir),
        }
        active_runtime.log(
            "executor",
            "success" if success else "message",
            f"workflow done: success={success}, cancelled={cancelled}, successful={successful}, failed={len(errors)}",
        )
        self._report_progress(completed, total, None, "cancelled" if cancelled else "done")
        return summary

    def _execute_sequential(
        self,
        *,
        definition: WorkflowDefinition,
        plan: InputPlan,
        steps: list[PreparedStep],
        units: list[ExecutionUnit],
        workspace: ExecutionWorkspace,
        direct_mode: bool,
        move_mode: bool,
        reference_mode: bool,
        shared: Mapping[str, Any] | None,
        results: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> tuple[int, bool]:
        successful = 0
        total = len(units)
        for idx, unit in enumerate(units, start=1):
            unit_workspace = workspace.create_unit(idx)
            try:
                self._raise_if_cancelled(self.runtime)
                if definition.scope != 0:
                    self.runtime.replace_bus()
                final_ctx = self._execute_unit(
                    plan=plan,
                    unit=unit,
                    unit_workspace=unit_workspace,
                    workspace=workspace,
                    direct_mode=direct_mode,
                    move_mode=move_mode,
                    reference_mode=reference_mode,
                    shared=shared,
                    runtime=self.runtime,
                    unit_index=idx,
                    total_units=total,
                    steps=steps,
                )
                success_result = self._success_result(unit, final_ctx)
                workspace.publish(unit_workspace)
                successful += 1
                results.append(success_result)
                self._report_progress(idx, total, _unit_display(unit), "completed")
            except PipelineCancelledError:
                if not move_mode and not direct_mode:
                    workspace.discard(unit_workspace)
                self.runtime.log(
                    "executor", "warning", f"cancelled: unit {idx}/{total} ({_unit_display(unit) or '<none>'})"
                )
                return successful, True
            except Exception as exc:
                if not move_mode and not direct_mode:
                    workspace.discard(unit_workspace)
                self._record_failure(unit, idx, total, exc, results, errors, self.runtime)
        return successful, False

    def _execute_parallel(
        self,
        *,
        plan: InputPlan,
        steps: list[PreparedStep],
        units: list[ExecutionUnit],
        workspace: ExecutionWorkspace,
        direct_mode: bool,
        move_mode: bool,
        reference_mode: bool,
        shared: Mapping[str, Any] | None,
        results: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> tuple[int, bool]:
        futures: dict[Future[PipelineContext], tuple[int, ExecutionUnit, UnitWorkspace, PipelineRuntime]] = {}
        outcomes: dict[int, tuple[dict[str, Any] | None, Exception | None, ExecutionUnit, UnitWorkspace]] = {}
        total = len(units)
        indexed_units = iter(enumerate(units, start=1))

        def submit_next(pool: ThreadPoolExecutor) -> bool:
            if self._is_cancelled():
                return False
            try:
                idx, unit = next(indexed_units)
            except StopIteration:
                return False
            unit_workspace = workspace.create_unit(idx)
            runtime = self.runtime.fork()
            future = pool.submit(
                self._execute_unit,
                plan=plan,
                unit=unit,
                unit_workspace=unit_workspace,
                workspace=workspace,
                direct_mode=direct_mode,
                move_mode=move_mode,
                reference_mode=reference_mode,
                shared=shared,
                runtime=runtime,
                unit_index=idx,
                total_units=total,
                steps=steps,
            )
            futures[future] = (idx, unit, unit_workspace, runtime)
            return True

        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="pipeline") as pool:
            for _ in range(min(self.concurrency, total)):
                if not submit_next(pool):
                    break

            completed = 0
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    idx, unit, unit_workspace, runtime = futures.pop(future)
                    completed += 1
                    try:
                        final_ctx = future.result()
                        success_result = self._success_result(unit, final_ctx)
                        workspace.publish(unit_workspace)
                        outcomes[idx] = (success_result, None, unit, unit_workspace)
                        self._report_progress(completed, total, _unit_display(unit), "completed")
                    except Exception as exc:
                        outcomes[idx] = (None, exc, unit, unit_workspace)
                        status = "cancelled" if isinstance(exc, PipelineCancelledError) else "failed"
                        self._report_progress(completed, total, _unit_display(unit), status)
                    finally:
                        runtime.close()
                    submit_next(pool)

        successful = 0
        cancelled = self._is_cancelled()
        for idx in sorted(outcomes):
            outcome_result, error, unit, unit_workspace = outcomes[idx]
            if error is None and outcome_result is not None:
                results.append(outcome_result)
                successful += 1
            elif isinstance(error, PipelineCancelledError):
                cancelled = True
                if not move_mode and not direct_mode:
                    workspace.discard(unit_workspace)
            elif error is not None:
                if not move_mode and not direct_mode:
                    workspace.discard(unit_workspace)
                self._record_failure(unit, idx, total, error, results, errors, self.runtime, report_progress=False)
        return successful, cancelled

    def _execute_unit(
        self,
        *,
        plan: InputPlan,
        unit: ExecutionUnit,
        unit_workspace: UnitWorkspace,
        workspace: ExecutionWorkspace,
        direct_mode: bool,
        move_mode: bool,
        reference_mode: bool,
        shared: Mapping[str, Any] | None,
        runtime: PipelineRuntime,
        unit_index: int,
        total_units: int,
        steps: list[PreparedStep],
    ) -> PipelineContext:
        self._raise_if_cancelled(runtime)
        ctx = prepare_context(
            plan,
            workspace,
            unit,
            shared=shared,
            direct_mode=direct_mode,
            move_mode=move_mode,
            reference_mode=reference_mode,
            unit_workspace=unit_workspace,
            unit_index=unit_index,
        )
        return self._run_unit(
            ctx=ctx,
            runtime=runtime,
            unit_index=unit_index,
            total_units=total_units,
            steps=steps,
        )

    @staticmethod
    def _success_result(unit: ExecutionUnit, ctx: PipelineContext) -> dict[str, Any]:
        return {
            "success": True,
            "unit": unit.display(),
            "working_path": str(ctx.current.path),
            "original_input": _display_name(ctx.original_input),
        }

    def _record_failure(
        self,
        unit: ExecutionUnit,
        index: int,
        total: int,
        exc: Exception,
        results: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        runtime: PipelineRuntime,
        *,
        report_progress: bool = True,
    ) -> None:
        error = {"unit": _unit_display(unit), "error": str(exc), "type": type(exc).__name__}
        errors.append(error)
        results.append({"success": False, **error})
        runtime.log(
            "executor",
            "error",
            f"unit failed [{index}/{total}]: {error['unit'] or '<none>'} -> {error['error']}",
        )
        if report_progress:
            self._report_progress(index, total, _unit_display(unit), "failed")

    def _prepare_steps(self, workflow: WorkflowDefinition) -> list[PreparedStep]:
        return prepare_steps(workflow, self.module_manager)

    # ------------------------------------------------------------------
    # Unit execution
    # ------------------------------------------------------------------

    def _run_unit(
        self,
        *,
        ctx: PipelineContext,
        runtime: PipelineRuntime,
        unit_index: int,
        total_units: int,
        steps: list[PreparedStep],
    ) -> PipelineContext:
        self._raise_if_cancelled(runtime)
        unit_name = _display_name(ctx.original_input) or "<none>"
        current = ctx

        runtime.log("executor", "message", f"start unit [{unit_index}/{total_units}]: {unit_name}")

        for step in steps:
            self._raise_if_cancelled(runtime)
            if not step.supported:
                supported = step.module_definition.platforms or ()
                runtime.log(
                    step.module_slug,
                    "warning",
                    f"skip step {step.index}/{len(steps)}: platform is not supported",
                    {
                        "status": "skipped",
                        "platform": current_platform(),
                        "supported_platforms": list(supported),
                    },
                )
                continue
            runtime.log(
                step.module_slug,
                "message",
                f"start step [{unit_index}/{total_units}] {step.index}/{len(steps)}: {step.name}",
            )

            try:
                with current.workspace.module_access(step.module_definition.access):
                    result = step.module_definition.run(current, dict(step.params), runtime)
                current = self._resolve_step_result(step_name=step.name, result=result, fallback=current)
                current.refresh()
            except PipelineCancelledError:
                raise
            except ModuleExecutionError:
                raise
            except Exception as exc:
                raise ModuleExecutionError(
                    f"module {step.module_slug} failed at step {step.index} ({step.name}): {exc}"
                ) from exc

            runtime.log(
                step.module_slug,
                "success",
                f"done step [{unit_index}/{total_units}] {step.index}/{len(steps)}: {step.name}",
            )

        runtime.log("executor", "success", f"unit ok [{unit_index}/{total_units}]: {unit_name}")
        return current

    def _resolve_step_result(self, *, step_name: str, result: Any, fallback: PipelineContext) -> PipelineContext:
        return resolve_step_result(step_name=step_name, result=result, fallback=fallback)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _report_progress(self, current: int, total: int, unit: str | None, status: str) -> None:
        if self.progress_callback is None:
            return
        percent = 100 if total == 0 else int(current * 100 / total)
        self.progress_callback(
            {
                "current": current,
                "total": total,
                "percent": percent,
                "unit": unit,
                "status": status,
            }
        )

    def _raise_if_cancelled(self, runtime: PipelineRuntime) -> None:
        if self.cancel_requested is not None and self.cancel_requested():
            raise PipelineCancelledError("execution cancelled.")
        if runtime.is_cancelled():
            raise PipelineCancelledError("execution cancelled.")
        if self.runtime.is_cancelled():
            raise PipelineCancelledError("execution cancelled.")

    def _is_cancelled(self) -> bool:
        return bool((self.cancel_requested and self.cancel_requested()) or self.runtime.is_cancelled())


def execute_workflow(
    workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
    *,
    output_dir: str | Path,
    files: list[str | Path] | None = None,
    recurse: bool = False,
    lines_text: str | None = None,
    lines_file: str | Path | None = None,
    direct_mode: bool = False,
    modules_dir: str | Path = "modules",
    workflows_dir: str | Path | None = None,
    enable_log: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: CancelCallback | None = None,
    event_listener: EventCallback | None = None,
    shared: Mapping[str, Any] | None = None,
    concurrency: int = 1,
    move_mode: bool = False,
) -> ExecutionSummary:
    """Standalone runner for CLI callers and tests.

    Builds a fresh ``ModuleManager`` and ``PipelineRuntime`` each call.
    No GUI imports; safe under multiprocessing.
    """

    definition = resolve_workflow_definition(workflow, workflows_dir=workflows_dir)
    plan = resolve_input(
        files=files,
        recurse=recurse,
        lines_text=lines_text,
        lines_file=lines_file,
    )
    if plan.kind == "path" and (move_mode or not direct_mode):
        validate_output_separation(list(plan.files), output_dir, strict=True)
    runtime = PipelineRuntime(
        enable_log=enable_log,
        output_dir=output_dir,
        workflow_slug=definition.meta.slug,
    )
    module_manager = ModuleManager(modules_dir)
    executor = PipelineExecutor(
        module_manager,
        runtime=runtime,
        progress_callback=progress_callback,
        cancel_requested=cancel_requested,
        event_listener=event_listener,
        concurrency=concurrency,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _display_name(path: Path | str | None) -> str | None:
    return None if path is None else str(path)


def _unit_display(unit: ExecutionUnit) -> str | None:
    return unit.display()


def _single_units(plan: InputPlan) -> list[ExecutionUnit]:
    if plan.kind == "none":
        return [ExecutionUnit(kind="none")]
    if plan.kind == "line":
        return [ExecutionUnit(kind="line", lines=(line,)) for line in plan.lines]
    return [
        ExecutionUnit(
            kind="path",
            paths=(PathInput(path=Path(item["path"]), source_root=item.get("source_root")),),
        )
        for item in units_from_plan(plan)
    ]


def _build_line_batches(lines: list[str], batch_size: int) -> list[ExecutionUnit]:
    return [
        ExecutionUnit(kind="line", layout="batch", lines=tuple(lines[index : index + batch_size]))
        for index in range(0, len(lines), batch_size)
    ]


def _build_path_batches(units: list[ExecutionUnit], batch_size: int) -> list[ExecutionUnit]:
    return [
        ExecutionUnit(
            kind="path",
            layout="batch",
            paths=tuple(path for unit in units[index : index + batch_size] for path in unit.paths),
        )
        for index in range(0, len(units), batch_size)
    ]
