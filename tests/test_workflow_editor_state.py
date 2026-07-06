from __future__ import annotations

from pathlib import Path
from types import ModuleType

from core import CORE_VERSION, ModuleDefinition, WorkflowMeta
from core.workflow_loader import WorkflowDefinition, WorkflowStep
from gui.workflow_editor_state import (
    WorkflowDraft,
    build_default_params,
    filter_modules,
    iter_schema_fields,
    normalize_params,
)


def make_module(
    slug: str,
    name: str,
    config_schema: dict,
    *,
    description: str = "",
    tags: list[str] | None = None,
    mode: list[str] | None = None,
    parent: str | None = None,
) -> ModuleDefinition:
    module = ModuleType(slug)
    return ModuleDefinition(
        slug=slug,
        module_meta={
            "slug": slug,
            "name": name,
            "description": description,
            "tags": tags or [],
        },
        config_schema=config_schema,
        run=lambda context, config: None,
        path=Path(f"/virtual/{slug}.py"),
        module=module,
        core_version="1.0.0",
        tags=tuple(tags or []),
        mode=tuple(mode or ["file"]),
        parent=parent,
    )


def test_iter_schema_fields_normalizes_supported_types_and_defaults() -> None:
    schema = {
        "type": "object",
        "required": ["width"],
        "properties": {
            "width": {
                "type": "integer",
                "title": "Width",
                "default": 1280,
                "minimum": 64,
                "maximum": 4096,
                "step": 64,
            },
            "quality": {"type": "number", "default": 0.85},
            "enabled": {"type": "boolean", "default": True},
            "format": {"type": "string", "enum": ["png", "jpg"]},
            "output_dir": {"type": "folder_path"},
        },
    }

    fields = iter_schema_fields(schema)

    assert [field.name for field in fields] == [
        "width",
        "quality",
        "enabled",
        "format",
        "output_dir",
    ]
    assert fields[0].field_type == "int"
    assert fields[0].required is True
    assert fields[0].default == 1280
    assert fields[3].field_type == "select"
    assert [option.value for option in fields[3].options] == ["png", "jpg"]
    assert fields[4].field_type == "folder_path"
    assert build_default_params(schema) == {
        "width": 1280,
        "quality": 0.85,
        "enabled": True,
        "format": "png",
        "output_dir": "",
    }


def test_normalize_params_merges_defaults_and_coerces_types() -> None:
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "int", "default": 2},
            "ratio": {"type": "float", "default": 1.5},
            "enabled": {"type": "bool", "default": False},
            "preset": {"type": "select", "options": ["small", "large"]},
            "target": {"type": "file_path"},
        },
    }

    params = normalize_params(
        schema,
        {
            "count": "4",
            "ratio": "3.25",
            "enabled": "true",
            "preset": "large",
            "target": Path("demo.txt"),
        },
    )

    assert params == {
        "count": 4,
        "ratio": 3.25,
        "enabled": True,
        "preset": "large",
        "target": "demo.txt",
    }


def test_workflow_draft_supports_filter_add_reorder_and_export() -> None:
    resize_module = make_module(
        "resize-image",
        "Resize Image",
        {
            "type": "object",
            "properties": {
                "width": {"type": "int", "default": 640},
                "height": {"type": "int", "default": 480},
            },
        },
        description="Resize image files",
        tags=["image", "resize"],
        mode=["file"],
    )
    rename_module = make_module(
        "rename-file",
        "Rename File",
        {"type": "object", "properties": {"suffix": {"type": "str", "default": "_done"}}},
        description="Rename generated files",
        tags=["file"],
        mode=["file", "folder"],
    )
    modules = {
        resize_module.slug: resize_module,
        rename_module.slug: rename_module,
    }

    filtered = filter_modules(modules, active_tags={"image"})
    assert [item.slug for item in filtered] == ["resize-image"]

    filtered_all = filter_modules(modules)
    assert len(filtered_all) == 2

    draft = WorkflowDraft(
        name="Media Pipeline",
        mode="file",
        description="Draft pipeline",
    )
    draft.add_step(resize_module)
    draft.add_step(rename_module, step_name="Rename Output")
    draft.update_step_params(0, {"width": 1920, "height": 1080})
    draft.move_step(1, -1)
    draft.update_step_name(0, "Rename First")

    assert [step.module for step in draft.steps] == ["rename-file", "resize-image"]
    assert draft.steps[0].name == "Rename First"
    assert draft.steps[1].params == {"width": 1920, "height": 1080}

    workflow = draft.to_workflow_definition()

    assert workflow.meta.name == "Media Pipeline"
    assert workflow.meta.description == "Draft pipeline"
    assert workflow.meta.version == CORE_VERSION
    assert len(workflow.meta.slug) == 8
    assert workflow.mode == "file"
    assert workflow.steps == (
        WorkflowStep(
            module="rename-file",
            params={"suffix": "_done"},
            name="Rename First",
        ),
        WorkflowStep(
            module="resize-image",
            params={"width": 1920, "height": 1080},
            name="",
        ),
    )


def test_workflow_draft_from_workflow_preserves_source_path() -> None:
    source_path = Path("/virtual/workflows/demo.yaml")
    workflow = WorkflowDefinition(
        meta=WorkflowMeta(name="Existing", description="Loaded"),
        mode="none",
        steps=(WorkflowStep(module="echo", params={"message": "hi"}),),
        source_path=source_path,
    )

    draft = WorkflowDraft.from_workflow(workflow)

    assert draft.source_path == source_path
    assert draft.steps[0].params == {"message": "hi"}


def test_filter_modules_by_mode_excludes_incompatible() -> None:
    file_module = make_module("fm", "File Only", {}, mode=["file"])
    none_module = make_module("nm", "None Only", {}, mode=["none"])

    modules = {"fm": file_module, "nm": none_module}

    filtered_file = filter_modules(modules, active_mode="file")
    assert [m.slug for m in filtered_file] == ["fm"]

    filtered_none = filter_modules(modules, active_mode="none")
    assert [m.slug for m in filtered_none] == ["nm"]


def test_filter_modules_by_tag_uses_and_logic() -> None:
    mod_a = make_module("a", "A", {}, tags=["image", "resize"], mode=["file"])
    mod_b = make_module("b", "B", {}, tags=["image"], mode=["file"])
    mod_c = make_module("c", "C", {}, tags=["resize"], mode=["file"])

    modules = {"a": mod_a, "b": mod_b, "c": mod_c}

    filtered = filter_modules(modules, active_tags={"image", "resize"})
    assert [m.slug for m in filtered] == ["a"]

    filtered_single = filter_modules(modules, active_tags={"image"})
    assert [m.slug for m in filtered_single] == ["a", "b"]


def test_workflow_draft_inserts_child_after_parent() -> None:
    parent_mod = make_module("parent", "Parent", {}, mode=["file"])
    child_mod = make_module("child", "Child", {}, mode=["file"], parent="parent")

    draft = WorkflowDraft(name="Test", mode="file")
    draft.add_step(parent_mod)
    draft.add_step(child_mod)

    assert [step.module for step in draft.steps] == ["parent", "child"]


def test_workflow_draft_appends_if_parent_not_found() -> None:
    mod_a = make_module("a", "A", {}, mode=["file"])
    mod_b = make_module("b", "B", {}, mode=["file"], parent="missing")

    draft = WorkflowDraft(name="Test", mode="file")
    draft.add_step(mod_a)
    draft.add_step(mod_b)

    assert [step.module for step in draft.steps] == ["a", "b"]


def test_workflow_draft_inserts_middle_when_parent_in_middle() -> None:
    mod_a = make_module("a", "A", {}, mode=["file"])
    mod_b = make_module("b", "B", {}, mode=["file"])
    mod_c = make_module("c", "C", {}, mode=["file"], parent="a")

    draft = WorkflowDraft(name="Test", mode="file")
    draft.add_step(mod_a)
    draft.add_step(mod_b)
    draft.add_step(mod_c)

    assert [step.module for step in draft.steps] == ["a", "c", "b"]
