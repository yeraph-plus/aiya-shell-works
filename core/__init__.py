"""Core package for workflow execution and shared services."""

CORE_VERSION = "1.0.0"

from .pipeline import PipelineContext, PipelineEvent, PipelineEventBus, PipelineEventType, PipelineMode
from .input_inspector import InputInspector, ValidationResult
from .handler_input import InputHandler
from .handler_file import FileHandler, FileHandlingError
from .terminal import TerminalResult, TerminalSession, get_session as get_terminal_session
from .workflow_loader import (
    VALID_WORKFLOW_MODES,
    WorkflowDefinition,
    WorkflowLoader,
    WorkflowMeta,
    WorkflowStep,
    WorkflowSummary,
    WorkflowValidationError,
    WorkflowValidationResult,
)
from .module_manager import ModuleDefinition, ModuleManager
from .executor import (
    PipelineCancelledError,
    PipelineExecutionError,
    PipelineExecutor,
    execute_workflow,
)
from .config_schema import (
    ConfigSchemaValidationError,
    ConfigValidationError,
    SUPPORTED_CONFIG_TYPES,
    normalize_config_params,
    validate_config_schema,
)
from .tools import (
    collect_file_targets,
    ensure_pywinpty,
    make_unique_path,
    parse_extension_set,
)

__all__ = [
    "CORE_VERSION",
    "FileHandler",
    "FileHandlingError",
    "PipelineContext",
    "PipelineEvent",
    "PipelineEventBus",
    "PipelineEventType",
    "PipelineMode",
    "InputInspector",
    "ValidationResult",
    "InputHandler",
    "TerminalResult",
    "TerminalSession",
    "get_terminal_session",
    "VALID_WORKFLOW_MODES",
    "WorkflowDefinition",
    "WorkflowLoader",
    "WorkflowMeta",
    "WorkflowStep",
    "WorkflowSummary",
    "WorkflowValidationError",
    "WorkflowValidationResult",
    "ModuleDefinition",
    "ModuleManager",
    "PipelineCancelledError",
    "PipelineExecutionError",
    "PipelineExecutor",
    "execute_workflow",
    "ConfigSchemaValidationError",
    "ConfigValidationError",
    "SUPPORTED_CONFIG_TYPES",
    "normalize_config_params",
    "validate_config_schema",
    "collect_file_targets",
    "ensure_pywinpty",
    "make_unique_path",
    "parse_extension_set",
]
