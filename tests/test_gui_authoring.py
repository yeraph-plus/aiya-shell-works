from __future__ import annotations

from pathlib import Path

import pytest

from core import WorkflowDefinition, WorkflowMeta, WorkflowStep
from gui.workflow_store import WorkflowAuthoringStore


def _workflow(name: str = "Round") -> WorkflowDefinition:
    return WorkflowDefinition(
        meta=WorkflowMeta(name=name, description="roundtrip"),
        atom="line",
        scope=1,
        recurse=False,
        steps=(WorkflowStep(module="demo", name="Run", params={"value": 1}),),
    )


def test_workflow_authoring_roundtrip(tmp_path: Path) -> None:
    store = WorkflowAuthoringStore(tmp_path / "workflows")
    saved = store.save(_workflow(), "round.yaml")
    loaded = store.loader.load(saved.name)

    assert saved.read_bytes().startswith(b"meta:\n")
    assert loaded.meta.name == "Round"
    assert loaded.steps[0].params == {"value": 1}


def test_workflow_authoring_uses_safe_default_filename(tmp_path: Path) -> None:
    store = WorkflowAuthoringStore(tmp_path / "workflows")
    saved = store.save(_workflow("My Workflow"))
    assert saved.name == "my-workflow.yaml"


def test_workflow_authoring_rejects_escape(tmp_path: Path) -> None:
    store = WorkflowAuthoringStore(tmp_path / "workflows")
    with pytest.raises(ValueError):
        store.save(_workflow(), "../escape.yaml")


def test_external_import_becomes_unsaved_project_draft(tmp_path: Path) -> None:
    store = WorkflowAuthoringStore(tmp_path / "project" / "workflows")
    external = tmp_path / "external.yaml"
    external.write_text("meta:\n  name: Imported\natom: none\nscope: 1\nsteps: []\n", encoding="utf-8")

    imported = store.import_workflow(external)

    assert imported.meta.name == "Imported"
    assert imported.source_path is None
