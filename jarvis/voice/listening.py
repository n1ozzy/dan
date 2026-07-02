"""ListeningLease manager (G2, CONTRACTS §8, ADR-006).

Leases are DB rows — never a /tmp flag. Modes: `hold` (PTT button held) and
`locked` (sticky listen). Releasing holds never clears a locked lease, and a
stale lease expires lazily instead of listening forever. The recorder runs
exactly while at least one lease is active.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.events.types import EventType
from jarvis.store.repositories import utc_now_iso
from jarvis.voice.models import ListeningLease


ALLOWED_MODES = ("hold", "locked")
# Who may ask to listen: the PTT button, the global hotkey, and the sticky
# lock. Never the model and never an automation source.
ALLOWED_SOURCES = ("ptt", "global_hotkey", "lock")
DEFAULT_HOLD_TTL_SECONDS = 30
DEFAULT_LOCK_TTL_SECONDS = 600


class ListeningLeaseError(Exception):
    """Raised on invalid lease requests (unknown mode/source)."""


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ListeningLeaseManager:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        config: Any,
        recorder: Any,
        event_store: Any | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._config = config
        self._recorder = recorder
        self._event_store = event_store
        self._now = now or utc_now_iso

    # -- public API -------------------------------------------------------

    def acquire(self, *, mode: str, source: str) -> ListeningLease:
        if mode not in ALLOWED_MODES:
            raise ListeningLeaseError(f"Unknown listening mode {mode!r}.")
        if source not in ALLOWED_SOURCES:
            raise ListeningLeaseError(f"Unknown listening source {source!r}.")

        self._expire_stale()
        now = self._now()
        expires_at = self._expiry_for(mode, now)

        existing = self._active_rows(mode=mode, source=source)
        if existing:
            lease_id = existing[0][0]
            with self._conn:
                self._conn.execute(
                    "UPDATE listening_leases SET expires_at = ?, updated_at = ? WHERE id = ?",
                    (expires_at, now, lease_id),
                )
            return self._lease_by_id(lease_id)

        lease_id = uuid.uuid4().hex
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO listening_leases (
                  id, created_at, updated_at, released_at, expires_at,
                  source, mode, status, owner_process, turn_id, metadata_json
                )
                VALUES (?, ?, ?, NULL, ?, ?, ?, 'active', NULL, NULL, '{}')
                """,
                (lease_id, now, now, expires_at, source, mode),
            )
        self._append_event(
            EventType.LISTENING_LEASE_CREATED,
            {"lease_id": lease_id, "mode": mode, "source": source},
        )
        self._sync_recorder()
        return self._lease_by_id(lease_id)

    def release(self, *, mode: str) -> list[ListeningLease]:
        """Release all active leases of one mode (hold never touches locked)."""

        if mode not in ALLOWED_MODES:
            raise ListeningLeaseError(f"Unknown listening mode {mode!r}.")
        self._expire_stale()
        now = self._now()
        rows = self._active_rows(mode=mode)
        released: list[ListeningLease] = []
        for (lease_id,) in [(row[0],) for row in rows]:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE listening_leases
                    SET status = 'released', released_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, lease_id),
                )
            self._append_event(
                EventType.LISTENING_LEASE_RELEASED,
                {"lease_id": lease_id, "mode": mode},
            )
            released.append(self._lease_by_id(lease_id))
        self._sync_recorder()
        return released

    def active(self) -> list[ListeningLease]:
        self._expire_stale()
        return [self._row_to_lease(row) for row in self._active_full_rows()]

    def is_listening(self) -> bool:
        return bool(self.active())

    # -- internals ---------------------------------------------------------

    def _expiry_for(self, mode: str, now_iso: str) -> str:
        if mode == "hold":
            ttl = int(getattr(self._config, "ptt_hold_ttl_seconds", DEFAULT_HOLD_TTL_SECONDS))
        else:
            ttl = int(getattr(self._config, "listen_lock_ttl_seconds", DEFAULT_LOCK_TTL_SECONDS))
        expires = _parse_iso(now_iso).astimezone(UTC) + timedelta(seconds=ttl)
        return expires.isoformat(timespec="seconds")

    def _expire_stale(self) -> None:
        now = self._now()
        now_dt = _parse_iso(now)
        stale = [
            row
            for row in self._active_full_rows()
            if _parse_iso(str(row[4])) <= now_dt
        ]
        for row in stale:
            lease_id = row[0]
            with self._conn:
                self._conn.execute(
                    "UPDATE listening_leases SET status = 'expired', updated_at = ? WHERE id = ?",
                    (now, lease_id),
                )
            self._append_event(
                EventType.LISTENING_LEASE_EXPIRED,
                {"lease_id": lease_id, "mode": str(row[6])},
            )
        if stale:
            self._sync_recorder()

    def _sync_recorder(self) -> None:
        if self._active_full_rows():
            self._recorder.start()
        else:
            self._recorder.stop()

    def _active_rows(self, *, mode: str, source: str | None = None) -> list[tuple]:
        query = "SELECT id FROM listening_leases WHERE status = 'active' AND mode = ?"
        params: list[Any] = [mode]
        if source is not None:
            query += " AND source = ?"
            params.append(source)
        return self._conn.execute(query, params).fetchall()

    def _active_full_rows(self) -> list[tuple]:
        return self._conn.execute(
            """
            SELECT id, created_at, released_at, source, expires_at, status, mode
            FROM listening_leases
            WHERE status = 'active'
            ORDER BY created_at ASC
            """
        ).fetchall()

    def _lease_by_id(self, lease_id: str) -> ListeningLease:
        row = self._conn.execute(
            """
            SELECT id, created_at, released_at, source, expires_at, status, mode
            FROM listening_leases WHERE id = ?
            """,
            (lease_id,),
        ).fetchone()
        return self._row_to_lease(row)

    @staticmethod
    def _row_to_lease(row: tuple) -> ListeningLease:
        return ListeningLease(
            id=str(row[0]),
            mode=str(row[6]),
            source=str(row[3]),
            status=str(row[5]),
            created_at=str(row[1]),
            expires_at=str(row[4]),
            released_at=str(row[2]) if row[2] else None,
        )

    def _append_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._event_store is not None:
            self._event_store.append(event_type, "voice", payload)


__all__ = [
    "ALLOWED_MODES",
    "ALLOWED_SOURCES",
    "ListeningLeaseError",
    "ListeningLeaseManager",
]
