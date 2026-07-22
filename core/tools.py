"""Shared utility functions for workflow modules.

All functions are pure Python — no PySide6 or GUI dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import PipelineContext
    from .runtime import PipelineRuntime


def collect_file_targets(
    context: PipelineContext,
    *,
    extensions: frozenset[str] | None = None,
) -> list[Path]:
    """Return files this module should process, derived from filesystem state.

    * the current resource is a file → that file (subject to ``extensions``)
    * the current resource is a directory → its direct tracked file children
      (subject to ``extensions``); nested subdirectories are not recursed here.
    * otherwise (e.g. nonexistent / mixed line input) → empty list.

    Modules no longer branch on ``ctx.atom``; the kernel derives behavior
    from filesystem state so the same module can run under any unit shape.
    """
    entries = context.files(recursive=False)
    files = [entry.path for entry in entries if entry.is_file]
    if context.current.is_file and context.current.path not in files:
        files.insert(0, context.current.path)
    if extensions is not None:
        files = [path for path in files if path.suffix.lower() in extensions]
    return files


def parse_extension_set(raw: str) -> frozenset[str]:
    """Split whitespace-delimited *raw* into a lower-cased extension set."""

    return frozenset(e.strip().lower().lstrip(".") for e in raw.split() if e.strip())


def ensure_pty_available(runtime: PipelineRuntime, slug: str) -> bool:
    """Check that a PTY backend is importable.  Logs an error if not."""

    import sys

    if sys.platform == "win32":
        try:
            import winpty  # noqa: F401

            return True
        except ImportError:
            runtime.log(slug, "warning", "pywinpty 未安装，使用子进程回退（无交互 stdin）。", {"backend": "subprocess"})
            return False
    # POSIX pty is part of stdlib; subprocess is implicit fallback.
    return True
