"""Resize images to a max width and overlay text or image watermarks."""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.files import WorkspaceFile
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "image-resize-watermark",
    "name": "Image Resize & Watermark",
    "core_version": "2.0.0",
    "tags": ["image", "resize", "watermark"],
    "access": "read_write",
    "platforms": None,
    "description": "Resize images to a maximum width and overlay text or image watermarks.",
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
    }
)

_VALID_POSITIONS = frozenset(
    {
        "top-left",
        "tl",
        "top-right",
        "tr",
        "bottom-left",
        "bl",
        "bottom-right",
        "br",
        "center",
    }
)

_POSITION_ALIASES: dict[str, str] = {
    "tl": "top-left",
    "tr": "top-right",
    "bl": "bottom-left",
    "br": "bottom-right",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "max_width_in": {
            "type": "int",
            "title": "Maximum Long Edge",
            "description": (
                "Maximum length of the longer edge in pixels. "
                "Shorter edge scales proportionally. Set to 0 to skip resizing."
            ),
            "default": 1920,
            "min": 0,
        },
        "quality": {
            "type": "int",
            "title": "Output Quality",
            "description": "JPEG/WebP/AVIF output quality (1-100).",
            "default": 85,
            "min": 1,
            "max": 100,
        },
        "watermark_enabled": {
            "type": "bool",
            "title": "Enable Watermark",
            "description": "Toggle watermark overlay on or off.",
            "default": False,
        },
        "watermark_content": {
            "type": "str",
            "title": "Watermark Content",
            "description": (
                "Text string or image file path. Auto-detected: if the value is a path to an existing image "
                "file it will be used as an image watermark; otherwise it is rendered as text."
            ),
            "default": "",
        },
        "watermark_opacity": {
            "type": "float",
            "title": "Watermark Opacity",
            "description": "Opacity from 0.0 (fully transparent) to 1.0 (fully opaque).",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
        },
        "watermark_positions": {
            "type": "str",
            "title": "Watermark Positions",
            "description": (
                "Comma-separated positions: top-left, top-right, bottom-left, bottom-right, center. "
                "Short forms: tl, tr, bl, br. Use 'random' to pick one random corner per image."
            ),
            "default": "bottom-right",
        },
        "watermark_margin": {
            "type": "int",
            "title": "Edge Margin",
            "description": "Distance from the edge in pixels.",
            "default": 20,
            "min": 0,
        },
        "output_format": {
            "type": "select",
            "title": "Output Format",
            "description": "Convert output to a fixed format. 'keep' preserves the original format.",
            "options": [
                {"value": "keep", "label": "Keep Original"},
                {"value": "jpg", "label": "JPEG"},
                {"value": "avif", "label": "AVIF"},
            ],
            "default": "keep",
        },
        "watermark_font_size": {
            "type": "int",
            "title": "Font Size",
            "description": "Font size for text watermarks. Ignored for image watermarks.",
            "default": 36,
            "min": 1,
        },
        "watermark_scale": {
            "type": "int",
            "title": "Watermark Scale (%)",
            "description": (
                "Image watermark size as a percentage of the longer image dimension (1-100). "
                "Ignored for text watermarks."
            ),
            "default": 5,
            "min": 1,
            "max": 100,
        },
    },
    "required": ["max_width_in", "quality"],
}

SLUG = MODULE_META["slug"]


def _is_image_path(value: str) -> bool:
    p = Path(value)
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS


def _parse_positions(raw: str) -> list[str]:
    parts = [s.strip().lower() for s in raw.split(",") if s.strip()]
    resolved: list[str] = []
    for part in parts:
        if part == "random":
            resolved.append(random.choice(["top-left", "top-right", "bottom-left", "bottom-right"]))
        elif part in _VALID_POSITIONS:
            resolved.append(_POSITION_ALIASES.get(part, part))
        else:
            raise ValueError(f"Invalid watermark position: {part!r}")
    return resolved


def _calc_position(
    img_w: int,
    img_h: int,
    wm_w: int,
    wm_h: int,
    position: str,
    margin: int,
) -> tuple[int, int]:
    if position == "center":
        return ((img_w - wm_w) // 2, (img_h - wm_h) // 2)
    if position == "top-left":
        return (margin, margin)
    if position == "top-right":
        return (img_w - wm_w - margin, margin)
    if position == "bottom-left":
        return (margin, img_h - wm_h - margin)
    if position == "bottom-right":
        return (img_w - wm_w - margin, img_h - wm_h - margin)
    return (margin, margin)


def _resolve_font(font_size: int) -> tuple[ImageFont.FreeTypeFont, str]:
    import sys

    if sys.platform == "win32":
        candidates = [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyh.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
            Path("/Library/Fonts/Arial.ttf"),
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
            Path("/usr/share/fonts/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        ]

    for fp in candidates:
        if fp.is_file():
            try:
                return ImageFont.truetype(str(fp), font_size), str(fp)
            except Exception:  # noqa: S110
                pass

    try:
        return ImageFont.truetype("arial.ttf", font_size), "arial.ttf (system)"
    except Exception:  # noqa: S110
        pass

    return ImageFont.load_default(), "PIL default"


def _collect_targets(ctx: PipelineContext, runtime: PipelineRuntime) -> list[WorkspaceFile]:
    from core.tools import collect_file_targets

    targets = collect_file_targets(ctx, extensions=SUPPORTED_EXTENSIONS)
    if not targets:
        runtime.log(SLUG, "message", "No supported image files found, skipping.")
    return targets


def _make_watermark_layer(
    img_width: int,
    img_height: int,
    use_image: bool,
    content: str,
    scale_pct: int,
    opacity: float,
    font: ImageFont.FreeTypeFont | None,
) -> Image.Image | None:
    ref = max(img_width, img_height)

    if use_image:
        wm = Image.open(content).convert("RGBA")
        target_w = int(ref * scale_pct / 100)
        ratio = target_w / wm.width
        target_h = int(wm.height * ratio)
        wm = wm.resize((max(1, target_w), max(1, target_h)), Image.LANCZOS)
        if opacity < 1.0:
            r, g, b, a = wm.split()
            a = a.point(lambda v: int(v * opacity))
            wm = Image.merge("RGBA", (r, g, b, a))
        return wm

    if font is None:
        return None

    temp = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp)
    bbox = draw.textbbox((0, 0), content, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    pad = 4
    wm = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(wm)
    alpha = int(255 * opacity)
    draw.text((pad, pad), content, font=font, fill=(255, 255, 255, alpha))
    return wm


_OUTPUT_EXT_MAP: dict[str, str] = {
    "jpg": ".jpg",
    "avif": ".avif",
}


def _needs_quality_param(ext: str) -> bool:
    return ext in (".jpg", ".jpeg", ".webp", ".avif")


def run(
    ctx: PipelineContext,
    cfg: dict[str, Any],
    runtime: PipelineRuntime,
) -> PipelineContext | None:
    targets = _collect_targets(ctx, runtime)
    if not targets:
        return ctx

    max_width_in = int(cfg.get("max_width_in", 1920))
    quality = int(cfg.get("quality", 85))
    output_format = str(cfg.get("output_format", "keep"))
    watermark_enabled = bool(cfg.get("watermark_enabled", False))
    watermark_content = str(cfg.get("watermark_content", "")).strip()
    watermark_opacity = float(cfg.get("watermark_opacity", 0.5))
    watermark_positions_str = str(cfg.get("watermark_positions", "bottom-right")).strip()
    watermark_margin = int(cfg.get("watermark_margin", 20))
    watermark_font_size = int(cfg.get("watermark_font_size", 36))
    watermark_scale = int(cfg.get("watermark_scale", 15))

    watermark_active = watermark_enabled and watermark_content

    positions: list[str] = []
    use_image_watermark = False
    font: ImageFont.FreeTypeFont | None = None
    font_desc = ""

    if watermark_active:
        try:
            positions = _parse_positions(watermark_positions_str)
        except ValueError as e:
            raise ValueError(str(e)) from e

        if _is_image_path(watermark_content):
            use_image_watermark = True
            runtime.log(
                SLUG,
                "message",
                f"Using image watermark: {watermark_content}",
                {"watermark_path": watermark_content},
            )
        else:
            font, font_desc = _resolve_font(watermark_font_size)
            runtime.log(
                SLUG,
                "message",
                f"Using text watermark: {watermark_content!r} (font: {font_desc})",
                {"font": font_desc},
            )

    runtime.log(
        SLUG,
        "message",
        f"Starting processing: {len(targets)} image(s)",
        {"total": len(targets)},
    )

    processed = 0
    original_current = ctx.current.path
    for target in targets:
        runtime.log(SLUG, "message", f"Processing: {target.name}")
        output_file = None
        try:
            img = Image.open(target.path)
            img = ImageOps.exif_transpose(img)

            if max_width_in > 0 and max(img.width, img.height) > max_width_in:
                ratio = max_width_in / max(img.width, img.height)
                new_w = int(img.width * ratio)
                new_h = int(img.height * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            if img.mode in ("P", "PA"):
                img = img.convert("RGBA")
            elif img.mode not in ("RGB", "RGBA", "L", "LA"):
                img = img.convert("RGB")

            if watermark_active and positions:
                wm_layer = _make_watermark_layer(
                    img.width,
                    img.height,
                    use_image_watermark,
                    watermark_content,
                    watermark_scale,
                    watermark_opacity,
                    font,
                )
                if wm_layer:
                    if img.mode != "RGBA":
                        img = img.convert("RGBA")
                    for pos in positions:
                        x, y = _calc_position(
                            img.width,
                            img.height,
                            wm_layer.width,
                            wm_layer.height,
                            pos,
                            watermark_margin,
                        )
                        img.paste(wm_layer, (x, y), wm_layer)

            suffix = target.path.suffix if output_format == "keep" else _OUTPUT_EXT_MAP[output_format]
            desired_name = target.path.with_suffix(suffix).name
            output_file = ctx.allocate_file(desired_name)

            if output_format == "jpg":
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                img.save(output_file.path, optimize=True, quality=quality)
            elif output_format == "avif":
                img.save(output_file.path, optimize=True, quality=quality)
            else:
                save_kwargs: dict[str, Any] = {"optimize": True}
                target_ext = target.path.suffix.lower()
                if _needs_quality_param(target_ext):
                    save_kwargs["quality"] = quality
                    if img.mode == "RGBA":
                        img = img.convert("RGB")
                img.save(output_file.path, **save_kwargs)

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
        f"Processed {processed} of {len(targets)} image(s).",
        {"processed": processed, "total": len(targets)},
    )
    return ctx
