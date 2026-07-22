"""State helpers for the workflow editor and schema-driven parameter forms.

Adapted to the new atom × scope × recurse model.  ``filter_modules`` now
ANDs on both ``atom`` and ``scope``, and ``WorkflowDraft`` carries the new
three fields instead of the legacy ``mode`` string.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import CORE_VERSION, ModuleDefinition, WorkflowDefinition, WorkflowMeta, WorkflowStep

SUPPORTED_FIELD_TYPES = (
    "int",
    "float",
    "str",
    "bool",
    "select",
    "radio",
    "file_path",
    "folder_path",
)


@dataclass(frozen=True, slots=True)
class SchemaOption:
    label: str
    value: Any


@dataclass(frozen=True, slots=True)
class SchemaField:
    name: str
    field_type: str
    label: str
    default: Any
    required: bool
    options: tuple[SchemaOption, ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    placeholder: str = ""
    description: str = ""


def filter_modules(
    modules: Mapping[str, ModuleDefinition],
    *,
    active_tags: set[str] | None = None,
) -> list[ModuleDefinition]:
    """Filter and sort modules by tag."""

    filtered: list[ModuleDefinition] = []
    for definition in modules.values():
        if active_tags and not (active_tags <= set(definition.tags)):
            continue
        filtered.append(definition)

    return sorted(
        filtered,
        key=lambda item: (
            str(item.module_meta.get("name", "")).lower(),
            item.slug.lower(),
        ),
    )


def iter_schema_fields(schema: Mapping[str, Any] | None) -> tuple[SchemaField, ...]:
    if not isinstance(schema, Mapping):
        return ()
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), Mapping):
        return ()
    properties = schema["properties"]
    required_fields = {item for item in schema.get("required", []) if isinstance(item, str) and item.strip()}

    fields: list[SchemaField] = []
    for name, raw in properties.items():
        definition = dict(raw)
        field_type = _normalize_field_type(definition)
        options = _normalize_options(definition)
        default = definition.get("default", _default_value_for_type(field_type, options))
        fields.append(
            SchemaField(
                name=name,
                field_type=field_type,
                label=str(definition.get("title") or definition.get("label") or name.replace("_", " ").title()),
                default=default,
                required=name in required_fields or bool(definition.get("required")),
                options=options,
                minimum=_coerce_number(definition.get("min")),
                maximum=_coerce_number(definition.get("max")),
                step=_coerce_number(definition.get("step")),
                placeholder=str(definition.get("placeholder", "")),
                description=str(definition.get("description", "")),
            )
        )
    return tuple(fields)


def build_default_params(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    return {field.name: field.default for field in iter_schema_fields(schema)}


def normalize_params(
    schema: Mapping[str, Any] | None,
    values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(values or {})
    out: dict[str, Any] = {}
    for field in iter_schema_fields(schema):
        out[field.name] = coerce_field_value(field, raw.get(field.name, field.default))
    return out


def coerce_field_value(field: SchemaField, value: Any) -> Any:
    if value is None:
        return field.default
    if field.field_type == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(field.default) if field.default not in ("", None) else 0
    if field.field_type == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(field.default) if field.default not in ("", None) else 0.0
    if field.field_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if field.field_type in {"select", "radio"}:
        valid = {option.value for option in field.options}
        if value in valid:
            return value
        return field.default
    if field.field_type in {"file_path", "folder_path", "str"}:
        return str(value)
    return value


@dataclass(slots=True)
class WorkflowDraft:
    """Mutable editor state for the new atom/scope/recurse workflow schema."""

    name: str
    atom: str
    scope: int = 1
    recurse: bool = False
    description: str = ""
    steps: list[WorkflowStep] | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []

    @classmethod
    def from_workflow(cls, workflow: WorkflowDefinition) -> WorkflowDraft:
        return cls(
            name=workflow.meta.name,
            description=workflow.meta.description,
            atom=workflow.atom or "file",
            scope=workflow.scope,
            recurse=workflow.recurse,
            steps=[
                WorkflowStep(module=step.module, params=dict(step.params), name=step.name) for step in workflow.steps
            ],
            source_path=workflow.source_path,
        )

    def add_step(
        self,
        module_definition: ModuleDefinition,
        *,
        step_name: str = "",
    ) -> WorkflowStep:
        params = build_default_params(module_definition.config_schema)
        step = WorkflowStep(
            module=module_definition.slug,
            params=params,
            name=step_name.strip(),
        )
        parent_slug = module_definition.parent
        if parent_slug:
            for i, existing in enumerate(self.steps):
                if existing.module == parent_slug:
                    self.steps.insert(i + 1, step)
                    return step
        self.steps.append(step)
        return step

    def remove_step(self, index: int) -> WorkflowStep:
        return self.steps.pop(index)

    def move_step(self, index: int, offset: int) -> int:
        new_index = max(0, min(index + offset, len(self.steps) - 1))
        if new_index == index:
            return index
        step = self.steps.pop(index)
        self.steps.insert(new_index, step)
        return new_index

    def update_step_name(self, index: int, name: str) -> None:
        current = self.steps[index]
        self.steps[index] = WorkflowStep(
            module=current.module,
            params=dict(current.params),
            name=name.strip(),
        )

    def update_step_params(self, index: int, params: Mapping[str, Any]) -> None:
        current = self.steps[index]
        self.steps[index] = WorkflowStep(
            module=current.module,
            params=dict(params),
            name=current.name,
        )

    def to_workflow_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            meta=WorkflowMeta(
                name=self.name.strip(),
                description=self.description.strip(),
                version=CORE_VERSION,
                slug=uuid.uuid4().hex[:8],
            ),
            atom=self.atom,
            scope=self.scope,
            recurse=self.recurse,
            steps=tuple(self.steps),
            source_path=self.source_path,
        )


def _normalize_field_type(definition: Mapping[str, Any]) -> str:
    raw_type = definition.get("type")
    if isinstance(raw_type, str):
        normalized = raw_type.strip().lower()
    else:
        normalized = "str"
    if normalized in {"integer", "int"}:
        return "int"
    if normalized in {"number", "float"}:
        return "float"
    if normalized in {"string", "str"} and definition.get("enum"):
        return "select"
    if normalized in {"boolean", "bool"}:
        return "bool"
    if normalized in SUPPORTED_FIELD_TYPES:
        return normalized
    return "str"


def _normalize_options(definition: Mapping[str, Any]) -> tuple[SchemaOption, ...]:
    raw_options = definition.get("options", definition.get("enum", []))
    if not isinstance(raw_options, Iterable) or isinstance(raw_options, (str, bytes, Mapping)):
        return ()
    options: list[SchemaOption] = []
    for item in raw_options:
        if isinstance(item, Mapping):
            value = item.get("value")
            label = item.get("label", value)
        else:
            value = item
            label = item
        if value is None:
            continue
        options.append(SchemaOption(label=str(label), value=value))
    return tuple(options)


def _default_value_for_type(
    field_type: str,
    options: tuple[SchemaOption, ...],
) -> Any:
    if field_type == "int":
        return 0
    if field_type == "float":
        return 0.0
    if field_type == "bool":
        return False
    if field_type in {"select", "radio"}:
        if options:
            return options[0].value
        return ""
    return ""


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None
