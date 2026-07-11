"""Workflow executor: input-plan x scope x recurse driven unit dispatch.

Single-threaded execution with per-unit event-bus isolation.  Scope values
control how inputs are batched into tasks:

* ``scope=1`` (per-unit) — every listed file / folder / line is its own
  ctx.  Executor calls ``runtime.replace_bus()`` before each unit so
  each one's events never bleed into the next.
* ``scope=0`` (shared)  — all inputs are merged into a single merged
  working tree (see ``files.py``) and the workflow runs exactly once
  over the merged output folder.  The module rglobs the working tree
  itself.
* ``scope>1`` (batched) — inputs are sliced into fixed-size batches.  Each
  batch runs with its own fresh bus, matching ``scope=1`` isolation.  Path
  batches get isolated worktrees; line batches are exposed via
  ``ctx.shared["input_lines"]``.

Cancellation is checked at step boundaries.  Module return-value contract
is ``PipelineContext | None | dict[str, ctx]``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_schema import (
    ConfigSchemaValidationError,
    ConfigValidationError,
    normalize_config_params,
)
from .context import PipelineContext
from .exceptions import (
    PipelineCancelledError,
    PipelineExecutionError,
    WorkflowValidationError,
)
from .files import WorkingCopier, units_from_plan
from .input import InputPlan, resolve_input
from .module_manager import ModuleDefinition, ModuleManager
from .runtime import PipelineRuntime
from .workflow_loader import WorkflowDefinition, WorkflowLoader

EventCallback = Callable[[Any], None]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class PreparedStep:
    index: int
    name: str
    module_slug: str
    module_definition: ModuleDefinition
    params: dict[str, Any]


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
) -> list[dict[str, Any]]:
    """Build unit dicts from a plan."""
    if workflow.scope == 0:
        if plan.kind == "none":
            return [{"path": None, "source_root": None}]
        if plan.kind == "line":
            return [{"lines": list(plan.lines)}]
        return [{"__shared_paths__": list(plan.files), "recurse": plan.recurse, "source_root": None}]
    if workflow.scope == 1:
        return units_from_plan(plan)
    if plan.kind == "line":
        return _build_line_batches(list(plan.lines), workflow.scope)
    if plan.kind == "none":
        return [{"path": None, "source_root": None}]
    return _build_path_batches(units_from_plan(plan), workflow.scope)


def prepare_context(
    workflow: WorkflowDefinition,
    plan: InputPlan,
    copier: WorkingCopier,
    unit: dict[str, Any],
    *,
    shared: Mapping[str, Any] | None,
) -> PipelineContext:
    """Build a ``PipelineContext`` for a unit."""
    if "__shared_paths__" in unit:
        return copier.prepare_shared_path_unit(
            list(unit["__shared_paths__"]),
            recurse=unit.get("recurse", plan.recurse),
            shared=shared,
        )
    if "__batched_paths__" in unit:
        return copier.prepare_batched_path_unit(
            list(unit["__batched_paths__"]),
            batch_index=int(unit.get("batch_index", 1)),
            shared=shared,
        )
    if plan.kind == "line" or unit.get("line") is not None or unit.get("lines") is not None:
        return copier.prepare_line(unit, shared=shared)
    if plan.kind == "none" or unit.get("path") is None:
        return copier.prepare_none(shared=shared)
    return copier.prepare_path_unit(unit, shared=shared)


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
        return result
    if isinstance(result, Mapping) and isinstance(result.get("context"), PipelineContext):
        return result["context"]
    raise PipelineExecutionError(
        f"step {step_name} returned invalid value: must be PipelineContext, None, or dict with context key"
    )


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
    ) -> None:
        self.module_manager = module_manager
        self.runtime = runtime or PipelineRuntime()
        self.progress_callback = progress_callback
        self.cancel_requested = cancel_requested
        self.event_listener = event_listener

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
    ) -> dict[str, Any]:
        """Run a workflow.  Returns a summary dict."""

        active_runtime = self.runtime
        if self.event_listener is not None:
            active_runtime.subscribe(self.event_listener)

        definition = _resolve_workflow_definition(workflow)
        plan = input_plan or resolve_input(
            files=files,
            recurse=recurse,
            lines_text=lines_text,
            lines_file=lines_file,
        )

        steps = self._prepare_steps(definition)
        copier = WorkingCopier(output_dir, direct_mode=direct_mode)

        units = self._build_units(definition, plan, copier, shared)
        total = len(units)
        errors: list[dict[str, Any]] = []
        successful = 0
        results: list[dict[str, Any]] = []
        cancelled = False

        active_runtime.log(
            "executor",
            "message",
            f"start workflow: {definition.meta.name} "
            f"(scope={definition.scope}, "
            f"recurse={definition.recurse}, units={total}, direct={direct_mode})",
        )
        self._report_progress(0, total, None, "starting")

        for warning in self.module_manager.warnings:
            active_runtime.log("executor", "warning", f"module scan warning: {warning}")

        for idx, unit in enumerate(units, start=1):
            try:
                self._raise_if_cancelled(active_runtime)
            except PipelineCancelledError as exc:
                cancelled = True
                active_runtime.log("executor", "warning", str(exc))
                break
            try:
                if definition.scope != 0:
                    active_runtime.replace_bus()

                ctx = self._prepare_context(definition, plan, copier, unit, shared=shared)
                final_ctx = self._run_unit(
                    ctx=ctx,
                    runtime=active_runtime,
                    unit_index=idx,
                    total_units=total,
                    steps=steps,
                )
                successful += 1
                results.append(
                    {
                        "success": True,
                        "unit": _unit_display(unit),
                        "working_path": str(final_ctx.working_path),
                        "original_input": _display_name(final_ctx.original_input),
                    }
                )
                self._report_progress(idx, total, _unit_display(unit), "completed")
            except PipelineCancelledError:
                cancelled = True
                active_runtime.log(
                    "executor", "warning", f"cancelled: unit {idx}/{total} ({_unit_display(unit) or '<none>'})"
                )
                break
            except Exception as exc:
                err = {
                    "unit": _unit_display(unit),
                    "error": str(exc),
                    "type": type(exc).__name__,
                }
                errors.append(err)
                results.append(
                    {
                        "success": False,
                        "unit": err["unit"],
                        "error": err["error"],
                        "type": err["type"],
                    }
                )
                active_runtime.log(
                    "executor",
                    "error",
                    f"unit failed [{idx}/{total}]: {err['unit'] or '<none>'} -> {err['error']}",
                )
                self._report_progress(idx, total, _unit_display(unit), "failed")

        completed = successful + len(errors)
        success = not errors and not cancelled
        summary = {
            "success": success,
            "cancelled": cancelled,
            "processed_units": total,
            "successful_units": successful,
            "failed_units": len(errors),
            "errors": errors,
            "results": results,
            "workflow": definition.meta.name,
            "scope": definition.scope,
            "output_dir": str(copier.output_dir),
        }
        active_runtime.log(
            "executor",
            "success" if success else "message",
            f"workflow done: success={success}, cancelled={cancelled}, successful={successful}, failed={len(errors)}",
        )
        self._report_progress(completed, total, None, "cancelled" if cancelled else "done")
        return summary

    def _prepare_steps(self, workflow: WorkflowDefinition) -> list[PreparedStep]:
        return prepare_steps(workflow, self.module_manager)

    # ------------------------------------------------------------------
    # Unit construction
    # ------------------------------------------------------------------

    def _build_units(
        self,
        workflow: WorkflowDefinition,
        plan: InputPlan,
        copier: WorkingCopier,
        shared: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        return build_units(workflow, plan)

    def _prepare_context(
        self,
        workflow: WorkflowDefinition,
        plan: InputPlan,
        copier: WorkingCopier,
        unit: dict[str, Any],
        *,
        shared: Mapping[str, Any] | None,
    ) -> PipelineContext:
        return prepare_context(workflow, plan, copier, unit, shared=shared)

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
            runtime.log(
                step.module_slug,
                "message",
                f"start step [{unit_index}/{total_units}] {step.index}/{len(steps)}: {step.name}",
            )

            result = step.module_definition.run(current, dict(step.params), runtime)
            current = self._resolve_step_result(step_name=step.name, result=result, fallback=current)

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
) -> dict[str, Any]:
    """Standalone runner for CLI callers and tests.

    Builds a fresh ``ModuleManager`` and ``PipelineRuntime`` each call.
    No GUI imports; safe under multiprocessing.
    """

    definition = _resolve_workflow_definition(workflow, workflows_dir=workflows_dir)
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
    )
    try:
        return executor.execute(
            definition,
            output_dir=output_dir,
            files=files,
            recurse=recurse,
            lines_text=lines_text,
            lines_file=lines_file,
            direct_mode=direct_mode,
            shared=shared,
        )
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_workflow_definition(
    workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
    *,
    workflows_dir: str | Path | None = None,
) -> WorkflowDefinition:
    if isinstance(workflow, WorkflowDefinition):
        return workflow
    loader_root = Path(workflows_dir).resolve() if workflows_dir is not None else Path.cwd() / "workflows"
    loader = WorkflowLoader(loader_root)
    if isinstance(workflow, Mapping):
        result = loader.validate_document(workflow)
        if not result.is_valid or result.workflow is None:
            raise WorkflowValidationError(list(result.errors))
        return result.workflow
    path = Path(workflow)
    if path.is_absolute():
        loader = WorkflowLoader(path.parent)
        return loader.load(path)
    return loader.load(path)


def _display_name(path: Path | str | None) -> str | None:
    return None if path is None else str(path)


def _unit_display(unit: dict[str, Any]) -> str | None:
    lines = unit.get("lines")
    if lines is not None:
        return f"[lines x{len(lines)}]"
    line = unit.get("line")
    if line is not None:
        return f"[line] {line}"
    batch_paths = unit.get("__batched_paths__")
    if batch_paths is not None:
        return f"[path batch x{len(batch_paths)}]"
    shared_paths = unit.get("__shared_paths__")
    if shared_paths is not None:
        return f"[shared path x{len(shared_paths)}]"
    return _display_name(unit.get("path"))


def _build_line_batches(lines: list[str], batch_size: int) -> list[dict[str, Any]]:
    return [{"lines": lines[index : index + batch_size]} for index in range(0, len(lines), batch_size)]


def _build_path_batches(units: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    for batch_index, index in enumerate(range(0, len(units), batch_size), start=1):
        batches.append(
            {
                "__batched_paths__": units[index : index + batch_size],
                "batch_index": batch_index,
            }
        )
    return batches
