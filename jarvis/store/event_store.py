"""Persistent append-only event store."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from jarvis.events.models import (
    Event,
    EventValidationError,
    event_from_row,
    event_to_row_payload,
    utc_now_iso,
)
from jarvis.security.redaction import redact_secrets


MAX_EVENT_QUERY_LIMIT = 1000


class EventStoreError(Exception):
    """Raised for event persistence and query errors."""


class EventStore:
    """SQLite-backed append-only event store.

    latest() returns newest events first. Stream-oriented list methods return
    ascending IDs so callers can replay events in storage order.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def append(
        self,
        type: str,
        source: str,
        payload: Mapping[str, Any],
        correlation_id: str | None = None,
        turn_id: str | None = None,
    ) -> Event:
        event_type = _normalize_required_text(type, "event type")
        event_source = _normalize_required_text(source, "event source")
        payload_json = _event_payload_json(payload)
        created_at = utc_now_iso()

        try:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT INTO events (
                      created_at, type, source, correlation_id, turn_id, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        event_type,
                        event_source,
                        correlation_id,
                        turn_id,
                        payload_json,
                    ),
                )
                event_id = int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise EventStoreError(f"Could not append event {event_type}: {exc}") from exc

        return Event(
            id=event_id,
            created_at=created_at,
            type=event_type,
            source=event_source,
            correlation_id=correlation_id,
            turn_id=turn_id,
            payload=_decode_payload(payload_json),
        )

    def get(self, event_id: int) -> Event | None:
        rows = self._fetch_events(
            """
            SELECT id, created_at, type, source, correlation_id, turn_id, payload_json
            FROM events
            WHERE id = ?
            """,
            (event_id,),
        )
        return rows[0] if rows else None

    def latest(self, limit: int = 100) -> list[Event]:
        bounded_limit = _bounded_limit(limit)
        return self._fetch_events(
            """
            SELECT id, created_at, type, source, correlation_id, turn_id, payload_json
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        )

    def list_after(self, after_id: int, limit: int = 100) -> list[Event]:
        bounded_limit = _bounded_limit(limit)
        return self._fetch_events(
            """
            SELECT id, created_at, type, source, correlation_id, turn_id, payload_json
            FROM events
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (after_id, bounded_limit),
        )

    def list_by_correlation_id(self, correlation_id: str, limit: int = 100) -> list[Event]:
        bounded_limit = _bounded_limit(limit)
        return self._fetch_events(
            """
            SELECT id, created_at, type, source, correlation_id, turn_id, payload_json
            FROM events
            WHERE correlation_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (correlation_id, bounded_limit),
        )

    def list_by_turn_id(self, turn_id: str, limit: int = 100) -> list[Event]:
        bounded_limit = _bounded_limit(limit)
        return self._fetch_events(
            """
            SELECT id, created_at, type, source, correlation_id, turn_id, payload_json
            FROM events
            WHERE turn_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (turn_id, bounded_limit),
        )

    def _fetch_events(self, sql: str, params: tuple[Any, ...]) -> list[Event]:
        cursor = self._conn.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            rows = cursor.execute(sql, params).fetchall()
            return [event_from_row(row) for row in rows]
        except (sqlite3.Error, EventValidationError) as exc:
            raise EventStoreError(f"Could not read events: {exc}") from exc
        finally:
            cursor.close()


def create_event_store(conn: sqlite3.Connection) -> EventStore:
    return EventStore(conn)


def _normalize_required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise EventStoreError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise EventStoreError(f"{label} must be a non-empty string.")
    return normalized


def _event_payload_json(payload: Mapping[str, Any]) -> str:
    try:
        redacted_payload = redact_secrets(payload)
        return event_to_row_payload(redacted_payload)
    except EventValidationError as exc:
        raise EventStoreError(str(exc)) from exc


def _decode_payload(payload_json: str) -> dict[str, Any]:
    row = {
        "id": 0,
        "created_at": utc_now_iso(),
        "type": "internal.payload.decode",
        "source": "event_store",
        "correlation_id": None,
        "turn_id": None,
        "payload_json": payload_json,
    }
    return event_from_row(row).payload


def _bounded_limit(limit: int) -> int:
    if limit <= 0:
        raise EventStoreError("Event query limit must be positive.")
    if limit > MAX_EVENT_QUERY_LIMIT:
        raise EventStoreError(f"Event query limit must be at most {MAX_EVENT_QUERY_LIMIT}.")
    return limit


__all__ = [
    "EventStore",
    "EventStoreError",
    "MAX_EVENT_QUERY_LIMIT",
    "create_event_store",
]
