"""Transactional persisted voice queue with immutable render snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from dan.events.types import EventType
from dan.store.event_store import create_event_store
from dan.store.repositories import utc_now_iso
from dan.voice.models import RenderSnapshot, SpeechIntent, VoiceRequest

TOMBSTONE_TTL_SECONDS = 3600
VOICE_QUEUE_POLICY_VERSION = 1
DEFAULT_GLOBAL_PENDING_LIMIT = 100
DEFAULT_SESSION_PENDING_LIMIT = 20
_PENDING_STATUSES = ("queued", "synthesizing", "speaking")


class VoiceQueueError(Exception):
    """Raised on invalid queue operations."""


class VoiceQueueCancelledError(VoiceQueueError):
    """Raised when a cancelled session attempts to enqueue late speech."""


class QueueBackpressure(VoiceQueueError):
    """Raised when versioned admission limits reject a request."""


class VoiceQueue:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        event_store: Any | None = None,
        now: Callable[[], str] | None = None,
        global_pending_limit: int = DEFAULT_GLOBAL_PENDING_LIMIT,
        session_pending_limit: int = DEFAULT_SESSION_PENDING_LIMIT,
    ) -> None:
        if global_pending_limit <= 0 or session_pending_limit <= 0:
            raise ValueError("voice queue pending limits must be positive")
        event_connection = getattr(event_store, "connection", None)
        if event_store is not None and event_connection is not conn:
            raise VoiceQueueError(
                "voice queue and event store must use the same SQLite connection"
            )
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso
        self._global_pending_limit = int(global_pending_limit)
        self._session_pending_limit = int(session_pending_limit)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def enqueue(self, intent: SpeechIntent, snapshot: RenderSnapshot) -> VoiceRequest:
        """Persist producer intent and the resolver's complete snapshot atomically."""

        if not isinstance(intent, SpeechIntent):
            raise VoiceQueueError("enqueue requires a SpeechIntent")
        if not isinstance(snapshot, RenderSnapshot):
            raise VoiceQueueError("enqueue requires a RenderSnapshot")
        snapshot.validate_complete()

        request_id = uuid.uuid4().hex
        now = self._now()
        metadata = {
            "kind": "filler" if intent.interrupt_policy == "interruptible" else "sentence",
            "queue_policy_version": VOICE_QUEUE_POLICY_VERSION,
            "seq": intent.utterance_index,
        }
        self._begin_immediate()
        try:
            # The tombstone check must run inside the write transaction:
            # a cancellation landing between a pre-transaction check and
            # BEGIN IMMEDIATE would otherwise let a late chunk slip in.
            if self.is_tombstoned(intent.session):
                raise VoiceQueueCancelledError(
                    f"session {intent.session} was cancelled; refusing new speech"
                )
            global_count = self._pending_count_in_transaction()
            if global_count >= self._global_pending_limit:
                raise QueueBackpressure(
                    f"global voice queue limit {self._global_pending_limit} reached"
                )
            session_count = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM voice_queue
                    WHERE session_id = ? AND status IN ('queued', 'synthesizing', 'speaking')
                    """,
                    (intent.session,),
                ).fetchone()[0]
            )
            if session_count >= self._session_pending_limit:
                raise QueueBackpressure(
                    f"voice queue session {intent.session!r} limit "
                    f"{self._session_pending_limit} reached"
                )
            self._conn.execute(
                """
                INSERT INTO voice_queue (
                  id, created_at, updated_at, turn_id, text, priority, voice_id,
                  interrupt_policy, status, error, metadata_json, spoken_at,
                  source, session_id, participant, persona, lane, utterance_index,
                  render_snapshot_json, synthesis_started_at, synthesis_completed_at,
                  playback_started_at, playback_completed_at, playback_confirmed
                )
                VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, ?, NULL,
                  ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0
                )
                """,
                (
                    request_id,
                    now,
                    now,
                    intent.session,
                    intent.text,
                    intent.priority,
                    snapshot.voice_or_style,
                    intent.interrupt_policy,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    intent.source,
                    intent.session,
                    intent.participant,
                    intent.persona,
                    intent.lane,
                    intent.utterance_index,
                    snapshot.canonical_json(),
                ),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

        self._append_event(
            EventType.VOICE_SPEAK_QUEUED,
            {
                "request_id": request_id,
                "session_id": intent.session,
                "source": intent.source,
                "persona": intent.persona,
                "lane": intent.lane,
                "utterance_index": intent.utterance_index,
                "render_snapshot": json.loads(snapshot.canonical_json()),
            },
        )
        return self._by_id(request_id)

    def claim_next(self) -> VoiceRequest | None:
        """Claim one playable row for synthesis; no claim may jump to speaking."""

        order_clause = """
            SELECT id FROM voice_queue
            WHERE status = 'queued'
              AND source != 'legacy-migration'
              AND render_snapshot_json != 'legacy-unresolved'
            ORDER BY CASE lane
                       WHEN 'live' THEN 0
                       WHEN 'normal' THEN 1
                       ELSE 2
                     END ASC,
                     priority DESC,
                     rowid ASC
            LIMIT 1
        """
        now = self._now()
        try:
            row = self._conn.execute(
                f"""
                WITH candidate AS ({order_clause})
                UPDATE voice_queue
                SET status = 'synthesizing', synthesis_started_at = ?, updated_at = ?
                WHERE id = (SELECT id FROM candidate)
                RETURNING id
                """,
                (now, now),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "RETURNING" not in str(exc):
                raise
            row = self._conn.execute(order_clause).fetchone()
            if row is None:
                return None
            request_id = str(row[0])
            with self._conn:
                updated = self._conn.execute(
                    """
                    UPDATE voice_queue
                    SET status = 'synthesizing', synthesis_started_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (now, now, request_id),
                ).rowcount
            if updated != 1:
                return None
            row = (request_id,)
        if row is None:
            return None
        request_id = str(row[0])
        self._append_event("voice.speak.synthesis.started", {"request_id": request_id})
        return self._by_id(request_id)

    def mark_synthesis_complete(self, request_id: str) -> None:
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE voice_queue SET synthesis_completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'synthesizing' AND synthesis_completed_at IS NULL
                """,
                (self._now(), self._now(), request_id),
            ).rowcount
        if updated == 1:
            self._append_event("voice.speak.synthesis.completed", {"request_id": request_id})

    def mark_playback_started(self, request_id: str) -> None:
        now = self._now()
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE voice_queue
                SET status = 'speaking', spoken_at = ?, playback_started_at = ?, updated_at = ?
                WHERE id = ? AND status = 'synthesizing'
                  AND synthesis_completed_at IS NOT NULL
                """,
                (now, now, now, request_id),
            ).rowcount
        if updated != 1:
            raise VoiceQueueError(
                f"request {request_id!r} cannot start playback before synthesis completes"
            )
        self._append_event(EventType.VOICE_SPEAK_STARTED, {"request_id": request_id})

    def mark_spoken(self, request_id: str) -> None:
        """Compatibility alias for the native playback-start transition."""

        self.mark_playback_started(request_id)

    def mark_done(self, request_id: str) -> None:
        now = self._now()
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE voice_queue
                SET status = 'done', playback_completed_at = ?, playback_confirmed = 1,
                    updated_at = ?
                WHERE id = ? AND status = 'speaking'
                """,
                (now, now, request_id),
            ).rowcount
        if updated == 1:
            self._append_event(EventType.VOICE_SPEAK_FINISHED, {"request_id": request_id})

    def mark_failed(self, request_id: str, *, error: str) -> None:
        now = self._now()
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE voice_queue
                SET status = 'failed', error = ?, playback_completed_at = CASE
                      WHEN status = 'speaking' THEN ? ELSE playback_completed_at END,
                    playback_confirmed = 0, updated_at = ?
                WHERE id = ? AND status IN ('queued', 'synthesizing', 'speaking')
                """,
                (error, now, now, request_id),
            ).rowcount
        if updated == 1:
            self._append_event(
                EventType.VOICE_SPEAK_FAILED,
                {"request_id": request_id, "error": error},
            )

    def cancel_session(
        self,
        session_id: str,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
    ) -> list[str]:
        self._begin_immediate()
        try:
            rows = [
                (str(row[0]), str(row[1]))
                for row in self._conn.execute(
                    """
                    SELECT id, session_id FROM voice_queue
                    WHERE session_id = ?
                      AND status IN ('queued', 'synthesizing', 'speaking')
                    ORDER BY rowid DESC
                    """,
                    (session_id,),
                ).fetchall()
            ]
            self._tombstone_turns_in_transaction([session_id])
            request_ids = self._cancel_rows_in_transaction(
                rows,
                now=self._now(),
                reason=reason,
                interruption_source=interruption_source,
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return request_ids

    def cancel_active(
        self,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
        capture_generation_turn_ids: Callable[[], Iterable[str]] | None = None,
    ) -> tuple[list[str], list[str], list[str], int]:
        self._begin_immediate()
        try:
            rows = [
                (str(row[0]), str(row[1]))
                for row in self._conn.execute(
                    """
                    SELECT id, session_id FROM voice_queue
                    WHERE status IN ('queued', 'synthesizing', 'speaking')
                    ORDER BY rowid DESC
                    """
                ).fetchall()
            ]
            queue_turn_ids = _unique_nonempty(session_id for _, session_id in rows)
            generation_turn_ids = _unique_nonempty(
                capture_generation_turn_ids()
                if capture_generation_turn_ids is not None
                else ()
            )
            tombstoned_turn_ids = _unique_nonempty(
                [*generation_turn_ids, *queue_turn_ids]
            )
            tombstoned = self._tombstone_turns_in_transaction(tombstoned_turn_ids)
            request_ids = self._cancel_rows_in_transaction(
                rows,
                now=self._now(),
                reason=reason,
                interruption_source=interruption_source,
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return request_ids, queue_turn_ids, generation_turn_ids, tombstoned

    def _cancel_rows_in_transaction(
        self,
        rows: list[tuple[str, str]],
        *,
        now: str,
        reason: str | None,
        interruption_source: str | None,
    ) -> list[str]:
        request_ids: list[str] = []
        for request_id, session_id in rows:
            updated = self._conn.execute(
                """
                UPDATE voice_queue
                SET status = 'cancelled', playback_completed_at = CASE
                      WHEN status = 'speaking' THEN ? ELSE playback_completed_at END,
                    playback_confirmed = 0, updated_at = ?
                WHERE id = ? AND status IN ('queued', 'synthesizing', 'speaking')
                """,
                (now, now, request_id),
            ).rowcount
            if updated != 1:
                raise VoiceQueueError(
                    f"request {request_id!r} changed during cancellation"
                )
            request_ids.append(request_id)
        for request_id, session_id in rows:
            self._append_event_in_transaction(
                EventType.VOICE_SPEAK_CANCELLED,
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "turn_id": session_id,
                    "reason": reason,
                    "interruption_source": interruption_source,
                },
            )
        return request_ids

    def cancel_turn(
        self,
        turn_id: str,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
        cancelled_request_ids: list[str] | None = None,
    ) -> int:
        request_ids = self.cancel_session(
            turn_id,
            reason=reason,
            interruption_source=interruption_source,
        )
        if cancelled_request_ids is not None:
            cancelled_request_ids.extend(request_ids)
        return len(request_ids)

    def cancel_request(
        self,
        request_id: str,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
    ) -> bool:
        return self._cancel_request(
            request_id,
            reason=reason,
            interruption_source=interruption_source,
            tombstone_session=True,
        )

    def cancel_superseded_request(
        self,
        request_id: str,
        *,
        reason: str | None = None,
        interruption_source: str | None = None,
    ) -> bool:
        return self._cancel_request(
            request_id,
            reason=reason,
            interruption_source=interruption_source,
            tombstone_session=False,
        )

    def _cancel_request(
        self,
        request_id: str,
        *,
        reason: str | None,
        interruption_source: str | None,
        tombstone_session: bool,
    ) -> bool:
        self._begin_immediate()
        try:
            row = self._conn.execute(
                """
                SELECT id, session_id FROM voice_queue
                WHERE id = ? AND status IN ('queued', 'synthesizing', 'speaking')
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                self._conn.rollback()
                return False
            session_id = str(row[1])
            if tombstone_session:
                self._tombstone_turns_in_transaction([session_id])
            self._cancel_rows_in_transaction(
                [(str(row[0]), session_id)],
                now=self._now(),
                reason=reason,
                interruption_source=interruption_source,
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return True

    def tombstone_turns(self, turn_ids: Iterable[str]) -> int:
        ids = _unique_nonempty(turn_ids)
        if not ids:
            return 0
        self._begin_immediate()
        try:
            count = self._tombstone_turns_in_transaction(ids)
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return count

    def _tombstone_turns_in_transaction(self, turn_ids: Iterable[str]) -> int:
        if not self._conn.in_transaction:
            raise VoiceQueueError("tombstones require an active transaction")
        ids = _unique_nonempty(turn_ids)
        if not ids:
            return 0
        now = self._now()
        self._conn.executemany(
            """
            INSERT INTO cancelled_turns (turn_id, cancelled_at) VALUES (?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET cancelled_at = excluded.cancelled_at
            """,
            [(turn_id, now) for turn_id in ids],
        )
        self._conn.execute(
            "DELETE FROM cancelled_turns WHERE cancelled_at < ?",
            (_tombstone_cutoff(now),),
        )
        placeholders = ",".join("?" for _ in ids)
        return int(
            self._conn.execute(
                f"SELECT COUNT(*) FROM cancelled_turns WHERE turn_id IN ({placeholders})",
                ids,
            ).fetchone()[0]
        )

    def is_tombstoned(self, turn_id: str) -> bool:
        # The TTL has to be enforced here, not only by the purge inside a
        # tombstone write: on an idle runtime nothing writes another tombstone,
        # so an expired row would keep a flushed session mute indefinitely.
        row = self._conn.execute(
            "SELECT 1 FROM cancelled_turns WHERE turn_id = ? AND cancelled_at >= ? LIMIT 1",
            (turn_id, _tombstone_cutoff(self._now())),
        ).fetchone()
        return row is not None

    def recover_orphans(self) -> int:
        now = self._now()
        with self._conn:
            synthesizing = self._conn.execute(
                """
                UPDATE voice_queue
                SET status = 'queued', synthesis_started_at = NULL,
                    synthesis_completed_at = NULL, updated_at = ?
                WHERE status = 'synthesizing'
                """,
                (now,),
            ).rowcount
            speaking = self._conn.execute(
                """
                UPDATE voice_queue
                SET status = 'failed', error = 'playback interrupted by broker restart',
                    playback_completed_at = ?, playback_confirmed = 0, updated_at = ?
                WHERE status = 'speaking'
                """,
                (now, now),
            ).rowcount
        return synthesizing + speaking

    def pending_count(self) -> int:
        return self._pending_count_in_transaction()

    def list(self) -> list[VoiceRequest]:
        ids = [
            str(row[0])
            for row in self._conn.execute(
                "SELECT id FROM voice_queue ORDER BY rowid ASC"
            ).fetchall()
        ]
        return [self._by_id(request_id) for request_id in ids]

    def get(self, request_id: str) -> VoiceRequest:
        return self._by_id(request_id)

    def _pending_count_in_transaction(self) -> int:
        return int(
            self._conn.execute(
                """
                SELECT COUNT(*) FROM voice_queue
                WHERE status IN ('queued', 'synthesizing', 'speaking')
                """
            ).fetchone()[0]
        )

    def _begin_immediate(self) -> None:
        if self._conn.in_transaction:
            raise VoiceQueueError("voice queue operation requires a clean transaction boundary")
        self._conn.execute("BEGIN IMMEDIATE")

    def _by_id(self, request_id: str) -> VoiceRequest:
        row = self._conn.execute(
            """
            SELECT id, text, priority, status, interrupt_policy, turn_id, voice_id,
                   created_at, source, session_id, participant, persona, lane,
                   utterance_index, render_snapshot_json, synthesis_started_at,
                   synthesis_completed_at, playback_started_at, playback_completed_at,
                   playback_confirmed
            FROM voice_queue WHERE id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise VoiceQueueError(f"Unknown voice request {request_id!r}.")
        snapshot_json = str(row[14])
        snapshot = (
            None
            if snapshot_json == "legacy-unresolved"
            else RenderSnapshot.from_json(snapshot_json)
        )
        return VoiceRequest(
            id=str(row[0]),
            text=str(row[1]),
            priority=int(row[2]),
            status=str(row[3]),
            interrupt_policy=str(row[4]),
            turn_id=str(row[5]) if row[5] else None,
            engine=snapshot.engine if snapshot is not None else None,
            voice=str(row[6]) if row[6] else None,
            created_at=str(row[7]),
            source=str(row[8]),
            session_id=str(row[9]),
            participant=str(row[10]),
            persona=str(row[11]),
            lane=str(row[12]),
            utterance_index=int(row[13]),
            render_snapshot=snapshot,
            synthesis_started_at=str(row[15]) if row[15] else None,
            synthesis_completed_at=str(row[16]) if row[16] else None,
            playback_started_at=str(row[17]) if row[17] else None,
            playback_completed_at=str(row[18]) if row[18] else None,
            playback_confirmed=bool(row[19]),
        )

    def _append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_store is not None:
            self._event_store.append(event_type, "voice", payload)

    def _append_event_in_transaction(
        self, event_type: str, payload: dict[str, Any]
    ) -> None:
        if self._event_store is None:
            self._event_store = create_event_store(self._conn)
        append = getattr(self._event_store, "append_in_transaction", None)
        if callable(append):
            append(event_type, "voice", payload)
            return
        raise VoiceQueueError("event store does not support transactional event append")


def _tombstone_cutoff(now_iso: str) -> str:
    try:
        moment = datetime.fromisoformat(now_iso)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = moment - timedelta(seconds=TOMBSTONE_TTL_SECONDS)
    return cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


__all__ = [
    "DEFAULT_GLOBAL_PENDING_LIMIT",
    "DEFAULT_SESSION_PENDING_LIMIT",
    "QueueBackpressure",
    "VOICE_QUEUE_POLICY_VERSION",
    "VoiceQueue",
    "VoiceQueueCancelledError",
    "VoiceQueueError",
]
