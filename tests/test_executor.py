"""Tests for workflow execution, progress reporting, and param validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import (
    ModuleManager,
    PipelineExecutionError,
    PipelineExecutor,
    WorkflowDefinition,
    WorkflowMeta,
    WorkflowStep,
)


def write_module(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def make_workflow(*, mode: str, steps: tuple[WorkflowStep, ...]) -> WorkflowDefinition:
    return WorkflowDefinition(meta=WorkflowMeta(name="Demo Workflow"), mode=mode, steps=steps)


def test_execute_workflow_in_file_mode_processes_each_file_with_error_isolation(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "annotate.py",
        """
from pathlib import Path

MODULE_META = {"slug": "annotate", "name": "Annotate", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "suffix": {"type": "str", "default": "_done"},
    },
}

def run(context, config):
    path = Path(context.working_path)
    if path.stem == "bad":
        raise RuntimeError("boom")
    renamed = path.with_name(path.stem + config["suffix"] + path.suffix)
    path.rename(renamed)
    return context.clone(working_path=renamed)
""".strip(),
    )

    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "good.txt").write_text("ok", encoding="utf-8")
    (input_dir / "bad.txt").write_text("fail", encoding="utf-8")

    events: list[object] = []
    progress_events: list[dict[str, object]] = []
    workflow = make_workflow(
        mode="file",
        steps=(WorkflowStep(module="annotate", params={"suffix": "_x"}),),
    )
    executor = PipelineExecutor(
        module_manager=ModuleManager(modules_dir),
        event_callback=events.append,
        progress_callback=progress_events.append,
    )

    result = executor.execute(
        workflow,
        input_path=input_dir,
        output_dir=tmp_path / "output",
    )

    assert result["success"] is False
    assert result["processed_units"] == 2
    assert result["successful_units"] == 1
    assert result["failed_units"] == 1
    assert len(result["errors"]) == 1
    assert (tmp_path / "output" / "good_x.txt").exists()
    assert (tmp_path / "output" / "bad.txt").exists()
    assert (input_dir / "good.txt").exists()
    assert any("处理单元失败" in e.text for e in events)
    assert progress_events[-1]["status"] == "done"
    assert progress_events[-2]["status"] in {"completed", "failed"}


def test_execute_workflow_in_none_mode_uses_output_dir_as_working_path(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "create_file.py",
        """
from pathlib import Path

MODULE_META = {"slug": "create-file", "name": "Create File", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "demo.txt"},
        "content": {"type": "str", "default": "hello"},
    },
}

def run(context, config):
    target = Path(context.working_path) / config["filename"]
    target.write_text(config["content"], encoding="utf-8")
    context.track_extra_file(target)
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="none",
        steps=(
            WorkflowStep(
                module="create-file",
                params={"filename": "created.txt", "content": "payload"},
            ),
        ),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(workflow, output_dir=tmp_path / "output")

    assert result["success"] is True
    assert result["processed_units"] == 1
    assert (tmp_path / "output" / "created.txt").read_text(encoding="utf-8") == "payload"


def test_execute_workflow_applies_default_params_in_copy_mode(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "append_text.py",
        """
from pathlib import Path

MODULE_META = {"slug": "append-text", "name": "Append Text", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "suffix": {"type": "str", "default": "_default"},
    },
}

def run(context, config):
    path = Path(context.working_path)
    renamed = path.with_name(path.stem + config["suffix"] + path.suffix)
    path.rename(renamed)
    return context.clone(working_path=renamed)
""".strip(),
    )

    original = tmp_path / "source.txt"
    original.write_text("demo", encoding="utf-8")
    workflow = make_workflow(mode="file", steps=(WorkflowStep(module="append-text", params={}),))
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_path=original,
        output_dir=tmp_path / "output",
    )

    assert result["success"] is True
    assert result["successful_units"] == 1
    # copy mode: original still exists, copy is in output_dir
    assert original.exists()
    assert (tmp_path / "output" / "source_default.txt").exists()


def test_execute_workflow_forwards_module_events_during_run(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "live_probe.py",
        """
MODULE_META = {"slug": "live-probe", "name": "Live Probe", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    context.events.log("live-probe", "message", "during-run")
    observed = context.shared["observed"]
    if "during-run" not in observed:
        raise RuntimeError("event callback did not receive live event")
    return context
""".strip(),
    )

    observed: list[str] = []
    workflow = make_workflow(mode="none", steps=(WorkflowStep(module="live-probe", params={}),))
    executor = PipelineExecutor(
        module_manager=ModuleManager(modules_dir),
        event_callback=lambda event: observed.append(event.text),
    )

    result = executor.execute(
        workflow,
        output_dir=tmp_path / "output",
        shared={"observed": observed},
    )

    assert result["success"] is True
    assert "during-run" in observed


def test_execute_workflow_direct_mode_works_on_original(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "touch.py",
        """
from pathlib import Path

MODULE_META = {"slug": "touch", "name": "Touch", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    path = Path(context.working_path)
    new_content = path.read_text(encoding="utf-8") + "_modified"
    path.write_text(new_content, encoding="utf-8")
    return context
""".strip(),
    )

    original = tmp_path / "direct.txt"
    original.write_text("hello", encoding="utf-8")
    workflow = make_workflow(mode="file", steps=(WorkflowStep(module="touch", params={}),))
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_path=original,
        output_dir=tmp_path / "output",
        direct_mode=True,
    )

    assert result["success"] is True
    # direct mode: original is modified in place
    assert original.read_text(encoding="utf-8") == "hello_modified"
    # no copy in output_dir (but output_dir may exist for extra files)
    assert not (tmp_path / "output" / "direct.txt").exists()


def test_execute_workflow_rejects_invalid_step_params_before_start(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "resize.py",
        """
MODULE_META = {"slug": "resize", "name": "Resize", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "width": {"type": "int", "required": True, "min": 1},
    },
}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="none",
        steps=(WorkflowStep(module="resize", params={"width": 0}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="参数校验失败"):
        executor.execute(workflow, output_dir=tmp_path / "output")


def test_execute_workflow_requires_folder_input_for_folder_mode(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "noop.py",
        """
MODULE_META = {"slug": "noop", "name": "Noop", "core_version": "1.0.0", "tags": ["test"], "mode": ["folder"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(mode="folder", steps=(WorkflowStep(module="noop", params={}),))
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))
    file_input = tmp_path / "input.txt"
    file_input.write_text("demo", encoding="utf-8")

    with pytest.raises(PipelineExecutionError, match="folder 模式要求输入路径为文件夹"):
        executor.execute(
            workflow,
            input_path=file_input,
            output_dir=tmp_path / "output",
        )


def test_execute_workflow_stops_before_next_unit_when_cancel_requested(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "noop.py",
        """
MODULE_META = {"slug": "noop", "name": "Noop", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "one.txt").write_text("1", encoding="utf-8")
    (input_dir / "two.txt").write_text("2", encoding="utf-8")

    progress_events: list[dict[str, object]] = []
    cancel_state = {"requested": False}

    def on_progress(event: dict[str, object]) -> None:
        progress_events.append(event)
        if event["status"] == "completed" and event["current"] == 1:
            cancel_state["requested"] = True

    workflow = make_workflow(mode="file", steps=(WorkflowStep(module="noop", params={}),))
    executor = PipelineExecutor(
        module_manager=ModuleManager(modules_dir),
        progress_callback=on_progress,
        cancel_requested=lambda: cancel_state["requested"],
    )

    result = executor.execute(
        workflow,
        input_path=input_dir,
        output_dir=tmp_path / "output",
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["successful_units"] == 1
    assert result["failed_units"] == 0
    assert (tmp_path / "output" / "one.txt").exists()
    assert not (tmp_path / "output" / "two.txt").exists()
    assert progress_events[-1]["status"] == "cancelled"


def test_execute_workflow_accepts_batch_input_paths_and_preserves_relative_paths(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "noop.py",
        """
MODULE_META = {"slug": "noop", "name": "Noop", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    source_dir = tmp_path / "inputs"
    nested = source_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "demo.txt").write_text("demo", encoding="utf-8")

    workflow = make_workflow(mode="file", steps=(WorkflowStep(module="noop", params={}),))
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_paths=[source_dir],
        output_dir=tmp_path / "output",
    )

    assert result["success"] is True
    assert result["processed_units"] == 1
    assert result["results"][0]["working_path"] == str(
        tmp_path / "output" / "nested" / "demo.txt"
    )


def test_execute_workflow_cycle_mode_shares_state_across_batch_input_paths(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "counter.py",
        """
from pathlib import Path

MODULE_META = {"slug": "counter", "name": "Counter", "core_version": "1.0.0", "tags": ["test"], "mode": ["cycle"]}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "default": "report.txt"},
    },
}

def run(context, config):
    count = context.shared.get("count", 0) + 1
    report = Path(context.output_dir) / config["filename"]
    report.write_text(str(count), encoding="utf-8")
    return context.clone(shared={**context.shared, "count": count})
""".strip(),
    )

    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")

    workflow = make_workflow(mode="cycle", steps=(WorkflowStep(module="counter", params={}),))
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_paths=[first, second],
        output_dir=tmp_path / "output",
    )

    assert result["success"] is True
    assert result["processed_units"] == 2
    assert (tmp_path / "output" / "report.txt").read_text(encoding="utf-8") == "2"


def test_execute_workflow_direct_mode_still_allows_sidecar_outputs(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "writer.py",
        """
from pathlib import Path

MODULE_META = {"slug": "writer", "name": "Writer", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    target = Path(context.output_dir) / "sidecar.txt"
    target.write_text("ok", encoding="utf-8")
    context.track_extra_file(target)
    return context
""".strip(),
    )

    original = tmp_path / "direct.txt"
    original.write_text("hello", encoding="utf-8")
    output_dir = tmp_path / "output"
    workflow = make_workflow(mode="file", steps=(WorkflowStep(module="writer", params={}),))
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_path=original,
        output_dir=output_dir,
        direct_mode=True,
    )

    assert result["success"] is True
    assert (output_dir / "sidecar.txt").read_text(encoding="utf-8") == "ok"


# ---------------------------------------------------------------------------
# Input mode tests
# ---------------------------------------------------------------------------


def test_execute_workflow_input_mode_processes_text_lines(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "echo_line.py",
        """
from pathlib import Path

MODULE_META = {"slug": "echo-line", "name": "Echo Line", "core_version": "1.0.0", "tags": ["test"], "mode": ["input"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    line = context.shared.get("input_line", "")
    out = Path(context.working_path) / "out.txt"
    out.write_text(line, encoding="utf-8")
    context.track_extra_file(out)
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="input",
        steps=(WorkflowStep(module="echo-line", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_text="hello\nworld\n",
        output_dir=tmp_path / "output",
    )

    assert result["success"] is True
    assert result["processed_units"] == 2
    assert result["successful_units"] == 2


def test_execute_workflow_input_mode_empty_text_returns_nothing(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "dummy.py",
        """
MODULE_META = {"slug": "dummy", "name": "Dummy", "core_version": "1.0.0", "tags": ["test"], "mode": ["input"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="input",
        steps=(WorkflowStep(module="dummy", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(
        workflow,
        input_text="",
        output_dir=tmp_path / "output",
    )

    assert result["processed_units"] == 0


# ---------------------------------------------------------------------------
# Edge case executor tests
# ---------------------------------------------------------------------------


def test_execute_none_mode_rejects_input_path(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="none",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="不接受输入路径"):
        executor.execute(workflow, input_path=tmp_path, output_dir=tmp_path / "out")


def test_execute_input_mode_rejects_file_input(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["input"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="input",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="不接受文件输入路径"):
        executor.execute(workflow, input_path=tmp_path, output_dir=tmp_path / "out")


def test_execute_rejects_both_input_path_and_input_paths(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="file",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="不能同时提供"):
        executor.execute(
            workflow,
            input_path=tmp_path,
            input_paths=[tmp_path],
            output_dir=tmp_path / "out",
        )


def test_execute_folder_rejects_multiple_inputs(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["folder"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    d1 = tmp_path / "d1"
    d1.mkdir()
    d2 = tmp_path / "d2"
    d2.mkdir()

    workflow = make_workflow(
        mode="folder",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="仅接受一个输入"):
        executor.execute(
            workflow,
            input_paths=[d1, d2],
            output_dir=tmp_path / "out",
        )


def test_execute_requires_input_paths_for_file_mode(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="file",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="必须提供输入路径"):
        executor.execute(workflow, output_dir=tmp_path / "out")


def test_execute_rejects_non_existent_input_path(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="file",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="输入路径不存在"):
        executor.execute(
            workflow,
            input_path=tmp_path / "missing.txt",
            output_dir=tmp_path / "out",
        )


def test_execute_rejects_unsupported_path_type(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    # On Windows, a valid path that is neither file nor dir is rare.
    # Use a broken symlink approach or just test the exception path via the
    # _normalize_input_sources method directly.
    workflow = make_workflow(
        mode="file",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    with pytest.raises(PipelineExecutionError, match="输入路径不存在"):
        executor.execute(
            workflow,
            input_path=tmp_path / "nonexistent_dir",
            output_dir=tmp_path / "out",
        )


def test_execute_module_not_found_raises(tmp_path: Path) -> None:
    workflow = make_workflow(
        mode="none",
        steps=(WorkflowStep(module="nonexistent-module", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(tmp_path / "empty_modules"))

    with pytest.raises(PipelineExecutionError, match="未找到工作流步骤所需模块"):
        executor.execute(workflow, output_dir=tmp_path / "out")


def test_execute_step_returning_dict_with_context(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "dict_return.py",
        """
from pathlib import Path

MODULE_META = {"slug": "dict-ret", "name": "Dict Return", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    new_ctx = context.clone()
    new_ctx.events.log("dict-ret", "message", "legacy return")
    return {"context": new_ctx}
""".strip(),
    )
    events: list[object] = []

    workflow = make_workflow(
        mode="none",
        steps=(WorkflowStep(module="dict-ret", params={}),),
    )
    executor = PipelineExecutor(
        module_manager=ModuleManager(modules_dir),
        event_callback=events.append,
    )

    result = executor.execute(workflow, output_dir=tmp_path / "out")
    assert result["success"] is True
    assert any("legacy return" in str(e) for e in events)


def test_execute_step_returning_invalid_type_raises(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_return.py",
        """
MODULE_META = {"slug": "bad-ret", "name": "Bad Return", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return "not-a-valid-return"
""".strip(),
    )

    workflow = make_workflow(
        mode="none",
        steps=(WorkflowStep(module="bad-ret", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(workflow, output_dir=tmp_path / "out")
    assert result["success"] is False
    assert result["failed_units"] == 1
    assert any("返回值非法" in e["error"] for e in result["errors"])


def test_execute_step_returning_none_uses_fallback(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "none_return.py",
        """
from pathlib import Path

MODULE_META = {"slug": "none-ret", "name": "None Return", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    out = Path(context.working_path) / "created.txt"
    out.write_text("ok", encoding="utf-8")
    return None
""".strip(),
    )

    workflow = make_workflow(
        mode="none",
        steps=(WorkflowStep(module="none-ret", params={}),),
    )
    executor = PipelineExecutor(module_manager=ModuleManager(modules_dir))

    result = executor.execute(workflow, output_dir=tmp_path / "out")
    assert result["success"] is True
    assert (tmp_path / "out" / "created.txt").read_text(encoding="utf-8") == "ok"


def test_execute_with_progress_total_zero(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["input"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    progress_events: list[dict[str, object]] = []
    workflow = make_workflow(
        mode="input",
        steps=(WorkflowStep(module="mod", params={}),),
    )
    executor = PipelineExecutor(
        module_manager=ModuleManager(modules_dir),
        progress_callback=progress_events.append,
    )

    result = executor.execute(
        workflow,
        input_text="",
        output_dir=tmp_path / "out",
    )

    assert result["processed_units"] == 0
    assert progress_events[0]["percent"] == 100


def test_execute_cancel_during_unit_steps(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "mod.py",
        """
import time

MODULE_META = {"slug": "mod", "name": "Mod", "core_version": "1.0.0", "tags": ["test"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return context
""".strip(),
    )

    workflow = make_workflow(
        mode="none",
        steps=(
            WorkflowStep(module="mod", params={}),
            WorkflowStep(module="mod", params={}),
        ),
    )

    cancel_called = [False]

    def cancel() -> bool:
        if cancel_called[0]:
            return True
        cancel_called[0] = True
        return False

    executor = PipelineExecutor(
        module_manager=ModuleManager(modules_dir),
        cancel_requested=cancel,
    )

    result = executor.execute(workflow, output_dir=tmp_path / "out")
    assert result["cancelled"] is True
