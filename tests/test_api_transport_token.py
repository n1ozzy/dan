"""Transport token enforcement on mutating daemon API requests (FAZA C1).

Design: docs/MACOS_PERMISSION_MODEL.md §5. The shared test config in
test_api_smoke.py opts out (`api_token_required = false`); this file runs the
daemon with the fail-closed default and proves the enforcement behavior.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from dan.brain import BrainManager, BrainRequest
from dan.brain.claude_cli_adapter import ClaudeCliAdapter
from dan.brain.test_adapter import TestBrainAdapter as HermeticBrainAdapter
from dan.daemon.app import DaemonApp, create_daemon_app
from dan.daemon.lifecycle import build_server
from dan.security.transport import (
    API_TOKEN_HEADER,
    api_token_path,
    ensure_api_token,
    load_api_token,
    verify_api_token,
)
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import ROOT, config_text


TOKEN_REQUIRED_SECURITY = "api_token_required = false"


def token_required_config_text(db_path: Path) -> str:
    text = config_text(db_path)
    assert TOKEN_REQUIRED_SECURITY in text
    return text.replace(TOKEN_REQUIRED_SECURITY, "api_token_required = true")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "dan.toml"
    path.write_text(
        token_required_config_text(tmp_path / "home" / "dan.db"),
        encoding="utf-8",
    )
    return path


def _replace_with_hermetic_brain(daemon_app: DaemonApp) -> None:
    production_manager = daemon_app.brain_manager
    daemon_app.brain_manager = BrainManager(
        [HermeticBrainAdapter(default_model="test-model")],
        default_adapter="test",
    )
    if production_manager is not None:
        production_manager.close()


@pytest.fixture
def forbid_production_claude(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    production_calls: list[str] = []

    def forbidden_generate(self: ClaudeCliAdapter, request: BrainRequest, **kwargs: Any):
        del self, request, kwargs
        production_calls.append("called")
        raise AssertionError("transport token fixture invoked production Claude")

    monkeypatch.setattr(ClaudeCliAdapter, "generate", forbidden_generate)
    return production_calls


@pytest.fixture
def app(config_path: Path, forbid_production_claude: list[str]) -> Iterator[DaemonApp]:
    del forbid_production_claude
    daemon_app = create_daemon_app(config_path)
    _replace_with_hermetic_brain(daemon_app)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


@pytest.fixture
def base_url(app: DaemonApp) -> Iterator[str]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="dan-token-http", daemon=True)
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
    payload: dict[str, object] | None = None,
    *,
    token: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers[API_TOKEN_HEADER] = token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_token_file_is_created_with_owner_only_permissions(app: DaemonApp) -> None:
    token_file = api_token_path(app.paths.runtime_dir)

    assert token_file.is_file()
    mode = stat.S_IMODE(token_file.stat().st_mode)
    assert mode == 0o600
    assert app.api_token == load_api_token(app.paths.runtime_dir)
    assert app.api_token


def test_token_is_stable_across_app_recreation(config_path: Path, app: DaemonApp) -> None:
    first_token = app.api_token
    assert first_token
    assert ensure_api_token(app.paths.runtime_dir) == first_token


def test_mutating_request_without_token_is_unauthorized(base_url: str) -> None:
    status, payload = request_json("POST", f"{base_url}/input/text", {"text": "hello"})

    assert status == 401
    assert payload == {"error": "Unauthorized", "status": 401}


def test_mutating_request_with_wrong_token_is_unauthorized(base_url: str) -> None:
    status, payload = request_json(
        "POST",
        f"{base_url}/input/text",
        {"text": "hello"},
        token="not-the-token",
    )

    assert status == 401
    assert payload == {"error": "Unauthorized", "status": 401}


def test_mutating_request_with_valid_token_succeeds(app: DaemonApp, base_url: str) -> None:
    status, payload = request_json(
        "POST",
        f"{base_url}/input/text",
        {"text": "token smoke"},
        token=app.api_token,
    )

    assert status == 200
    assert payload["turn"]["status"] == "finished"


def test_transport_token_fixture_never_calls_production_claude_adapter(
    app: DaemonApp,
    base_url: str,
    forbid_production_claude: list[str],
) -> None:
    status, payload = request_json(
        "POST",
        f"{base_url}/input/text",
        {"text": "hermetic token smoke"},
        token=app.api_token,
    )

    assert status == 200
    assert payload["final_text"] == "Test response: hermetic token smoke"
    assert forbid_production_claude == []


def test_unauthorized_request_creates_no_turn_or_event(app: DaemonApp, base_url: str) -> None:
    assert app.event_store is not None
    before = [event.id for event in app.event_store.list_after(0, limit=500)]

    status, _ = request_json("POST", f"{base_url}/input/text", {"text": "blocked"})

    assert status == 401
    after = [event.id for event in app.event_store.list_after(0, limit=500)]
    assert after == before


def test_read_only_status_endpoints_do_not_require_token(base_url: str) -> None:
    # Status/mechanism reads stay open (monitoring, panel bootstrap). Private
    # DATA reads are covered separately below (FIX-06 follow-up moved /memory
    # out of this list).
    for path in ("/health", "/state", "/events", "/tools"):
        status, _ = request_json("GET", f"{base_url}{path}")
        assert status == 200, path


def test_private_read_endpoints_require_token(base_url: str) -> None:
    # FIX-06 follow-up: after CORS null removal + Host validation, an untokened
    # GET of private data was still the "any local process reads your data"
    # vector. These endpoints now require the transport token.
    for path in ("/conversations", "/turns", "/memory", "/memory/some-id", "/settings"):
        status, payload = request_json("GET", f"{base_url}{path}")
        assert status == 401, path
        assert payload == {"error": "Unauthorized", "status": 401}


def test_private_read_endpoints_succeed_with_token(app: DaemonApp, base_url: str) -> None:
    token = app.api_token
    assert token
    for path in ("/conversations", "/memory", "/settings", "/turns?conversation_id=none"):
        status, _ = request_json("GET", f"{base_url}{path}", token=token)
        assert status == 200, path


def test_patch_and_delete_require_token(app: DaemonApp, base_url: str) -> None:
    for method in ("PATCH", "DELETE"):
        status, payload = request_json(method, f"{base_url}/memory/some-id")
        assert status == 401, method
        assert payload == {"error": "Unauthorized", "status": 401}


def test_token_not_required_when_disabled_in_config(tmp_path: Path) -> None:
    config_file = tmp_path / "dan.toml"
    config_file.write_text(
        config_text(tmp_path / "home" / "dan.db"),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_file)
    _replace_with_hermetic_brain(daemon_app)
    daemon_app.start()
    server = build_server(daemon_app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = request_json(
            "POST",
            f"{server.base_url}/input/text",
            {"text": "open mode"},
        )
        assert status == 200
        assert payload["turn"]["status"] == "finished"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


def test_verify_api_token_rejects_missing_values() -> None:
    assert verify_api_token(None, "x") is False
    assert verify_api_token("x", None) is False
    assert verify_api_token("", "") is False
    assert verify_api_token("abc", "abc") is True
    assert verify_api_token("abc", "abd") is False


def test_cli_sends_token_for_mutating_requests(
    app: DaemonApp,
    base_url: str,
    config_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dan.cli",
            "--config",
            str(config_path),
            "input",
            "text",
            "--url",
            base_url,
            "cli token smoke",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["turn"]["status"] == "finished"


def test_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
