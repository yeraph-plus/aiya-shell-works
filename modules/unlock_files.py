"""检测并解除 dllhost、资源管理器、photo.exe 等进程对文件的占用锁定。"""

from __future__ import annotations

import ctypes
import platform
import subprocess
import sys
import time
from pathlib import Path


MODULE_META = {
    "slug": "unlock-files",
    "name": "解除文件占用",
    "core_version": "1.0.0",
    "tags": ["unlock", "system"],
    "mode": ["file", "folder"],
    "description": "检测并终止 dllhost、资源管理器、photo.exe 等进程对文件的占用锁定。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "auto_kill": {
            "type": "bool",
            "title": "自动终止占用进程",
            "default": True,
        },
        "max_retries": {
            "type": "int",
            "title": "最大重试次数",
            "default": 2,
            "min": 1,
            "max": 5,
        },
    },
}

_KNOWN_OFFENDERS = [
    "dllhost.exe",
    "explorer.exe",
    "Microsoft.Photos.exe",
    "PhotosApp.exe",
    "Photos.exe",
]
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _is_locked(path: Path) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ | GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        return True
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return False


def _find_locked_files(targets: list[Path]) -> list[Path]:
    locked: list[Path] = []
    for f in targets:
        if not f.is_file():
            continue
        if _is_locked(f):
            locked.append(f)
    return locked


def _kill_process(name: str) -> tuple[bool, str]:
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            ["taskkill", "/f", "/im", name],
            capture_output=True, text=True, timeout=15,
            creationflags=creation_flags,
        )
        if proc.returncode == 0:
            return True, f"已终止 {name}"
        return False, f"{name} 未运行或无法终止"
    except Exception as e:
        return False, f"终止 {name} 失败: {e}"


def _collect_targets(context) -> list[Path]:
    wp = Path(context.working_path)
    if context.mode == "file":
        return [wp] if wp.is_file() else []
    if wp.is_dir():
        return [f for f in wp.iterdir() if f.is_file()]
    return []


def run(context, config):
    if platform.system() != "Windows":
        context.events.log("unlock-files", "hint", "当前系统非 Windows，跳过解锁。")
        return context

    targets = _collect_targets(context)
    if not targets:
        context.events.log("unlock-files", "hint", "无可操作的文件。")
        return context

    auto_kill = config.get("auto_kill", True)
    max_retries = config.get("max_retries", 2)

    locked = _find_locked_files(targets)
    if not locked:
        context.events.log("unlock-files", "success", "未检测到文件占用，无需解锁。")
        return context

    context.events.log("unlock-files", "warning", f"检测到 {len(locked)} 个文件被占用，开始解锁...")
    for f in locked:
        context.events.log("unlock-files", "warning", f"  被占用: {f.name}")

    if not auto_kill:
        context.events.log("unlock-files", "error", "自动终止已禁用，解锁未执行。")
        return context

    for offender in _KNOWN_OFFENDERS:
        ok, msg = _kill_process(offender)
        if ok:
            context.events.log("unlock-files", "message", msg)

    for attempt in range(max_retries):
        time.sleep(0.5)
        remaining = _find_locked_files(targets)
        if not remaining:
            context.events.log("unlock-files", "success", "解锁成功，所有文件已释放。")
            return context
        if attempt < max_retries - 1:
            context.events.log(
                "unlock-files", "message",
                f"仍有 {len(remaining)} 个文件被占用，重试 ({attempt + 2}/{max_retries})...",
            )

    remaining = _find_locked_files(targets)
    freed = len(locked) - len(remaining)
    if remaining:
        context.events.log(
            "unlock-files", "warning",
            f"解锁完成: 释放 {freed} 个，仍有 {len(remaining)} 个被占用。",
        )
        for f in remaining:
            context.events.log("unlock-files", "error", f"  仍被占用: {f.name}")
    else:
        context.events.log(
            "unlock-files", "success",
            f"解锁完成，全部 {len(locked)} 个文件已释放。",
        )

    return context
