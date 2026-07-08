"""Pipeline runtime: control plane for a single workflow execution.

The runtime owns the control-flow plane —
event bus, terminal sessions, cancellation and resume — while ``PipelineContext``
carries only business data (paths, shared scratch dict, extra files).

Splitting the two is a non-degrading invariant: previously the event bus
lived on the context and modules shared it implicitly through ``clone()``;
this worked only because module authors were trained to use
``clone(events=self.events)``.  The new contract makes the sharing explicit
and lets a single ``PipelineRuntime`` drive a whole execution without any
per-unit subscription bookkeeping in the executor.

Per-unit event-bus isolation: each unit gets a fresh ``EventBus`` swapped in
``runtime.bus``; other busses accumulate independently.  This counts as
"task isolation" — two units never observe each other's events.

Multiprocessing-ready: ``runtime.spawn`` and ``runtime.log`` stay abstract so
that a future ``QueueRuntime`` subclass can ship the same calls across a
process boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .events import EventBus, JSONLFileSink, LogSink, PipelineEvent, PipelineEventType
from .terminal import TerminalResult, TerminalSession, TerminalSessionRegistry

LOGGER = logging.getLogger(__name__)


class PipelineRuntime:
    """Owns event dispatch, terminal sessions, cancellation and log sink."""

    def __init__(
        self,
        *,
        log_sink: LogSink | None = None,
        log_file: str | Path | None = None,
    ) -> None:
        self._log_sink = log_sink
        if log_file is not None and log_sink is None:
            self._log_sink = JSONLFileSink(log_file)
        self._sessions = TerminalSessionRegistry()
        self._cancel = False
        self._resuming = False
        self._bus: EventBus = EventBus(sink=self._log_sink)
        # Persistent listeners are re-attached to every new bus that replaces the
        # active one.  This is the GUI contract: subscribe once at startup and
        # get every per-unit event stream without re-subscribing manually.
        self._persistent_listeners: list[Callable[[PipelineEvent], None]] = []

    # ------------------------------------------------------------------
    # Event bus surface
    # ------------------------------------------------------------------

    @property
    def bus(self) -> EventBus:
        return self._bus

    @bus.setter
    def bus(self, value: EventBus) -> None:
        self._bus = value

    def log(
        self,
        slug: str,
        event_type: PipelineEventType,
        text: str,
        data: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        """Forward to the currently-active event bus."""

        return self._bus.log(slug, event_type, text, data)

    def subscribe(self, listener: Callable[[PipelineEvent], None]) -> Callable[[], None]:
        """Register a *persistent* listener — re-attached across per-unit busses.

        Returns an unsubscribe closure.  This is the contract GUI callers rely
        on: a single ``runtime.subscribe(log_callback)`` keeps working even when
        the executor calls ``replace_bus()`` to isolate one unit's events from
        another.  Per-unit *event isolation* is about the historical store,
        not about who listens going forward.
        """

        self._persistent_listeners.append(listener)
        self._bus.subscribe(listener)

        def _unsubscribe() -> None:
            try:
                self._persistent_listeners.remove(listener)
            except ValueError:
                pass
            self._bus.unsubscribe(listener)

        return _unsubscribe

    def unsubscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        """Remove a previously registered persistent listener (idempotent)."""

        try:
            self._persistent_listeners.remove(listener)
        except ValueError:
            pass
        self._bus.unsubscribe(listener)

    def replace_bus(self, *, sink: LogSink | None = None) -> EventBus:
        """Install a fresh bus for a new processing unit.

        The previous bus is returned so the executor can hand it to GUI / CLI
        consumers without re-attaching their listeners.  Persistent listeners
        are automatically re-attached to the new bus so external subscribers
        have an uninterrupted stream.
        """

        new_sink = sink if sink is not None else self._log_sink
        previous = self._bus
        self._bus = EventBus(sink=new_sink)
        for listener in self._persistent_listeners:
            self._bus.subscribe(listener)
        return previous

    def _iter_persistent_listeners(self) -> list[Callable[[PipelineEvent], None]]:
        return list(self._persistent_listeners)

    # ------------------------------------------------------------------
    # Terminal session surface
    # ------------------------------------------------------------------

    @property
    def sessions(self) -> TerminalSessionRegistry:
        return self._sessions

    def spawn(
        self,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
    ) -> TerminalResult:
        """Spawn ``command`` in a PTY (or subprocess fallback) and block.

        Output is streamed to the active bus as ``terminal:output`` events
        so GUI layers (or CLI sinks) stay uniform regardless of platform.
        """

        session = TerminalSession(
            cmd=command,
            runtime=self,
            cwd=cwd,
            env=env,
            exit_pattern=exit_pattern,
            exit_action=exit_action,
        )
        self._sessions.register(session)
        try:
            return session.run()
        finally:
            self._sessions.unregister(session)

    # ------------------------------------------------------------------
    # Cancellation surface
    # ------------------------------------------------------------------

    def request_cancel(self) -> None:
        """Request graceful stop at the next step boundary."""

        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    # ------------------------------------------------------------------
    # Resume surface (reserved for P5 pause/resume capability)
    # ------------------------------------------------------------------

    def set_resuming(self, value: bool) -> None:
        self._resuming = value

    def is_resuming(self) -> bool:
        return self._resuming

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Tear down log sinks and outstanding terminal sessions."""

        self._sessions.close_all()
        sink = self._log_sink
        if isinstance(sink, JSONLFileSink):
            sink.close()
