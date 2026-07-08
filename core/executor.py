"""Workflow executor: atom x scope x recurse driven unit dispatch.

Single-threaded execution with per-unit event-bus isolation.  Scope values
control how inputs are batched into tasks:

* ``scope=1`` (per-unit) — every listed file / folder / line is its own
  ctx.  Executor calls ``runtime.replace_bus()`` before each unit so
  each one's events never bleed into the next.
* ``scope=0`` (shared)  — all inputs are merged into a single merged
  working tree (see ``files.py``) and the workflow runs exactly once
  over the merged output folder.  The module rglobs the working tree
  itself.
* Scope values > 1 are reserved for future batch slicing; currently
  treated as 1.

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
        self._check_plan_compat(definition, plan)

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
            f"(atom={definition.atom}, scope={definition.scope}, "
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
            "atom": definition.atom,
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

    # ------------------------------------------------------------------
    # Step / compat checks
    # ------------------------------------------------------------------

    def _check_plan_compat(self, workflow: WorkflowDefinition, plan: InputPlan) -> None:
        """Validate that the resolved input plan is compatible with workflow atom.

        * atom="file"  accepts file or directory inputs; the recurse flag
          controls whether dirs expand to file units or stay as folder units.
        * atom="folder" accepts directory-only inputs; files are rejected.
        * atom="line"  requires text-line inputs.
        * atom="none"  requires no inputs.
        """
        if workflow.atom == "none":
            if plan.atom != "none":
                raise PipelineExecutionError("workflow atom='none' 不接受输入路径。")
            return
        if workflow.atom == "line":
            if plan.atom != "line":
                raise PipelineExecutionError("workflow atom='line' 不接受文件输入路径，请使用文本输入。")
            return
        # atom="file" or "folder": must have file/dir inputs
        if plan.atom not in ("file", "folder"):
            raise PipelineExecutionError(
                f"workflow atom='{workflow.atom}' 需要文件/文件夹输入，当前输入类型为 '{plan.atom}'"
            )
        if workflow.atom == "folder":
            for p in plan.files:
                if not p.is_dir():
                    raise PipelineExecutionError(f"workflow atom='folder' 仅接受文件夹输入，收到文件: {p}")

    def _prepare_steps(self, workflow: WorkflowDefinition) -> list[PreparedStep]:
        modules = self.module_manager.get_modules()
        prepared: list[PreparedStep] = []
        for idx, step in enumerate(workflow.steps, start=1):
            definition = modules.get(step.module)
            if definition is None:
                raise PipelineExecutionError(f"module not found: {step.module}")
            if workflow.atom not in definition.atom:
                raise PipelineExecutionError(
                    f"step {idx} ('{step.module}') does not support atom "
                    f"'{workflow.atom}' (supports: {', '.join(definition.atom)})"
                )
            if workflow.scope != definition.scope:
                raise PipelineExecutionError(
                    f"step {idx} ('{step.module}') does not support scope "
                    f"'{workflow.scope}' (module requires: {definition.scope})"
                )
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
        if workflow.scope == 0:
            if plan.atom == "none":
                return [{"path": None, "source_root": None}]
            if plan.atom == "line":
                return [{"line": "\n".join(plan.lines)}]
            return [{"__shared_paths__": list(plan.files), "recurse": plan.recurse, "source_root": None}]
        return units_from_plan(plan)

    def _prepare_context(
        self,
        workflow: WorkflowDefinition,
        plan: InputPlan,
        copier: WorkingCopier,
        unit: dict[str, Any],
        *,
        shared: Mapping[str, Any] | None,
    ) -> PipelineContext:
        if "__shared_paths__" in unit:
            return copier.prepare_shared_path_unit(
                list(unit["__shared_paths__"]),
                recurse=unit.get("recurse", plan.recurse),
                shared=shared,
            )
        if plan.atom == "line" or unit.get("line") is not None:
            return copier.prepare_line(unit, shared=shared)
        if plan.atom == "none" or unit.get("path") is None:
            return copier.prepare_none(shared=shared)
        ctx_atom = workflow.atom
        return copier.prepare_path_unit(unit, atom=ctx_atom, shared=shared)

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
        if result is None:
            return fallback
        if isinstance(result, PipelineContext):
            return result
        if isinstance(result, Mapping) and isinstance(result.get("context"), PipelineContext):
            return result["context"]
        raise PipelineExecutionError(
            f"step {step_name} returned invalid value: must be PipelineContext, None, or dict with context key"
        )

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
    log_file: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: CancelCallback | None = None,
    event_listener: EventCallback | None = None,
    shared: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Standalone runner for CLI callers and tests.

    Builds a fresh ``ModuleManager`` and ``PipelineRuntime`` each call.
    No GUI imports; safe under multiprocessing.
    """

    runtime = PipelineRuntime(log_file=log_file)
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
            workflow,
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
) -> WorkflowDefinition:
    if isinstance(workflow, WorkflowDefinition):
        return workflow
    loader = WorkflowLoader(Path.cwd() / "workflows")
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
    line = unit.get("line")
    if line is not None:
        return f"[line] {line}"
    return _display_name(unit.get("path"))
