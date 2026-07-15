"""Prompt 08 brain adapter contract and mock brain tests."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.brain import (
    BrainAdapterError,
    BrainManager,
    BrainManagerError,
    BrainMemoryBlock,
    BrainMessage,
    BrainRequest,
    BrainResponse,
    BrainToolCall,
    BrainToolSpec,
    BrainUsage,
    MockBrainAdapter,
)
from jarvis.brain.claude_cli_adapter import ClaudeCliAdapter
from jarvis.brain.codex_cli_adapter import CodexCliAdapter
from jarvis.brain.openai_adapter import OpenAIAdapter


ROOT = Path(__file__).resolve().parents[1]


def make_request(input_text: str = "hello") -> BrainRequest:
    return BrainRequest(
        turn_id="turn-1",
        conversation_id="conversation-1",
        input_text=input_text,
        context_messages=[
            BrainMessage(role="user", content="previous", name="ozzy", metadata={"rank": 1})
        ],
        memory_blocks=[
            BrainMemoryBlock(
                id="memory-1",
                kind="preference",
                title="Tone",
                body="Be concise",
                priority=3,
                metadata={"source": "test"},
            )
        ],
        available_tools=[
            BrainToolSpec(
                name="read_status",
                description="Read status",
                input_schema={"type": "object"},
                risk="safe_read",
            )
        ],
        settings={"model": "mock-local"},
        metadata={"correlation_id": "corr-1"},
    )


class AlternateFakeAdapter:
    name = "alternate"
    default_model = "alternate-model"

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        return BrainResponse(text=f"alternate: {request.input_text}", model=self.default_model)


def test_brain_request_can_be_constructed_with_required_fields() -> None:
    request = make_request("status")

    assert request.turn_id == "turn-1"
    assert request.conversation_id == "conversation-1"
    assert request.input_text == "status"
    assert request.context_messages[0].role == "user"
    assert request.memory_blocks[0].priority == 3
    assert request.available_tools[0].name == "read_status"


def test_brain_response_defaults_to_empty_tool_calls() -> None:
    response = BrainResponse(text="hello")

    assert response.text == "hello"
    assert response.tool_calls == []
    assert response.model == "unknown"
    assert isinstance(response.usage, BrainUsage)
    assert response.raw_metadata == {}


def test_mock_brain_adapter_returns_deterministic_response() -> None:
    adapter = MockBrainAdapter()
    request = make_request("What now?")

    first = adapter.generate(request)
    second = adapter.generate(request)

    assert first == second
    assert first.text == "Jarvis mock response: What now?"
    assert first.model == "mock-local"


def test_mock_brain_adapter_handles_blank_input_deterministically() -> None:
    response = MockBrainAdapter().generate(make_request("  \n\t"))

    assert response.text == "Jarvis mock response: empty input"
    assert response.model == "mock-local"


def test_mock_brain_adapter_usage_is_deterministic() -> None:
    adapter = MockBrainAdapter()
    request = make_request("count these words")

    first = adapter.generate(request).usage
    second = adapter.generate(request).usage

    assert first == second
    assert first.input_tokens is not None
    assert first.output_tokens is not None
    assert first.total_tokens == first.input_tokens + first.output_tokens


def test_mock_brain_adapter_raw_metadata_marks_mock_and_stateless() -> None:
    response = MockBrainAdapter().generate(make_request("metadata"))

    assert response.raw_metadata["adapter"] == "mock"
    assert response.raw_metadata["stateless"] is True


def test_brain_manager_registers_mock_adapter() -> None:
    manager = BrainManager([MockBrainAdapter()], default_adapter="mock")

    assert manager.adapter_names() == ["mock"]
    assert manager.current_adapter_name == "mock"
    assert manager.get_adapter().name == "mock"


def test_brain_manager_rejects_duplicate_adapter_names() -> None:
    with pytest.raises(BrainManagerError, match="Duplicate brain adapter"):
        BrainManager([MockBrainAdapter(), MockBrainAdapter()], default_adapter="mock")


def test_brain_manager_rejects_unknown_adapter() -> None:
    manager = BrainManager([MockBrainAdapter()], default_adapter="mock")

    with pytest.raises(BrainManagerError, match="Unknown brain adapter"):
        manager.get_adapter("missing")


def test_brain_manager_rejects_missing_default_adapter() -> None:
    with pytest.raises(BrainManagerError, match="Default brain adapter is not registered"):
        BrainManager([MockBrainAdapter()], default_adapter="missing")


def test_brain_manager_generate_uses_current_default_adapter() -> None:
    manager = BrainManager([MockBrainAdapter()], default_adapter="mock")

    response = manager.generate(make_request("default path"))

    assert response.text == "Jarvis mock response: default path"
    assert response.model == "mock-local"


def test_brain_manager_generate_can_use_explicit_adapter_name() -> None:
    manager = BrainManager([MockBrainAdapter(), AlternateFakeAdapter()], default_adapter="mock")

    response = manager.generate(make_request("route me"), adapter_name="alternate")

    assert response.text == "alternate: route me"
    assert manager.current_adapter_name == "mock"


def test_brain_manager_switch_adapter_works_with_second_adapter() -> None:
    manager = BrainManager([MockBrainAdapter(), AlternateFakeAdapter()], default_adapter="mock")

    manager.switch_adapter("alternate")
    response = manager.generate(make_request("after switch"))

    assert manager.current_adapter_name == "alternate"
    assert response.text == "alternate: after switch"


def test_brain_manager_switch_adapter_does_not_modify_request_data() -> None:
    manager = BrainManager([MockBrainAdapter(), AlternateFakeAdapter()], default_adapter="mock")
    request = make_request("preserve me")
    before = asdict(request)

    manager.switch_adapter("alternate")
    manager.generate(request)

    assert asdict(request) == before


def test_brain_manager_from_config_ignores_test_adapter_product_config() -> None:
    config = SimpleNamespace(
        brain=SimpleNamespace(
            default_adapter="test",
            default_model="mock-configured",
            test=SimpleNamespace(enabled=True, model="mock-configured")
        )
    )

    manager = BrainManager.from_config(config)
    assert manager.adapter_names() == ["claude_cli"]
    assert manager.current_adapter_name == "claude_cli"
    assert manager.get_adapter().default_model == "mock-configured"


def test_brain_modules_do_not_import_runtime_side_effect_dependencies() -> None:
    forbidden_fragments = (
        "import socket",
        "import urllib",
        "from urllib",
        "jarvis.store",
        "jarvis.events",
        "jarvis.memory",
        "jarvis.voice",
    )
    for path in (ROOT / "jarvis" / "brain").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        offenders = [fragment for fragment in forbidden_fragments if fragment in source]
        assert offenders == [], f"{path.relative_to(ROOT)} imports runtime side effects: {offenders}"


def test_provider_placeholder_adapters_raise_brain_adapter_error() -> None:
    request = make_request("provider")

    with pytest.raises(BrainAdapterError, match="not implemented yet"):
        OpenAIAdapter().generate(request)


def test_brain_tool_call_can_represent_requested_tool_without_execution() -> None:
    call = BrainToolCall(id="tool-call-1", name="read_status", arguments={"verbose": True})
    response = BrainResponse(text="Need a tool", tool_calls=[call], model="mock-local")

    assert response.tool_calls == [call]
    assert response.tool_calls[0].risk == "safe_read"


def test_brain_runtime_files_avoid_forbidden_legacy_strings() -> None:
    forbidden = (
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )

    offenders: list[tuple[str, str]] = []
    for path in (ROOT / "jarvis" / "brain").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in source:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
