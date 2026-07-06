"""Tests for workflow YAML loading, saving, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core import WorkflowLoader, WorkflowMeta, WorkflowStep, WorkflowValidationError
from core.workflow_loader import WorkflowDefinition


def test_load_valid_workflow_file(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    workflow_path = workflows_dir / "image-pipeline.yaml"
    workflow_path.write_text(
        yaml.safe_dump(
            {
                "meta": {
                    "name": "Image Pipeline",
                    "description": "Resize and rename images.",
                    "version": "1.2.0",
                },
                "mode": "file",
                "steps": [
                    {
                        "module": "resize-image",
                        "params": {"width": 640, "height": 480},
                        "name": "Resize",
                    },
                    {
                        "module": "rename-file",
                        "params": {"suffix": "_done"},
                    },
                ],
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )

    loader = WorkflowLoader(workflows_dir)
    workflow = loader.load("image-pipeline.yaml")

    assert workflow.meta.name == "Image Pipeline"
    assert workflow.mode == "file"
    assert len(workflow.steps) == 2
    assert workflow.steps[0].name == "Resize"
    assert workflow.steps[1].params == {"suffix": "_done"}
    assert workflow.source_path == workflow_path.resolve()


def test_validate_document_reports_schema_errors(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {
            "meta": {"name": ""},
            "mode": "zip",
            "steps": [{"module": "", "params": []}],
        }
    )

    assert result.is_valid is False
    assert "Field 'meta.name' must be a non-empty string." in result.errors
    assert "Field 'mode' must be one of: 'file', 'folder', 'none', 'cycle', 'input'." in result.errors
    assert "Field 'steps[0].module' must be a non-empty string." in result.errors


def test_save_normalizes_and_round_trips_workflow(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    loader = WorkflowLoader(workflows_dir)
    workflow = WorkflowDefinition(
        meta=WorkflowMeta(name="Folder Processor", description="Process copied folders."),
        mode="folder",
        steps=(
            WorkflowStep(module="prepare-folder", params={"overwrite": True}),
            WorkflowStep(module="summarize", params={}),
        ),
    )

    saved_path = loader.save(workflow)
    loaded = loader.load(saved_path.name)

    assert saved_path.name == "folder-processor.yaml"
    assert saved_path.exists()
    assert loaded.to_dict() == workflow.to_dict()


def test_list_workflows_can_include_invalid_entries(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "good.yaml").write_text(
        yaml.safe_dump(
            {
                "meta": {"name": "Good Workflow"},
                "mode": "none",
                "steps": [],
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    (workflows_dir / "broken.yaml").write_text("meta: []\nmode: file\nsteps: {}\n", encoding="utf-8")

    loader = WorkflowLoader(workflows_dir)
    summaries = loader.list_workflows(include_invalid=True)

    assert [summary.filename for summary in summaries] == ["broken.yaml", "good.yaml"]
    invalid_summary = summaries[0]
    valid_summary = summaries[1]
    assert invalid_summary.is_valid is False
    assert "Field 'meta' must be a mapping." in invalid_summary.errors
    assert valid_summary.name == "Good Workflow"
    assert valid_summary.mode == "none"


def test_save_raises_for_invalid_workflow() -> None:
    loader = WorkflowLoader("workflows")

    with pytest.raises(WorkflowValidationError) as exc_info:
        loader.save({"meta": {"name": "Missing Steps"}, "mode": "file"})

    assert "Field 'steps' must be a list." in exc_info.value.errors


def test_new_workflow_returns_editor_friendly_template(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")

    workflow = loader.new_workflow(name="Draft", mode="none", description="Empty editor draft")

    assert workflow.meta.name == "Draft"
    assert workflow.mode == "none"
    assert workflow.steps == ()
    assert workflow.to_dict()["steps"] == []


def test_load_rejects_paths_outside_workflows_dir(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("meta: {name: Outside}\nmode: none\nsteps: []\n", encoding="utf-8")
    loader = WorkflowLoader(workflows_dir)

    with pytest.raises(ValueError, match="within the workflows directory"):
        loader.load("..\\outside.yaml")


# ---------------------------------------------------------------------------
# Additional boundary tests
# ---------------------------------------------------------------------------


def test_list_workflows_excludes_invalid_by_default(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "good.yaml").write_text(
        yaml.safe_dump({"meta": {"name": "Good"}, "mode": "none", "steps": []}),
        encoding="utf-8",
    )
    (workflows_dir / "bad.yaml").write_text("bogus", encoding="utf-8")

    loader = WorkflowLoader(workflows_dir)
    summaries = loader.list_workflows()
    assert len(summaries) == 1
    assert summaries[0].filename == "good.yaml"


def test_list_workflows_empty_dir(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    loader = WorkflowLoader(workflows_dir)
    assert loader.list_workflows() == []


def test_list_workflows_nonexistent_dir(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "no-such-workflows")
    assert loader.list_workflows() == []


def test_list_workflows_skips_non_yaml_suffix(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "valid.yaml").write_text(
        yaml.safe_dump({"meta": {"name": "Valid"}, "mode": "none", "steps": []}),
        encoding="utf-8",
    )
    (workflows_dir / "readme.txt").write_text("not a workflow", encoding="utf-8")
    (workflows_dir / "config.json").write_text('{"key": 1}', encoding="utf-8")

    loader = WorkflowLoader(workflows_dir)
    summaries = loader.list_workflows()
    assert len(summaries) == 1
    assert summaries[0].filename == "valid.yaml"


def test_load_yml_suffix_file(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "test.yml").write_text(
        yaml.safe_dump({"meta": {"name": "YML Test"}, "mode": "none", "steps": []}),
        encoding="utf-8",
    )
    loader = WorkflowLoader(workflows_dir)
    workflow = loader.load("test.yml")
    assert workflow.meta.name == "YML Test"


def test_load_file_not_found_propagates(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    loader = WorkflowLoader(workflows_dir)
    with pytest.raises(FileNotFoundError):
        loader.load("missing.yaml")


def test_load_empty_yaml_validates_and_raises(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "empty.yaml").write_text("", encoding="utf-8")

    loader = WorkflowLoader(workflows_dir)
    with pytest.raises(WorkflowValidationError, match="must be a mapping"):
        loader.load("empty.yaml")


def test_load_yaml_list_not_mapping(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "list.yaml").write_text("- item: 1\n- item: 2\n", encoding="utf-8")

    loader = WorkflowLoader(workflows_dir)
    with pytest.raises(WorkflowValidationError, match="must be a mapping"):
        loader.load("list.yaml")


def test_validate_document_version_default(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "mode": "none", "steps": []}
    )
    assert result.is_valid is True


def test_validate_document_empty_version(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test", "version": "  "}, "mode": "none", "steps": []}
    )
    assert result.is_valid is False
    assert any("Field 'meta.version'" in e for e in result.errors)


def test_validate_document_valid_slug(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test", "slug": "my-slug"}, "mode": "none", "steps": []}
    )
    assert result.is_valid is True


def test_validate_document_empty_slug(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test", "slug": "  "}, "mode": "none", "steps": []}
    )
    assert result.is_valid is False
    assert any("Field 'meta.slug'" in e for e in result.errors)


def test_validate_document_missing_mode(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "steps": []}
    )
    assert result.is_valid is False
    assert any("mode" in e.lower() for e in result.errors)


def test_validate_document_step_not_mapping(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "mode": "none", "steps": ["not a dict"]}
    )
    assert result.is_valid is False
    assert any("steps[0]" in e for e in result.errors)


def test_validate_document_empty_module_slug(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "mode": "none", "steps": [{"module": "  "}]}
    )
    assert result.is_valid is False
    assert any("steps[0].module" in e for e in result.errors)


def test_validate_document_empty_step_name_is_rejected(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "mode": "none", "steps": [{"module": "mod", "name": "  "}]}
    )
    assert result.is_valid is False
    assert any("name" in e and "non-empty" in e for e in result.errors)


def test_validate_document_description_not_string(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test", "description": 123}, "mode": "none", "steps": []}
    )
    assert result.is_valid is False
    assert any("description" in e for e in result.errors)


def test_validate_document_params_not_mapping(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "mode": "none", "steps": [{"module": "mod", "params": "bad"}]}
    )
    assert result.is_valid is False
    assert any("steps[0].params" in e for e in result.errors)


def test_new_workflow_default_mode(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    workflow = loader.new_workflow(name="Default")
    assert workflow.mode == "file"


def test_save_with_explicit_name(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    loader = WorkflowLoader(workflows_dir)
    workflow = WorkflowDefinition(
        meta=WorkflowMeta(name="Test"),
        mode="none",
        steps=(),
    )
    saved = loader.save(workflow, workflow_name="custom-name.yaml")
    assert saved.name == "custom-name.yaml"
    assert saved.exists()


def test_save_from_dict_mapping(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    loader = WorkflowLoader(workflows_dir)
    saved = loader.save(
        {"meta": {"name": "From Dict"}, "mode": "none", "steps": []}
    )
    assert saved.name == "from-dict.yaml"


def test_default_filename_special_chars(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    workflow = WorkflowDefinition(
        meta=WorkflowMeta(name="!!!Special $$$ Chars???"),
        mode="none",
        steps=(),
    )
    saved = loader.save(workflow)
    assert saved.name.endswith(".yaml")


def test_workflow_meta_to_dict_excludes_empty_slug() -> None:
    meta = WorkflowMeta(name="Test", slug="")
    d = meta.to_dict()
    assert "slug" not in d


def test_workflow_step_to_dict_excludes_empty_name() -> None:
    step = WorkflowStep(module="mod", params={})
    d = step.to_dict()
    assert "name" not in d


def test_validate_document_source_path_as_string(tmp_path: Path) -> None:
    loader = WorkflowLoader(tmp_path / "workflows")
    result = loader.validate_document(
        {"meta": {"name": "Test"}, "mode": "none", "steps": []},
        source_path=str(tmp_path / "source.yaml"),
    )
    assert result.is_valid is True
