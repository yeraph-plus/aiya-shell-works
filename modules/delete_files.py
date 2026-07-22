"""按匹配模式硬删除无用文件。"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "delete-files",
    "name": "删除无用文件",
    "core_version": "2.0.0",
    "tags": ["cleanup", "delete"],
    "access": "read_write",
    "platforms": None,
    "description": "按 glob 模式匹配并硬删除 .txt/.url/.html/Thumbs.db/desktop.ini 等无用文件。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "patterns": {
            "type": "str",
            "title": "匹配模式",
            "default": "*.txt *.url *.html *.htm Thumbs.db desktop.ini",
            "description": "空格分隔的 glob 模式，匹配的文件将被永久删除。",
        },
    },
}


def _parse_patterns(patterns_str: str) -> list[str]:
    return [p.strip() for p in patterns_str.split() if p.strip()]


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    patterns_str = cfg.get("patterns", "")
    patterns = _parse_patterns(patterns_str)

    if not patterns:
        runtime.log("delete-files", "hint", "匹配模式为空，跳过删除。")
        return ctx

    targets = ctx.files(recursive=False)
    if not targets:
        runtime.log("delete-files", "hint", "无可操作的文件。")
        return ctx

    deleted = 0
    for target in targets:
        matched = any(fnmatch.fnmatch(target.name.lower(), pattern.lower()) for pattern in patterns)
        if not matched:
            continue
        name = target.name
        target.delete()
        deleted += 1
        runtime.log("delete-files", "success", f"已删除: {name}")

    if deleted > 0:
        runtime.log(
            "delete-files",
            "message",
            f"删除完成: {deleted} 个文件已删除。",
        )
    else:
        runtime.log("delete-files", "message", "未匹配到需要删除的文件。")

    return ctx
