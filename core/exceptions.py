"""Unified exception hierarchy for the Shell Worker core.

For the full call chain a single module keeps the import surface slim and
makes GUI / CLI error rendering easier to maintain.
"""

from __future__ import annotations


class ShellWorkerError(RuntimeError):
    """Common base for all intentional core runtime errors."""


class PipelineExecutionError(ShellWorkerError):
    """Raised before / during execution for invalid workflow setup or step contract."""


class PipelineCancelledError(ShellWorkerError):
    """Raised when a cancel signal is observed at a safe boundary."""


class FileHandlingError(ShellWorkerError):
    """Raised when copying / preparing a working unit fails."""


class WorkflowValidationError(ValueError):
    """Raised when a YAML workflow document does not match the platform schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(errors) if errors else "Invalid workflow document.")