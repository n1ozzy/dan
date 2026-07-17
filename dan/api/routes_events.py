"""Event polling route payloads."""

from __future__ import annotations

from typing import Any

from dan.api.event_safety import safe_event_payload_for_client
from dan.daemon.app import DaemonApp
from dan.events.models import Event


ROUTE_GROUP = "events"


def get_events(
    app: DaemonApp,
    *,
    after_id: int = 0,
    limit: int = 100,
    latest: bool = False,
) -> dict[str, Any]:
    events = (
        app.list_latest_events(limit=limit)
        if latest
        else app.list_events_after(after_id, limit=limit)
    )
    return {
        "events": [event_to_dict(event) for event in events],
        "after_id": after_id,
        "limit": limit,
        "latest": latest,
        "latest_event_id": app.snapshot_state()["latest_event_id"],
    }


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "created_at": event.created_at,
        "type": event.type,
        "source": event.source,
        "correlation_id": event.correlation_id,
        "turn_id": event.turn_id,
        "payload": safe_event_payload_for_client(event),
    }


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "event_to_dict", "get_events", "register_routes"]
