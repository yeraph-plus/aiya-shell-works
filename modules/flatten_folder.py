"""递归移动子文件夹文件到根目录，按深度层级添加数字前缀。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "flatten-folder",
    "name": "递归提取文件",
    "core_version": "2.0.0",
    "tags": ["flatten", "organize"],
    "is_file_module": True,
    "description": "递归移动子文件夹文件到根目录，按深度层级添加数字前缀辅助排序。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "subfolder_first": {
            "type": "bool",
            "title": "子文件夹文件置前",
            "default": True,
            "description": "子文件夹文件排在根目录文件之前。",
        },
        "prefix_separator": {
            "type": "str",
            "title": "前缀分隔符",
            "default": "_",
            "description": "前缀与文件名的分隔符。",
        },
    },
}


def _assign_prefixes(root: Path, subfolder_first: bool) -> dict[Path, str]:
    prefix_map: dict[Path, str] = {}

    if subfolder_first:
        prefix_map[root] = "999"
        _assign_children(root, root, prefix_map, start_index=1)
    else:
        prefix_map[root] = "1"
        _assign_children(root, root, prefix_map, start_index=2)

    return prefix_map


def _assign_children(
    parent: Path,
    root: Path,
    prefix_map: dict[Path, str],
    start_index: int,
) -> None:
    children = sorted(
        [d for d in parent.iterdir() if d.is_dir()],
        key=lambda d: d.name.lower(),
    )
    parent_prefix = prefix_map[parent]

    for i, child in enumerate(children):
        idx = start_index + i
        if parent is root:
            child_prefix = str(idx)
        else:
            child_prefix = f"{parent_prefix}_{idx}"

        prefix_map[child] = child_prefix
        _assign_children(child, root, prefix_map, start_index=1)


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    working_dir = Path(ctx.working_path)
    if not working_dir.is_dir():
        runtime.log("flatten-folder", "error", "working_path 不是目录。")
        return ctx

    subfolder_first = cfg.get("subfolder_first", True)
    separator = cfg.get("prefix_separator", "_")

    prefix_map = _assign_prefixes(working_dir, subfolder_first)

    moved = 0
    failed = 0

    for dir_path, prefix in prefix_map.items():
        for f in sorted(dir_path.iterdir()):
            if not f.is_file():
                continue
            new_name = f"{prefix}{separator}{f.name}"
            target = working_dir / new_name

            collision = 1
            while target.exists():
                stem = f.stem
                suffix = f.suffix
                new_name = f"{prefix}{separator}{stem} ({collision}){suffix}"
                target = working_dir / new_name
                collision += 1

            try:
                f.rename(target)
                moved += 1
            except OSError as e:
                failed += 1
                runtime.log(
                    "flatten-folder",
                    "error",
                    f"移动失败: {f.name} ({e})",
                )

    subdirs = sorted(
        [d for d in working_dir.rglob("*") if d.is_dir() and d != working_dir],
        key=lambda d: -len(d.relative_to(working_dir).parts),
    )

    removed_dirs = 0
    for d in subdirs:
        try:
            d.rmdir()
            removed_dirs += 1
        except OSError:
            pass

    if moved > 0:
        runtime.log(
            "flatten-folder",
            "message",
            f"提取完成: {moved} 个文件已移至根目录, {failed} 个失败, 已清理 {removed_dirs} 个空子目录。",
            {"moved": moved, "failed": failed, "removed_dirs": removed_dirs},
        )
    else:
        runtime.log("flatten-folder", "message", "未发现需要提取的文件。")

    return ctx
