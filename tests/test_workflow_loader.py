"""Workflow loader: new YAML schema, migration rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import WorkflowDefinition, WorkflowLoader, WorkflowMeta


@pytest.fixture()
def loader(tmp_path: Path) -> WorkflowLoader:
    return WorkflowLoader(tmp_path / "workflows")


VALID_DOC = {
    "meta": {"name": "Demo", "description": "d", "version": "1.0.0"},
    "atom": "file",
    "scope": 1,
    "recurse": True,
    "steps": [
        {"module": "demo", "name": "Run demo", "params": {"k": "v"}},
    ],
}


def test_validate_valid_document_succeeds(loader: WorkflowLoader) -> None:
    result = loader.validate_document(VALID_DOC)
    assert result.is_valid, result.errors
    wf = result.workflow
    assert wf.atom == "file"
    assert wf.scope == 1
    assert wf.recurse is True
    assert wf.steps[0].module == "demo"


def test_validate_atom_optional(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC}
    del doc["atom"]
    result = loader.validate_document(doc)
    assert result.is_valid, result.errors
    assert result.workflow is not None
    assert result.workflow.atom is None


def test_validate_recurse_decoupled_from_atom(loader: WorkflowLoader) -> None:
    # recurse no longer cross-validated against atom (kernel derives the
    # execution shape from actual inputs). atom=line + recurse=true is valid.
    doc = {**VALID_DOC, "atom": "line", "recurse": True}
    result = loader.validate_document(doc)
    assert result.is_valid, result.errors


def test_validate_legacy_mode_rejected(loader: WorkflowLoader) -> None:
    doc = {"meta": {"name": "X"}, "mode": "file", "steps": []}
    result = loader.validate_document(doc)
    assert not result.is_valid
    assert any("mode" in e for e in result.errors)


def test_validate_legacy_batch_rejected(loader: WorkflowLoader) -> None:
    doc = {
        "meta": {"name": "X"},
        "atom": "file",
        "scope": 1,
        "batch": 1,
        "steps": [],
    }
    result = loader.validate_document(doc)
    assert not result.is_valid
    assert any("batch" in e for e in result.errors)


def test_validate_legacy_batch_scope_rejected(loader: WorkflowLoader) -> None:
    doc = {
        "meta": {"name": "X"},
        "atom": "file",
        "scope": 1,
        "batch_scope": "shared",
        "steps": [],
    }
    result = loader.validate_document(doc)
    assert not result.is_valid
    assert any("batch_scope" in e for e in result.errors)


def test_validate_invalid_atom_rejected(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "atom": "bogus"}
    assert not loader.validate_document(doc).is_valid


def test_validate_invalid_scope_rejected(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "scope": "bogus"}
    assert not loader.validate_document(doc).is_valid


def test_validate_negative_scope_rejected(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "scope": -1}
    assert not loader.validate_document(doc).is_valid


def test_validate_large_scope_accepted(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "scope": 999}
    result = loader.validate_document(doc)
    assert result.is_valid, result.errors
    assert result.workflow is not None
    assert result.workflow.scope == 999


def test_validate_empty_steps_list_ok(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "steps": []}
    result = loader.validate_document(doc)
    assert result.is_valid
    assert result.workflow.steps == ()


def test_validate_step_must_be_mapping(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "steps": ["not-a-dict"]}
    result = loader.validate_document(doc)
    assert not result.is_valid


def test_load_real_yaml_document(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    loader.workflows_dir.mkdir()
    (loader.workflows_dir / "round.yaml").write_text(
        "meta:\n  name: Round\natom: line\nscope: 1\nrecurse: false\n"
        "steps:\n  - module: demo\n    name: x\n    params:\n      a: 1\n",
        encoding="utf-8",
    )
    loaded = loader.load("round.yaml")
    assert loaded.atom == "line"
    assert loaded.steps[0].module == "demo"
    assert loaded.steps[0].params == {"a": 1}


def test_path_beyond_workflows_dir_rejected(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    with pytest.raises(ValueError):
        loader._resolve_path("../escape.yaml")


def test_list_workflows_includes_invalid_flag(tmp_path: Path) -> None:
    ldr = WorkflowLoader(tmp_path / "workflows")
    ldr.workflows_dir.mkdir()
    (ldr.workflows_dir / "a.yaml").write_text(
        "meta:\n  name: A\natom: file\nscope: 1\nsteps: []\n",
        encoding="utf-8",
    )
    (tmp_path / "workflows" / "bad.yaml").write_text(
        "meta:\n  name: Bad\natom: bogus\nscope: x\nsteps: []\n", encoding="utf-8"
    )
    summaries = ldr.list_workflows(include_invalid=True)
    valid = [s for s in summaries if s.is_valid]
    invalid = [s for s in summaries if not s.is_valid]
    assert len(valid) == 1
    assert len(invalid) == 1
    assert invalid[0].atom == "invalid"


def test_validate_document_accepts_definition(loader: WorkflowLoader) -> None:
    wf = WorkflowDefinition(
        meta=WorkflowMeta(name="A", description="d"),
        atom="none",
        scope=1,
        steps=(),
    )
    result = loader.validate_document(wf)
    assert result.is_valid
