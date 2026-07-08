"""FFmpeg 合成编码模块。

将 VapourSynth 输出的 Y4M 原始视频流或帧序列合成为最终编码视频。
支持 x264/x265/NVENC 硬件编码，自动检测输入类型。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "ffmpeg-compose",
    "name": "FFmpeg 合成编码",
    "core_version": "2.0.0",
    "tags": ["video", "ffmpeg", "encode", "compose"],
    "is_file_module": True,
    "parent": "vs-super-resolution",
    "description": "使用 FFmpeg 将 Y4M 原始流或帧序列合成为最终编码视频，支持多种编码器和参数。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "ffmpeg_path": {
            "type": "file_path",
            "title": "FFmpeg 路径",
            "default": "",
            "description": "ffmpeg.exe 路径，留空自动从 resources/ffmpeg/ 查找。",
        },
        "encoder": {
            "type": "select",
            "title": "编码器",
            "options": [
                "libx264",
                "libx265",
                "h264_nvenc",
                "hevc_nvenc",
                "libsvtav1",
            ],
            "default": "libx264",
            "description": "视频编码器。NVENC 需要 NVIDIA 显卡。",
        },
        "preset": {
            "type": "select",
            "title": "编码预设",
            "options": ["ultrafast", "fast", "medium", "slow", "veryslow"],
            "default": "medium",
            "description": "编码速度/质量平衡 (仅 x264/x265 有效)。",
        },
        "crf": {
            "type": "int",
            "title": "CRF 质量",
            "default": 18,
            "min": 0,
            "max": 51,
            "description": "CRF 质量控制 (0=无损, 18=视觉无损, 23=默认, 51=最差)。",
        },
        "pixel_format": {
            "type": "select",
            "title": "像素格式",
            "options": ["yuv420p", "yuv422p", "yuv444p", "rgb24", "auto"],
            "default": "auto",
            "description": "输出像素格式。auto=自动检测。",
        },
        "output_container": {
            "type": "select",
            "title": "输出封装格式",
            "options": ["mp4", "mkv", "mov"],
            "default": "mp4",
            "description": "输出文件封装容器。",
        },
        "framerate": {
            "type": "str",
            "title": "帧率",
            "default": "",
            "description": "帧序列合成时的帧率，如 24000/1001 或 30。Y4M 模式下自动检测。",
        },
        "frame_pattern": {
            "type": "str",
            "title": "帧序列匹配模式",
            "default": "%06d",
            "description": "sprintf 风格帧编号模式，通常为 %06d (6位补零)。",
        },
    },
}

_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".ts",
        ".m4v",
        ".flv",
        ".wmv",
        ".m2ts",
        ".vob",
        ".y4m",
    }
)


def _resolve_ffmpeg_path(cfg: dict[str, Any]) -> str | None:
    custom = cfg.get("ffmpeg_path", "").strip()
    if custom:
        p = Path(custom)
        if p.exists():
            return str(p)

    project_root = Path(__file__).resolve().parent.parent
    resources = project_root / "resources"

    for candidate in sorted(resources.glob("**/ffmpeg.exe"), reverse=True):
        return str(candidate)

    return None


def _format_ext(container: str) -> str:
    return {
        "mp4": ".mp4",
        "mkv": ".mkv",
        "mov": ".mov",
    }.get(container, ".mp4")


def _find_sequence_pattern(directory: Path) -> tuple[str, str] | None:
    png_files = sorted(directory.glob("*.png"))
    jpg_files = sorted(directory.glob("*.jpg")) + sorted(directory.glob("*.jpeg"))
    files = png_files or jpg_files

    if not files:
        return None

    first = files[0]
    stem = first.stem
    ext = first.suffix

    digits = ""
    for ch in reversed(stem):
        if ch.isdigit():
            digits = ch + digits
        else:
            break

    if digits:
        prefix = stem[: -len(digits)]
        padding = len(digits)
        pattern = f"{prefix}%0{padding}d{ext}"
        return (prefix, pattern)

    pattern = f"%06d{ext}"
    return ("", pattern)


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    working_path = Path(ctx.working_path)
    output_dir = Path(ctx.output_dir)

    ffmpeg = _resolve_ffmpeg_path(cfg)
    if ffmpeg is None:
        runtime.log(
            "ffmpeg-compose",
            "error",
            "FFmpeg 未找到，请配置 ffmpeg_path 或运行 resources/install_ffmpeg.ps1。",
        )
        return ctx

    encoder = cfg.get("encoder", "libx264")
    preset = cfg.get("preset", "medium")
    crf = int(cfg.get("crf", 18))
    pixel_format = cfg.get("pixel_format", "auto")
    container = cfg.get("output_container", "mp4")
    framerate_str = cfg.get("framerate", "").strip()

    stem = working_path.stem

    is_frame_sequence = working_path.is_dir()
    is_y4m = not is_frame_sequence and working_path.suffix.lower() == ".y4m"
    is_video = not is_frame_sequence and working_path.suffix.lower() in _VIDEO_EXTENSIONS

    if not (is_frame_sequence or is_y4m or is_video):
        if working_path.is_dir() and _find_sequence_pattern(working_path):
            is_frame_sequence = True
        else:
            runtime.log(
                "ffmpeg-compose",
                "error",
                f"不支持的输入类型: {working_path}。需要 Y4M 文件或帧序列目录。",
            )
            return ctx

    output_ext = _format_ext(container)
    output_path = output_dir / f"{stem}_output{output_ext}"

    if is_frame_sequence:
        detected = _find_sequence_pattern(working_path)
        pattern = framerate_str or (detected[1] if detected else cfg.get("frame_pattern", "%06d"))

        fps = framerate_str or "24"

        cmd = [
            ffmpeg,
            "-framerate",
            fps,
            "-i",
            str(working_path / pattern),
        ]
        runtime.log(
            "ffmpeg-compose",
            "message",
            f"合成帧序列: {working_path.name} (帧率: {fps}, 模式: {pattern})...",
        )
    elif is_y4m:
        cmd = [
            ffmpeg,
            "-i",
            str(working_path),
        ]
        runtime.log(
            "ffmpeg-compose",
            "message",
            f"编码 Y4M: {working_path.name}...",
        )
    else:
        cmd = [
            ffmpeg,
            "-i",
            str(working_path),
        ]
        runtime.log(
            "ffmpeg-compose",
            "message",
            f"编码视频: {working_path.name}...",
        )

    cmd.extend(["-c:v", encoder])

    if encoder in ("libx264", "libx265"):
        cmd.extend(["-preset", preset])
        cmd.extend(["-crf", str(crf)])

    if pixel_format != "auto":
        cmd.extend(["-pix_fmt", pixel_format])

    cmd.extend(["-y", str(output_path)])

    runtime.log("ffmpeg-compose", "hint", f"命令行: {' '.join(cmd)}")

    try:
        result = runtime.spawn(cmd)
    except OSError as e:
        runtime.log("ffmpeg-compose", "error", f"FFmpeg 启动失败: {e}")
        return ctx

    if not result.is_success:
        runtime.log(
            "ffmpeg-compose",
            "error",
            f"FFmpeg 返回非零退出码: {result.exit_code}",
        )
        return ctx

    ctx.track_extra_file(output_path)
    runtime.log(
        "ffmpeg-compose",
        "success",
        f"编码完成: {output_path.name}",
        {"output_path": str(output_path)},
    )
    return ctx.clone(working_path=output_path)
