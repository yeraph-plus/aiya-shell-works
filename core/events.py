"""Event bus: stream-only, no query API.

The bus stores a sequence of ``PipelineEvent`` records for replay/audit and
forwards each new record to live subscribers.  Listeners are observers;
their failures never propagate back to the publishing module.

Design departures from the legacy ``PipelineEventBus``:

* ``query()`` is gone -- modules must not reach into the event log to
  reconstruct cross-step state.  Cross-step data flows through
  ``PipelineContext.shared``.
* Subscribers register through the bus, but lifecycle (one subscription per
  runtime vs per-unit) is managed by ``PipelineRuntime``.
* Sinks (file / queue / stdout) are pluggable via the ``LogSink`` protocol --
  used by the CLI ``--log-file`` JSONL writer and the future
  multiprocessing bridge.
* ``retain`` flag controls memory: when ``False``, only ``error``-type
  events are stored in ``_events`` (others are discarded after dispatch).
  ``_error_events`` always retains every error for ``has_errors()``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Literal, Protocol

LOGGER = logging.getLogger(__name__)

PipelineEventType = Literal["success", "message", "hint", "warning", "error"]


@dataclass(slots=True)
class PipelineEvent:
    """Immutable single record flowing through the bus."""

    slug: str
    type: PipelineEventType
    text: str
    data: dict[str, Any] = field(default_factory=dict)


class LogSink(Protocol):
    """Pluggable destination for event persistence (file / queue / stdout)."""

    def write(self, event: PipelineEvent) -> None: ...


class NullSink:
    """Default no-op sink for in-memory operations."""

    def write(self, event: PipelineEvent) -> None:  # noqa: D401
        return None


Listener = Callable[[PipelineEvent], None]
Unsubscribe = Callable[[], None]


class EventBus:
    """Stream + store pipeline events; dispatch to listeners and sinks.

    The bus never raises from a listener.  Listeners are observers -- a buggy
    GUI callback must not crash the executor.  This is a non-degrading
    invariant: see AGENTS.md "event listener isolation".
    """

    def __init__(self, *, sink: LogSink | None = None, retain: bool = True) -> None:
        self._events: list[PipelineEvent] = []
        self._listeners: list[Listener] = []
        self._sink: LogSink = sink or NullSink()
        self._retain = retain
        self._error_events: list[PipelineEvent] = []

    def log(
        self,
        slug: str,
        event_type: PipelineEventType,
        text: str,
        data: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        """Append an event, push it to every listener, persist to sink."""

        event = PipelineEvent(slug=slug, type=event_type, text=text, data=data or {})
        if self._retain or event_type == "error":
            self._events.append(event)
        if event_type == "error":
            self._error_events.append(event)
        for listener in list(self._listeners):  # snapshot, listener may unsubscribe mid-loop
            try:
                listener(event)
            except Exception:  # pragma: no cover - defensive isolation
                LOGGER.exception("Pipeline event listener failed: %r", listener)
        try:
            self._sink.write(event)
        except Exception:  # pragma: no cover - sinks must be best-effort
            LOGGER.exception("Pipeline log sink failed: %r", self._sink)
        return event

    def subscribe(self, listener: Listener) -> Unsubscribe:
        """Register a listener and return an unsubscribe closure."""

        if listener not in self._listeners:
            self._listeners.append(listener)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsubscribe

    def unsubscribe(self, listener: Listener) -> None:
        """Remove a listener (idempotent)."""

        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def has_errors(self) -> bool:
        """Return True if any ``error`` event has been logged."""

        return any(event.type == "error" for event in self._error_events)

    def iterate(self) -> Iterator[PipelineEvent]:
        """Read-only iteration over the historical event stream.

        Replaces the legacy ``query()`` API.  Iteration gives a stable snapshot
        -- newly appended events will only appear to callers that re-iterate.
        """

        return iter(list(self._events))

    def reset(self) -> None:
        """Clear stored events; do not touch listeners (runtime owns them)."""

        self._events.clear()
        self._error_events.clear()

    def __iter__(self) -> Iterator[PipelineEvent]:
        return self.iterate()

    def __len__(self) -> int:
        return len(self._events)


class InMemorySink:
    """Collects every event into a list.  Used by tests and the CLI summary."""

    def __init__(self) -> None:
        self.events: list[PipelineEvent] = []

    def write(self, event: PipelineEvent) -> None:
        self.events.append(event)


class JSONLFileSink:
    """Append each event as a JSON line for log retention / resume support."""

    def __init__(self, path: str | Any, *, encoding: str = "utf-8") -> None:
        import json
        import threading
        from pathlib import Path

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._encoding = encoding
        self._fh = self._path.open("a", encoding=encoding, newline="\n")
        self._json = json
        self._lock = threading.Lock()

    def write(self, event: PipelineEvent) -> None:
        from datetime import datetime

        record = {
            "ts": datetime.now(UTC).isoformat(timespec="microseconds"),
            "slug": event.slug,
            "type": event.type,
            "text": event.text,
            "data": event.data,
        }
        line = self._json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:  # pragma: no cover
                pass
