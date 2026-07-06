"""从 ZIP 压缩包中随机提取图片并生成信息文件。"""

from __future__ import annotations

import json
import random
import zipfile
from pathlib import Path


MODULE_META = {
    "slug": "extract-archive",
    "name": "提取图集",
    "core_version": "1.0.0",
    "tags": ["extract", "zip", "gallery", "archive"],
    "mode": ["file"],
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
        "image_extensions": {
            "type": "str",
            "title": "图片扩展名",
            "default": "jpg jpeg png gif bmp webp",
            "description": "空格分隔的图片文件扩展名列表。",
        },
        "video_extensions": {
            "type": "str",
            "title": "视频扩展名",
            "default": "mp4 mov mkv wmv flv webm avi",
            "description": "空格分隔的视频文件扩展名列表（仅用于统计）。",
        },
    },
}


def run(context, config):
    working_path = Path(context.working_path)
    output_dir = Path(context.output_dir)

    if working_path.suffix.lower() != ".zip":
        context.events.log(
            "extract-archive", "error",
            f"文件类型不支持，仅接受 .zip 文件: {working_path.name}",
        )
        return context

    image_exts = _parse_extensions(config.get("image_extensions", "jpg jpeg png gif bmp webp"))
    video_exts = _parse_extensions(config.get("video_extensions", "mp4 mov mkv wmv flv webm avi"))
    extract_count = int(config.get("extract_count", 6))

    try:
        with zipfile.ZipFile(working_path, "r") as zf:
            all_entries = [n for n in zf.infolist() if not n.is_dir()]
            image_files = []
            video_files = []
            total_uncompressed = 0

            for entry in all_entries:
                total_uncompressed += entry.file_size
                ext = Path(entry.filename).suffix.lower().lstrip(".")
                if ext in image_exts:
                    image_files.append(entry.filename)
                elif ext in video_exts:
                    video_files.append(entry.filename)

            if not image_files:
                context.events.log(
                    "extract-archive", "error",
                    f"ZIP 中未发现图片文件: {working_path.name}",
                )
                return context

            selected_count = min(extract_count, len(image_files))
            selected = random.sample(image_files, selected_count)
    except zipfile.BadZipFile:
        context.events.log(
            "extract-archive", "error",
            f"ZIP 文件损坏或无法读取: {working_path.name}",
        )
        return context

    stem = working_path.stem
    subfolder = output_dir / stem
    subfolder.mkdir(parents=True, exist_ok=True)

    extracted_files = []
    with zipfile.ZipFile(working_path, "r") as zf:
        for name in selected:
            original_name = Path(name).name
            dest = _unique_path(subfolder / original_name)
            with zf.open(name) as src:
                dest.write_bytes(src.read())
            extracted_files.append(dest)

    info = {
        "title": stem,
        "category": "Gallery",
        "language": "Chinese",
        "file_count": {
            "image": len(image_files),
            "video": len(video_files),
        },
        "file_size": total_uncompressed,
        "thumbnail": "./.thumb",
        "tags": {
            "artist": [],
            "group": [],
            "parody": [],
            "character": [],
            "female": [],
            "male": [],
            "mixed": [],
            "other": [],
        },
    }
    info_path = subfolder / "info.json"
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    context.track_extra_file(subfolder)
    context.track_extra_file(info_path)
    for path in extracted_files:
        context.track_extra_file(path)

    context.events.log(
        "extract-archive", "success",
        f"已从 {working_path.name} 提取 {selected_count} 张图片到 {subfolder.name}/（图片 {len(image_files)} 张，视频 {len(video_files)} 个，体积 {total_uncompressed} 字节）",
        {
            "extracted": selected_count,
            "total_images": len(image_files),
            "total_videos": len(video_files),
            "total_size": total_uncompressed,
            "subfolder": str(subfolder),
        },
    )

    return context


def _parse_extensions(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split() if e.strip()}


def _unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
