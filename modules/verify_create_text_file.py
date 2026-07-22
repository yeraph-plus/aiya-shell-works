"""create-text-file — minimal no-input example module.

Creates a single text file in ``output_dir``.  Demonstrates the bare-minimum
``run(ctx, cfg, runtime)`` contract for the no-input path (one empty unit,
no input needed).
"""

from __future__ import annotations

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
    "access": "read_write",
    "platforms": None,
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
    target = ctx.create_file(cfg["filename"], cfg["content"])
    runtime.log("verify-create-text-file", "success", f"已生成 {target.name}", {"path": str(target.path)})
    return ctx
