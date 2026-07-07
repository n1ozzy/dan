"""Deterministic mock brain adapter."""

from __future__ import annotations

from jarvis.brain.base import BrainRequest, BrainResponse, BrainUsage


class MockBrainAdapter:
    """A stateless adapter for tests and local scaffold operation."""

    name = "mock"
    default_model = "mock-local"

    def __init__(self, default_model: str | None = None) -> None:
        if default_model:
            self.default_model = default_model

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest, *, on_delta=None) -> BrainResponse:
        # G0 §2: the mock cannot stream; it never calls on_delta and the
        # chunker sentence-cuts the final text after the fact.
        normalized_input = request.input_text.strip()
        if normalized_input:
            text = f"Jarvis mock response: {normalized_input}"
        else:
            text = "Jarvis mock response: empty input"

        usage = BrainUsage(
            input_tokens=_token_count_for_request(request),
            output_tokens=_token_count(text),
        )
        usage.total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)

        return BrainResponse(
            text=text,
            speech_text=f"Mock: {normalized_input}" if normalized_input else "Mock response",
            model=self.default_model,
            usage=usage,
            raw_metadata={"adapter": self.name, "stateless": True},
        )


def _token_count_for_request(request: BrainRequest) -> int:
    parts: list[str] = [request.input_text]
    parts.extend(message.content for message in request.context_messages)
    parts.extend(block.body for block in request.memory_blocks)
    return _token_count(" ".join(parts))


def _token_count(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 0
    return len(stripped.split())
