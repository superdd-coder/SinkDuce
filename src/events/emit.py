"""Generic event emission for domain modules outside file_mgmt.

`file_mgmt.events.emit_event` predates the bus and keeps its collection-scoped
signature for the ~40 existing call sites. New domains (tasks, sessions,
meetings, notes, …) use `emit` instead, which takes an explicit scope and derives
its origin from the request context.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§5, §8).
"""

from __future__ import annotations

from typing import Any

from src.events.models import EventEnvelope


def emit(
    event_type: str,
    *,
    scope: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    origin: dict[str, Any] | None = None,
) -> EventEnvelope:
    """Publish one change event.

    `origin` defaults to the current request's client identity. A background
    thread has no request context, so the origin is left bare and the event is
    delivered to every client — correct for server-side completions.
    """
    # Imported at call time so tests can swap the process-wide bus.
    from src.events.bus import bus
    from src.events.context import current_client
    from src.events.models import make_event

    if origin is None:
        origin = current_client().as_origin()
    return bus.publish(make_event(event_type, scope=scope, payload=payload, origin=origin))
