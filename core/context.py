"""Module-facing execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .files import UnitWorkspace, WorkspaceFile


@dataclass(slots=True)
class PipelineContext:
    """Business state for one unit; filesystem state lives on ``workspace``."""

    workspace: UnitWorkspace
    original_input: Path | None = None
    shared: dict[str, Any] = field(default_factory=dict)
    source_root: Path | None = None

    def __post_init__(self) -> None:
        if self.original_input is not None:
            self.original_input = Path(self.original_input)
        if self.source_root is not None:
            self.source_root = Path(self.source_root)

    @property
    def current(self) -> WorkspaceFile:
        return self.workspace.current

    def path(self, *parts: str | Path) -> Path:
        return self.workspace.path(*parts)

    def file(self, path: str | Path) -> WorkspaceFile:
        return self.workspace.file(path)

    def set_current(self, path: str | Path) -> WorkspaceFile:
        return self.workspace.set_current(path)

    def entries(self, recursive: bool = True) -> list[WorkspaceFile]:
        return self.workspace.entries(recursive=recursive)

    def files(self, recursive: bool = True) -> list[WorkspaceFile]:
        return self.workspace.files(recursive=recursive)

    def directories(self, recursive: bool = True) -> list[WorkspaceFile]:
        return self.workspace.directories(recursive=recursive)

    def create_file(self, name: str | Path, data: str | bytes = b"", **kwargs: Any) -> WorkspaceFile:
        return self.workspace.create_file(name, data, **kwargs)

    def create_directory(self, name: str | Path) -> WorkspaceFile:
        return self.workspace.create_directory(name)

    def allocate_file(self, name: str | Path) -> WorkspaceFile:
        return self.workspace.allocate_file(name)

    def adopt(self, path: str | Path) -> WorkspaceFile:
        return self.workspace.adopt(path)

    def read_text(self, source: str | Path, **kwargs: Any) -> str:
        return self.workspace.read_text(source, **kwargs)

    def read_bytes(self, source: str | Path) -> bytes:
        return self.workspace.read_bytes(source)

    def write_text(self, target: str | Path, data: str, **kwargs: Any) -> WorkspaceFile:
        return self.workspace.write_text(target, data, **kwargs)

    def write_bytes(self, target: str | Path, data: bytes) -> WorkspaceFile:
        return self.workspace.write_bytes(target, data)

    def copy(self, source: str | Path, target: str | Path) -> WorkspaceFile:
        return self.workspace.copy(source, target)

    def move(self, source: str | Path, target: str | Path) -> WorkspaceFile:
        return self.workspace.move(source, target)

    def rename(self, source: str | Path, name: str) -> WorkspaceFile:
        return self.workspace.rename(source, name)

    def delete(self, source: str | Path) -> None:
        self.workspace.delete(source)

    def refresh(self) -> None:
        self.workspace.refresh()

    def publish(self) -> None:
        self.workspace.publish()
