"""State helpers for the workflow editor and schema-driven parameter forms."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    """One selectable option in a generated form field."""

    label: str
    value: Any


@dataclass(frozen=True, slots=True)
class SchemaField:
    """Normalized schema field consumed by the dynamic parameter form."""

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
    active_mode: str | None = None,
) -> list[ModuleDefinition]:
    """Filter and sort modules by tag intersection and compatible mode."""

    filtered: list[ModuleDefinition] = []
    for definition in modules.values():
        if active_tags and not (active_tags <= set(definition.tags)):
            continue
        if active_mode and active_mode not in definition.mode:
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
    """Normalize a module config schema into ordered GUI field definitions."""

    if not isinstance(schema, Mapping):
        return ()

    properties: Mapping[str, Any]
    if schema.get("type") == "object" and isinstance(schema.get("properties"), Mapping):
        properties = schema["properties"]
        required_fields = {
            item
            for item in schema.get("required", [])
            if isinstance(item, str) and item.strip()
        }
    else:
        properties = {
            key: value
            for key, value in schema.items()
            if isinstance(key, str) and isinstance(value, Mapping)
        }
        required_fields = set()

    fields: list[SchemaField] = []
    for name, raw_definition in properties.items():
        definition = dict(raw_definition)
        field_type = _normalize_field_type(definition)
        options = _normalize_options(definition)
        default = definition.get("default", _default_value_for_type(field_type, options))
        fields.append(
            SchemaField(
                name=name,
                field_type=field_type,
                label=str(
                    definition.get("title")
                    or definition.get("label")
                    or name.replace("_", " ").title()
                ),
                default=default,
                required=name in required_fields or bool(definition.get("required")),
                options=options,
                minimum=_coerce_number(definition.get("minimum")),
                maximum=_coerce_number(definition.get("maximum")),
                step=_coerce_number(definition.get("step")),
                placeholder=str(definition.get("placeholder", "")),
                description=str(definition.get("description", "")),
            )
        )
    return tuple(fields)


def build_default_params(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a default parameter dictionary from a config schema."""

    return {
        field.name: field.default
        for field in iter_schema_fields(schema)
    }


def normalize_params(
    schema: Mapping[str, Any] | None,
    values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge explicit values onto schema defaults with basic type coercion."""

    raw_values = dict(values or {})
    normalized: dict[str, Any] = {}
    for field in iter_schema_fields(schema):
        candidate = raw_values.get(field.name, field.default)
        normalized[field.name] = coerce_field_value(field, candidate)
    return normalized


def coerce_field_value(field: SchemaField, value: Any) -> Any:
    """Coerce a raw GUI value to the normalized type expected by the workflow."""

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
        valid_values = {option.value for option in field.options}
        if value in valid_values:
            return value
        return field.default

    if field.field_type in {"file_path", "folder_path", "str"}:
        return str(value)

    return value


@dataclass(slots=True)
class WorkflowDraft:
    """Mutable editor state that can be converted to a workflow definition."""

    name: str
    mode: str
    description: str = ""
    steps: list[WorkflowStep] | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []

    @classmethod
    def from_workflow(cls, workflow: WorkflowDefinition) -> "WorkflowDraft":
        """Create a mutable draft from a loaded or new workflow."""

        return cls(
            name=workflow.meta.name,
            description=workflow.meta.description,
            mode=workflow.mode,
            steps=[
                WorkflowStep(
                    module=step.module,
                    params=dict(step.params),
                    name=step.name,
                )
                for step in workflow.steps
            ],
            source_path=workflow.source_path,
        )

    def add_step(
        self,
        module_definition: ModuleDefinition,
        *,
        step_name: str = "",
    ) -> WorkflowStep:
        """Append a module as a new step, inserting after parent if present."""

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
        """Remove one step by index."""

        return self.steps.pop(index)

    def move_step(self, index: int, offset: int) -> int:
        """Move a step up or down and return the new index."""

        new_index = max(0, min(index + offset, len(self.steps) - 1))
        if new_index == index:
            return index
        step = self.steps.pop(index)
        self.steps.insert(new_index, step)
        return new_index

    def update_step_name(self, index: int, name: str) -> None:
        """Replace the step display name."""

        current = self.steps[index]
        self.steps[index] = WorkflowStep(
            module=current.module,
            params=dict(current.params),
            name=name.strip(),
        )

    def update_step_params(self, index: int, params: Mapping[str, Any]) -> None:
        """Replace the step parameters."""

        current = self.steps[index]
        self.steps[index] = WorkflowStep(
            module=current.module,
            params=dict(params),
            name=current.name,
        )

    def to_workflow_definition(self) -> WorkflowDefinition:
        """Convert the draft to the immutable workflow structure."""

        return WorkflowDefinition(
            meta=WorkflowMeta(
                name=self.name.strip(),
                description=self.description.strip(),
                version=CORE_VERSION,
                slug=uuid.uuid4().hex[:8],
            ),
            mode=self.mode,
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
