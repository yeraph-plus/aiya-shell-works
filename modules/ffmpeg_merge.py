"""使用 FFmpeg 下载并合并 m3u8/m3u 播放列表为单个媒体文件，支持自定义 HTTP 头、AES 解密。"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "ffmpeg-merge",
    "name": "FFmpeg 合并 m3u8",
    "core_version": "2.0.0",
    "tags": ["ffmpeg", "merge", "m3u8", "hls", "download"],
    "is_file_module": False,
    "description": "使用 FFmpeg 下载并合并 m3u8/m3u/HLS 播放列表为单个文件，支持自定义 HTTP 请求头、断线重连、AES-128 解密。",  # noqa: E501
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
            "description": "合并后输出的容器格式。",
            "options": [
                {"value": "mp4", "label": "MP4"},
                {"value": "mkv", "label": "MKV"},
                {"value": "ts", "label": "TS"},
                {"value": "mov", "label": "MOV"},
                {"value": "m4a", "label": "M4A (仅音频)"},
                {"value": "mp3", "label": "MP3 (仅音频)"},
            ],
        },
        "video_codec": {
            "type": "select",
            "title": "视频编码",
            "default": "copy",
            "description": "copy 表示不重编码直接复制原始流；选择编码器则重编码。",
            "options": [
                {"value": "copy", "label": "复制原始流"},
                {"value": "h264", "label": "H.264"},
                {"value": "h265", "label": "H.265 / HEVC"},
            ],
        },
        "audio_codec": {
            "type": "select",
            "title": "音频编码",
            "default": "copy",
            "description": "copy 表示不重编码直接复制原始流。",
            "options": [
                {"value": "copy", "label": "复制原始流"},
                {"value": "aac", "label": "AAC"},
                {"value": "mp3", "label": "MP3"},
                {"value": "opus", "label": "Opus"},
            ],
        },
        "user_agent": {
            "type": "str",
            "title": "User-Agent",
            "default": "",
            "description": "HTTP User-Agent 请求头。",
            "placeholder": "例如 Mozilla/5.0 ...",
        },
        "referer": {
            "type": "str",
            "title": "Referer",
            "default": "",
            "description": "HTTP Referer 请求头。",
        },
        "origin": {
            "type": "str",
            "title": "Origin",
            "default": "",
            "description": "HTTP Origin 请求头。",
        },
        "cookie": {
            "type": "str",
            "title": "Cookie",
            "default": "",
            "description": "Cookie 字符串，如 key1=val1; key2=val2。",
        },
        "extra_headers": {
            "type": "str",
            "title": "附加请求头",
            "default": "",
            "description": '每行一个 "Key: Value" 格式的附加 HTTP 请求头。',
            "placeholder": "X-Custom: value\nX-Forwarded-For: 1.2.3.4",
        },
        "reconnect": {
            "type": "bool",
            "title": "断线自动重连",
            "default": True,
            "description": "启用 -reconnect 等参数，断开时自动重连。",
        },
        "reconnect_delay": {
            "type": "int",
            "title": "重连延迟（秒）",
            "default": 5,
            "min": 1,
            "max": 60,
            "description": "重连最大等待秒数。",
        },
        "segment_timeout": {
            "type": "int",
            "title": "分段超时（微秒）",
            "default": 10000000,
            "min": 1000000,
            "max": 300000000,
            "description": "单个分片下载超时时间（微秒），默认 10 秒。",
        },
        "key": {
            "type": "str",
            "title": "AES-128 解密密钥",
            "default": "",
            "description": "AES-128 解密密钥。填写 32 位十六进制字符串（如 0123...ef）则不重编码，留空则自动处理 m3u8 内置密钥。",  # noqa: E501
            "placeholder": "32 位十六进制字符串",
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
        ".m3u8",
        ".m3u",
    }
)

_SOFTWARE_CODECS = {
    "h264": "libx264",
    "h265": "libx265",
}

_HEX_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


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


def _resolve_input(ctx: PipelineContext) -> str | None:
    line = (ctx.shared.get("input_line") or "").strip()
    if line:
        return line
    return str(ctx.working_path) if ctx.is_file or ctx.is_dir else None


def _derive_output_name(source: str, output_format: str) -> str:
    ext = output_format if output_format.startswith(".") else f".{output_format}"
    raw = source.rsplit("?", 1)[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = Path(raw).stem
    return f"{stem}{ext}" if stem else f"merged{ext}"


def _output_path(name: str, output_dir: Path) -> Path:
    candidate = output_dir / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    ext = Path(name).suffix
    counter = 1
    while True:
        candidate = output_dir / f"{stem}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def _build_headers(cfg: dict) -> list[str]:
    args: list[str] = []
    ua = cfg.get("user_agent", "").strip()
    if ua:
        args.extend(["-user_agent", ua])

    custom_headers: list[str] = []
    referer = cfg.get("referer", "").strip()
    if referer:
        custom_headers.append(f"Referer: {referer}")
    origin = cfg.get("origin", "").strip()
    if origin:
        custom_headers.append(f"Origin: {origin}")
    cookie = cfg.get("cookie", "").strip()
    if cookie:
        custom_headers.append(f"Cookie: {cookie}")

    extra = cfg.get("extra_headers", "").strip()
    if extra:
        for line in extra.splitlines():
            line = line.strip()
            if line and ":" in line:
                custom_headers.append(line)

    if custom_headers:
        args.extend(["-headers", "\r\n".join(custom_headers)])

    return args


def _build_reconnect_args(cfg: dict) -> list[str]:
    if not cfg.get("reconnect", True):
        return []
    delay = cfg.get("reconnect_delay", 5)
    return [
        "-reconnect",
        "1",
        "-reconnect_at_eof",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        str(delay),
    ]


def _build_key_args(cfg: dict) -> list[str] | None:
    key = cfg.get("key", "").strip()
    if not key:
        return None
    if _HEX_KEY_PATTERN.match(key):
        return _key_info_from_hex(key)
    p = Path(key)
    if p.exists():
        return ["-hls_key_info_file", str(p)]
    return None


def _key_info_from_hex(hex_key: str) -> list[str]:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".key",
        delete=False,
        encoding="ascii",
    ) as tf:
        tf.write(hex_key)
        key_file = tf.name
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".keyinfo",
        delete=False,
        encoding="ascii",
    ) as tf:
        tf.write(f"file:{key_file}\n{key_file}\n")
        info_file = tf.name
    return ["-hls_key_info_file", info_file]


def _build_command(
    source: str,
    output_file: str,
    cfg: dict,
) -> list[str]:
    cmd: list[str] = []

    if cfg.get("overwrite", True):
        cmd.append("-y")

    cmd.extend(_build_headers(cfg))
    cmd.extend(_build_reconnect_args(cfg))

    timeout = cfg.get("segment_timeout", 10000000)
    cmd.extend(["-timeout", str(timeout)])

    key_args = _build_key_args(cfg)
    if key_args:
        cmd.extend(key_args)

    cmd.extend(["-i", source])

    video_codec = cfg.get("video_codec", "copy")
    if video_codec == "copy":
        cmd.extend(["-c:v", "copy"])
    else:
        software_encoder = _SOFTWARE_CODECS.get(video_codec, video_codec)
        cmd.extend(["-c:v", software_encoder])

    audio_codec = cfg.get("audio_codec", "copy")
    if audio_codec == "copy":
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-c:a", audio_codec])

    cmd.append(output_file)
    return cmd


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    source = _resolve_input(ctx)
    if not source:
        runtime.log("ffmpeg-merge", "message", "无输入源，跳过。")
        return ctx

    if ctx.is_file:
        wp = Path(ctx.working_path)
        if wp.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            runtime.log(
                "ffmpeg-merge",
                "message",
                f"不支持的文件类型，仅接受 {', '.join(sorted(_SUPPORTED_EXTENSIONS))}。",
            )
            return ctx

    ffmpeg = _resolve_ffmpeg_path(cfg)
    if ffmpeg is None:
        runtime.log(
            "ffmpeg-merge",
            "error",
            "FFmpeg 未找到，请配置路径或将 ffmpeg.exe 放置到 resources/ffmpeg/ 下。",
        )
        return ctx

    output_format = cfg.get("output_format", "mp4")
    output_dir = Path(ctx.output_dir)
    output_name = _derive_output_name(source, output_format)
    output_file = _output_path(output_name, output_dir)

    cmd = _build_command(source, str(output_file), cfg)

    runtime.log(
        "ffmpeg-merge",
        "hint",
        f"FFmpeg 命令行: {' '.join(cmd)}",
    )
    runtime.log(
        "ffmpeg-merge",
        "message",
        f"开始合并: {source} → {output_file.name}",
    )

    try:
        cwd = str(Path(source).parent) if ctx.is_file and Path(source).exists() else str(output_dir)
        result = runtime.spawn(cmd, cwd=cwd)
    except OSError as e:
        runtime.log("ffmpeg-merge", "error", f"FFmpeg 启动失败: {e}")
        return ctx

    if result.is_success:
        ctx.track_extra_file(output_file)
        runtime.log(
            "ffmpeg-merge",
            "success",
            f"合并完成: {output_file.name}",
            {"output_file": str(output_file), "source": source},
        )
    else:
        runtime.log(
            "ffmpeg-merge",
            "error",
            f"FFmpeg 返回非零退出码: {result.exit_code}",
            {"source": source},
        )

    return ctx
