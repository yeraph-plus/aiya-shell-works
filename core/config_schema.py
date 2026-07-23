"""Validate module CONFIG_SCHEMA and normalize workflow step parameters.

The module contract supports eight field types. Numeric ranges use ``min``
and ``max``; workflow preparation fills defaults before invoking a module.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

SUPPORTED_CONFIG_TYPES = frozenset({"int", "float", "str", "bool", "select", "radio", "file_path", "folder_path"})

_MISSING = object()


class _ConfigError(ValueError):
    def __init__(self, errors: list[str], default_message: str = "Invalid config.") -> None:
        self.errors = tuple(errors)
        message = "; ".join(self.errors) if self.errors else default_message
        super().__init__(message)


class ConfigSchemaValidationError(_ConfigError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors, default_message="Invalid CONFIG_SCHEMA.")


class ConfigValidationError(_ConfigError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors, default_message="Invalid step params.")


def validate_config_schema(schema: Any) -> tuple[bool, tuple[str, ...]]:
    """Return whether a CONFIG_SCHEMA matches the supported structure."""

    if not isinstance(schema, dict):
        return False, ("CONFIG_SCHEMA 必须是字典。",)

    errors: list[str] = []
    if schema.get("type") != "object":
        errors.append("CONFIG_SCHEMA.type 必须为 'object'。")

    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        errors.append("CONFIG_SCHEMA.properties 必须是字典。")
        properties = {}

    required = schema.get("required", [])
    required_names: set[str] = set()
    if required in (None, []):
        required_names = set()
    elif isinstance(required, list) and all(isinstance(item, str) and item.strip() for item in required):
        required_names = {item.strip() for item in required}
    else:
        errors.append("CONFIG_SCHEMA.required 必须是非空字符串列表。")

    for field_name, field_schema in properties.items():
        prefix = f"CONFIG_SCHEMA.properties.{field_name}"
        if not isinstance(field_name, str) or not field_name.strip():
            errors.append(f"{prefix} 的字段名必须是非空字符串。")
            continue
        if not isinstance(field_schema, Mapping):
            errors.append(f"{prefix} 必须是字典。")
            continue
        field_type = field_schema.get("type")
        if field_type not in SUPPORTED_CONFIG_TYPES:
            supported = ", ".join(sorted(SUPPORTED_CONFIG_TYPES))
            errors.append(f"{prefix}.type 必须是以下之一: {supported}。")
            continue
        title = field_schema.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            errors.append(f"{prefix}.title 提供时必须是非空字符串。")
        description = field_schema.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{prefix}.description 提供时必须是字符串。")
        field_required = field_schema.get("required")
        if field_required is not None and not isinstance(field_required, bool):
            errors.append(f"{prefix}.required 提供时必须是布尔值。")
        default = field_schema.get("default", _MISSING)
        if default is not _MISSING:
            try:
                _validate_single_value(field_name, field_type, default, field_schema)
            except ConfigValidationError as exc:
                errors.extend(f"{prefix}.default 无效: {d}" for d in exc.errors)
        minimum = field_schema.get("min", _MISSING)
        maximum = field_schema.get("max", _MISSING)
        if minimum is not _MISSING or maximum is not _MISSING:
            if field_type not in {"int", "float"}:
                errors.append(f"{prefix}.min/max 仅适用于 int 或 float 类型。")
            else:
                if minimum is not _MISSING and not _is_number(minimum):
                    errors.append(f"{prefix}.min 必须是数字。")
                if maximum is not _MISSING and not _is_number(maximum):
                    errors.append(f"{prefix}.max 必须是数字。")
                if (
                    minimum is not _MISSING
                    and maximum is not _MISSING
                    and _is_number(minimum)
                    and _is_number(maximum)
                    and minimum > maximum
                ):
                    errors.append(f"{prefix}.min 不能大于 max。")
        options = field_schema.get("options", _MISSING)
        if field_type in {"select", "radio"}:
            if not isinstance(options, list) or not options:
                errors.append(f"{prefix}.options 必须是非空列表。")
            elif any(not _is_scalar_option(item) for item in options):
                errors.append(f"{prefix}.options 只能包含字符串、数字或布尔值。")
        elif options is not _MISSING and not isinstance(options, list):
            errors.append(f"{prefix}.options 提供时必须是列表。")

    if required_names:
        missing = sorted(n for n in required_names if n not in properties)
        if missing:
            errors.append(f"CONFIG_SCHEMA.required 引用了未定义字段: {', '.join(missing)}。")
    return not errors, tuple(errors)


def normalize_config_params(
    schema: Mapping[str, Any],
    params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate step params and fill defaults from a (validated) schema."""

    if params is None:
        raw: dict[str, Any] = {}
    elif isinstance(params, Mapping):
        raw = dict(params)
    else:
        raise ConfigValidationError(["步骤参数必须是字典。"])

    properties = dict(schema.get("properties", {}))
    top_required = {name.strip() for name in schema.get("required", []) if isinstance(name, str) and name.strip()}
    errors: list[str] = []
    normalized: dict[str, Any] = {}

    for key in raw:
        if key not in properties:
            errors.append(f"存在未声明参数: {key}")

    for field_name, field_schema in properties.items():
        typed = dict(field_schema)
        field_type = typed["type"]
        is_required = bool(typed.get("required", False) or field_name in top_required)
        if field_name in raw:
            value = raw[field_name]
        elif "default" in typed:
            value = typed["default"]
        elif is_required:
            errors.append(f"缺少必填参数: {field_name}")
            continue
        else:
            continue
        try:
            normalized[field_name] = _validate_single_value(field_name, field_type, value, typed)
        except ConfigValidationError as exc:
            errors.extend(exc.errors)

    if errors:
        raise ConfigValidationError(errors)
    return normalized


def _validate_single_value(
    field_name: str,
    field_type: str,
    value: Any,
    field_schema: Mapping[str, Any],
) -> Any:
    if field_type == "int":
        if type(value) is not int:
            raise ConfigValidationError([f"参数 {field_name} 必须是整数。"])
        _validate_number_range(field_name, value, field_schema)
        return value
    if field_type == "float":
        if not _is_number(value):
            raise ConfigValidationError([f"参数 {field_name} 必须是数字。"])
        v = float(value)
        _validate_number_range(field_name, v, field_schema)
        return v
    if field_type == "str":
        if not isinstance(value, str):
            raise ConfigValidationError([f"参数 {field_name} 必须是字符串。"])
        return value
    if field_type == "bool":
        if not isinstance(value, bool):
            raise ConfigValidationError([f"参数 {field_name} 必须是布尔值。"])
        return value
    if field_type in {"select", "radio"}:
        valid = {
            item["value"] if isinstance(item, Mapping) and "value" in item else item
            for item in field_schema.get("options", [])
        }
        if value not in valid:
            raise ConfigValidationError([f"参数 {field_name} 必须是以下选项之一: {sorted(valid)}。"])
        return value
    if field_type in {"file_path", "folder_path"}:
        if isinstance(value, Path):
            return str(value)
        if not isinstance(value, str):
            raise ConfigValidationError([f"参数 {field_name} 必须是路径字符串。"])
        return value
    raise ConfigValidationError([f"参数 {field_name} 使用了不支持的类型: {field_type}。"])


def _validate_number_range(field_name: str, value: int | float, field_schema: Mapping[str, Any]) -> None:
    errors: list[str] = []
    minimum = field_schema.get("min", _MISSING)
    maximum = field_schema.get("max", _MISSING)
    if minimum is not _MISSING and value < minimum:
        errors.append(f"参数 {field_name} 不能小于 {minimum}。")
    if maximum is not _MISSING and value > maximum:
        errors.append(f"参数 {field_name} 不能大于 {maximum}。")
    if errors:
        raise ConfigValidationError(errors)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_scalar_option(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, Mapping) and "value" in value:
        return _is_scalar_option(value["value"])
    return False
