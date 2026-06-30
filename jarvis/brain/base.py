"""Brain adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class BrainMessage:
    role: str
    content: str


@dataclass(frozen=True)
class BrainRequest:
    conversation_id: str
    turn_id: str
    correlation_id: str
    system_prompt: str
    messages: tuple[BrainMessage, ...] = field(default_factory=tuple)
    memory: tuple[str, ...] = field(default_factory=tuple)
    settings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainResponse:
    text: str
    model: str
    finish_reason: str = "ok"
    error: str | None = None


class BrainAdapter(Protocol):
    def respond(self, request: BrainRequest) -> BrainResponse:
        """Return a response without retaining provider-side state."""
