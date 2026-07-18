"""GET /sessions: truthful session/model usage for the operator panel.

Task 10 contract: report only what the daemon actually owns (session token
counters, brain adapter name, queue state, health blockers). A metric the
daemon does not measure is ``"unknown"`` — never a synthesized green value.
Only IDs and sizes leave the daemon; utterance text stays inside.
"""

from __future__ import annotations

from typing import Any

from dan.daemon.app import DaemonApp

ROUTE_GROUP = "sessions"

UNKNOWN = "unknown"

# The claimed row first (audible now), then the one being synthesized.
_ACTIVE_STATUS_ORDER = ("speaking", "synthesizing")


def get_sessions(app: DaemonApp) -> dict[str, Any]:
    snapshot = app.snapshot_state()
    rows = _safe_queue_rows(app)

    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or UNKNOWN)
        counts[status] = counts.get(status, 0) + 1

    return {
        "sessions": {
            "read_only": True,
            "daemon": {
                "ok": bool(snapshot.get("ok")),
                "started": bool(snapshot.get("started")),
                "state": snapshot.get("state") or UNKNOWN,
            },
            "brain": {
                "adapter": snapshot.get("brain_adapter") or UNKNOWN,
                # The daemon does not own the provider's effective model id;
                # claiming one here would be fiction.
                "model": UNKNOWN,
            },
            "usage": {
                "session_tokens_in": snapshot.get("session_tokens_in"),
                "session_tokens_out": snapshot.get("session_tokens_out"),
                "cost": UNKNOWN,
                "context_window": UNKNOWN,
            },
            "voice_queue": {
                "counts": counts,
                "active_request": _active_request(rows),
            },
            "hotkey": snapshot.get("hotkey"),
            "health_blockers": _health_blockers(snapshot),
        }
    }


def _safe_queue_rows(app: DaemonApp) -> list[dict[str, Any]]:
    try:
        return app.list_voice_queue(limit=100)
    except Exception:  # noqa: BLE001 - a stopped daemon still answers /sessions
        return []


def _active_request(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for status in _ACTIVE_STATUS_ORDER:
        claimed = [row for row in rows if str(row.get("status")) == status]
        if claimed:
            # rows are newest-first; the broker claims oldest-first.
            row = claimed[-1]
            return {
                "id": row.get("id"),
                "status": status,
                "text_length": row.get("text_length"),
                "playback_confirmed": bool(row.get("playback_confirmed")),
                "created_at": row.get("created_at"),
                "spoken_at": row.get("spoken_at"),
            }
    return None


def _health_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not snapshot.get("ok"):
        blockers.append("daemon is not healthy")
    if not snapshot.get("started"):
        blockers.append("daemon app is not started")
    hotkey = snapshot.get("hotkey")
    if isinstance(hotkey, dict) and hotkey.get("blocker"):
        blockers.append(f"hotkey: {hotkey['blocker']}")
    return blockers


def register_routes(app: object) -> None:
    return None


__all__ = ["ROUTE_GROUP", "UNKNOWN", "get_sessions", "register_routes"]
