"""config_schema: parameter validation for the 8 supported types.

Copied/derived from the legacy behavior; the validator is intentionally
unchanged.
"""

from __future__ import annotations

import pytest

from core import (
    ConfigSchemaValidationError, ConfigValidationError,
    normalize_config_params, validate_config_schema,
)


def _schema(properties: dict, required: list = None) -> dict:
    return {"type": "object",
            "properties": properties,
            "required": required or []}


def test_validate_config_schema_rejects_non_dict() -> None:
    valid, errs = validate_config_schema([])
    assert not valid and errs


def test_validate_config_schema_requires_object_type() -> None:
    valid, errs = validate_config_schema({"type": "wrong"})
    assert not valid
    assert any("type" in e for e in errs)


def test_validate_config_schema_rejects_unknown_type_property() -> None:
    s = _schema({"x": {"type": "bogus"}})
    valid, errs = validate_config_schema(s)
    assert not valid


def test_validate_config_schema_min_max_for_numbers_only() -> None:
    s = _schema({"x": {"type": "str", "min": 1}})
    assert not validate_config_schema(s)[0]


def test_normalize_int_validates_and_passes() -> None:
    s = _schema({"x": {"type": "int", "min": 0, "max": 10}})
    assert normalize_config_params(s, {"x": 5}) == {"x": 5}


def test_normalize_int_rejects_out_of_range() -> None:
    s = _schema({"x": {"type": "int", "min": 0, "max": 10}})
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": 11})


def test_normalize_int_rejects_non_int() -> None:
    s = _schema({"x": {"type": "int"}})
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": 1.5})


def test_normalize_float_coerces_int_to_float() -> None:
    s = _schema({"x": {"type": "float"}})
    assert normalize_config_params(s, {"x": 2}) == {"x": 2.0}


def test_normalize_str_validates() -> None:
    s = _schema({"x": {"type": "str", "default": "hi"}})
    assert normalize_config_params(s, {}) == {"x": "hi"}
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": 1})


def test_normalize_bool_validates() -> None:
    s = _schema({"x": {"type": "bool"}})
    assert normalize_config_params(s, {"x": True}) == {"x": True}
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": "true"})


def test_normalize_radio_validates_options() -> None:
    s = _schema({"x": {"type": "radio", "options": ["a", "b"]}})
    assert normalize_config_params(s, {"x": "a"}) == {"x": "a"}
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": "c"})


def test_normalize_select_validates_options() -> None:
    s = _schema({"x": {"type": "select", "options": ["a", "b"]}})
    assert normalize_config_params(s, {"x": "b"}) == {"x": "b"}
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": "z"})


def test_normalize_file_path_accepts_str_or_path() -> None:
    from pathlib import Path
    s = _schema({"x": {"type": "file_path"}})
    assert normalize_config_params(s, {"x": "a/b.txt"}) == {"x": "a/b.txt"}
    # Path normalization (a/b → a\b on win32) is acceptable; only structure matters.
    assert normalize_config_params(s, {"x": Path("a/b.txt")}) == {"x": str(Path("a/b.txt"))}
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": 1})


def test_normalize_folder_path_rejects_non_str() -> None:
    s = _schema({"x": {"type": "folder_path"}})
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": 1})


def test_normalize_undeclared_param_rejects() -> None:
    s = _schema({"x": {"type": "str"}})
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {"x": "ok", "extra": "no"})


def test_normalize_required_missing_raises() -> None:
    s = _schema({"x": {"type": "str"}}, required=["x"])
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {})


def test_normalize_required_at_field_level_raises() -> None:
    s = _schema({"x": {"type": "str", "required": True}})
    with pytest.raises(ConfigValidationError):
        normalize_config_params(s, {})


def test_normalize_accepts_none_params() -> None:
    s = _schema({"x": {"type": "str", "default": "ok"}})
    assert normalize_config_params(s, None) == {"x": "ok"}


def test_config_schema_invalid_required_referencing_missing_field() -> None:
    s = _schema({"x": {"type": "str"}}, required=["does-not-exist"])
    valid, errs = validate_config_schema(s)
    assert not valid


def test_config_schema_default_value_validated() -> None:
    s = _schema({"x": {"type": "int", "default": "not-an-int"}})
    valid, errs = validate_config_schema(s)
    assert not valid and any("default" in e for e in errs)