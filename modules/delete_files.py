"""按匹配模式硬删除无用文件。"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

MODULE_META = {
    "slug": "delete-files",
    "name": "删除无用文件",
    "core_version": "2.0.0",
    "tags": ["cleanup", "delete"],
    "atom": ["file", "folder"],
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


def _collect_targets(ctx: Any) -> list[Path]:
    wp = Path(ctx.working_path)
    if ctx.atom == "file":
        return [wp] if wp.is_file() else []
    if wp.is_dir():
        return [f for f in wp.iterdir() if f.is_file()]
    return []


def run(ctx: Any, cfg: Any, runtime: Any) -> Any:
    patterns_str = cfg.get("patterns", "")
    patterns = _parse_patterns(patterns_str)

    if not patterns:
        runtime.log("delete-files", "hint", "匹配模式为空，跳过删除。")
        return ctx

    targets = _collect_targets(ctx)
    if not targets:
        runtime.log("delete-files", "hint", "无可操作的文件。")
        return ctx

    deleted = 0
    failed = 0

    for f in targets:
        matched = any(fnmatch.fnmatch(f.name.lower(), p.lower()) for p in patterns)
        if not matched:
            continue

        try:
            f.unlink()
            deleted += 1
            runtime.log("delete-files", "success", f"已删除: {f.name}")
        except OSError as e:
            failed += 1
            runtime.log("delete-files", "error", f"删除失败: {f.name} ({e})")

    if deleted > 0:
        runtime.log(
            "delete-files",
            "message",
            f"删除完成: {deleted} 个文件已删除, {failed} 个失败。",
        )
    elif failed == 0:
        runtime.log("delete-files", "message", "未匹配到需要删除的文件。")

    return ctx
