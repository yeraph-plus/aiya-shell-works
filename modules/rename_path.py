"""Example module that renames the current working file or folder."""

from __future__ import annotations

from pathlib import Path


MODULE_META = {
    "slug": "rename-path",
    "name": "Rename Path",
    "description": "Rename the current working file or folder in place.",
    "core_version": "1.0.0",
    "tags": ["rename", "path"],
    "mode": ["file", "folder"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "prefix": {
            "type": "str",
            "title": "Prefix",
            "default": "",
        },
        "suffix": {
            "type": "str",
            "title": "Suffix",
            "default": "_processed",
        },
    },
}


def run(context, config):
    working_path = Path(context.working_path)
    prefix = config["prefix"]
    suffix = config["suffix"]

    if working_path.is_dir():
        new_name = f"{prefix}{working_path.name}{suffix}"
    else:
        suffixes = working_path.suffixes
        extension = "".join(suffixes)
        stem = working_path.name
        if extension:
            stem = stem[: -len(extension)]
        new_name = f"{prefix}{stem}{suffix}{extension}"

    target_path = working_path.with_name(new_name)
    if target_path.exists():
        context.events.log("rename-path", "error", f"目标路径已存在: {target_path}")
        raise RuntimeError(f"Target path already exists: {target_path}")

    working_path.rename(target_path)
    updated_context = context.clone(working_path=target_path)
    updated_context.shared["renamed_path"] = str(target_path)
    updated_context.events.log("rename-path", "success", "路径已重命名", {"new_path": str(target_path), "old_path": str(working_path)})
    return updated_context
