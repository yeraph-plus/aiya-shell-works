"""Transcode images to AVIF (lossless) or JPEG (strip alpha channel)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "image-transcode",
    "name": "图片转码",
    "core_version": "2.0.0",
    "tags": ["image", "transcode", "avif", "jpg"],
    "is_file_module": True,
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


def _collect_targets(ctx: PipelineContext, runtime: PipelineRuntime) -> list[Path]:
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
    failed = 0

    for target in targets:
        runtime.log(SLUG, "info", f"Processing: {target.name}")

        try:
            img = Image.open(target)
            img = ImageOps.exif_transpose(img)

            if mode == "avif":
                save_path = target.with_suffix(".avif")
                if img.mode in ("P", "PA"):
                    img = img.convert("RGBA")
                elif img.mode not in ("RGB", "RGBA", "L", "LA"):
                    img = img.convert("RGBA")
                img.save(save_path, lossless=True)

            elif mode == "jpg":
                save_path = target.with_suffix(".jpg")
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
                img.save(save_path, optimize=True, quality=quality)

            if save_path != target:
                target.unlink(missing_ok=True)

            processed += 1

        except Exception:
            runtime.log(SLUG, "error", f"Failed to transcode: {target.name}")
            failed += 1
            continue

    runtime.log(
        SLUG,
        "success" if failed == 0 else "warning",
        f"Transcoded {processed} image(s) to {mode.upper()}, {failed} failed (total {len(targets)}).",
        {"mode": mode, "processed": processed, "failed": failed, "total": len(targets)},
    )

    if failed > 0 and processed == 0:
        runtime.log(SLUG, "error", "All images failed to transcode.")

    return ctx
