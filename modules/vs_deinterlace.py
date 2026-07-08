"""VapourSynth 去隔行/反交错模块。

通过 VSPipe 调用 VapourSynth 脚本对隔行视频进行去隔行处理。
支持 BWDIF（内置）、VIVTC（反胶卷过带）两种方法。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "vs-deinterlace",
    "name": "VapourSynth 去隔行",
    "core_version": "2.0.0",
    "tags": ["video", "vapoursynth", "deinterlace", "ivtc"],
    "is_file_module": True,
    "parent": None,
    "description": "使用 VapourSynth 对隔行视频进行去隔行处理，支持 BWDIF 双倍帧率去隔行和 VIVTC 反胶卷过带。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "vspipe_path": {
            "type": "file_path",
            "title": "VSPipe 路径",
            "default": "",
            "description": "VSPipe.exe 路径，留空自动从 resources/vapoursynth/ 查找。",
        },
        "method": {
            "type": "select",
            "title": "去隔行方法",
            "options": ["bwdif", "vivtc"],
            "default": "bwdif",
            "description": "BWDIF: 双倍帧率自适应去隔行 (快速，需要内置插件)。VIVTC: 反胶卷过带 IVTC (用于 3:2 pulldown 素材)。",  # noqa: E501
        },
        "double_rate": {
            "type": "bool",
            "title": "双倍帧率",
            "default": True,
            "description": "BWDIF 模式下将场拆分为帧，输出帧率翻倍。VIVTC 模式下始终为原帧率。",
        },
        "output_format": {
            "type": "select",
            "title": "输出格式",
            "options": ["y4m", "png-sequence", "jpg-sequence"],
            "default": "y4m",
            "description": "Y4M: 无压缩 YUV 流。PNG/JPG 序列: 在子文件夹中输出帧图片。",
        },
        "start_frame": {
            "type": "int",
            "title": "起始帧",
            "default": 0,
            "min": 0,
            "description": "处理起始帧 (含)。",
        },
        "end_frame": {
            "type": "int",
            "title": "结束帧",
            "default": -1,
            "min": -1,
            "description": "处理结束帧 (含)，-1 为全部。",
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


def _resolve_vspipe_path(cfg: dict) -> str | None:
    custom = cfg.get("vspipe_path", "").strip()
    if custom:
        p = Path(custom)
        if p.exists():
            return str(p)

    project_root = Path(__file__).resolve().parent.parent
    resources = project_root / "resources"

    for candidate in sorted(resources.glob("**/VSPipe.exe"), reverse=True):
        return str(candidate)

    return None


def _get_stem(working_path: Path) -> str:
    return working_path.stem


def _generate_vpy_script(
    *,
    input_path: str,
    method: str,
    double_rate: bool,
    output_dir: str,
    output_format: str,
    stem: str,
    script_path: str,
) -> None:
    input_escaped = input_path.replace("\\", "\\\\")
    output_escaped = output_dir.replace("\\", "\\\\")

    lines: list[str] = []
    lines.append("import vapoursynth as vs")
    lines.append("from vapoursynth import core")
    lines.append("")
    lines.append(f'src = core.ffms2.Source(r"{input_escaped}")')
    lines.append("")

    if method == "bwdif":
        field = 1 if double_rate else 0
        lines.append(f"# BWDIF 去隔行 (field={field})")
        lines.append(f"deint = core.bwdif.Bwdif(src, field={field})")
    elif method == "vivtc":
        lines.append("# VIVTC 反胶卷过带 (VFM + VDecimate)")
        lines.append("deint = core.vivtc.VFM(src, order=1)")
        lines.append("deint = core.vivtc.VDecimate(deint)")
    else:
        lines.append(f"deint = src  # unknown method: {method}")

    lines.append("")

    if output_format in ("png-sequence", "jpg-sequence"):
        fmt = "PNG" if output_format == "png-sequence" else "JPEG"
        subfolder = f"{stem}_deinterlace_frames"
        lines.append("# 输出帧序列到子文件夹")
        lines.append(f'deint = core.imwri.Write(deint, "{fmt}", r"{output_escaped}\\\\{subfolder}\\\\%06d.png")')

    lines.append("deint.set_output()")

    script_content = "\n".join(lines) + "\n"
    Path(script_path).write_text(script_content, encoding="utf-8")


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    working_path = Path(ctx.working_path)
    output_dir = Path(ctx.output_dir)

    if not working_path.is_file():
        runtime.log("vs-deinterlace", "error", f"输入不是文件: {working_path}")
        return ctx

    if working_path.suffix.lower() not in _VIDEO_EXTENSIONS:
        runtime.log(
            "vs-deinterlace",
            "message",
            f"不支持的视频格式: {working_path.suffix}，跳过。支持的格式: {', '.join(sorted(_VIDEO_EXTENSIONS))}",
        )
        return ctx

    vspipe = _resolve_vspipe_path(cfg)
    if vspipe is None:
        runtime.log(
            "vs-deinterlace",
            "error",
            "VSPipe.exe 未找到。请配置 vspipe_path 或运行 resources/install_vapoursynth.ps1 安装 VapourSynth。",
        )
        return ctx

    method = cfg.get("method", "bwdif")
    double_rate = cfg.get("double_rate", True)
    output_format = cfg.get("output_format", "y4m")
    start_frame = int(cfg.get("start_frame", 0))
    end_frame = int(cfg.get("end_frame", -1))

    stem = _get_stem(working_path)
    script_path = output_dir / f"{stem}_deinterlace.vpy"

    _generate_vpy_script(
        input_path=str(working_path),
        method=method,
        double_rate=double_rate,
        output_dir=str(output_dir),
        output_format=output_format,
        stem=stem,
        script_path=str(script_path),
    )

    runtime.log(
        "vs-deinterlace",
        "hint",
        f"VSPipe 脚本已生成: {script_path}",
    )
    runtime.log(
        "vs-deinterlace",
        "message",
        f"开始去隔行: {working_path.name} (方法: {method}, 输出: {output_format})...",
    )

    if output_format == "y4m":
        output_path = output_dir / f"{stem}_deinterlace.y4m"
        cmd = [vspipe, "-c", "y4m", str(script_path), str(output_path)]
    else:
        output_path = output_dir
        cmd = [vspipe, "-c", "y4m", str(script_path), "NUL"]

    if start_frame > 0:
        cmd.extend(["-s", str(start_frame)])
    if end_frame >= 0:
        cmd.extend(["-e", str(end_frame)])

    runtime.log("vs-deinterlace", "hint", f"命令行: {' '.join(cmd)}")

    try:
        result = runtime.spawn(cmd)
    except OSError as e:
        runtime.log("vs-deinterlace", "error", f"VSPipe 启动失败: {e}")
        return ctx

    if not result.is_success:
        runtime.log(
            "vs-deinterlace",
            "error",
            f"VSPipe 返回非零退出码: {result.exit_code}",
        )
        return ctx

    if output_format in ("png-sequence", "jpg-sequence"):
        subfolder = f"{stem}_deinterlace_frames"
        frame_dir = output_dir / subfolder
        if frame_dir.exists():
            ctx.track_extra_file(frame_dir)
            runtime.log(
                "vs-deinterlace",
                "success",
                f"去隔行完成，帧序列输出到: {frame_dir}",
            )
            return ctx.clone(working_path=frame_dir)
        else:
            runtime.log(
                "vs-deinterlace",
                "error",
                f"帧序列子文件夹未创建: {frame_dir}",
            )
            return ctx

    ctx.track_extra_file(output_path)
    runtime.log(
        "vs-deinterlace",
        "success",
        f"去隔行完成: {output_path.name}",
        {"output_path": str(output_path)},
    )
    return ctx.clone(working_path=output_path)
