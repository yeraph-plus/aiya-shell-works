"""Example module for cycle mode: counts processed files via shared context."""

from __future__ import annotations

from pathlib import Path

MODULE_META = {
    "slug": "cycle-counter",
    "name": "Cycle Counter",
    "description": "Count how many files have been processed in a cycle workflow via shared context.",
    "core_version": "1.0.0",
    "tags": ["cycle", "counter"],
    "mode": ["cycle"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "report_filename": {
            "type": "str",
            "title": "Report Filename",
            "default": "cycle-report.txt",
        },
    },
}


def run(context, config):
    count = context.shared.get("cycle_count", 0) + 1
    report_path = Path(context.output_dir) / config["report_filename"]

    lines: list[str] = []
    if report_path.exists():
        lines = report_path.read_text(encoding="utf-8").splitlines()

    working_info = (
        f"working_path is a {'directory' if context.is_dir else 'file'}: {context.working_path}"
    )
    lines.append(f"[{count}] {working_info}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    updated = context.clone(shared={**context.shared, "cycle_count": count})
    updated.track_extra_file(report_path)
    updated.events.log(
        "cycle-counter", "success",
        f"已处理第 {count} 个文件: {context.working_path.name}",
        {"count": count, "path": str(context.working_path)},
    )
    return updated
