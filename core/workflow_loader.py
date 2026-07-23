"""Workflow YAML loading, listing, and validation.

Current YAML schema:

```yaml
meta:
  name: Example
  description: ...
  version: "1.0.0"
  slug: optional-slug
atom: file              # optional GUI metadata: file | folder | line | none
scope: 1                # 0 = shared, 1 = per-unit, N > 1 = batched
recurse: false          # optional path-input directory expansion
steps:
  - module: verify-rename-path
    name: Rename
    params: {...}
```

The validator rejects fields outside this contract, including ``mode``,
``batch`` and ``batch_scope``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import WorkflowValidationError

WORKFLOW_SUFFIXES = (".yaml", ".yml")
VALID_ATOMS = ("file", "folder", "line", "none")
# scope: 0 = shared (single task), 1 = per-unit (isolated), >1 = fixed-size batch
VALID_SCOPES = ">=0"


@dataclass(frozen=True)
class WorkflowMeta:
    name: str
    description: str = ""
    version: str = "1.0.0"
    slug: str = ""

    def to_dict(self) -> dict[str, Any]:
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
    module: str
    params: dict[str, Any] = field(default_factory=dict)
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "module": self.module,
            "params": dict(self.params),
        }
        if self.name:
            data["name"] = self.name
        return data


@dataclass(frozen=True)
class WorkflowDefinition:
    meta: WorkflowMeta
    scope: int
    steps: tuple[WorkflowStep, ...]
    atom: str | None = None
    recurse: bool = False
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "meta": self.meta.to_dict(),
            "scope": self.scope,
            "recurse": self.recurse,
            "steps": [step.to_dict() for step in self.steps],
        }
        if self.atom is not None:
            data["atom"] = self.atom
        return data


@dataclass(frozen=True)
class WorkflowSummary:
    filename: str
    name: str
    scope: int
    step_count: int
    path: Path
    atom: str = ""
    description: str = ""
    recurse: bool = False
    is_valid: bool = True
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowValidationResult:
    is_valid: bool
    errors: tuple[str, ...] = ()
    workflow: WorkflowDefinition | None = None


class WorkflowLoader:
    """Load, list, and validate workflows under a root directory."""

    def __init__(self, workflows_dir: str | Path) -> None:
        self.workflows_dir = Path(workflows_dir).resolve()

    def list_workflows(self, *, include_invalid: bool = False) -> list[WorkflowSummary]:
        if not self.workflows_dir.exists():
            return []
        out: list[WorkflowSummary] = []
        for path in sorted(self.workflows_dir.iterdir(), key=lambda p: p.name.lower()):
            if not path.is_file() or path.suffix.lower() not in WORKFLOW_SUFFIXES:
                continue
            try:
                wf = self.load(path.name)
                out.append(
                    WorkflowSummary(
                        filename=path.name,
                        name=wf.meta.name,
                        description=wf.meta.description,
                        atom=wf.atom or "",
                        scope=wf.scope,
                        recurse=wf.recurse,
                        step_count=len(wf.steps),
                        path=path,
                    )
                )
            except (OSError, yaml.YAMLError, WorkflowValidationError) as exc:
                if include_invalid:
                    errs = exc.errors if isinstance(exc, WorkflowValidationError) else (str(exc),)
                    out.append(
                        WorkflowSummary(
                            filename=path.name,
                            name=path.stem,
                            description="",
                            atom="invalid",
                            scope=-1,
                            step_count=0,
                            path=path,
                            is_valid=False,
                            errors=tuple(errs),
                        )
                    )
        return out

    def load(self, name: str | Path) -> WorkflowDefinition:
        path = self._resolve_path(name)
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        result = self.validate_document(doc, source_path=path)
        if not result.is_valid or result.workflow is None:
            raise WorkflowValidationError(list(result.errors))
        return result.workflow

    def validate_document(
        self,
        document: WorkflowDefinition | Mapping[str, Any],
        *,
        source_path: str | Path | None = None,
    ) -> WorkflowValidationResult:
        if isinstance(document, WorkflowDefinition):
            doc = document.to_dict()
        elif isinstance(document, Mapping):
            doc = dict(document)
        else:
            return WorkflowValidationResult(
                is_valid=False,
                errors=("Workflow document must be a mapping or WorkflowDefinition.",),
            )

        errors: list[str] = []

        if "mode" in doc:
            errors.append("旧 'mode' 字段已被弃用，请使用 atom/scope/recurse。")
        if "batch" in doc:
            errors.append("旧 'batch' 字段已被弃用，请使用 scope。")
        if "batch_scope" in doc:
            errors.append("旧 'batch_scope' 字段已被弃用，请使用 scope。")

        meta_data = doc.get("meta")
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

        atom = doc.get("atom")
        if atom is None:
            atom_value: str | None = None
        elif isinstance(atom, str) and atom in VALID_ATOMS:
            atom_value = atom
        else:
            valid_atoms = ", ".join(f"'{value}'" for value in VALID_ATOMS)
            errors.append(f"Field 'atom' must be one of: {valid_atoms} (optional, GUI metadata only).")
            atom_value = None
        scope = doc.get("scope")
        if not isinstance(scope, int) or scope < 0:
            errors.append("Field 'scope' must be an integer >= 0.")
        recurse = doc.get("recurse", False)
        if not isinstance(recurse, bool):
            errors.append("Field 'recurse' must be a boolean (omitted → false).")
            recurse = False

        raw_steps = doc.get("steps")
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

        valid_name = name if isinstance(name, str) else ""
        valid_scope = scope if isinstance(scope, int) else 1

        definition = WorkflowDefinition(
            meta=WorkflowMeta(
                name=valid_name.strip(),
                description=description.strip(),
                version=version.strip(),
                slug=slug.strip() if isinstance(slug, str) else "",
            ),
            atom=atom_value,
            scope=valid_scope,
            recurse=recurse,
            steps=tuple(steps),
            source_path=Path(source_path).resolve() if source_path else None,
        )
        return WorkflowValidationResult(is_valid=True, errors=(), workflow=definition)

    def _resolve_path(self, name: str | Path) -> Path:
        raw = Path(name)
        if raw.is_absolute():
            candidate = raw if raw.suffix.lower() in WORKFLOW_SUFFIXES else raw.with_suffix(".yaml")
            try:
                candidate = candidate.resolve()
                candidate.relative_to(self.workflows_dir.resolve())
            except ValueError as exc:
                raise ValueError("Workflow path must stay within the workflows directory.") from exc
        else:
            normalized = raw if raw.suffix.lower() in WORKFLOW_SUFFIXES else raw.with_suffix(".yaml")
            candidate = (self.workflows_dir / normalized).resolve()
            try:
                candidate.relative_to(self.workflows_dir.resolve())
            except ValueError as exc:
                raise ValueError("Workflow path must stay within the workflows directory.") from exc
        return candidate


def resolve_workflow_definition(
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
