"""仅 Windows 工作，文件预处理组件，用于清除文件的只读/隐藏属性防止后续步骤操作被阻止。"""

from __future__ import annotations

import ctypes
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "strip-attributes",
    "name": "清除文件属性",
    "core_version": "2.0.0",
    "tags": ["attribute", "system"],
    "access": "read_write",
    "platforms": ["windows"],
    "description": "清除文件的只读/隐藏属性，确保后续操作不受文件属性限制。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "remove_readonly": {
            "type": "bool",
            "title": "去除只读属性",
            "default": True,
        },
        "remove_hidden": {
            "type": "bool",
            "title": "去除隐藏属性",
            "default": False,
        },
    },
}

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _get_attrs(path: str) -> int | None:
    try:
        val = ctypes.windll.kernel32.GetFileAttributesW(path)
        if val == INVALID_FILE_ATTRIBUTES:
            return None
        return val
    except Exception:
        return None


def _set_attrs(path: str, attrs: int) -> bool:
    try:
        return bool(ctypes.windll.kernel32.SetFileAttributesW(path, attrs))
    except Exception:
        return False


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    remove_readonly = cfg.get("remove_readonly", True)
    remove_hidden = cfg.get("remove_hidden", False)

    mask = 0
    if remove_readonly:
        mask |= FILE_ATTRIBUTE_READONLY
    if remove_hidden:
        mask |= FILE_ATTRIBUTE_HIDDEN

    if mask == 0:
        runtime.log("strip-attributes", "hint", "未选择任何待清除属性。")
        return ctx

    targets = ctx.files(recursive=False)
    if not targets:
        runtime.log("strip-attributes", "hint", "无可操作的文件。")
        return ctx

    processed = 0
    for target in targets:
        path_str = str(target.path)
        attrs = _get_attrs(path_str)
        if attrs is None or (attrs & mask) == 0:
            continue

        parts: list[str] = []
        if attrs & FILE_ATTRIBUTE_READONLY and remove_readonly:
            parts.append("只读")
        if attrs & FILE_ATTRIBUTE_HIDDEN and remove_hidden:
            parts.append("隐藏")

        new_attrs = attrs & ~mask
        if new_attrs == 0:
            new_attrs = FILE_ATTRIBUTE_NORMAL

        if _set_attrs(path_str, new_attrs):
            processed += 1
            runtime.log(
                "strip-attributes",
                "success",
                f"已清除属性: {target.name} ({', '.join(parts)})",
            )
        else:
            raise OSError(f"清除属性失败: {target.name}")

    if processed > 0:
        runtime.log(
            "strip-attributes",
            "message",
            f"属性清除完成: {processed} 个处理。",
        )
    else:
        runtime.log("strip-attributes", "hint", "未发现需要清除属性的文件。")

    return ctx
