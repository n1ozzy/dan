"""Settings route payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.daemon.app import DaemonApp, DaemonAppError


ROUTE_GROUP = "settings"


def get_settings(app: DaemonApp) -> dict[str, Any]:
    return {"settings": app.get_settings()}


def update_settings(app: DaemonApp, request_payload: Mapping[str, Any]) -> dict[str, Any]:
    updates = _settings_from_request(request_payload)
    return {"settings": app.update_settings(updates)}


def _settings_from_request(request_payload: Mapping[str, Any]) -> dict[str, Any]:
    if "settings" in request_payload:
        settings = request_payload["settings"]
        if not isinstance(settings, Mapping):
            raise DaemonAppError("settings must be a JSON object.")
        return dict(settings)

    if "key" in request_payload:
        key = request_payload["key"]
        if not isinstance(key, str) or not key.strip():
            raise DaemonAppError("key must be a non-empty string.")
        if "value" not in request_payload:
            raise DaemonAppError("value is required when key is provided.")
        return {key: request_payload["value"]}

    raise DaemonAppError("Request must include key/value or settings.")


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "get_settings", "register_routes", "update_settings"]
