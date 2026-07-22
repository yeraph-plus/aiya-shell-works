"""使用 FFmpeg 对媒体文件进行转码、格式转换、编码参数调整。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "ffmpeg-convert",
    "name": "FFmpeg 转码",
    "core_version": "2.0.0",
    "tags": ["ffmpeg", "convert", "video", "audio"],
    "is_file_module": True,
    "description": "使用 FFmpeg 转换媒体文件格式、调整编码参数、分辨率、帧率与像素格式，支持硬件加速编码。",
}

_SOFTWARE_CODECS = {
    "h264": "libx264",
    "h265": "libx265",
    "vp9": "libvpx-vp9",
    "mpeg2video": "mpeg2video",
    "mpeg4": "mpeg4",
}

_HW_ENCODER_MAP: dict[str, dict[str, str]] = {
    "h264": {
        "nvenc": "h264_nvenc",
        "qsv": "h264_qsv",
        "amf": "h264_amf",
        "videotoolbox": "h264_videotoolbox",
    },
    "h265": {
        "nvenc": "hevc_nvenc",
        "qsv": "hevc_qsv",
        "amf": "hevc_amf",
        "videotoolbox": "hevc_videotoolbox",
    },
}

_RESOLUTION_SCALE: dict[str, str] = {
    "480p": "scale=-2:480",
    "720p": "scale=-2:720",
    "1080p": "scale=-2:1080",
    "4K": "scale=-2:2160",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "ffmpeg_path": {
            "type": "file_path",
            "title": "FFmpeg 路径",
            "default": "",
            "description": "ffmpeg.exe 的路径，留空则使用项目 resources/ffmpeg 下的版本。",
        },
        "output_format": {
            "type": "select",
            "title": "输出格式",
            "default": "mp4",
            "description": "输出容器的文件格式。",
            "options": [
                {"value": "mp4", "label": "MP4"},
                {"value": "mkv", "label": "MKV"},
                {"value": "avi", "label": "AVI"},
                {"value": "mov", "label": "MOV"},
                {"value": "webm", "label": "WebM"},
                {"value": "gif", "label": "GIF"},
                {"value": "mp3", "label": "MP3 (仅音频)"},
                {"value": "flac", "label": "FLAC (仅音频)"},
                {"value": "wav", "label": "WAV (仅音频)"},
                {"value": "m4a", "label": "M4A (仅音频)"},
            ],
        },
        "video_codec": {
            "type": "select",
            "title": "视频编码器",
            "default": "h264",
            "description": "视频编码格式。",
            "options": [
                {"value": "h264", "label": "H.264"},
                {"value": "h265", "label": "H.265 / HEVC"},
                {"value": "vp9", "label": "VP9"},
                {"value": "mpeg2video", "label": "MPEG-2"},
                {"value": "mpeg4", "label": "MPEG-4"},
                {"value": "copy", "label": "复制视频流"},
                {"value": "none", "label": "无视频"},
            ],
        },
        "audio_codec": {
            "type": "select",
            "title": "音频编码器",
            "default": "aac",
            "description": "音频编码格式。",
            "options": [
                {"value": "aac", "label": "AAC"},
                {"value": "mp3", "label": "MP3"},
                {"value": "opus", "label": "Opus"},
                {"value": "flac", "label": "FLAC"},
                {"value": "copy", "label": "复制音频流"},
                {"value": "none", "label": "无音频"},
            ],
        },
        "hw_accel": {
            "type": "radio",
            "title": "硬件加速",
            "default": "none",
            "description": "硬件加速编码器。仅 H.264/H.265 生效；其他编码器忽略此选项。",
            "options": [
                {"value": "none", "label": "禁用（软件编码）"},
                {"value": "nvenc", "label": "NVIDIA NVENC"},
                {"value": "qsv", "label": "Intel QuickSync"},
                {"value": "amf", "label": "AMD AMF"},
                {"value": "videotoolbox", "label": "Apple VideoToolbox"},
            ],
        },
        "quality": {
            "type": "int",
            "title": "质量 CRF",
            "default": 28,
            "min": 0,
            "max": 51,
            "description": "CRF 质量系数 (0–51，越小质量越好)。硬件加速时自动切换为 -qp。视频编码为 copy/none 时忽略。",
        },
        "preset": {
            "type": "select",
            "title": "编码预设",
            "default": "medium",
            "description": "编码速度预设，仅软件编码有效。",
            "options": [
                "ultrafast",
                "superfast",
                "veryfast",
                "faster",
                "fast",
                "medium",
                "slow",
                "slower",
                "veryslow",
            ],
        },
        "resolution": {
            "type": "select",
            "title": "输出分辨率",
            "default": "original",
            "description": "缩放输出视频分辨率。",
            "options": [
                {"value": "original", "label": "保持原始"},
                {"value": "480p", "label": "480p (SD)"},
                {"value": "720p", "label": "720p (HD)"},
                {"value": "1080p", "label": "1080p (Full HD)"},
                {"value": "4K", "label": "4K (2160p)"},
            ],
        },
        "fps": {
            "type": "str",
            "title": "帧率",
            "default": "",
            "description": "输出帧率，留空保持原始。如 30、24、30000/1001 (NTSC)。",
            "placeholder": "例如 30 或 30000/1001",
        },
        "pix_fmt": {
            "type": "select",
            "title": "像素格式",
            "default": "yuv420p",
            "description": "像素采样格式。yuv420p 兼容性最好；硬件加速推荐 nv12；留空保持原始。",
            "options": [
                {"value": "yuv420p", "label": "yuv420p (8-bit, 最佳兼容)"},
                {"value": "yuv422p", "label": "yuv422p"},
                {"value": "yuv444p", "label": "yuv444p"},
                {"value": "yuv420p10le", "label": "yuv420p10le (10-bit)"},
                {"value": "yuv444p10le", "label": "yuv444p10le (10-bit)"},
                {"value": "rgb24", "label": "rgb24"},
                {"value": "nv12", "label": "nv12 (硬件友好)"},
                {"value": "original", "label": "保持原始"},
            ],
        },
        "audio_bitrate": {
            "type": "str",
            "title": "音频码率",
            "default": "",
            "description": "音频码率，留空使用编码器默认。如 128k、192k、320k。",
            "placeholder": "例如 128k",
        },
        "overwrite": {
            "type": "bool",
            "title": "覆盖已存在文件",
            "default": True,
            "description": "输出文件已存在时自动覆盖 (-y)。",
        },
    },
}

_SUPPORTED_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".ts",
        ".mts",
        ".m2ts",
        ".3gp",
        ".ogv",
        ".vob",
        ".mxf",
        ".mp3",
        ".aac",
        ".wav",
        ".flac",
        ".ogg",
        ".wma",
        ".m4a",
        ".opus",
        ".ape",
        ".wv",
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".gif",
        ".webp",
        ".tiff",
        ".tif",
    }
)


def _resolve_ffmpeg_path(cfg: dict) -> str | None:
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


def _collect_targets(ctx: PipelineContext) -> list[Path]:
    wp = Path(ctx.working_path)
    if wp.is_file():
        return [wp] if wp.suffix.lower() in _SUPPORTED_EXTENSIONS else []
    if wp.is_dir():
        return sorted(f for f in wp.iterdir() if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS)
    return []


def _output_path(input_file: Path, output_dir: Path, output_format: str) -> Path:
    ext = output_format if output_format.startswith(".") else f".{output_format}"
    stem = input_file.stem
    candidate = output_dir / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = output_dir / f"{stem}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def _build_command(
    input_file: Path,
    output_file: Path,
    cfg: dict,
) -> list[str]:
    cmd: list[str] = []

    if cfg.get("overwrite", True):
        cmd.append("-y")

    cmd.extend(["-i", str(input_file)])

    video_codec = cfg.get("video_codec", "h264")
    hw_accel = cfg.get("hw_accel", "none")

    if video_codec == "none":
        cmd.append("-vn")
    elif video_codec == "copy":
        cmd.extend(["-c:v", "copy"])
    else:
        if hw_accel != "none" and (hw_encoder := _HW_ENCODER_MAP.get(video_codec, {}).get(hw_accel)):
            cmd.extend(["-c:v", hw_encoder])
            quality = cfg.get("quality", 28)
            cmd.extend(["-qp", str(quality)])
        else:
            software_encoder = _SOFTWARE_CODECS.get(video_codec, video_codec)
            cmd.extend(["-c:v", software_encoder])
            quality = cfg.get("quality", 28)
            cmd.extend(["-crf", str(quality)])
            preset = cfg.get("preset", "medium")
            cmd.extend(["-preset", preset])

    audio_codec = cfg.get("audio_codec", "aac")
    if audio_codec == "none":
        cmd.append("-an")
    elif audio_codec == "copy":
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-c:a", audio_codec])
        ab = cfg.get("audio_bitrate", "").strip()
        if ab:
            cmd.extend(["-b:a", ab])

    resolution = cfg.get("resolution", "original")
    if resolution != "original":
        scale_filter = _RESOLUTION_SCALE.get(resolution)
        if scale_filter:
            cmd.extend(["-vf", scale_filter])

    fps = cfg.get("fps", "").strip()
    if fps:
        cmd.extend(["-r", fps])

    pix_fmt = cfg.get("pix_fmt", "yuv420p")
    if pix_fmt != "original":
        cmd.extend(["-pix_fmt", pix_fmt])

    cmd.append(str(output_file))
    return cmd


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    targets = _collect_targets(ctx)
    if not targets:
        runtime.log("ffmpeg-convert", "message", "未发现支持的媒体文件，跳过。")
        return ctx

    ffmpeg = _resolve_ffmpeg_path(cfg)
    if ffmpeg is None:
        runtime.log(
            "ffmpeg-convert",
            "error",
            "FFmpeg 未找到，请配置路径或将 ffmpeg.exe 放置到 resources/ffmpeg/ 下，或在工作流配置中指定 ffmpeg.exe 位置。",  # noqa: E501
        )
        return ctx

    output_format = cfg.get("output_format", "mp4")
    output_dir = Path(ctx.output_dir)
    succeeded = 0
    failed = 0

    for target in targets:
        output_file = _output_path(target, output_dir, output_format)
        cmd = _build_command(target, output_file, cfg)

        runtime.log(
            "ffmpeg-convert",
            "hint",
            f"FFmpeg 命令行: {' '.join(cmd)}",
        )
        runtime.log(
            "ffmpeg-convert",
            "message",
            f"开始转码: {target.name} → {output_file.name}",
        )

        try:
            result = runtime.spawn(cmd)
        except OSError as e:
            runtime.log("ffmpeg-convert", "error", f"FFmpeg 启动失败: {e}")
            failed += 1
            continue

        if result.is_success:
            ctx.track_extra_file(output_file)
            succeeded += 1
            runtime.log(
                "ffmpeg-convert",
                "success",
                f"转码完成: {output_file.name}",
                {"output_file": str(output_file)},
            )
        else:
            failed += 1
            runtime.log(
                "ffmpeg-convert",
                "error",
                f"FFmpeg 返回非零退出码: {result.exit_code} — {target.name}",
            )

    runtime.log(
        "ffmpeg-convert",
        "message",
        f"转码批次完成: {succeeded} 成功, {failed} 失败 (共 {len(targets)} 个文件)。",
        {"succeeded": succeeded, "failed": failed, "total": len(targets)},
    )

    if failed > 0 and succeeded == 0:
        runtime.log(
            "ffmpeg-convert",
            "error",
            "所有文件转码均失败。",
        )

    return ctx
