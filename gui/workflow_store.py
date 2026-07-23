"""GUI-owned workflow authoring and persistence services."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from core import WorkflowDefinition, WorkflowLoader
from core.exceptions import WorkflowValidationError
from core.workflow_loader import WORKFLOW_SUFFIXES


class WorkflowAuthoringStore:
    """Create and save workflow YAML inside one GUI project."""

    def __init__(self, workflows_dir: str | Path) -> None:
        self.workflows_dir = Path(workflows_dir).resolve()
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.loader = WorkflowLoader(self.workflows_dir)

    @staticmethod
    def default_filename(name: str) -> str:
        slug = "".join(character.lower() if character.isalnum() else "-" for character in name.strip())
        cleaned = "-".join(part for part in slug.split("-") if part)
        return f"{cleaned or 'workflow'}.yaml"

    def import_workflow(self, path: str | Path) -> WorkflowDefinition:
        source = Path(path).resolve()
        imported = WorkflowLoader(source.parent).load(source.name)
        return WorkflowDefinition(
            meta=imported.meta,
            atom=imported.atom,
            scope=imported.scope,
            recurse=imported.recurse,
            steps=imported.steps,
        )

    def save(self, workflow: WorkflowDefinition, name: str | Path | None = None) -> Path:
        result = self.loader.validate_document(workflow)
        if not result.is_valid or result.workflow is None:
            raise WorkflowValidationError(list(result.errors))

        target = self._resolve_target(name or self.default_filename(result.workflow.meta.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                yaml.safe_dump(
                    result.workflow.to_dict(),
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(target)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return target

    def _resolve_target(self, name: str | Path) -> Path:
        raw = Path(name)
        normalized = raw if raw.suffix.lower() in WORKFLOW_SUFFIXES else raw.with_suffix(".yaml")
        candidate = normalized.resolve() if normalized.is_absolute() else (self.workflows_dir / normalized).resolve()
        try:
            candidate.relative_to(self.workflows_dir)
        except ValueError as exc:
            raise ValueError("Workflow path must stay within the active project's workflows directory.") from exc
        return candidate
