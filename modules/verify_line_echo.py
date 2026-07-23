"""line-echo — line-input + per-unit example module.

Each non-empty text line becomes its own unit, with the raw line stored in
``ctx.shared["input_line"]`` (injected by the executor).  The module writes
a per-line file whose name hashes from the line content to avoid
collisions in ``output_dir``.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "verify-line-echo",
    "name": "Verify — Line Echo",
    "description": "Echo a single input text line into a file in output_dir.",
    "core_version": "2.0.0",
    "tags": ["example", "echo"],
    "access": "read_write",
    "platforms": None,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "prefix": {"type": "str", "title": "File prefix", "default": "task"},
        "extension": {"type": "str", "title": "File extension", "default": ".txt"},
    },
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    line = ctx.shared.get("input_line", "")
    ident = hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]
    filename = f"{cfg['prefix']}_{ident}{cfg['extension']}"
    target = ctx.create_file(filename, line + "\n")
    runtime.log(
        "verify-line-echo", "success", f"已写入: {line[:40]!r} -> {filename}", {"line": line, "file": str(target.path)}
    )
    return ctx
