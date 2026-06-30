"""Turn models from the frozen contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TurnSource(StrEnum):
    PANEL_TEXT = "panel_text"
    VOICE_TRANSCRIPT = "voice_transcript"


class TurnStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    source: TurnSource
    input_text: str
    status: TurnStatus
    correlation_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    response_text: str | None = None
