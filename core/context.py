"""PipelineContext: business-only data carrier for one processing unit.

The context never carries control-flow state (event bus, terminal sessions,
cancellation, resume).  All such state lives on ``PipelineRuntime``.  Modules
interact with the runtime through the third ``run(ctx, cfg, runtime)``
parameter rather than reaching through the context.

Why "no events on context":
The previous design let modules call ``ctx.events.log(...)`` and that
implicitly shared the bus with downstream modules.  Authors had to remember
``clone(events=...)`` to keep the bus alive, which made reasoning fragile.
With ``runtime`` as the explicit control handle, the bus is always present
and never accidentally duplicated on a ``clone()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Atom = Literal["file", "folder", "line", "none"]

_VALID_CLONE_FIELDS: frozenset[str] = frozenset(
    {
        "original_input",
        "working_path",
        "output_dir",
        "atom",
        "shared",
        "extra_files",
        "source_root",
        "is_file",
        "is_dir",
    }
)


@dataclass(slots=True)
class PipelineContext:
    """Per-unit business data.  Mutated through ``clone(**changes)`` or in place."""

    original_input: Path | None
    working_path: Path
    output_dir: Path
    atom: Atom
    shared: dict[str, Any] = field(default_factory=dict)
    extra_files: list[Path] = field(default_factory=list)
    source_root: Path | None = None
    is_file: bool = False
    is_dir: bool = False

    def __post_init__(self) -> None:
        if self.original_input is not None:
            self.original_input = Path(self.original_input)
        self.working_path = Path(self.working_path)
        self.output_dir = Path(self.output_dir)
        if self.source_root is not None:
            self.source_root = Path(self.source_root)
        self.is_file = self.working_path.is_file()
        self.is_dir = self.working_path.is_dir()

    def clone(self, **changes: Any) -> PipelineContext:
        """Shallow-copy with field override.  ``events`` is intentionally absent."""

        for key in changes:
            if key not in _VALID_CLONE_FIELDS:
                allowed = ", ".join(sorted(_VALID_CLONE_FIELDS))
                raise TypeError(f"clone() 不支持字段 '{key}'，有效字段: {allowed}")
        payload = {
            "original_input": self.original_input,
            "working_path": self.working_path,
            "output_dir": self.output_dir,
            "atom": self.atom,
            "shared": dict(self.shared),
            "extra_files": list(self.extra_files),
            "source_root": self.source_root,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
        }
        payload.update(changes)
        return PipelineContext(**payload)

    def track_extra_file(self, path: str | Path) -> Path:
        """Track a side产出 file so later steps / GUI pick it up."""

        tracked = Path(path)
        self.extra_files.append(tracked)
        return tracked
