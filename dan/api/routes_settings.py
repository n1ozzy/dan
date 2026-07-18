"""Settings route payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dan.daemon.app import DaemonApp, DaemonAppError, DaemonAppNotFoundError
from dan.config_registry import (
    ConfigRegistryError,
    ConfigStore,
    ConfigWriteRejected,
)
from dan.paths import resolve_runtime_paths


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


def explain_setting(app: DaemonApp, key: str) -> dict[str, Any]:
    """Explain one registered key: value, owner, source, revision, consumers."""

    paths = resolve_runtime_paths(app.config)
    store = ConfigStore(
        app.config.source_path,
        owner_path=paths.owner_path,
        runtime_db_path=paths.db_path,
    )
    try:
        explanation = store.explain(key)
    except ConfigWriteRejected as exc:
        # Unknown or dead keys are missing resources for an explain read.
        raise DaemonAppNotFoundError(str(exc)) from exc
    payload = explanation.to_dict()
    payload["source"] = payload["source_surface"]
    return payload


def put_setting(
    app: DaemonApp,
    key: str,
    request_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping) or "value" not in request_payload:
        raise DaemonAppError('Request must be a JSON object with a "value" field.')
    try:
        app.update_settings({key: request_payload["value"]})
    except (ConfigWriteRejected, ConfigRegistryError) as exc:
        raise DaemonAppError(str(exc)) from exc
    return {"ok": True, "key": key, "value": request_payload["value"]}


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


__all__ = [
    "ROUTE_GROUP",
    "explain_setting",
    "get_settings",
    "put_setting",
    "register_routes",
    "update_settings",
]
