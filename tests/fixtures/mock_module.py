"""Test fixtures: a static probe module + event probe used across tests."""

from __future__ import annotations

from typing import Any

CALLS: list[dict[str, Any]] = []
EVENTS: list[tuple[str, str, str]] = []


def reset() -> None:
    CALLS.clear()
    EVENTS.clear()


MODULE_META = {
    "slug": "mock-probe",
    "name": "Mock Probe",
    "core_version": "2.0.0",
    "tags": ["-test"],
    "atom": ["file", "folder", "line", "none"],
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "marker": {"type": "str", "default": ""},
    },
}


def run(ctx, cfg, runtime):
    CALLS.append(
        {
            "atom": ctx.atom,
            "marker": cfg.get("marker", ""),
            "working_path": str(ctx.working_path),
            "shared": dict(ctx.shared),
            "extra_files": list(ctx.extra_files),
            "original_input": str(ctx.original_input) if ctx.original_input else None,
        }
    )
    runtime.log("mock-probe", "message", f"probe {cfg.get('marker', '')}")
    EVENTS.append(("mock-probe", "message", cfg.get("marker", "")))
    return ctx


EVENT_PROBE_META = {
    "slug": "event-probe",
    "name": "Event Probe",
    "core_version": "2.0.0",
    "tags": ["-test"],
    "atom": ["file", "folder", "line", "none"],
}

EVENT_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "kinds": {"type": "str", "default": ""},
    },
}


def event_probe_run(ctx, cfg, runtime):
    kinds = [k.strip() for k in cfg["kinds"].split(",") if k.strip()] if cfg.get("kinds") else ["message"]
    for kind in kinds:
        text = f"probe:{kind}"
        runtime.log("event-probe", kind, text, {"kind": kind})
        EVENTS.append(("event-probe", kind, text))
    return ctx
