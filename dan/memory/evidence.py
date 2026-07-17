"""Memory candidate evidence ledger repository."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dan.memory.inbox import NEEDS_REVIEW
from dan.memory.manager import utc_now_iso
from dan.security.redaction import redact_secret_text

if TYPE_CHECKING:
    from dan.store.event_store import EventStore


class MemoryEvidenceError(Exception):
    """Raised when Memory Evidence rows cannot be read or written."""


class MemoryEvidenceValidationError(ValueError):
    """Raised when an evidence payload is malformed."""


class MemoryEvidenceNotFound(MemoryEvidenceError):
    """Raised when the evidence target candidate does not exist."""


class MemoryEvidenceConflict(MemoryEvidenceError):
    """Raised when evidence cannot be attached to the target candidate."""


@dataclass(frozen=True, kw_only=True)
class MemoryEvidence:
    id: str
    candidate_id: str
    source_type: str
    source_id: str | None
    conversation_id: str | None
    turn_id: str | None
    event_id: int | None
    quote: str | None
    weight: float
    observation_id: str
    created_at: str


class MemoryEvidenceRepository:
    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: "EventStore | None" = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso

    def add_evidence(
        self,
        candidate_id: str,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        event_id: int | None = None,
        quote: str | None = None,
        weight: float | int | None = None,
        **ignored: Any,
    ) -> MemoryEvidence:
        del ignored
        normalized_candidate_id = _required_text(candidate_id, "candidate_id")
        normalized_source_type = _required_text(source_type, "source_type")
        normalized_source_id = _optional_text(source_id, "source_id")
        normalized_conversation_id = _optional_text(conversation_id, "conversation_id")
        normalized_turn_id = _optional_text(turn_id, "turn_id")
        normalized_event_id = _optional_event_id(event_id)
        normalized_quote = _redacted_optional_text(quote, "quote")
        normalized_weight = _weight(weight)
        _require_locator(
            source_id=normalized_source_id,
            conversation_id=normalized_conversation_id,
            turn_id=normalized_turn_id,
            event_id=normalized_event_id,
            quote=normalized_quote,
        )

        timestamp = self._now()
        evidence = MemoryEvidence(
            id=uuid.uuid4().hex,
            candidate_id=normalized_candidate_id,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
            conversation_id=normalized_conversation_id,
            turn_id=normalized_turn_id,
            event_id=normalized_event_id,
            quote=normalized_quote,
            weight=normalized_weight,
            observation_id=uuid.uuid4().hex,
            created_at=timestamp,
        )

        try:
            with self._conn:
                status = self._candidate_status(normalized_candidate_id)
                if status is None:
                    raise MemoryEvidenceNotFound(
                        f"Memory candidate not found: {normalized_candidate_id}"
                    )
                if status != NEEDS_REVIEW:
                    raise MemoryEvidenceConflict(
                        f"Memory candidate already decided: {normalized_candidate_id}"
                    )
                self._guard_candidate_needs_review_for_write(normalized_candidate_id)
                self._conn.execute(
                    """
                    INSERT INTO memory_observations (
                      id, source_type, source_id, conversation_id, turn_id,
                      event_id, observed_text, detected_kind, sensitivity, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence.observation_id,
                        evidence.source_type,
                        evidence.source_id,
                        evidence.conversation_id,
                        evidence.turn_id,
                        evidence.event_id,
                        evidence.quote or "",
                        None,
                        "unknown",
                        evidence.created_at,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO memory_evidence (
                      id, memory_id, candidate_id, observation_id,
                      conversation_id, turn_id, event_id, quote, weight, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence.id,
                        None,
                        evidence.candidate_id,
                        evidence.observation_id,
                        evidence.conversation_id,
                        evidence.turn_id,
                        evidence.event_id,
                        evidence.quote,
                        evidence.weight,
                        evidence.created_at,
                    ),
                )
                self._append_created_event(evidence)
        except (MemoryEvidenceNotFound, MemoryEvidenceConflict):
            raise
        except sqlite3.Error as exc:
            raise MemoryEvidenceError(
                f"Could not create memory evidence for {normalized_candidate_id}: {exc}"
            ) from exc
        return evidence

    def list_evidence(self, candidate_id: str) -> list[MemoryEvidence]:
        normalized_candidate_id = _required_text(candidate_id, "candidate_id")
        try:
            if self._candidate_status(normalized_candidate_id) is None:
                raise MemoryEvidenceNotFound(
                    f"Memory candidate not found: {normalized_candidate_id}"
                )
            rows = self._conn.execute(
                """
                SELECT e.id, e.candidate_id, o.source_type, o.source_id,
                       e.conversation_id, e.turn_id, e.event_id, e.quote,
                       e.weight, e.observation_id, e.created_at
                FROM memory_evidence AS e
                JOIN memory_observations AS o ON o.id = e.observation_id
                WHERE e.candidate_id = ?
                ORDER BY e.created_at ASC, e.rowid ASC
                """,
                (normalized_candidate_id,),
            ).fetchall()
        except MemoryEvidenceNotFound:
            raise
        except sqlite3.Error as exc:
            raise MemoryEvidenceError(
                f"Could not list memory evidence for {normalized_candidate_id}: {exc}"
            ) from exc
        return [_evidence_from_row(row) for row in rows]

    def _candidate_status(self, candidate_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM memory_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def _guard_candidate_needs_review_for_write(self, candidate_id: str) -> None:
        cursor = self._conn.execute(
            """
            UPDATE memory_candidates
            SET status = ?
            WHERE id = ? AND status = ?
            """,
            (NEEDS_REVIEW, candidate_id, NEEDS_REVIEW),
        )
        if cursor.rowcount == 1:
            return
        status = self._candidate_status(candidate_id)
        if status is None:
            raise MemoryEvidenceNotFound(f"Memory candidate not found: {candidate_id}")
        raise MemoryEvidenceConflict(f"Memory candidate already decided: {candidate_id}")

    def _append_created_event(self, evidence: MemoryEvidence) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            "memory.evidence.created",
            "memory_evidence",
            {
                "evidence_id": evidence.id,
                "candidate_id": evidence.candidate_id,
                "source_type": evidence.source_type,
                "has_quote": evidence.quote is not None,
                "has_conversation_id": evidence.conversation_id is not None,
                "has_turn_id": evidence.turn_id is not None,
                "has_event_id": evidence.event_id is not None,
                "weight": evidence.weight,
            },
        )


def _evidence_from_row(row: sqlite3.Row | tuple[Any, ...]) -> MemoryEvidence:
    (
        evidence_id,
        candidate_id,
        source_type,
        source_id,
        conversation_id,
        turn_id,
        event_id,
        quote,
        weight,
        observation_id,
        created_at,
    ) = row
    return MemoryEvidence(
        id=str(evidence_id),
        candidate_id=str(candidate_id),
        source_type=str(source_type),
        source_id=None if source_id is None else str(source_id),
        conversation_id=None if conversation_id is None else str(conversation_id),
        turn_id=None if turn_id is None else str(turn_id),
        event_id=None if event_id is None else int(event_id),
        quote=None if quote is None else str(quote),
        weight=float(weight),
        observation_id=str(observation_id),
        created_at=str(created_at),
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MemoryEvidenceValidationError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise MemoryEvidenceValidationError(f"{label} must be a non-empty string.")
    return normalized


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryEvidenceValidationError(f"{label} must be a string or null.")
    normalized = value.strip()
    return normalized or None


def _redacted_optional_text(value: Any, label: str) -> str | None:
    normalized = _optional_text(value, label)
    if normalized is None:
        return None
    return redact_secret_text(normalized)


def _optional_event_id(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise MemoryEvidenceValidationError("event_id must be an integer or null.")
    return value


def _weight(value: Any) -> float:
    if value is None:
        return 1.0
    if type(value) not in {int, float}:
        raise MemoryEvidenceValidationError("weight must be a number.")
    normalized = float(value)
    if normalized <= 0 or normalized > 1:
        raise MemoryEvidenceValidationError("weight must satisfy 0 < weight <= 1.")
    return normalized


def _require_locator(
    *,
    source_id: str | None,
    conversation_id: str | None,
    turn_id: str | None,
    event_id: int | None,
    quote: str | None,
) -> None:
    if any(
        value is not None
        for value in (source_id, conversation_id, turn_id, event_id, quote)
    ):
        return
    raise MemoryEvidenceValidationError(
        "evidence requires at least one locator: source_id, conversation_id, "
        "turn_id, event_id, or quote."
    )


__all__ = [
    "MemoryEvidence",
    "MemoryEvidenceConflict",
    "MemoryEvidenceError",
    "MemoryEvidenceNotFound",
    "MemoryEvidenceRepository",
    "MemoryEvidenceValidationError",
]
