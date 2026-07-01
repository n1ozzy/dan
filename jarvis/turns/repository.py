"""SQLite repositories for conversations and turns."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from jarvis.store.repositories import (
    RepositoryError,
    bounded_limit,
    ensure_mapping,
    ensure_non_empty_text,
    json_dumps,
    json_loads_object,
    utc_now_iso,
)
from jarvis.turns.models import (
    Conversation,
    ConversationRepositoryError,
    ConversationStatus,
    Turn,
    TurnRepositoryError,
    TurnSource,
    TurnStatus,
)


class ConversationRepository:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], str] | None = None) -> None:
        self._conn = conn
        self._now = now or utc_now_iso

    def create(
        self,
        title: str | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> Conversation:
        new_id = _optional_conversation_id(conversation_id) or uuid.uuid4().hex
        timestamp = self._now()
        metadata_dict = _conversation_mapping(metadata, "conversation metadata")
        normalized_title = _optional_title(title)

        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO conversations (
                      id, created_at, updated_at, title, status, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        timestamp,
                        timestamp,
                        normalized_title,
                        ConversationStatus.ACTIVE.value,
                        json_dumps(metadata_dict, "conversation metadata"),
                    ),
                )
        except sqlite3.Error as exc:
            raise ConversationRepositoryError(f"Could not create conversation: {exc}") from exc

        return Conversation(
            id=new_id,
            created_at=timestamp,
            updated_at=timestamp,
            title=normalized_title,
            status=ConversationStatus.ACTIVE.value,
            metadata=metadata_dict,
        )

    def get(self, conversation_id: str) -> Conversation | None:
        normalized_id = _conversation_id(conversation_id)
        rows = self._fetch(
            """
            SELECT id, created_at, updated_at, title, status, metadata_json
            FROM conversations
            WHERE id = ?
            """,
            (normalized_id,),
        )
        return rows[0] if rows else None

    def get_or_create(
        self,
        conversation_id: str | None = None,
        *,
        title: str | None = None,
    ) -> Conversation:
        if conversation_id is not None:
            existing = self.get(conversation_id)
            if existing is not None:
                return existing
        return self.create(title=title, conversation_id=conversation_id)

    def list_recent(self, limit: int = 50) -> list[Conversation]:
        selected_limit = _limit(limit, default=50, error_type=ConversationRepositoryError)
        return self._fetch(
            """
            SELECT id, created_at, updated_at, title, status, metadata_json
            FROM conversations
            ORDER BY updated_at DESC, created_at DESC, id ASC
            LIMIT ?
            """,
            (selected_limit,),
        )

    def list_recent_with_stats(
        self,
        limit: int = 50,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        selected_limit = _limit(limit, default=50, error_type=ConversationRepositoryError)
        where_clause = "" if include_archived else "WHERE c.status != ?"
        params: tuple[Any, ...]
        if include_archived:
            params = (selected_limit,)
        else:
            params = (ConversationStatus.ARCHIVED.value, selected_limit)

        try:
            rows = self._conn.execute(
                f"""
                SELECT c.id, c.created_at, c.updated_at, c.title, c.status, c.metadata_json,
                       COUNT(t.id) AS turn_count, MAX(t.created_at) AS latest_turn_at
                FROM conversations c
                LEFT JOIN turns t ON t.conversation_id = c.id
                {where_clause}
                GROUP BY c.id, c.created_at, c.updated_at, c.title, c.status, c.metadata_json
                ORDER BY c.updated_at DESC, c.created_at DESC, c.id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        except sqlite3.Error as exc:
            raise ConversationRepositoryError(f"Could not read conversations: {exc}") from exc

        return [_conversation_summary_from_stats_row(row) for row in rows]

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Conversation:
        existing = self.get(conversation_id)
        if existing is None:
            raise ConversationRepositoryError(f"Conversation not found: {conversation_id}")

        new_title = existing.title if title is None else _optional_title(title)
        new_status = existing.status if status is None else _conversation_status(status)
        new_metadata = existing.metadata if metadata is None else _conversation_mapping(
            metadata,
            "conversation metadata",
        )
        updated_at = self._now()

        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE conversations
                    SET title = ?, status = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_title,
                        new_status,
                        json_dumps(new_metadata, "conversation metadata"),
                        updated_at,
                        existing.id,
                    ),
                )
        except sqlite3.Error as exc:
            raise ConversationRepositoryError(
                f"Could not update conversation {existing.id}: {exc}"
            ) from exc

        return Conversation(
            id=existing.id,
            created_at=existing.created_at,
            updated_at=updated_at,
            title=new_title,
            status=new_status,
            metadata=new_metadata,
        )

    def archive(self, conversation_id: str) -> Conversation:
        return self.update(conversation_id, status=ConversationStatus.ARCHIVED.value)

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[Conversation]:
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise ConversationRepositoryError(f"Could not read conversations: {exc}") from exc

        return [_conversation_from_row(row) for row in rows]


class TurnRepository:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], str] | None = None) -> None:
        self._conn = conn
        self._now = now or utc_now_iso

    def create(
        self,
        conversation_id: str,
        *,
        source: str,
        input_text: str | None = None,
        status: str = "received",
        metadata: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> Turn:
        normalized_conversation_id = _turn_conversation_id(conversation_id)
        new_id = _optional_turn_id(turn_id) or uuid.uuid4().hex
        normalized_source = _turn_source(source)
        normalized_status = _turn_status(status)
        metadata_dict = _turn_mapping(metadata, "turn metadata")
        timestamp = self._now()

        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO turns (
                      id, conversation_id, created_at, updated_at, source, status,
                      input_text, final_text, brain_adapter, brain_model,
                      context_snapshot_json, error, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        normalized_conversation_id,
                        timestamp,
                        timestamp,
                        normalized_source,
                        normalized_status,
                        input_text,
                        None,
                        None,
                        None,
                        None,
                        None,
                        json_dumps(metadata_dict, "turn metadata"),
                    ),
                )
        except sqlite3.Error as exc:
            raise TurnRepositoryError(f"Could not create turn: {exc}") from exc

        return Turn(
            id=new_id,
            conversation_id=normalized_conversation_id,
            created_at=timestamp,
            updated_at=timestamp,
            source=normalized_source,
            status=normalized_status,
            input_text=input_text,
            metadata=metadata_dict,
        )

    def get(self, turn_id: str) -> Turn | None:
        normalized_id = _turn_id(turn_id)
        rows = self._fetch(
            """
            SELECT id, conversation_id, created_at, updated_at, source, status,
                   input_text, final_text, brain_adapter, brain_model,
                   context_snapshot_json, error, metadata_json
            FROM turns
            WHERE id = ?
            """,
            (normalized_id,),
        )
        return rows[0] if rows else None

    def update_status(self, turn_id: str, status: str, *, error: str | None = None) -> Turn:
        return self._update_turn(
            turn_id,
            status=_turn_status(status),
            error=error,
        )

    def attach_context_snapshot(self, turn_id: str, snapshot: Mapping[str, Any]) -> Turn:
        snapshot_dict = _turn_mapping(snapshot, "context snapshot")
        return self._update_turn(
            turn_id,
            status=TurnStatus.CONTEXT_BUILT.value,
            context_snapshot=snapshot_dict,
        )

    def attach_brain_request(
        self,
        turn_id: str,
        *,
        adapter: str | None = None,
        model: str | None = None,
    ) -> Turn:
        return self._update_turn(
            turn_id,
            status=TurnStatus.BRAIN_REQUESTED.value,
            brain_adapter=_optional_non_empty(adapter, "brain adapter"),
            brain_model=_optional_non_empty(model, "brain model"),
        )

    def finish(
        self,
        turn_id: str,
        *,
        final_text: str,
        brain_adapter: str | None = None,
        brain_model: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Turn:
        normalized_final_text = ensure_non_empty_text(final_text, "final_text")
        return self._update_turn(
            turn_id,
            status=TurnStatus.FINISHED.value,
            final_text=normalized_final_text,
            brain_adapter=_optional_non_empty(brain_adapter, "brain adapter"),
            brain_model=_optional_non_empty(brain_model, "brain model"),
            metadata_merge=_turn_mapping(metadata, "turn metadata"),
        )

    def fail(
        self,
        turn_id: str,
        *,
        error: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Turn:
        normalized_error = ensure_non_empty_text(error, "error")
        return self._update_turn(
            turn_id,
            status=TurnStatus.FAILED.value,
            error=normalized_error,
            metadata_merge=_turn_mapping(metadata, "turn metadata"),
        )

    def cancel(self, turn_id: str, *, reason: str | None = None) -> Turn:
        return self._update_turn(
            turn_id,
            status=TurnStatus.CANCELLED.value,
            error=_optional_non_empty(reason, "reason"),
        )

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        newest_first: bool = False,
    ) -> list[Turn]:
        normalized_conversation_id = _turn_conversation_id(conversation_id)
        selected_limit = _limit(limit, default=50, error_type=TurnRepositoryError)
        direction = "DESC" if newest_first else "ASC"
        return self._fetch(
            f"""
            SELECT id, conversation_id, created_at, updated_at, source, status,
                   input_text, final_text, brain_adapter, brain_model,
                   context_snapshot_json, error, metadata_json
            FROM turns
            WHERE conversation_id = ?
            ORDER BY created_at {direction}, rowid {direction}
            LIMIT ?
            """,
            (normalized_conversation_id, selected_limit),
        )

    def recent_for_context(self, conversation_id: str, *, limit: int = 12) -> list[Turn]:
        normalized_conversation_id = _turn_conversation_id(conversation_id)
        selected_limit = _limit(limit, default=12, error_type=TurnRepositoryError)
        turns = self._fetch(
            """
            SELECT id, conversation_id, created_at, updated_at, source, status,
                   input_text, final_text, brain_adapter, brain_model,
                   context_snapshot_json, error, metadata_json
            FROM turns
            WHERE conversation_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (normalized_conversation_id, selected_limit),
        )
        return list(reversed(turns))

    def _update_turn(
        self,
        turn_id: str,
        *,
        status: str,
        error: str | None = None,
        final_text: str | None = None,
        brain_adapter: str | None = None,
        brain_model: str | None = None,
        context_snapshot: Mapping[str, Any] | None = None,
        metadata_merge: Mapping[str, Any] | None = None,
    ) -> Turn:
        existing = self.get(turn_id)
        if existing is None:
            raise TurnRepositoryError(f"Turn not found: {turn_id}")

        updated_at = self._now()
        merged_metadata = dict(existing.metadata)
        if metadata_merge:
            merged_metadata.update(dict(metadata_merge))

        new_final_text = existing.final_text if final_text is None else final_text
        new_brain_adapter = existing.brain_adapter if brain_adapter is None else brain_adapter
        new_brain_model = existing.brain_model if brain_model is None else brain_model
        new_context_snapshot = (
            existing.context_snapshot if context_snapshot is None else dict(context_snapshot)
        )

        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE turns
                    SET status = ?, updated_at = ?, final_text = ?, brain_adapter = ?,
                        brain_model = ?, context_snapshot_json = ?, error = ?,
                        metadata_json = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        updated_at,
                        new_final_text,
                        new_brain_adapter,
                        new_brain_model,
                        None
                        if new_context_snapshot is None
                        else json_dumps(new_context_snapshot, "context snapshot"),
                        error,
                        json_dumps(merged_metadata, "turn metadata"),
                        existing.id,
                    ),
                )
        except sqlite3.Error as exc:
            raise TurnRepositoryError(f"Could not update turn {existing.id}: {exc}") from exc

        return Turn(
            id=existing.id,
            conversation_id=existing.conversation_id,
            created_at=existing.created_at,
            updated_at=updated_at,
            source=existing.source,
            status=status,
            input_text=existing.input_text,
            final_text=new_final_text,
            brain_adapter=new_brain_adapter,
            brain_model=new_brain_model,
            context_snapshot=new_context_snapshot,
            error=error,
            metadata=merged_metadata,
        )

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[Turn]:
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise TurnRepositoryError(f"Could not read turns: {exc}") from exc

        return [_turn_from_row(row) for row in rows]


def _conversation_from_row(row: sqlite3.Row | tuple[Any, ...]) -> Conversation:
    return Conversation(
        id=str(row[0]),
        created_at=str(row[1]),
        updated_at=str(row[2]),
        title=None if row[3] is None else str(row[3]),
        status=_conversation_status(str(row[4])),
        metadata=_conversation_json_object(str(row[5]), "conversation metadata"),
    )


def _conversation_summary_from_stats_row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    conversation = _conversation_from_row(row)
    return {
        "id": conversation.id,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "title": conversation.title,
        "status": conversation.status,
        "metadata": conversation.metadata,
        "turn_count": int(row[6]),
        "latest_turn_at": None if row[7] is None else str(row[7]),
    }


def _turn_from_row(row: sqlite3.Row | tuple[Any, ...]) -> Turn:
    return Turn(
        id=str(row[0]),
        conversation_id=str(row[1]),
        created_at=str(row[2]),
        updated_at=str(row[3]),
        source=_turn_source(str(row[4])),
        status=_turn_status(str(row[5])),
        input_text=None if row[6] is None else str(row[6]),
        final_text=None if row[7] is None else str(row[7]),
        brain_adapter=None if row[8] is None else str(row[8]),
        brain_model=None if row[9] is None else str(row[9]),
        context_snapshot=None
        if row[10] is None
        else _turn_json_object(str(row[10]), "context snapshot"),
        error=None if row[11] is None else str(row[11]),
        metadata=_turn_json_object(str(row[12]), "turn metadata"),
    )


def _optional_conversation_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ensure_non_empty_text(value, "conversation_id")
    except RepositoryError as exc:
        raise ConversationRepositoryError(str(exc)) from exc


def _optional_turn_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ensure_non_empty_text(value, "turn_id")
    except RepositoryError as exc:
        raise TurnRepositoryError(str(exc)) from exc


def _conversation_id(value: str) -> str:
    try:
        return ensure_non_empty_text(value, "conversation_id")
    except RepositoryError as exc:
        raise ConversationRepositoryError(str(exc)) from exc


def _turn_conversation_id(value: str) -> str:
    try:
        return ensure_non_empty_text(value, "conversation_id")
    except RepositoryError as exc:
        raise TurnRepositoryError(str(exc)) from exc


def _turn_id(value: str) -> str:
    try:
        return ensure_non_empty_text(value, "turn_id")
    except RepositoryError as exc:
        raise TurnRepositoryError(str(exc)) from exc


def _optional_title(title: str | None) -> str | None:
    if title is None:
        return None
    return title.strip() or None


def _conversation_status(status: str) -> str:
    try:
        return ConversationStatus(status).value
    except ValueError as exc:
        raise ConversationRepositoryError(f"Invalid conversation status: {status}") from exc


def _turn_source(source: str) -> str:
    try:
        return TurnSource(source).value
    except ValueError as exc:
        raise TurnRepositoryError(f"Invalid turn source: {source}") from exc


def _turn_status(status: str) -> str:
    try:
        return TurnStatus(status).value
    except ValueError as exc:
        raise TurnRepositoryError(f"Invalid turn status: {status}") from exc


def _optional_non_empty(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        return ensure_non_empty_text(value, label)
    except RepositoryError as exc:
        raise TurnRepositoryError(str(exc)) from exc


def _conversation_mapping(
    value: Mapping[str, Any] | None,
    label: str,
) -> dict[str, Any]:
    try:
        return ensure_mapping(value, label)
    except RepositoryError as exc:
        raise ConversationRepositoryError(str(exc)) from exc


def _turn_mapping(value: Mapping[str, Any] | None, label: str) -> dict[str, Any]:
    try:
        return ensure_mapping(value, label)
    except RepositoryError as exc:
        raise TurnRepositoryError(str(exc)) from exc


def _conversation_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        return json_loads_object(value, label)
    except RepositoryError as exc:
        raise ConversationRepositoryError(str(exc)) from exc


def _turn_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        return json_loads_object(value, label)
    except RepositoryError as exc:
        raise TurnRepositoryError(str(exc)) from exc


def _limit(limit: int, *, default: int, error_type: type[Exception]) -> int:
    try:
        return bounded_limit(limit, default=default)
    except RepositoryError as exc:
        raise error_type(str(exc)) from exc


__all__ = ["ConversationRepository", "TurnRepository"]
