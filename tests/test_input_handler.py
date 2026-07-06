"""Tests for InputHandler: text-input mode unit building and context prep."""

from __future__ import annotations

from pathlib import Path

from core import InputHandler, PipelineContext


def test_build_units_creates_line_dicts() -> None:
    units = InputHandler.build_units(["a", "b", "c"])
    assert units == [{"line": "a"}, {"line": "b"}, {"line": "c"}]


def test_build_units_empty_lines() -> None:
    units = InputHandler.build_units([])
    assert units == []


def test_build_units_single_line() -> None:
    units = InputHandler.build_units(["only"])
    assert units == [{"line": "only"}]


def test_prepare_context_sets_input_line_in_shared(tmp_path: Path) -> None:
    ctx = InputHandler.prepare_context("hello", tmp_path)
    assert ctx.mode == "input"
    assert ctx.original_input is None
    assert ctx.working_path == tmp_path
    assert ctx.output_dir == tmp_path
    assert ctx.shared == {"input_line": "hello"}


def test_prepare_context_merges_existing_shared(tmp_path: Path) -> None:
    ctx = InputHandler.prepare_context(
        "world",
        tmp_path,
        shared={"key": "val", "input_line": "should-be-overwritten"},
    )
    assert ctx.shared == {"key": "val", "input_line": "world"}


def test_prepare_context_shared_defaults_to_none(tmp_path: Path) -> None:
    ctx = InputHandler.prepare_context("test", tmp_path)
    assert ctx.shared == {"input_line": "test"}


def test_prepare_context_output_dir_from_string(tmp_path: Path) -> None:
    ctx = InputHandler.prepare_context("line", str(tmp_path))
    assert ctx.working_path == tmp_path
    assert ctx.output_dir == tmp_path
