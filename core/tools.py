"""Shared utility functions for workflow modules.

All functions are pure Python — no PySide6 or GUI dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import PipelineContext


def ensure_pywinpty(context: "PipelineContext", slug: str) -> bool:
    """Check that pywinpty is importable.

    Returns True if available.  Otherwise logs an error to *context.events*
    and returns False.
    """
    try:
        import winpty  # noqa: F401
    except ImportError:
        context.events.log(
            slug,
            "error",
            "pywinpty 未安装，无法使用终端，请运行 pip install pywinpty。",
        )
        return False
    return True


def collect_file_targets(
    context: "PipelineContext",
    *,
    extensions: frozenset[str] | None = None,
) -> list[Path]:
    """Return the list of file paths this module should process.

    In *file* mode returns the single working path (if it is a file).
    In *folder* mode returns direct children (if *extensions* is supplied
    the result is also filtered to files whose suffix matches).
    """
    wp = Path(context.working_path)
    if context.mode == "file":
        if not wp.is_file():
            return []
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
    """Return *target* unchanged, or a sibling with a unique name.

    The de-duplication strategy is controlled by *separator* and
    *parenthetical*:

    * ``separator="_"``, ``parenthetical=False`` → ``stem_1.ext``
    * ``separator=" "``, ``parenthetical=True`` → ``stem (1).ext``

    An infinite-loop guard is included (max 10 000 attempts).
    """
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
        candidate = parent / fmt.format(
            stem=stem, counter=counter, suffix=suffix, separator=separator
        )
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"无法生成唯一文件名: {target} (尝试 10000 次)")


def parse_extension_set(raw: str) -> frozenset[str]:
    """Split whitespace-delimited *raw* into a lower-cased extension set."""
    return frozenset(
        e.strip().lower().lstrip(".") for e in raw.split() if e.strip()
    )
