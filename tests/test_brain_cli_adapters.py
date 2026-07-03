"""Prompt 12 safe subprocess CLI brain adapter tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain import (
    BrainAdapterError,
    BrainMemoryBlock,
    BrainMessage,
    BrainRequest,
    BrainToolSpec,
)
from jarvis.brain.claude_cli_adapter import ClaudeCliAdapter, format_cli_prompt
from jarvis.brain.codex_cli_adapter import CodexCliAdapter
from jarvis.brain.manager import BrainManager
from jarvis.brain.tool_call_parser import parse_tool_call_blocks
from jarvis.config import load_config
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)
DANGEROUS_PERMISSION_FLAG = "--dangerously-skip-permissions"


class FakeRunner:
    def __init__(
        self,
        *,
        stdout: str = "provider reply\n",
        stderr: str = "",
        returncode: int = 0,
        exception: Exception | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.exception = exception
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        command: list[str],
        input_text: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            {
                "command": list(command),
                "input_text": input_text,
                "timeout": timeout,
            }
        )
        if self.exception is not None:
            raise self.exception
        return subprocess.CompletedProcess(
            command,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class StaticFakeAdapter:
    name = "static"
    default_model = "static-model"

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest):  # type: ignore[no-untyped-def]
        from jarvis.brain import BrainResponse

        return BrainResponse(text=f"static: {request.input_text}", model=self.default_model)


def make_request() -> BrainRequest:
    return BrainRequest(
        turn_id="turn-1",
        conversation_id="conversation-1",
        input_text="Kim jesteś?",
        context_messages=[
            BrainMessage(
                role="system",
                content="You are Jarvis, a concise local runtime.",
                metadata={"kind": "persona"},
            ),
            BrainMessage(
                role="user",
                content="Previous question",
                metadata={"kind": "turn", "turn_id": "turn-prev", "field": "input_text"},
            ),
            BrainMessage(
                role="assistant",
                content="Previous answer",
                metadata={"kind": "turn", "turn_id": "turn-prev", "field": "final_text"},
            ),
        ],
        memory_blocks=[
            BrainMemoryBlock(
                id="mem-1",
                kind="preference",
                title="Style",
                body="Prefer short direct replies.",
                priority=5,
            )
        ],
        available_tools=[
            BrainToolSpec(
                name="shell",
                description="Run a shell command",
                input_schema={"type": "object"},
                risk="write",
            )
        ],
        settings={
            "provider_sessions_are_memory": True,
            "model": "ignored-by-cli-formatter",
        },
        metadata={
            "context_snapshot": {
                "provider_sessions_are_memory": True,
                "estimated_context_chars": 1234,
            },
            "huge": "x" * 5000,
        },
    )


def config_text(
    *,
    default_adapter: str = "mock",
    claude_enabled: bool = False,
    codex_enabled: bool = False,
) -> str:
    return f"""
[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = 41741
log_level = "INFO"

[database]
path = "~/.jarvis/jarvis.db"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "{default_adapter}"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[brain.claude_cli]
enabled = {str(claude_enabled).lower()}
command = "claude"
args = ["-p"]
model = "claude-test"
timeout_seconds = 120

[brain.codex_cli]
enabled = {str(codex_enabled).lower()}
command = "codex"
args = []
model = "codex-test"
timeout_seconds = 120

[memory]
enabled = true
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true

[voice]
enabled = false
speak_responses = false
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true

[audio]
enabled = false
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = false
always_listen_enabled = false

[panel]
enabled = false
api_base_url = "http://127.0.0.1:41741"
width = 420
height = 620

[security]
localhost_only = true
require_approval_for_shell = true
require_approval_for_file_write = true
require_approval_for_network = true
destructive_tools_enabled = false

[runtime]
home = "~/.jarvis"
logs_dir = "~/.jarvis/logs"
runtime_dir = "~/.jarvis/runtime"
pid_file = "~/.jarvis/runtime/jarvisd.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd"
install_automatically = false
"""


def write_config(tmp_path: Path, **kwargs: object) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text(config_text(**kwargs), encoding="utf-8")
    return path


def test_parser_extracts_one_valid_tool_call_block() -> None:
    parsed = parse_tool_call_blocks(
        '<jarvis_tool_call>{"name":"approval_probe","arguments":{"reason":"demo"}}</jarvis_tool_call>'
    )

    assert parsed.text == "Jarvis requested tool approval."
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "approval_probe"
    assert parsed.tool_calls[0].arguments == {"reason": "demo"}
    # FIX-07: the parser does not trust a model-declared risk; it fails safe and
    # the authoritative risk is derived downstream from the registered spec.
    assert parsed.tool_calls[0].risk == "destructive"
    assert parsed.parse_errors == []


def test_parser_ignores_model_declared_risk_and_fails_safe() -> None:
    # FIX-07: a model claiming a permissive risk for its own call must NOT set
    # it — the parser drops the field and fails safe (most restrictive).
    parsed = parse_tool_call_blocks(
        '<jarvis_tool_call>{"name":"file_read","arguments":{"path":"/etc/x"},'
        '"risk":"safe_read"}</jarvis_tool_call>'
    )

    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].risk == "destructive"  # the model's "safe_read" is ignored


def test_parser_extracts_multiple_valid_tool_call_blocks() -> None:
    parsed = parse_tool_call_blocks(
        "\n".join(
            [
                '<jarvis_tool_call>{"name":"echo","arguments":{"text":"one"}}</jarvis_tool_call>',
                '<jarvis_tool_call>{"id":"call-2","name":"approval_probe","arguments":{"reason":"two"},"risk":"shell_read"}</jarvis_tool_call>',
            ]
        )
    )

    assert [call.name for call in parsed.tool_calls] == ["echo", "approval_probe"]
    assert [call.arguments for call in parsed.tool_calls] == [
        {"text": "one"},
        {"reason": "two"},
    ]
    assert parsed.tool_calls[1].id == "call-2"
    # FIX-07: the model's declared "shell_read" is ignored; the parser fails safe.
    assert parsed.tool_calls[1].risk == "destructive"
    assert parsed.text == "Jarvis requested tool approval."


def test_parser_removes_valid_block_from_visible_response_text() -> None:
    parsed = parse_tool_call_blocks(
        'Before.\n<jarvis_tool_call>{"name":"approval_probe","arguments":{}}</jarvis_tool_call>\nAfter.'
    )

    assert parsed.text == "Before.\nAfter."
    assert len(parsed.tool_calls) == 1


def test_parser_uses_fallback_text_when_output_only_contains_tool_call() -> None:
    parsed = parse_tool_call_blocks(
        '   <jarvis_tool_call>{"name":"approval_probe","arguments":{}}</jarvis_tool_call>   '
    )

    assert parsed.text == "Jarvis requested tool approval."


def test_parser_missing_name_produces_parse_error_without_tool_call() -> None:
    parsed = parse_tool_call_blocks(
        '<jarvis_tool_call>{"arguments":{"reason":"missing"}}</jarvis_tool_call>'
    )

    assert parsed.tool_calls == []
    assert parsed.text == "Jarvis requested tool approval."
    assert any("name must be a non-empty string" in error for error in parsed.parse_errors)


def test_parser_non_object_arguments_produces_parse_error_without_tool_call() -> None:
    parsed = parse_tool_call_blocks(
        '<jarvis_tool_call>{"name":"approval_probe","arguments":["not","object"]}</jarvis_tool_call>'
    )

    assert parsed.tool_calls == []
    assert any("arguments must be a JSON object" in error for error in parsed.parse_errors)


def test_parser_malformed_json_produces_parse_error_without_tool_call() -> None:
    parsed = parse_tool_call_blocks(
        '<jarvis_tool_call>{"name":"approval_probe","arguments":</jarvis_tool_call>'
    )

    assert parsed.tool_calls == []
    assert any("invalid JSON" in error for error in parsed.parse_errors)


def test_prompt_formatter_includes_persona_system_messages() -> None:
    prompt = format_cli_prompt(make_request())

    assert "System context" in prompt
    assert "You are Jarvis, a concise local runtime." in prompt


def test_prompt_formatter_includes_memory_blocks() -> None:
    prompt = format_cli_prompt(make_request())

    assert "Memory blocks" in prompt
    assert "Style" in prompt
    assert "Prefer short direct replies." in prompt


def test_prompt_formatter_includes_recent_context_messages() -> None:
    prompt = format_cli_prompt(make_request())

    assert "Recent context" in prompt
    assert "Previous question" in prompt
    assert "Previous answer" in prompt


def test_prompt_formatter_renders_tool_argument_schema() -> None:
    # Bez schematu w prompcie model zgaduje kształt argumentów (żywy przypadek:
    # memory_save dostał {"key","value"} zamiast kind/title/body) — lista
    # narzędzi musi nieść nazwy pól, typy, enumy i które pola są wymagane.
    request = make_request()
    request.available_tools = [
        BrainToolSpec(
            name="memory_save",
            description="Save one durable memory block.",
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["fact", "identity"]},
                    "title": {"type": "string"},
                    "priority": {"type": "integer"},
                },
                "required": ["kind", "title"],
            },
            risk="memory_write",
        )
    ]

    prompt = format_cli_prompt(request)

    assert "kind (string, required, one of: fact|identity)" in prompt
    assert "title (string, required)" in prompt
    assert "priority (integer)" in prompt


def test_prompt_formatter_tool_without_properties_renders_no_args() -> None:
    prompt = format_cli_prompt(make_request())  # narzędzie "shell" ma pusty schemat

    assert "- shell [write]: Run a shell command" in prompt
    assert "args:" not in prompt.split("Available tools:")[1].split("Current user input:")[0]


def test_prompt_formatter_includes_user_input() -> None:
    prompt = format_cli_prompt(make_request())

    assert "Current user input" in prompt
    assert "Kim jesteś?" in prompt


def test_prompt_formatter_forces_provider_sessions_out_of_memory() -> None:
    prompt = format_cli_prompt(make_request())

    assert "Provider sessions are not Jarvis memory" in prompt
    assert "provider_sessions_are_memory = true" not in prompt.lower()


def test_prompt_formatter_does_not_grant_tool_execution() -> None:
    prompt = format_cli_prompt(make_request())

    assert "Tools are not executable in this call" in prompt
    assert "pending approval" in prompt
    assert "permission granted" not in prompt.lower()


def test_prompt_formatter_documents_tool_call_block_syntax() -> None:
    prompt = format_cli_prompt(make_request())

    assert '<jarvis_tool_call>{"name":"tool_name","arguments":{...}}</jarvis_tool_call>' in prompt


def test_prompt_formatter_says_tool_requests_require_approval_without_execution() -> None:
    prompt = format_cli_prompt(make_request())

    assert "Tool requests are not executed automatically" in prompt
    assert "Human approval is required" in prompt
    assert "Do not claim a requested tool has already been executed" in prompt


def test_claude_cli_adapter_uses_injected_fake_runner() -> None:
    runner = FakeRunner(stdout="claude says hi\n")
    adapter = ClaudeCliAdapter(command="fake-claude", args=["-p"], runner=runner)

    response = adapter.generate(make_request())

    assert response.text == "claude says hi"
    assert runner.calls[0]["command"] == ["fake-claude", "-p"]
    assert "Kim jesteś?" in runner.calls[0]["input_text"]


def test_codex_cli_adapter_uses_injected_fake_runner() -> None:
    runner = FakeRunner(stdout="codex says hi\n")
    adapter = CodexCliAdapter(command="fake-codex", args=["exec"], runner=runner)

    response = adapter.generate(make_request())

    assert response.text == "codex says hi"
    assert runner.calls[0]["command"] == ["fake-codex", "exec"]
    assert "Kim jesteś?" in runner.calls[0]["input_text"]


def test_successful_fake_runner_stdout_becomes_brain_response_text() -> None:
    response = ClaudeCliAdapter(runner=FakeRunner(stdout="final answer\n")).generate(make_request())

    assert response.text == "final answer"


def test_claude_cli_adapter_parses_tool_call_blocks_from_fake_runner_stdout() -> None:
    runner = FakeRunner(
        stdout=(
            'I need approval.\n'
            '<jarvis_tool_call>{"name":"approval_probe","arguments":{"reason":"adapter"}}</jarvis_tool_call>\n'
            'Waiting.'
        )
    )

    response = ClaudeCliAdapter(command="fake-claude", runner=runner).generate(make_request())

    assert response.text == "I need approval.\nWaiting."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "approval_probe"
    assert response.tool_calls[0].arguments == {"reason": "adapter"}


def test_codex_cli_adapter_parses_tool_call_blocks_from_fake_runner_stdout() -> None:
    runner = FakeRunner(
        stdout=(
            'Codex wants a probe.\n'
            '<jarvis_tool_call>{"name":"approval_probe","arguments":{"reason":"codex"}}</jarvis_tool_call>'
        )
    )

    response = CodexCliAdapter(command="fake-codex", runner=runner).generate(make_request())

    assert response.text == "Codex wants a probe."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "approval_probe"
    assert response.tool_calls[0].arguments == {"reason": "codex"}


def test_cli_adapter_raw_metadata_includes_parsed_tool_call_count() -> None:
    response = ClaudeCliAdapter(
        command="fake-claude",
        runner=FakeRunner(
            stdout='<jarvis_tool_call>{"name":"approval_probe","arguments":{}}</jarvis_tool_call>'
        ),
    ).generate(make_request())

    assert response.raw_metadata["parsed_tool_call_count"] == 1


def test_cli_adapter_raw_metadata_includes_parse_errors_for_malformed_blocks() -> None:
    response = ClaudeCliAdapter(
        command="fake-claude",
        runner=FakeRunner(
            stdout='Visible.\n<jarvis_tool_call>{"name":"approval_probe","arguments":</jarvis_tool_call>'
        ),
    ).generate(make_request())

    assert response.text == "Visible."
    assert response.tool_calls == []
    assert response.raw_metadata["parsed_tool_call_count"] == 0
    assert any(
        "invalid JSON" in error for error in response.raw_metadata["tool_call_parse_errors"]
    )


def test_cli_adapter_raw_metadata_marks_stateless() -> None:
    response = ClaudeCliAdapter(command="fake-claude", runner=FakeRunner()).generate(make_request())

    assert response.raw_metadata["adapter"] == "claude_cli"
    assert response.raw_metadata["command_name"] == "fake-claude"
    assert response.raw_metadata["stateless"] is True


def test_cli_adapters_default_to_provider_model_names() -> None:
    claude_response = ClaudeCliAdapter(runner=FakeRunner()).generate(make_request())
    codex_response = CodexCliAdapter(runner=FakeRunner()).generate(make_request())

    assert claude_response.model == "claude-cli"
    assert codex_response.model == "codex-cli"


def test_non_zero_exit_raises_brain_adapter_error() -> None:
    runner = FakeRunner(returncode=2, stderr="provider failed")

    with pytest.raises(BrainAdapterError, match="exited with code 2"):
        ClaudeCliAdapter(runner=runner).generate(make_request())


def test_timeout_raises_brain_adapter_error() -> None:
    runner = FakeRunner(exception=subprocess.TimeoutExpired(["fake"], timeout=1))

    with pytest.raises(BrainAdapterError, match="timed out"):
        ClaudeCliAdapter(runner=runner).generate(make_request())


def test_missing_executable_raises_brain_adapter_error() -> None:
    runner = FakeRunner(exception=FileNotFoundError("missing"))

    with pytest.raises(BrainAdapterError, match="executable not found"):
        ClaudeCliAdapter(command="missing-cli", runner=runner).generate(make_request())


def test_empty_stdout_raises_brain_adapter_error() -> None:
    runner = FakeRunner(stdout=" \n\t")

    with pytest.raises(BrainAdapterError, match="empty stdout"):
        ClaudeCliAdapter(runner=runner).generate(make_request())


def test_stderr_errors_are_redacted_for_obvious_secrets() -> None:
    runner = FakeRunner(
        returncode=1,
        stderr="bad OPENAI_API_KEY=sk-proj-secret Authorization: Bearer token",
    )

    with pytest.raises(BrainAdapterError) as exc_info:
        ClaudeCliAdapter(runner=runner).generate(make_request())

    rendered = str(exc_info.value)
    assert "sk-proj-secret" not in rendered
    assert "Bearer token" not in rendered
    assert "[REDACTED]" in rendered


def test_adapters_do_not_add_dangerous_permission_flags() -> None:
    claude_runner = FakeRunner()
    codex_runner = FakeRunner()

    ClaudeCliAdapter(runner=claude_runner).generate(make_request())
    CodexCliAdapter(runner=codex_runner).generate(make_request())

    assert DANGEROUS_PERMISSION_FLAG not in claude_runner.calls[0]["command"]
    assert DANGEROUS_PERMISSION_FLAG not in codex_runner.calls[0]["command"]


def test_adapter_rejects_configured_dangerous_permission_flag_before_running() -> None:
    runner = FakeRunner()

    with pytest.raises(BrainAdapterError, match="unsafe CLI argument"):
        ClaudeCliAdapter(args=[DANGEROUS_PERMISSION_FLAG], runner=runner).generate(make_request())

    assert runner.calls == []


def test_adapter_rejects_equivalent_dangerous_flag_spellings() -> None:
    # FIX-07: a one-token denylist missed equivalent spellings. The allowlist
    # fails closed on any unexpected flag, including value-attached forms and
    # unknown flags.
    runner = FakeRunner()
    for bad in (
        "--dangerously-skip-permissions=1",
        "--allow-dangerously-skip-permissions",
        "--some-unexpected-flag",
    ):
        with pytest.raises(BrainAdapterError, match="unsafe CLI argument"):
            ClaudeCliAdapter(args=["-p", bad], runner=runner).generate(make_request())
    assert runner.calls == []


def test_adapter_allows_the_known_safe_flags() -> None:
    # The legitimate flag set (print + streaming) is not rejected.
    runner = FakeRunner(stdout="ok\n")
    adapter = ClaudeCliAdapter(
        args=["-p", "--output-format", "text", "--model", "claude-x"], runner=runner
    )

    response = adapter.generate(make_request())

    assert response.text == "ok"


def test_adapter_allows_the_effort_flag() -> None:
    # The FIX-07 allowlist omitted --effort, so a config with
    # args = [-p, --model, sonnet, --effort, low] failed EVERY turn with
    # "unsafe CLI argument: --effort". --effort is a safe claude flag (session
    # effort level, value low/medium/high; see `claude --help`), so it must be
    # allowed like --model. Streaming path exercised via on_delta.
    runner = FakeRunner(stdout="ok\n")
    adapter = ClaudeCliAdapter(
        args=["-p", "--model", "sonnet", "--effort", "low"], runner=runner
    )

    response = adapter.generate(make_request())

    assert response.text == "ok"


def test_adapters_do_not_require_real_provider_cli_when_runner_is_injected() -> None:
    runner = FakeRunner(stdout="works without executable\n")

    response = ClaudeCliAdapter(command="definitely-missing-provider", runner=runner).generate(make_request())

    assert response.text == "works without executable"


def test_brain_manager_from_config_registers_only_mock_by_default() -> None:
    config = load_config(ROOT / "config" / "jarvis.example.toml")

    manager = BrainManager.from_config(config)

    assert manager.adapter_names() == ["mock"]


def test_brain_manager_from_config_registers_claude_cli_when_enabled(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, claude_enabled=True))

    manager = BrainManager.from_config(config)

    assert manager.adapter_names() == ["claude_cli", "mock"]
    assert manager.current_adapter_name == "mock"


def test_brain_manager_from_config_registers_codex_cli_when_enabled(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, codex_enabled=True))

    manager = BrainManager.from_config(config)

    assert manager.adapter_names() == ["codex_cli", "mock"]
    assert manager.current_adapter_name == "mock"


def test_brain_manager_from_config_can_use_claude_cli_as_default(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, default_adapter="claude_cli"))

    manager = BrainManager.from_config(config)

    assert manager.current_adapter_name == "claude_cli"
    assert manager.get_adapter().name == "claude_cli"


def test_brain_manager_from_config_can_use_codex_cli_as_default(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, default_adapter="codex_cli"))

    manager = BrainManager.from_config(config)

    assert manager.current_adapter_name == "codex_cli"
    assert manager.get_adapter().name == "codex_cli"


def test_text_turn_pipeline_still_works_with_mock_default() -> None:
    manager = BrainManager.from_config(load_config(ROOT / "config" / "jarvis.example.toml"))

    response = manager.generate(make_request())

    assert manager.current_adapter_name == "mock"
    assert response.text == "Jarvis mock response: Kim jesteś?"


def test_text_turn_pipeline_can_use_fake_custom_adapter_by_injection() -> None:
    manager = BrainManager([StaticFakeAdapter()], default_adapter="static")

    response = manager.generate(make_request())

    assert response.text == "static: Kim jesteś?"
    assert response.model == "static-model"


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_avoid_forbidden_legacy_strings() -> None:
    offenders: list[tuple[str, str]] = []
    for path in (ROOT / "jarvis").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_RUNTIME_SNIPPETS:
            if snippet in source:
                offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
