"""Tests for CONFIG_SCHEMA validation and parameter normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config_schema import (
    ConfigSchemaValidationError,
    ConfigValidationError,
    _is_number,
    _is_scalar_option,
    _validate_single_value,
    normalize_config_params,
    validate_config_schema,
)


# ---------------------------------------------------------------------------
# _is_number
# ---------------------------------------------------------------------------


def test_is_number_accepts_int_and_float() -> None:
    assert _is_number(1) is True
    assert _is_number(3.14) is True
    assert _is_number(0) is True
    assert _is_number(-5) is True


def test_is_number_rejects_bool() -> None:
    assert _is_number(True) is False
    assert _is_number(False) is False


def test_is_number_rejects_non_numeric() -> None:
    assert _is_number("3") is False
    assert _is_number(None) is False
    assert _is_number([]) is False


# ---------------------------------------------------------------------------
# _is_scalar_option
# ---------------------------------------------------------------------------


def test_is_scalar_option_accepts_scalars() -> None:
    assert _is_scalar_option("hello") is True
    assert _is_scalar_option(42) is True
    assert _is_scalar_option(3.14) is True
    assert _is_scalar_option(True) is True
    assert _is_scalar_option(False) is True


def test_is_scalar_option_rejects_containers() -> None:
    assert _is_scalar_option(None) is False
    assert _is_scalar_option([]) is False
    assert _is_scalar_option({}) is False
    assert _is_scalar_option(()) is False


# ---------------------------------------------------------------------------
# validate_config_schema – top-level
# ---------------------------------------------------------------------------


def test_validate_schema_rejects_non_dict() -> None:
    valid, errors = validate_config_schema("not a dict")
    assert valid is False
    assert "必须是字典" in errors[0]


def test_validate_schema_rejects_wrong_type() -> None:
    valid, errors = validate_config_schema({"type": "array", "properties": {}})
    assert valid is False
    assert "type 必须为 'object'" in errors[0]


def test_validate_schema_accepts_minimal_valid() -> None:
    valid, errors = validate_config_schema({"type": "object", "properties": {}})
    assert valid is True
    assert errors == ()


# ---------------------------------------------------------------------------
# validate_config_schema – properties edge cases
# ---------------------------------------------------------------------------


def test_validate_schema_properties_not_mapping() -> None:
    valid, errors = validate_config_schema({"type": "object", "properties": "bad"})
    assert valid is False
    assert any("properties 必须是字典" in e for e in errors)


def test_validate_schema_defaults_properties_empty() -> None:
    """When properties is omitted, schema.get returns None → error."""
    valid, errors = validate_config_schema({"type": "object"})
    assert valid is False
    assert any("properties 必须是字典" in e for e in errors)


def test_validate_schema_field_name_non_string() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {1: {"type": "str"}}}
    )
    assert valid is False
    assert any("字段名必须是非空字符串" in e for e in errors)


def test_validate_schema_field_name_empty() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"   ": {"type": "str"}}}
    )
    assert valid is False
    assert any("字段名必须是非空字符串" in e for e in errors)


def test_validate_schema_field_schema_not_mapping() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": "not a dict"}}
    )
    assert valid is False
    assert any("必须是字典" in e for e in errors)


def test_validate_schema_field_type_not_supported() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "number"}}}
    )
    assert valid is False
    assert any("type 必须是以下之一" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_config_schema – title / description / required
# ---------------------------------------------------------------------------


def test_validate_schema_title_empty_string() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "title": "  "}}}
    )
    assert valid is False
    assert any("title 提供时必须是非空字符串" in e for e in errors)


def test_validate_schema_title_not_string() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "title": 123}}}
    )
    assert valid is False
    assert any("title 提供时必须是非空字符串" in e for e in errors)


def test_validate_schema_title_none_ok() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "title": None}}}
    )
    assert valid is True


def test_validate_schema_description_not_string() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "description": 456}}}
    )
    assert valid is False
    assert any("description 提供时必须是字符串" in e for e in errors)


def test_validate_schema_field_required_not_bool() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "required": "yes"}}}
    )
    assert valid is False
    assert any("required 提供时必须是布尔值" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_config_schema – top-level required
# ---------------------------------------------------------------------------


def test_validate_schema_required_none() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {}, "required": None}
    )
    assert valid is True


def test_validate_schema_required_empty_list() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {}, "required": []}
    )
    assert valid is True


def test_validate_schema_required_valid_list() -> None:
    valid, errors = validate_config_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "str"}, "b": {"type": "int"}},
            "required": ["a"],
        }
    )
    assert valid is True


def test_validate_schema_required_non_string_items() -> None:
    valid, errors = validate_config_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "str"}},
            "required": [1, 2],
        }
    )
    assert valid is False
    assert any("必须是非空字符串列表" in e for e in errors)


def test_validate_schema_required_empty_string_item() -> None:
    valid, errors = validate_config_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "str"}},
            "required": ["a", "  "],
        }
    )
    assert valid is False
    assert any("必须是非空字符串列表" in e for e in errors)


def test_validate_schema_required_missing_field() -> None:
    valid, errors = validate_config_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "str"}},
            "required": ["a", "missing"],
        }
    )
    assert valid is False
    assert any("引用了未定义字段" in e for e in errors)
    assert "missing" in errors[0]


# ---------------------------------------------------------------------------
# validate_config_schema – default value validation
# ---------------------------------------------------------------------------


def test_validate_schema_default_valid() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "int", "default": 5}}}
    )
    assert valid is True


def test_validate_schema_default_invalid_type() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "int", "default": "bad"}}}
    )
    assert valid is False
    assert any("default 无效" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_config_schema – min/max
# ---------------------------------------------------------------------------


def test_validate_schema_min_max_on_non_numeric() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "min": 1}}}
    )
    assert valid is False
    assert any("min/max 仅适用于 int 或 float" in e for e in errors)


def test_validate_schema_min_not_number() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "int", "min": "bad"}}}
    )
    assert valid is False
    assert any("min 必须是数字" in e for e in errors)


def test_validate_schema_max_not_number() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "int", "max": "bad"}}}
    )
    assert valid is False
    assert any("max 必须是数字" in e for e in errors)


def test_validate_schema_min_greater_than_max() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "int", "min": 10, "max": 5}}}
    )
    assert valid is False
    assert any("min 不能大于 max" in e for e in errors)


def test_validate_schema_min_max_valid() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "float", "min": 0.0, "max": 100.0}}}
    )
    assert valid is True


# ---------------------------------------------------------------------------
# validate_config_schema – select / options
# ---------------------------------------------------------------------------


def test_validate_schema_select_missing_options() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "select"}}}
    )
    assert valid is False
    assert any("options 必须是非空列表" in e for e in errors)


def test_validate_schema_select_empty_options() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "select", "options": []}}}
    )
    assert valid is False
    assert any("options 必须是非空列表" in e for e in errors)


def test_validate_schema_select_invalid_option_types() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "select", "options": [1, None, []]}}}
    )
    assert valid is False
    assert any("只能包含字符串、数字或布尔值" in e for e in errors)


def test_validate_schema_select_valid_options() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "select", "options": ["a", "b", 1, True]}}}
    )
    assert valid is True


def test_validate_schema_options_on_non_select() -> None:
    valid, errors = validate_config_schema(
        {"type": "object", "properties": {"x": {"type": "str", "options": "not a list"}}}
    )
    assert valid is False
    assert any("options 提供时必须是列表" in e for e in errors)


# ---------------------------------------------------------------------------
# _validate_single_value
# ---------------------------------------------------------------------------


def test_validate_single_value_int() -> None:
    result = _validate_single_value("x", "int", 42, {})
    assert result == 42


def test_validate_single_value_int_rejects_bool() -> None:
    with pytest.raises(ConfigValidationError, match="必须是整数"):
        _validate_single_value("x", "int", True, {})


def test_validate_single_value_int_rejects_float() -> None:
    with pytest.raises(ConfigValidationError, match="必须是整数"):
        _validate_single_value("x", "int", 3.0, {})


def test_validate_single_value_float_from_int() -> None:
    result = _validate_single_value("x", "float", 5, {})
    assert result == 5.0
    assert isinstance(result, float)


def test_validate_single_value_float_rejects_bool() -> None:
    with pytest.raises(ConfigValidationError, match="必须是数字"):
        _validate_single_value("x", "float", False, {})


def test_validate_single_value_float_rejects_string() -> None:
    with pytest.raises(ConfigValidationError, match="必须是数字"):
        _validate_single_value("x", "float", "3.14", {})


def test_validate_single_value_str() -> None:
    result = _validate_single_value("x", "str", "hello", {})
    assert result == "hello"


def test_validate_single_value_str_rejects_int() -> None:
    with pytest.raises(ConfigValidationError, match="必须是字符串"):
        _validate_single_value("x", "str", 42, {})


def test_validate_single_value_bool() -> None:
    result = _validate_single_value("x", "bool", True, {})
    assert result is True


def test_validate_single_value_bool_rejects_int() -> None:
    with pytest.raises(ConfigValidationError, match="必须是布尔值"):
        _validate_single_value("x", "bool", 1, {})


def test_validate_single_value_select_valid() -> None:
    result = _validate_single_value("x", "select", "a", {"options": ["a", "b"]})
    assert result == "a"


def test_validate_single_value_select_invalid() -> None:
    with pytest.raises(ConfigValidationError, match="必须是以下选项之一"):
        _validate_single_value("x", "select", "c", {"options": ["a", "b"]})


def test_validate_single_value_file_path_from_path() -> None:
    result = _validate_single_value("x", "file_path", Path("/foo/bar.txt"), {})
    assert result == str(Path("/foo/bar.txt"))


def test_validate_single_value_file_path_from_str() -> None:
    result = _validate_single_value("x", "file_path", "/foo/bar.txt", {})
    assert result == "/foo/bar.txt"


def test_validate_single_value_file_path_rejects_int() -> None:
    with pytest.raises(ConfigValidationError, match="必须是文件路径字符串"):
        _validate_single_value("x", "file_path", 42, {})


def test_validate_single_value_folder_path_from_path() -> None:
    result = _validate_single_value("x", "folder_path", Path("/foo"), {})
    assert result == str(Path("/foo"))


def test_validate_single_value_folder_path_rejects_int() -> None:
    with pytest.raises(ConfigValidationError, match="必须是文件夹路径字符串"):
        _validate_single_value("x", "folder_path", 42, {})


def test_validate_single_value_unsupported_type() -> None:
    with pytest.raises(ConfigValidationError, match="不支持的类型"):
        _validate_single_value("x", "unknown_type", "val", {})


# ---------------------------------------------------------------------------
# _validate_single_value – range checks
# ---------------------------------------------------------------------------


def test_validate_single_value_int_below_min() -> None:
    with pytest.raises(ConfigValidationError, match="不能小于"):
        _validate_single_value("x", "int", 3, {"min": 5})


def test_validate_single_value_int_above_max() -> None:
    with pytest.raises(ConfigValidationError, match="不能大于"):
        _validate_single_value("x", "int", 10, {"max": 8})


def test_validate_single_value_int_within_range() -> None:
    result = _validate_single_value("x", "int", 7, {"min": 5, "max": 10})
    assert result == 7


def test_validate_single_value_float_below_min() -> None:
    with pytest.raises(ConfigValidationError, match="不能小于"):
        _validate_single_value("x", "float", 1.5, {"min": 2.0})


def test_validate_single_value_float_above_max() -> None:
    with pytest.raises(ConfigValidationError, match="不能大于"):
        _validate_single_value("x", "float", 15.0, {"max": 10.0})


# ---------------------------------------------------------------------------
# normalize_config_params
# ---------------------------------------------------------------------------


def test_normalize_params_none_is_empty_dict() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "str", "default": "x"}}}
    result = normalize_config_params(schema, None)
    assert result == {"a": "x"}


def test_normalize_params_not_mapping_raises() -> None:
    schema = {"type": "object", "properties": {}}
    with pytest.raises(ConfigValidationError, match="步骤参数必须是字典"):
        normalize_config_params(schema, "bad")  # type: ignore[arg-type]


def test_normalize_params_invalid_schema_raises() -> None:
    is_valid, errors = validate_config_schema({"type": "array"})
    assert not is_valid
    assert any("type" in e for e in errors)


def test_normalize_params_undeclared_key() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "str"}}}
    with pytest.raises(ConfigValidationError, match="存在未声明参数"):
        normalize_config_params(schema, {"a": "x", "b": "y"})


def test_normalize_params_missing_required() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "str"}},
        "required": ["name"],
    }
    with pytest.raises(ConfigValidationError, match="缺少必填参数"):
        normalize_config_params(schema, {})


def test_normalize_params_optional_without_default_skipped() -> None:
    schema = {"type": "object", "properties": {"opt": {"type": "str"}}}
    result = normalize_config_params(schema, {})
    assert "opt" not in result


def test_normalize_params_default_used() -> None:
    schema = {"type": "object", "properties": {"size": {"type": "int", "default": 100}}}
    result = normalize_config_params(schema, {})
    assert result == {"size": 100}


def test_normalize_params_value_override_default() -> None:
    schema = {"type": "object", "properties": {"size": {"type": "int", "default": 100}}}
    result = normalize_config_params(schema, {"size": 200})
    assert result == {"size": 200}


def test_normalize_params_field_required_from_top_level() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "str"}, "alt": {"type": "str"}},
        "required": ["name"],
    }
    with pytest.raises(ConfigValidationError, match="缺少必填参数"):
        normalize_config_params(schema, {})


def test_normalize_params_field_required_from_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "str", "required": True}},
    }
    with pytest.raises(ConfigValidationError, match="缺少必填参数"):
        normalize_config_params(schema, {})


def test_normalize_params_multiple_errors() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "int", "required": True}, "b": {"type": "int", "required": True}},
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        normalize_config_params(schema, {})
    assert len(exc_info.value.errors) == 2


def test_normalize_params_extra_key_but_no_required_fields() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "str", "default": "x"}}}
    result = normalize_config_params(schema, {})
    assert result == {"a": "x"}


def test_normalize_params_file_path_coerces_string() -> None:
    schema = {"type": "object", "properties": {"f": {"type": "file_path"}}}
    result = normalize_config_params(schema, {"f": "/a/b.txt"})
    assert result["f"] == "/a/b.txt"
