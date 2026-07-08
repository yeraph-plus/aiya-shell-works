"""create-text-file — minimal no-input example module.

Creates a single text file in ``output_dir``.  Demonstrates the bare-minimum
``run(ctx, cfg, runtime)`` contract for the no-input path (one empty unit,
no input needed).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "verify-create-text-file",
    "name": "Verify — Create Text File",
    "description": "Write a single text file into the output directory.",
    "core_version": "2.0.0",
    "tags": ["example", "io"],
    "is_file_module": False,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {"type": "str", "title": "Filename", "default": "hello.txt"},
        "content": {"type": "str", "title": "Content", "default": "hello"},
    },
    "required": ["filename"],
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    target = Path(ctx.output_dir) / cfg["filename"]
    target.write_text(cfg["content"], encoding="utf-8")
    runtime.log("verify-create-text-file", "success", f"已生成 {target.name}", {"path": str(target)})
    return ctx.clone(working_path=target, extra_files=[*ctx.extra_files, target])
