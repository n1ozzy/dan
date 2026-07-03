"""Stateless brain adapter contracts for Jarvis v4.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class BrainAdapterError(Exception):
    """Raised when a brain adapter cannot produce a response."""


class BrainGenerationCancelled(BrainAdapterError):
    """Raised when a generation was deliberately cancelled (barge-in leg 1),
    not because it failed.

    A subclass of ``BrainAdapterError`` so every existing ``except
    BrainAdapterError`` keeps catching it, but callers that care (the
    orchestrator) can tell a cancelled turn — CANCELLED, runtime back to IDLE —
    apart from a genuine failure (FAILED, runtime to ERROR)."""


@dataclass
class BrainMessage:
    role: str
    content: str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainMemoryBlock:
    id: str
    kind: str
    title: str
    body: str
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    risk: str = "safe_read"


@dataclass
class BrainToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: str = "safe_read"


@dataclass
class BrainRequest:
    turn_id: str
    conversation_id: str
    input_text: str
    context_messages: list[BrainMessage] = field(default_factory=list)
    memory_blocks: list[BrainMemoryBlock] = field(default_factory=list)
    available_tools: list[BrainToolSpec] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class BrainResponse:
    text: str
    tool_calls: list[BrainToolCall] = field(default_factory=list)
    model: str = "unknown"
    usage: BrainUsage = field(default_factory=BrainUsage)
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class BrainAdapter(Protocol):
    """Stateless model connector interface.

    `generate` MAY accept an optional keyword `on_delta(text: str)` (G0 §2):
    called zero or more times with incremental text fragments, best effort.
    Deltas carry no authority — the returned `BrainResponse.text` is the
    single canonical answer, and adapters that cannot stream simply never
    call it. The manager only passes `on_delta` to adapters that declare it,
    so existing adapters keep working unchanged.
    """

    name: str
    default_model: str

    def available_models(self) -> list[str]:
        """Return model identifiers this adapter can currently offer."""

    def generate(self, request: BrainRequest) -> BrainResponse:
        """Return a response without retaining provider-side state."""
