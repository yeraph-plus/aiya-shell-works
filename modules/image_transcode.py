"""Transcode images to AVIF (lossless) or JPEG (strip alpha channel)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.files import WorkspaceFile
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "image-transcode",
    "name": "图片转码",
    "core_version": "2.0.0",
    "tags": ["image", "transcode", "avif", "jpg"],
    "access": "read_write",
    "platforms": None,
    "description": "将图片无损转码为 AVIF，或清除透明通道后转码为 JPEG。",
}

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".tiff",
        ".tif",
        ".avif",
    }
)

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "select",
            "title": "转码模式",
            "description": "AVIF = 无损保留透明通道；JPEG = 清除不可见像素后输出 JPG。",
            "options": [
                {"value": "avif", "label": "AVIF（无损）"},
                {"value": "jpg", "label": "JPEG（清除透明像素）"},
            ],
            "default": "avif",
        },
        "quality": {
            "type": "int",
            "title": "JPEG 质量",
            "description": "JPEG 输出质量 1–100。AVIF 模式忽略此参数。",
            "default": 95,
            "min": 1,
            "max": 100,
        },
        "jpeg_background": {
            "type": "str",
            "title": "JPEG 背景色",
            "description": "六位十六进制颜色（如 FFFFFF），透明像素合成到此色上。仅 JPEG 模式生效。",
            "default": "FFFFFF",
        },
    },
}

SLUG = MODULE_META["slug"]


def _collect_targets(ctx: PipelineContext, runtime: PipelineRuntime) -> list[WorkspaceFile]:
    from core.tools import collect_file_targets

    targets = collect_file_targets(ctx, extensions=SUPPORTED_EXTENSIONS)
    if not targets:
        runtime.log(SLUG, "message", "No supported image files found, skipping.")
    return targets


def _parse_hex_color(raw: str) -> tuple[int, int, int]:
    hex_str = raw.strip().lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))


def run(
    ctx: PipelineContext,
    cfg: dict[str, Any],
    runtime: PipelineRuntime,
) -> PipelineContext | None:
    targets = _collect_targets(ctx, runtime)
    if not targets:
        return ctx

    mode = str(cfg.get("mode", "avif"))
    quality = int(cfg.get("quality", 95))
    bg_color = _parse_hex_color(str(cfg.get("jpeg_background", "FFFFFF")))

    runtime.log(
        SLUG,
        "message",
        f"Starting transcode ({mode}): {len(targets)} image(s)",
        {"mode": mode, "total": len(targets)},
    )

    processed = 0
    original_current = ctx.current.path
    for target in targets:
        runtime.log(SLUG, "message", f"Processing: {target.name}")
        output_file = None
        try:
            img = Image.open(target.path)
            img = ImageOps.exif_transpose(img)

            if mode == "avif":
                desired_name = target.path.with_suffix(".avif").name
                output_file = ctx.allocate_file(desired_name)
                if img.mode in ("P", "PA"):
                    img = img.convert("RGBA")
                elif img.mode not in ("RGB", "RGBA", "L", "LA"):
                    img = img.convert("RGBA")
                img.save(output_file.path, lossless=True)

            elif mode == "jpg":
                desired_name = target.path.with_suffix(".jpg").name
                output_file = ctx.allocate_file(desired_name)
                if img.mode == "RGBA":
                    background = Image.new("RGB", img.size, bg_color)
                    background.paste(img, mask=img.split()[3])
                    img = background
                elif img.mode in ("P", "PA"):
                    img = img.convert("RGBA")
                    background = Image.new("RGB", img.size, bg_color)
                    background.paste(img, mask=img.split()[3])
                    img = background
                elif img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(output_file.path, optimize=True, quality=quality)

            output_file = ctx.adopt(output_file.path)
            replaces_current = target.path == original_current
            target.delete()
            if target.name == desired_name and output_file.name != desired_name:
                output_file = output_file.rename(desired_name)
            if replaces_current:
                ctx.set_current(output_file.path)

            processed += 1

        except Exception:
            if output_file is not None:
                ctx.delete(output_file.path)
            raise

    runtime.log(
        SLUG,
        "success",
        f"Transcoded {processed} image(s) to {mode.upper()} (total {len(targets)}).",
        {"mode": mode, "processed": processed, "total": len(targets)},
    )

    return ctx
