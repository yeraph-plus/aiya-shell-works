"""Tests for pipeline context and file handling helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import FileHandler, FileHandlingError, InputInspector, PipelineContext, PipelineEventBus


def test_prepare_none_context_uses_output_dir(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")

    ctx = handler.prepare_none_context(shared={"job": "demo"})

    assert isinstance(ctx, PipelineContext)
    assert ctx.original_input is None
    assert ctx.mode == "none"
    assert ctx.working_path == handler.output_dir
    assert ctx.shared == {"job": "demo"}


def test_prepare_file_keeps_relative_path_copy_mode(tmp_path: Path) -> None:
    source_root = tmp_path / "inputs"
    nested_file = source_root / "nested" / "demo.txt"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("payload", encoding="utf-8")

    handler = FileHandler(tmp_path / "output")
    ctx = handler._prepare_file_unit(
        {"path": nested_file, "source_root": source_root},
        shared={"step": 1},
    )

    expected_copy = handler.output_dir / "nested" / "demo.txt"
    assert ctx.mode == "file"
    assert ctx.original_input == nested_file
    assert ctx.working_path == expected_copy
    assert ctx.source_root == source_root
    assert ctx.shared == {"step": 1}
    assert expected_copy.read_text(encoding="utf-8") == "payload"


def test_prepare_file_direct_mode_uses_original_as_working_path(tmp_path: Path) -> None:
    source = tmp_path / "demo.txt"
    source.write_text("direct-content", encoding="utf-8")

    handler = FileHandler(tmp_path / "output", direct_mode=True)
    ctx = handler._prepare_file_unit(
        {"path": source, "source_root": None},
        shared={"step": 1},
    )

    assert ctx.mode == "file"
    assert ctx.original_input == source
    assert ctx.working_path == source
    assert ctx.shared == {"step": 1}
    assert source.exists()
    assert source.read_text(encoding="utf-8") == "direct-content"


def test_prepare_file_renames_duplicate_destination(tmp_path: Path) -> None:
    source_a = tmp_path / "source-a.txt"
    source_a.write_text("one", encoding="utf-8")

    handler = FileHandler(tmp_path / "output")
    first = handler._prepare_file_unit({"path": source_a, "source_root": None})
    source_a.write_text("three", encoding="utf-8")

    second = handler._prepare_file_unit({"path": source_a, "source_root": None})

    assert first.working_path.name == "source-a.txt"
    assert second.working_path.name == "source-a (1).txt"
    assert second.working_path.read_text(encoding="utf-8") == "three"


def test_prepare_folder_renames_duplicate_directory(tmp_path: Path) -> None:
    source = tmp_path / "batch"
    (source / "a").mkdir(parents=True)
    (source / "a" / "file.txt").write_text("demo", encoding="utf-8")

    handler = FileHandler(tmp_path / "output")
    first = handler._prepare_folder_unit({"path": source})
    second = handler._prepare_folder_unit({"path": source})

    assert first.working_path.name == "batch"
    assert second.working_path.name == "batch (1)"
    assert (second.working_path / "a" / "file.txt").read_text(encoding="utf-8") == "demo"


def test_prepare_folder_direct_mode_uses_original(tmp_path: Path) -> None:
    source = tmp_path / "myfolder"
    source.mkdir()
    (source / "inner.txt").write_text("inner", encoding="utf-8")

    handler = FileHandler(tmp_path / "output", direct_mode=True)
    ctx = handler._prepare_folder_unit({"path": source})

    assert ctx.mode == "folder"
    assert ctx.working_path == source
    assert ctx.original_input == source
    assert (ctx.working_path / "inner.txt").read_text(encoding="utf-8") == "inner"


def test_finalize_context_always_returns_false(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    ctx = PipelineContext(
        original_input=None,
        working_path=tmp_path / "output",
        output_dir=handler.output_dir,
        mode="none",
    )
    assert handler.finalize_context(ctx, success=True) is False
    assert handler.finalize_context(ctx, success=False) is False


def test_prepare_file_raises_when_source_root_does_not_contain_file(tmp_path: Path) -> None:
    source = tmp_path / "demo.txt"
    source.write_text("demo", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")
    other_root = tmp_path / "other-root"
    other_root.mkdir()

    with pytest.raises(FileHandlingError, match="无法保持相对路径"):
        handler._prepare_file_unit(
            {"path": source, "source_root": other_root},
        )


def test_prepare_file_wraps_permission_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "demo.txt"
    source.write_text("demo", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")

    def raise_permission_error(_: Path, __: Path) -> None:
        raise PermissionError("blocked")

    monkeypatch.setattr("core.handler_file.copy2", raise_permission_error)

    with pytest.raises(FileHandlingError, match="复制文件失败"):
        handler._prepare_file_unit({"path": source, "source_root": None})


def test_build_file_units_expands_folder(tmp_path: Path) -> None:
    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")
    (folder / "b.txt").write_text("b", encoding="utf-8")

    units = FileHandler.build_file_units([folder])
    assert len(units) == 2
    assert units[0]["source_root"] == folder
    assert units[1]["source_root"] == folder


def test_build_file_units_single_file(tmp_path: Path) -> None:
    f = tmp_path / "single.txt"
    f.write_text("solo", encoding="utf-8")

    units = FileHandler.build_file_units([f])
    assert len(units) == 1
    assert units[0]["path"] == f
    assert units[0]["source_root"] is None


def test_build_cycle_units_no_source_root(tmp_path: Path) -> None:
    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")

    units = FileHandler.build_cycle_units([folder])
    assert len(units) == 1
    assert units[0]["source_root"] is None


def test_build_folder_unit(tmp_path: Path) -> None:
    f = tmp_path / "myfolder"
    f.mkdir()
    units = FileHandler.build_folder_unit(f)
    assert len(units) == 1
    assert units[0]["path"] == f


# ---------------------------------------------------------------------------
# PipelineEventBus tests
# ---------------------------------------------------------------------------


def test_event_bus_log_and_query() -> None:
    bus = PipelineEventBus()

    bus.log("mod-a", "message", "step started")
    bus.log("mod-a", "success", "step done", data={"result": "ok"})
    bus.log("mod-b", "error", "step failed")

    assert len(bus) == 3
    assert bus.has_errors() is True

    mod_a_events = bus.query(slug="mod-a")
    assert len(mod_a_events) == 2
    assert mod_a_events[0].text == "step started"
    assert mod_a_events[1].data == {"result": "ok"}

    error_events = bus.query(event_type="error")
    assert len(error_events) == 1
    assert error_events[0].slug == "mod-b"


def test_event_bus_reset_and_iteration() -> None:
    bus = PipelineEventBus()

    bus.log("x", "hint", "note")
    bus.reset()
    assert len(bus) == 0
    assert not bus.has_errors()

    bus.log("y", "warning", "careful")
    collected = list(bus)
    assert len(collected) == 1
    assert collected[0].type == "warning"


def test_event_bus_listener_receives_events_live() -> None:
    bus = PipelineEventBus()
    received = []

    def listener(event) -> None:
        received.append((event.slug, event.text))

    bus.subscribe(listener)
    bus.log("live", "message", "ping")
    bus.unsubscribe(listener)
    bus.log("live", "message", "pong")

    assert received == [("live", "ping")]


def test_event_bus_listener_failure_does_not_break_logging() -> None:
    bus = PipelineEventBus()
    received = []

    def broken_listener(_event) -> None:
        raise RuntimeError("boom")

    def healthy_listener(event) -> None:
        received.append(event.text)

    bus.subscribe(broken_listener)
    bus.subscribe(healthy_listener)

    event = bus.log("live", "message", "still-delivered")

    assert event.text == "still-delivered"
    assert received == ["still-delivered"]


def test_validate_path_input_keeps_directories_unexpanded(tmp_path: Path) -> None:
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    (source_dir / "nested.txt").write_text("demo", encoding="utf-8")

    valid, invalid = InputInspector.validate_path_input([source_dir])

    assert invalid == []
    assert valid == [source_dir]


def test_pipeline_context_clone_shares_event_bus() -> None:
    bus = PipelineEventBus()
    ctx = PipelineContext(
        original_input=None,
        working_path=Path("."),
        output_dir=Path("."),
        mode="none",
        events=bus,
    )
    ctx.events.log("step1", "message", "hello")

    cloned = ctx.clone(working_path=Path("new"))
    assert cloned.events is ctx.events
    assert len(cloned.events) == 1
    # delete_original no longer exists on PipelineContext
    assert not hasattr(cloned, "delete_original")


def test_pipeline_context_default_events_is_fresh() -> None:
    ctx1 = PipelineContext(
        original_input=None,
        working_path=Path("a"),
        output_dir=Path("."),
        mode="none",
    )
    ctx2 = PipelineContext(
        original_input=None,
        working_path=Path("b"),
        output_dir=Path("."),
        mode="none",
    )
    ctx1.events.log("x", "message", "from ctx1")
    assert len(ctx2.events) == 0


# ---------------------------------------------------------------------------
# Additional boundary tests
# ---------------------------------------------------------------------------


def test_prepare_context_invalid_mode_raises(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    with pytest.raises(FileHandlingError, match="不支持的工作流模式"):
        handler.prepare_context({"path": tmp_path}, mode="input")


def test_prepare_context_unknown_mode_raises(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    with pytest.raises(FileHandlingError, match="不支持的工作流模式"):
        handler.prepare_context({"path": tmp_path}, mode="unknown")


def test_file_handler_init_from_string(tmp_path: Path) -> None:
    handler = FileHandler(str(tmp_path / "output"))
    assert isinstance(handler.output_dir, Path)
    assert handler.output_dir.exists()


def test_build_file_units_empty_list() -> None:
    units = FileHandler.build_file_units([])
    assert units == []


def test_build_file_units_empty_folder(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    units = FileHandler.build_file_units([d])
    assert units == []


def test_build_cycle_units_empty_list() -> None:
    units = FileHandler.build_cycle_units([])
    assert units == []


def test_build_cycle_units_empty_folder(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    units = FileHandler.build_cycle_units([d])
    assert units == []


def test_prepare_file_source_not_exists(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    missing = tmp_path / "missing.txt"
    with pytest.raises(FileHandlingError, match="输入文件不存在"):
        handler._prepare_file_unit({"path": missing, "source_root": None})


def test_prepare_file_source_is_directory(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(FileHandlingError, match="预期文件但收到非文件路径"):
        handler._prepare_file_unit({"path": d, "source_root": None})


def test_prepare_file_source_root_not_exists(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("data", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")
    bad_root = tmp_path / "no-such-root"
    with pytest.raises(FileHandlingError, match="source root不存在"):
        handler._prepare_file_unit({"path": f, "source_root": bad_root})


def test_prepare_file_source_root_is_file(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("data", encoding="utf-8")
    other = tmp_path / "other.txt"
    other.write_text("x", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")
    with pytest.raises(FileHandlingError, match="source root不是目录"):
        handler._prepare_file_unit({"path": f, "source_root": other})


def test_prepare_folder_source_not_exists(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    missing = tmp_path / "no-such-folder"
    with pytest.raises(FileHandlingError, match="输入目录不存在"):
        handler._prepare_folder_unit({"path": missing})


def test_prepare_folder_source_is_file(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    f = tmp_path / "f.txt"
    f.write_text("data", encoding="utf-8")
    with pytest.raises(FileHandlingError, match="输入目录不是目录"):
        handler._prepare_folder_unit({"path": f})


def test_make_unique_path_counter_above_one(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    (handler.output_dir / "dup.txt").write_text("original", encoding="utf-8")
    (handler.output_dir / "dup (1).txt").write_text("dup1", encoding="utf-8")
    r1 = handler._make_unique_path(handler.output_dir / "dup.txt")
    (handler.output_dir / r1.name).write_text("created", encoding="utf-8")
    r2 = handler._make_unique_path(handler.output_dir / "dup.txt")
    assert r1.name == "dup (2).txt"
    assert r2.name == "dup (3).txt"


def test_make_unique_path_no_suffix(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    (handler.output_dir / "README").write_text("original", encoding="utf-8")
    result = handler._make_unique_path(handler.output_dir / "README")
    assert result.name == "README (1)"


def test_make_unique_path_multi_suffix(tmp_path: Path) -> None:
    handler = FileHandler(tmp_path / "output")
    (handler.output_dir / "archive.tar.gz").write_text("original", encoding="utf-8")
    result = handler._make_unique_path(handler.output_dir / "archive.tar.gz")
    assert result.name == "archive (1).tar.gz"


def test_prepare_file_source_root_as_string(tmp_path: Path) -> None:
    f = tmp_path / "sub" / "file.txt"
    f.parent.mkdir(parents=True)
    f.write_text("data", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")
    ctx = handler._prepare_file_unit(
        {"path": f, "source_root": str(tmp_path)},
    )
    assert ctx.source_root == tmp_path
    assert (handler.output_dir / "sub" / "file.txt").exists()


def test_pipeline_context_str_input_coerced_to_path() -> None:
    ctx = PipelineContext(
        original_input="/a/b/c",
        working_path="d:/x/y",
        output_dir="d:/out",
        mode="none",
    )
    assert isinstance(ctx.original_input, Path)
    assert isinstance(ctx.working_path, Path)
    assert isinstance(ctx.output_dir, Path)


def test_pipeline_context_nonexistent_working_path() -> None:
    ctx = PipelineContext(
        original_input=None,
        working_path=Path("/does/not/exist"),
        output_dir=Path("."),
        mode="none",
    )
    assert ctx.is_file is False
    assert ctx.is_dir is False


def test_pipeline_context_track_extra_file_from_string(tmp_path: Path) -> None:
    ctx = PipelineContext(
        original_input=None,
        working_path=tmp_path,
        output_dir=tmp_path,
        mode="none",
    )
    result = ctx.track_extra_file(str(tmp_path / "extra.txt"))
    assert isinstance(result, Path)
    assert result == tmp_path / "extra.txt"
    assert result in ctx.extra_files


def test_pipeline_context_clone_shared_is_shallow_copy() -> None:
    ctx = PipelineContext(
        original_input=None,
        working_path=Path("."),
        output_dir=Path("."),
        mode="none",
        shared={"nested": {"key": "val"}},
    )
    cloned = ctx.clone()
    assert cloned.shared is not ctx.shared
    assert cloned.shared["nested"] is ctx.shared["nested"]


def test_event_bus_query_dual_filter() -> None:
    bus = PipelineEventBus()
    bus.log("a", "message", "m1")
    bus.log("a", "error", "e1")
    bus.log("b", "message", "m2")

    filtered = bus.query(slug="a", event_type="error")
    assert len(filtered) == 1
    assert filtered[0].text == "e1"


def test_event_bus_query_no_filters_returns_all() -> None:
    bus = PipelineEventBus()
    bus.log("x", "message", "hello")
    bus.log("y", "warning", "world")
    assert len(bus.query()) == 2


def test_event_bus_bool() -> None:
    bus = PipelineEventBus()
    assert bool(bus) is True


def test_event_bus_subscribe_duplicate_prevented() -> None:
    bus = PipelineEventBus()
    received: list[str] = []

    def listener(event) -> None:
        received.append(event.text)

    bus.subscribe(listener)
    bus.subscribe(listener)
    bus.log("x", "message", "once")
    assert len(received) == 1


def test_event_bus_unsubscribe_non_registered() -> None:
    bus = PipelineEventBus()

    def listener(event) -> None:
        pass

    bus.unsubscribe(listener)


def test_event_bus_log_returns_event() -> None:
    bus = PipelineEventBus()
    event = bus.log("slug", "hint", "text", data={"k": "v"})
    assert event.slug == "slug"
    assert event.type == "hint"
    assert event.text == "text"
    assert event.data == {"k": "v"}


def test_file_handler_output_dir_is_created(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "output"
    handler = FileHandler(out)
    assert handler.output_dir.exists()


def test_file_handler_direct_mode_output_dir_created(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "direct_output"
    handler = FileHandler(out, direct_mode=True)
    assert handler.output_dir.exists()


def test_prepare_cycle_unit_with_base_context(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("cycle", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")
    base = PipelineContext(
        original_input=None,
        working_path=tmp_path / "output",
        output_dir=handler.output_dir,
        mode="cycle",
        shared={"count": 1},
    )
    ctx = handler._prepare_cycle_unit(
        {"path": f, "source_root": None},
        base_context=base,
    )
    assert ctx.shared["count"] == 1
    assert ctx.events is base.events


def test_prepare_cycle_unit_without_base_context(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("cycle", encoding="utf-8")
    handler = FileHandler(tmp_path / "output")
    ctx = handler._prepare_cycle_unit({"path": f, "source_root": None})
    assert isinstance(ctx.events, PipelineEventBus)
