"""Voice contract models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class VoiceRequestStatus(StrEnum):
    QUEUED = "queued"
    SPEAKING = "speaking"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ListeningMode(StrEnum):
    HOLD = "hold"
    LOCKED = "locked"


@dataclass(frozen=True)
class VoiceRequest:
    id: str
    text: str
    priority: int
    status: VoiceRequestStatus = VoiceRequestStatus.QUEUED
    turn_id: str | None = None
    correlation_id: str | None = None
    engine: str | None = None
    voice: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ListeningLease:
    id: str
    mode: ListeningMode
    source: str
    created_at: datetime | None = None
    expires_at: datetime | None = None
    released_at: datetime | None = None
