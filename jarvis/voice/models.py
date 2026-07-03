"""Voice contract models."""

from __future__ import annotations

from dataclasses import dataclass
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
    status: str = VoiceRequestStatus.QUEUED.value
    interrupt_policy: str = "no_interrupt"
    turn_id: str | None = None
    correlation_id: str | None = None
    engine: str | None = None
    voice: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class ListeningLease:
    id: str
    mode: str
    source: str
    status: str = "active"
    created_at: str | None = None
    expires_at: str | None = None
    released_at: str | None = None
