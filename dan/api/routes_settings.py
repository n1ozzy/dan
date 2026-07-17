"""Settings route payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dan.daemon.app import DaemonApp, DaemonAppError
from dan.config_registry import ConfigStore, ConfigWriteRejected


ROUTE_GROUP = "settings"


def get_settings(app: DaemonApp) -> dict[str, Any]:
    settings = ConfigStore(app.config.source_path).installation_snapshot()
    settings.update(app.get_settings())
    return {"settings": settings}


def update_settings(app: DaemonApp, request_payload: Mapping[str, Any]) -> dict[str, Any]:
    updates = _settings_from_request(request_payload)
    try:
        app.update_settings(updates)
        return get_settings(app)
    except ConfigWriteRejected as exc:
        raise DaemonAppError(str(exc)) from exc


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
