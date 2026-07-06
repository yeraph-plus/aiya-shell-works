"""VapourSynth ML 超分模块。

使用 VSPipe + vs-mlrt 调用超分辨率模型对视频进行 AI 超分辨率处理。
支持 RealESRGAN、SwinIR 等模型，2x/4x 倍率放大。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MODULE_META = {
    "slug": "vs-super-resolution",
    "name": "VapourSynth 超分",
    "core_version": "2.0.0",
    "tags": ["video", "vapoursynth", "super-resolution", "esrgan", "swinir", "ml"],
    "atom": ["file"],
    "parent": "vs-frame-interpolate",
    "description": "使用 VapourSynth + vs-mlrt 对视频进行 AI 超分辨率处理，支持 RealESRGAN / SwinIR 模型。",
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
            "title": "超分模型",
            "options": [
                "RealESRGAN_x4plus",
                "RealESRGAN_x2plus",
                "RealESRGANv2-animevideo-xsx4",
                "SwinIR-L_x4",
            ],
            "default": "RealESRGAN_x4plus",
            "description": "超分辨率模型: x4plus (4x 通用) / x2plus (2x 通用) / anime (动漫) / SwinIR (高质量)。",
        },
        "scale_factor": {
            "type": "select",
            "title": "放大倍数",
            "options": ["2x", "4x"],
            "default": "2x",
            "description": "分辨率放大倍数。注意选择与模型能力匹配的倍数。",
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
        "denoise_strength": {
            "type": "float",
            "title": "降噪强度",
            "default": 0.0,
            "min": 0.0,
            "max": 1.0,
            "description": "内置降噪强度 (0=不降噪, 1=最大)。仅在 GPU 模式下有效。",
        },
        "start_frame": {
            "type": "int",
            "title": "起始帧",
            "default": 0,
            "min": 0,
        },
        "end_frame": {
            "type": "int",
            "title": "结束帧",
            "default": -1,
            "min": -1,
        },
    },
}

_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts",
    ".m4v", ".flv", ".wmv", ".m2ts", ".vob", ".y4m",
})


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


def _resolve_vsmlrt_plugin() -> str | None:
    project_root = Path(__file__).resolve().parent.parent
    resources = project_root / "resources"

    for candidate in sorted(resources.glob("**/vsmlrt.dll"), reverse=True):
        return str(candidate)

    return None


def _resolve_model_dir(cfg: dict) -> Path | None:
    custom = cfg.get("model_path", "").strip()
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
    scale_factor: str,
    gpu: bool,
    denoise_strength: float,
    output_dir: str,
    output_format: str,
    stem: str,
    script_path: str,
) -> None:
    input_escaped = input_path.replace("\\", "\\\\")
    plugin_escaped = plugin_path.replace("\\", "\\\\")
    output_escaped = output_dir.replace("\\", "\\\\")
    model_dir_fwd = model_dir.replace("\\", "/")
    model_file = f"{model_name}.onnx"
    model_full = f"{model_dir_fwd}/{model_file}"
    scale = 2 if scale_factor == "2x" else 4
    gpu_id = 0 if gpu else -1

    lines = []
    lines.append("import vapoursynth as vs")
    lines.append("from vapoursynth import core")
    lines.append("")
    lines.append(f"core.std.LoadPlugin(r\"{plugin_escaped}\")")
    lines.append("")
    lines.append(f"src = core.ffms2.Source(r\"{input_escaped}\")")
    lines.append("")

    if gpu:
        lines.append(f"# ML 超分: {scale_factor} ({model_name})")
        lines.append(f"src = core.resize.Bicubic(src, format=vs.RGBS)")
        lines.append(f"sr = core.mlrt.SR(src,")
        lines.append(f"    model_path=r\"{model_full}\",")
        lines.append(f"    scale={scale},")
        lines.append(f"    gpu_id={gpu_id},")
        if denoise_strength > 0:
            lines.append(f"    denoise_strength={denoise_strength:.2f},")
        lines.append(f")")
    else:
        lines.append(f"# ML 超分 (CPU): {scale_factor} ({model_name})")
        lines.append(f"src = core.resize.Bicubic(src, format=vs.RGBS)")
        lines.append(f"sr = core.mlrt.SR(src,")
        lines.append(f"    model_path=r\"{model_full}\",")
        lines.append(f"    scale={scale},")
        lines.append(f"    gpu_id=-1,")
        lines.append(f")")

    lines.append("")

    if output_format in ("png-sequence", "jpg-sequence"):
        fmt = "PNG" if output_format == "png-sequence" else "JPEG"
        subfolder = f"{stem}_sr_frames"
        lines.append(f"# 输出帧序列到子文件夹")
        lines.append(f"sr = core.imwri.Write(sr, \"{fmt}\", r\"{output_escaped}\\\\{subfolder}\\\\%06d.png\")")

    lines.append("sr.set_output()")

    script_content = "\n".join(lines) + "\n"
    Path(script_path).write_text(script_content, encoding="utf-8")


def run(ctx: "Any", cfg: "Any", runtime: "Any") -> "Any":
    working_path = Path(ctx.working_path)
    output_dir = Path(ctx.output_dir)

    if working_path.is_dir():
        runtime.log(
            "vs-super-resolution", "message",
            "输入为目录 (帧序列)。",
        )
    elif working_path.is_file():
        if working_path.suffix.lower() not in _VIDEO_EXTENSIONS:
            runtime.log(
                "vs-super-resolution", "message",
                f"不支持的视频格式: {working_path.suffix}，跳过。",
            )
            return ctx
    else:
        runtime.log("vs-super-resolution", "error", f"输入无效: {working_path}")
        return ctx

    vspipe = _resolve_vspipe_path(cfg)
    if vspipe is None:
        runtime.log(
            "vs-super-resolution", "error",
            "VSPipe.exe 未找到。请配置 vspipe_path 或运行 resources/install_vapoursynth.ps1 安装 VapourSynth。",
        )
        return ctx

    plugin_path_str = _resolve_vsmlrt_plugin()
    if plugin_path_str is None:
        runtime.log(
            "vs-super-resolution", "error",
            "vsmlrt.dll 未找到。请运行 resources/install_vsmlrt.ps1 安装 vs-mlrt 插件，或将 vsmlrt.dll 放入 resources/ 下任意位置。",
        )
        return ctx
    plugin_path = Path(plugin_path_str)

    model_dir = _resolve_model_dir(cfg)
    if model_dir is None:
        runtime.log(
            "vs-super-resolution", "error",
            "模型目录未找到，请运行 resources/install_vsmlrt.ps1 下载模型，或配置 model_path。",
        )
        return ctx

    model_name = cfg.get("model", "RealESRGAN_x4plus")
    scale_factor = cfg.get("scale_factor", "2x")
    gpu = cfg.get("gpu", True)
    denoise_strength = float(cfg.get("denoise_strength", 0.0))
    output_format = cfg.get("output_format", "y4m")
    start_frame = int(cfg.get("start_frame", 0))
    end_frame = int(cfg.get("end_frame", -1))

    stem = _get_stem(working_path)
    script_path = output_dir / f"{stem}_sr.vpy"

    _generate_vpy_script(
        input_path=str(working_path),
        plugin_path=str(plugin_path),
        model_name=model_name,
        model_dir=str(model_dir),
        scale_factor=scale_factor,
        gpu=gpu,
        denoise_strength=denoise_strength,
        output_dir=str(output_dir),
        output_format=output_format,
        stem=stem,
        script_path=str(script_path),
    )

    runtime.log("vs-super-resolution", "hint", f"VSPipe 脚本已生成: {script_path}")
    runtime.log(
        "vs-super-resolution", "message",
        f"开始超分: {working_path.name} (模型: {model_name}, 倍数: {scale_factor})...",
    )

    if output_format == "y4m":
        output_path = output_dir / f"{stem}_super_resolved.y4m"
        cmd = [vspipe, "-c", "y4m", str(script_path), str(output_path)]
    else:
        output_path = output_dir
        cmd = [vspipe, "-c", "y4m", str(script_path), "NUL"]

    if start_frame > 0:
        cmd.extend(["-s", str(start_frame)])
    if end_frame >= 0:
        cmd.extend(["-e", str(end_frame)])

    runtime.log("vs-super-resolution", "hint", f"命令行: {' '.join(cmd)}")

    try:
        result = runtime.spawn(cmd)
    except OSError as e:
        runtime.log("vs-super-resolution", "error", f"VSPipe 启动失败: {e}")
        return ctx

    if not result.is_success:
        runtime.log(
            "vs-super-resolution", "error",
            f"VSPipe 返回非零退出码: {result.exit_code}",
        )
        return ctx

    if output_format in ("png-sequence", "jpg-sequence"):
        subfolder = f"{stem}_sr_frames"
        frame_dir = output_dir / subfolder
        if frame_dir.exists():
            ctx.track_extra_file(frame_dir)
            runtime.log(
                "vs-super-resolution", "success",
                f"超分完成，帧序列输出到: {frame_dir}",
            )
            return ctx.clone(working_path=frame_dir)
        else:
            runtime.log(
                "vs-super-resolution", "error",
                f"帧序列子文件夹未创建: {frame_dir}",
            )
            return ctx

    ctx.track_extra_file(output_path)
    runtime.log(
        "vs-super-resolution", "success",
        f"超分完成: {output_path.name}",
        {"output_path": str(output_path)},
    )
    return ctx.clone(working_path=output_path)
