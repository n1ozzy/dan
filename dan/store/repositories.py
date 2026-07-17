"""Shared helpers for SQLite repositories."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


MAX_REPOSITORY_LIMIT = 500


class RepositoryError(ValueError):
    """Raised by shared repository helpers."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def json_dumps(value: Any, label: str) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(f"{label} must be JSON serializable.") from exc


def json_loads_object(value: str | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RepositoryError(f"{label} must contain valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise RepositoryError(f"{label} must decode to a JSON object.")
    return decoded


def ensure_mapping(value: Mapping[str, Any] | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RepositoryError(f"{label} must be a mapping.")
    json_dumps(value, label)
    return dict(value)


def ensure_non_empty_text(value: str | None, label: str) -> str:
    if not isinstance(value, str):
        raise RepositoryError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise RepositoryError(f"{label} must be a non-empty string.")
    return normalized


def bounded_limit(
    limit: int | None,
    *,
    default: int,
    maximum: int = MAX_REPOSITORY_LIMIT,
) -> int:
    selected = default if limit is None else limit
    if type(selected) is not int:
        raise RepositoryError("limit must be an integer.")
    if selected <= 0:
        raise RepositoryError("limit must be positive.")
    if selected > maximum:
        raise RepositoryError(f"limit must be at most {maximum}.")
    return selected


__all__ = [
    "MAX_REPOSITORY_LIMIT",
    "RepositoryError",
    "bounded_limit",
    "ensure_mapping",
    "ensure_non_empty_text",
    "json_dumps",
    "json_loads_object",
    "utc_now_iso",
]
