"""Workflow YAML loading, saving, listing, and validation.

New YAML schema (no legacy ``mode`` field):

```yaml
meta:
  name: Example
  description: ...
  version: "1.0.0"
  slug: optional-slug
atom: file              # file | folder | line | none
scope: per-unit         # per-unit | shared
recurse: false          # optional; only meaningful when atom == file
steps:
  - module: verify-rename-path
    name: Rename
    params: {...}
```

The legacy ``mode`` and ``batch`` fields are rejected by the validator —
this is the hard-cut migration strategy decided in §D6.  No compatibility
shims are provided.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .context import Atom
from .exceptions import WorkflowValidationError

WORKFLOW_SUFFIXES = (".yaml", ".yml")
VALID_ATOMS = ("file", "folder", "line", "none")
# scope: 0 = shared (single task), 1 = per-unit (isolated), >1 reserved for batch
VALID_SCOPES = (0, 1)
_RECURSE_ONLY_ATOMS = ("file",)


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
    atom: Atom
    scope: int
    steps: tuple[WorkflowStep, ...]
    recurse: bool = False
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta.to_dict(),
            "atom": self.atom,
            "scope": self.scope,
            "recurse": self.recurse,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class WorkflowSummary:
    filename: str
    name: str
    atom: str
    scope: int
    step_count: int
    path: Path
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
    """Load, save, list, and validate workflows under a root directory."""

    def __init__(self, workflows_dir: str | Path) -> None:
        self.workflows_dir = Path(workflows_dir).resolve()

    def ensure_workflows_dir(self) -> Path:
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        return self.workflows_dir

    def new_workflow(
        self,
        name: str = "New Workflow",
        *,
        atom: str = "file",
        scope: int = 1,
        recurse: bool = False,
        description: str = "",
    ) -> WorkflowDefinition:
        return WorkflowDefinition(
            meta=WorkflowMeta(name=name, description=description),
            atom=atom,
            scope=scope,
            recurse=recurse,
            steps=(),
        )

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
                        atom=wf.atom,
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

    def save(
        self,
        workflow: WorkflowDefinition | Mapping[str, Any],
        name: str | Path | None = None,
    ) -> Path:
        result = self.validate_document(workflow)
        if not result.is_valid or result.workflow is None:
            raise WorkflowValidationError(list(result.errors))
        target_name = name or self._default_filename(result.workflow.meta.name)
        target = self._resolve_path(target_name, create_parent=True)
        serialized = result.workflow.to_dict()
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            yaml.safe_dump(serialized, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return target

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
        if not isinstance(atom, str) or atom not in VALID_ATOMS:
            errors.append("Field 'atom' must be one of: " + ", ".join(f"'{a}'" for a in VALID_ATOMS) + ".")
        scope = doc.get("scope")
        if not isinstance(scope, int) or scope not in VALID_SCOPES:
            errors.append("Field 'scope' must be an integer in " + ", ".join(str(s) for s in VALID_SCOPES) + ".")
        recurse = doc.get("recurse", False)
        if not isinstance(recurse, bool):
            errors.append("Field 'recurse' must be a boolean (omitted → false).")
            recurse = False
        if atom in _RECURSE_ONLY_ATOMS:
            # recurse only meaningful when atom == file
            pass
        if atom not in _RECURSE_ONLY_ATOMS and recurse:
            errors.append(
                f"Field 'recurse=true' 仅在 atom ∈ {list(_RECURSE_ONLY_ATOMS)} 时有意义；当前 atom='{atom}'。"
            )

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

        definition = WorkflowDefinition(
            meta=WorkflowMeta(
                name=name.strip(),
                description=description.strip(),
                version=version.strip(),
                slug=slug.strip() if isinstance(slug, str) else "",
            ),
            atom=atom,
            scope=scope,
            recurse=recurse,
            steps=tuple(steps),
            source_path=Path(source_path).resolve() if source_path else None,
        )
        return WorkflowValidationResult(is_valid=True, errors=(), workflow=definition)

    def _resolve_path(self, name: str | Path, *, create_parent: bool = False) -> Path:
        raw = Path(name)
        # An absolute path under workflows_dir is supported directly; absolute
        # paths elsewhere are rejected to keep writes contained.
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
        if create_parent:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            self.ensure_workflows_dir()
        return candidate

    @staticmethod
    def _default_filename(name: str) -> str:
        slug = "".join(c.lower() if c.isalnum() else "-" for c in name.strip())
        cleaned = "-".join(part for part in slug.split("-") if part)
        return f"{cleaned or 'workflow'}.yaml"
