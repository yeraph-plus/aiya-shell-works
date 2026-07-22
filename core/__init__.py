"""Shell Worker core: runtime engine, workflows, modules."""

from __future__ import annotations

from .config_schema import (
    SUPPORTED_CONFIG_TYPES,
    ConfigSchemaValidationError,
    ConfigValidationError,
    normalize_config_params,
    validate_config_schema,
)
from .context import PipelineContext
from .events import (
    EventBus,
    InMemorySink,
    JSONLFileSink,
    LogSink,
    NullSink,
    PipelineEvent,
    PipelineEventType,
)
from .exceptions import (
    FileHandlingError,
    ModuleExecutionError,
    PipelineCancelledError,
    PipelineExecutionError,
    TerminalSpawnError,
    WorkflowValidationError,
)
from .executor import PipelineExecutor, PreparedStep, execute_workflow
from .files import WorkingCopier, build_lines_units, build_path_units, make_unique_path, units_from_plan
from .input import InputPlan, resolve_input
from .input_inspector import InputInspector, ValidationResult
from .module_manager import (
    ModuleDefinition,
    ModuleManager,
)
from .runtime import PipelineRuntime
from .scheduler import WorkflowScheduler
from .terminal import TerminalResult, TerminalSession, TerminalSessionRegistry, get_session
from .tools import collect_file_targets, ensure_pty_available, parse_extension_set
from .workflow_loader import (
    VALID_SCOPES,
    WorkflowDefinition,
    WorkflowLoader,
    WorkflowMeta,
    WorkflowStep,
    WorkflowSummary,
    WorkflowValidationResult,
)

CORE_VERSION = "2.0.0"

__all__ = [
    "CORE_VERSION",
    "PipelineContext",
    "PipelineEvent",
    "PipelineEventType",
    "EventBus",
    "InMemorySink",
    "JSONLFileSink",
    "LogSink",
    "NullSink",
    "PipelineRuntime",
    "TerminalResult",
    "TerminalSession",
    "TerminalSessionRegistry",
    "get_session",
    "InputPlan",
    "resolve_input",
    "InputInspector",
    "ValidationResult",
    "WorkingCopier",
    "build_lines_units",
    "build_path_units",
    "units_from_plan",
    "make_unique_path",
    "WorkflowDefinition",
    "WorkflowLoader",
    "WorkflowMeta",
    "WorkflowStep",
    "WorkflowSummary",
    "WorkflowValidationResult",
    "VALID_SCOPES",
    "WorkflowValidationError",
    "ModuleDefinition",
    "ModuleManager",
    "PipelineExecutor",
    "execute_workflow",
    "WorkflowScheduler",
    "PreparedStep",
    "PipelineCancelledError",
    "PipelineExecutionError",
    "ModuleExecutionError",
    "TerminalSpawnError",
    "FileHandlingError",
    "ConfigSchemaValidationError",
    "ConfigValidationError",
    "SUPPORTED_CONFIG_TYPES",
    "normalize_config_params",
    "validate_config_schema",
    "collect_file_targets",
    "ensure_pty_available",
    "parse_extension_set",
]
