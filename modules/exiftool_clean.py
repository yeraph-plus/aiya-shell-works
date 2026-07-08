"""调用 ExifTool 批量清除图片/视频文件的 EXIF 元数据。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "exiftool-clean",
    "name": "清除EXIF元数据",
    "core_version": "2.0.0",
    "tags": ["exif", "metadata", "privacy"],
    "atom": ["file", "folder"],
    "description": "使用 ExifTool 清除图片/视频/PDF 文件的元数据。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "exiftool_path": {
            "type": "file_path",
            "title": "ExifTool 路径",
            "default": "",
            "description": "exiftool(-k).exe 的路径，留空则使用项目 resources/exiftool 下的版本。",
        },
        "charset_filename": {
            "type": "str",
            "title": "文件名字符集",
            "default": "936",
            "description": "ExifTool -charset filename= 参数值。默认 936 (简体中文 GBK)。可运行 chcp 查看终端当前代码页（如 65001 为 UTF-8 代码页），设为空则不指定字符集。",  # noqa: E501
        },
        "keep_orientation": {
            "type": "bool",
            "title": "保留方向信息",
            "default": False,
        },
        "keep_datetime": {
            "type": "bool",
            "title": "保留时间戳",
            "default": False,
        },
        "recursive": {
            "type": "bool",
            "title": "递归处理子目录",
            "default": False,
            "description": "使用 -r 参数递归处理所有子文件夹（folder 模式下有效）。",
        },
    },
}

_SUPPORTED_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".jpe",
        ".jfif",
        ".png",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".avif",
        ".heic",
        ".heif",
        ".gif",
        ".raw",
        ".cr2",
        ".nef",
        ".arw",
        ".dng",
        ".cr3",
        ".orf",
        ".rw2",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".ts",
        ".pdf",
    }
)


def _resolve_exiftool_path(cfg: dict[str, Any]) -> str | None:
    custom = cfg.get("exiftool_path", "").strip()
    if custom:
        p = Path(custom)
        if p.exists():
            return str(p)

    project_root = Path(__file__).resolve().parent.parent
    resources = project_root / "resources"

    for exe_name in ["exiftool(-k).exe", "exiftool.exe"]:
        for candidate in sorted(resources.glob("exiftool-*"), key=lambda p: p.name, reverse=True):
            exe = candidate / exe_name
            if exe.exists():
                return str(exe)

    exiftool_dir = resources / "exiftool"
    for exe_name in ["exiftool(-k).exe", "exiftool.exe"]:
        exe = exiftool_dir / exe_name
        if exe.exists():
            return str(exe)

    return None


def _collect_targets(ctx: PipelineContext, cfg: dict[str, Any]) -> list[Path]:
    wp = Path(ctx.working_path)
    if ctx.atom == "file":
        return [wp] if wp.is_file() and wp.suffix.lower() in _SUPPORTED_EXTENSIONS else []
    if wp.is_dir():
        if cfg.get("recursive", False):
            return [wp]
        return [f for f in wp.iterdir() if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS]
    return []


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    targets = _collect_targets(ctx, cfg)
    if not targets:
        runtime.log("exiftool-clean", "message", "未发现支持格式的文件，跳过。")
        return ctx

    exiftool = _resolve_exiftool_path(cfg)
    if exiftool is None:
        runtime.log(
            "exiftool-clean",
            "error",
            "ExifTool 未找到，请配置路径或将 exiftool(-k).exe 放置到 resources/exiftool/ 下，或在工作流配置中指定 exiftool(-k).exe 位置。",  # noqa: E501
        )
        return ctx

    cmd = [exiftool, "-all=", "-overwrite_original"]

    charset = cfg.get("charset_filename", "").strip()
    if charset:
        cmd.extend(["-charset", f"filename={charset}"])

    if cfg.get("keep_orientation", False):
        cmd.extend(["-tagsfromfile", "@", "-Orientation"])
    if cfg.get("keep_datetime", False):
        cmd.extend(["-tagsfromfile", "@", "-DateTimeOriginal", "-CreateDate", "-ModifyDate"])

    if cfg.get("recursive", False) and ctx.atom == "folder":
        cmd.append("-r")
        cmd.append(str(ctx.working_path))
    else:
        cmd.extend(str(f) for f in targets)

    runtime.log(
        "exiftool-clean",
        "hint",
        f"ExifTool 命令行: {' '.join(cmd)}",
    )
    runtime.log(
        "exiftool-clean",
        "message",
        f"开始清除 {len(targets)} 个文件的元数据 (ExifTool: {exiftool})...",
    )

    try:
        result = runtime.spawn(cmd, exit_pattern="-- press ENTER --")
    except OSError as e:
        runtime.log("exiftool-clean", "error", f"ExifTool 启动失败: {e}")
        return ctx

    if result.is_success:
        runtime.log(
            "exiftool-clean",
            "success",
            f"元数据清除完成: {len(targets)} 个文件。",
            {"file_count": len(targets)},
        )
    else:
        runtime.log(
            "exiftool-clean",
            "error",
            f"ExifTool 返回非零退出码: {result.exit_code}",
        )

    return ctx
