"""从 ZIP 压缩包中随机提取图片并生成信息文件。"""

from __future__ import annotations

import json
import random
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "extract-archive",
    "name": "提取图集",
    "core_version": "2.0.0",
    "tags": ["extract", "zip", "gallery", "archive"],
    "access": "read_write",
    "platforms": None,
    "description": "从 ZIP 压缩包中随机提取图片文件到输出目录，并生成 info.json 信息文件。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "extract_count": {
            "type": "int",
            "title": "提取数量",
            "default": 6,
            "min": 1,
            "max": 100,
            "description": "从每个 ZIP 中随机提取的图片数量。不足时全取。",
        },
        "category": {
            "type": "str",
            "title": "分类",
            "default": "Gallery",
            "description": "写入 info.json 的 category 字段值。",
        },
    },
}

def _parse_extensions(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split() if e.strip()}


_IMAGE_EXTENSIONS = _parse_extensions("jpg jpeg png gif bmp webp")
_VIDEO_EXTENSIONS = _parse_extensions("mp4 mov mkv wmv flv webm avi")


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    if not ctx.current.is_file or ctx.current.path.suffix.lower() != ".zip":
        raise ValueError(f"文件类型不支持，仅接受 .zip 文件: {ctx.current.name}")
    working_path = ctx.current.path

    extract_count = int(cfg.get("extract_count", 6))
    category = cfg.get("category", "Gallery")

    try:
        with zipfile.ZipFile(working_path, "r") as archive:
            all_entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            image_files = []
            video_files = []
            other_files = []
            total_uncompressed = 0

            for entry in all_entries:
                total_uncompressed += entry.file_size
                ext = Path(entry.filename).suffix.lower().lstrip(".")
                if ext in _IMAGE_EXTENSIONS:
                    image_files.append(entry.filename)
                elif ext in _VIDEO_EXTENSIONS:
                    video_files.append(entry.filename)
                else:
                    other_files.append(entry.filename)

            if not image_files:
                raise ValueError(f"ZIP 中未发现图片文件: {working_path.name}")

            selected_count = min(extract_count, len(image_files))
            selected = random.sample(image_files, selected_count)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"ZIP 文件损坏或无法读取: {working_path.name}") from exc

    stem = working_path.stem
    subfolder = ctx.create_directory(stem)

    with zipfile.ZipFile(working_path, "r") as archive:
        for name in selected:
            original_name = Path(name).name
            with archive.open(name) as source:
                ctx.create_file(subfolder.path / original_name, source.read())

    info = {
        "title": stem,
        "category": category,
        "language": "Chinese",
        "file_count": {
            "image": len(image_files),
            "video": len(video_files),
            "other": len(other_files),
        },
        "file_size": total_uncompressed,
        "thumbnail": "./.thumb",
        "tags": {},
    }
    ctx.create_file(
        subfolder.path / "info.json",
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    runtime.log(
        "extract-archive",
        "success",
        f"已从 {working_path.name} 提取 {selected_count} 张图片到 {subfolder.name}/（图片 {len(image_files)} 张，视频 {len(video_files)} 个，其他 {len(other_files)} 个，体积 {total_uncompressed} 字节）",  # noqa: E501
        {
            "extracted": selected_count,
            "total_images": len(image_files),
            "total_videos": len(video_files),
            "total_other": len(other_files),
            "total_size": total_uncompressed,
            "subfolder": str(subfolder.path),
        },
    )

    return ctx
