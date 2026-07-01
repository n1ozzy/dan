"""Health route payloads."""

from __future__ import annotations

from typing import Any

from jarvis.daemon.app import DaemonApp


ROUTE_GROUP = "health"


def get_health(app: DaemonApp) -> dict[str, Any]:
    snapshot = app.snapshot_state()
    return {
        "ok": snapshot["ok"],
        "service": snapshot["service"],
        "state": snapshot["state"],
        "started": snapshot["started"],
        "schema_version": snapshot["schema_version"],
    }


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "get_health", "register_routes"]
