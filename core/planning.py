from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

UnitKind = Literal["none", "line", "path"]
WorkspaceLayout = Literal["single", "shared", "batch"]


@dataclass(frozen=True, slots=True)
class PathInput:
    path: Path
    source_root: Path | None = None


@dataclass(frozen=True, slots=True)
class ExecutionUnit:
    kind: UnitKind
    layout: WorkspaceLayout = "single"
    paths: tuple[PathInput, ...] = ()
    lines: tuple[str, ...] = ()

    def display(self) -> str | None:
        if self.kind == "line":
            if len(self.lines) == 1:
                return f"[line] {self.lines[0]}"
            return f"[lines x{len(self.lines)}]"
        if self.kind == "path":
            if self.layout == "shared":
                return f"[shared path x{len(self.paths)}]"
            if self.layout == "batch":
                return f"[path batch x{len(self.paths)}]"
            return str(self.paths[0].path) if self.paths else None
        return None
