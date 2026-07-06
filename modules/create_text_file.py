"""Example module that creates a text file inside the output directory."""

from __future__ import annotations

from pathlib import Path


MODULE_META = {
    "slug": "create-text-file",
    "name": "Create Text File",
    "description": "Create a text file for none-mode workflows.",
    "core_version": "1.0.0",
    "tags": ["generate", "text"],
    "mode": ["none"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "str",
            "title": "Filename",
            "default": "generated.txt",
        },
        "content": {
            "type": "str",
            "title": "Content",
            "default": "hello from shell worker",
        },
        "subfolder": {
            "type": "str",
            "title": "Subfolder",
            "default": "",
        },
    },
}


def run(context, config):
    base_dir = Path(context.output_dir)
    subfolder = config["subfolder"].strip()
    if subfolder:
        base_dir = base_dir / subfolder
        base_dir.mkdir(parents=True, exist_ok=True)

    target_path = base_dir / config["filename"]
    target_path.write_text(config["content"], encoding="utf-8")

    updated_context = context.clone(working_path=target_path)
    updated_context.track_extra_file(target_path)
    updated_context.shared["created_file"] = str(target_path)
    updated_context.events.log("create-text-file", "success", "文件已生成", {"path": str(target_path)})
    return updated_context
