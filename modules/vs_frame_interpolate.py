"""VapourSynth RIFE 智能补帧模块。

使用 VSPipe + vs-mlrt 调用 RIFE 模型对视频进行 AI 补帧。
支持 2x/4x/8x 帧率倍增，输出 Y4M 流或帧序列。
"""

from __future__ import annotations

from pathlib import Path

MODULE_META = {
    "slug": "vs-frame-interpolate",
    "name": "VapourSynth 补帧",
    "core_version": "1.0.0",
    "tags": ["video", "vapoursynth", "interpolation", "rife", "ml"],
    "mode": ["file"],
    "parent": "vs-deinterlace",
    "description": "使用 VapourSynth + vs-mlrt RIFE 模型进行 AI 智能补帧，支持 2x/4x/8x 帧率倍增。",
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
        "model": {
            "type": "select",
            "title": "RIFE 模型",
            "options": ["rife-v4.6_ensemble", "rife-v4.15_lite"],
            "default": "rife-v4.6_ensemble",
            "description": "RIFE 补帧模型: v4.6 ensemble (高精度) / v4.15 lite (快速)。",
        },
        "factor": {
            "type": "select",
            "title": "插帧倍数",
            "options": ["2x", "4x", "8x"],
            "default": "2x",
            "description": "帧率倍增倍数。",
        },
        "model_path": {
            "type": "folder_path",
            "title": "模型目录",
            "default": "",
            "description": "ONNX 模型目录，留空使用 resources/models/。",
        },
        "output_format": {
            "type": "select",
            "title": "输出格式",
            "options": ["y4m", "png-sequence", "jpg-sequence"],
            "default": "y4m",
            "description": "Y4M: 无压缩 YUV 流。PNG/JPG 序列: 在子文件夹中输出帧图片。",
        },
        "gpu": {
            "type": "bool",
            "title": "GPU 加速",
            "default": True,
            "description": "启用 CUDA GPU 加速 (需要 NVIDIA 显卡)。",
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

_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts",
    ".m4v", ".flv", ".wmv", ".m2ts", ".vob", ".y4m",
})


def _resolve_vspipe_path(config: dict) -> str | None:
    custom = config.get("vspipe_path", "").strip()
    if custom:
        p = Path(custom)
        if p.exists():
            return str(p)

    project_root = Path(__file__).resolve().parent.parent
    resources = project_root / "resources"

    for candidate in sorted(resources.glob("**/VSPipe.exe"), reverse=True):
        return str(candidate)

    return None


def _resolve_vsmlrt_plugin() -> str | None:
    project_root = Path(__file__).resolve().parent.parent
    resources = project_root / "resources"

    for candidate in sorted(resources.glob("**/vsmlrt.dll"), reverse=True):
        return str(candidate)

    return None


def _resolve_model_dir(config: dict) -> Path | None:
    custom = config.get("model_path", "").strip()
    if custom:
        p = Path(custom)
        if p.is_dir():
            return p

    project_root = Path(__file__).resolve().parent.parent
    models_dir = project_root / "resources" / "models"
    if models_dir.is_dir():
        return models_dir
    return None


def _get_stem(working_path: Path) -> str:
    return working_path.stem


def _generate_vpy_script(
    *,
    input_path: str,
    plugin_path: str,
    model_name: str,
    model_dir: str,
    factor: str,
    gpu: bool,
    output_dir: str,
    output_format: str,
    stem: str,
    script_path: str,
    is_frame_sequence: bool = False,
) -> None:
    input_escaped = input_path.replace("\\", "\\\\")
    plugin_escaped = plugin_path.replace("\\", "\\\\")
    model_escaped = Path(model_dir).replace("\\\\", "\\\\").as_posix() if model_dir else ""
    output_escaped = output_dir.replace("\\", "\\\\")

    # Map factor string to model selector
    factor_map = {"2x": 1, "4x": 2, "8x": 3}
    model_idx = factor_map.get(factor, 1)
    gpu_id = 0 if gpu else -1
    model_file = f"{model_name}.onnx"
    model_full = f"{model_dir.replace(chr(92), '/')}/{model_file}"

    lines = []
    lines.append("import vapoursynth as vs")
    lines.append("from vapoursynth import core")
    lines.append("")
    lines.append(f"core.std.LoadPlugin(r\"{plugin_escaped}\")")
    lines.append("")

    if is_frame_sequence:
        lines.append(f"src = core.imwri.Read(r\"{input_escaped}\\\\%06d.*\")")
    else:
        lines.append(f"src = core.ffms2.Source(r\"{input_escaped}\")")
    lines.append("")

    if gpu:
        lines.append(f"# RIFE 补帧: {factor} ({model_name})")
        lines.append(f"src = core.resize.Bicubic(src, format=vs.RGBS)")
        lines.append(f"interp = core.mlrt.RIFE(src, model={model_idx}, gpu_id={gpu_id})")
    else:
        lines.append(f"# RIFE 补帧 (CPU): {factor} ({model_name})")
        lines.append(f"src = core.resize.Bicubic(src, format=vs.RGBS)")
        lines.append(f"interp = core.mlrt.RIFE(src, model={model_idx}, gpu_id=-1)")

    lines.append("")

    if output_format in ("png-sequence", "jpg-sequence"):
        fmt = "PNG" if output_format == "png-sequence" else "JPEG"
        subfolder = f"{stem}_interp_frames"
        lines.append(f"# 输出帧序列到子文件夹")
        lines.append(f"interp = core.imwri.Write(interp, \"{fmt}\", r\"{output_escaped}\\\\{subfolder}\\\\%06d.png\")")

    lines.append("interp.set_output()")

    script_content = "\n".join(lines) + "\n"
    Path(script_path).write_text(script_content, encoding="utf-8")


def run(context, config):
    working_path = Path(context.working_path)
    output_dir = Path(context.output_dir)

    # Allow both files and directories (sequence from upstream)
    if working_path.is_dir():
        context.events.log(
            "vs-frame-interpolate", "message",
            f"输入为目录 (帧序列)，使用 imwri.Read 读取。",
        )
        # For frame sequences, the upstream module would have set shared
    elif working_path.is_file():
        if working_path.suffix.lower() not in _VIDEO_EXTENSIONS:
            context.events.log(
                "vs-frame-interpolate", "message",
                f"不支持的视频格式: {working_path.suffix}，跳过。",
            )
            return context
    else:
        context.events.log("vs-frame-interpolate", "error", f"输入无效: {working_path}")
        return context

    vspipe = _resolve_vspipe_path(config)
    if vspipe is None:
        context.events.log(
            "vs-frame-interpolate", "error",
            "VSPipe.exe 未找到。请配置 vspipe_path 或运行 resources/install_vapoursynth.ps1 安装 VapourSynth。",
        )
        return context

    plugin_path_str = _resolve_vsmlrt_plugin()
    if plugin_path_str is None:
        context.events.log(
            "vs-frame-interpolate", "error",
            "vsmlrt.dll 未找到。请运行 resources/install_vsmlrt.ps1 安装 vs-mlrt 插件，或将 vsmlrt.dll 放入 resources/ 下任意位置。",
        )
        return context
    plugin_path = Path(plugin_path_str)

    model_dir = _resolve_model_dir(config)
    if model_dir is None:
        context.events.log(
            "vs-frame-interpolate", "error",
            "模型目录未找到，请运行 resources/install_vsmlrt.ps1 下载模型，或配置 model_path。",
        )
        return context

    model_name = config.get("model", "rife-v4.6_ensemble")
    factor = config.get("factor", "2x")
    gpu = config.get("gpu", True)
    output_format = config.get("output_format", "y4m")
    start_frame = int(config.get("start_frame", 0))
    end_frame = int(config.get("end_frame", -1))

    stem = _get_stem(working_path)
    script_path = output_dir / f"{stem}_interp.vpy"

    _generate_vpy_script(
        input_path=str(working_path),
        plugin_path=str(plugin_path),
        model_name=model_name,
        model_dir=str(model_dir),
        factor=factor,
        gpu=gpu,
        output_dir=str(output_dir),
        output_format=output_format,
        stem=stem,
        script_path=str(script_path),
        is_frame_sequence=working_path.is_dir(),
    )

    context.events.log(
        "vs-frame-interpolate", "hint",
        f"VSPipe 脚本已生成: {script_path}",
    )
    context.events.log(
        "vs-frame-interpolate", "message",
        f"开始补帧: {working_path.name} (模型: {model_name}, 倍数: {factor})...",
    )

    if output_format == "y4m":
        output_path = output_dir / f"{stem}_interpolated.y4m"
        cmd = [vspipe, "-c", "y4m", str(script_path), str(output_path)]
    else:
        output_path = output_dir
        cmd = [vspipe, "-c", "y4m", str(script_path), "NUL"]

    if start_frame > 0:
        cmd.extend(["-s", str(start_frame)])
    if end_frame >= 0:
        cmd.extend(["-e", str(end_frame)])

    context.events.log("vs-frame-interpolate", "hint", f"命令行: {' '.join(cmd)}")

    try:
        import winpty  # noqa: F401
    except ImportError:
        context.events.log("vs-frame-interpolate", "error", "pywinpty 未安装，无法执行终端命令。")
        return context

    try:
        result = context.run_command(cmd)
    except OSError as e:
        context.events.log("vs-frame-interpolate", "error", f"VSPipe 启动失败: {e}")
        return context

    if not result.is_success:
        context.events.log(
            "vs-frame-interpolate", "error",
            f"VSPipe 返回非零退出码: {result.exit_code}",
        )
        return context

    if output_format in ("png-sequence", "jpg-sequence"):
        subfolder = f"{stem}_interp_frames"
        frame_dir = output_dir / subfolder
        if frame_dir.exists():
            new_context = context.clone(working_path=frame_dir)
            new_context.track_extra_file(frame_dir)
            new_context.events.log(
                "vs-frame-interpolate", "success",
                f"补帧完成，帧序列输出到: {frame_dir}",
            )
            return new_context
        else:
            context.events.log(
                "vs-frame-interpolate", "error",
                f"帧序列子文件夹未创建: {frame_dir}",
            )
            return context

    updated_context = context.clone(working_path=output_path)
    updated_context.track_extra_file(output_path)
    updated_context.events.log(
        "vs-frame-interpolate", "success",
        f"补帧完成: {output_path.name}",
        {"output_path": str(output_path)},
    )
    return updated_context
