"""Runtime state route payloads."""

from __future__ import annotations

from typing import Any

from jarvis.daemon.app import DaemonApp


ROUTE_GROUP = "state"


def get_state(app: DaemonApp) -> dict[str, Any]:
    payload = app.snapshot_state()
    payload["allowed_state_targets"] = app.allowed_state_targets()
    return payload


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "get_state", "register_routes"]
