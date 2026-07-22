"""write-summary — folder-friendly metadata writer.

Reads the cross-step contract from ``ctx.shared["renames"]`` written by
``rename-path`` and persists a human-readable summary back to
``output_dir``.  Demonstrates:

* upstream→downstream data channel via ctx.shared,
* multi-event emission (``success`` after writing, ``warning`` if no renames),
* ``track_extra_file`` for downstream consumers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "verify-write-summary",
    "name": "Verify — Write Summary",
    "description": "Write a small summary file capturing renames and current working_path.",
    "core_version": "2.0.0",
    "tags": ["example", "report"],
    "is_file_module": False,
    "parent": "verify-rename-path",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "title": "Summary filename", "default": "summary.txt"},
        "title": {"type": "str", "title": "Title", "default": "Summary"},
    },
    "required": ["filename"],
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    summary_path = Path(ctx.output_dir) / cfg["filename"]
    lines = [
        cfg["title"],
        f"is_file: {ctx.is_file}",
        f"is_dir: {ctx.is_dir}",
        f"original_input: {ctx.original_input}",
        f"working_path: {ctx.working_path}",
    ]
    renames = ctx.shared.get("renames", [])
    if renames:
        lines.append("renames:")
        for entry in renames:
            lines.append(f"- {entry.get('from_name', '?')} -> {entry.get('to_name', '?')}")
    else:
        runtime.log("verify-write-summary", "warning", "无 renames 可写入摘要（缺少上游 rename-path 步骤？）")

    if ctx.extra_files:
        lines.append("extra_files:")
        for fp in ctx.extra_files:
            lines.append(f"- {fp}")
    if ctx.shared:
        lines.append("shared keys: " + ", ".join(sorted(ctx.shared)))

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.track_extra_file(summary_path)
    runtime.log("verify-write-summary", "success", f"摘要已写入: {summary_path.name}", {"path": str(summary_path)})
    return ctx
