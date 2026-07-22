"""write-summary — folder-friendly metadata writer.

Reads the cross-step contract from ``ctx.shared["renames"]`` written by
``rename-path`` and persists a human-readable summary back to
the unit workspace.  Demonstrates:

* upstream→downstream data channel via ctx.shared,
* multi-event emission (``success`` after writing, ``warning`` if no renames),
* collision-safe output creation through ``ctx.create_file()``.
"""

from __future__ import annotations

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
    "access": "read_write",
    "platforms": None,
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
    lines = [
        cfg["title"],
        f"is_file: {ctx.current.is_file}",
        f"is_dir: {ctx.current.is_dir}",
        f"original_input: {ctx.original_input}",
        f"working_path: {ctx.current.path}",
    ]
    renames = ctx.shared.get("renames", [])
    if renames:
        lines.append("renames:")
        for entry in renames:
            lines.append(f"- {entry.get('from_name', '?')} -> {entry.get('to_name', '?')}")
    else:
        runtime.log("verify-write-summary", "warning", "无 renames 可写入摘要（缺少上游 rename-path 步骤？）")

    if ctx.shared:
        lines.append("shared keys: " + ", ".join(sorted(ctx.shared)))

    summary = ctx.create_file(cfg["filename"], "\n".join(lines) + "\n")
    runtime.log(
        "verify-write-summary",
        "success",
        f"摘要已写入: {summary.name}",
        {"path": str(summary.path)},
    )
    return ctx
