"""cycle-counter — file path input + scope=shared example module.

The classic "all inputs share one context" case.  Because scope=shared
merges every input file into ``output_dir`` (the working tree), this module
simply rglobs the working tree once and counts files.  No cross-unit shared
accumulation hack is needed — the executor hands us a single ctx already.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "cycle-counter",
    "name": "Cycle Counter",
    "description": "Count files inside the merged working tree and write a report.",
    "core_version": "2.0.0",
    "tags": ["example", "counter"],
    "is_file_module": True,
    "scope": 0,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "report_filename": {"type": "str", "title": "Report Filename", "default": "count.txt"},
    },
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    files = sorted(p for p in Path(ctx.working_path).rglob("*") if p.is_file())
    for i, fp in enumerate(files, 1):
        runtime.log("cycle-counter", "success", f"{i}: {fp.name}", {"index": i, "path": str(fp)})

    # Avoid re-counting our own report by writing last.
    report = Path(ctx.working_path) / cfg["report_filename"]
    report.write_text(f"count={len(files)}\n", encoding="utf-8")
    ctx.track_extra_file(report)
    runtime.log(
        "cycle-counter", "message", f"统计完成: count={len(files)}", {"count": len(files), "report": str(report)}
    )
    return ctx
