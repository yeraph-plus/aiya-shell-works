"""PipelineRuntime / EventBus runtime-test boundary."""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from core.events import (
    EventBus, InMemorySink, JSONLFileSink, NullSink, PipelineEvent,
)
from core.runtime import PipelineRuntime


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

def test_event_bus_log_stores_and_dispatches() -> None:
    bus = EventBus()
    seen: list[PipelineEvent] = []
    bus.subscribe(seen.append)
    bus.log("m", "message", "hello", {"k": "v"})
    assert len(bus) == 1
    assert seen and seen[0].slug == "m"
    assert seen[0].data == {"k": "v"}


def test_event_bus_unsubscribe_closure() -> None:
    bus = EventBus()
    seen: list[PipelineEvent] = []
    unsubscribe = bus.subscribe(seen.append)
    bus.log("m", "message", "one")
    unsubscribe()
    bus.log("m", "message", "two")
    assert len(seen) == 1
    assert len(bus) == 2


def test_event_bus_listener_exception_isolation() -> None:
    """A failing listener must not crash the publishing module."""

    bus = EventBus()
    good: list[PipelineEvent] = []
    bad_calls: list[int] = []

    def bad_listener(event: PipelineEvent) -> None:
        bad_calls.append(1)
        raise RuntimeError("listener boom")

    bus.subscribe(bad_listener)
    bus.subscribe(good.append)
    # Must not raise — the listener's failure is isolated.
    bus.log("m", "message", "still ok")
    assert good, "good listener must still receive event"
    assert bad_calls, "bad listener must have been called"


def test_event_bus_unsubscribe_is_idempotent() -> None:
    bus = EventBus()
    listener = lambda e: None  # noqa: E731
    bus.subscribe(listener)
    bus.unsubscribe(listener)
    bus.unsubscribe(listener)  # second removal must not raise


def test_event_bus_no_query_method() -> None:
    """Sanity: legacy query() API is gone from the new bus."""

    bus = EventBus()
    assert not hasattr(bus, "query")
    # iterate() provides an alternative read API
    bus.log("m", "message", "x")
    events = list(bus.iterate())
    assert events and events[0].slug == "m"


def test_event_bus_sink_invocation() -> None:
    sink = InMemorySink()
    bus = EventBus(sink=sink)
    bus.log("m", "message", "streamed", {"x": 1})
    bus.log("m", "success", "ok")
    assert len(sink.events) == 2
    assert sink.events[0].data == {"x": 1}
    assert sink.events[1].type == "success"


def test_event_bus_sink_failure_isolated() -> None:
    """Sink errors must not crash the publisher."""

    class BadSink:
        def write(self, event: PipelineEvent) -> None: raise RuntimeError("sink boom")

    bus = EventBus(sink=BadSink())
    bus.log("m", "message", "ok")
    assert len(bus) == 1


def test_event_bus_has_errors() -> None:
    bus = EventBus()
    assert not bus.has_errors()
    bus.log("m", "message", "x")
    assert not bus.has_errors()
    bus.log("m", "error", "boom")
    assert bus.has_errors()


# ---------------------------------------------------------------------------
# JSONLFileSink
# ---------------------------------------------------------------------------

def test_jsonl_file_sink_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.log"
    sink = JSONLFileSink(path)
    sink.write(PipelineEvent(slug="s", type="message", text="t1", data={"k": 1}))
    sink.write(PipelineEvent(slug="s", type="success", text="t2"))
    sink.close()
    raw = path.read_text(encoding="utf-8").splitlines()
    assert len(raw) == 2
    entry = json.loads(raw[0])
    assert entry["slug"] == "s"
    assert entry["data"] == {"k": 1}
    assert "ts" in entry


# ---------------------------------------------------------------------------
# PipelineRuntime
# ---------------------------------------------------------------------------

def test_runtime_starts_with_fresh_bus() -> None:
    r = PipelineRuntime()
    assert isinstance(r.bus, EventBus)
    assert not r.is_cancelled()


def test_runtime_log_forwards_to_active_bus() -> None:
    r = PipelineRuntime()
    seen: list[PipelineEvent] = []
    r.subscribe(seen.append)
    r.log("slug", "message", "x")
    assert seen and seen[0].slug == "slug"


def test_runtime_replace_bus_isolates_stream(tmp_path: Path) -> None:
    """Per-unit bus isolation: events on a previous bus must NOT appear on the
    active bus after ``replace_bus``.

    Persistent listeners (subscribed through ``runtime.subscribe``) continue
    to receive new events on the new bus — that's a deliberate stem listener
    contract.  This test isolates the *storage* seams by examining each bus
    via ``bus.iterate()`` instead of subscriber snapshots.
    """

    r = PipelineRuntime()
    bus_a = r.bus
    bus_a.log("unitA", "message", "first")
    bus_b = r.replace_bus()
    r.log("unitB", "message", "second")

    # bus_a retains only its own events; bus_b retains only its own.
    assert list(bus_a.iterate()) and bus_a.iterate().__length_hint__() > 0
    assert [e.text for e in bus_a.iterate() if e.slug == "unitA"]
    assert not [e for e in bus_a.iterate() if e.slug == "unitB"]
    assert not [e for e in r.bus.iterate() if e.slug == "unitA"]
    assert [e for e in r.bus.iterate() if e.slug == "unitB"]


def test_runtime_request_cancel_then_is_cancelled() -> None:
    r = PipelineRuntime()
    assert not r.is_cancelled()
    r.request_cancel()
    assert r.is_cancelled()


def test_runtime_log_file_sink(tmp_path: Path) -> None:
    p = tmp_path / "rt.log"
    r = PipelineRuntime(log_file=p)
    r.log("a", "message", "x")
    r.close()
    assert p.exists() and len(p.read_text(encoding="utf-8").splitlines()) >= 1


def test_runtime_sessions_registry_starts_empty() -> None:
    r = PipelineRuntime()
    assert len(r.sessions) == 0