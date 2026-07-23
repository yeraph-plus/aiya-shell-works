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
from .executor import ExecutionSummary, PipelineExecutor, PreparedStep, UnitResult, execute_workflow
from .files import (
    WorkspaceFile,
)
from .input import InputPlan, resolve_input
from .input_inspector import InputInspector, ValidationResult
from .module_manager import (
    VALID_MODULE_ACCESS,
    VALID_MODULE_PLATFORMS,
    ModuleAccess,
    ModuleDefinition,
    ModuleManager,
    current_platform,
)
from .runtime import PipelineRuntime
from .scheduler import WorkflowScheduler
from .terminal import TerminalResult, TerminalSession, TerminalSessionRegistry
from .tools import collect_file_targets, ensure_pty_available, parse_extension_set
from .version import CORE_VERSION
from .workflow_loader import (
    VALID_SCOPES,
    WorkflowDefinition,
    WorkflowLoader,
    WorkflowMeta,
    WorkflowStep,
    WorkflowSummary,
    WorkflowValidationResult,
    resolve_workflow_definition,
)

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
    "InputPlan",
    "resolve_input",
    "InputInspector",
    "ValidationResult",
    "WorkspaceFile",
    "WorkflowDefinition",
    "WorkflowLoader",
    "WorkflowMeta",
    "WorkflowStep",
    "WorkflowSummary",
    "WorkflowValidationResult",
    "resolve_workflow_definition",
    "VALID_SCOPES",
    "WorkflowValidationError",
    "ModuleDefinition",
    "ModuleManager",
    "ModuleAccess",
    "VALID_MODULE_ACCESS",
    "VALID_MODULE_PLATFORMS",
    "current_platform",
    "PipelineExecutor",
    "ExecutionSummary",
    "UnitResult",
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
