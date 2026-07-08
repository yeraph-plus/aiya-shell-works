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

    * ``working_path`` is a file → ``[working_path]`` (subject to ``extensions``)
    * ``working_path`` is a directory → its direct file children (subject to
      ``extensions``); nested subdirectories are not recursed here.
    * otherwise (e.g. nonexistent / mixed line input) → empty list.

    Modules no longer branch on ``ctx.atom``; the kernel derives behavior
    from filesystem state so the same module can run under any unit shape.
    """
    wp = Path(context.working_path)
    if wp.is_file():
        if extensions is not None and wp.suffix.lower() not in extensions:
            return []
        return [wp]
    if wp.is_dir():
        files = [f for f in wp.iterdir() if f.is_file()]
        if extensions is not None:
            files = [f for f in files if f.suffix.lower() in extensions]
        return files
    return []


def make_unique_path(
    target: Path,
    *,
    separator: str = "_",
    parenthetical: bool = False,
) -> Path:
    """Return *target* unchanged, or a sibling with a unique name."""

    if not target.exists():
        return target
    parent = target.parent
    suffix = "".join(target.suffixes)
    stem = target.name
    if suffix:
        stem = stem[: -len(suffix)]
    if parenthetical:
        fmt = "{stem} ({counter}){suffix}"
    else:
        fmt = "{stem}{separator}{counter}{suffix}"
    for counter in range(1, 10001):
        candidate = parent / fmt.format(stem=stem, counter=counter, suffix=suffix, separator=separator)
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名: {target} (尝试 10000 次)")


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
