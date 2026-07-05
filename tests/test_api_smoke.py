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
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest

from jarvis.tools.permissions import RequestSource

from tests.git_guards import assert_schema_and_migrations_unchanged
from jarvis.config import COMPILED_MEMORY_ENABLED_ENV, COMPILED_MEMORY_FORCE_DISABLED_ENV
from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.daemon.lifecycle import MAX_REQUEST_BODY_BYTES, DaemonServer, build_server
from jarvis.daemon.state_machine import RuntimeState
from jarvis.memory.compiler import CompiledMemoryContext, MemoryCompiler, MemoryCompilerConfig
from jarvis.runtime.supervisor import RuntimeSupervisor
from jarvis.store.db import close_quietly
from jarvis.store.migrations import LATEST_SCHEMA_VERSION
from jarvis.tools.registry import Tool


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
    return f"""
[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = {port}
log_level = "INFO"

[database]
path = "{db_path}"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "mock"
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
destructive_tools_enabled = false

[runtime]
home = "{runtime_home}"
logs_dir = "{runtime_home / "logs"}"
runtime_dir = "{runtime_home / "runtime"}"
pid_file = "{runtime_home / "runtime" / "jarvisd.pid"}"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd"
install_automatically = false
"""


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
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


@contextmanager
def running_server(app: DaemonApp) -> Iterator[str]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-test-http", daemon=True)
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
    merged_env.pop("JARVIS_CONFIG", None)
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "jarvis.cli", *args],
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
            "project/jarvis",
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
    monkeypatch.setattr("jarvis.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("jarvis.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)

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
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
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
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
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
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)

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
    monkeypatch.setattr("jarvis.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("jarvis.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
        db_path,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "default"),
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
    monkeypatch.setattr("jarvis.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("jarvis.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
        db_path,
        memory_enabled=False,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "default"),
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
    monkeypatch.setattr("jarvis.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("jarvis.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)

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
        "JARVIS_COMPILED_MEMORY",
        "compiled_memory",
        "compiled-memory",
        "compiled memory",
    )
    user_facing_paths = [
        ROOT / "jarvis" / "daemon" / "lifecycle.py",
        *sorted((ROOT / "jarvis" / "api").glob("*.py")),
        *sorted((ROOT / "jarvis" / "panel" / "assets").glob("*")),
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

    monkeypatch.setattr("jarvis.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("jarvis.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
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
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
        db_path,
        compiled_context_enabled=True,
    )

    daemon_app = create_daemon_app(
        config_path,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "default"),
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
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
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
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
        db_path,
        compiled_context_enabled=False,
    )
    compiler = RuntimeSpyCompiler()

    daemon_app = create_daemon_app(
        config_path,
        memory_compiler=compiler,
        compiled_memory_enabled_session_profiles=(
            ("conversation-runtime", "default"),
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

    monkeypatch.setattr("jarvis.daemon.app.MemoryCompiler", ExplodingMemoryCompiler)
    monkeypatch.setattr("jarvis.brain.context_builder.MemoryCompiler", ExplodingMemoryCompiler)
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(
        tmp_path / "jarvis.toml",
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
    db_path = tmp_path / "home" / "jarvis.db"
    config = write_config(tmp_path / "jarvis.toml", db_path)

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
        "pending_approval_count",
    }

    snapshot = app.snapshot_state()

    assert set(snapshot) == expected
    assert snapshot["service"] == "jarvisd"
    assert snapshot["ok"] is True
    assert snapshot["started"] is True
    assert snapshot["state"] == "IDLE"
    assert snapshot["latest_event_id"] == 2
    assert snapshot["pending_approval_count"] == 0


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


def test_get_health_returns_200_json_and_expected_fields(app: DaemonApp) -> None:
    app.start()
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/health")

    assert status == 200
    assert payload["ok"] is True
    assert payload["service"] == "jarvisd"
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


def test_get_settings_returns_empty_settings_initially(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/settings")

    assert status == 200
    assert payload == {"settings": {}}


def test_post_settings_upserts_single_key(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"key": "ui.theme", "value": {"mode": "dark"}},
        )

    assert status == 200
    assert payload["settings"]["ui.theme"] == {"mode": "dark"}
    row = app.conn.execute("SELECT value_json, source FROM settings WHERE key = ?", ("ui.theme",)).fetchone()
    assert json.loads(row[0]) == {"mode": "dark"}
    assert row[1] == "api"


def test_post_settings_upserts_multiple_keys(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/settings",
            {"settings": {"ui.theme": "dark", "voice.enabled": False}},
        )

    assert status == 200
    assert payload["settings"] == {"ui.theme": "dark", "voice.enabled": False}


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
    assert payload["final_text"] == "Jarvis mock response: hello"
    assert payload["brain_adapter"] == "mock"
    assert payload["brain_model"] == "mock-local"
    turn_count = app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert turn_count == 1
    assert "brain.responded" in event_types(app)


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
    (launch_agents / "com.ozzy.jarvisd.plist").write_text("placeholder", encoding="utf-8")
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
    assert payload["official_label"] == "com.ozzy.jarvisd"
    assert payload["startup"]["official_label"] == "com.ozzy.jarvisd"
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
    assert json.loads(startup.stdout)["official_label"] == "com.ozzy.jarvisd"
    assert json.loads(legacy.stdout)["legacy_conflict_count"] == 1


def test_health_cli_exits_nonzero_when_daemon_is_unreachable(config_path: Path) -> None:
    result = run_cli("--config", str(config_path), "health", "--url", "http://127.0.0.1:9")

    assert result.returncode != 0
    assert "unreachable" in result.stderr.lower()


def test_no_real_home_is_touched_by_temp_config(tmp_path: Path) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config = write_config(tmp_path / "jarvis.toml", db_path)

    daemon_app = create_daemon_app(config)
    try:
        assert str(daemon_app.paths.home).startswith(str(tmp_path))
        assert str(daemon_app.paths.db_path).startswith(str(tmp_path))
    finally:
        daemon_app.close()


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "jarvis",
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
            for snippet in forbidden:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
