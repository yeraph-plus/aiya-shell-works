"""标准化文件后缀：小写 + 映射变体到标准形式。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "normalize-extensions",
    "name": "标准化文件后缀",
    "core_version": "2.0.0",
    "tags": ["normalize", "extension"],
    "is_file_module": True,
    "description": "统一文件扩展名为小写标准后缀，如 jpeg→jpg、JPG→jpg、tiff→tif。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "lowercase": {
            "type": "bool",
            "title": "强制转为小写",
            "default": True,
        },
    },
}

_EXTENSION_MAP = {
    ".jpeg": ".jpg",
    ".jpe": ".jpg",
    ".jfif": ".jpg",
    ".tiff": ".tif",
    ".htm": ".html",
}


def _resolve_new_suffix(suffix: str, lowercase: bool) -> str:
    if lowercase:
        suffix = suffix.lower()
    return _EXTENSION_MAP.get(suffix, suffix)


def _make_unique(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _collect_targets(ctx: PipelineContext) -> list[Path]:
    wp = Path(ctx.working_path)
    if wp.is_file():
        return [wp]
    if wp.is_dir():
        return [f for f in wp.iterdir() if f.is_file()]
    return []


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    lowercase = cfg.get("lowercase", True)

    targets = _collect_targets(ctx)
    if not targets:
        runtime.log("normalize-extensions", "hint", "无可操作的文件。")
        return ctx

    renamed = 0
    updated_working_path = None

    for f in targets:
        current_suffix = f.suffix
        new_suffix = _resolve_new_suffix(current_suffix, lowercase)
        if new_suffix == current_suffix:
            continue

        new_path = _make_unique(f.with_suffix(new_suffix))
        try:
            f.rename(new_path)
            renamed += 1
            runtime.log(
                "normalize-extensions",
                "success",
                f"已标准化: {f.name} → {new_path.name}",
            )
            if ctx.is_file:
                updated_working_path = new_path
        except OSError as e:
            runtime.log(
                "normalize-extensions",
                "error",
                f"重命名失败: {f.name} ({e})",
            )

    if renamed > 0:
        runtime.log(
            "normalize-extensions",
            "message",
            f"后缀标准化完成: {renamed} 个文件。",
        )
    else:
        runtime.log("normalize-extensions", "message", "所有文件后缀已为标准格式。")

    if updated_working_path is not None:
        return ctx.clone(working_path=updated_working_path)
    return ctx
