"""Workflow loader: new YAML schema, migration rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import WorkflowDefinition, WorkflowLoader, WorkflowMeta, WorkflowStep


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


def test_validate_recurse_only_when_atom_file(loader: WorkflowLoader) -> None:
    # atom=line cannot specify recurse=true
    doc = {**VALID_DOC, "atom": "line", "recurse": True}
    result = loader.validate_document(doc)
    assert not result.is_valid
    assert any("recurse" in e for e in result.errors)


def test_validate_empty_steps_list_ok(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "steps": []}
    result = loader.validate_document(doc)
    assert result.is_valid
    assert result.workflow.steps == ()


def test_validate_step_must_be_mapping(loader: WorkflowLoader) -> None:
    doc = {**VALID_DOC, "steps": ["not-a-dict"]}
    result = loader.validate_document(doc)
    assert not result.is_valid


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Round trip via a real loader that writes/reads YAML."""
    loader = WorkflowLoader(tmp_path / "workflows")
    loader.ensure_workflows_dir()
    wf = WorkflowDefinition(
        meta=WorkflowMeta(name="Round", description="roundtest"),
        atom="line",
        scope=1,
        steps=(WorkflowStep(module="demo", name="x", params={"a": 1}),),
        recurse=False,
    )
    saved = loader.save(wf, "round.yaml")
    loaded = loader.load(saved.name)
    assert loaded.atom == "line"
    assert loaded.steps[0].module == "demo"
    assert loaded.steps[0].params == {"a": 1}


def test_path_beyond_workflows_dir_rejected(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    with pytest.raises(ValueError):
        loader._resolve_path("../escape.yaml")


def test_list_workflows_includes_invalid_flag(tmp_path: Path) -> None:
    ldr = WorkflowLoader(tmp_path / "workflows")
    ldr.ensure_workflows_dir()
    # Valid file:
    ldr.save(
        WorkflowDefinition(
            meta=WorkflowMeta(name="A"),
            atom="file",
            scope=1,
            steps=(),
        ),
        "a.yaml",
    )
    # Invalid YAML:
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
