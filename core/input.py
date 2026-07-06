"""Input plan resolution: turn CLI inputs into a unit-ready description.

This module decides ``atom`` by input source and preserves raw paths so the
executor can honor ``recurse`` during unit construction.

Resolution rules:

1. ``files`` non-empty → ``atom=file``.  ``recurse=false`` dirs become
   whole-folder units at unit-build time; the atom stays ``"file"``.
2. ``files`` empty, ``lines_text``/``lines_file`` supplied → ``atom=line``
3. All empty → ``atom=none`` (single empty unit)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .exceptions import PipelineExecutionError

Atom = Literal["file", "folder", "line", "none"]


@dataclass(frozen=True, slots=True)
class InputPlan:
    """The outcome of CLI / GUI input resolution."""

    atom: Atom
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

    raw_files = [Path(p) for p in files] if files else []

    if raw_files:
        for p in raw_files:
            if not p.exists():
                raise PipelineExecutionError(f"--files 指定的路径不存在: {p}")
        has_file = any(p.is_file() for p in raw_files)
        has_dir = any(p.is_dir() for p in raw_files)
        if has_dir and has_file and not recurse:
            raise PipelineExecutionError(
                "混合文件与文件夹输入时必须启用 --recurse（仅展开文件）以避免 atom 不一致。"
            )
        return InputPlan(atom="file", recurse=recurse, files=tuple(raw_files))

    lines: list[str] = []
    if lines_text is not None:
        lines = [ln.strip() for ln in lines_text.splitlines() if ln.strip()]
    elif lines_file is not None:
        if str(lines_file) == "-":
            lines = _read_lines_file("-")
        else:
            lines = _read_lines_file(lines_file)

    if lines:
        return InputPlan(atom="line", lines=tuple(lines))

    return InputPlan(atom="none")