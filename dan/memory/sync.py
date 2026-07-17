"""Explicit incremental importers for local conversation and memory sources."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dan.memory.archive import ArchiveDocument, ArchiveSyncResult, MemoryArchive


class MemorySourceSynchronizer:
    def __init__(self, archive: MemoryArchive, conn: sqlite3.Connection) -> None:
        self._archive = archive
        self._conn = conn

    def sync_path(self, source_type: str, path: str | Path) -> ArchiveSyncResult:
        source_path = Path(path).expanduser().resolve()
        if source_path.is_dir():
            return self._sync_directory(source_type, source_path)
        if not source_path.is_file():
            raise ValueError(f"memory source is not a file: {source_path}")
        if source_type == "claude_jsonl":
            source_uri = _jsonl_source_uri(source_type, source_path)
            return self._sync_jsonl(source_type, source_uri, source_path, _claude_document)
        if source_type == "codex_session":
            source_uri = _jsonl_source_uri(source_type, source_path)
            return self._sync_jsonl(source_type, source_uri, source_path, _codex_document)
        if source_type in {"claude_memory", "codex_memory", "gpt_transcript"}:
            source_uri = source_path.as_uri()
            return self._sync_markdown(source_type, source_uri, source_path)
        raise ValueError(f"unsupported memory source type: {source_type}")

    def _sync_directory(self, source_type: str, root: Path) -> ArchiveSyncResult:
        owns_transaction = not self._conn.in_transaction
        if owns_transaction:
            self._conn.execute("BEGIN")
        try:
            result = self._sync_directory_contents(source_type, root)
        except BaseException:
            if owns_transaction:
                self._conn.rollback()
            raise
        if owns_transaction:
            self._conn.commit()
        return result

    def _sync_directory_contents(
        self,
        source_type: str,
        root: Path,
    ) -> ArchiveSyncResult:
        if source_type in {"claude_jsonl", "codex_session"}:
            candidates = sorted(root.rglob("*.jsonl"))
        elif source_type in {"claude_memory", "codex_memory"}:
            candidates = sorted(root.rglob("*.md"))
        elif source_type == "gpt_transcript":
            candidates = sorted(root.glob("*.md"))
        else:
            raise ValueError(f"unsupported memory source type: {source_type}")

        manifest_type = f"directory:{source_type}"
        manifest_uri = root.as_uri()
        manifest_state = self._archive.get_sync_state(manifest_type, manifest_uri)
        previous_uris = _directory_manifest_uris(
            manifest_state.cursor if manifest_state is not None else None
        )
        current_uris = {
            _source_uri_for_path(source_type, candidate) for candidate in candidates
        }
        totals = {"imported": 0, "updated": 0, "unchanged": 0, "deleted": 0}
        for candidate in candidates:
            result = self.sync_path(source_type, candidate)
            for key in totals:
                totals[key] += int(getattr(result, key))
        for missing_uri in sorted(previous_uris - current_uris):
            result = self._archive.sync_source(
                source_type,
                missing_uri,
                [],
                cursor=None,
                fingerprint=None,
                replace=True,
            )
            for key in totals:
                totals[key] += int(getattr(result, key))
            self._conn.execute(
                """
                DELETE FROM memory_archive_sync_state
                WHERE source_type = ? AND source_uri = ?
                """,
                (source_type, missing_uri),
            )
        self._archive.sync_source(
            manifest_type,
            manifest_uri,
            [],
            cursor=json.dumps(sorted(current_uris), separators=(",", ":")),
            fingerprint=None,
        )
        return ArchiveSyncResult(
            imported=totals["imported"],
            updated=totals["updated"],
            unchanged=totals["unchanged"],
            deleted=totals["deleted"],
            cursor=None,
            fingerprint=None,
        )

    def sync_dan_turns(self) -> ArchiveSyncResult:
        source_type = "dan_turn"
        source_uri = "dan:turns"
        state = self._archive.get_sync_state(source_type, source_uri)
        cursor = state.cursor if state is not None else None
        if cursor is None:
            rows = self._conn.execute(
                """
                SELECT id, conversation_id, created_at, updated_at, source,
                       input_text, final_text
                FROM turns
                ORDER BY updated_at ASC, rowid ASC
                """
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT id, conversation_id, created_at, updated_at, source,
                       input_text, final_text
                FROM turns
                WHERE updated_at >= ?
                ORDER BY updated_at ASC, rowid ASC
                """,
                (cursor,),
            ).fetchall()

        documents: list[ArchiveDocument] = []
        delete_item_ids: list[str] = []
        next_cursor = cursor
        for row in rows:
            turn_id = str(row[0])
            next_cursor = max(next_cursor or "", str(row[3]))
            shared_metadata = {"conversation_id": str(row[1]), "turn_source": str(row[4])}
            if isinstance(row[5], str) and row[5].strip():
                documents.append(
                    ArchiveDocument(
                        source_type=source_type,
                        source_uri=source_uri,
                        source_item_id=f"{turn_id}:user",
                        content=row[5],
                        source_updated_at=str(row[2]),
                        metadata={**shared_metadata, "role": "user"},
                    )
                )
            else:
                delete_item_ids.append(f"{turn_id}:user")
            if isinstance(row[6], str) and row[6].strip():
                documents.append(
                    ArchiveDocument(
                        source_type=source_type,
                        source_uri=source_uri,
                        source_item_id=f"{turn_id}:assistant",
                        content=row[6],
                        source_updated_at=str(row[3]),
                        metadata={**shared_metadata, "role": "assistant"},
                    )
                )
            else:
                delete_item_ids.append(f"{turn_id}:assistant")
        return self._archive.sync_source(
            source_type,
            source_uri,
            documents,
            cursor=next_cursor,
            fingerprint=None,
            delete_item_ids=tuple(delete_item_ids),
        )

    def _sync_markdown(
        self,
        source_type: str,
        source_uri: str,
        path: Path,
    ) -> ArchiveSyncResult:
        fingerprint = _file_fingerprint(path)
        state = self._archive.get_sync_state(source_type, source_uri)
        if state is not None and state.fingerprint == fingerprint:
            return ArchiveSyncResult(
                imported=0,
                updated=0,
                unchanged=0,
                deleted=0,
                cursor=state.cursor,
                fingerprint=state.fingerprint,
            )
        content = path.read_text(encoding="utf-8").rstrip()
        if not content:
            return self._archive.sync_source(
                source_type,
                source_uri,
                [],
                cursor=str(path.stat().st_size),
                fingerprint=fingerprint,
                replace=True,
            )
        stat = path.stat()
        document = ArchiveDocument(
            source_type=source_type,
            source_uri=source_uri,
            source_item_id="document",
            title=path.stem,
            content=content,
            source_updated_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            metadata={"format": "markdown"},
        )
        return self._archive.sync_source(
            source_type,
            source_uri,
            [document],
            cursor=str(stat.st_size),
            fingerprint=fingerprint,
            replace=True,
        )

    def _sync_jsonl(
        self,
        source_type: str,
        source_uri: str,
        path: Path,
        parser: Any,
    ) -> ArchiveSyncResult:
        state = self._archive.get_sync_state(source_type, source_uri)
        cursor = int(state.cursor) if state and state.cursor else 0
        size = path.stat().st_size
        reset = False
        if cursor < 0 or cursor > size:
            cursor = 0
            reset = True
        elif state is not None and state.fingerprint is not None:
            if _jsonl_prefix_fingerprint(path, cursor) != state.fingerprint:
                cursor = 0
                reset = True

        documents: list[ArchiveDocument] = []
        fallback_counts = (
            {}
            if reset
            else _existing_fallback_counts(self._conn, source_type, source_uri)
        )
        consumed = cursor
        incomplete = False
        with path.open("rb") as handle:
            handle.seek(cursor)
            while True:
                offset = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    incomplete = True
                    break
                consumed = handle.tell()
                try:
                    payload = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                document = parser(payload, source_type, source_uri, offset)
                if document is not None:
                    documents.append(_number_fallback_occurrence(document, fallback_counts))

        if reset and incomplete:
            return ArchiveSyncResult(
                imported=0,
                updated=0,
                unchanged=0,
                deleted=0,
                cursor=state.cursor if state is not None else None,
                fingerprint=state.fingerprint if state is not None else None,
            )
        fingerprint = _jsonl_prefix_fingerprint(path, consumed)
        return self._archive.sync_source(
            source_type,
            source_uri,
            documents,
            cursor=str(consumed),
            fingerprint=fingerprint,
            replace=reset,
        )


def _claude_document(
    payload: Any,
    source_type: str,
    source_uri: str,
    offset: int,
) -> ArchiveDocument | None:
    if (
        not isinstance(payload, dict)
        or payload.get("type") not in {"user", "assistant"}
        or payload.get("isMeta") is True
        or "hookContext" in payload
    ):
        return None
    message = payload.get("message")
    if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
        return None
    content = _visible_text(message.get("content"), allowed_types={"text"})
    if not content:
        return None
    native_id = payload.get("uuid")
    source_updated_at = _optional_string(payload.get("timestamp"))
    item_id = (
        f"{message['role']}:{native_id}"
        if isinstance(native_id, str) and native_id.strip()
        else _content_item_id(message["role"], content, source_updated_at)
    )
    return ArchiveDocument(
        source_type=source_type,
        source_uri=source_uri,
        source_item_id=item_id,
        content=content,
        source_updated_at=source_updated_at,
        metadata={
            "role": message["role"],
            "session_id": _optional_string(payload.get("sessionId")),
        },
    )


def _codex_document(
    payload: Any,
    source_type: str,
    source_uri: str,
    offset: int,
) -> ArchiveDocument | None:
    if not isinstance(payload, dict) or payload.get("type") != "response_item":
        return None
    item = payload.get("payload")
    if (
        not isinstance(item, dict)
        or item.get("type") != "message"
        or item.get("role") not in {"user", "assistant"}
    ):
        return None
    allowed_types = {"input_text"} if item["role"] == "user" else {"output_text"}
    content = _visible_text(item.get("content"), allowed_types=allowed_types)
    if not content:
        return None
    native_id = item.get("id") or item.get("call_id")
    source_updated_at = _optional_string(payload.get("timestamp"))
    return ArchiveDocument(
        source_type=source_type,
        source_uri=source_uri,
        source_item_id=(
            f"{item['role']}:{native_id}"
            if isinstance(native_id, str) and native_id.strip()
            else _content_item_id(item["role"], content, source_updated_at)
        ),
        content=content,
        source_updated_at=source_updated_at,
        metadata={"role": item["role"]},
    )


def _visible_text(value: Any, *, allowed_types: set[str]) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") not in allowed_types:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _content_item_id(role: str, content: str, source_updated_at: str | None) -> str:
    identity = json.dumps(
        [role, content, source_updated_at],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{role}:content:{hashlib.sha256(identity).hexdigest()}"


_FALLBACK_ITEM_ID = re.compile(
    r"^(?:user|assistant):content:[0-9a-f]{64}(?::(?P<occurrence>[1-9][0-9]*))?$"
)


def _existing_fallback_counts(
    conn: sqlite3.Connection,
    source_type: str,
    source_uri: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT source_item_id FROM memory_archive_documents
        WHERE source_type = ? AND source_uri = ?
        """,
        (source_type, source_uri),
    ).fetchall()
    for row in rows:
        item_id = str(row[0])
        match = _FALLBACK_ITEM_ID.fullmatch(item_id)
        if match is None:
            continue
        occurrence = int(match.group("occurrence") or "1")
        base = item_id.rsplit(":", 1)[0] if match.group("occurrence") else item_id
        counts[base] = max(counts.get(base, 0), occurrence)
    return counts


def _number_fallback_occurrence(
    document: ArchiveDocument,
    counts: dict[str, int],
) -> ArchiveDocument:
    if _FALLBACK_ITEM_ID.fullmatch(document.source_item_id) is None:
        return document
    base = document.source_item_id
    occurrence = counts.get(base, 0) + 1
    counts[base] = occurrence
    if occurrence == 1:
        return document
    return replace(document, source_item_id=f"{base}:{occurrence}")


def _file_fingerprint(path: Path, *, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with path.open("rb") as handle:
        while remaining is None or remaining > 0:
            chunk_size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return f"sha256:{digest.hexdigest()}"


def _jsonl_prefix_fingerprint(path: Path, length: int) -> str:
    return _file_fingerprint(path, limit=length)


def _source_uri_for_path(source_type: str, path: Path) -> str:
    if source_type in {"claude_jsonl", "codex_session"}:
        return _jsonl_source_uri(source_type, path)
    return path.as_uri()


def _directory_manifest_uris(cursor: str | None) -> set[str]:
    if cursor is None:
        return set()
    try:
        value = json.loads(cursor)
    except json.JSONDecodeError:
        return set()
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _jsonl_source_uri(source_type: str, path: Path) -> str:
    with path.open("rb") as handle:
        for _index in range(256):
            raw_line = handle.readline()
            if not raw_line:
                break
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if source_type == "claude_jsonl":
                session_id = payload.get("sessionId")
            else:
                item = payload.get("payload")
                session_id = (
                    item.get("id")
                    if payload.get("type") == "session_meta" and isinstance(item, dict)
                    else None
                )
            if isinstance(session_id, str) and session_id.strip():
                return f"{source_type}:session:{session_id.strip()}"
    return path.as_uri()


__all__ = ["MemorySourceSynchronizer"]
