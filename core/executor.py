"""Workflow execution engine independent from the GUI layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .config_schema import (
    ConfigSchemaValidationError,
    ConfigValidationError,
    normalize_config_params,
)
from .handler_input import InputHandler
from .handler_file import FileHandler
from .input_inspector import InputInspector
from .module_manager import ModuleDefinition, ModuleManager
from .pipeline import PipelineContext, PipelineEvent, PipelineMode
from .workflow_loader import (
    WorkflowDefinition,
    WorkflowLoader,
    WorkflowValidationError,
)

EventCallback = Callable[[PipelineEvent], None]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class PreparedWorkflowStep:
    """Runtime-ready workflow step with validated module and params."""

    index: int
    name: str
    module_slug: str
    module_definition: ModuleDefinition
    params: dict[str, Any]


class PipelineExecutionError(RuntimeError):
    """Raised when the workflow cannot start due to invalid setup."""


class PipelineCancelledError(RuntimeError):
    """Raised when an execution is cancelled at a safe boundary."""


class PipelineExecutor:
    """Execute workflow definitions with unit-level error isolation."""

    def __init__(
        self,
        module_manager: ModuleManager,
        *,
        event_callback: EventCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> None:
        self.module_manager = module_manager
        self.event_callback = event_callback
        self.progress_callback = progress_callback
        self.cancel_requested = cancel_requested

    def execute(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
        *,
        output_dir: str | Path,
        input_path: str | Path | None = None,
        input_paths: list[str | Path] | None = None,
        input_text: str | None = None,
        direct_mode: bool = False,
        shared: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a workflow and return a summary consumable by GUI or CLI callers."""

        workflow_definition = _resolve_workflow_definition(workflow)
        prepared_steps = self._prepare_steps(workflow_definition)
        file_handler = FileHandler(output_dir, direct_mode=direct_mode)
        input_handler = InputHandler()
        units = self._build_units(
            workflow_definition,
            file_handler,
            input_handler,
            input_path,
            input_paths,
            input_text,
        )
        total_units = len(units)
        errors: list[dict[str, Any]] = []
        unit_results: list[dict[str, Any]] = []
        successful_units = 0
        cancelled = False

        self._emit_event(
            "executor",
            "message",
            f"开始执行工作流: {workflow_definition.meta.name} "
            f"(mode={workflow_definition.mode}, units={total_units}, direct={direct_mode})",
        )
        self._progress(
            current=0,
            total=total_units,
            unit=None,
            status="starting",
        )

        for warning in self.module_manager.warnings:
            self._emit_event("executor", "warning", f"模块扫描警告: {warning}")

        shared_context: PipelineContext | None = None
        for index, unit in enumerate(units, start=1):
            try:
                self._raise_if_cancelled()
            except PipelineCancelledError as exc:
                cancelled = True
                self._emit_event("executor", "warning", str(exc))
                break
            try:
                mode: PipelineMode = workflow_definition.mode
                base_ctx = (
                    shared_context
                    if mode == "cycle" and index > 1 and shared_context is not None
                    else None
                )
                context = self._prepare_context(
                    file_handler=file_handler,
                    input_handler=input_handler,
                    mode=mode,
                    unit=unit,
                    shared=shared,
                    base_context=base_ctx,
                )
                final_context = self._run_unit(
                    context=context,
                    unit_index=index,
                    total_units=total_units,
                    prepared_steps=prepared_steps,
                )

                successful_units += 1
                unit_results.append(
                    {
                        "success": True,
                        "unit": _unit_display(unit),
                        "working_path": str(final_context.working_path),
                        "original_input": _display_unit_name(final_context.original_input),
                    }
                )
                self._progress(
                    current=index,
                    total=total_units,
                    unit=_unit_display(unit),
                    status="completed",
                )
                if mode == "cycle":
                    shared_context = final_context
            except PipelineCancelledError as exc:
                cancelled = True
                self._emit_event("executor", "warning", str(exc))
                break
            except Exception as exc:
                error = {
                    "unit": _unit_display(unit),
                    "error": str(exc),
                    "type": type(exc).__name__,
                }
                errors.append(error)
                unit_results.append(
                    {
                        "success": False,
                        "unit": error["unit"],
                        "error": error["error"],
                        "type": error["type"],
                    }
                )
                self._emit_event(
                    "executor",
                    "error",
                    f"处理单元失败 [{index}/{total_units}]: "
                    f"{error['unit'] or '<none>'} -> {error['error']}",
                )
                self._progress(
                    current=index,
                    total=total_units,
                    unit=_unit_display(unit),
                    status="failed",
                )

        completed_units = successful_units + len(errors)
        success = not errors and not cancelled
        summary = {
            "success": success,
            "cancelled": cancelled,
            "processed_units": total_units,
            "successful_units": successful_units,
            "failed_units": len(errors),
            "errors": errors,
            "results": unit_results,
            "workflow": workflow_definition.meta.name,
            "mode": workflow_definition.mode,
            "output_dir": str(file_handler.output_dir),
        }
        self._emit_event(
            "executor",
            "success" if success else "message",
            f"工作流执行结束: success={success}, cancelled={cancelled}, "
            f"successful_units={successful_units}, failed_units={len(errors)}",
        )
        self._progress(
            current=completed_units,
            total=total_units,
            unit=None,
            status="cancelled" if cancelled else "done",
        )
        return summary

    def _prepare_steps(
        self,
        workflow_definition: WorkflowDefinition,
    ) -> list[PreparedWorkflowStep]:
        available_modules = self.module_manager.get_modules()
        prepared_steps: list[PreparedWorkflowStep] = []

        for index, step in enumerate(workflow_definition.steps, start=1):
            module_definition = available_modules.get(step.module)
            if module_definition is None:
                raise PipelineExecutionError(
                    f"未找到工作流步骤所需模块: {step.module}"
                )

            workflow_mode = workflow_definition.mode
            if workflow_mode not in module_definition.mode:
                raise PipelineExecutionError(
                    f"步骤 {index} 的模块 '{step.module}' 不支持工作流模式 "
                    f"'{workflow_mode}'（支持: {', '.join(module_definition.mode)}）"
                )

            try:
                params = normalize_config_params(
                    module_definition.config_schema,
                    step.params,
                )
            except ConfigValidationError as exc:
                raise PipelineExecutionError(
                    f"步骤 {index} ({step.module}) 参数校验失败: {'；'.join(exc.errors)}"
                ) from exc
            except ConfigSchemaValidationError as exc:
                raise PipelineExecutionError(
                    f"模块 {step.module} 的 CONFIG_SCHEMA 非法: {'；'.join(exc.errors)}"
                ) from exc

            prepared_steps.append(
                PreparedWorkflowStep(
                    index=index,
                    name=step.name or step.module,
                    module_slug=step.module,
                    module_definition=module_definition,
                    params=params,
                )
            )

        return prepared_steps

    # ------------------------------------------------------------------
    # Unit building – delegates to dedicated handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_units(
        workflow_definition: WorkflowDefinition,
        file_handler: FileHandler,
        input_handler: InputHandler,
        input_path: str | Path | None,
        input_paths: list[str | Path] | None,
        input_text: str | None = None,
    ) -> list[dict[str, Any]]:
        mode: PipelineMode = workflow_definition.mode

        if mode == "none":
            if input_path is not None or input_paths:
                raise PipelineExecutionError("none 模式不接受输入路径。")
            return [{"path": None, "source_root": None}]

        if mode == "input":
            if input_path is not None or input_paths:
                raise PipelineExecutionError(
                    "input 模式不接受文件输入路径，请使用文本输入。"
                )
            lines = InputInspector.validate_text_input(input_text or "")
            return input_handler.build_units(lines) if lines else []

        sources = PipelineExecutor._normalize_input_sources(
            mode=mode,
            input_path=input_path,
            input_paths=input_paths,
        )

        if mode == "file":
            return file_handler.build_file_units(sources)

        if mode == "cycle":
            return file_handler.build_cycle_units(sources)

        if mode == "folder":
            if len(sources) != 1:
                raise PipelineExecutionError("folder 模式仅接受一个输入文件夹。")
            source = sources[0]
            if not source.is_dir():
                raise PipelineExecutionError(
                    "folder 模式要求输入路径为文件夹。"
                )
            return file_handler.build_folder_unit(source)

        raise PipelineExecutionError(f"不支持的工作流模式: {mode}")

    @staticmethod
    def _normalize_input_sources(
        *,
        mode: PipelineMode,
        input_path: str | Path | None,
        input_paths: list[str | Path] | None,
    ) -> list[Path]:
        if input_path is not None and input_paths:
            raise PipelineExecutionError("不能同时提供 input_path 和 input_paths。")

        raw_sources: list[str | Path]
        if input_paths is not None:
            raw_sources = list(input_paths)
        elif input_path is not None:
            raw_sources = [input_path]
        else:
            raw_sources = []

        if not raw_sources:
            raise PipelineExecutionError(
                f"{mode} 模式执行前必须提供输入路径。"
            )

        sources: list[Path] = []
        for raw in raw_sources:
            source = Path(raw)
            if not source.exists():
                raise PipelineExecutionError(f"输入路径不存在: {source}")
            if not source.is_file() and not source.is_dir():
                raise PipelineExecutionError(f"不支持的输入路径类型: {source}")
            sources.append(source)
        return sources

    # ------------------------------------------------------------------
    # Context preparation – delegates to handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_context(
        *,
        file_handler: FileHandler,
        input_handler: InputHandler,
        mode: PipelineMode,
        unit: dict[str, Any],
        shared: Mapping[str, Any] | None = None,
        base_context: PipelineContext | None = None,
    ) -> PipelineContext:
        if mode == "input":
            return input_handler.prepare_context(
                unit.get("line", ""),
                file_handler.output_dir,
                shared=dict(shared or {}),
            )
        return file_handler.prepare_context(
            unit,
            mode=mode,
            shared=dict(shared or {}),
            base_context=base_context,
        )

    # ------------------------------------------------------------------
    # Unit execution
    # ------------------------------------------------------------------

    def _run_unit(
        self,
        *,
        context: PipelineContext,
        unit_index: int,
        total_units: int,
        prepared_steps: list[PreparedWorkflowStep],
    ) -> PipelineContext:
        self._raise_if_cancelled()
        unit_name = _display_unit_name(context.original_input) or "<none>"
        current_context = context
        subscribed_buses: list[object] = []

        try:
            self._subscribe_live_events(current_context, subscribed_buses)
            context.events.log(
                "executor",
                "message",
                f"开始处理单元 [{unit_index}/{total_units}]: {unit_name}",
            )

            for step in prepared_steps:
                self._raise_if_cancelled()
                self._subscribe_live_events(current_context, subscribed_buses)
                current_context.events.log(
                    step.module_slug,
                    "message",
                    f"开始步骤 [{unit_index}/{total_units}] {step.index}/{len(prepared_steps)}: {step.name}",
                )

                try:
                    result = step.module_definition.run(
                        current_context, dict(step.params)
                    )
                except Exception as exc:
                    current_context.events.log(
                        step.module_slug,
                        "error",
                        f"步骤 {step.index} ({step.module_slug}) 执行失败: {exc}",
                    )
                    raise PipelineExecutionError(
                        f"步骤 {step.index} ({step.module_slug}) 执行失败: {exc}"
                    ) from exc

                current_context = self._resolve_step_result(
                    step_name=step.name,
                    result=result,
                    fallback=current_context,
                )
                self._subscribe_live_events(current_context, subscribed_buses)
                current_context.events.log(
                    step.module_slug,
                    "success",
                    f"完成步骤 [{unit_index}/{total_units}] {step.index}/{len(prepared_steps)}: {step.name}",
                )

            current_context.events.log(
                "executor",
                "success",
                f"处理单元成功 [{unit_index}/{total_units}]: {unit_name}",
            )
            return current_context
        finally:
            if self.event_callback is not None:
                for bus in subscribed_buses:
                    bus.unsubscribe(self.event_callback)

    def _resolve_step_result(
        self,
        *,
        step_name: str,
        result: Any,
        fallback: PipelineContext,
    ) -> PipelineContext:
        if result is None:
            return fallback
        if isinstance(result, PipelineContext):
            return result
        if isinstance(result, Mapping) and isinstance(
            result.get("context"), PipelineContext
        ):
            return result["context"]
        raise PipelineExecutionError(
            f"步骤 {step_name} 返回值非法，必须返回 PipelineContext、None 或包含 context 的字典。"
        )

    def _emit_event(
        self, slug: str, event_type: str, text: str, data: dict[str, Any] | None = None
    ) -> None:
        if self.event_callback is not None:
            self.event_callback(
                PipelineEvent(slug=slug, type=event_type, text=text, data=data or {})
            )

    def _progress(
        self,
        *,
        current: int,
        total: int,
        unit: Path | None,
        status: str,
    ) -> None:
        if self.progress_callback is None:
            return
        percent = 100 if total == 0 else int(current * 100 / total)
        self.progress_callback(
            {
                "current": current,
                "total": total,
                "percent": percent,
                "unit": _display_unit_name(unit),
                "status": status,
            }
        )

    def _subscribe_live_events(
        self,
        context: PipelineContext,
        subscribed_buses: list[object],
    ) -> None:
        if self.event_callback is None:
            return
        bus = context.events
        if bus in subscribed_buses:
            return
        bus.subscribe(self.event_callback)
        subscribed_buses.append(bus)

    def _raise_if_cancelled(self) -> None:
        if self.cancel_requested is not None and self.cancel_requested():
            raise PipelineCancelledError("执行已取消，停止后续处理。")


def execute_workflow(
    workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
    *,
    output_dir: str | Path,
    input_path: str | Path | None = None,
    input_paths: list[str | Path] | None = None,
    input_text: str | None = None,
    direct_mode: bool = False,
    modules_dir: str | Path = "modules",
    event_callback: EventCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: CancelCallback | None = None,
    shared: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure helper for running workflows without importing GUI code."""

    module_manager = ModuleManager(modules_dir)
    executor = PipelineExecutor(
        module_manager,
        event_callback=event_callback,
        progress_callback=progress_callback,
        cancel_requested=cancel_requested,
    )
    return executor.execute(
        workflow,
        output_dir=output_dir,
        input_path=input_path,
        input_paths=input_paths,
        input_text=input_text,
        direct_mode=direct_mode,
        shared=shared,
    )


def _resolve_workflow_definition(
    workflow: WorkflowDefinition | Mapping[str, Any] | str | Path,
) -> WorkflowDefinition:
    if isinstance(workflow, WorkflowDefinition):
        return workflow

    loader = WorkflowLoader(Path.cwd() / "workflows")

    if isinstance(workflow, Mapping):
        return _validate_and_unwrap(loader.validate_document(workflow))

    workflow_path = Path(workflow)
    if workflow_path.is_absolute():
        loader = WorkflowLoader(workflow_path.parent)
        return _validate_and_unwrap(
            loader.validate_document(_read_yaml(workflow_path), source_path=workflow_path)
        )

    return loader.load(workflow_path)


def _validate_and_unwrap(result: Any) -> WorkflowDefinition:  # WorkflowValidationResult
    if not result.is_valid or result.workflow is None:
        raise WorkflowValidationError(list(result.errors))
    return result.workflow


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _display_unit_name(path: Path | str | None) -> str | None:
    if path is None:
        return None
    return str(path)


def _unit_display(unit: dict[str, Any]) -> str | None:
    line = unit.get("line")
    if line is not None:
        return f"[input] {line}"
    return _display_unit_name(unit.get("path"))
