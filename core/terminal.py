"""Pseudo-terminal session wrapper for interactive subprocess management.

Provides a synchronous, blocking interface that modules call from within
``run()``.  Output is streamed to the PipelineEventBus so the GUI layer
can detect and display it in real time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import re
import uuid

from .pipeline import PipelineEventBus  # noqa: TCH001  -- used in forward refs

_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[@-_]")


# ---------------------------------------------------------------------------
# Module-level session registry so GUI can locate an active session by id.
# ---------------------------------------------------------------------------
_sessions: dict[str, "TerminalSession"] = {}


def _register(session: "TerminalSession") -> None:
    _sessions[session.id] = session


def _unregister(session: "TerminalSession") -> None:
    _sessions.pop(session.id, None)


def get_session(session_id: str) -> "TerminalSession | None":
    """Retrieve an active terminal session by its id (called from GUI)."""
    return _sessions.get(session_id)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class TerminalResult:
    """Outcome of a terminal command execution."""

    exit_code: int

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class TerminalSession:
    """Spawn a command inside a pseudo-terminal and stream output to the
    pipeline event bus.

    Usage from a module's ``run()``::

        result = TerminalSession(cmd, event_bus=context.events).run()
        if not result.is_success:
            raise RuntimeError(...)
    """

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        event_bus: PipelineEventBus,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
    ) -> None:
        self.id: str = uuid.uuid4().hex
        self.command = list(command)
        self.cwd = Path(cwd) if cwd else None
        default_env = dict(os.environ)
        default_env.setdefault("PYTHONIOENCODING", "utf-8")
        default_env.setdefault("PYTHONUTF8", "1")
        if env:
            default_env.update(env)
        self.env = default_env
        self._event_bus = event_bus
        self._process: Any = None
        self._exit_code: int | None = None
        self._exit_pattern = exit_pattern
        self._exit_action = exit_action
        self._output_buf = ""
        self._pattern_matched = False

    def run(self) -> TerminalResult:
        """Spawn the command, stream output, and block until completion.

        If *exit_pattern* was supplied, once the accumulated output contains it
        the session will emit ``terminal:close`` (so the GUI can auto-dismiss
        the terminal window) and perform *exit_action* (``"write_newline"`` to
        send Enter to stdin or ``"terminate"`` to kill the process).
        """
        from winpty import PtyProcess  # lazy import so core stays importable

        _register(self)

        self._event_bus.log(
            "terminal",
            "message",
            "terminal:started",
            {"session_id": self.id, "command": " ".join(self.command)},
        )

        try:
            self._process = PtyProcess.spawn(
                self.command,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
            )
            while self._process.isalive():
                try:
                    raw = self._process.read(4096)
                    if raw:
                        data = _ANSI_STRIP_RE.sub("", raw)
                        if data:
                            self._event_bus.log(
                                "terminal",
                                "message",
                                "terminal:output",
                                {"session_id": self.id, "text": data},
                            )
                            self._check_exit_pattern(data)
                except (OSError, EOFError):
                    break
            self._exit_code = self._process.wait()
        finally:
            self._process = None
            self._event_bus.log(
                "terminal",
                "message",
                "terminal:finished",
                {
                    "session_id": self.id,
                    "exit_code": self._exit_code if self._exit_code is not None else -1,
                },
            )
            _unregister(self)

        return TerminalResult(exit_code=self._exit_code if self._exit_code is not None else -1)

    def _check_exit_pattern(self, data: str) -> None:
        """If *exit_pattern* is set and found in the accumulated output,
        emit ``terminal:close`` and perform *exit_action*."""
        if self._exit_pattern is None or self._pattern_matched:
            return
        self._output_buf += data
        if self._exit_pattern in self._output_buf:
            self._pattern_matched = True
            self._event_bus.log(
                "terminal",
                "message",
                "terminal:close",
                {"session_id": self.id},
            )
            if self._exit_action == "write_newline" and self._process is not None:
                try:
                    self._process.write("\n")
                except OSError:
                    pass
            elif self._exit_action == "terminate" and self._process is not None:
                try:
                    self._process.terminate()
                except OSError:
                    pass

    def write(self, data: str) -> None:
        """Write *data* to the process stdin (called from the GUI)."""
        if self._process is not None:
            self._process.write(data)

    def terminate(self) -> None:
        """Terminate the underlying process."""
        if self._process is not None:
            try:
                self._process.terminate()
            except OSError:
                pass

    @property
    def exit_code(self) -> int | None:
        return self._exit_code
