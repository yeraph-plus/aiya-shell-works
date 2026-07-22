"""rename-path — file/folder path rename example module.

Renames a working copy in place and records the rename in
``ctx.shared["renames"]`` so downstream modules (e.g. ``write-summary``) can
report it via the cross-step data channel.  The workspace keeps
``ctx.current`` pointed at the renamed resource.
"""

from __future__ import annotations

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
    "access": "read_write",
    "platforms": None,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "prefix": {"type": "str", "title": "Prefix", "default": ""},
        "suffix": {"type": "str", "title": "Suffix", "default": "_done"},
    },
}


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    src = ctx.current
    name = f"{cfg.get('prefix', '')}{src.name}{cfg.get('suffix', '')}"
    if src.name == name:
        runtime.log("verify-rename-path", "hint", f"无变化: {src.name}")
        return ctx
    renamed = src.rename(name)
    renames = list(ctx.shared.get("renames", []))
    renames.append(
        {
            "from": str(src.path),
            "to": str(renamed.path),
            "from_name": src.name,
            "to_name": renamed.name,
        }
    )
    runtime.log(
        "verify-rename-path",
        "success",
        f"{src.name} -> {renamed.name}",
        {"old": str(src.path), "new": str(renamed.path)},
    )
    return ctx.clone(shared={**ctx.shared, "renames": renames})
