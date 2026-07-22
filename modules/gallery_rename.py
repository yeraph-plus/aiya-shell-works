"""文件夹图集智能重命名：按类型分组建模编号，自然排序。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "gallery-rename",
    "name": "Gallery 重命名",
    "core_version": "2.0.0",
    "tags": ["rename", "gallery", "sort"],
    "access": "read_write",
    "platforms": None,
    "description": "按文件类型分组建模重命名：jpg/png 无前缀，视频统一 VIDEO_ 队列，其余格式按自身后缀前缀。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "padding": {
            "type": "int",
            "title": "数字位数",
            "default": 3,
            "min": 1,
            "max": 6,
            "description": "零填充位数，如 3 → 001。",
        },
        "video_extensions": {
            "type": "str",
            "title": "视频扩展名",
            "default": "mp4 mov mkv wmv flv",
            "description": "空格分隔，共用 VIDEO_ 统一队列编号。",
        },
        "image_extensions": {
            "type": "str",
            "title": "图片扩展名",
            "default": "jpg jpeg png",
            "description": "空格分隔，这些格式无前缀直接编号（如 001.jpg）。",
        },
    },
}


def _natural_sort_key(name: str) -> list:
    parts = re.split(r"(\d+)", name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    if not ctx.current.is_dir:
        raise ValueError("当前工作区资源不是目录")

    padding = cfg.get("padding", 3)
    video_str = cfg.get("video_extensions", "mp4 mov mkv wmv flv")
    image_str = cfg.get("image_extensions", "jpg jpeg png")
    video_exts = {e.strip().lower() for e in video_str.split() if e.strip()}
    image_exts = {e.strip().lower() for e in image_str.split() if e.strip()}

    files = sorted(ctx.files(recursive=False), key=lambda item: item.name.lower())
    if not files:
        runtime.log("gallery-rename", "hint", "无可重命名的文件。")
        return ctx

    groups: dict[str, list[Any]] = {}
    for file_entry in files:
        ext = file_entry.path.suffix.lower().lstrip(".")
        if not ext:
            continue
        if ext in image_exts:
            key = ext
        elif ext in video_exts:
            key = "VIDEO_"
        else:
            key = f"{ext.upper()}_"
        groups.setdefault(key, []).append(file_entry)

    renamed = 0
    for prefix, group_files in groups.items():
        group_files.sort(key=lambda item: _natural_sort_key(item.path.stem))
        counter = 1

        for file_entry in group_files:
            suffix = file_entry.path.suffix.lower()

            if prefix in image_exts:
                new_stem = f"{counter:0{padding}d}"
            elif prefix == "VIDEO_":
                new_stem = f"VIDEO_{counter:0{padding}d}"
            else:
                new_stem = f"{prefix}{counter:0{padding}d}"

            new_name = f"{new_stem}{suffix}"
            if new_name == file_entry.name:
                counter += 1
                continue
            file_entry.rename(new_name)
            renamed += 1

            counter += 1

    if renamed > 0:
        runtime.log(
            "gallery-rename",
            "message",
            f"重命名完成: {renamed} 个文件。",
            {"renamed": renamed},
        )
    else:
        runtime.log("gallery-rename", "message", "文件已为期望名称，无需重命名。")

    return ctx
