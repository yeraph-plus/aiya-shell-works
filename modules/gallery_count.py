"""文件夹图集统计计数：忽略图片，统计视频和其他文件，在文件夹后追加计数标签。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "gallery-count",
    "name": "Gallery 统计计数",
    "core_version": "2.0.0",
    "tags": ["gallery", "count", "statistics"],
    "is_file_module": True,
    "description": "扫描文件夹顶层文件，忽略图片，统计视频和其他文件数量，在文件夹名后追加 [1V 2PDF 3GIF] 格式的计数标签。",  # noqa: E501
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "video_extensions": {
            "type": "str",
            "title": "视频扩展名",
            "default": "mp4 mov mkv wmv flv",
            "description": "空格分隔，统计后合并为 V 标签。",
        },
        "image_extensions": {
            "type": "str",
            "title": "图片扩展名",
            "default": "jpg jpeg png",
            "description": "空格分隔，这些格式的文件将被忽略，不计入统计。",
        },
    },
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    working_dir = Path(ctx.working_path)
    if not working_dir.is_dir():
        runtime.log("gallery-count", "error", "working_path 不是目录。")
        return ctx

    video_str = cfg.get("video_extensions", "mp4 mov mkv wmv flv")
    image_str = cfg.get("image_extensions", "jpg jpeg png")

    video_exts = {e.strip().lower() for e in video_str.split() if e.strip()}
    image_exts = {e.strip().lower() for e in image_str.split() if e.strip()}

    files = [f for f in working_dir.iterdir() if f.is_file()]
    if not files:
        runtime.log("gallery-count", "hint", "无可统计的文件。")
        return ctx

    video_count = 0
    other_counts: dict[str, int] = {}

    for f in files:
        ext = f.suffix.lower().lstrip(".")
        if not ext:
            continue
        if ext in image_exts:
            continue
        if ext in video_exts:
            video_count += 1
        else:
            other_counts[ext.upper()] = other_counts.get(ext.upper(), 0) + 1

    parts = []
    if video_count > 0:
        parts.append(f"{video_count}V")
    for ext_label in sorted(other_counts):
        parts.append(f"{other_counts[ext_label]}{ext_label}")

    if not parts:
        runtime.log("gallery-count", "message", "未发现图片以外的文件。")
        return ctx

    suffix = " [" + " ".join(parts) + "]"
    new_name = working_dir.name + suffix
    new_dir = working_dir.with_name(new_name)

    if new_dir.exists():
        runtime.log(
            "gallery-count",
            "error",
            f"目标路径已存在: {new_dir}",
            {"target": str(new_dir)},
        )
        return ctx

    try:
        working_dir.rename(new_dir)
    except OSError as e:
        runtime.log(
            "gallery-count",
            "error",
            f"重命名文件夹失败: {e}",
            {"source": str(working_dir), "target": str(new_dir)},
        )
        return ctx

    new_ctx = ctx.clone(working_path=new_dir)
    runtime.log(
        "gallery-count",
        "success",
        f"已追加计数标签: {suffix}",
        {"suffix": suffix, "video_count": video_count, "other_counts": dict(other_counts)},
    )
    return new_ctx
