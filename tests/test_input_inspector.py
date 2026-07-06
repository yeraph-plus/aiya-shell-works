"""Tests for InputInspector: path validation utilities."""

from __future__ import annotations

from pathlib import Path

from core import InputInspector, ValidationResult


# ---------------------------------------------------------------------------
# validate_file
# ---------------------------------------------------------------------------


def test_validate_file_exists(tmp_path: Path) -> None:
    f = tmp_path / "good.txt"
    f.write_text("data", encoding="utf-8")
    result = InputInspector.validate_file(f)
    assert result.is_valid is True
    assert result.path == f
    assert result.error == ""


def test_validate_file_not_exists(tmp_path: Path) -> None:
    p = tmp_path / "missing.txt"
    result = InputInspector.validate_file(p)
    assert result.is_valid is False
    assert "文件不存在" in result.error
    assert result.path == p


def test_validate_file_is_directory(tmp_path: Path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    result = InputInspector.validate_file(d)
    assert result.is_valid is False
    assert "不是文件" in result.error


def test_validate_file_accepts_string(tmp_path: Path) -> None:
    f = tmp_path / "good.txt"
    f.write_text("data", encoding="utf-8")
    result = InputInspector.validate_file(str(f))
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# validate_directory
# ---------------------------------------------------------------------------


def test_validate_directory_exists(tmp_path: Path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    result = InputInspector.validate_directory(d)
    assert result.is_valid is True
    assert result.path == d


def test_validate_directory_not_exists(tmp_path: Path) -> None:
    d = tmp_path / "missing"
    result = InputInspector.validate_directory(d)
    assert result.is_valid is False
    assert "目录不存在" in result.error


def test_validate_directory_is_file(tmp_path: Path) -> None:
    f = tmp_path / "afile.txt"
    f.write_text("data", encoding="utf-8")
    result = InputInspector.validate_directory(f)
    assert result.is_valid is False
    assert "不是目录" in result.error


def test_validate_directory_accepts_string(tmp_path: Path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    result = InputInspector.validate_directory(str(d))
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# validate_file_input (with directory expansion)
# ---------------------------------------------------------------------------


def test_validate_file_input_single_file(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("data", encoding="utf-8")
    valid, invalid = InputInspector.validate_file_input([f])
    assert valid == [f]
    assert invalid == []


def test_validate_file_input_directory_expands(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a.txt").write_text("a", encoding="utf-8")
    (d / "b.txt").write_text("b", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "c.txt").write_text("c", encoding="utf-8")

    valid, invalid = InputInspector.validate_file_input([d])
    assert invalid == []
    assert len(valid) == 3
    assert all(f.is_file() for f in valid)


def test_validate_file_input_not_exists(tmp_path: Path) -> None:
    p = tmp_path / "missing"
    valid, invalid = InputInspector.validate_file_input([p])
    assert valid == []
    assert len(invalid) == 1
    assert "路径不存在" in invalid[0].error


def test_validate_file_input_neither_file_nor_dir(tmp_path: Path) -> None:
    valid, invalid = InputInspector.validate_file_input([Path("/dev/null")])
    assert valid == []
    assert len(invalid) == 1
    # /dev/null does not exist on Windows, skip assertion


def test_validate_file_input_empty_list() -> None:
    valid, invalid = InputInspector.validate_file_input([])
    assert valid == []
    assert invalid == []


def test_validate_file_input_mixed_valid_invalid(tmp_path: Path) -> None:
    f = tmp_path / "good.txt"
    f.write_text("data", encoding="utf-8")
    missing = tmp_path / "bad.txt"

    valid, invalid = InputInspector.validate_file_input([f, missing])
    assert valid == [f]
    assert len(invalid) == 1
    assert invalid[0].path == missing


# ---------------------------------------------------------------------------
# validate_path_input (no expansion)
# ---------------------------------------------------------------------------


def test_validate_path_input_file(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("data", encoding="utf-8")
    valid, invalid = InputInspector.validate_path_input([f])
    assert valid == [f]
    assert invalid == []


def test_validate_path_input_directory_kept_as_is(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    (d / "f.txt").write_text("data", encoding="utf-8")
    valid, invalid = InputInspector.validate_path_input([d])
    assert valid == [d]
    assert invalid == []


def test_validate_path_input_not_exists(tmp_path: Path) -> None:
    p = tmp_path / "missing"
    valid, invalid = InputInspector.validate_path_input([p])
    assert valid == []
    assert len(invalid) == 1
    assert "路径不存在" in invalid[0].error


def test_validate_path_input_neither_file_nor_dir(tmp_path: Path) -> None:
    valid, invalid = InputInspector.validate_path_input([Path("/dev/null")])
    assert valid == []
    assert len(invalid) == 1
    # /dev/null does not exist on Windows, skip assertion


def test_validate_path_input_empty_list() -> None:
    valid, invalid = InputInspector.validate_path_input([])
    assert valid == []
    assert invalid == []


def test_validate_path_input_mixed(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("data", encoding="utf-8")
    missing = tmp_path / "bad.txt"

    valid, invalid = InputInspector.validate_path_input([f, missing])
    assert valid == [f]
    assert len(invalid) == 1


# ---------------------------------------------------------------------------
# validate_folder_input
# ---------------------------------------------------------------------------


def test_validate_folder_input_valid(tmp_path: Path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    result = InputInspector.validate_folder_input(d)
    assert result.is_valid is True


def test_validate_folder_input_file(tmp_path: Path) -> None:
    f = tmp_path / "afile.txt"
    f.write_text("data", encoding="utf-8")
    result = InputInspector.validate_folder_input(f)
    assert result.is_valid is False
    assert "不是目录" in result.error


# ---------------------------------------------------------------------------
# validate_text_input
# ---------------------------------------------------------------------------


def test_validate_text_input_normal() -> None:
    lines = InputInspector.validate_text_input("line1\nline2\nline3")
    assert lines == ["line1", "line2", "line3"]


def test_validate_text_input_skips_blank_lines() -> None:
    lines = InputInspector.validate_text_input("line1\n\n  \nline2\n")
    assert lines == ["line1", "line2"]


def test_validate_text_input_empty_string() -> None:
    lines = InputInspector.validate_text_input("")
    assert lines == []


def test_validate_text_input_whitespace_only() -> None:
    lines = InputInspector.validate_text_input("  \n\t\n  ")
    assert lines == []


def test_validate_text_input_strips_whitespace() -> None:
    lines = InputInspector.validate_text_input("  hello  \n  world  ")
    assert lines == ["hello", "world"]
