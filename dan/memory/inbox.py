"""Memory Inbox candidate repository."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dan.memory.manager import utc_now_iso
from dan.security.redaction import redact_secret_text

if TYPE_CHECKING:
    from dan.store.event_store import EventStore


NEEDS_REVIEW = "needs_review"
APPROVED = "approved"
REJECTED = "rejected"
VALID_CANDIDATE_STATUSES = {NEEDS_REVIEW, APPROVED, REJECTED}


class MemoryCandidateError(Exception):
    """Raised when Memory Inbox candidates cannot be read or written."""


class MemoryCandidateValidationError(ValueError):
    """Raised when a candidate payload is malformed."""


class MemoryCandidateNotFound(MemoryCandidateError):
    """Raised when a candidate id does not exist."""


class MemoryCandidateConflict(MemoryCandidateError):
    """Raised when a candidate transition is not allowed."""


@dataclass(frozen=True, kw_only=True)
class MemoryCandidate:
    id: str
    candidate_kind: str
    scope: str
    namespace: str
    claim: str
    title: str | None
    reason: str | None
    confidence: str
    sensitivity: str
    recommended_action: str
    target_memory_id: str | None
    status: str
    created_at: str
    reviewed_at: str | None


class MemoryCandidateRepository:
    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: "EventStore | None" = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso

    def create_candidate(
        self,
        *,
        candidate_kind: str | None = None,
        scope: str | None = None,
        namespace: str | None = None,
        claim: str | None = None,
        recommended_action: str | None = None,
        title: str | None = None,
        reason: str | None = None,
        confidence: str | None = None,
        sensitivity: str | None = None,
        target_memory_id: str | None = None,
        **ignored: Any,
    ) -> MemoryCandidate:
        del ignored
        timestamp = self._now()
        candidate = MemoryCandidate(
            id=uuid.uuid4().hex,
            candidate_kind=_required_text(candidate_kind, "candidate_kind"),
            scope=_required_text(scope, "scope"),
            namespace=_required_text(namespace, "namespace"),
            claim=_redacted_required_text(claim, "claim"),
            title=_redacted_optional_text(title, "title"),
            reason=_redacted_optional_text(reason, "reason"),
            confidence=_optional_text(confidence, "confidence") or "unknown",
            sensitivity=_optional_text(sensitivity, "sensitivity") or "unknown",
            recommended_action=_required_text(recommended_action, "recommended_action"),
            target_memory_id=_optional_text(target_memory_id, "target_memory_id"),
            status=NEEDS_REVIEW,
            created_at=timestamp,
            reviewed_at=None,
        )
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO memory_candidates (
                      id, candidate_kind, scope, namespace, claim, title, reason,
                      confidence, sensitivity, recommended_action, target_memory_id,
                      status, created_at, reviewed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.id,
                        candidate.candidate_kind,
                        candidate.scope,
                        candidate.namespace,
                        candidate.claim,
                        candidate.title,
                        candidate.reason,
                        candidate.confidence,
                        candidate.sensitivity,
                        candidate.recommended_action,
                        candidate.target_memory_id,
                        candidate.status,
                        candidate.created_at,
                        candidate.reviewed_at,
                    ),
                )
                self._append_candidate_event("memory.candidate.created", candidate)
        except sqlite3.Error as exc:
            raise MemoryCandidateError(f"Could not create memory candidate: {exc}") from exc
        return candidate

    def list_candidates(self, *, status: str | None = None) -> list[MemoryCandidate]:
        params: tuple[Any, ...] = ()
        where_sql = ""
        if status is not None:
            normalized_status = _status(status)
            where_sql = "WHERE status = ?"
            params = (normalized_status,)

        try:
            rows = self._conn.execute(
                f"""
                SELECT id, candidate_kind, scope, namespace, claim, title, reason,
                       confidence, sensitivity, recommended_action, target_memory_id,
                       status, created_at, reviewed_at
                FROM memory_candidates
                {where_sql}
                ORDER BY created_at ASC, rowid ASC
                """,
                params,
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryCandidateError(f"Could not list memory candidates: {exc}") from exc
        return [_candidate_from_row(row) for row in rows]

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        normalized_id = _required_text(candidate_id, "candidate_id")
        try:
            row = self._conn.execute(
                """
                SELECT id, candidate_kind, scope, namespace, claim, title, reason,
                       confidence, sensitivity, recommended_action, target_memory_id,
                       status, created_at, reviewed_at
                FROM memory_candidates
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise MemoryCandidateError(
                f"Could not get memory candidate {normalized_id}: {exc}"
            ) from exc
        if row is None:
            return None
        return _candidate_from_row(row)

    def approve_candidate(self, candidate_id: str) -> MemoryCandidate:
        return self._decide(candidate_id, APPROVED, "memory.candidate.approved")

    def reject_candidate(self, candidate_id: str) -> MemoryCandidate:
        return self._decide(candidate_id, REJECTED, "memory.candidate.rejected")

    def _decide(
        self,
        candidate_id: str,
        status: str,
        event_type: str,
    ) -> MemoryCandidate:
        normalized_id = _required_text(candidate_id, "candidate_id")
        existing = self.get_candidate(normalized_id)
        if existing is None:
            raise MemoryCandidateNotFound(f"Memory candidate not found: {normalized_id}")
        if existing.status != NEEDS_REVIEW:
            raise MemoryCandidateConflict(f"Memory candidate already decided: {normalized_id}")

        reviewed_at = self._now()
        try:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE memory_candidates
                    SET status = ?, reviewed_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (status, reviewed_at, normalized_id, NEEDS_REVIEW),
                )
                if cursor.rowcount == 0:
                    raise MemoryCandidateConflict(
                        f"Memory candidate already decided: {normalized_id}"
                    )
                decided = self.get_candidate(normalized_id)
                if decided is None:
                    raise MemoryCandidateNotFound(
                        f"Memory candidate not found: {normalized_id}"
                    )
                self._append_candidate_event(event_type, decided)
        except sqlite3.Error as exc:
            raise MemoryCandidateError(
                f"Could not decide memory candidate {normalized_id}: {exc}"
            ) from exc
        return decided

    def _append_candidate_event(self, event_type: str, candidate: MemoryCandidate) -> None:
        if self._event_store is None:
            return
        payload: dict[str, Any] = {
            "candidate_id": candidate.id,
            "candidate_kind": candidate.candidate_kind,
            "scope": candidate.scope,
            "namespace": candidate.namespace,
            "status": candidate.status,
        }
        if candidate.target_memory_id is not None:
            payload["target_memory_id"] = candidate.target_memory_id
        self._event_store.append(event_type, "memory_inbox", payload)


def _candidate_from_row(row: sqlite3.Row | tuple[Any, ...]) -> MemoryCandidate:
    (
        candidate_id,
        candidate_kind,
        scope,
        namespace,
        claim,
        title,
        reason,
        confidence,
        sensitivity,
        recommended_action,
        target_memory_id,
        status,
        created_at,
        reviewed_at,
    ) = row
    return MemoryCandidate(
        id=str(candidate_id),
        candidate_kind=str(candidate_kind),
        scope=str(scope),
        namespace=str(namespace),
        claim=str(claim),
        title=None if title is None else str(title),
        reason=None if reason is None else str(reason),
        confidence=str(confidence),
        sensitivity=str(sensitivity),
        recommended_action=str(recommended_action),
        target_memory_id=None if target_memory_id is None else str(target_memory_id),
        status=str(status),
        created_at=str(created_at),
        reviewed_at=None if reviewed_at is None else str(reviewed_at),
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MemoryCandidateValidationError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise MemoryCandidateValidationError(f"{label} must be a non-empty string.")
    return normalized


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryCandidateValidationError(f"{label} must be a string or null.")
    normalized = value.strip()
    return normalized or None


def _redacted_required_text(value: Any, label: str) -> str:
    return redact_secret_text(_required_text(value, label))


def _redacted_optional_text(value: Any, label: str) -> str | None:
    normalized = _optional_text(value, label)
    if normalized is None:
        return None
    return redact_secret_text(normalized)


def _status(value: Any) -> str:
    normalized = _required_text(value, "status")
    if normalized not in VALID_CANDIDATE_STATUSES:
        raise MemoryCandidateValidationError(
            "status must be one of: approved, needs_review, rejected."
        )
    return normalized


__all__ = [
    "APPROVED",
    "NEEDS_REVIEW",
    "REJECTED",
    "VALID_CANDIDATE_STATUSES",
    "MemoryCandidate",
    "MemoryCandidateConflict",
    "MemoryCandidateError",
    "MemoryCandidateNotFound",
    "MemoryCandidateRepository",
    "MemoryCandidateValidationError",
]
