"""Typed event models and JSON payload helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class EventValidationError(Exception):
    """Raised when an event row or payload is malformed."""


@dataclass(frozen=True)
class Event:
    id: int
    created_at: str
    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    turn_id: str | None = None


def utc_now_iso() -> str:
    """Return a compact UTC ISO-8601 timestamp for persisted rows."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_payload_jsonable(payload: Mapping[str, Any]) -> None:
    """Validate that an event payload is a JSON-serializable object."""

    if not isinstance(payload, Mapping):
        raise EventValidationError("Event payload must be a JSON object/dict.")

    try:
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"Event payload is not JSON serializable: {exc}") from exc


def event_to_row_payload(payload: Mapping[str, Any]) -> str:
    """Serialize a validated event payload for the events.payload_json column."""

    validate_payload_jsonable(payload)
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_from_row(row: sqlite3.Row | Mapping[str, Any]) -> Event:
    """Build an Event from a SQLite Row or row-like mapping."""

    payload_json = _row_value(row, "payload_json")
    try:
        payload = json.loads(str(payload_json))
    except json.JSONDecodeError as exc:
        raise EventValidationError(f"Event row has invalid payload_json: {exc}") from exc

    if not isinstance(payload, dict):
        raise EventValidationError("Event row payload_json must decode to a JSON object/dict.")

    return Event(
        id=int(_row_value(row, "id")),
        created_at=str(_row_value(row, "created_at")),
        type=str(_row_value(row, "type")),
        source=str(_row_value(row, "source")),
        correlation_id=_optional_str(_row_value(row, "correlation_id")),
        turn_id=_optional_str(_row_value(row, "turn_id")),
        payload=payload,
    )


def _row_value(row: sqlite3.Row | Mapping[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError, TypeError) as exc:
        raise EventValidationError(f"Event row is missing required column: {key}") from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = [
    "Event",
    "EventValidationError",
    "event_from_row",
    "event_to_row_payload",
    "utc_now_iso",
    "validate_payload_jsonable",
]
