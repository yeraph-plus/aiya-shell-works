"""Integration tests for flatten-folder module."""

from __future__ import annotations

from pathlib import Path

from core import (
    ModuleManager,
    PipelineExecutor,
    WorkflowDefinition,
    WorkflowMeta,
    WorkflowStep,
)


def make_workflow(*, mode: str, steps: tuple[WorkflowStep, ...]) -> WorkflowDefinition:
    return WorkflowDefinition(meta=WorkflowMeta(name="Flatten Test"), mode=mode, steps=steps)


def _generate_nested_structure(root: Path) -> None:
    root.mkdir()
    (root / "root_file_a.txt").write_text("ra", encoding="utf-8")
    (root / "root_file_b.txt").write_text("rb", encoding="utf-8")

    photos = root / "photos"
    photos.mkdir()
    (photos / "cat.jpg").write_text("cat", encoding="utf-8")
    (photos / "dog.jpg").write_text("dog", encoding="utf-8")

    deep = photos / "deep"
    deep.mkdir()
    (deep / "raw.cr2").write_text("raw", encoding="utf-8")

    videos = root / "videos"
    videos.mkdir()
    (videos / "demo.mp4").write_text("demo", encoding="utf-8")


def _get_sorted_files(dir_path: Path) -> list[str]:
    return sorted(
        f.name for f in dir_path.iterdir() if f.is_file()
    )


def test_flatten_subfolder_first_true(tmp_path: Path) -> None:
    """subfolder_first=True → root files get 999_, subdirs 1_,2_,3_..."""
    work = tmp_path / "work"
    out = tmp_path / "output"
    _generate_nested_structure(work)

    executor = PipelineExecutor(module_manager=ModuleManager("modules"))
    result = executor.execute(
        WorkflowDefinition(
            meta=WorkflowMeta(name="flatten-test"),
            mode="folder",
            steps=(
                WorkflowStep(
                    module="flatten-folder",
                    name="提取",
                    params={"subfolder_first": True, "prefix_separator": "_"},
                ),
            ),
        ),
        input_path=work,
        output_dir=out,
    )

    assert result["success"] is True
    work_copy = out / "work"
    files = _get_sorted_files(work_copy)
    # subfolder_first=True: subdir files first (1_...), root files last (999_...)
    # photos=1_ (alphabetically before videos=2_)
    # photos/deep=1_1_
    assert files == [
        "1_1_raw.cr2",
        "1_cat.jpg",
        "1_dog.jpg",
        "2_demo.mp4",
        "999_root_file_a.txt",
        "999_root_file_b.txt",
    ]
    assert not (work_copy / "photos").exists()
    assert not (work_copy / "videos").exists()


def test_flatten_subfolder_first_false(tmp_path: Path) -> None:
    """subfolder_first=False → root files get 1_, subdirs 2_,3_..."""
    work = tmp_path / "work"
    out = tmp_path / "output"
    _generate_nested_structure(work)

    executor = PipelineExecutor(module_manager=ModuleManager("modules"))
    result = executor.execute(
        WorkflowDefinition(
            meta=WorkflowMeta(name="flatten-test"),
            mode="folder",
            steps=(
                WorkflowStep(
                    module="flatten-folder",
                    name="提取",
                    params={"subfolder_first": False, "prefix_separator": "_"},
                ),
            ),
        ),
        input_path=work,
        output_dir=out,
    )

    assert result["success"] is True
    work_copy = out / "work"
    files = _get_sorted_files(work_copy)
    # root files 1_, then photos=2_, photos/deep=2_1_, videos=3_
    assert files == [
        "1_root_file_a.txt",
        "1_root_file_b.txt",
        "2_1_raw.cr2",
        "2_cat.jpg",
        "2_dog.jpg",
        "3_demo.mp4",
    ]
