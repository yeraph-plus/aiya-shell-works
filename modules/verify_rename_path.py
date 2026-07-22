"""rename-path — file/folder path rename example module.

Renames a working copy in place and records the rename in
``ctx.shared["renames"]`` so downstream modules (e.g. ``write-summary``) can
report it via the cross-step data channel.  ``clone(working_path=...)`` is
used to keep the downstream steps pointed at the new path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "verify-rename-path",
    "name": "Verify — Rename Path",
    "description": "Prepend/append a prefix or suffix to the working path.",
    "core_version": "2.0.0",
    "tags": ["example", "rename"],
    "is_file_module": True,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "prefix": {"type": "str", "title": "Prefix", "default": ""},
        "suffix": {"type": "str", "title": "Suffix", "default": "_done"},
    },
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    src = Path(ctx.working_path)
    new = src.with_name(f"{cfg.get('prefix', '')}{src.name}{cfg.get('suffix', '')}")
    if src == new:
        runtime.log("verify-rename-path", "hint", f"无变化: {src.name}")
        return ctx
    src.rename(new)
    renames = list(ctx.shared.get("renames", []))
    renames.append(
        {
            "from": str(src),
            "to": str(new),
            "from_name": src.name,
            "to_name": new.name,
        }
    )
    runtime.log("verify-rename-path", "success", f"{src.name} -> {new.name}", {"old": str(src), "new": str(new)})
    return ctx.clone(working_path=new, shared={**ctx.shared, "renames": renames})
