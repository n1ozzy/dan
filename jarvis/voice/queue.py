"""Persisted VoiceQueue (G3, CONTRACTS §7, ADR-005).

One sentence = one row in voice_queue. Statuses: queued -> speaking ->
done | cancelled | failed, with the frozen voice.speak.* event family.
Only the broker claims work; queued items recover after a restart.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from typing import Any

from jarvis.events.types import EventType
from jarvis.store.repositories import utc_now_iso
from jarvis.voice.models import VoiceRequest


class VoiceQueueError(Exception):
    """Raised on invalid queue operations."""


class VoiceQueue:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        event_store: Any | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso

    def enqueue(
        self,
        *,
        text: str,
        turn_id: str | None,
        kind: str = "sentence",
        seq: int = 0,
        priority: int = 0,
        voice_id: str | None = None,
        interrupt_policy: str = "no_interrupt",
    ) -> VoiceRequest:
        if not isinstance(text, str) or not text.strip():
            raise VoiceQueueError("text must be a non-empty string.")
        request_id = uuid.uuid4().hex
        now = self._now()
        metadata = {"kind": kind, "seq": int(seq)}
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO voice_queue (
                  id, created_at, updated_at, turn_id, text, priority,
                  voice_id, interrupt_policy, status, error, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, ?)
                """,
                (
                    request_id,
                    now,
                    now,
                    turn_id,
                    text.strip(),
                    int(priority),
                    voice_id,
                    interrupt_policy,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
        self._append_event(
            EventType.VOICE_SPEAK_QUEUED,
            {"request_id": request_id, "turn_id": turn_id, "kind": kind, "seq": int(seq)},
        )
        return self._by_id(request_id)

    def claim_next(self) -> VoiceRequest | None:
        """Move the next queued request to speaking (broker-only path)."""

        row = self._conn.execute(
            """
            SELECT id FROM voice_queue
            WHERE status = 'queued'
            ORDER BY priority DESC,
                     CAST(json_extract(metadata_json, '$.seq') AS INTEGER) ASC,
                     rowid ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        request_id = str(row[0])
        with self._conn:
            updated = self._conn.execute(
                "UPDATE voice_queue SET status = 'speaking', updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (self._now(), request_id),
            ).rowcount
        if updated != 1:
            return None
        self._append_event(EventType.VOICE_SPEAK_STARTED, {"request_id": request_id})
        return self._by_id(request_id)

    def mark_done(self, request_id: str) -> None:
        self._finish(request_id, "done", EventType.VOICE_SPEAK_FINISHED, None)

    def mark_failed(self, request_id: str, *, error: str) -> None:
        self._finish(request_id, "failed", EventType.VOICE_SPEAK_FAILED, error)

    def cancel_turn(self, turn_id: str) -> int:
        """Cancel every unfinished request of one turn (barge-in leg 2)."""

        rows = self._conn.execute(
            "SELECT id FROM voice_queue WHERE turn_id = ? AND status IN ('queued', 'speaking')",
            (turn_id,),
        ).fetchall()
        now = self._now()
        for (request_id,) in rows:
            with self._conn:
                self._conn.execute(
                    "UPDATE voice_queue SET status = 'cancelled', updated_at = ? WHERE id = ?",
                    (now, request_id),
                )
            self._append_event(
                EventType.VOICE_SPEAK_CANCELLED,
                {"request_id": str(request_id), "turn_id": turn_id},
            )
        return len(rows)

    def recover_orphans(self) -> int:
        """Requeue speaking rows orphaned by a restart (queued items recover)."""

        with self._conn:
            return self._conn.execute(
                "UPDATE voice_queue SET status = 'queued', updated_at = ? "
                "WHERE status = 'speaking'",
                (self._now(),),
            ).rowcount

    def pending_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM voice_queue WHERE status IN ('queued', 'speaking')"
            ).fetchone()[0]
        )

    # -- internals ---------------------------------------------------------

    def _finish(
        self,
        request_id: str,
        status: str,
        event_type: EventType,
        error: str | None,
    ) -> None:
        with self._conn:
            updated = self._conn.execute(
                "UPDATE voice_queue SET status = ?, error = ?, updated_at = ? "
                "WHERE id = ? AND status = 'speaking'",
                (status, error, self._now(), request_id),
            ).rowcount
        if updated == 1:
            payload: dict[str, Any] = {"request_id": request_id}
            if error:
                payload["error"] = error
            self._append_event(event_type, payload)

    def _by_id(self, request_id: str) -> VoiceRequest:
        row = self._conn.execute(
            """
            SELECT id, text, priority, status, turn_id, voice_id, created_at
            FROM voice_queue WHERE id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise VoiceQueueError(f"Unknown voice request {request_id!r}.")
        return VoiceRequest(
            id=str(row[0]),
            text=str(row[1]),
            priority=int(row[2]),
            status=str(row[3]),
            turn_id=str(row[4]) if row[4] else None,
            voice=str(row[5]) if row[5] else None,
            created_at=str(row[6]),
        )

    def _append_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._event_store is not None:
            self._event_store.append(event_type, "voice", payload)


__all__ = ["VoiceQueue", "VoiceQueueError"]
