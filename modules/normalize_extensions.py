"""文件预处理组件，标准化文件后缀：小写 + 映射变体到标准形式。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "normalize-extensions",
    "name": "标准化文件后缀",
    "core_version": "2.0.0",
    "tags": ["normalize", "extension"],
    "access": "read_write",
    "platforms": None,
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


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    lowercase = cfg.get("lowercase", True)

    targets = ctx.files(recursive=False)
    if not targets:
        runtime.log("normalize-extensions", "hint", "无可操作的文件。")
        return ctx

    renamed = 0
    for target in targets:
        current_suffix = target.path.suffix
        new_suffix = _resolve_new_suffix(current_suffix, lowercase)
        if new_suffix == current_suffix:
            continue
        original_name = target.name
        renamed_target = target.rename(target.path.with_suffix(new_suffix).name)
        renamed += 1
        runtime.log(
            "normalize-extensions",
            "success",
            f"已标准化: {original_name} → {renamed_target.name}",
        )

    if renamed > 0:
        runtime.log(
            "normalize-extensions",
            "message",
            f"后缀标准化完成: {renamed} 个文件。",
        )
    else:
        runtime.log("normalize-extensions", "message", "所有文件后缀已为标准格式。")

    return ctx
