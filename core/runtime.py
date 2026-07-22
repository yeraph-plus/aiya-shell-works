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
import threading
from collections.abc import Callable, Sequence
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
        enable_log: bool = False,
        output_dir: str | Path | None = None,
        workflow_slug: str = "",
        sessions: TerminalSessionRegistry | None = None,
        owns_log_sink: bool = True,
        owns_sessions: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._log_sink = log_sink
        if enable_log and log_sink is None and output_dir:
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug_part = f"_{workflow_slug}" if workflow_slug else ""
            path = Path(output_dir) / f"{ts}{slug_part}.jsonl"
            self._log_sink = JSONLFileSink(path)
        self._sessions = sessions or TerminalSessionRegistry()
        self._owns_log_sink = owns_log_sink
        self._owns_sessions = owns_sessions
        self._cancel_event = cancel_event or threading.Event()
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

        if listener not in self._persistent_listeners:
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

    def fork(self) -> PipelineRuntime:
        """Create an isolated event bus sharing sessions, sink and listeners."""

        runtime = PipelineRuntime(
            log_sink=self._log_sink,
            sessions=self._sessions,
            owns_log_sink=False,
            owns_sessions=False,
            cancel_event=self._cancel_event,
        )
        for listener in self._persistent_listeners:
            runtime.subscribe(listener)
        return runtime

    # ------------------------------------------------------------------
    # Terminal session surface
    # ------------------------------------------------------------------

    @property
    def sessions(self) -> TerminalSessionRegistry:
        return self._sessions

    def spawn(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
        shell: bool = False,
    ) -> TerminalResult:
        """Spawn ``command`` in a PTY (or subprocess fallback) and block.

        Output is streamed to the active bus as ``terminal:output`` events
        so GUI layers (or CLI sinks) stay uniform regardless of platform.
        """

        return self.start(
            command,
            cwd=cwd,
            env=env,
            exit_pattern=exit_pattern,
            exit_action=exit_action,
            shell=shell,
        ).wait()

    def start(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
        shell: bool = False,
    ) -> TerminalSession:
        """Start a live child session and return immediately."""

        session = TerminalSession(
            cmd=command,
            runtime=self,
            cwd=cwd,
            env=env,
            exit_pattern=exit_pattern,
            exit_action=exit_action,
            shell=shell,
            on_finished=self._sessions.unregister,
        )
        self._sessions.register(session)
        try:
            return session.start()
        except Exception:
            self._sessions.unregister(session)
            raise

    # ------------------------------------------------------------------
    # Cancellation surface
    # ------------------------------------------------------------------

    def request_cancel(self) -> None:
        """Request graceful stop at the next step boundary."""

        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

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

        if self._owns_sessions:
            self._sessions.close_all()
        sink = self._log_sink
        if self._owns_log_sink and isinstance(sink, JSONLFileSink):
            sink.close()
