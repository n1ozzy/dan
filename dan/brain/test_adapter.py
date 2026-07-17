"""Test brain adapter for integration tests."""

from __future__ import annotations

from dan.brain.base import BrainAdapter, BrainRequest, BrainResponse, BrainUsage


class TestBrainAdapter:
    """Test adapter that provides deterministic responses for testing."""

    name = "test"
    default_model = "test-model"
    supports_streaming = False

    def __init__(self, default_model: str = "test-model") -> None:
        self.default_model = default_model

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest, *, on_delta=None) -> BrainResponse:
        text = f"Test response: {request.input_text}"
        usage = BrainUsage(
            input_tokens=len(request.input_text.split()),
            output_tokens=len(text.split()),
        )
        usage.total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)
        return BrainResponse(
            text=text,
            speech_text=text,
            model=self.default_model,
            usage=usage,
            raw_metadata={"adapter": self.name, "test": True},
        )


def create_test_adapter(config: object, generation_registry: object = None) -> TestBrainAdapter:
    brain_config = getattr(config, "brain", None)
    test_config = getattr(brain_config, "test", None)
    model = getattr(test_config, "model", "test-model") if test_config else "test-model"
    return TestBrainAdapter(default_model=model)