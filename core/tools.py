"""Shared utility functions for workflow modules.

All functions are pure Python — no PySide6 or GUI dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import PipelineContext
    from .files import WorkspaceFile
    from .runtime import PipelineRuntime


def collect_file_targets(
    context: PipelineContext,
    *,
    extensions: frozenset[str] | None = None,
) -> list[WorkspaceFile]:
    """Return files this module should process, derived from filesystem state.

    * the current resource is a file → that file (subject to ``extensions``)
    * the current resource is a directory → its direct tracked file children
      (subject to ``extensions``); nested subdirectories are not recursed here.
    * otherwise (e.g. nonexistent / mixed line input) → empty list.

    Modules no longer branch on ``ctx.atom``; the kernel derives behavior
    from filesystem state so the same module can run under any unit shape.
    """
    files = [entry for entry in context.files(recursive=False) if entry.is_file]
    if context.current.is_file and all(entry.path != context.current.path for entry in files):
        files.insert(0, context.current)
    if extensions is not None:
        normalized = frozenset(extension if extension.startswith(".") else f".{extension}" for extension in extensions)
        files = [entry for entry in files if entry.path.suffix.lower() in normalized]
    return files


def parse_extension_set(raw: str) -> frozenset[str]:
    """Split whitespace-delimited *raw* into a lower-cased extension set."""

    return frozenset(f".{extension.strip().lower().lstrip('.')}" for extension in raw.split() if extension.strip())


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
