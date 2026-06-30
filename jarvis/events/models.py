"""Event model placeholder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    turn_id: str | None = None
    ts: datetime | None = None
