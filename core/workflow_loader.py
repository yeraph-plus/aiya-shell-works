"""Workflow YAML loading, saving, and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

VALID_WORKFLOW_MODES = ("file", "folder", "none", "cycle", "input")
WORKFLOW_SUFFIXES = (".yaml", ".yml")


class WorkflowValidationError(ValueError):
    """Raised when a workflow document does not match the expected schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        message = "; ".join(self.errors) if self.errors else "Invalid workflow document."
        super().__init__(message)


@dataclass(frozen=True)
class WorkflowMeta:
    """Top-level workflow metadata shared by runtime and GUI."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    slug: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata to a YAML-safe mapping."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
        }
        if self.slug:
            data["slug"] = self.slug
        return data


@dataclass(frozen=True)
class WorkflowStep:
    """One workflow step describing a module slug and its parameters."""

    module: str
    params: dict[str, Any] = field(default_factory=dict)
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the step to a YAML-safe mapping."""
        data: dict[str, Any] = {
            "module": self.module,
            "params": dict(self.params),
        }
        if self.name:
            data["name"] = self.name
        return data


@dataclass(frozen=True)
class WorkflowDefinition:
    """In-memory workflow definition reusable by the executor and GUI."""

    meta: WorkflowMeta
    mode: str
    steps: tuple[WorkflowStep, ...]
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the workflow to a serialized mapping."""
        return {
            "meta": self.meta.to_dict(),
            "mode": self.mode,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class WorkflowSummary:
    """Lightweight data for main-window workflow selection lists."""

    filename: str
    name: str
    mode: str
    step_count: int
    path: Path
    description: str = ""
    is_valid: bool = True
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowValidationResult:
    """Result of validating a workflow document."""

    is_valid: bool
    errors: tuple[str, ...] = ()
    workflow: WorkflowDefinition | None = None


class WorkflowLoader:
    """Load, save, list, and validate workflow YAML files under a root folder."""

    def __init__(self, workflows_dir: str | Path) -> None:
        self.workflows_dir = Path(workflows_dir).resolve()

    def ensure_workflows_dir(self) -> Path:
        """Create the workflow directory when saving for the first time."""
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        return self.workflows_dir

    def new_workflow(
        self,
        name: str = "New Workflow",
        *,
        mode: str = "file",
        description: str = "",
    ) -> WorkflowDefinition:
        """Build an empty workflow template for the editor."""
        return WorkflowDefinition(
            meta=WorkflowMeta(name=name, description=description),
            mode=mode,
            steps=(),
        )

    def list_workflows(self, *, include_invalid: bool = False) -> list[WorkflowSummary]:
        """Return workflow summaries sorted by filename for GUI selectors."""
        if not self.workflows_dir.exists():
            return []

        summaries: list[WorkflowSummary] = []
        for path in sorted(self.workflows_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in WORKFLOW_SUFFIXES:
                continue

            try:
                workflow = self.load(path.name)
                summaries.append(
                    WorkflowSummary(
                        filename=path.name,
                        name=workflow.meta.name,
                        description=workflow.meta.description,
                        mode=workflow.mode,
                        step_count=len(workflow.steps),
                        path=path,
                    )
                )
            except (OSError, yaml.YAMLError, WorkflowValidationError) as exc:
                if include_invalid:
                    errors = exc.errors if isinstance(exc, WorkflowValidationError) else (str(exc),)
                    summaries.append(
                        WorkflowSummary(
                            filename=path.name,
                            name=path.stem,
                            description="",
                            mode="invalid",
                            step_count=0,
                            path=path,
                            is_valid=False,
                            errors=tuple(errors),
                        )
                    )
        return summaries

    def load(self, workflow_name: str | Path) -> WorkflowDefinition:
        """Load one YAML workflow and return its normalized definition."""
        workflow_path = self._resolve_workflow_path(workflow_name)
        with workflow_path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}

        result = self.validate_document(document, source_path=workflow_path)
        if not result.is_valid or result.workflow is None:
            raise WorkflowValidationError(list(result.errors))
        return result.workflow

    def save(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any],
        workflow_name: str | Path | None = None,
    ) -> Path:
        """Validate and save a workflow as YAML under the workflows directory."""
        source_hint = workflow_name or (
            workflow.source_path.name if isinstance(workflow, WorkflowDefinition) and workflow.source_path else None
        )
        result = self.validate_document(workflow)
        if not result.is_valid or result.workflow is None:
            raise WorkflowValidationError(list(result.errors))

        target_name = source_hint or self._default_filename(result.workflow.meta.name)
        target_path = self._resolve_workflow_path(target_name, create_parent=True)
        serialized = result.workflow.to_dict()
        with target_path.open("w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(
                serialized,
                handle,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        return target_path

    def validate_document(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any],
        *,
        source_path: str | Path | None = None,
    ) -> WorkflowValidationResult:
        """Validate workflow data and return a normalized reusable definition."""
        if isinstance(workflow, WorkflowDefinition):
            document = workflow.to_dict()
        elif isinstance(workflow, Mapping):
            document = dict(workflow)
        else:
            return WorkflowValidationResult(
                is_valid=False,
                errors=("Workflow document must be a mapping or WorkflowDefinition.",),
            )

        errors: list[str] = []

        meta_data = document.get("meta")
        if not isinstance(meta_data, Mapping):
            errors.append("Field 'meta' must be a mapping.")
            meta_data = {}

        name = meta_data.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("Field 'meta.name' must be a non-empty string.")

        description = meta_data.get("description", "")
        if not isinstance(description, str):
            errors.append("Field 'meta.description' must be a string.")
            description = ""

        version = meta_data.get("version", "1.0.0")
        if not isinstance(version, str) or not version.strip():
            errors.append("Field 'meta.version' must be a non-empty string.")
            version = "1.0.0"

        slug = meta_data.get("slug", "")
        if slug and (not isinstance(slug, str) or not slug.strip()):
            errors.append("Field 'meta.slug' must be a non-empty string when provided.")
            slug = ""

        mode = document.get("mode")
        if not isinstance(mode, str) or mode not in VALID_WORKFLOW_MODES:
            errors.append(
                "Field 'mode' must be one of: "
                + ", ".join(f"'{item}'" for item in VALID_WORKFLOW_MODES)
                + "."
            )

        raw_steps = document.get("steps")
        if not isinstance(raw_steps, list):
            errors.append("Field 'steps' must be a list.")
            raw_steps = []

        steps: list[WorkflowStep] = []
        for index, item in enumerate(raw_steps):
            prefix = f"steps[{index}]"
            if not isinstance(item, Mapping):
                errors.append(f"Field '{prefix}' must be a mapping.")
                continue

            module = item.get("module")
            if not isinstance(module, str) or not module.strip():
                errors.append(f"Field '{prefix}.module' must be a non-empty string.")
                continue

            step_name = item.get("name", "")
            if step_name and (not isinstance(step_name, str) or not step_name.strip()):
                errors.append(f"Field '{prefix}.name' must be a non-empty string when provided.")
                step_name = ""

            params = item.get("params", {})
            if not isinstance(params, Mapping):
                errors.append(f"Field '{prefix}.params' must be a mapping.")
                continue

            steps.append(
                WorkflowStep(
                    module=module.strip(),
                    params=dict(params),
                    name=step_name.strip() if isinstance(step_name, str) else "",
                )
            )

        if errors:
            return WorkflowValidationResult(is_valid=False, errors=tuple(errors))

        workflow_definition = WorkflowDefinition(
            meta=WorkflowMeta(
                name=name.strip(),
                description=description.strip(),
                version=version.strip(),
                slug=slug.strip() if isinstance(slug, str) else "",
            ),
            mode=mode,
            steps=tuple(steps),
            source_path=Path(source_path).resolve() if source_path else None,
        )
        return WorkflowValidationResult(
            is_valid=True,
            errors=(),
            workflow=workflow_definition,
        )

    def _resolve_workflow_path(
        self,
        workflow_name: str | Path,
        *,
        create_parent: bool = False,
    ) -> Path:
        raw_path = Path(workflow_name)
        normalized = raw_path if raw_path.suffix.lower() in WORKFLOW_SUFFIXES else raw_path.with_suffix(".yaml")
        candidate = (self.workflows_dir / normalized).resolve()
        try:
            candidate.relative_to(self.workflows_dir)
        except ValueError as exc:
            raise ValueError("Workflow path must stay within the workflows directory.") from exc

        if create_parent:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            self.ensure_workflows_dir()
        return candidate

    @staticmethod
    def _default_filename(name: str) -> str:
        slug = "".join(char.lower() if char.isalnum() else "-" for char in name.strip())
        cleaned = "-".join(part for part in slug.split("-") if part)
        return f"{cleaned or 'workflow'}.yaml"
