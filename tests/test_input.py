"""Input resolver: classifies CLI inputs into atom × scope × recurse."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import resolve_input
from core.exceptions import PipelineExecutionError


def test_no_input_resolves_to_none(tmp_path: Path) -> None:
    plan = resolve_input()
    assert plan.atom == "none"
    assert plan.files == () and plan.lines == ()


def test_lines_text_resolves_to_line(tmp_path: Path) -> None:
    plan = resolve_input(lines_text="a\n\nb\n c ")
    assert plan.atom == "line"
    assert plan.lines == ("a", "b", "c")


def test_lines_file_resolves_to_line(tmp_path: Path) -> None:
    f = tmp_path / "urls.txt"
    f.write_text("https://a\n\nhttps://b\n", encoding="utf-8")
    plan = resolve_input(lines_file=f)
    assert plan.atom == "line"
    assert plan.lines == ("https://a", "https://b")


def test_files_take_precedence_over_lines(tmp_path: Path) -> None:
    pf = tmp_path / "a.txt"
    pf.write_text("x", encoding="utf-8")
    plan = resolve_input(files=[pf], lines_text="b")
    assert plan.atom == "file"
    assert plan.lines == ()


def test_files_wrong_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(PipelineExecutionError):
        resolve_input(files=[tmp_path / "missing.txt"])


def test_files_only_files_atom_file_default(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("x", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("y", encoding="utf-8")
    plan = resolve_input(files=[a, b])
    assert plan.atom == "file"
    assert not plan.recurse


def test_files_all_dirs_without_recurse_atom_file(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d1.mkdir()
    plan = resolve_input(files=[d1])
    assert plan.atom == "file"
    assert not plan.recurse


def test_files_all_dirs_with_recurse_atom_file(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d1.mkdir()
    (d1 / "x.txt").write_text("x", encoding="utf-8")
    plan = resolve_input(files=[d1], recurse=True)
    assert plan.atom == "file"
    assert plan.recurse


def test_files_mixed_without_recurse_rejected(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(PipelineExecutionError):
        resolve_input(files=[d, f], recurse=False)


def test_lines_empty_lines_resolves_to_none() -> None:
    plan = resolve_input(lines_text="\n\n   \n")
    # All lines stripped → empty → atom=none.
    assert plan.atom == "none"
