"""Example module that writes a simple workflow summary file."""

from __future__ import annotations

from pathlib import Path


MODULE_META = {
    "slug": "write-summary",
    "name": "Write Summary",
    "description": "Write a text summary of the current pipeline context.",
    "core_version": "1.0.0",
    "tags": ["summary", "report"],
    "mode": ["file", "folder", "none"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "str",
            "title": "Summary Filename",
            "default": "workflow-summary.txt",
        },
        "title": {
            "type": "str",
            "title": "Summary Title",
            "default": "Workflow Summary",
        },
    },
}


def run(context, config):
    summary_path = Path(context.output_dir) / config["filename"]
    lines = [
        config["title"],
        f"mode: {context.mode}",
        f"original_input: {context.original_input}",
        f"working_path: {context.working_path}",
    ]

    if context.extra_files:
        lines.append("extra_files:")
        lines.extend(f"- {item}" for item in context.extra_files)

    if context.shared:
        lines.append("shared:")
        for key in sorted(context.shared):
            lines.append(f"- {key}: {context.shared[key]}")

    rename_events = context.events.query(slug="rename-path")
    if rename_events:
        lines.append("rename_events:")
        for ev in rename_events:
            lines.append(f"- {ev.type}: {ev.text}")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    context.track_extra_file(summary_path)
    context.events.log("write-summary", "success", "摘要已写入", {"path": str(summary_path)})
    return context
