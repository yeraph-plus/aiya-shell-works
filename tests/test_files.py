"""WorkingCopier: unit building, copy semantics, source_root, direct mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import (
    ExecutionWorkspace,
    InputPlan,
    WorkingCopier,
    build_lines_units,
    build_path_units,
    make_unique_path,
)
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
    assert ctx.current.path == out / "a.txt"
    assert ctx.current.path.exists()
    assert ctx.current.is_file is True
    assert ctx.source_root == src_root


def test_copier_direct_mode_no_copy(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out, direct_mode=True)
    ctx = copier.prepare_path_unit({"path": src, "source_root": None})
    assert ctx.current.path == src
    # No copy: original is the working path.
    assert not (out / "a.txt").exists()


def test_copier_none_unit_uses_output_dir_as_working(tmp_path: Path) -> None:
    out = tmp_path / "out"
    copier = WorkingCopier(out)
    ctx = copier.prepare_none(shared={"k": "v"})
    assert ctx.current.is_dir is True
    assert ctx.current.path == out and ctx.shared == {"k": "v"}


def test_copier_line_unit_injects_input_line(tmp_path: Path) -> None:
    out = tmp_path / "out"
    copier = WorkingCopier(out)
    ctx = copier.prepare_line({"line": "hello"})
    assert ctx.shared["input_line"] == "hello"
    assert ctx.shared["input_lines"] == ["hello"]
    assert ctx.current.path == out


def test_copier_line_batch_injects_input_lines(tmp_path: Path) -> None:
    out = tmp_path / "out"
    copier = WorkingCopier(out)
    ctx = copier.prepare_line({"lines": ["hello", "world"]})
    assert "input_line" not in ctx.shared
    assert ctx.shared["input_lines"] == ["hello", "world"]
    assert ctx.current.path == out


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
    assert " (1)" in ctx2.current.name


def test_workspace_file_collision_preserves_existing_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "result.txt"
    existing.write_text("old", encoding="utf-8")

    ctx = WorkingCopier(out).prepare_none()
    created = ctx.create_file("result.txt", "new")

    assert created.path == out / "result (1).txt"
    assert existing.read_text(encoding="utf-8") == "old"
    assert created.read_text(encoding="utf-8") == "new"
    assert [entry.name for entry in ctx.files()] == ["result (1).txt"]


def test_workspace_directory_collision_renames_whole_top_level(tmp_path: Path) -> None:
    out = tmp_path / "out"
    existing = out / "bundle"
    existing.mkdir(parents=True)
    (existing / "old.txt").write_text("old", encoding="utf-8")

    ctx = WorkingCopier(out).prepare_none()
    created = ctx.create_directory("bundle")
    nested = ctx.create_file(created.path / "new.txt", "new")

    assert created.path == out / "bundle (1)"
    assert nested.path == out / "bundle (1)" / "new.txt"
    assert (existing / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (existing / "new.txt").exists()


def test_workspace_manifest_refreshes_external_tool_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.txt").write_text("unrelated", encoding="utf-8")
    ctx = WorkingCopier(out).prepare_none()

    reserved = ctx.allocate_file("external/tool-result.txt")
    assert reserved.path.parent.is_dir()
    reserved.path.write_text("generated", encoding="utf-8")
    derived = out / "derived.txt"
    derived.write_text("derived", encoding="utf-8")
    ctx.adopt(derived)
    ctx.refresh()

    assert reserved.read_text(encoding="utf-8") == "generated"
    assert [entry.name for entry in ctx.files()] == ["derived.txt", "tool-result.txt"]


def test_workspace_rejects_path_escape_and_invalid_rename(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = WorkingCopier(out).prepare_none()
    resource = ctx.create_file("safe.txt", "safe")

    with pytest.raises(FileHandlingError, match="路径越界"):
        ctx.create_file("../escape.txt", "bad")
    with pytest.raises(FileHandlingError, match="非法文件名"):
        resource.rename("../renamed.txt")
    assert not (tmp_path / "escape.txt").exists()


def test_workspace_discard_removes_only_current_unit_entries(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    keep = out / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    workspace = ExecutionWorkspace(out)
    unit = workspace.create_unit(1)
    plan = InputPlan(kind="path", recurse=False, files=(source,), lines=())
    ctx = workspace.prepare_unit(
        1,
        {"path": source, "source_root": None},
        plan,
        unit_workspace=unit,
    )
    generated = ctx.create_file("generated.txt", "generated")
    copied = ctx.current.path

    workspace.discard(unit)

    assert keep.read_text(encoding="utf-8") == "keep"
    assert not copied.exists()
    assert not generated.path.exists()


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
    assert ctx.current.path == out
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
    assert ctx.current.path == out


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
    assert ctx.current.path == out / "_batch_0001"
    assert (ctx.current.path / "a.txt").exists()
    assert (ctx.current.path / "b.txt").exists()


def test_batched_path_unit_direct_mode_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    copier = WorkingCopier(out, direct_mode=True)
    with pytest.raises(FileHandlingError):
        copier.prepare_batched_path_unit([{"path": src, "source_root": None}], batch_index=1, shared={})


def test_copier_folder_unit_is_dir(tmp_path: Path) -> None:
    """recurse=False directory unit exposes a directory resource."""

    out = tmp_path / "out"
    src = tmp_path / "d"
    src.mkdir()
    (src / "f.txt").write_text("y", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_path_unit({"path": src, "source_root": None})
    assert ctx.current.is_dir is True
    assert (out / "d").exists()
