"""文件夹图集智能重命名：按类型分组建模编号，自然排序。"""

from __future__ import annotations

import re
from pathlib import Path


MODULE_META = {
    "slug": "gallery-rename",
    "name": "Gallery 重命名",
    "core_version": "1.0.0",
    "tags": ["rename", "gallery", "sort"],
    "mode": ["folder"],
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


def _collect_targets(context) -> list[Path]:
    wp = Path(context.working_path)
    if not wp.is_dir():
        return []
    return sorted(
        [f for f in wp.iterdir() if f.is_file()], key=lambda f: f.name.lower(),
    )


def run(context, config):
    working_dir = Path(context.working_path)
    if not working_dir.is_dir():
        context.events.log("gallery-rename", "error", "working_path 不是目录。")
        return context

    padding = config.get("padding", 3)
    video_str = config.get("video_extensions", "mp4 mov mkv wmv flv")
    image_str = config.get("image_extensions", "jpg jpeg png")
    video_exts = {e.strip().lower() for e in video_str.split() if e.strip()}
    image_exts = {e.strip().lower() for e in image_str.split() if e.strip()}

    files = _collect_targets(context)
    if not files:
        context.events.log("gallery-rename", "hint", "无可重命名的文件。")
        return context

    # Group files by naming strategy
    groups: dict[str, list[Path]] = {}
    for f in files:
        ext = f.suffix.lower().lstrip(".")
        if not ext:
            continue
        if ext in image_exts:
            key = ext
        elif ext in video_exts:
            key = "VIDEO_"
        else:
            key = f"{ext.upper()}_"
        groups.setdefault(key, []).append(f)

    renamed = 0
    failed = 0

    for prefix, group_files in groups.items():
        group_files.sort(key=lambda f: _natural_sort_key(f.stem))
        counter = 1

        for f in group_files:
            suffix = f.suffix.lower()

            if prefix in image_exts:
                new_stem = f"{counter:0{padding}d}"
            elif prefix == "VIDEO_":
                new_stem = f"VIDEO_{counter:0{padding}d}"
            else:
                new_stem = f"{prefix}{counter:0{padding}d}"

            target = working_dir / f"{new_stem}{suffix}"

            if target == f:
                counter += 1
                continue

            # Resolve collisions
            collision = 0
            while target.exists():
                collision += 1
                target = working_dir / f"{new_stem}_{collision}{suffix}"

            try:
                f.rename(target)
                renamed += 1
            except OSError as e:
                failed += 1
                context.events.log(
                    "gallery-rename", "error",
                    f"重命名失败: {f.name} ({e})",
                )

            counter += 1

    if renamed > 0:
        context.events.log(
            "gallery-rename", "message",
            f"重命名完成: {renamed} 个文件, {failed} 个失败。",
            {"renamed": renamed, "failed": failed},
        )
    elif failed == 0:
        context.events.log("gallery-rename", "message", "文件已为期望名称，无需重命名。")

    return context
