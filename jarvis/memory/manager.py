"""SQLite-backed Jarvis-owned memory blocks."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jarvis.brain.base import BrainMemoryBlock
from jarvis.logging import redact_secrets
from jarvis.memory.policies import select_memory_for_budget, validate_memory_kind

if TYPE_CHECKING:
    from jarvis.store.event_store import EventStore


class MemoryError(Exception):
    """Raised when Jarvis-owned memory cannot be read or written."""


@dataclass(frozen=True, kw_only=True)
class MemoryBlock:
    id: str
    kind: str
    title: str
    body: str
    priority: int = 0
    active: bool = True
    created_at: str
    updated_at: str
    source_event_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryManager:
    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: "EventStore | None" = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso

    def create_block(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        priority: int = 0,
        active: bool = True,
        source_event_id: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryBlock:
        normalized_kind = _normalize_kind(kind)
        normalized_title = _normalize_required_text(title, "title")
        normalized_body = _normalize_required_text(body, "body")
        normalized_priority = _normalize_priority(priority)
        metadata_dict = _jsonable_metadata(metadata)
        timestamp = self._now()
        block_id = uuid.uuid4().hex

        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO memory_blocks (
                      id, kind, title, body, priority, active, created_at, updated_at,
                      source_event_id, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        block_id,
                        normalized_kind,
                        normalized_title,
                        normalized_body,
                        normalized_priority,
                        1 if active else 0,
                        timestamp,
                        timestamp,
                        source_event_id,
                        _metadata_to_json(metadata_dict),
                    ),
                )
        except sqlite3.Error as exc:
            raise MemoryError(f"Could not create memory block: {exc}") from exc

        block = MemoryBlock(
            id=block_id,
            kind=normalized_kind,
            title=normalized_title,
            body=normalized_body,
            priority=normalized_priority,
            active=bool(active),
            created_at=timestamp,
            updated_at=timestamp,
            source_event_id=source_event_id,
            metadata=metadata_dict,
        )
        self._append_memory_event("created", block)
        return block

    def create_candidate(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        priority: int = 0,
        proposed_by: str = "worker",
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryBlock:
        """Create a memory *candidate*: an inactive block awaiting promotion.

        Candidates never enter brain context (only active blocks do), so a
        worker result stays advisory until a human — or an explicit policy —
        promotes it (CONTRACTS.md §6, ADR-009).
        """

        candidate_metadata = _jsonable_metadata(metadata)
        candidate_metadata["candidate"] = True
        candidate_metadata["proposed_by"] = proposed_by
        block = self.create_block(
            kind,
            title,
            body,
            priority=priority,
            active=False,
            metadata=candidate_metadata,
        )
        self._append_candidate_event(
            "memory.candidate.created", block, {"proposed_by": proposed_by}
        )
        return block

    def get_block(self, block_id: str) -> MemoryBlock | None:
        rows = self._read_blocks("WHERE id = ?", (block_id,))
        return rows[0] if rows else None

    def list_blocks(
        self,
        *,
        active_only: bool = False,
        kinds: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryBlock]:
        clauses: list[str] = []
        params: list[Any] = []

        if active_only:
            clauses.append("active = 1")

        if kinds is not None:
            normalized_kinds = [_normalize_kind(kind) for kind in kinds]
            if normalized_kinds:
                placeholders = ", ".join("?" for _ in normalized_kinds)
                clauses.append(f"kind IN ({placeholders})")
                params.extend(normalized_kinds)
            else:
                return []

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        blocks = self._read_blocks(where_sql, tuple(params))
        if limit is not None:
            if limit <= 0:
                return []
            return blocks[:limit]
        return blocks

    def update_block(
        self,
        block_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        priority: int | None = None,
        active: bool | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryBlock:
        # Activating a candidate IS promotion, whatever else the update
        # touches: an active block enters brain context, so it must never
        # stay flagged as a candidate (fail-closed on the flag, not the
        # caller's intent).
        existing = self.get_block(block_id)
        promoting = (
            active is True
            and existing is not None
            and not existing.active
            and (metadata if metadata is not None else existing.metadata).get("candidate")
            is True
        )
        if promoting:
            merged = _jsonable_metadata(metadata if metadata is not None else existing.metadata)
            merged["candidate"] = False
            merged.setdefault("promoted_by", "human")
            metadata = merged

        block = self._update_block(
            block_id,
            title=title,
            body=body,
            priority=priority,
            active=active,
            metadata=metadata,
            event_action="updated",
        )
        if promoting:
            self._append_candidate_event(
                "memory.candidate.promoted",
                block,
                {"promoted_by": block.metadata.get("promoted_by", "human")},
            )
        return block

    def promote_candidate(self, block_id: str, *, promoted_by: str = "human") -> MemoryBlock:
        existing = self.get_block(block_id)
        if existing is None:
            raise MemoryError(f"Memory block not found: {block_id}")
        if existing.metadata.get("candidate") is not True:
            raise MemoryError(f"Memory block is not a candidate: {block_id}")
        metadata = dict(existing.metadata)
        metadata["promoted_by"] = promoted_by
        return self.update_block(block_id, active=True, metadata=metadata)

    def disable_block(self, block_id: str) -> MemoryBlock:
        return self._update_block(block_id, active=False, event_action="disabled")

    def active_blocks_for_context(
        self,
        max_blocks: int | None = None,
        max_chars: int | None = None,
    ) -> list[MemoryBlock]:
        return select_memory_for_budget(
            self.list_blocks(active_only=True),
            max_blocks=max_blocks,
            max_chars=max_chars,
        )

    def to_brain_memory_blocks(self, blocks: Iterable[MemoryBlock]) -> list[BrainMemoryBlock]:
        return [
            BrainMemoryBlock(
                id=block.id,
                kind=block.kind,
                title=block.title,
                body=block.body,
                priority=block.priority,
                metadata=dict(block.metadata),
            )
            for block in blocks
        ]

    def _update_block(
        self,
        block_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        priority: int | None = None,
        active: bool | None = None,
        metadata: Mapping[str, Any] | None = None,
        event_action: str,
    ) -> MemoryBlock:
        existing = self.get_block(block_id)
        if existing is None:
            raise MemoryError(f"Memory block not found: {block_id}")

        new_title = existing.title if title is None else _normalize_required_text(title, "title")
        new_body = existing.body if body is None else _normalize_required_text(body, "body")
        new_priority = existing.priority if priority is None else _normalize_priority(priority)
        new_active = existing.active if active is None else bool(active)
        new_metadata = existing.metadata if metadata is None else _jsonable_metadata(metadata)
        updated_at = self._now()

        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE memory_blocks
                    SET title = ?, body = ?, priority = ?, active = ?, updated_at = ?,
                        metadata_json = ?
                    WHERE id = ?
                    """,
                    (
                        new_title,
                        new_body,
                        new_priority,
                        1 if new_active else 0,
                        updated_at,
                        _metadata_to_json(new_metadata),
                        block_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise MemoryError(f"Could not update memory block {block_id}: {exc}") from exc

        block = MemoryBlock(
            id=existing.id,
            kind=existing.kind,
            title=new_title,
            body=new_body,
            priority=new_priority,
            active=new_active,
            created_at=existing.created_at,
            updated_at=updated_at,
            source_event_id=existing.source_event_id,
            metadata=new_metadata,
        )
        self._append_memory_event(event_action, block)
        return block

    def _read_blocks(self, where_sql: str = "", params: tuple[Any, ...] = ()) -> list[MemoryBlock]:
        sql = f"""
            SELECT id, kind, title, body, priority, active, created_at, updated_at,
                   source_event_id, metadata_json
            FROM memory_blocks
            {where_sql}
            ORDER BY updated_at DESC, id ASC
        """
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise MemoryError(f"Could not read memory blocks: {exc}") from exc

        return [_block_from_row(row) for row in rows]

    def _append_memory_event(self, action: str, block: MemoryBlock) -> None:
        if self._event_store is None:
            return
        payload = {
            "action": action,
            "block_id": block.id,
            "kind": block.kind,
            "title": redact_secrets(block.title),
            "priority": block.priority,
            "active": block.active,
            "metadata": _redact_value(block.metadata),
        }
        self._event_store.append("memory.updated", "memory_manager", payload)

    def _append_candidate_event(
        self, event_type: str, block: MemoryBlock, extra: Mapping[str, Any]
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "block_id": block.id,
            "kind": block.kind,
            "title": redact_secrets(block.title),
            "active": block.active,
            **dict(extra),
        }
        self._event_store.append(event_type, "memory_manager", payload)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _block_from_row(row: sqlite3.Row | tuple[Any, ...]) -> MemoryBlock:
    (
        block_id,
        kind,
        title,
        body,
        priority,
        active,
        created_at,
        updated_at,
        source_event_id,
        metadata_json,
    ) = row
    return MemoryBlock(
        id=str(block_id),
        kind=str(kind),
        title=str(title),
        body=str(body),
        priority=int(priority),
        active=bool(active),
        created_at=str(created_at),
        updated_at=str(updated_at),
        source_event_id=None if source_event_id is None else int(source_event_id),
        metadata=_metadata_from_json(str(metadata_json)),
    )


def _normalize_kind(kind: str) -> str:
    try:
        return validate_memory_kind(kind)
    except ValueError as exc:
        raise MemoryError(str(exc)) from exc


def _normalize_required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise MemoryError(f"Memory {label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise MemoryError(f"Memory {label} must be a non-empty string.")
    return normalized


def _normalize_priority(priority: int) -> int:
    if type(priority) is not int:
        raise MemoryError("Memory priority must be an integer.")
    return priority


def _jsonable_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise MemoryError("Memory metadata must be a mapping.")
    try:
        json.dumps(metadata)
    except (TypeError, ValueError) as exc:
        raise MemoryError("Memory metadata must be JSON serializable.") from exc
    return dict(metadata)


def _metadata_to_json(metadata: Mapping[str, Any]) -> str:
    return json.dumps(metadata, sort_keys=True)


def _metadata_from_json(metadata_json: str) -> dict[str, Any]:
    try:
        value = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise MemoryError(f"Invalid memory metadata JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MemoryError("Memory metadata JSON must decode to an object.")
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, Mapping):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


__all__ = ["MemoryBlock", "MemoryError", "MemoryManager", "utc_now_iso"]
