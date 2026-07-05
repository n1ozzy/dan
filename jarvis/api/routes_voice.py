"""Voice listening/PTT route payloads (G2, CONTRACTS §8, PANEL_CONTRACT §2)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.daemon.app import DaemonApp
from jarvis.voice.listening import ALLOWED_SOURCES
from jarvis.voice.models import ListeningLease


ROUTE_GROUP = "voice"


class VoiceDisabledError(Exception):
    """Raised when a voice mutation arrives while voice is disabled."""


class VoiceRequestValidationError(ValueError):
    """Raised when a voice API payload is invalid."""


def _lease_payload(lease: ListeningLease) -> dict[str, Any]:
    return {
        "id": lease.id,
        "mode": lease.mode,
        "source": lease.source,
        "status": lease.status,
        "created_at": lease.created_at,
        "expires_at": lease.expires_at,
        "released_at": lease.released_at,
    }


def _require_voice_enabled(app: DaemonApp) -> None:
    if not app.config.voice.enabled:
        raise VoiceDisabledError("Voice is disabled ([voice].enabled = false).")


def _source_from_request(payload: Any, default: str) -> str:
    if payload is None:
        return default
    if not isinstance(payload, Mapping):
        raise VoiceRequestValidationError("Request JSON must be an object.")
    source = payload.get("source", default)
    if not isinstance(source, str) or not source.strip():
        raise VoiceRequestValidationError("source must be a non-empty string.")
    return source.strip()


def _validated_source_from_request(payload: Any, default: str) -> str:
    source = _source_from_request(payload, default)
    if source not in ALLOWED_SOURCES:
        raise VoiceRequestValidationError(f"Unknown listening source {source!r}.")
    return source


def post_ptt_down(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    source = _validated_source_from_request(request_payload, "ptt")
    cancellation = app.cancel_active_speech(reason="ptt_down")
    lease = app.acquire_listening_lease(mode="hold", source=source)
    return {"ok": True, "lease": _lease_payload(lease), "cancellation": cancellation}


def post_ptt_up(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    released = app.release_listening_leases(mode="hold")
    return {"ok": True, "released": len(released)}


def post_listen_lock(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    source = _source_from_request(request_payload, "lock")
    lease = app.acquire_listening_lease(mode="locked", source=source)
    return {"ok": True, "lease": _lease_payload(lease)}


def post_listen_unlock(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    _require_voice_enabled(app)
    released = app.release_listening_leases(mode="locked")
    return {"ok": True, "released": len(released)}


def get_listening(app: DaemonApp) -> dict[str, Any]:
    leases = app.active_listening_leases()
    return {
        "listening": bool(leases),
        "voice_enabled": bool(app.config.voice.enabled),
        "leases": [_lease_payload(lease) for lease in leases],
    }


def get_voice_queue(app: DaemonApp, *, limit: int = 20) -> dict[str, Any]:
    if type(limit) is not int or limit <= 0 or limit > 100:
        raise VoiceRequestValidationError("limit must be an integer between 1 and 100.")
    return {"voice_queue": app.list_voice_queue(limit=limit), "limit": limit}


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "VoiceDisabledError",
    "VoiceRequestValidationError",
    "get_listening",
    "get_voice_queue",
    "post_listen_lock",
    "post_listen_unlock",
    "post_ptt_down",
    "post_ptt_up",
    "register_routes",
]
