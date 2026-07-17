"""Prompt 06 daemon app and local HTTP API smoke tests."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest

from dan.security.redaction import REDACTION_PLACEHOLDER
from dan.tools.permissions import RequestSource

from tests.git_guards import assert_schema_and_migrations_unchanged
from dan.brain import BrainManager, BrainRequest
from dan.brain.claude_cli_adapter import ClaudeCliAdapter, apply_claude_system_prompt
from dan.brain.claude_cli_contract import build_claude_cli_command
from dan.brain.test_adapter import TestBrainAdapter as HermeticBrainAdapter
from dan.config import (
    COMPILED_MEMORY_ENABLED_ENV,
    COMPILED_MEMORY_FORCE_DISABLED_ENV,
    ConfigError,
    load_config,
)
from dan.daemon.app import BRAIN_ADAPTER_SETTING_KEY, DaemonApp, create_daemon_app
from dan.daemon.lifecycle import MAX_REQUEST_BODY_BYTES, DaemonServer, build_server
from dan.daemon.state_machine import RuntimeState
from dan.memory.compiler import CompiledMemoryContext, MemoryCompiler, MemoryCompilerConfig
from dan.runtime.supervisor import RuntimeSupervisor
from dan.store.db import close_quietly
from dan.store.migrations import LATEST_SCHEMA_VERSION
from dan.tools.registry import Tool, ToolRunRecorder


ROOT = Path(__file__).resolve().parents[1]


def config_text(
    db_path: Path,
    *,
    port: int = 41741,
    memory_enabled: bool = True,
    compiled_context_enabled: bool = False,
    compiled_context_max_items: int | None = None,
    compiled_context_max_chars: int | None = None,
    compiled_context_include_procedural: bool = False,
    brain_default_adapter: str = "test",
    extra_toml: str = "",
) -> str:
    runtime_home = db_path.parent
    compiler_defaults = MemoryCompilerConfig()
    compiled_context_max_items = (
        compiler_defaults.max_items
        if compiled_context_max_items is None
        else compiled_context_max_items
    )
    compiled_context_max_chars = (
        compiler_defaults.max_chars
        if compiled_context_max_chars is None
        else compiled_context_max_chars
    )
    return (
        f"""
[daemon]
name = "dand"
host = "127.0.0.1"
port = {port}
log_level = "INFO"

[database]
path = "{db_path}"
migrations = "manual"
destroy_existing = false

    [brain]
    default_adapter = "{brain_default_adapter}"
    default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[memory]
enabled = {str(memory_enabled).lower()}
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true
compiled_context_enabled = {str(compiled_context_enabled).lower()}
compiled_context_max_items = {compiled_context_max_items}
compiled_context_max_chars = {compiled_context_max_chars}
compiled_context_include_procedural = {str(compiled_context_include_procedural).lower()}

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
api_base_url = "http://127.0.0.1:{port}"
width = 420
height = 620

[security]
localhost_only = true
api_token_required = false
require_approval_for_shell = true
require_approval_for_file_write = true
require_approval_for_network = true
require_approval_for_ui = true
require_approval_for_terminal = true
require_approval_for_memory = true
destructive_tools_enabled = false

[brain.test]
enabled = true
model = "test-model"

[runtime]
home = "{runtime_home}"
logs_dir = "{runtime_home / "logs"}"
runtime_dir = "{runtime_home / "runtime"}"
pid_file = "{runtime_home / "runtime" / "dand.pid"}"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.dan.dand"
install_automatically = false
"""
        + extra_toml
    )


def write_config(
    path: Path,
    db_path: Path,
    *,
    port: int = 41741,
    memory_enabled: bool = True,
    compiled_context_enabled: bool = False,
    compiled_context_max_items: int | None = None,
    compiled_context_max_chars: int | None = None,
    compiled_context_include_procedural: bool = False,
    brain_default_adapter: str = "test",
    extra_toml: str = "",
) -> Path:
    path.write_text(
        config_text(
            db_path,
            port=port,
            memory_enabled=memory_enabled,
            compiled_context_enabled=compiled_context_enabled,
            compiled_context_max_items=compiled_context_max_items,
            compiled_context_max_chars=compiled_context_max_chars,
            compiled_context_include_procedural=compiled_context_include_procedural,
            brain_default_adapter=brain_default_adapter,
            extra_toml=extra_toml,
        ),
        encoding="utf-8",
    )
    return path


def rewrite_voice_section(path: Path, voice_toml: str) -> Path:
    content = path.read_text(encoding="utf-8")
    start = content.find("[voice]")
    if start < 0:
        raise ValueError("voice section not found in generated config")

    end = content.find("\n[", start + len("[voice]"))
    if end < 0:
        end = len(content)

    updated = content[:start] + "[voice]\n" + voice_toml.strip() + "\n" + content[end:]
    path.write_text(updated, encoding="utf-8")
    return path


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    production_manager = daemon_app.brain_manager
    daemon_app.brain_manager = BrainManager(
        [HermeticBrainAdapter(default_model="test-model")],
        default_adapter="test",
    )
    if production_manager is not None:
        production_manager.close()
    try:
        yield daemon_app
    finally:
        daemon_app.close()


@contextmanager
def running_server(app: DaemonApp) -> Iterator[str]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="dan-test-http", daemon=True)
    thread.start()
    try:
        yield server.base_url
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


def request_json(
    method: str,
    url: str,
    payload: object | bytes | None = None,
) -> tuple[int, dict[str, object]]:
    data: bytes | None
    headers = {"Accept": "application/json"}
    if isinstance(payload, bytes):
        data = payload
        headers["Content-Type"] = "application/json"
    elif payload is None:
        data = None
    else:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def request_raw(
    method: str,
    url: str,
    payload: object | bytes | None = None,
) -> tuple[int, str, str]:
    data: bytes | None
    headers = {"Accept": "application/json"}
    if isinstance(payload, bytes):
        data = payload
        headers["Content-Type"] = "application/json"
    elif payload is None:
        data = None
    else:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode("utf-8")


def request_declared_json_length(method: str, url: str, content_length: int) -> tuple[int, str, str]:
    parsed = urlparse(url)
    assert parsed.hostname is not None
    assert parsed.port is not None
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    conn = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        conn.putrequest(method, path)
        conn.putheader("Accept", "application/json")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(content_length))
        conn.endheaders()
        response = conn.getresponse()
        return response.status, response.getheader("Content-Type", ""), response.read().decode("utf-8")
    finally:
        conn.close()


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.pop("DAN_CONFIG", None)
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "dan.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def event_types(app: DaemonApp) -> list[str]:
    assert app.event_store is not None
    return [event.type for event in app.event_store.list_after(0, limit=100)]


def table_count(app: DaemonApp, table: str) -> int:
    assert app.conn is not None
    return int(app.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def settings_value(app: DaemonApp, key: str) -> object | None:
    return app.get_settings().get(key)


def insert_runtime_conversation(app: DaemonApp, conversation_id: str = "conversation-runtime") -> None:
    assert app.conn is not None
    app.conn.execute(
        """
        INSERT INTO conversations (id, created_at, updated_at, title, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            "2026-07-04T12:00:00+00:00",
            "2026-07-04T12:00:00+00:00",
            "Runtime",
            "active",
            "{}",
        ),
    )
    app.conn.commit()


def insert_runtime_memory_item(
    app: DaemonApp,
    *,
    memory_id: str,
    canonical_key: str | None = None,
    title: str,
    claim: str,
    content: str | None = None,
    evidence_quote: str = "Runtime evidence quote should not render.",
    observation_text: str | None = None,
) -> None:
    assert app.conn is not None
    observation_id = f"observation-{memory_id}"
    app.conn.execute(
        """
        INSERT INTO memory_items (
          id, canonical_key, kind, scope, namespace, title, claim, content,
          status, confidence, sensitivity, source_policy, created_at,
          updated_at, last_used_at, last_confirmed_at, supersedes, superseded_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            canonical_key or f"key-{memory_id}",
            "semantic",
            "project",
            "project/dan",
            title,
            claim,
            content if content is not None else claim,
            "active",
            "high",
            "low",
            "candidate_evidence",
            "2026-07-04T11:00:00+00:00",
            "2026-07-04T12:00:00+00:00",
            None,
            None,
            None,
            None,
        ),
    )
    if observation_text is not None:
        app.conn.execute(
            """
            INSERT INTO memory_observations (
              id, source_type, source_id, conversation_id, turn_id, event_id,
              observed_text, detected_kind, sensitivity, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                "test",
                "source-runtime",
                "conversation-runtime",
                "turn-runtime",
                1,
                observation_text,
                None,
                "unknown",
                "2026-07-04T12:00:30+00:00",
            ),
        )
    app.conn.execute(
        """
        INSERT INTO memory_evidence (
          id, memory_id, candidate_id, observation_id, conversation_id, turn_id,
          event_id, quote, weight, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"evidence-{memory_id}",
            memory_id,
            None,
            observation_id,
            "conversation-runtime",
            "turn-runtime",
            1,
            evidence_quote,
            1.0,
            "2026-07-04T12:01:00+00:00",
        ),
    )
    app.conn.commit()


def tool_run_count_for_approval(app: DaemonApp, approval_id: object) -> int:
    assert app.conn is not None
    return int(
        app.conn.execute(
            "SELECT COUNT(*) FROM tool_runs WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()[0]
    )


class ApiFakeTool(Tool):
    description = "fake API smoke tool"
    input_schema = {"type": "object"}

    def __init__(self, *, name: str, risk: str):
        self.name = name
        self.risk = risk
        self.calls: list[dict[str, object]] = []

    def run(self, arguments: dict[str, object]) -> dict[str, object]:
        payload = dict(arguments)
        self.calls.append(payload)
        return {"received": payload}


class RuntimeSpyCompiler:
    def __init__(self) -> None:
        self.calls = 0

    def compile(self, request: object) -> CompiledMemoryContext:
        del request
        self.calls += 1
        return CompiledMemoryContext()


def compiled_memory_messages(result: object) -> list[object]:
    return [
        message
        for message in result.request.context_messages
        if message.metadata.get("kind") == "compiled_memory"
    ]


def compiled_memory_field_names(content: str) -> list[str]:
    names: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
        if ": " in stripped:
            names.append(stripped.split(":", 1)[0])
    return names


def test_create_daemon_app_with_temp_config_initializes_temp_db_only(config_path: Path) -> None:
    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.paths.db_path.is_file()
        assert daemon_app.paths.db_path.parent == config_path.parent / "home"
        assert daemon_app.paths.home == config_path.parent / "home"
    finally:
        daemon_app.close()


def test_runtime_context_output_shape_remains_default_off(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(COMPILED_MEMORY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)
    compiler = RuntimeSpyCompiler()
    compiler_config = MemoryCompilerConfig(max_items=1, max_chars=64)

    daemon_app = create_daemon_app(
        config_path,
        memory_compiler=compiler,
        compiled_memory_config=compiler_config,
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._memory_compiler is compiler
        assert daemon_app.context_builder._compiled_memory_config is compiler_config
        assert daemon_app.context_builder._compiled_memory_enabled is False

        insert_runtime_conversation(daemon_app)
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime default-off check",
        )

        assert compiler.calls == 0
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": True,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
        assert [message.metadata.get("kind") for message in result.request.context_messages] == [
            "persona"
        ]
        assert [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ] == []
        assert result.context_snapshot["memory_block_count"] == 0
        assert "compiled_memory" not in json.dumps(
            {
                "context_messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "metadata": message.metadata,
                    }
                    for message in result.request.context_messages
                ],
                "memory_blocks": [block.__dict__ for block in result.request.memory_blocks],
                "context_snapshot": result.context_snapshot,
            },
            sort_keys=True,
        )
    finally:
        daemon_app.close()


def test_create_daemon_app_default_runtime_path_does_not_call_memory_compiler(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingMemoryCompiler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("default runtime context build must not create MemoryCompiler")

    monkeypatch.delenv(COMPILED_MEMORY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)
    monkeypatch.setattr("dan.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("dan.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)

    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._memory_compiler is None
        assert daemon_app.context_builder._compiled_memory_enabled is False

        insert_runtime_conversation(daemon_app)
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime default path",
        )

        assert [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ] == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": False,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_create_daemon_app_config_enabled_wires_compiled_memory_context(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=True,
        compiled_context_max_items=1,
        compiled_context_max_chars=256,
        compiled_context_include_procedural=True,
    )

    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is True
        assert isinstance(daemon_app.context_builder._memory_compiler, MemoryCompiler)
        assert daemon_app.context_builder._compiled_memory_config == MemoryCompilerConfig(
            max_items=1,
            max_chars=256,
            include_procedural=True,
        )

        raw_evidence_quote = "RAW_EVIDENCE_QUOTE_RUNTIME_CONFIG_MARKER"
        raw_observation_text = "RAW_OBSERVATION_TEXT_RUNTIME_CONFIG_MARKER"
        raw_secret_marker = "sk-runtimeconfig1234567890"
        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="MEMORY_ID_RUNTIME_CONFIG_RAW_MARKER",
            canonical_key=f"CANONICAL_KEY_RUNTIME_CONFIG_RAW_MARKER {raw_secret_marker}",
            title="Runtime config memory",
            claim=f"Explicit dev config can inject compiled memory. {raw_secret_marker}",
            content=f"RAW_CONTENT_RUNTIME_CONFIG_MARKER {raw_secret_marker}",
            evidence_quote=raw_evidence_quote,
            observation_text=raw_observation_text,
        )

        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime config enabled check",
        )

        messages = [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ]
        assert len(messages) == 1
        assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
        assert "Runtime config memory" in messages[0].content
        assert "Explicit dev config can inject compiled memory." in messages[0].content
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": True,
            "compiler_available": True,
            "compiled_memory_attempted": True,
            "compiled_memory_section_present": True,
            "selected_count": 1,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }

        rendered_final_context = json.dumps(
            {
                "request": asdict(result.request),
                "context_snapshot": result.context_snapshot,
                "compiled_memory_diagnostics": asdict(result.compiled_memory_diagnostics),
            },
            sort_keys=True,
        )
        forbidden_markers = (
            "memory_id",
            "canonical_key",
            "audit_metadata",
            "skipped_items",
            "MEMORY_ID_RUNTIME_CONFIG_RAW_MARKER",
            "CANONICAL_KEY_RUNTIME_CONFIG_RAW_MARKER",
            raw_evidence_quote,
            raw_observation_text,
            "RAW_CONTENT_RUNTIME_CONFIG_MARKER",
            raw_secret_marker,
            "Traceback",
            "RuntimeError",
            "compiler boom",
        )
        assert [marker for marker in forbidden_markers if marker in rendered_final_context] == []
    finally:
        daemon_app.close()


def test_env_enable_true_wires_compiled_memory_without_prompt_or_diagnostics_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "true")
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=False,
    )

    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.config.memory.enabled is True
        assert daemon_app.config.memory.compiled_context_enabled is False
        assert daemon_app.context_builder._compiled_memory_enabled is True
        assert isinstance(daemon_app.context_builder._memory_compiler, MemoryCompiler)

        raw_evidence_quote = "RAW_ENV_ENABLE_EVIDENCE_QUOTE"
        raw_secret_marker = "sk-env-enable1234567890"
        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-env-enabled",
            canonical_key=f"RAW_ENV_ENABLE_KEY {raw_secret_marker}",
            title="Runtime env enabled title",
            claim=f"Runtime env enabled claim. {raw_secret_marker}",
            content=f"RAW_ENV_ENABLE_CONTENT {raw_secret_marker}",
            evidence_quote=raw_evidence_quote,
            observation_text=f"RAW_ENV_ENABLE_OBSERVATION {raw_secret_marker}",
        )

        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime env enabled check",
        )

        messages = compiled_memory_messages(result)
        assert len(messages) == 1
        assert messages[0].metadata == {"kind": "compiled_memory", "untrusted": True}
        assert compiled_memory_field_names(messages[0].content) == [
            "title",
            "claim",
            "evidence_count",
        ]
        assert "Runtime env enabled title" in messages[0].content
        assert "Runtime env enabled claim." in messages[0].content
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": True,
            "compiler_available": True,
            "compiled_memory_attempted": True,
            "compiled_memory_section_present": True,
            "selected_count": 1,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }

        rendered_final_context = json.dumps(
            {
                "request": asdict(result.request),
                "context_snapshot": result.context_snapshot,
            },
            sort_keys=True,
        )
        diagnostics_text = json.dumps(
            asdict(result.compiled_memory_diagnostics),
            sort_keys=True,
        )
        forbidden_model_markers = (
            "memory_id",
            "canonical_key",
            "audit_metadata",
            "skipped_items",
            "mem-runtime-env-enabled",
            "RAW_ENV_ENABLE_KEY",
            raw_evidence_quote,
            "RAW_ENV_ENABLE_OBSERVATION",
            "RAW_ENV_ENABLE_CONTENT",
            raw_secret_marker,
            "compiled_memory_diagnostics",
        )
        assert [marker for marker in forbidden_model_markers if marker in rendered_final_context] == []
        assert "Runtime env enabled title" not in diagnostics_text
        assert "Runtime env enabled claim" not in diagnostics_text
        assert raw_secret_marker not in diagnostics_text
    finally:
        daemon_app.close()


def test_request_override_false_remains_request_local_with_env_enablement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "true")
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(tmp_path / "dan.toml", db_path)

    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is True

        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-env-request-local",
            title="Runtime env request-local title",
            claim="Runtime env request-local claim.",
        )

        disabled = daemon_app.context_builder.build_request(
            turn_id="turn-runtime-disabled",
            conversation_id="conversation-runtime",
            input_text="runtime env request-local disabled check",
            compiled_memory_enabled_override=False,
        )
        enabled = daemon_app.context_builder.build_request(
            turn_id="turn-runtime-enabled",
            conversation_id="conversation-runtime",
            input_text="runtime env request-local enabled check",
        )

        assert compiled_memory_messages(disabled) == []
        assert len(compiled_memory_messages(enabled)) == 1
        assert asdict(disabled.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": True,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
        assert asdict(enabled.compiled_memory_diagnostics)[
            "compiled_memory_enabled"
        ] is True
    finally:
        daemon_app.close()


def test_env_force_disabled_blocks_every_runtime_enablement_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingMemoryCompiler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("env force-disabled runtime must not create MemoryCompiler")

    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "true")
    monkeypatch.setenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, "true")
    monkeypatch.setattr("dan.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("dan.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "dan"),
        ),
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.config.memory.compiled_context_enabled is True
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is False
        assert daemon_app.context_builder._memory_compiler is None

        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-env-force-disabled",
            title="Runtime env force-disabled title",
            claim="Runtime env force-disabled claim must not render.",
        )
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime env force-disabled check",
            compiled_memory_enabled_override=True,
        )

        rendered = json.dumps(
            {
                "request": asdict(result.request),
                "context_snapshot": result.context_snapshot,
            },
            sort_keys=True,
        )
        assert compiled_memory_messages(result) == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": False,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
        assert "Runtime env force-disabled title" not in rendered
        assert "Runtime env force-disabled claim" not in rendered
        assert "compiled_memory_force_disabled" not in rendered
        assert "compiled_memory_diagnostics" not in rendered
    finally:
        daemon_app.close()


def test_memory_disabled_blocks_env_enablement_and_request_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingMemoryCompiler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("memory.enabled=false must block env compiled memory")

    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "true")
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)
    monkeypatch.setattr("dan.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("dan.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        memory_enabled=False,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "dan"),
        ),
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.config.memory.enabled is False
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is False
        assert daemon_app.context_builder._memory_compiler is None

        insert_runtime_conversation(daemon_app)
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime memory disabled env check",
            compiled_memory_enabled_override=True,
        )

        assert compiled_memory_messages(result) == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": False,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_invalid_env_enable_value_does_not_enable_compiled_memory(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "definitely")
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)
    compiler = RuntimeSpyCompiler()

    daemon_app = create_daemon_app(config_path, memory_compiler=compiler)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is False

        insert_runtime_conversation(daemon_app)
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime invalid env check",
        )

        assert compiler.calls == 0
        assert compiled_memory_messages(result) == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": True,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_invalid_env_force_disabled_value_blocks_env_enablement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingMemoryCompiler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("invalid env force-disabled value must fail closed")

    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "true")
    monkeypatch.setenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, "definitely")
    monkeypatch.setattr("dan.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("dan.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(tmp_path / "dan.toml", db_path)

    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is False
        assert daemon_app.context_builder._memory_compiler is None

        insert_runtime_conversation(daemon_app)
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime invalid force-disabled env check",
            compiled_memory_enabled_override=True,
        )

        assert compiled_memory_messages(result) == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": False,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_compiled_memory_operator_env_controls_are_not_panel_or_api_toggles() -> None:
    forbidden_markers = (
        "DAN_COMPILED_MEMORY",
        "compiled_memory",
        "compiled-memory",
        "compiled memory",
    )
    user_facing_paths = [
        ROOT / "dan" / "daemon" / "lifecycle.py",
        *sorted((ROOT / "dan" / "api").glob("*.py")),
        *sorted((ROOT / "dan" / "panel" / "assets").glob("*")),
    ]

    offenders: list[tuple[str, str]] = []
    for path in user_facing_paths:
        if not path.is_file() or path.suffix not in {".html", ".js", ".css", ".py"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for marker in forbidden_markers:
            if marker.lower() in text:
                offenders.append((str(path.relative_to(ROOT)), marker))

    assert offenders == []


def test_create_daemon_app_force_disabled_blocks_config_enabled_compiled_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingMemoryCompiler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("force-disabled runtime must not create MemoryCompiler")

    monkeypatch.setattr("dan.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("dan.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_force_disabled=True,
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.config.memory.enabled is True
        assert daemon_app.config.memory.compiled_context_enabled is True
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is False
        assert daemon_app.context_builder._memory_compiler is None

        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-force-disabled",
            title="Runtime force-disabled title",
            claim="Runtime force-disabled claim must not render.",
        )
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime force-disabled check",
            compiled_memory_enabled_override=True,
        )

        rendered = json.dumps(
            {
                "request": asdict(result.request),
                "context_snapshot": result.context_snapshot,
            },
            sort_keys=True,
        )
        assert [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ] == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": False,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
        assert "Runtime force-disabled title" not in rendered
        assert "Runtime force-disabled claim" not in rendered
        assert "compiled_memory_force_disabled" not in rendered
        assert "compiled_memory_diagnostics" not in rendered
    finally:
        daemon_app.close()


def test_create_daemon_app_scoped_enablement_uses_config_gate_without_global_leak(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "dan"),
        ),
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is True
        assert isinstance(daemon_app.context_builder._memory_compiler, MemoryCompiler)

        insert_runtime_conversation(daemon_app)
        insert_runtime_conversation(daemon_app, conversation_id="conversation-other")
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-scoped",
            title="Runtime scoped title",
            claim="Runtime scoped claim.",
        )

        matched = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime scoped check",
        )
        unrelated = daemon_app.context_builder.build_request(
            turn_id="turn-other",
            conversation_id="conversation-other",
            input_text="runtime unrelated check",
        )

        matched_messages = [
            message
            for message in matched.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ]
        unrelated_messages = [
            message
            for message in unrelated.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ]
        assert len(matched_messages) == 1
        assert "Runtime scoped title" in matched_messages[0].content
        assert unrelated_messages == []
        assert asdict(matched.compiled_memory_diagnostics)[
            "compiled_memory_enabled"
        ] is True
        assert asdict(unrelated.compiled_memory_diagnostics)[
            "compiled_memory_enabled"
        ] is False
    finally:
        daemon_app.close()


def test_create_daemon_app_empty_scoped_allow_list_does_not_enable_globally(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(),
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is True

        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-empty-scope",
            title="Runtime empty scope title",
            claim="Runtime empty scope claim must not render.",
        )

        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime empty scoped allow-list check",
        )

        messages = [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ]
        assert messages == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": True,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_create_daemon_app_scoped_enablement_requires_config_gate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        compiled_context_enabled=False,
    )
    compiler = RuntimeSpyCompiler()

    daemon_app = create_daemon_app(
        config_path,
        memory_compiler=compiler,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "dan"),
        ),
    )
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._compiled_memory_scope_gate_enabled is False

        insert_runtime_conversation(daemon_app)
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime scoped gate disabled check",
        )

        assert compiler.calls == 0
        assert [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ] == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": True,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_memory_disabled_overrides_compiled_context_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingMemoryCompiler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("memory.enabled=false must not create MemoryCompiler")

    monkeypatch.setattr("dan.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("dan.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(
        tmp_path / "dan.toml",
        db_path,
        memory_enabled=False,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(config_path)
    try:
        assert daemon_app.context_builder is not None
        assert daemon_app.config.memory.enabled is False
        assert daemon_app.config.memory.compiled_context_enabled is True
        assert daemon_app.context_builder._compiled_memory_enabled is False
        assert daemon_app.context_builder._memory_compiler is None

        insert_runtime_conversation(daemon_app)
        insert_runtime_memory_item(
            daemon_app,
            memory_id="mem-runtime-disabled",
            title="Disabled memory title",
            claim="Disabled memory claim must not render.",
        )
        result = daemon_app.context_builder.build_request(
            turn_id="turn-runtime",
            conversation_id="conversation-runtime",
            input_text="runtime memory disabled check",
        )

        assert [
            message
            for message in result.request.context_messages
            if message.metadata.get("kind") == "compiled_memory"
        ] == []
        assert asdict(result.compiled_memory_diagnostics) == {
            "compiled_memory_enabled": False,
            "compiler_available": False,
            "compiled_memory_attempted": False,
            "compiled_memory_section_present": False,
            "selected_count": 0,
            "skipped_count": 0,
            "fail_closed": False,
            "failure_category": None,
            "skipped_categories": {},
        }
    finally:
        daemon_app.close()


def test_create_daemon_app_initialize_false_does_not_create_db(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config = write_config(tmp_path / "dan.toml", db_path)

    daemon_app = create_daemon_app(config, initialize=False)
    try:
        assert daemon_app.conn is None
        assert daemon_app.event_store is None
        assert not db_path.exists()
        assert not db_path.parent.exists()
    finally:
        daemon_app.close()


def test_app_start_appends_daemon_started(app: DaemonApp) -> None:
    app.start()

    assert "daemon.started" in event_types(app)
    assert app.started is True


def test_app_start_transitions_booting_to_idle_and_appends_state_changed(
    app: DaemonApp,
) -> None:
    app.start()

    assert app.state_machine is not None
    assert app.state_machine.state is RuntimeState.IDLE
    assert event_types(app) == ["daemon.started", "state.changed"]


def test_app_start_is_idempotent(app: DaemonApp) -> None:
    app.start()
    app.start()

    assert event_types(app) == ["daemon.started", "state.changed"]


def test_snapshot_state_returns_required_keys(app: DaemonApp) -> None:
    app.start()
    expected = {
        "service",
        "ok",
        "started",
        "state",
        "schema_version",
        "latest_event_id",
        "host",
        "port",
        "voice_enabled",
        "brain_adapter",
        "launchd_label",
        "session_tokens_in",
        "session_tokens_out",
    }

    snapshot = app.snapshot_state()

    assert set(snapshot) == expected
    assert snapshot["service"] == "dand"
    assert snapshot["ok"] is True
    assert snapshot["started"] is True
    assert snapshot["state"] == "IDLE"
    assert snapshot["latest_event_id"] == 2
    assert snapshot["session_tokens_in"] == 0
    assert snapshot["session_tokens_out"] == 0


def test_app_stop_transitions_to_stopping_and_appends_daemon_stopped(app: DaemonApp) -> None:
    app.start()

    app.stop(reason="test shutdown")

    assert app.state_machine is not None
    assert app.state_machine.state is RuntimeState.STOPPING
    assert app.started is False
    assert event_types(app) == [
        "daemon.started",
        "state.changed",
        "daemon.stopped",
        "state.changed",
    ]


def test_app_stop_closes_persistent_brain_manager(
    app: DaemonApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert app.brain_manager is not None
    closed: list[bool] = []
    monkeypatch.setattr(app.brain_manager, "close", lambda: closed.append(True))
    app.start()

    app.stop(reason="test")

    assert closed == [True]


def test_daemon_wires_persistent_brain_state_under_runtime_dir(
    config_path: Path,
) -> None:
    production_app = create_daemon_app(config_path)
    assert production_app.brain_manager is not None
    adapter = production_app.brain_manager.get_adapter("claude_cli")

    try:
        assert adapter.state_path == production_app.paths.runtime_dir / "claude-session.json"
    finally:
        production_app.close()


def test_runtime_settings_projects_only_safe_persistent_brain_session_fields(
    app: DaemonApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert app.brain_manager is not None
    monkeypatch.setattr(
        app.brain_manager,
        "session_snapshot",
        lambda: {
            "session_id": "session-safe-id",
            "generation": 7,
            "context_percent": 42.5,
            "last_action": "resumed",
            "healthy": True,
            "checkpoint_prompt": "must never reach runtime settings",
        },
    )

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    assert payload["brain"]["session"]["effective_value"] == {
        "session_id": "session-safe-id",
        "generation": 7,
        "context_percent": 42.5,
        "last_action": "resumed",
        "healthy": True,
    }


def test_get_health_returns_200_json_and_expected_fields(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/health")

    assert status == 200
    assert payload["ok"] is True
    assert payload["service"] == "dand"
    assert payload["state"] == "IDLE"
    assert payload["started"] is True
    assert payload["schema_version"] == LATEST_SCHEMA_VERSION


def test_get_state_returns_current_state_and_allowed_targets(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/state")

    assert status == 200
    assert payload["state"] == "IDLE"
    assert set(payload["allowed_state_targets"]) == {"LISTENING", "THINKING", "ERROR", "STOPPING"}


def test_get_voice_queue_returns_bounded_redacted_status(app: DaemonApp) -> None:
    from dan.store.event_store import create_event_store
    from dan.voice.queue import VoiceQueue

    app.start()
    queue = VoiceQueue(app.conn, event_store=create_event_store(app.conn))
    request = queue.enqueue(
        text="Playback status for sk-live-secret-token-that-must-not-leak",
        turn_id="turn-voice-status",
        kind="sentence",
        seq=0,
    )
    queue.claim_next()
    queue.mark_failed(request.id, error="playback failed: mock stopped")

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/voice/queue?limit=5")

    encoded = json.dumps(payload)
    assert status == 200
    assert payload["limit"] == 5
    assert len(payload["voice_queue"]) == 1
    row = payload["voice_queue"][0]
    assert row["id"] == request.id
    assert row["turn_id"] == "turn-voice-status"
    assert row["status"] == "failed"
    assert row["kind"] == "sentence"
    assert row["seq"] == 0
    assert row["text_length"] > 0
    assert "text_preview" in row
    assert "playback failed" in row["error"]
    assert "sk-live-secret" not in encoded


def test_get_events_returns_ascending_events_after_after_id(app: DaemonApp) -> None:
    app.start()
    app.stop(reason="done")

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/events?after_id=1&limit=10")

    assert status == 200
    ids = [event["id"] for event in payload["events"]]
    assert ids == sorted(ids)
    assert ids == [2, 3, 4]
    assert payload["after_id"] == 1
    assert payload["limit"] == 10
    assert payload["latest_event_id"] == 4


def test_get_events_omits_tool_finished_output_for_clients(app: DaemonApp) -> None:
    app.start()
    assert app.conn is not None
    assert app.event_store is not None
    recorder = ToolRunRecorder(app.conn, event_store=app.event_store)
    recorder.record_requested(
        run_id="run-rest-output",
        tool_name="ui_read_window",
        risk="safe_read",
        input={"window": "main"},
        turn_id="turn-rest-output",
    )
    raw_secret = "sk-" + "rest-events-secret-token"
    recorder.record_finished(
        "run-rest-output",
        output={
            "stdout": f"raw tool output {raw_secret}",
            "headers": {"Authorization": f"Bearer {raw_secret}"},
        },
    )

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/events?latest=true&limit=10")

    encoded = json.dumps(payload, sort_keys=True)
    events = [
        event
        for event in payload["events"]
        if event["type"] == "tool.finished" and event["payload"].get("run_id") == "run-rest-output"
    ]
    assert status == 200
    assert len(events) == 1
    event_payload = events[0]["payload"]
    assert event_payload["tool_name"] == "ui_read_window"
    assert event_payload["status"] == "finished"
    assert event_payload["output_omitted"] is True
    assert event_payload["output"] == REDACTION_PLACEHOLDER
    assert raw_secret not in encoded
    assert '"arguments"' not in encoded
    assert '"headers"' not in encoded
    assert "raw tool output" not in encoded
    assert "Authorization" not in encoded


@pytest.mark.parametrize("query", ["after_id=bad", "limit=bad", "limit=0", "limit=1001"])
def test_get_events_rejects_invalid_query_values_with_json_400(
    app: DaemonApp,
    query: str,
) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/events?{query}")

    assert status == 400
    assert payload["status"] == 400
    assert "error" in payload


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
def test_unsupported_methods_return_json_errors_not_html(
    app: DaemonApp,
    method: str,
) -> None:
    with running_server(app) as base_url:
        status, content_type, body = request_raw(method, f"{base_url}/state")

    assert status in {405, 501}
    assert "application/json" in content_type
    payload = json.loads(body)
    assert payload["status"] == status
    assert "error" in payload
    assert "<html" not in body.lower()
    assert "<!doctype" not in body.lower()


def test_get_settings_returns_registered_installation_settings(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/settings")

    assert status == 200
    assert payload["settings"]["voice.output_gain"] == 1.0
    assert payload["settings"]["voice.ptt_hotkey"] == app.config.voice.ptt_hotkey
    assert "ui.theme" not in payload["settings"]


def test_post_settings_rejects_unregistered_key_without_write(app: DaemonApp) -> None:
    before = app.get_settings()
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"key": "ui.theme", "value": {"mode": "dark"}},
        )

    assert status == 400
    assert "unknown configuration key" in payload["error"]
    assert app.get_settings() == before


def test_post_settings_rejects_mixed_batch_before_file_or_database_write(
    app: DaemonApp,
) -> None:
    before_settings = app.get_settings()
    before_config = app.config.source_path.read_bytes()
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"settings": {"voice.output_gain": 0.92, "ui.theme": "dark"}},
        )

    assert status == 400
    assert "ui.theme" in payload["error"]
    assert app.get_settings() == before_settings
    assert app.config.source_path.read_bytes() == before_config


def test_post_settings_rejects_malformed_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, content_type, body = request_raw("POST", f"{base_url}/settings", b"{not-json")

    assert status == 400
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    payload = json.loads(body)
    assert payload["status"] == 400
    assert "JSON" in payload["error"]


def test_post_settings_rejects_oversized_json_with_json_400(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, content_type, body = request_declared_json_length(
            "POST",
            f"{base_url}/settings",
            MAX_REQUEST_BODY_BYTES + 1,
        )

    assert status == 400
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    payload = json.loads(body)
    assert payload["status"] == 400
    assert "too large" in payload["error"]


def test_post_settings_rejects_non_object_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/settings", ["not", "object"])

    assert status == 400
    assert payload["status"] == 400


def test_post_input_text_returns_200_and_creates_turn(
    app: DaemonApp,
) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/input/text", {"text": "hello"})

    assert status == 200
    assert payload["ok"] is True
    assert payload["final_text"] == "Test response: hello"
    assert payload["brain_adapter"] == "test"
    assert payload["brain_model"] == "test-model"
    turn_count = app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert turn_count == 1
    assert "brain.responded" in event_types(app)


def test_api_fixture_never_calls_production_claude_adapter(
    app: DaemonApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_calls: list[str] = []

    def forbidden_generate(self: ClaudeCliAdapter, request: BrainRequest, **kwargs: Any):
        del self, request, kwargs
        production_calls.append("called")
        raise AssertionError("API smoke fixture invoked production Claude")

    monkeypatch.setattr(ClaudeCliAdapter, "generate", forbidden_generate)
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "hermetic"},
        )

    assert status == 200
    assert payload["final_text"] == "Test response: hermetic"
    assert production_calls == []


def test_get_input_text_returns_json_method_error(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/input/text")

    assert status in {405, 501}
    assert payload["status"] == status


def test_get_tools_requires_started_app(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/tools")

    assert status == 503
    assert payload["status"] == 503


def test_get_tools_returns_default_tools(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/tools")

    assert status == 200
    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert tools["echo"]["risk"] == "safe_read"
    assert tools["system_status"]["risk"] == "safe_status"
    assert tools["approval_probe"]["risk"] == "shell_read"
    assert "Approval-required demo tool" in tools["approval_probe"]["description"]
    assert tools["ui_active_app"]["risk"] == "ui_read"
    assert tools["ui_read_window"]["risk"] == "ui_read"
    assert tools["ui_click"]["risk"] == "ui_act"
    assert tools["ui_type"]["risk"] == "ui_act"
    assert tools["ui_focus_app"]["risk"] == "ui_act"
    assert tools["screen_read_window"]["risk"] == "screen_read"
    assert tools["screen_ocr_region"]["risk"] == "screen_read"
    assert tools["terminal_read_screen"]["risk"] == "terminal_read"
    assert tools["terminal_paste"]["risk"] == "terminal_write"


def test_post_tools_request_echo_executes_and_records_run(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "echo", "arguments": {"text": "hello"}, "requested_by": "api"},
        )

    assert status == 200
    assert payload["status"] == "finished"
    assert payload["output"] == {"arguments": {"text": "hello"}}
    assert table_count(app, "tool_runs") == 1
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0
    assert "tool.requested" in event_types(app)
    assert "tool.finished" in event_types(app)


def test_post_tools_request_unknown_tool_returns_404_json(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "missing", "arguments": {}, "requested_by": "api"},
        )

    assert status == 404
    assert payload["status"] == 404
    assert table_count(app, "tool_runs") == 0


def test_post_tools_request_approval_required_creates_approval_without_execution(
    app: DaemonApp,
) -> None:
    fake = ApiFakeTool(name="needs_approval", risk="shell_read")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/tools/request",
            {
                "tool_name": "needs_approval",
                "arguments": {"command": "status"},
                "requested_by": "api",
            },
        )

    assert status == 200
    assert payload["status"] == "approval_required"
    assert isinstance(payload["approval_id"], str)
    assert fake.calls == []
    assert table_count(app, "approvals") == 1
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_post_tools_request_default_approval_probe_creates_approval_without_replay(
    app: DaemonApp,
) -> None:
    app.start()

    with running_server(app) as base_url:
        request_status, requested = request_json(
            "POST",
            f"{base_url}/tools/request",
            {
                "tool_name": "approval_probe",
                "arguments": {"purpose": "smoke"},
                "requested_by": "api",
            },
        )
        approve_status, approved = request_json(
            "POST",
            f"{base_url}/approvals/{requested['approval_id']}/approve",
            {"reason": "manual smoke approval endpoint check"},
        )

    assert request_status == 200
    assert requested["status"] == "approval_required"
    assert isinstance(requested["approval_id"], str)
    assert requested["output"] is None
    assert approve_status == 200
    assert approved["approval"]["status"] == "approved"
    assert table_count(app, "approvals") == 1
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_post_approval_execute_requires_started_app(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/approvals/missing/execute")

    assert status == 503
    assert payload["status"] == 503


def test_post_approval_execute_runs_approved_tool_once_and_records_events(
    app: DaemonApp,
) -> None:
    fake = ApiFakeTool(name="execute_approved", risk="shell_read")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        request_status, requested = request_json(
            "POST",
            f"{base_url}/tools/request",
            {
                "tool_name": "execute_approved",
                "arguments": {"command": "status"},
                "requested_by": "api",
                "turn_id": "turn-execute",
            },
        )
        approve_status, approved = request_json(
            "POST",
            f"{base_url}/approvals/{requested['approval_id']}/approve",
            {"reason": "ok"},
        )
        execute_status, executed = request_json(
            "POST",
            f"{base_url}/approvals/{requested['approval_id']}/execute",
        )

    assert request_status == 200
    assert approve_status == 200
    assert approved["approval"]["status"] == "approved"
    assert execute_status == 200
    assert executed["ok"] is True
    assert executed["approval_id"] == requested["approval_id"]
    assert executed["result"] == {"received": {"command": "status"}}
    assert executed["tool_run"]["approval_id"] == requested["approval_id"]
    assert executed["tool_run"]["status"] == "finished"
    assert executed["tool_run"]["turn_id"] == "turn-execute"
    assert fake.calls == [{"command": "status"}]
    assert tool_run_count_for_approval(app, requested["approval_id"]) == 1
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0
    assert "tool.started" in event_types(app)
    assert "tool.finished" in event_types(app)


def test_post_approval_execute_approval_probe_returns_harmless_result(app: DaemonApp) -> None:
    app.start()

    with running_server(app) as base_url:
        _, requested = request_json(
            "POST",
            f"{base_url}/tools/request",
            {
                "tool_name": "approval_probe",
                "arguments": {"purpose": "smoke"},
                "requested_by": "api",
            },
        )
        request_json("POST", f"{base_url}/approvals/{requested['approval_id']}/approve")
        status, payload = request_json("POST", f"{base_url}/approvals/{requested['approval_id']}/execute")

    assert status == 200
    assert payload["ok"] is True
    assert payload["result"] == {
        "ok": True,
        "message": "approval_probe executed safely",
    }
    assert tool_run_count_for_approval(app, requested["approval_id"]) == 1
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_post_approval_execute_pending_rejected_and_missing_approvals_do_not_execute(
    app: DaemonApp,
) -> None:
    fake = ApiFakeTool(name="execute_guarded", risk="shell_read")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        _, pending = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "execute_guarded", "arguments": {"n": 1}, "requested_by": "api"},
        )
        _, rejectable = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "execute_guarded", "arguments": {"n": 2}, "requested_by": "api"},
        )
        request_json("POST", f"{base_url}/approvals/{rejectable['approval_id']}/reject")
        pending_status, pending_payload = request_json(
            "POST",
            f"{base_url}/approvals/{pending['approval_id']}/execute",
        )
        rejected_status, rejected_payload = request_json(
            "POST",
            f"{base_url}/approvals/{rejectable['approval_id']}/execute",
        )
        missing_status, missing_payload = request_json("POST", f"{base_url}/approvals/missing/execute")

    assert pending_status == 409
    assert "not approved" in pending_payload["error"]
    assert rejected_status == 409
    assert "not approved" in rejected_payload["error"]
    assert missing_status == 404
    assert missing_payload["status"] == 404
    assert fake.calls == []
    assert table_count(app, "tool_runs") == 0


def test_post_approval_execute_unknown_tool_payload_does_not_record_run(app: DaemonApp) -> None:
    app.start()
    assert app.approval_gate is not None
    approval = app.approval_gate.create_approval(
        risk="shell_read",
        requested_by="api",
        action_type="tool:missing",
        payload={
            "tool_name": "missing",
            "arguments": {},
            "requested_by": "api",
            "source": str(RequestSource.DIRECT_USER_COMMAND),
        },
    )
    app.approve(str(approval["id"]))

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/approvals/{approval['id']}/execute")

    assert status == 404
    assert payload["status"] == 404
    assert table_count(app, "tool_runs") == 0


def test_post_approval_execute_duplicate_returns_409_without_second_run(
    app: DaemonApp,
) -> None:
    fake = ApiFakeTool(name="execute_once", risk="shell_read")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        _, requested = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "execute_once", "arguments": {"n": 1}, "requested_by": "api"},
        )
        request_json("POST", f"{base_url}/approvals/{requested['approval_id']}/approve")
        first_status, first = request_json("POST", f"{base_url}/approvals/{requested['approval_id']}/execute")
        second_status, second = request_json("POST", f"{base_url}/approvals/{requested['approval_id']}/execute")

    assert first_status == 200
    assert first["ok"] is True
    assert second_status == 409
    assert "already executed" in second["error"]
    assert fake.calls == [{"n": 1}]
    assert tool_run_count_for_approval(app, requested["approval_id"]) == 1


def test_post_approval_execute_blocks_destructive_when_disabled(app: DaemonApp) -> None:
    fake = ApiFakeTool(name="destructive_execute", risk="destructive")
    app.tool_registry.register(fake)
    app.start()
    assert app.approval_gate is not None
    approval = app.approval_gate.create_approval(
        risk="destructive",
        requested_by="api",
        action_type="tool:destructive_execute",
        payload={
            "tool_name": "destructive_execute",
            "arguments": {},
            "requested_by": "api",
            "source": str(RequestSource.DIRECT_USER_COMMAND),
        },
    )
    app.approve(str(approval["id"]))

    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/approvals/{approval['id']}/execute")

    assert status == 200
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "destructive tools are disabled" in payload["error"]
    assert fake.calls == []
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_post_tools_request_blocked_tool_does_not_execute(app: DaemonApp) -> None:
    fake = ApiFakeTool(name="blocked_api", risk="destructive")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "blocked_api", "arguments": {}, "requested_by": "api"},
        )

    assert status == 200
    assert payload["status"] == "blocked"
    assert fake.calls == []
    assert table_count(app, "approvals") == 0
    assert table_count(app, "tool_runs") == 0
    assert table_count(app, "voice_queue") == 0
    assert table_count(app, "worker_jobs") == 0


def test_get_approvals_lists_pending(app: DaemonApp) -> None:
    fake = ApiFakeTool(name="approval_listed", risk="file_write")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        request_json(
            "POST",
            f"{base_url}/tools/request",
            {
                "tool_name": "approval_listed",
                "arguments": {"path": "x"},
                "requested_by": "api",
            },
        )
        status, payload = request_json("GET", f"{base_url}/approvals")

    assert status == 200
    assert len(payload["approvals"]) == 1
    assert payload["approvals"][0]["status"] == "pending"
    assert payload["approvals"][0]["risk"] == "file_write"


def test_approve_and_reject_endpoints_update_pending_approval_status(
    app: DaemonApp,
) -> None:
    fake = ApiFakeTool(name="approval_decision", risk="network")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        first_status, first = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "approval_decision", "arguments": {}, "requested_by": "api"},
        )
        second_status, second = request_json(
            "POST",
            f"{base_url}/tools/request",
            {"tool_name": "approval_decision", "arguments": {}, "requested_by": "api"},
        )
        approve_status, approved = request_json(
            "POST",
            f"{base_url}/approvals/{first['approval_id']}/approve",
            {"reason": "ok"},
        )
        reject_status, rejected = request_json(
            "POST",
            f"{base_url}/approvals/{second['approval_id']}/reject",
            {"reason": "no"},
        )

    assert first_status == 200
    assert second_status == 200
    assert approve_status == 200
    assert reject_status == 200
    assert approved["approval"]["status"] == "approved"
    assert rejected["approval"]["status"] == "rejected"
    assert fake.calls == []


def test_approval_endpoints_require_started_app(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/approvals")

    assert status == 503
    assert payload["status"] == 503


def test_unknown_route_returns_404_json(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, content_type, body = request_raw("GET", f"{base_url}/missing")

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    payload = json.loads(body)
    assert payload == {"error": "Not found", "status": 404}


def test_get_runtime_processes_returns_report_only_observations(
    app: DaemonApp,
    tmp_path: Path,
) -> None:
    app.runtime_supervisor = RuntimeSupervisor(
        home=tmp_path / "home",
        temp_dir=tmp_path / "temp",
        process_provider=lambda: [
            {"pid": 321, "process_name": "python", "command": "python voice_broker.py"}
        ],
        now=lambda: "2026-07-01T12:00:00+00:00",
    )
    before_events = event_types(app)

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/processes")

    assert status == 200
    assert payload["report_only"] is True
    assert payload["cleanup_automated"] is False
    assert payload["conflict_count"] == 1
    assert payload["observations"][0]["label"] == "legacy_voice_broker"
    assert payload["conflicts"][0]["risk"] == "high"
    assert event_types(app) == before_events


def test_get_runtime_startup_returns_official_label_and_snapshot(
    app: DaemonApp,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.dan.dand.plist").write_text("placeholder", encoding="utf-8")
    app.runtime_supervisor = RuntimeSupervisor(
        home=home,
        temp_dir=tmp_path / "temp",
        process_provider=lambda: [],
        now=lambda: "2026-07-01T12:00:00+00:00",
    )
    before_events = event_types(app)

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/startup")

    assert status == 200
    assert payload["report_only"] is True
    assert payload["official_label"] == "com.dan.dand"
    assert payload["startup"]["official_label"] == "com.dan.dand"
    assert payload["startup"]["official_plist_installed"] is True
    assert payload["startup"]["official_plist_loaded"] == "not_checked"
    assert event_types(app) == before_events


def test_get_runtime_legacy_returns_guidance_and_no_cleanup(
    app: DaemonApp,
    tmp_path: Path,
) -> None:
    app.runtime_supervisor = RuntimeSupervisor(
        home=tmp_path / "home",
        temp_dir=tmp_path / "temp",
        process_provider=lambda: [
            {"pid": 333, "process_name": "python", "command": "python listen_ozzy.py loop"}
        ],
        now=lambda: "2026-07-01T12:00:00+00:00",
    )
    before_events = event_types(app)

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/legacy")

    assert status == 200
    assert payload["legacy_conflict_count"] == 1
    assert payload["legacy_conflicts"][0]["label"] == "legacy_listener"
    guidance = " ".join(payload["guidance"])
    assert "detected only" in guidance
    assert "no cleanup performed" in guidance
    assert "explicit human approval" in guidance
    assert event_types(app) == before_events


def test_unknown_runtime_route_returns_json_404(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, content_type, body = request_raw("GET", f"{base_url}/runtime/missing")

    assert status == 404
    assert "application/json" in content_type
    assert "<html" not in body.lower()
    payload = json.loads(body)
    assert payload == {"error": "Not found", "status": 404}


def _runtime_warning_messages(payload: dict[str, Any]) -> list[str]:
    values = payload.get("voice_errors", {}).get("warnings", {}).get("value")
    if not isinstance(values, list):
        return []
    return [str(item) for item in values]


def _settings_preview_field(payload: dict[str, Any], section: str, field: str) -> dict[str, Any]:
    settings_preview = payload["settings_preview"]
    sections = settings_preview["sections"]
    return sections[section]["fields"][field]


def _brain_capability_provider(payload: dict[str, Any], provider_id: str) -> dict[str, Any]:
    return next(
        provider
        for provider in payload["capability_graph"]["brain_capabilities"]["providers"]
        if provider["id"] == provider_id
    )


def test_get_runtime_settings_returns_typed_projection_groups_and_fields(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    projection_groups = (
        "runtime",
        "brain",
        "voice",
        "audio",
        "tools",
        "memory",
        "panel",
        "runtime_readiness",
        "current_turn_state",
        "latest_turn_trace",
    )
    for group in projection_groups:
        assert group in payload
        assert isinstance(payload[group], dict)

    for group_name in projection_groups:
        group = payload[group_name]
        for field in group.values():
            assert set(field.keys()) == {
                "value",
                "effective_value",
                "source",
                "status",
                "editable_later",
                "warning",
            }
            assert field["status"] in {"ok", "missing", "invalid", "unsupported", "unknown"}
            assert field["source"] in {
                "config",
                "settings",
                "default",
                "runtime_detected",
                "unknown",
            }

    assert payload["runtime"]["host"]["value"] == "127.0.0.1"
    assert payload["runtime"]["host"]["source"] == "config"
    assert payload["memory"]["enabled"]["source"] == "config"
    readiness = payload["runtime_readiness"]
    for field in (
        "daemon_config",
        "database_path",
        "panel_backend_connected",
        "brain_provider_command",
        "tts_provider",
        "stt_provider",
        "recorder_command",
        "playback_command",
        "network_tools_capability",
        "summary",
        "top_blockers",
        "warnings",
    ):
        assert field in readiness
    assert readiness["panel_backend_connected"]["value"] == "yes"
    assert readiness["daemon_config"]["status"] in {"ok", "missing", "invalid", "unknown"}
    assert readiness["database_path"]["status"] in {"ok", "missing", "invalid", "unknown"}


def test_get_runtime_settings_includes_settings_preview_payload_and_capability_graph(
    app: DaemonApp,
) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    settings_preview = payload["settings_preview"]
    assert settings_preview["preview_only"] is True
    assert settings_preview["save_implemented"] is False
    assert settings_preview["save_disabled_reason"] == "Save is handled by targeted runtime apply controls."

    expected_sections = {
        "brain_provider": (
            "provider",
            "provider_id",
            "transport",
            "model",
            "selected_model",
            "effective_model",
            "model_source",
            "effort",
            "selected_effort",
            "effective_effort",
            "effort_source",
            "fast",
            "context_budget",
            "provider_sessions_are_memory",
            "tools_support",
            "streaming_support",
            "auth_status",
            "version",
            "permission_mode",
            "tools",
            "allowed_tools",
            "disallowed_tools",
            "mcp_config_status",
            "strict_mcp_config",
            "output_format",
            "input_format",
            "partial_messages_supported",
            "hook_events_supported",
            "apply_semantics",
            "command_preview",
            "command_status",
            "credentials_or_command_status",
            "latest_provider_error",
        ),
        "voice_tts": (
            "tts_provider",
            "tts_model",
            "voice_id",
            "voice_profile",
            "speed_or_rate",
            "style",
            "stability",
            "similarity",
            "streaming_support",
            "continuity_support",
            "latest_tts_error",
        ),
        "voice_stt": (
            "stt_provider",
            "stt_model",
            "language",
            "transcription_ready",
            "endpointing_support",
            "latest_stt_error",
        ),
        "endpointing_ptt": (
            "ptt_mode",
            "ptt_hotkey",
            "merge_window",
            "silence_threshold",
            "silence_duration",
            "interrupt_policy",
            "listening_lease_state",
        ),
        "queue_barge_in": (
            "queue_status",
            "cancel_support",
            "active_speech_id",
            "current_spoken_kind",
            "interrupted_previous_response",
            "last_cancellation_reason",
            "manual_cancel_available",
        ),
        "tools_internet": (
            "tools_enabled",
            "tools_support",
            "internet_capability",
            "latest_tool_error",
        ),
        "personality": (
            "active_persona",
            "active_style",
            "personality_source",
            "editable_later",
        ),
    }
    assert set(settings_preview["sections"]) == set(expected_sections)
    required_field_keys = {
        "id",
        "label",
        "current",
        "effective",
        "status",
        "source",
        "allowed_values",
        "disabled_values",
        "warning",
        "blocker",
        "dependencies",
        "invalidates",
        "requires_restart",
        "requires_reload",
        "editable_now",
        "editable_later",
        "developer_only",
        "apply_capable",
        "apply_disabled_reason",
        "validation",
    }
    for section_id, field_ids in expected_sections.items():
        section = settings_preview["sections"][section_id]
        assert set(section["fields"]) == set(field_ids)
        for field_id in field_ids:
            field = section["fields"][field_id]
            assert set(field) == required_field_keys
            assert field["id"] == f"{section_id}.{field_id}"
            assert field["status"] in {"ok", "missing", "invalid", "unsupported", "unknown"}

    graph = payload["capability_graph"]
    assert set(graph) == {"brain_capabilities", "voice_capabilities", "tools_capabilities", "local_capabilities"}
    brain_capabilities = graph["brain_capabilities"]
    assert isinstance(brain_capabilities["providers"], list)
    assert "mock" not in {provider["id"] for provider in brain_capabilities["providers"]}
    assert brain_capabilities["current_provider"]
    voice_capabilities = graph["voice_capabilities"]
    assert "tts_providers" in voice_capabilities
    assert "stt_providers" in voice_capabilities
    local_capabilities = graph["local_capabilities"]
    assert {"ollama", "mlx", "llama_cpp_metal", "bielik", "mistral"}.issubset(
        {runtime["id"] for runtime in local_capabilities["runtimes"]}
    )
    credentials_or_command_status = _settings_preview_field(
        payload,
        "brain_provider",
        "credentials_or_command_status",
    )
    expected_status_values = {"ok", "missing", "invalid", "unknown", "unavailable"}
    assert credentials_or_command_status["current"] in expected_status_values
    assert credentials_or_command_status["effective"] in expected_status_values
    assert credentials_or_command_status["status"] in {"ok", "missing", "invalid", "unsupported", "unknown"}


def test_runtime_settings_structured_warnings_cover_invalid_preview_fixtures(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\nspeak_responses = true\nbroker_enabled = false\ndefault_tts = ''\ndefault_stt = ''\n",
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    warnings = payload["compatibility_warnings"]
    warning_ids = {warning["id"] for warning in warnings}
    # The test config triggers these specific warnings
    assert {"brain_model_missing", "voice_enabled_tts_missing", "voice_enabled_stt_missing"}.issubset(
        warning_ids
    )
    for warning in warnings:
        assert set(warning) == {
            "id",
            "severity",
            "group",
            "field_ids",
            "message",
            "reason",
            "suggested_action",
        }
        assert warning["severity"] in {"info", "warning", "invalid", "blocker"}
        assert isinstance(warning["field_ids"], list)

    tts_provider = _settings_preview_field(payload, "voice_tts", "tts_provider")
    stt_provider = _settings_preview_field(payload, "voice_stt", "stt_provider")
    assert tts_provider["status"] == "missing"
    assert tts_provider["blocker"]
    assert stt_provider["status"] == "missing"
    assert stt_provider["blocker"]


def test_runtime_settings_preview_blocks_tts_voice_id_required_for_supertonic(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\nspeak_responses = true\ndefault_tts = 'supertonic'\ndefault_stt = 'mock'\nsupertonic_voice = ''\nsupertonic_lang = 'pl'\n",
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    voice_id = _settings_preview_field(payload, "voice_tts", "voice_id")
    warning_ids = {warning["id"] for warning in payload["compatibility_warnings"]}
    assert voice_id["status"] == "missing"
    assert "requires voice_id" in voice_id["blocker"]
    assert "tts_voice_id_missing" in warning_ids


def test_runtime_settings_preview_redacts_secret_shaped_values(
    tmp_path: Path,
) -> None:
    raw_secret = "sk-settings-preview-secret"
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        extra_toml=f'\n[brain.claude_cli]\nenabled = true\ncommand = "{raw_secret}"\nmodel = "claude-cli"\n',
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    encoded = json.dumps(payload, sort_keys=True)
    assert raw_secret not in encoded
    assert "[REDACTED]" in encoded


def test_runtime_settings_mlx_model_env_without_mlx_runtime_is_not_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dan.api import routes_runtime

    monkeypatch.setenv("DAN_MLX_MODEL", "mlx-community/test-model")

    def fake_executable_probe(path: str | None) -> tuple[str, str | None, bool]:
        if path == "python":
            return "ok", "/usr/bin/python", True
        return "missing", str(path), False

    monkeypatch.setattr(routes_runtime, "_safe_is_executable", fake_executable_probe)
    monkeypatch.setattr(routes_runtime.importlib.util, "find_spec", lambda name: None)
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    mlx = next(
        runtime
        for runtime in payload["capability_graph"]["local_capabilities"]["runtimes"]
        if runtime["id"] == "mlx"
    )
    assert mlx["available"] is False
    assert mlx["status"] == "missing"
    assert "runtime not detected" in (mlx["warning"] or "").lower()
    assert mlx["blocker"]


def test_runtime_settings_base_mlx_without_mlx_lm_is_not_llm_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dan.api import routes_runtime

    monkeypatch.setenv("DAN_MLX_MODEL", "mlx-community/test-model")
    monkeypatch.setattr(
        routes_runtime,
        "_safe_is_executable",
        lambda path: ("missing", str(path), False),
    )
    monkeypatch.setattr(
        routes_runtime.importlib.util,
        "find_spec",
        lambda name: object() if name == "mlx" else None,
    )
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    mlx = next(
        runtime
        for runtime in payload["capability_graph"]["local_capabilities"]["runtimes"]
        if runtime["id"] == "mlx"
    )
    assert mlx["available"] is False
    assert mlx["status"] in {"missing", "invalid", "partial"}
    assert "mlx-lm" in (mlx["warning"] or "").lower()
    assert mlx["blocker"]


def test_runtime_settings_mlx_runtime_can_be_detected_by_safe_import_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dan.api import routes_runtime

    monkeypatch.setenv("DAN_MLX_MODEL", "mlx-community/test-model")
    monkeypatch.setattr(
        routes_runtime,
        "_safe_is_executable",
        lambda path: ("missing", str(path), False),
    )
    monkeypatch.setattr(
        routes_runtime.importlib.util,
        "find_spec",
        lambda name: object() if name in {"mlx", "mlx_lm"} else None,
    )
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    mlx = next(
        runtime
        for runtime in payload["capability_graph"]["local_capabilities"]["runtimes"]
        if runtime["id"] == "mlx"
    )
    assert mlx["available"] is True
    assert mlx["status"] == "ok"
    assert mlx["command"] == "python-module:mlx_lm"


def test_runtime_readiness_warns_for_voice_config_blockers(tmp_path: Path) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\nspeak_responses = true\nbroker_enabled = false\ndefault_tts = ''\ndefault_stt = ''\n",
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    readiness = payload["runtime_readiness"]
    warnings = readiness["warnings"]["value"]
    blockers = readiness["top_blockers"]["value"]
    assert readiness["tts_provider"]["status"] == "missing"
    assert readiness["stt_provider"]["status"] == "missing"
    assert any("voice enabled but broker disabled" in item for item in warnings)
    assert any("speak_responses enabled but TTS missing" in item for item in warnings)
    assert any("TTS provider" in item for item in blockers)


def test_get_runtime_settings_includes_latest_turn_trace_for_last_text_turn(
    app: DaemonApp,
) -> None:
    app.start()

    with running_server(app) as base_url:
        status, input_payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "dan test latest turn", "source": "text"},
        )
        assert status == 200
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    trace = payload["latest_turn_trace"]
    turn_state = payload["current_turn_state"]
    assert trace["turn_id"]["value"] == input_payload["turn_id"]
    assert trace["source"]["value"] in {"text", "panel", "voice"}
    assert trace["conversation_id"]["value"] == input_payload["conversation_id"]
    assert trace["provider_adapter"]["value"] == input_payload["brain_adapter"]
    assert trace["provider_model"]["value"] == input_payload["brain_model"]
    assert trace["approvals_requested_count"]["value"] == 0
    assert trace["approvals_executed_count"]["value"] == 0
    assert trace["tools_attempted_count"]["value"] == 0
    assert trace["voice_rows_created"]["value"] == {"filler": 0, "final": 0, "error": 0}
    timestamps = trace["timestamps"]["value"]
    assert isinstance(timestamps, dict)
    assert "created_at" in timestamps
    assert "completion_at" in timestamps
    assert turn_state["current_turn_id"]["value"] == input_payload["turn_id"]
    assert turn_state["current_conversation_id"]["value"] == input_payload["conversation_id"]
    assert turn_state["current_turn_source"]["value"] == "text"
    assert turn_state["generation_state"]["value"] == "idle"


def test_ptt_down_acquires_lease_without_cancelling_current_speech(tmp_path: Path) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\ndefault_tts = 'mock'\ndefault_stt = 'mock'\n",
    )
    app = create_daemon_app(config_path)
    try:
        status = 0
        runtime_payload: dict[str, Any] = {}
        app.start()
        from dan.store.event_store import create_event_store
        from dan.store.repositories import utc_now_iso
        from dan.voice.queue import VoiceQueue

        now = utc_now_iso()
        turn_id = "turn-ptt-barge-in"
        insert_runtime_conversation(app)
        assert app.conn is not None
        app.conn.execute(
            """
            INSERT INTO turns (
              id, conversation_id, created_at, updated_at, source, status,
              input_text, final_text, brain_adapter, brain_model,
              context_snapshot_json, error, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                "conversation-runtime",
                now,
                now,
                "voice",
                "done",
                "Zapytanie do przechwyconego trybu.",
                "Odpowiedź do przechwyconego trybu.",
                "mock",
                "mock-local",
                "{}",
                None,
                "{}",
            ),
        )

        queue = VoiceQueue(app.conn, event_store=create_event_store(app.conn))
        queued = queue.enqueue(
            text="To zdanie zostanie przerwane przez PTT.",
            turn_id=turn_id,
            seq=0,
        )

        with running_server(app) as base_url:
            status, ptt_payload = request_json(
                "POST",
                f"{base_url}/voice/ptt/down",
                {"source": "ptt"},
            )
            assert status == 200, ptt_payload
            # Contract (80dcbb5 "Stabilize PTT contracts"): ptt/down acquires a
            # hold lease and does NOT cancel current speech as a side effect of
            # the key press. Mic-side barge-in is the gateway's job when the
            # user's transcript actually arrives (gateway.handle_transcript →
            # cancel_active_speech), covered by test_voice_turn_gateway.
            assert ptt_payload["ok"] is True
            assert "cancellation" not in ptt_payload
            assert ptt_payload["lease"]

            # The queued speech must still be alive — pressing PTT did not cancel it.
            row = app.conn.execute(
                "SELECT status FROM voice_queue WHERE id = ?", (queued.id,)
            ).fetchone()
            assert row is not None and row[0] != "cancelled"

            status, runtime_payload = request_json(
                "GET",
                f"{base_url}/runtime/settings",
            )

    finally:
        app.close()

    assert status == 200
    # runtime/settings stays readable after a PTT hold lease was acquired.
    assert "latest_turn_trace" in runtime_payload


def test_get_runtime_settings_does_not_apply_stale_barge_in_to_later_text_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\ndefault_tts = 'mock'\ndefault_stt = 'mock'\n",
    )
    app = create_daemon_app(config_path)
    production_manager = app.brain_manager
    app.brain_manager = BrainManager(
        [HermeticBrainAdapter(default_model="test-model")],
        default_adapter="test",
    )
    if production_manager is not None:
        production_manager.close()
    production_calls: list[str] = []

    def forbidden_generate(self: ClaudeCliAdapter, request: BrainRequest, **kwargs: Any):
        del self, request, kwargs
        production_calls.append("called")
        raise AssertionError("direct API test invoked production Claude")

    monkeypatch.setattr(ClaudeCliAdapter, "generate", forbidden_generate)
    try:
        app.start()
        from dan.store.event_store import create_event_store
        from dan.store.repositories import utc_now_iso
        from dan.voice.queue import VoiceQueue

        now = utc_now_iso()
        interrupted_turn_id = "turn-interrupted-voice"
        insert_runtime_conversation(app)
        assert app.conn is not None
        app.conn.execute(
            """
            INSERT INTO turns (
              id, conversation_id, created_at, updated_at, source, status,
              input_text, final_text, brain_adapter, brain_model,
              context_snapshot_json, error, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interrupted_turn_id,
                "conversation-runtime",
                now,
                now,
                "voice",
                "done",
                "Przerwij poprzednią odpowiedź.",
                "Ta odpowiedź zostanie przerwana.",
                "mock",
                "mock-local",
                "{}",
                None,
                "{}",
            ),
        )

        queue = VoiceQueue(app.conn, event_store=create_event_store(app.conn))
        queued = queue.enqueue(
            text="Stara mowa do anulowania.",
            turn_id=interrupted_turn_id,
            seq=0,
        )

        with running_server(app) as base_url:
            status, first_payload = request_json(
                "POST",
                f"{base_url}/voice/ptt/down",
                {"source": "ptt"},
            )
            assert status == 200, first_payload
            assert app.voice_cancellation is not None
            app.voice_cancellation.cancel_active_speech(
                reason="barge_in",
                source="ptt",
            )
            status, original_runtime = request_json("GET", f"{base_url}/runtime/settings")
            assert status == 200
            request_json("POST", f"{base_url}/voice/ptt/up", {"source": "ptt"})

            status, text_payload = request_json(
                "POST",
                f"{base_url}/input/text",
                {"text": "later unrelated text turn", "source": "text"},
            )
            assert status == 200, text_payload
            status, later_runtime = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert original_runtime["latest_turn_trace"]["turn_id"]["value"] == interrupted_turn_id
    assert original_runtime["latest_turn_trace"]["interrupted_previous_response"]["value"] is True
    assert original_runtime["latest_turn_trace"]["cancelled_speech_id"]["value"] == queued.id
    assert original_runtime["latest_turn_trace"]["interruption_attributed_to_turn_id"]["value"] == interrupted_turn_id

    assert status == 200
    trace = later_runtime["latest_turn_trace"]
    assert trace["turn_id"]["value"] == text_payload["turn_id"]
    assert trace["source"]["value"] == "text"
    assert trace["interrupted_previous_response"]["value"] is False
    assert trace["cancelled_speech_id"]["value"] is None
    assert trace["interruption_reason"]["value"] is None
    assert trace["previous_turn_id"]["value"] is None
    assert trace["interrupted_turn_id"]["value"] is None
    assert trace["interruption_attributed_to_turn_id"]["value"] is None
    assert trace["interruption_source"]["value"] is None

    turn_state = later_runtime["current_turn_state"]
    assert turn_state["current_turn_id"]["value"] == text_payload["turn_id"]
    assert turn_state["current_turn_source"]["value"] == "text"
    assert turn_state["interrupted_previous_response"]["value"] is False
    assert turn_state["cancelled_speech_id"]["value"] is None
    assert turn_state["interruption_reason"]["value"] is None
    assert turn_state["interrupted_turn_id"]["value"] is None
    assert production_calls == []


def test_get_runtime_settings_counts_turn_approvals_and_tool_attempts(
    app: DaemonApp,
) -> None:
    fake = ApiFakeTool(name="needs_approval", risk="shell_read")
    app.tool_registry.register(fake)
    app.start()

    with running_server(app) as base_url:
        status, input_payload = request_json(
            "POST",
            f"{base_url}/input/text",
            {"text": "approval count trace", "source": "text"},
        )
        assert status == 200
        turn_id = input_payload["turn_id"]

        _, requested = request_json(
            "POST",
            f"{base_url}/tools/request",
            {
                "tool_name": "needs_approval",
                "arguments": {"command": "status"},
                "requested_by": "api",
                "turn_id": turn_id,
            },
        )
        request_json(
            "POST",
            f"{base_url}/approvals/{requested['approval_id']}/approve",
        )
        request_json("POST", f"{base_url}/approvals/{requested['approval_id']}/execute")

        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    trace = payload["latest_turn_trace"]
    assert trace["turn_id"]["value"] == turn_id
    assert trace["approvals_requested_count"]["value"] == 1
    assert trace["approvals_executed_count"]["value"] == 1
    assert trace["tools_attempted_count"]["value"] == 1
    assert trace["latest_safe_error"]["value"] is None


def test_runtime_settings_warnings_include_tts_unavailable_when_voice_enabled(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\ndefault_tts = 'does_not_exist'\nsupertonic_binary = '/this/path/does/not/exist.bin'\n",
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    warnings = _runtime_warning_messages(payload)
    assert any("TTS" in message and "unavailable" in message.lower() for message in warnings) or any(
        "not available" in message.lower() for message in warnings
    )


def test_runtime_settings_closed_value_sets_are_backed_by_str_enums() -> None:
    from enum import StrEnum

    from dan.api.routes_runtime import (
        CANONICAL_PTT_MODES,
        KNOWN_PROVIDER_SUPPORT_NO,
        KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        KNOWN_PROVIDER_SUPPORT_YES,
        KNOWN_SOURCES,
        KNOWN_STATUSES,
        SUPERTONIC_BUILTIN_VOICE_IDS,
        ProviderSupportState,
        RuntimeProjectionSource,
        RuntimeProjectionStatus,
        SupertonicVoiceId,
        VoicePttMode,
    )
    from dan.brain.claude_cli_contract import (
        CLAUDE_CLI_EFFORTS,
        CLAUDE_CLI_INPUT_FORMATS,
        CLAUDE_CLI_OUTPUT_FORMATS,
        CLAUDE_CLI_PERMISSION_MODES,
        ClaudeCliEffortLevel,
        ClaudeCliInputFormat,
        ClaudeCliOutputFormat,
        ClaudeCliPermissionMode,
    )

    enum_classes = (
        RuntimeProjectionSource,
        RuntimeProjectionStatus,
        ClaudeCliEffortLevel,
        ClaudeCliPermissionMode,
        ClaudeCliOutputFormat,
        ClaudeCliInputFormat,
        ProviderSupportState,
        VoicePttMode,
        SupertonicVoiceId,
    )
    for enum_class in enum_classes:
        assert issubclass(enum_class, StrEnum)

    assert KNOWN_SOURCES == frozenset(RuntimeProjectionSource)
    assert all(isinstance(source, RuntimeProjectionSource) for source in KNOWN_SOURCES)
    assert KNOWN_STATUSES == frozenset(RuntimeProjectionStatus)
    assert all(isinstance(status, RuntimeProjectionStatus) for status in KNOWN_STATUSES)
    assert CLAUDE_CLI_EFFORTS == tuple(ClaudeCliEffortLevel)
    assert CLAUDE_CLI_PERMISSION_MODES == tuple(ClaudeCliPermissionMode)
    assert CLAUDE_CLI_OUTPUT_FORMATS == tuple(ClaudeCliOutputFormat)
    assert CLAUDE_CLI_INPUT_FORMATS == tuple(ClaudeCliInputFormat)
    assert (
        KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        KNOWN_PROVIDER_SUPPORT_YES,
        KNOWN_PROVIDER_SUPPORT_NO,
    ) == tuple(ProviderSupportState)
    assert CANONICAL_PTT_MODES == tuple(VoicePttMode)
    assert SUPERTONIC_BUILTIN_VOICE_IDS == tuple(SupertonicVoiceId)
    assert [voice.value for voice in SupertonicVoiceId] == [
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
    ]


def test_runtime_settings_supertonic_readiness_uses_static_builtin_voices_without_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    called = False

    def fail_if_voice_list_is_probed(binary_path: str | None, voice: str | None) -> tuple[str, str | None, str | None]:
        nonlocal called
        called = True
        raise AssertionError(f"unexpected Supertonic voice-list probe for {binary_path} {voice}")

    monkeypatch.setattr(routes_runtime, "_safe_probe_supertonic_voice", fail_if_voice_list_is_probed)
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        (
            "enabled = true\n"
            "default_tts = 'supertonic'\n"
            "default_stt = 'mock'\n"
            "supertonic_binary = '/bin/echo'\n"
            "supertonic_voice = 'M2'\n"
            "supertonic_lang = 'pl'\n"
        ),
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    assert called is False
    voice_ids = _settings_preview_field(payload, "voice_tts", "voice_id")["allowed_values"]
    assert {"F1", "F5", "M1", "M5"}.issubset(set(voice_ids))
    tts_dependencies = payload["voice_tts_voice_model"]["dependency_status"]["value"]
    assert tts_dependencies["supertonic_voice"] == "unknown"
    warnings = _runtime_warning_messages(payload)
    assert any("voice list requires manual diagnostic" in message for message in warnings)


def test_runtime_settings_warnings_include_stt_unavailable_when_voice_enabled(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\ndefault_stt = 'unknown_stt_engine'\n",
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    warnings = _runtime_warning_messages(payload)
    assert any("STT package" in message for message in warnings)


def test_runtime_settings_warnings_include_missing_stt_local_model_for_selected_provider(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        'enabled = true\ndefault_stt = "mlx_whisper"\nstt_model = "/no/such/path/stt-model.ggml"\n',
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    warnings = _runtime_warning_messages(payload)
    assert any("STT model" in message or "local model" in message for message in warnings)


def test_runtime_settings_warns_supertonic_voice_requires_manual_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    called = False

    def fake_run(command, **_: object) -> object:
        nonlocal called
        called = True
        raise AssertionError(f"unexpected subprocess probe: {command}")

    monkeypatch.setattr(routes_runtime.subprocess, "run", fake_run)

    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        (
            "enabled = true\n"
            "default_tts = 'supertonic'\n"
            "default_stt = 'mock'\n"
            "supertonic_binary = '/bin/echo'\n"
            "supertonic_voice = 'M2'\n"
            "supertonic_lang = 'pl'\n"
        ),
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    assert called is False
    warnings = _runtime_warning_messages(payload)
    assert any("voice list requires manual diagnostic" in message for message in warnings)


def test_runtime_settings_warnings_when_tools_are_shown_but_provider_does_not_support_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\n",
    )
    app = create_daemon_app(config_path)
    monkeypatch.setattr(app.tool_registry, "list_specs", lambda: [type("ToolSpec", (), {"name": "demo", "risk": "low", "description": "demo"})])
    try:
        with running_server(app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    warning_ids = {warning["id"] for warning in payload["compatibility_warnings"]}
    assert "tools_enabled_provider_unsupported" in warning_ids


def test_runtime_settings_rejects_runtime_persona_override(
    app: DaemonApp,
) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"settings": {"persona.profile": "does-not-exist"}},
        )
    assert status == 400
    assert "persona.profile" in payload["error"]
    assert "persona.profile" not in app.get_settings()


def test_runtime_settings_warns_stale_brain_model_and_effort_settings(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\n",
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/settings",
                {"settings": {"model": "invalid-model", "effort": "x-large"}},
            )
            assert status == 200
            assert payload["settings"]["model"] == "invalid-model"

            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 200
    warning_ids = {warning["id"] for warning in payload["compatibility_warnings"]}
    assert {
        "brain_model_missing",
        "brain_effort_unsupported",
    }.issubset(warning_ids)


def test_runtime_settings_ignores_legacy_network_approval_without_network_tool(
    app: DaemonApp,
) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    warnings = _runtime_warning_messages(payload)
    assert not any("network enabled but no network tool registered" in message for message in warnings)
    warning_ids = {warning["id"] for warning in payload["compatibility_warnings"]}
    assert "internet_policy_without_capability" not in warning_ids
    assert "approval_required_surface_unavailable" not in warning_ids


def test_get_runtime_settings_exposes_only_cold_claude_provider(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    providers = payload["brain"]["providers"]["value"]
    assert isinstance(providers, list)
    provider_names = {provider["name"] for provider in providers}
    assert provider_names == {"claude_cli"}
    graph_provider_ids = {
        provider["id"]
        for provider in payload["capability_graph"]["brain_capabilities"]["providers"]
    }
    assert graph_provider_ids == {"claude_cli"}

    claude_provider = _brain_capability_provider(payload, "claude_cli")
    assert (
        claude_provider.get("current") is True
        or claude_provider.get("raw", {}).get("current") is True
    )


def test_post_runtime_settings_apply_rejects_unknown_setting(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"brain.provider": "mock", "unknown.setting": True}},
        )

    assert status == 400
    assert "Unknown runtime setting" in payload["error"]


def test_post_runtime_settings_apply_rejects_unavailable_provider(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"brain.provider": "ollama"}},
        )

    assert status == 422
    assert (
        "not present in capability_graph" in payload["error"]
        or "unavailable" in payload["error"].lower()
        or "not registered" in payload["error"].lower()
    )


def test_post_runtime_settings_apply_local_runtime_cannot_enter_brain_graph_or_persist(
    app: DaemonApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(
        routes_runtime,
        "_build_local_capabilities",
        lambda _app: {
            "runtimes": [
                {
                    "id": "ollama",
                    "label": "Ollama",
                    "kind": "Local",
                    "configured": True,
                    "available": True,
                    "status": "ok",
                    "command": "ollama",
                    "command_path": "/usr/bin/ollama",
                    "models": [
                        {
                            "id": "llama3",
                            "label": "llama3",
                            "available": True,
                            "configured": False,
                        }
                    ],
                    "local_models": [
                        {
                            "id": "llama3",
                            "label": "llama3",
                            "available": True,
                        }
                    ],
                    "warning": None,
                    "blocker": None,
                }
            ],
            "local_runtime_status": "ok",
            "local_models": [{"id": "llama3", "label": "llama3", "available": True}],
        },
    )

    app.start()
    app.update_settings({BRAIN_ADAPTER_SETTING_KEY: "test"})
    assert app.brain_manager is not None
    assert "ollama" not in app.brain_manager.adapter_names()
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"brain.provider": "ollama", "brain.model": "llama3"}},
        )
        _, refreshed = request_json("GET", f"{base_url}/runtime/settings")

    assert status in {409, 422}
    assert "not present in capability_graph" in payload["error"]
    assert settings_value(app, BRAIN_ADAPTER_SETTING_KEY) == "test"
    assert refreshed["brain"]["current_adapter"]["value"] == "test"


def test_post_runtime_settings_apply_rejects_unsupported_effort(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"brain.effort": "x-large"}},
        )

    assert status == 422
    assert "not next-turn apply-capable" in payload["error"]
    assert "provider 'test'" in payload["error"]


def test_runtime_settings_claude_model_next_turn_apply_updates_command_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(routes_runtime.shutil, "which", lambda command: "/usr/bin/fake-claude")
    monkeypatch.setattr(routes_runtime, "_safe_probe_cli_version", lambda command: ("1.0.0", "ok", None))
    monkeypatch.setattr(routes_runtime, "_safe_probe_claude_auth_status", lambda command: ("logged_in", None))
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml=(
            "\n[brain.claude_cli]\n"
            "enabled = true\n"
            "command = \"fake-claude\"\n"
            "model = \"claude-old\"\n"
            "effort = \"low\"\n"
            "permission_mode = \"manual\"\n"
        ),
    )
    app = create_daemon_app(config_path)
    try:
        assert app.brain_manager is not None
        adapter = app.brain_manager.get_adapter("claude_cli")
        adapter.available_models = lambda: ["claude-old", "claude-new"]  # type: ignore[method-assign]
        with running_server(app) as base_url:
            status, before = request_json("GET", f"{base_url}/runtime/settings")
            assert status == 200
            brain_section = before["settings_preview"]["sections"]["brain_provider"]
            assert brain_section["apply_semantics"] == "next_turn"
            assert brain_section["apply_capable"] is True
            assert "brain.model" in brain_section["valid_next_turn_changes"]
            assert brain_section["fields"]["model"]["apply_capable"] is True

            status, payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": {"brain.model": "claude-new"}},
            )
    finally:
        app.close()

    assert status == 200, payload
    assert payload["applied"] == ["brain.model"]
    refreshed = payload["runtime_settings"]
    command_preview = _settings_preview_field(refreshed, "brain_provider", "command_preview")
    assert "--model claude-new" in command_preview["current"]
    assert "--effort low" in command_preview["current"]


def test_runtime_settings_rejects_unknown_effort_without_pending_applyable_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(routes_runtime.shutil, "which", lambda command: "/usr/bin/fake-claude")
    monkeypatch.setattr(routes_runtime, "_safe_probe_cli_version", lambda command: ("1.0.0", "ok", None))
    monkeypatch.setattr(routes_runtime, "_safe_probe_claude_auth_status", lambda command: ("logged_in", None))
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml=(
            "\n[brain.claude_cli]\n"
            "enabled = true\n"
            "command = \"fake-claude\"\n"
            "model = \"claude-sonnet\"\n"
            "effort = \"\"\n"
        ),
    )
    app = create_daemon_app(config_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": {"brain.effort": "unknown"}},
            )
            _, refreshed = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        app.close()

    assert status == 422
    assert "invalid value" in payload["error"].lower()
    assert "brain.effort" in payload["rejected_keys"]
    brain_section = refreshed["settings_preview"]["sections"]["brain_provider"]
    assert "brain.effort" in brain_section["valid_next_turn_changes"]
    assert brain_section["fields"]["effort"]["validation"]["target_valid"] is True
    assert settings_value(app, "effort") is None


def test_runtime_settings_rejects_unregistered_stale_warm_provider_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(routes_runtime.shutil, "which", lambda command: "/usr/bin/fake-claude")
    monkeypatch.setattr(routes_runtime, "_safe_probe_cli_version", lambda command: ("1.0.0", "ok", None))
    monkeypatch.setattr(routes_runtime, "_safe_probe_claude_auth_status", lambda command: ("logged_in", None))
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli_warm",
        extra_toml=(
            "\n[brain.claude_cli_warm]\n"
            "enabled = true\n"
            "command = \"fake-claude\"\n"
            "model = \"claude-warm\"\n"
            "effort = \"high\"\n"
        ),
    )
    with pytest.raises(ConfigError, match="brain.claude_cli_warm.enabled"):
        create_daemon_app(config_path)


def test_post_runtime_settings_apply_rejects_mock_as_normal_brain_provider(
    app: DaemonApp,
) -> None:
    with running_server(app) as base_url:
        status, runtime = request_json("GET", f"{base_url}/runtime/settings")
        assert status == 200
        provider_field = _settings_preview_field(runtime, "brain_provider", "provider")
        assert "mock" not in provider_field["allowed_values"]
        assert not any(item["value"] == "mock" for item in provider_field["disabled_values"])

        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"brain.provider": "mock"}},
        )

    assert status == 422
    assert "mock" in payload["error"].lower()
    assert "not present in capability_graph" in payload["error"]


def test_post_runtime_settings_apply_updates_session_voice_projection(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"voice.speak_responses": True}},
        )
        assert status == 200, payload
        status, refreshed = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    assert payload["applied"] == ["voice.speak_responses"]
    assert payload["runtime_settings"]["voice"]["speak_responses"]["effective_value"] is True
    assert refreshed["voice"]["speak_responses"]["effective_value"] is True
    assert payload["status"] == "applied"
    assert payload["applied_keys"] == ["voice.speak_responses"]
    assert payload["rejected_keys"] == []
    assert payload["unchanged_keys"] == []
    assert payload["requires_restart_keys"] == []
    assert payload["blockers"] == []


def test_post_runtime_settings_apply_blocks_speak_responses_without_tts(tmp_path: Path) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = true\nspeak_responses = false\nbroker_enabled = false\ndefault_tts = ''\ndefault_stt = 'mock'\n",
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": {"voice.speak_responses": True}},
            )
    finally:
        daemon_app.close()

    assert status == 422
    assert "TTS" in payload["error"]


def test_post_runtime_settings_apply_rejects_versioned_tts_before_voice_validation(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = false\nspeak_responses = false\nbroker_enabled = false\n"
        "default_tts = 'mock'\ndefault_stt = 'mock'\n"
        "supertonic_binary = '/bin/echo'\nsupertonic_voice = ''\nsupertonic_lang = 'pl'\n",
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": {"voice.default_tts": "supertonic"}},
            )
            _, refreshed = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 400
    assert "voice.default_tts" in payload["error"]
    assert "read-only" in payload["error"]
    assert refreshed["voice"]["default_tts"]["effective_value"] == "mock"


def test_post_runtime_settings_apply_rejects_versioned_default_tts(
    tmp_path: Path,
) -> None:
    config_path = rewrite_voice_section(
        write_config(
            tmp_path / "dan.toml",
            tmp_path / "home" / "dan.db",
            extra_toml="\n",
        ),
        "enabled = false\nspeak_responses = false\nbroker_enabled = false\n"
        "default_tts = 'mock'\ndefault_stt = 'mock'\n"
        "supertonic_binary = '/bin/echo'\nsupertonic_voice = 'M2'\nsupertonic_lang = 'pl'\n",
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": {"voice.default_tts": "supertonic"}},
            )
            _, refreshed = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 400
    assert "voice.default_tts" in payload["error"]
    assert "read-only" in payload["error"]
    assert refreshed["voice"]["default_tts"]["effective_value"] == "mock"


def test_post_runtime_settings_apply_rejects_unsupported_ptt_mode(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"voice.ptt_mode": "off"}},
        )

    assert status == 422
    assert "voice.ptt_mode" in payload["error"]


def test_post_runtime_settings_apply_updates_ptt_hotkey(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"voice.ptt_hotkey": "right_cmd+right_shift"}},
        )
        refreshed_status, refreshed = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200, payload
    assert refreshed_status == 200
    assert payload["applied_keys"] == ["voice.ptt_hotkey"]
    assert payload["rejected_keys"] == []
    assert payload["runtime_settings"]["voice"]["ptt_hotkey"]["effective_value"] == "right_cmd+right_shift"
    ptt_hotkey = refreshed["settings_preview"]["sections"]["endpointing_ptt"]["fields"]["ptt_hotkey"]
    assert ptt_hotkey["current"] == "right_cmd+right_shift"
    assert ptt_hotkey["source"] == "config"
    assert ptt_hotkey["apply_capable"] is True
    assert load_config(app.config.source_path).voice.ptt_hotkey == "right_cmd+right_shift"


def test_post_runtime_settings_apply_rejects_invalid_ptt_hotkey(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"voice.ptt_hotkey": "bad_key"}},
        )

    assert status == 422
    assert "bad_key" in payload["error"]


def test_post_runtime_settings_apply_rejects_dead_merge_window(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {"settings": {"voice.merge_window": 2.5}},
        )

    assert status == 400
    assert "voice.merge_window" in payload["error"]
    assert "dead runtime setting" in payload["error"]


def test_runtime_settings_tools_internet_projection_uses_registered_network_tool_truth(
    app: DaemonApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(routes_runtime, "_safe_probe_network_capability", lambda: ("yes", "curl"))

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    tools = payload["tools"]
    assert tools["tools_enabled"]["effective_value"] is True
    assert tools["tools_master_flag"]["effective_value"] == "enabled"
    assert tools["tool_registry_status"]["effective_value"] == "registered"
    assert tools["network_search_tool"]["effective_value"] == "registered"
    assert tools["internet_capability"]["effective_value"] == {
        "state": "available",
        "registered_network_tools": ["web_fetch"],
    }
    assert tools["internet_capability"]["warning"] is None
    tools_capabilities = payload["capability_graph"]["tools_capabilities"]
    assert tools_capabilities["tools_master_flag"] == "enabled"
    assert tools_capabilities["internet_capability"] == {
        "state": "available",
        "registered_network_tools": ["web_fetch"],
    }
    assert tools_capabilities["network_search_tool"] == "registered"
    assert tools_capabilities["apply_capability"] == "yes"
    assert tools_capabilities["requires_restart"] is False
    assert tools_capabilities["blocker"] is None
    warnings = _runtime_warning_messages(payload)
    assert not any("network enabled but no network tool registered" in message for message in warnings)


def test_runtime_settings_exposes_only_active_tool_policy_apply_capabilities(
    app: DaemonApp,
) -> None:
    """Legacy approval flags are absent; remaining live policy stays truthful."""

    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    tools = payload["tools"]
    assert tools["apply_capability"]["effective_value"] == "yes"
    assert tools["requires_restart"]["effective_value"] is False

    apply_capabilities = payload["capability_graph"]["tools_capabilities"]["apply_capabilities"]
    approval_keys = {
        "security.require_approval_for_network",
        "security.require_approval_for_shell",
        "security.require_approval_for_file_write",
        "security.require_approval_for_ui",
        "security.require_approval_for_terminal",
        "security.require_approval_for_memory",
    }
    assert approval_keys.isdisjoint(apply_capabilities)
    for key in (
        "security.destructive_tools_enabled",
        "security.auto_approve_mode",
        "security.approved_roots",
        "security.voice_auto_approve_tools",
    ):
        capability = apply_capabilities[key]
        assert capability["apply_capable"] is True, key
        assert capability["requires_restart"] is False, key
    # The tool registry itself still cannot be rebuilt without a restart.
    for key in ("tools.enabled", "tools.network_enabled"):
        capability = apply_capabilities[key]
        assert capability["apply_capable"] is False, key
        assert capability["requires_restart"] is True, key


def test_post_runtime_settings_apply_rejects_legacy_approval_policy(app: DaemonApp) -> None:
    before = app.get_settings()

    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/runtime/settings/apply",
            {
                "settings": {
                    "security.require_approval_for_network": False,
                    "security.require_approval_for_ui": False,
                }
            },
        )

    assert status == 400
    assert payload["status"] == "blocked"
    assert payload["applied_keys"] == []
    assert payload["rejected_keys"] == [
        "security.require_approval_for_network",
        "security.require_approval_for_ui",
    ]
    assert app.get_settings() == before


def test_runtime_settings_marks_invalid_stale_effort_state_for_current_provider(
    app: DaemonApp,
) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"settings": {"model": "invalid-model", "effort": "x-large"}},
        )
        assert status == 200
        assert payload["settings"]["model"] == "invalid-model"

        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    brain_field = _settings_preview_field(payload, "brain_provider", "provider")
    assert brain_field["current"] == "test"
    assert brain_field["status"] == "ok"
    assert brain_field["apply_capable"] is False


def _install_fake_claude_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_output: str = "Logged in as dan@example.test",
    auth_returncode: int = 0,
    version_output: str = "claude fake 1.2.3",
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'if [ "$1" = "--version" ]; then',
                f"  printf '%s\\n' {json.dumps(version_output)}",
                "  exit 0",
                "fi",
                'if [ "$1" = "auth" ] && [ "$2" = "status" ]; then',
                f"  printf '%s\\n' {json.dumps(auth_output)}",
                f"  exit {auth_returncode}",
                "fi",
                "exit 64",
                "",
            ]
        ),
        encoding="utf-8",
    )
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return claude


def test_runtime_settings_claude_cli_missing_command_is_unavailable(
    tmp_path: Path,
) -> None:
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml='\n[brain.claude_cli]\nenabled = true\ncommand = "definitely-missing-claude"\nmodel = "claude-sonnet"\n',
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 200
    provider = next(
        item
        for item in payload["capability_graph"]["brain_capabilities"]["providers"]
        if item["id"] == "claude_cli"
    )
    assert provider["available"] is False
    assert provider["command_status"] == "missing"
    assert provider["apply_semantics"] == "not_apply_capable"
    assert "missing" in provider["blocker"].lower()


def test_runtime_settings_claude_cli_contract_command_preview_and_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_claude_cli(tmp_path, monkeypatch)
    secret = "sk-command-preview-secret"
    mcp_config = tmp_path / "claude-mcp.json"
    mcp_config.write_text("{}", encoding="utf-8")
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml=(
            "\n[brain.claude_cli]\n"
            "enabled = true\n"
            'command = "claude"\n'
            'args = ["-p"]\n'
            'model = "claude-configured"\n'
            'permission_mode = "acceptEdits"\n'
            'output_format = "stream-json"\n'
            'input_format = "text"\n'
            'tools = ["Bash", "Edit", "Read"]\n'
            'allowed_tools = ["file_read", "shell_read"]\n'
            f'disallowed_tools = ["{secret}"]\n'
            f'mcp_config_path = "{mcp_config}"\n'
            "strict_mcp_config = true\n"
        ),
    )
    daemon_app = create_daemon_app(config_path)
    try:
        daemon_app.start()
        daemon_app.update_settings({"model": "claude-sonnet-4", "effort": "xhigh"})
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 200
    provider = next(
        item
        for item in payload["capability_graph"]["brain_capabilities"]["providers"]
        if item["id"] == "claude_cli"
    )
    assert provider["provider_id"] == "claude_cli"
    assert provider["label"] == "Claude CLI"
    assert provider["kind"] == "cli"
    assert provider["transport"] == "subprocess"
    assert provider["command"] == "claude"
    assert provider["command_status"] == "found"
    assert provider["auth_status"] == "logged_in"
    assert provider["version"] == "claude fake 1.2.3"
    assert provider["selected_model"] == "claude-sonnet-4"
    assert provider["effective_model"] == "claude-sonnet-4"
    assert provider["model_source"] == "dan_explicit"
    # Model list is now live-resolved from Claude Code (stubbed deterministically
    # in tests) and always unions the adapter's configured model first.
    assert provider["allowed_models"][0] == "claude-configured"
    assert "claude-opus-4-8" in provider["allowed_models"]
    # The runtime-selected model (not the adapter's known set) must not leak into
    # allowed_models, and the bare-word aliases must not appear.
    assert "claude-sonnet-4" not in provider["allowed_models"]
    assert not {"sonnet", "opus", "haiku", "fable"} & set(provider["allowed_models"])
    assert provider["selected_effort"] == "xhigh"
    assert provider["effective_effort"] == "xhigh"
    assert provider["effort_source"] == "dan_explicit"
    assert provider["permission_mode"] == "acceptEdits"
    assert provider["tools"] == ["Bash", "Edit", "Read"]
    assert provider["allowed_tools"] == ["file_read", "shell_read"]
    assert provider["disallowed_tools"] == ["[REDACTED]"]
    assert provider["mcp_config_path"] == str(mcp_config)
    assert provider["mcp_config_status"] == "configured"
    assert provider["strict_mcp_config"] is True
    assert provider["output_format"] == "stream-json"
    assert provider["input_format"] == "text"
    assert provider["streaming_supported_state"] == "yes"
    assert provider["partial_messages_supported"] == "yes"
    assert provider["hook_events_supported"] == "unknown"
    assert provider["apply_semantics"] == "next_turn"
    preview = provider["command_preview"]
    assert "--model claude-sonnet-4" in preview
    assert "--effort xhigh" in preview
    assert "--permission-mode acceptEdits" in preview
    assert "--tools Bash,Edit,Read" in preview
    assert "--allowedTools file_read,shell_read" in preview
    assert "--disallowedTools [REDACTED]" in preview
    assert f"--mcp-config {mcp_config}" in preview
    assert "--strict-mcp-config" in preview
    assert "--output-format stream-json" in preview
    assert "--input-format text" in preview
    encoded = json.dumps(payload, sort_keys=True)
    assert secret not in encoded
    assert "[REDACTED]" in encoded


def test_runtime_settings_claude_cli_missing_auth_is_redacted_and_blocks_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_claude_cli(
        tmp_path,
        monkeypatch,
        auth_output="not logged in; token sk-auth-secret is unavailable",
        auth_returncode=1,
    )
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml='\n[brain.claude_cli]\nenabled = true\ncommand = "claude"\nmodel = "claude-sonnet"\n',
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 200
    provider = next(
        item
        for item in payload["capability_graph"]["brain_capabilities"]["providers"]
        if item["id"] == "claude_cli"
    )
    assert provider["auth_status"] == "missing"
    assert provider["available"] is False
    assert provider["apply_semantics"] == "not_apply_capable"
    encoded = json.dumps(payload, sort_keys=True)
    assert "sk-auth-secret" not in encoded


def test_runtime_settings_claude_cli_unknown_auth_warns_without_blocking_next_turn_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_claude_cli(
        tmp_path,
        monkeypatch,
        auth_output="Claude account state cannot be determined",
        auth_returncode=0,
    )
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml='\n[brain.claude_cli]\nenabled = true\ncommand = "claude"\nmodel = "claude-sonnet"\n',
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 200
    provider = next(
        item
        for item in payload["capability_graph"]["brain_capabilities"]["providers"]
        if item["id"] == "claude_cli"
    )
    assert provider["auth_status"] == "unknown"
    assert provider["available"] is True
    assert provider["apply_semantics"] == "next_turn"
    assert provider["apply_capable"] is True
    assert provider["apply_semantics_reason"] is None
    assert any("auth" in warning.lower() for warning in provider["warnings"])


def test_runtime_settings_claude_cli_auto_permission_mode_blocks_next_turn_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_claude_cli(tmp_path, monkeypatch)
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml=(
            "\n[brain.claude_cli]\n"
            "enabled = true\n"
            'command = "claude"\n'
            'model = "claude-sonnet-4.5"\n'
            'permission_mode = "auto"\n'
        ),
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 200
    provider = next(
        item
        for item in payload["capability_graph"]["brain_capabilities"]["providers"]
        if item["id"] == "claude_cli"
    )
    assert provider["auth_status"] == "logged_in"
    assert provider["permission_mode"] == "auto"
    assert provider["apply_semantics"] == "not_apply_capable"
    assert provider["apply_capable"] is False
    assert "auto" in provider["apply_semantics_reason"]


def test_runtime_settings_claude_cli_bypass_permission_mode_allows_model_effort_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_claude_cli(tmp_path, monkeypatch)
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml=(
            "\n[brain.claude_cli]\n"
            "enabled = true\n"
            'command = "claude"\n'
            'model = "claude-sonnet"\n'
            'permission_mode = "bypassPermissions"\n'
        ),
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
            assert status == 200
            provider = next(
                item
                for item in payload["capability_graph"]["brain_capabilities"]["providers"]
                if item["id"] == "claude_cli"
            )
            assert provider["auth_status"] == "logged_in"
            assert provider["permission_mode"] == "bypassPermissions"
            # bypassPermissions no longer blocks next-turn apply of model/effort —
            # those are pure argument swaps and must be settable from the panel.
            assert provider["apply_semantics"] == "next_turn"
            assert provider["apply_capable"] is True
            assert provider["apply_semantics_reason"] is None

            # And the apply actually goes through (200) and changes the CLI command.
            apply_status, apply_payload = request_json(
                "POST",
                f"{base_url}/runtime/settings/apply",
                {"settings": {"brain.model": "claude-opus-4-8", "brain.effort": "medium"}},
            )
            assert apply_status == 200, apply_payload
            assert set(apply_payload["applied"]) == {"brain.model", "brain.effort"}
            command_preview = _settings_preview_field(
                apply_payload["runtime_settings"], "brain_provider", "command_preview"
            )
            assert "--model claude-opus-4-8" in command_preview["current"]
            assert "--effort medium" in command_preview["current"]
    finally:
        daemon_app.close()


def test_runtime_settings_claude_cli_unknown_effort_does_not_enter_command_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_claude_cli(tmp_path, monkeypatch)
    config_path = write_config(
        tmp_path / "dan.toml",
        tmp_path / "home" / "dan.db",
        brain_default_adapter="claude_cli",
        extra_toml='\n[brain.claude_cli]\nenabled = true\ncommand = "claude"\nmodel = "claude-sonnet"\n',
    )
    daemon_app = create_daemon_app(config_path)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json("GET", f"{base_url}/runtime/settings")
    finally:
        daemon_app.close()

    assert status == 200
    provider = next(
        item
        for item in payload["capability_graph"]["brain_capabilities"]["providers"]
        if item["id"] == "claude_cli"
    )
    assert provider["selected_effort"] is None
    assert provider["effective_effort"] is None
    assert "--effort" not in provider["command_preview"]


def test_claude_cli_adapter_argv_uses_selected_model_from_command_contract() -> None:
    runner_calls: list[dict[str, Any]] = []

    def runner(
        command: list[str],
        input_text: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        runner_calls.append(
            {"command": list(command), "input_text": input_text, "timeout": timeout}
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    request = BrainRequest(
        turn_id="turn-claude-contract",
        conversation_id="conversation-claude-contract",
        input_text="hello",
        settings={
            "model": "claude-sonnet-4",
            "model_source": "settings",
            "effort": "high",
        },
    )
    adapter = ClaudeCliAdapter(
        command="claude",
        args=["-p"],
        model="claude-configured",
        permission_mode="acceptEdits",
        runner=runner,
    )

    response = adapter.generate(request)
    contract = build_claude_cli_command(
        adapter.command_settings(),
        request_settings=request.settings,
        streaming=False,
    )
    expected_command, expected_input = apply_claude_system_prompt(contract.argv, request)

    assert response.text == "ok"
    assert response.model == "claude-sonnet-4"
    assert runner_calls[0]["command"] == expected_command
    assert runner_calls[0]["input_text"] == expected_input
    assert expected_command.count("--system-prompt") == 1
    assert "--safe-mode" in expected_command
    assert "--no-session-persistence" in expected_command
    assert expected_command[expected_command.index("--setting-sources") + 1] == ""
    assert "--model" in contract.argv
    assert contract.argv[contract.argv.index("--model") + 1] == "claude-sonnet-4"
    assert "--model claude-sonnet-4" in contract.command_preview
    assert "--permission-mode acceptEdits" in contract.command_preview
    assert {
        "flag": "--model",
        "included": True,
        "source": "request_settings",
        "reason": "DAN-selected model is explicit.",
    } in contract.flag_metadata




def test_cli_health_state_and_events_can_query_ephemeral_server(
    app: DaemonApp,
    config_path: Path,
) -> None:
    app.start()
    with running_server(app) as base_url:
        health = run_cli("--config", str(config_path), "health", "--url", base_url)
        state = run_cli("--config", str(config_path), "state", "--url", base_url)
        events = run_cli(
            "--config",
            str(config_path),
            "events",
            "after",
            "--id",
            "0",
            "--limit",
            "100",
            "--url",
            base_url,
        )

    assert health.returncode == 0, health.stderr
    assert state.returncode == 0, state.stderr
    assert events.returncode == 0, events.stderr
    assert json.loads(health.stdout)["state"] == "IDLE"
    assert json.loads(state.stdout)["allowed_state_targets"]
    assert [event["type"] for event in json.loads(events.stdout)["events"]] == [
        "daemon.started",
        "state.changed",
    ]


def test_cli_runtime_commands_can_query_ephemeral_server(
    app: DaemonApp,
    config_path: Path,
    tmp_path: Path,
) -> None:
    app.runtime_supervisor = RuntimeSupervisor(
        home=tmp_path / "home",
        temp_dir=tmp_path / "temp",
        process_provider=lambda: [
            {"pid": 444, "process_name": "python", "command": "python auto_jarvis.py"}
        ],
        now=lambda: "2026-07-01T12:00:00+00:00",
    )
    with running_server(app) as base_url:
        processes = run_cli("--config", str(config_path), "runtime", "processes", "--url", base_url)
        startup = run_cli("--config", str(config_path), "runtime", "startup", "--url", base_url)
        legacy = run_cli("--config", str(config_path), "runtime", "legacy", "--url", base_url)

    assert processes.returncode == 0, processes.stderr
    assert startup.returncode == 0, startup.stderr
    assert legacy.returncode == 0, legacy.stderr
    assert json.loads(processes.stdout)["conflict_count"] == 1
    assert json.loads(startup.stdout)["official_label"] == "com.dan.dand"
    assert json.loads(legacy.stdout)["legacy_conflict_count"] == 1


def test_health_cli_exits_nonzero_when_daemon_is_unreachable(config_path: Path) -> None:
    result = run_cli("--config", str(config_path), "health", "--url", "http://127.0.0.1:9")

    assert result.returncode != 0
    assert "unreachable" in result.stderr.lower()


def test_no_real_home_is_touched_by_temp_config(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config = write_config(tmp_path / "dan.toml", db_path)

    daemon_app = create_daemon_app(config)
    try:
        assert str(daemon_app.paths.home).startswith(str(tmp_path))
        assert str(daemon_app.paths.db_path).startswith(str(tmp_path))
    finally:
        daemon_app.close()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    allowed_contracts = {("dan/voice/shared_broker.py", "/tmp/dan")}
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "dan",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css"}
    offenders: list[tuple[str, str]] = []

    for root in roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            relative = str(path.relative_to(ROOT))
            for snippet in forbidden:
                if snippet in text and (relative, snippet) not in allowed_contracts:
                    offenders.append((relative, snippet))

    assert offenders == []


# ---------------------------------------------------------------------------
# System-tab backend: per-model effort support + live Whisper STT option lists.
# These cover the capability_graph fields the panel's System tab consumes:
#   brain_capabilities.providers[].model_effort_support (owner decree: the
#     effort picker must reflect what the *selected model* actually accepts —
#     haiku takes none, the 4.6 generation tops out below xhigh) and
#   voice_capabilities.stt_languages / stt_providers[].models (real Whisper
#     language codes + model ids, not one-option echoes).
# ---------------------------------------------------------------------------


def test_model_effort_support_maps_efforts_per_model() -> None:
    from dan.api.routes_runtime import CLAUDE_CLI_EFFORTS, _model_effort_support

    provider_efforts = [str(effort) for effort in CLAUDE_CLI_EFFORTS]
    support = _model_effort_support(
        [
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5-20251001",
            "claude-brand-new-99",
        ],
        provider_efforts,
    )

    # Unknown / newest models get the full ladder so nothing is accidentally
    # restricted before we know better.
    assert support["claude-opus-4-8"] == ["low", "medium", "high", "xhigh", "max"]
    assert support["claude-brand-new-99"] == ["low", "medium", "high", "xhigh", "max"]
    # The 4.6 generation tops out at max with no xhigh.
    assert support["claude-sonnet-4-6"] == ["low", "medium", "high", "max"]
    assert support["claude-opus-4-6"] == ["low", "medium", "high", "max"]
    # Haiku takes no effort flag at all.
    assert support["claude-haiku-4-5-20251001"] == []


def test_model_effort_support_intersects_with_empty_provider_ladder() -> None:
    from dan.api.routes_runtime import _model_effort_support

    # A provider that reports no effort ladder (e.g. groq) yields [] for every
    # model, regardless of what the model itself would otherwise accept.
    support = _model_effort_support(["claude-opus-4-8", "claude-haiku-4-5"], [])
    assert support == {"claude-opus-4-8": [], "claude-haiku-4-5": []}
    # Blank ids are skipped entirely.
    assert _model_effort_support(["", "claude-opus-4-8"], ["low"]) == {
        "claude-opus-4-8": ["low"]
    }


def test_brain_capabilities_expose_model_effort_support(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    providers = payload["capability_graph"]["brain_capabilities"]["providers"]
    assert {provider["id"] for provider in providers} == {"claude_cli"}
    claude_provider = providers[0]
    support = claude_provider["model_effort_support"]
    assert isinstance(support, dict)
    assert all(isinstance(efforts, list) for efforts in support.values())
    assert set(support) == {model["id"] for model in claude_provider["models"]}


def test_voice_capabilities_expose_whisper_languages_and_models(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/runtime/settings")

    assert status == 200
    voice = payload["capability_graph"]["voice_capabilities"]

    # STT language list is the real Whisper contract (~99 codes), not a single
    # echoed value.
    languages = voice["stt_languages"]
    assert isinstance(languages, list)
    assert {"pl", "en", "de", "fr"}.issubset(set(languages))
    assert len(languages) > 50

    # The mlx_whisper provider offers genuine Whisper model ids plus its own
    # language list (not a lone configured-model echo).
    mlx = next(
        provider
        for provider in voice["stt_providers"]
        if provider["id"] == "mlx_whisper"
    )
    model_ids = {model["id"] for model in mlx["models"]}
    assert "mlx-community/whisper-large-v3-mlx" in model_ids
    assert len(model_ids) > 1
    assert {"pl", "en"}.issubset(set(mlx["languages"]))
