"""line-echo — atom=line + scope=per-unit example module.

Each non-empty text line becomes its own unit, with the raw line stored in
``ctx.shared["input_line"]`` (instruction injected by the executor).  The
module writes a per-line file whose name hashes from the line content to
avoid collisions in ``output_dir``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MODULE_META = {
    "slug": "verify-line-echo",
    "name": "Verify — Line Echo",
    "description": "Echo a single input text line into a file in output_dir.",
    "core_version": "2.0.0",
    "tags": ["example", "echo"],
    "atom": ["line"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "prefix": {"type": "str", "title": "File prefix", "default": "task"},
        "extension": {"type": "str", "title": "File extension", "default": ".txt"},
    },
}


def run(ctx: "Any", cfg: "Any", runtime: "Any") -> "Any":
    line = ctx.shared.get("input_line", "")
    ident = f"{abs(hash(line)) & 0xFFFF:04x}"
    filename = f"{cfg['prefix']}_{ident}{cfg['extension']}"
    target = Path(ctx.output_dir) / filename
    target.write_text(line + "\n", encoding="utf-8")
    ctx.track_extra_file(target)
    runtime.log("verify-line-echo", "success",
                f"已写入: {line[:40]!r} -> {filename}",
                {"line": line, "file": str(target)})
    return ctx.clone(working_path=target)