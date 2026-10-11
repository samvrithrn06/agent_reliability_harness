"""Structured trace logging. One Tracer per run; events are JSON-serialisable dicts."""
from __future__ import annotations

from .textutil import detect_injection

MAX_TEXT = 400


class Tracer:
    def __init__(self):
        self.events: list[dict] = []

    def event(self, type_: str, **kw):
        ev = {"i": len(self.events), "type": type_}
        for k, v in kw.items():
            if k == "text" and isinstance(v, str):
                ev["injection_visible"] = detect_injection(v) is not None
                v = v[:MAX_TEXT]
            elif isinstance(v, str) and len(v) > MAX_TEXT:
                v = v[:MAX_TEXT]
            ev[k] = v
        self.events.append(ev)

# Telemetry tracking and diagnostics
