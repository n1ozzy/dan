"""Memory item activation repository."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jarvis.memory.inbox import APPROVED, MemoryCandidateRepository
from jarvis.memory.manager import utc_now_iso

if TYPE_CHECKING:
    from jarvis.store.event_store import EventStore


ACTIVE = "active"
SOURCE_POLICY_CANDIDATE_EVIDENCE = "candidate_evidence"


class MemoryItemError(Exception):
    """Raised when memory items cannot be read or written."""


class MemoryItemValidationError(ValueError):
    """Raised when a memory item request is malformed."""


class MemoryItemNotFound(MemoryItemError):
    """Raised when a memory item or source candidate does not exist."""


class MemoryItemConflict(MemoryItemError):
    """Raised when a memory item activation is not allowed."""


@dataclass(frozen=True, kw_only=True)
class MemoryItem:
    id: str
    canonical_key: str
    kind: str
    scope: str
    namespace: str
    title: str | None
    claim: str
    content: str | None
    status: str
    confidence: str
    sensitivity: str
    source_policy: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, kw_only=True)
class CompilerMemoryItem:
    id: str
    canonical_key: str
    kind: str
    scope: str
    namespace: str
    title: str | None
    claim: str
    content: str | None
    status: str
    confidence: str
    sensitivity: str
    source_policy: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    last_confirmed_at: str | None
    supersedes: str | None
    superseded_by: str | None
    evidence_count: int


class MemoryItemRepository:
    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: "EventStore | None" = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso
        self._candidate_repository = MemoryCandidateRepository(conn)

    def activate_candidate(self, candidate_id: str) -> MemoryItem:
        normalized_candidate_id = _required_text(candidate_id, "candidate_id")
        transaction_started = False
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            transaction_started = True

            linked = self._item_linked_to_candidate(normalized_candidate_id)
            if linked is not None:
                self._conn.commit()
                return linked

            candidate = self._candidate_repository.get_candidate(
                normalized_candidate_id
            )
            if candidate is None:
                raise MemoryItemNotFound(
                    f"Memory candidate not found: {normalized_candidate_id}"
                )
            if candidate.status != APPROVED:
                raise MemoryItemConflict(
                    "Only approved memory candidates can activate."
                )

            evidence_count = self._candidate_evidence_count(normalized_candidate_id)
            if evidence_count <= 0:
                raise MemoryItemConflict(
                    "Approved memory candidate has no evidence."
                )

            canonical_key = canonical_key_for_candidate(
                scope=candidate.scope,
                namespace=candidate.namespace,
                kind=candidate.candidate_kind,
                title=candidate.title,
                claim=candidate.claim,
            )
            existing = self._item_by_canonical_key(canonical_key)
            if existing is not None:
                self._link_candidate_evidence(
                    normalized_candidate_id,
                    existing.id,
                )
                self._conn.commit()
                return existing

            timestamp = self._now()
            item = MemoryItem(
                id=uuid.uuid4().hex,
                canonical_key=canonical_key,
                kind=candidate.candidate_kind,
                scope=candidate.scope,
                namespace=candidate.namespace,
                title=candidate.title,
                claim=candidate.claim,
                content=candidate.claim,
                status=ACTIVE,
                confidence=candidate.confidence,
                sensitivity=candidate.sensitivity,
                source_policy=SOURCE_POLICY_CANDIDATE_EVIDENCE,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._conn.execute(
                """
                INSERT INTO memory_items (
                  id, canonical_key, kind, scope, namespace, title, claim,
                  content, status, confidence, sensitivity, source_policy,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.canonical_key,
                    item.kind,
                    item.scope,
                    item.namespace,
                    item.title,
                    item.claim,
                    item.content,
                    item.status,
                    item.confidence,
                    item.sensitivity,
                    item.source_policy,
                    item.created_at,
                    item.updated_at,
                ),
            )
            self._link_candidate_evidence(normalized_candidate_id, item.id)
            self._append_activated_event(
                candidate_id=normalized_candidate_id,
                item=item,
            )
            self._conn.commit()
            return item
        except (MemoryItemNotFound, MemoryItemConflict):
            if transaction_started and self._conn.in_transaction:
                self._conn.rollback()
            raise
        except sqlite3.Error as exc:
            if transaction_started and self._conn.in_transaction:
                self._conn.rollback()
            raise MemoryItemError(
                f"Could not activate memory candidate {normalized_candidate_id}: {exc}"
            ) from exc
        except Exception:
            if transaction_started and self._conn.in_transaction:
                self._conn.rollback()
            raise

    def list_items(self) -> list[MemoryItem]:
        try:
            rows = self._conn.execute(
                """
                SELECT id, canonical_key, kind, scope, namespace, title, claim,
                       content, status, confidence, sensitivity, source_policy,
                       created_at, updated_at
                FROM memory_items
                ORDER BY created_at ASC, rowid ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryItemError(f"Could not list memory items: {exc}") from exc
        return [_item_from_row(row) for row in rows]

    def list_items_for_compiler(self) -> list[CompilerMemoryItem]:
        """Return read-only memory item projections for compiler selection."""

        try:
            rows = self._conn.execute(
                """
                SELECT i.id, i.canonical_key, i.kind, i.scope, i.namespace,
                       i.title, i.claim, i.content, i.status, i.confidence,
                       i.sensitivity, i.source_policy, i.created_at,
                       i.updated_at, i.last_used_at, i.last_confirmed_at,
                       i.supersedes, i.superseded_by,
                       COALESCE(e.evidence_count, 0) AS evidence_count
                FROM memory_items AS i
                LEFT JOIN (
                    SELECT memory_id, COUNT(*) AS evidence_count
                    FROM memory_evidence
                    WHERE memory_id IS NOT NULL
                    GROUP BY memory_id
                ) AS e ON e.memory_id = i.id
                ORDER BY i.created_at ASC, i.rowid ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryItemError(
                f"Could not list memory items for compiler: {exc}"
            ) from exc
        return [_compiler_item_from_row(row) for row in rows]

    def get_item(self, memory_id: str) -> MemoryItem | None:
        normalized_id = _required_text(memory_id, "memory_id")
        try:
            row = self._conn.execute(
                """
                SELECT id, canonical_key, kind, scope, namespace, title, claim,
                       content, status, confidence, sensitivity, source_policy,
                       created_at, updated_at
                FROM memory_items
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise MemoryItemError(
                f"Could not get memory item {normalized_id}: {exc}"
            ) from exc
        if row is None:
            return None
        return _item_from_row(row)

    def _candidate_evidence_count(self, candidate_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM memory_evidence WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return int(row[0])

    def _item_linked_to_candidate(self, candidate_id: str) -> MemoryItem | None:
        row = self._conn.execute(
            """
            SELECT i.id, i.canonical_key, i.kind, i.scope, i.namespace, i.title,
                   i.claim, i.content, i.status, i.confidence, i.sensitivity,
                   i.source_policy, i.created_at, i.updated_at
            FROM memory_evidence AS e
            JOIN memory_items AS i ON i.id = e.memory_id
            WHERE e.candidate_id = ? AND e.memory_id IS NOT NULL
            ORDER BY e.created_at ASC, e.rowid ASC
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return _item_from_row(row)

    def _item_by_canonical_key(self, canonical_key: str) -> MemoryItem | None:
        row = self._conn.execute(
            """
            SELECT id, canonical_key, kind, scope, namespace, title, claim,
                   content, status, confidence, sensitivity, source_policy,
                   created_at, updated_at
            FROM memory_items
            WHERE canonical_key = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT 1
            """,
            (canonical_key,),
        ).fetchone()
        if row is None:
            return None
        return _item_from_row(row)

    def _link_candidate_evidence(self, candidate_id: str, memory_id: str) -> None:
        self._conn.execute(
            """
            UPDATE memory_evidence
            SET memory_id = ?
            WHERE candidate_id = ? AND memory_id IS NULL
            """,
            (memory_id, candidate_id),
        )

    def _append_activated_event(self, *, candidate_id: str, item: MemoryItem) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            "memory.activated",
            "memory_items",
            {
                "candidate_id": candidate_id,
                "memory_id": item.id,
                "kind": item.kind,
                "scope": item.scope,
                "namespace": item.namespace,
                "status": item.status,
            },
        )


def canonical_key_for_candidate(
    *,
    scope: str,
    namespace: str,
    kind: str,
    title: str | None,
    claim: str,
) -> str:
    if title is None:
        title_part = ["no-title", None]
    else:
        title_part = ["title", _canonical_part(title)]
    return json.dumps(
        [
            "memory-item-v1",
            _canonical_part(scope),
            _canonical_part(namespace),
            _canonical_part(kind),
            title_part,
            _canonical_part(claim),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _item_from_row(row: sqlite3.Row | tuple[Any, ...]) -> MemoryItem:
    (
        memory_id,
        canonical_key,
        kind,
        scope,
        namespace,
        title,
        claim,
        content,
        status,
        confidence,
        sensitivity,
        source_policy,
        created_at,
        updated_at,
    ) = row
    return MemoryItem(
        id=str(memory_id),
        canonical_key=str(canonical_key),
        kind=str(kind),
        scope=str(scope),
        namespace=str(namespace),
        title=None if title is None else str(title),
        claim=str(claim),
        content=None if content is None else str(content),
        status=str(status),
        confidence=str(confidence),
        sensitivity=str(sensitivity),
        source_policy=None if source_policy is None else str(source_policy),
        created_at=str(created_at),
        updated_at=str(updated_at),
    )


def _compiler_item_from_row(row: sqlite3.Row | tuple[Any, ...]) -> CompilerMemoryItem:
    (
        memory_id,
        canonical_key,
        kind,
        scope,
        namespace,
        title,
        claim,
        content,
        status,
        confidence,
        sensitivity,
        source_policy,
        created_at,
        updated_at,
        last_used_at,
        last_confirmed_at,
        supersedes,
        superseded_by,
        evidence_count,
    ) = row
    return CompilerMemoryItem(
        id=str(memory_id),
        canonical_key=str(canonical_key),
        kind=str(kind),
        scope=str(scope),
        namespace=str(namespace),
        title=None if title is None else str(title),
        claim=str(claim),
        content=None if content is None else str(content),
        status=str(status),
        confidence=str(confidence),
        sensitivity=str(sensitivity),
        source_policy=None if source_policy is None else str(source_policy),
        created_at=str(created_at),
        updated_at=str(updated_at),
        last_used_at=None if last_used_at is None else str(last_used_at),
        last_confirmed_at=None
        if last_confirmed_at is None
        else str(last_confirmed_at),
        supersedes=None if supersedes is None else str(supersedes),
        superseded_by=None if superseded_by is None else str(superseded_by),
        evidence_count=int(evidence_count),
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MemoryItemValidationError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise MemoryItemValidationError(f"{label} must be a non-empty string.")
    return normalized


def _canonical_part(value: str) -> str:
    return " ".join(value.strip().lower().split())


__all__ = [
    "ACTIVE",
    "CompilerMemoryItem",
    "SOURCE_POLICY_CANDIDATE_EVIDENCE",
    "MemoryItem",
    "MemoryItemConflict",
    "MemoryItemError",
    "MemoryItemNotFound",
    "MemoryItemRepository",
    "MemoryItemValidationError",
    "canonical_key_for_candidate",
]
