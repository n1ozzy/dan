"""Persisted VoiceQueue (G3, CONTRACTS §7, ADR-005).

One sentence = one row in voice_queue. Statuses: queued -> speaking ->
done | cancelled | failed, with the frozen voice.speak.* event family.
Only the broker claims work; queued items recover after a restart.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.events.types import EventType
from jarvis.store.repositories import utc_now_iso
from jarvis.voice.models import VoiceRequest


# A tombstone only needs to outlive a cancelled turn's last in-flight delta /
# filler (seconds); an hour is a generous bound that keeps the table tiny.
TOMBSTONE_TTL_SECONDS = 3600


class VoiceQueueError(Exception):
    """Raised on invalid queue operations."""


class VoiceQueueCancelledError(VoiceQueueError):
    """Raised by enqueue for a turn a barge-in already tombstoned (FIX-09).

    A subclass of VoiceQueueError, so the best-effort speech paths that already
    swallow queue errors keep muting cleanly instead of failing a turn."""


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
        if turn_id is not None and self.is_tombstoned(turn_id):
            # Barge-in already cancelled this turn: an in-flight delta or a late
            # FillerTimer must not enqueue a fresh 'queued' row that would then
            # be played after the cancel sweep already ran (FIX-09).
            raise VoiceQueueCancelledError(
                f"turn {turn_id} was cancelled; refusing a new speech row."
            )
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

        order_clause = """
            SELECT id FROM voice_queue AS vq
            WHERE status = 'queued'
            ORDER BY priority DESC,
                     -- Group by turn (turns ordered by their first row), so
                     -- seq — which is per-turn — never interleaves two turns
                     -- (FIX-09). `IS` groups the NULL-turn rows together.
                     (SELECT MIN(vq2.rowid) FROM voice_queue AS vq2
                      WHERE vq2.turn_id IS vq.turn_id) ASC,
                     CAST(json_extract(vq.metadata_json, '$.seq') AS INTEGER) ASC,
                     vq.rowid ASC
            LIMIT 1
        """
        try:
            row = self._conn.execute(
                f"""
                WITH candidate AS ({order_clause})
                UPDATE voice_queue
                SET status = 'speaking', updated_at = ?
                WHERE id = (SELECT id FROM candidate)
                RETURNING id
                """,
                (self._now(),),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            # Fallback for older SQLite versions without RETURNING support.
            if "RETURNING" not in str(exc):
                raise
            row = self._conn.execute(order_clause).fetchone()
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
        if row is None:
            return None
        request_id = str(row[0])
        self._append_event(EventType.VOICE_SPEAK_STARTED, {"request_id": request_id})
        return self._by_id(request_id)

    def mark_spoken(self, request_id: str) -> None:
        """Stamp the moment a row actually reaches the speaker (broker pre-play).

        Only a still-'speaking' row is stamped, and only once. spoken_at — not
        the final status — is what the anti-echo corpus reads, so a 'queued' row
        flipped to 'cancelled' by barge-in (never played) stays out of it."""

        with self._conn:
            self._conn.execute(
                "UPDATE voice_queue SET spoken_at = ? "
                "WHERE id = ? AND status = 'speaking' AND spoken_at IS NULL",
                (self._now(), request_id),
            )

    def mark_done(self, request_id: str) -> None:
        self._finish(request_id, "done", EventType.VOICE_SPEAK_FINISHED, None)

    def mark_failed(self, request_id: str, *, error: str) -> None:
        self._finish(request_id, "failed", EventType.VOICE_SPEAK_FAILED, error)

    def cancel_turn(
        self,
        turn_id: str,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
        cancelled_request_ids: list[str] | None = None,
    ) -> int:
        """Cancel every unfinished request of one turn (barge-in leg 2)."""

        rows = self._conn.execute(
            """
            SELECT id
            FROM voice_queue
            WHERE turn_id = ? AND status IN ('queued', 'speaking')
            ORDER BY rowid DESC
            """,
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
                {
                    "request_id": str(request_id),
                    "turn_id": turn_id,
                    "reason": reason,
                    "interruption_source": interruption_source,
                },
            )
            if cancelled_request_ids is not None:
                cancelled_request_ids.append(str(request_id))
        return len(rows)

    def cancel_request(
        self,
        request_id: str,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
    ) -> bool:
        """Cancel exactly one unfinished request without touching its turn."""

        row = self._conn.execute(
            "SELECT turn_id FROM voice_queue WHERE id = ? AND status IN ('queued', 'speaking')",
            (request_id,),
        ).fetchone()
        if row is None:
            return False
        now = self._now()
        with self._conn:
            updated = self._conn.execute(
                "UPDATE voice_queue SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, request_id),
            ).rowcount
        if updated != 1:
            return False
        self._append_event(
            EventType.VOICE_SPEAK_CANCELLED,
            {
                "request_id": request_id,
                "turn_id": str(row[0]) if row[0] else None,
                "reason": reason,
                "interruption_source": interruption_source,
            },
        )
        return True

    def tombstone_turns(self, turn_ids: Iterable[str]) -> int:
        """Mark turns as cancelled so enqueue refuses new rows for them.

        Idempotent (INSERT OR IGNORE) and self-bounding: each call also prunes
        tombstones older than the TTL, so the table never grows without limit."""

        ids = [str(turn_id) for turn_id in turn_ids if turn_id]
        if not ids:
            return 0
        now = self._now()
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO cancelled_turns (turn_id, cancelled_at) VALUES (?, ?)",
                [(turn_id, now) for turn_id in ids],
            )
            self._conn.execute(
                "DELETE FROM cancelled_turns WHERE cancelled_at < ?",
                (_tombstone_cutoff(now),),
            )
        return len(ids)

    def is_tombstoned(self, turn_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM cancelled_turns WHERE turn_id = ? LIMIT 1", (turn_id,)
        ).fetchone()
        return row is not None

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
            SELECT id, text, priority, status, interrupt_policy, turn_id, voice_id, created_at
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
            interrupt_policy=str(row[4]),
            turn_id=str(row[5]) if row[5] else None,
            voice=str(row[6]) if row[6] else None,
            created_at=str(row[7]),
        )

    def _append_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._event_store is not None:
            self._event_store.append(event_type, "voice", payload)


def _tombstone_cutoff(now_iso: str) -> str:
    """The oldest cancelled_at to keep; best effort — an unparseable clock skips
    pruning (returns "") rather than risk wiping still-needed tombstones."""

    try:
        moment = datetime.fromisoformat(now_iso)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = moment - timedelta(seconds=TOMBSTONE_TTL_SECONDS)
    return cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = ["VoiceQueue", "VoiceQueueCancelledError", "VoiceQueueError"]
