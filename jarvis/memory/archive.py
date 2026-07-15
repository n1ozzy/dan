"""Shared local SQLite/FTS5 memory archive."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jarvis.security.redaction import redact_secret_text, redact_secrets
from jarvis.store.repositories import utc_now_iso


MAX_RECALL_QUERY_CHARS = 2000
MAX_RECALL_QUERY_TOKENS = 64


class MemoryRecallValidationError(ValueError):
    """Raised when a recall request cannot be executed safely."""


@dataclass(frozen=True)
class MemoryRecallRequest:
    query: str
    limit: int = 10


@dataclass(frozen=True)
class ArchiveDocument:
    source_type: str
    source_uri: str
    source_item_id: str
    content: str
    title: str | None = None
    source_updated_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchivedMemory:
    canonical_id: str
    source_type: str
    source_uri: str
    source_item_id: str
    title: str | None
    content: str
    content_hash: str
    source_updated_at: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArchiveUpsertResult:
    document: ArchivedMemory
    changed: bool


@dataclass(frozen=True)
class MemoryRecallHit:
    canonical_id: str
    source_type: str
    source_uri: str
    source_item_id: str
    title: str | None
    content: str
    source_updated_at: str | None
    metadata: dict[str, Any]
    score: float


@dataclass(frozen=True)
class MemoryRecallResponse:
    query: str
    limit: int
    results: tuple[MemoryRecallHit, ...]


@dataclass(frozen=True)
class ArchiveSyncResult:
    imported: int
    updated: int
    unchanged: int
    deleted: int
    cursor: str | None
    fingerprint: str | None


@dataclass(frozen=True)
class ArchiveSyncState:
    source_type: str
    source_uri: str
    cursor: str | None
    fingerprint: str | None
    synced_at: str
    metadata: dict[str, Any]


class MemoryArchive:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._now = now or utc_now_iso

    def upsert(self, document: ArchiveDocument) -> ArchiveUpsertResult:
        transaction = nullcontext() if self._conn.in_transaction else self._conn
        with transaction:
            result, _created = self._upsert(document, timestamp=self._now())
        return result

    def sync_source(
        self,
        source_type: str,
        source_uri: str,
        documents: list[ArchiveDocument],
        *,
        cursor: str | None,
        fingerprint: str | None,
        replace: bool = False,
        delete_item_ids: tuple[str, ...] = (),
    ) -> ArchiveSyncResult:
        normalized_type = _required_text(source_type, "source_type")
        normalized_uri = _required_text(source_uri, "source_uri")
        timestamp = self._now()
        imported = updated = unchanged = 0
        seen_ids: set[str] = set()
        transaction = nullcontext() if self._conn.in_transaction else self._conn
        with transaction:
            for document in documents:
                if (
                    _required_text(document.source_type, "source_type") != normalized_type
                    or _required_text(document.source_uri, "source_uri") != normalized_uri
                ):
                    raise ValueError("all synchronized documents must belong to the source")
                seen_ids.add(
                    canonical_memory_id(
                        document.source_type,
                        document.source_uri,
                        document.source_item_id,
                    )
                )
                result, created = self._upsert(document, timestamp=timestamp)
                if not result.changed:
                    unchanged += 1
                elif created:
                    imported += 1
                else:
                    updated += 1
            deleted = 0
            for source_item_id in delete_item_ids:
                canonical_id = canonical_memory_id(
                    normalized_type,
                    normalized_uri,
                    source_item_id,
                )
                if canonical_id in seen_ids:
                    continue
                self._conn.execute(
                    "DELETE FROM memory_archive_fts WHERE canonical_id = ?",
                    (canonical_id,),
                )
                removed = self._conn.execute(
                    "DELETE FROM memory_archive_documents WHERE canonical_id = ?",
                    (canonical_id,),
                ).rowcount
                deleted += max(0, removed)
            if replace:
                existing_ids = {
                    str(row[0])
                    for row in self._conn.execute(
                        """
                        SELECT canonical_id FROM memory_archive_documents
                        WHERE source_type = ? AND source_uri = ?
                        """,
                        (normalized_type, normalized_uri),
                    )
                }
                stale_ids = sorted(existing_ids - seen_ids)
                for canonical_id in stale_ids:
                    self._conn.execute(
                        "DELETE FROM memory_archive_fts WHERE canonical_id = ?",
                        (canonical_id,),
                    )
                    self._conn.execute(
                        "DELETE FROM memory_archive_documents WHERE canonical_id = ?",
                        (canonical_id,),
                    )
                deleted += len(stale_ids)
            self._conn.execute(
                """
                INSERT INTO memory_archive_sync_state (
                  source_type, source_uri, cursor, fingerprint, synced_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, '{}')
                ON CONFLICT(source_type, source_uri) DO UPDATE SET
                  cursor = excluded.cursor,
                  fingerprint = excluded.fingerprint,
                  synced_at = excluded.synced_at
                """,
                (
                    normalized_type,
                    normalized_uri,
                    _optional_text(cursor),
                    _optional_text(fingerprint),
                    timestamp,
                ),
            )
        return ArchiveSyncResult(
            imported=imported,
            updated=updated,
            unchanged=unchanged,
            deleted=deleted,
            cursor=_optional_text(cursor),
            fingerprint=_optional_text(fingerprint),
        )

    def get_sync_state(self, source_type: str, source_uri: str) -> ArchiveSyncState | None:
        row = self._conn.execute(
            """
            SELECT source_type, source_uri, cursor, fingerprint, synced_at, metadata_json
            FROM memory_archive_sync_state
            WHERE source_type = ? AND source_uri = ?
            """,
            (
                _required_text(source_type, "source_type"),
                _required_text(source_uri, "source_uri"),
            ),
        ).fetchone()
        if row is None:
            return None
        metadata = json.loads(str(row[5]))
        return ArchiveSyncState(
            source_type=str(row[0]),
            source_uri=str(row[1]),
            cursor=str(row[2]) if row[2] is not None else None,
            fingerprint=str(row[3]) if row[3] is not None else None,
            synced_at=str(row[4]),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def _upsert(
        self,
        document: ArchiveDocument,
        *,
        timestamp: str,
    ) -> tuple[ArchiveUpsertResult, bool]:
        source_type = _required_text(document.source_type, "source_type")
        source_uri = _required_text(document.source_uri, "source_uri")
        source_item_id = _required_text(document.source_item_id, "source_item_id")
        content = redact_secret_text(_required_text(document.content, "content"))
        title = _optional_redacted_text(document.title)
        metadata_value = redact_secrets(dict(document.metadata))
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        metadata_json = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_updated_at = _optional_iso_timestamp(document.source_updated_at)
        canonical_id = canonical_memory_id(source_type, source_uri, source_item_id)
        content_hash = _document_hash(title, content, metadata_json, source_updated_at)
        existing = self._conn.execute(
            """
            SELECT canonical_id, source_type, source_uri, source_item_id, title, content,
                   content_hash, source_updated_at, metadata_json, created_at, updated_at
            FROM memory_archive_documents
            WHERE canonical_id = ?
            """,
            (canonical_id,),
        ).fetchone()
        if existing is not None and str(existing[6]) == content_hash:
            return ArchiveUpsertResult(document=_memory_from_row(existing), changed=False), False

        created_at = str(existing[9]) if existing is not None else timestamp
        self._conn.execute(
            """
            INSERT INTO memory_archive_documents (
              canonical_id, source_type, source_uri, source_item_id, title, content,
              content_hash, source_updated_at, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_id) DO UPDATE SET
              title = excluded.title,
              content = excluded.content,
              content_hash = excluded.content_hash,
              source_updated_at = excluded.source_updated_at,
              metadata_json = excluded.metadata_json,
              updated_at = excluded.updated_at
            """,
            (
                canonical_id,
                source_type,
                source_uri,
                source_item_id,
                title,
                content,
                content_hash,
                source_updated_at,
                metadata_json,
                created_at,
                timestamp,
            ),
        )
        self._conn.execute(
            "DELETE FROM memory_archive_fts WHERE canonical_id = ?",
            (canonical_id,),
        )
        self._conn.execute(
            "INSERT INTO memory_archive_fts (canonical_id, title, content) VALUES (?, ?, ?)",
            (canonical_id, title or "", content),
        )

        return (
            ArchiveUpsertResult(
                document=ArchivedMemory(
                    canonical_id=canonical_id,
                    source_type=source_type,
                    source_uri=source_uri,
                    source_item_id=source_item_id,
                    title=title,
                    content=content,
                    content_hash=content_hash,
                    source_updated_at=source_updated_at,
                    metadata=metadata,
                    created_at=created_at,
                    updated_at=timestamp,
                ),
                changed=True,
            ),
            existing is None,
        )

    def recall(self, query: str, *, limit: int = 10) -> MemoryRecallResponse:
        raw_query = _recall_query(query)
        normalized_query = redact_secret_text(raw_query)
        selected_limit = _recall_limit(limit)
        if normalized_query != raw_query:
            return MemoryRecallResponse(query=normalized_query, limit=selected_limit, results=())
        match_query = _fts_match_query(normalized_query)
        if not match_query:
            return MemoryRecallResponse(query=normalized_query, limit=selected_limit, results=())

        rows = self._conn.execute(
            """
            SELECT d.canonical_id, d.source_type, d.source_uri, d.source_item_id,
                   d.title, d.content, d.source_updated_at, d.metadata_json,
                   bm25(memory_archive_fts) AS score
            FROM memory_archive_fts
            JOIN memory_archive_documents d
              ON d.canonical_id = memory_archive_fts.canonical_id
            WHERE memory_archive_fts MATCH ?
            ORDER BY score ASC, d.canonical_id ASC
            LIMIT ?
            """,
            (match_query, selected_limit),
        ).fetchall()
        return MemoryRecallResponse(
            query=normalized_query,
            limit=selected_limit,
            results=tuple(_recall_hit_from_row(row) for row in rows),
        )


def canonical_memory_id(source_type: str, source_uri: str, source_item_id: str) -> str:
    """Return a stable identifier scoped to one source item."""

    normalized_source_type = _required_text(source_type, "source_type")
    normalized_source_uri = _required_text(source_uri, "source_uri")
    normalized_source_item_id = _required_text(source_item_id, "source_item_id")
    identity = json.dumps(
        [
            "memory-archive-v1",
            normalized_source_type,
            normalized_source_uri,
            normalized_source_item_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"mem_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def memory_recall_to_dict(response: MemoryRecallResponse) -> dict[str, Any]:
    results = [
        {
            "canonical_id": hit.canonical_id,
            "source_type": hit.source_type,
            "source_uri": hit.source_uri,
            "source_item_id": hit.source_item_id,
            "title": hit.title,
            "content": hit.content,
            "source_updated_at": hit.source_updated_at,
            "metadata": dict(hit.metadata),
            "score": round(hit.score, 12),
        }
        for hit in response.results
    ]
    return {
        "query": response.query,
        "limit": response.limit,
        "count": len(results),
        "results": results,
    }


def parse_memory_recall_request(arguments: Mapping[str, Any]) -> MemoryRecallRequest:
    if not isinstance(arguments, Mapping):
        raise MemoryRecallValidationError("memory_recall arguments must be an object")
    unexpected = sorted(str(key) for key in arguments if key not in {"query", "limit"})
    if unexpected:
        raise MemoryRecallValidationError(
            f"memory_recall received unexpected arguments: {', '.join(unexpected)}"
        )
    return MemoryRecallRequest(
        query=_recall_query(arguments.get("query")),
        limit=_recall_limit(arguments.get("limit", 10)),
    )


def _document_hash(
    title: str | None,
    content: str,
    metadata_json: str,
    source_updated_at: str | None,
) -> str:
    payload = json.dumps(
        [title, content, metadata_json, source_updated_at],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text must be a string or null")
    normalized = value.strip()
    return normalized or None


def _optional_redacted_text(value: Any) -> str | None:
    normalized = _optional_text(value)
    return redact_secret_text(normalized) if normalized is not None else None


def _optional_iso_timestamp(value: Any) -> str | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("source_updated_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("source_updated_at must include a timezone")
    return parsed.isoformat().replace("+00:00", "Z")


def _memory_from_row(row: tuple[Any, ...]) -> ArchivedMemory:
    metadata = json.loads(str(row[8]))
    return ArchivedMemory(
        canonical_id=str(row[0]),
        source_type=str(row[1]),
        source_uri=str(row[2]),
        source_item_id=str(row[3]),
        title=str(row[4]) if row[4] is not None else None,
        content=str(row[5]),
        content_hash=str(row[6]),
        source_updated_at=str(row[7]) if row[7] is not None else None,
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=str(row[9]),
        updated_at=str(row[10]),
    )


def _recall_hit_from_row(row: tuple[Any, ...]) -> MemoryRecallHit:
    metadata = json.loads(str(row[7]))
    return MemoryRecallHit(
        canonical_id=str(row[0]),
        source_type=str(row[1]),
        source_uri=str(row[2]),
        source_item_id=str(row[3]),
        title=str(row[4]) if row[4] is not None else None,
        content=str(row[5]),
        source_updated_at=str(row[6]) if row[6] is not None else None,
        metadata=metadata if isinstance(metadata, dict) else {},
        score=float(row[8]),
    )


def _fts_match_query(query: str) -> str:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _recall_query(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryRecallValidationError("query must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > MAX_RECALL_QUERY_CHARS:
        raise MemoryRecallValidationError(
            f"query must be at most {MAX_RECALL_QUERY_CHARS} characters"
        )
    if len(re.findall(r"\w+", normalized, flags=re.UNICODE)) > MAX_RECALL_QUERY_TOKENS:
        raise MemoryRecallValidationError(
            f"query must contain at most {MAX_RECALL_QUERY_TOKENS} tokens"
        )
    return normalized


def _recall_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MemoryRecallValidationError("limit must be an integer")
    if value < 1 or value > 100:
        raise MemoryRecallValidationError("limit must be between 1 and 100")
    return value


__all__ = [
    "ArchiveDocument",
    "ArchivedMemory",
    "ArchiveUpsertResult",
    "ArchiveSyncResult",
    "ArchiveSyncState",
    "MemoryRecallHit",
    "MemoryRecallRequest",
    "MemoryRecallResponse",
    "MemoryRecallValidationError",
    "MemoryArchive",
    "canonical_memory_id",
    "memory_recall_to_dict",
    "parse_memory_recall_request",
]
