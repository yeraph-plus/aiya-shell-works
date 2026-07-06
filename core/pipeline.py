"""Core pipeline context and event system."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Callable, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from .terminal import TerminalResult


LOGGER = logging.getLogger(__name__)

PipelineMode = Literal["file", "folder", "none", "cycle", "input"]
PipelineEventType = Literal["success", "message", "hint", "warning", "error"]


@dataclass(slots=True)
class PipelineEvent:
    """A typed event logged by a module during pipeline execution."""

    slug: str
    type: PipelineEventType
    text: str
    data: dict[str, Any] = field(default_factory=dict)


class PipelineEventBus:
    """Per-unit event bus for inter-step signalling and structured logging."""

    def __init__(self) -> None:
        self._events: list[PipelineEvent] = []
        self._listeners: list[Callable[[PipelineEvent], None]] = []

    def log(
        self,
        slug: str,
        event_type: PipelineEventType,
        text: str,
        data: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        event = PipelineEvent(slug=slug, type=event_type, text=text, data=data or {})
        self._events.append(event)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # pragma: no cover - defensive isolation
                LOGGER.exception("Pipeline event listener failed: %r", listener)
        return event

    def subscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        """Register a live listener for newly logged events."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        """Remove a previously registered live listener."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def query(
        self,
        *,
        slug: str | None = None,
        event_type: PipelineEventType | None = None,
    ) -> list[PipelineEvent]:
        results: list[PipelineEvent] = []
        for event in self._events:
            if slug is not None and event.slug != slug:
                continue
            if event_type is not None and event.type != event_type:
                continue
            results.append(event)
        return results

    def has_errors(self) -> bool:
        return any(event.type == "error" for event in self._events)

    def reset(self) -> None:
        self._events.clear()

    def __iter__(self):
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __bool__(self) -> bool:
        return True


_VALID_CLONE_FIELDS: frozenset[str] = frozenset({
    "original_input", "working_path", "output_dir", "mode",
    "shared", "extra_files", "source_root", "events", "is_file", "is_dir",
})


@dataclass(slots=True)
class PipelineContext:
    """Mutable context shared across workflow modules."""

    original_input: Path | None
    working_path: Path
    output_dir: Path
    mode: PipelineMode
    shared: dict[str, Any] = field(default_factory=dict)
    extra_files: list[Path] = field(default_factory=list)
    source_root: Path | None = None
    events: PipelineEventBus = field(default_factory=PipelineEventBus)
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

    def clone(self, **changes: Any) -> "PipelineContext":
        """Return a shallow copy with selected fields replaced."""
        for key in changes:
            if key not in _VALID_CLONE_FIELDS:
                allowed = ", ".join(sorted(_VALID_CLONE_FIELDS))
                raise TypeError(
                    f"clone() 不支持字段 '{key}'，有效字段: {allowed}"
                )

        payload = {
            "original_input": self.original_input,
            "working_path": self.working_path,
            "output_dir": self.output_dir,
            "mode": self.mode,
            "shared": dict(self.shared),
            "extra_files": list(self.extra_files),
            "source_root": self.source_root,
            "events": self.events,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
        }
        payload.update(changes)
        return PipelineContext(**payload)

    def track_extra_file(self, path: str | Path) -> Path:
        """Track an extra output file for downstream modules."""

        tracked_path = Path(path)
        self.extra_files.append(tracked_path)
        return tracked_path

    def run_command(
        self,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
    ) -> "TerminalResult":
        """Convenience wrapper that spawns a terminal session and blocks.

        Module authors only need to supply the command; the core handles
        PTY lifecycle, output streaming, and GUI window creation.

        When *exit_pattern* is given, the session monitors accumulated
        output for the string and, on match, emits ``terminal:close``
        (prompting the GUI to dismiss the window) then performs
        *exit_action* (``"write_newline"`` or ``"terminate"``).
        """
        from .terminal import TerminalSession

        session = TerminalSession(
            command,
            cwd=cwd if cwd is not None else self.output_dir,
            env=env,
            event_bus=self.events,
            exit_pattern=exit_pattern,
            exit_action=exit_action,
        )
        return session.run()
