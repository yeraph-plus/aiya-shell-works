"""Control plane for one workflow execution.

The runtime owns event dispatch, terminal sessions, cancellation, persistent
listeners and the log sink. Per-unit and worker runtimes have independent
event histories while sharing the cancellation signal, session registry and
thread-safe listener registry.
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


class _ListenerRegistry:
    def __init__(self) -> None:
        self._listeners: list[Callable[[PipelineEvent], None]] = []
        self._lock = threading.RLock()
        self._dispatch_lock = threading.RLock()

    def subscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def dispatch(self, event: PipelineEvent) -> None:
        with self._lock:
            listeners = list(self._listeners)
        with self._dispatch_lock:
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    LOGGER.exception("Pipeline runtime listener failed: %r", listener)

    def snapshot(self) -> list[Callable[[PipelineEvent], None]]:
        with self._lock:
            return list(self._listeners)

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()


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
        listener_registry: _ListenerRegistry | None = None,
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
        self._listener_registry = listener_registry or _ListenerRegistry()
        self._bus: EventBus = EventBus(sink=self._log_sink)
        self._bus.subscribe(self._listener_registry.dispatch)
        self._close_lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # Event bus surface
    # ------------------------------------------------------------------

    @property
    def bus(self) -> EventBus:
        return self._bus

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

        self._listener_registry.subscribe(listener)

        def _unsubscribe() -> None:
            self._listener_registry.unsubscribe(listener)

        return _unsubscribe

    def unsubscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        """Remove a previously registered persistent listener (idempotent)."""

        self._listener_registry.unsubscribe(listener)

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
        self._bus.subscribe(self._listener_registry.dispatch)
        return previous

    def _iter_persistent_listeners(self) -> list[Callable[[PipelineEvent], None]]:
        return self._listener_registry.snapshot()

    def fork(self) -> PipelineRuntime:
        """Create an isolated event bus sharing sessions, sink and listeners."""

        runtime = PipelineRuntime(
            log_sink=self._log_sink,
            sessions=self._sessions,
            owns_log_sink=False,
            owns_sessions=False,
            cancel_event=self._cancel_event,
            listener_registry=self._listener_registry,
        )
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
        timeout: float | None = None,
    ) -> TerminalResult:
        """Spawn ``command`` in a PTY (or subprocess fallback) and block.

        Output is streamed to the active bus as ``terminal:output`` events
        so GUI layers (or CLI sinks) stay uniform regardless of platform.
        """

        session = self.start(
            command,
            cwd=cwd,
            env=env,
            exit_pattern=exit_pattern,
            exit_action=exit_action,
            shell=shell,
        )
        try:
            return session.wait(timeout=timeout)
        except TimeoutError:
            session.close()
            raise

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
        self._sessions.terminate_all()

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

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if self._owns_sessions:
            self._sessions.close_all()
        self._bus.reset()
        self._bus.clear_listeners()
        if self._owns_log_sink:
            close = getattr(self._log_sink, "close", None)
            if callable(close):
                close()
            self._listener_registry.clear()
