"""WorkingCopier: unit building, copy semantics, source_root, direct mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import WorkingCopier, build_lines_units, build_path_units, make_unique_path
from core.exceptions import FileHandlingError

# ---------------------------------------------------------------------------
# build_path_units / build_lines_units
# ---------------------------------------------------------------------------


def test_build_path_units_file_only(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    units = build_path_units([f], recurse=False)
    assert len(units) == 1
    assert units[0]["path"] == f and units[0]["source_root"] is None


def test_build_path_units_dir_with_recurse_keeps_source_root(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    (d / "b.txt").write_text("y", encoding="utf-8")
    units = build_path_units([d], recurse=True)
    # Each file becomes its own unit, source_root = the original dir.
    assert len(units) == 2
    assert all(u["source_root"] == d for u in units)


def test_build_path_units_dir_without_recurse_is_one_folder_unit(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "x").write_text("y", encoding="utf-8")
    units = build_path_units([d], recurse=False)
    assert len(units) == 1
    assert units[0]["path"] == d
    assert units[0]["source_root"] is None


def test_build_path_units_unsupported_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileHandlingError):
        build_path_units([tmp_path / "nope"], recurse=True)


def test_build_lines_units_strips_per_line() -> None:
    units = build_lines_units(["a", "b", "c"])
    assert [u["line"] for u in units] == ["a", "b", "c"]


def test_build_lines_units_batches_when_batch_size_gt_one() -> None:
    units = build_lines_units(["a", "b", "c"], batch_size=2)
    assert units == [{"lines": ["a", "b"]}, {"lines": ["c"]}]


# ---------------------------------------------------------------------------
# WorkingCopier basics
# ---------------------------------------------------------------------------


def test_copier_creates_output_dir_even_in_direct_mode(tmp_path: Path) -> None:
    out = tmp_path / "out"
    WorkingCopier(out, direct_mode=True)
    assert out.exists() and out.is_dir()


def test_copier_default_mode_copies_file_with_source_root(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src_root = tmp_path / "data"
    src_root.mkdir()
    f = src_root / "a.txt"
    f.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_path_unit(
        {"path": f, "source_root": src_root},
    )
    assert ctx.working_path == out / "a.txt"
    assert ctx.working_path.exists()
    assert ctx.is_file is True
    assert ctx.source_root == src_root


def test_copier_direct_mode_no_copy(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out, direct_mode=True)
    ctx = copier.prepare_path_unit({"path": src, "source_root": None})
    assert ctx.working_path == src
    # No copy: original is the working path.
    assert not (out / "a.txt").exists()


def test_copier_none_unit_uses_output_dir_as_working(tmp_path: Path) -> None:
    out = tmp_path / "out"
    copier = WorkingCopier(out)
    ctx = copier.prepare_none(shared={"k": "v"})
    assert ctx.is_dir is True
    assert ctx.working_path == out and ctx.shared == {"k": "v"}


def test_copier_line_unit_injects_input_line(tmp_path: Path) -> None:
    out = tmp_path / "out"
    copier = WorkingCopier(out)
    ctx = copier.prepare_line({"line": "hello"})
    assert ctx.shared["input_line"] == "hello"
    assert ctx.shared["input_lines"] == ["hello"]
    assert ctx.working_path == out


def test_copier_line_batch_injects_input_lines(tmp_path: Path) -> None:
    out = tmp_path / "out"
    copier = WorkingCopier(out)
    ctx = copier.prepare_line({"lines": ["hello", "world"]})
    assert "input_line" not in ctx.shared
    assert ctx.shared["input_lines"] == ["hello", "world"]
    assert ctx.working_path == out


def test_copier_make_unique_path_de_dups(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    unique = make_unique_path(p)
    assert unique != p
    assert unique.stem.startswith("a")  # contains original stem (e.g. "a (1)")
    # If target does not exist → returned untouched.
    fresh = tmp_path / "fresh.txt"
    assert make_unique_path(fresh) == fresh


def test_copier_copy_collision_parenthetical(tmp_path: Path) -> None:
    """Existing file must be copied as ``stem (1).ext``."""

    out = tmp_path / "out"
    src_root = tmp_path / "data"
    src_root.mkdir()
    src_file = src_root / "a.txt"
    src_file.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out)
    copier.prepare_path_unit({"path": src_file, "source_root": src_root})
    # Make a second copy — must be unique:
    ctx2 = copier.prepare_path_unit({"path": src_file, "source_root": src_root})
    assert " (1)" in ctx2.working_path.name


# ---------------------------------------------------------------------------
# scope=shared
# ---------------------------------------------------------------------------


def test_shared_merges_multiple_files_into_outdir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    d1 = tmp_path / "d1"
    d1.mkdir()
    f1 = d1 / "a.txt"
    f1.write_text("x1", encoding="utf-8")
    f2 = d1 / "b.txt"
    f2.write_text("x2", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_shared_path_unit([f1, f2], recurse=False, shared={})
    assert ctx.working_path == out
    assert (out / "a.txt").exists()
    assert (out / "b.txt").exists()


def test_shared_merges_dir_into_subdir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "pics"
    src.mkdir()
    (src / "1.jpg").write_text("j", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_shared_path_unit([src], recurse=False, shared={})
    assert (out / "pics" / "1.jpg").exists()
    assert ctx.working_path == out


def test_shared_direct_mode_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out, direct_mode=True)
    with pytest.raises(FileHandlingError):
        copier.prepare_shared_path_unit([src], recurse=False, shared={})


def test_batched_path_unit_merges_into_isolated_batch_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "src"
    src.mkdir()
    a = src / "a.txt"
    a.write_text("a", encoding="utf-8")
    b = src / "b.txt"
    b.write_text("b", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_batched_path_unit(
        [
            {"path": a, "source_root": src},
            {"path": b, "source_root": src},
        ],
        batch_index=1,
        shared={},
    )
    assert ctx.working_path == out / "_batch_0001"
    assert (ctx.working_path / "a.txt").exists()
    assert (ctx.working_path / "b.txt").exists()
    assert ctx.output_dir == out


def test_batched_path_unit_direct_mode_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out, direct_mode=True)
    with pytest.raises(FileHandlingError):
        copier.prepare_batched_path_unit([{"path": src, "source_root": None}], batch_index=1, shared={})


def test_copier_folder_unit_is_dir(tmp_path: Path) -> None:
    """recurse=False directory unit → working_path is a directory."""

    out = tmp_path / "out"
    src = tmp_path / "d"
    src.mkdir()
    (src / "f.txt").write_text("y", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_path_unit({"path": src, "source_root": None})
    assert ctx.is_dir is True
    assert (out / "d").exists()
