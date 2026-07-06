"""Shell Worker core: runtime engine, workflows, modules."""

from __future__ import annotations

CORE_VERSION = "2.0.0"

from .events import (
    EventBus, InMemorySink, JSONLFileSink, LogSink, NullSink,
    PipelineEvent, PipelineEventType,
)
from .context import Atom, PipelineContext
from .terminal import TerminalResult, TerminalSession, TerminalSessionRegistry, get_session
from .runtime import PipelineRuntime
from .config_schema import (
    ConfigSchemaValidationError, ConfigValidationError,
    SUPPORTED_CONFIG_TYPES, normalize_config_params, validate_config_schema,
)
from .exceptions import (
    PipelineCancelledError,
    PipelineExecutionError,
    FileHandlingError,
    WorkflowValidationError,
)
from .input import InputPlan, resolve_input
from .files import WorkingCopier, build_lines_units, build_path_units, make_unique_path, units_from_plan
from .input_inspector import InputInspector, ValidationResult
from .workflow_loader import (
    VALID_ATOMS, VALID_SCOPES,
    WorkflowDefinition, WorkflowLoader, WorkflowMeta, WorkflowStep, WorkflowSummary,
    WorkflowValidationResult,
)
from .module_manager import (
    ModuleDefinition, ModuleManager,
)
from .executor import PipelineExecutor, execute_workflow, PreparedStep
from .tools import collect_file_targets, ensure_pty_available, parse_extension_set

__all__ = [
    "CORE_VERSION",
    "Atom",
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
    "VALID_ATOMS",
    "VALID_SCOPES",
    "WorkflowValidationError",
    "ModuleDefinition",
    "ModuleManager",
    "PipelineExecutor",
    "execute_workflow",
    "PreparedStep",
    "PipelineCancelledError",
    "PipelineExecutionError",
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