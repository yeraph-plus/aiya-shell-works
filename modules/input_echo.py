"""Example module for input mode: echoes each input line to a separate file."""

from __future__ import annotations

from pathlib import Path

MODULE_META = {
    "slug": "input-echo",
    "name": "Input Echo",
    "description": "Echo each input text line into a file in the output directory.",
    "core_version": "1.0.0",
    "tags": ["input", "echo"],
    "mode": ["input"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "file_prefix": {
            "type": "str",
            "title": "File Prefix",
            "default": "task",
        },
        "extension": {
            "type": "str",
            "title": "File Extension",
            "default": ".txt",
        },
    },
}


def run(context, config):
    input_line = context.shared.get("input_line", "")

    import uuid
    unique_id = uuid.uuid4().hex[:6]
    filename = f"{config['file_prefix']}_{unique_id}{config['extension']}"
    target = Path(context.output_dir) / filename
    target.write_text(input_line + "\n", encoding="utf-8")

    updated = context.clone(working_path=target)
    updated.track_extra_file(target)
    updated.events.log(
        "input-echo", "success",
        f"已写入: {input_line[:40]}",
        {"file": str(target)},
    )
    return updated
