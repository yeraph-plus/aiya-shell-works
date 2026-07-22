"""Input plan resolution: turn CLI inputs into a unit-ready description.

The kernel derives its execution shape from the actual inputs (no YAML
``atom`` constraint):

1. ``files`` non-empty → path inputs.  ``recurse`` decides whether dir
   inputs expand to contained files (preserving ``source_root``) or stay
   as whole-folder units.
2. ``files`` empty, ``lines_text``/``lines_file`` supplied → line inputs.
3. All empty → a single empty unit (no input).

The internal ``InputPlan.kind`` is one of ``"path"`` / ``"line"`` / ``"none"``
and is consumed only by the executor / WorkingCopier to build units.  It is
NOT exposed to modules as a hard constraint — modules read ``ctx.is_file`` /
``ctx.is_dir`` / ``ctx.shared["input_line"]`` instead.
"""

from __future__ import annotations

import glob
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .exceptions import PipelineExecutionError

_PLAN_KINDS = ("path", "line", "none")
_GLOB_MAGIC = ("*", "?", "[")


@dataclass(frozen=True, slots=True)
class InputPlan:
    """The outcome of CLI / GUI input resolution.

    ``kind`` is an internal kernel hint for unit construction; not exported
    as a module-facing constraint.
    """

    kind: str
    recurse: bool = False
    files: tuple[Path, ...] = field(default_factory=tuple)
    lines: tuple[str, ...] = field(default_factory=tuple)


def _read_lines_file(path: str | Path) -> list[str]:
    p = Path(path)
    if str(path) == "-":
        return sys.stdin.read().splitlines()
    if not p.exists():
        raise PipelineExecutionError(f"--lines-file 指定的文件不存在: {p}")
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def resolve_input(
    *,
    files: list[str | Path] | None = None,
    recurse: bool = False,
    lines_text: str | None = None,
    lines_file: str | Path | None = None,
) -> InputPlan:
    """Produce an ``InputPlan`` from CLI / GUI inputs.

    No file enumeration happens here.  We only classify the input axis and
    preserve raw paths so the executor can honor ``recurse`` during unit
    construction (which needs ``output_dir`` to compute ``source_root``).
    """

    raw_files = _expand_file_inputs(files or [])

    if raw_files:
        for p in raw_files:
            if not p.exists():
                raise PipelineExecutionError(f"--files 指定的路径不存在: {p}")
        return InputPlan(kind="path", recurse=recurse, files=tuple(raw_files))

    lines: list[str] = []
    if lines_text is not None:
        lines = [ln.strip() for ln in lines_text.splitlines() if ln.strip()]
    elif lines_file is not None:
        if str(lines_file) == "-":
            lines = _read_lines_file("-")
        else:
            lines = _read_lines_file(lines_file)

    if lines:
        return InputPlan(kind="line", lines=tuple(lines))

    return InputPlan(kind="none")


def _expand_file_inputs(files: list[str | Path]) -> list[Path]:
    """Expand CLI/API glob tokens while preserving deterministic order."""

    expanded: list[Path] = []
    seen: set[str] = set()
    for raw in files:
        token = os.fspath(raw)
        path = Path(token)
        if path.exists():
            matches = [path]
        elif any(char in token for char in _GLOB_MAGIC):
            matches = [Path(match) for match in glob.glob(token, recursive=True, include_hidden=False)]
            if not matches:
                raise PipelineExecutionError(f"--files 通配符未匹配任何路径: {token}")
        else:
            matches = [path]

        for match in matches:
            if not match.exists():
                raise PipelineExecutionError(f"--files 指定的路径不存在: {match}")
            key = os.path.normcase(str(match.resolve()))
            if key in seen:
                continue
            seen.add(key)
            expanded.append(match)
    return expanded
