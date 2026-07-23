"""Execution workspace: unit building, ownership, copy and direct modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import PipelineContext
from core.exceptions import FileHandlingError
from core.files import ExecutionWorkspace, build_lines_units, build_path_units, make_unique_path
from core.planning import ExecutionUnit, PathInput


class WorkingCopier:
    """Test-only context adapter around the low-level workspace contract."""

    def __init__(self, output_dir: Path, *, direct_mode: bool = False, move_mode: bool = False) -> None:
        self.workspace = ExecutionWorkspace(output_dir)
        self.direct_mode = direct_mode
        self.move_mode = move_mode

    @staticmethod
    def _context(prepared, shared=None) -> PipelineContext:
        payload = dict(shared or {})
        if prepared.input_lines:
            payload["input_lines"] = list(prepared.input_lines)
            if len(prepared.input_lines) == 1:
                payload["input_line"] = prepared.input_lines[0]
        return PipelineContext(
            workspace=prepared.workspace,
            original_input=prepared.original_input,
            source_root=prepared.source_root,
            shared=payload,
        )

    def prepare_none(self, *, shared=None) -> PipelineContext:
        return self._context(self.workspace.prepare_unit(1, ExecutionUnit(kind="none")), shared)

    def prepare_line(self, unit, *, shared=None) -> PipelineContext:
        lines = tuple(unit.get("lines") or [unit.get("line", "")])
        execution_unit = ExecutionUnit(kind="line", lines=lines)
        return self._context(self.workspace.prepare_unit(1, execution_unit), shared)

    def prepare_path_unit(self, unit, *, shared=None) -> PipelineContext:
        execution_unit = ExecutionUnit(
            kind="path",
            paths=(PathInput(Path(unit["path"]), unit.get("source_root")),),
        )
        prepared = self.workspace.prepare_unit(
            1,
            execution_unit,
            direct_mode=self.direct_mode,
            move_mode=self.move_mode,
        )
        return self._context(prepared, shared)

    def prepare_shared_path_unit(self, paths, *, recurse, shared=None) -> PipelineContext:
        execution_unit = ExecutionUnit(
            kind="path",
            layout="shared",
            paths=tuple(PathInput(Path(path)) for path in paths),
        )
        prepared = self.workspace.prepare_unit(
            1,
            execution_unit,
            direct_mode=self.direct_mode,
            move_mode=self.move_mode,
        )
        return self._context(prepared, shared)

    def prepare_batched_path_unit(self, units, *, batch_index, shared=None) -> PipelineContext:
        execution_unit = ExecutionUnit(
            kind="path",
            layout="batch",
            paths=tuple(PathInput(Path(item["path"]), item.get("source_root")) for item in units),
        )
        prepared = self.workspace.prepare_unit(
            batch_index,
            execution_unit,
            direct_mode=self.direct_mode,
            move_mode=self.move_mode,
        )
        return self._context(prepared, shared)

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
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(kind="path", paths=(PathInput(source),)),
        unit_workspace=unit,
    ))
    generated = ctx.create_file("generated.txt", "generated")
    copied = ctx.current.path

    workspace.discard(unit)

    assert keep.read_text(encoding="utf-8") == "keep"
    assert not copied.exists()
    assert not generated.path.exists()


def test_workspace_rejects_adopting_preexisting_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "existing.txt"
    existing.write_text("keep", encoding="utf-8")
    copier = WorkingCopier(out)
    ctx = copier.prepare_none()

    with pytest.raises(FileHandlingError, match="执行前已存在"):
        ctx.adopt(existing)

    copier.workspace.discard(ctx.workspace)
    assert existing.read_text(encoding="utf-8") == "keep"


def test_workspace_rejects_other_units_published_output(tmp_path: Path) -> None:
    copier = WorkingCopier(tmp_path / "out")
    first = copier.prepare_none()
    artifact = first.create_file("artifact.txt", "first")
    copier.workspace.publish(first.workspace)
    second = WorkingCopier._context(
        copier.workspace.prepare_unit(
            2,
            ExecutionUnit(kind="none"),
        )
    )

    with pytest.raises(FileHandlingError, match="其他处理单元"):
        second.adopt(artifact.path)
    assert artifact.read_text(encoding="utf-8") == "first"


def test_workspace_current_handle_tracks_rename(tmp_path: Path) -> None:
    ctx = WorkingCopier(tmp_path / "out").prepare_none()
    original = ctx.create_file("before.txt", "value")
    ctx.set_current(original.path)

    renamed = original.rename("after.txt")

    assert original is renamed
    assert original.path.name == "after.txt"
    assert ctx.current is original


def test_workspace_delete_current_falls_back_to_root(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = WorkingCopier(out).prepare_none()
    current = ctx.create_file("current.txt", "value")
    ctx.set_current(current.path)

    current.delete()

    assert ctx.current.path == out.resolve()
    assert ctx.current.is_dir


def test_workspace_rejects_untracked_current_and_read(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = WorkingCopier(out).prepare_none()
    untracked = out / "untracked.txt"
    untracked.write_text("private", encoding="utf-8")

    with pytest.raises(FileHandlingError, match="未登记"):
        ctx.set_current(untracked)
    with pytest.raises(FileHandlingError, match="未登记"):
        ctx.read_text(untracked, encoding="utf-8")


def test_reference_unit_reads_source_without_copy_and_rejects_mutation(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    workspace = ExecutionWorkspace(out)
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(kind="path", paths=(PathInput(source),)),
        reference_mode=True,
    ))

    assert ctx.current.path == source
    assert ctx.current.read_text(encoding="utf-8") == "source"
    assert not (out / "source.txt").exists()
    with pytest.raises(FileHandlingError, match="只读引用"):
        ctx.current.write_text("changed", encoding="utf-8")
    assert source.read_text(encoding="utf-8") == "source"


def test_referenced_file_does_not_expose_sibling_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source = tmp_path / "source.txt"
    sibling = tmp_path / "sibling.txt"
    source.write_text("source", encoding="utf-8")
    sibling.write_text("sibling", encoding="utf-8")
    workspace = ExecutionWorkspace(out)
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(kind="path", paths=(PathInput(source),)),
        reference_mode=True,
    ))

    with pytest.raises(FileHandlingError, match="路径越界"):
        ctx.read_text(sibling, encoding="utf-8")


def test_referenced_directory_exposes_descendants(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source = tmp_path / "source"
    source.mkdir()
    child = source / "child.txt"
    child.write_text("child", encoding="utf-8")
    workspace = ExecutionWorkspace(out)
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(kind="path", paths=(PathInput(source),)),
        reference_mode=True,
    ))

    assert ctx.read_text(child, encoding="utf-8") == "child"


def test_direct_file_does_not_expose_sibling_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source = tmp_path / "source.txt"
    sibling = tmp_path / "sibling.txt"
    source.write_text("source", encoding="utf-8")
    sibling.write_text("sibling", encoding="utf-8")
    workspace = ExecutionWorkspace(out)
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(kind="path", paths=(PathInput(source),)),
        direct_mode=True,
    ))

    with pytest.raises(FileHandlingError, match="路径越界"):
        ctx.read_text(sibling, encoding="utf-8")


def test_set_current_selects_existing_tracked_resource(tmp_path: Path) -> None:
    workspace = ExecutionWorkspace(tmp_path / "out")
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(kind="none"),
    ))
    created = ctx.create_file("created.txt", "created")

    selected = ctx.set_current(created.path)

    assert selected.path == created.path
    assert ctx.current.path == created.path


def test_shared_reference_assigns_unique_logical_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "same.txt"
    second = second_dir / "same.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    workspace = ExecutionWorkspace(out)
    ctx = WorkingCopier._context(workspace.prepare_unit(
        1,
        ExecutionUnit(
            kind="path",
            layout="shared",
            paths=(PathInput(first), PathInput(second)),
        ),
        reference_mode=True,
    ))

    assert ctx.current.path == out
    assert [str(entry.relative_path) for entry in ctx.files()] == ["same (1).txt", "same.txt"]
    assert list(out.iterdir()) == []


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


def test_context_workspace_surface_and_stable_handles(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = WorkingCopier(out).prepare_none()

    assert ctx.path() == out.resolve()
    directory = ctx.create_directory("bundle")
    text = ctx.create_file(directory.path / "text.txt", "alpha", encoding="utf-8")
    binary = ctx.create_file(directory.path / "data.bin", b"\x00\x01")
    assert str(text) == str(text.path)
    assert Path(text) == text.path
    assert ctx.file(text.path) is text
    assert text.relative_path == Path("bundle/text.txt")
    assert text.read_text(encoding="utf-8") == "alpha"
    assert binary.read_bytes() == b"\x00\x01"

    text.write_text("beta", encoding="utf-8")
    binary.write_bytes(b"\x02")
    assert ctx.read_text(text.path, encoding="utf-8") == "beta"
    assert ctx.read_bytes(binary.path) == b"\x02"
    ctx.write_text(text.path, "gamma", encoding="utf-8")
    ctx.write_bytes(binary.path, b"\x03")

    copied = text.copy_to("copy.txt")
    moved = ctx.move(copied.path, "moved.txt")
    assert ctx.move(moved.path, moved.path) is moved
    renamed = ctx.rename(moved.path, "renamed.txt")
    assert renamed.read_text(encoding="utf-8") == "gamma"
    directory_copy = ctx.copy(directory.path, "bundle-copy")
    assert directory_copy.is_dir

    ctx.set_current(directory.path)
    assert {entry.name for entry in ctx.entries(recursive=False)} == {"data.bin", "text.txt"}
    assert {entry.name for entry in ctx.files(recursive=False)} == {"data.bin", "text.txt"}
    assert ctx.directories(recursive=True)
    ctx.refresh()
    ctx.publish()

    ctx.delete(directory_copy.path)
    renamed.delete()
    assert not directory_copy.path.exists()
    assert not renamed.path.exists()


def test_workspace_rejects_missing_and_unallocated_adoptions(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = WorkingCopier(out).prepare_none()

    with pytest.raises(FileHandlingError, match="产物路径不存在"):
        ctx.adopt(out / "missing.txt")

    existing_directory = out / "existing"
    existing_directory.mkdir()
    child = existing_directory / "child.txt"
    child.write_text("child", encoding="utf-8")
    with pytest.raises(FileHandlingError, match="未分配路径"):
        ctx.adopt(child)


def test_workspace_write_collision_preserves_existing_top_level(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "value.txt"
    existing.write_text("existing", encoding="utf-8")
    ctx = WorkingCopier(out).prepare_none()

    written = ctx.write_text("value.txt", "new", encoding="utf-8")

    assert written.path == out / "value (1).txt"
    assert existing.read_text(encoding="utf-8") == "existing"


def test_workspace_missing_current_and_source_are_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = WorkingCopier(out).prepare_none()

    with pytest.raises(FileHandlingError, match="当前资源不存在"):
        ctx.set_current(out / "missing.txt")
    with pytest.raises(FileHandlingError, match="源路径不存在"):
        ctx.copy(out / "missing.txt", "copy.txt")
    with pytest.raises(FileHandlingError, match="源路径不存在"):
        ctx.move(out / "missing.txt", "moved.txt")
