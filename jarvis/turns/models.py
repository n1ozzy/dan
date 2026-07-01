"""Conversation and turn models for Jarvis-owned history."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TurnRepositoryError(Exception):
    """Raised when turn repository operations fail."""


class ConversationRepositoryError(Exception):
    """Raised when conversation repository operations fail."""


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TurnSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    PANEL = "panel"
    CLI = "cli"
    API = "api"


class TurnStatus(StrEnum):
    RECEIVED = "received"
    STARTED = "started"
    CONTEXT_BUILT = "context_built"
    BRAIN_REQUESTED = "brain_requested"
    BRAIN_RESPONDED = "brain_responded"
    AWAITING_APPROVAL = "awaiting_approval"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Conversation:
    id: str
    created_at: str
    updated_at: str
    title: str | None
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    created_at: str
    updated_at: str
    source: str
    status: str
    input_text: str | None = None
    final_text: str | None = None
    brain_adapter: str | None = None
    brain_model: str | None = None
    context_snapshot: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "Conversation",
    "ConversationRepositoryError",
    "ConversationStatus",
    "Turn",
    "TurnRepositoryError",
    "TurnSource",
    "TurnStatus",
]
