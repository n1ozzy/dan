"""Prompt 11C CLI history client tests."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from jarvis import cli as jarvis_cli
from tests.test_api_smoke import write_config


ROOT = Path(__file__).resolve().parents[1]


class RecordedRequest(dict[str, Any]):
    pass


@contextmanager
def history_server(
    *,
    status: int = 200,
    response_payload: dict[str, Any] | None = None,
) -> Iterator[tuple[str, list[RecordedRequest]]]:
    records: list[RecordedRequest] = []
    payload = response_payload or {"conversations": [], "limit": 50, "include_archived": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            records.append(
                RecordedRequest(
                    method="GET",
                    path=parsed.path,
                    query=parse_qs(parsed.query),
                    raw_path=self.path,
                )
            )
            self._write_json(status, payload)

        def do_POST(self) -> None:
            records.append(RecordedRequest(method="POST", path=self.path))
            self._write_json(405, {"error": "method not allowed", "status": 405})

        def log_message(self, format: str, *args: object) -> None:
            return None

        def _write_json(self, response_status: int, response_payload: dict[str, Any]) -> None:
            body = json.dumps(response_payload).encode("utf-8")
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-cli-history-test", daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", records
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    rc = jarvis_cli.main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def config_args() -> tuple[str, str]:
    return "--config", str(ROOT / "config" / "jarvis.example.toml")


def unused_local_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
    return f"http://{host}:{port}"


def test_cli_conversations_list_sends_get_to_conversations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with history_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "conversations",
            "list",
            "--url",
            base_url,
        )

    assert rc == 0
    assert len(records) == 1
    assert records[0]["method"] == "GET"
    assert records[0]["path"] == "/conversations"


def test_cli_conversations_list_sends_limit_when_provided(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with history_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "conversations",
            "list",
            "--limit",
            "25",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["query"]["limit"] == ["25"]


def test_cli_conversations_list_sends_include_archived_when_provided(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with history_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "conversations",
            "list",
            "--include-archived",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["query"]["include_archived"] == ["true"]


def test_cli_conversations_list_exits_zero_and_prints_json_on_200(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {
        "conversations": [{"id": "conversation-cli", "turn_count": 1}],
        "limit": 50,
        "include_archived": False,
    }

    with history_server(response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(capsys, *config_args(), "conversations", "list", "--url", base_url)

    assert rc == 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_conversations_list_exits_nonzero_and_preserves_json_on_http_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {"error": "Daemon app is not started.", "status": 503}

    with history_server(status=503, response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(capsys, *config_args(), "conversations", "list", "--url", base_url)

    assert rc != 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_turns_list_requires_conversation_id_locally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        jarvis_cli.main([*config_args(), "turns", "list"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "conversation-id" in captured.err


def test_cli_turns_list_sends_get_to_turns_with_conversation_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {"conversation_id": "conversation-cli", "turns": [], "limit": 50, "newest_first": False}

    with history_server(response_payload=response) as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "turns",
            "list",
            "--conversation-id",
            "conversation-cli",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["method"] == "GET"
    assert records[0]["path"] == "/turns"
    assert records[0]["query"]["conversation_id"] == ["conversation-cli"]


def test_cli_turns_list_sends_limit_when_provided(capsys: pytest.CaptureFixture[str]) -> None:
    with history_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "turns",
            "list",
            "--conversation-id",
            "conversation-cli",
            "--limit",
            "10",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["query"]["limit"] == ["10"]


def test_cli_turns_list_sends_newest_first_when_provided(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with history_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "turns",
            "list",
            "--conversation-id",
            "conversation-cli",
            "--newest-first",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["query"]["newest_first"] == ["true"]


def test_cli_turns_list_exits_zero_and_prints_json_on_200(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {
        "conversation_id": "conversation-cli",
        "turns": [{"id": "turn-cli"}],
        "limit": 50,
        "newest_first": False,
    }

    with history_server(response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "turns",
            "list",
            "--conversation-id",
            "conversation-cli",
            "--url",
            base_url,
        )

    assert rc == 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_turns_list_exits_nonzero_on_unreachable_daemon(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out, err = run_cli(
        capsys,
        *config_args(),
        "turns",
        "list",
        "--conversation-id",
        "conversation-cli",
        "--url",
        unused_local_url(),
        "--timeout",
        "0.2",
    )

    assert rc != 0
    assert out == ""
    assert json.loads(err)["error"] == "daemon_unreachable"


@pytest.mark.parametrize(
    "command",
    [
        ("conversations", "list"),
        ("turns", "list", "--conversation-id", "conversation-cli"),
    ],
)
def test_cli_history_commands_do_not_start_daemon(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: tuple[str, ...],
) -> None:
    def fail_create_daemon_app(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("history commands must not start DaemonApp")

    monkeypatch.setattr(jarvis_cli, "create_daemon_app", fail_create_daemon_app)

    with history_server() as (base_url, _records):
        rc, _out, _err = run_cli(capsys, *config_args(), *command, "--url", base_url)

    assert rc == 0


@pytest.mark.parametrize(
    "command",
    [
        ("conversations", "list"),
        ("turns", "list", "--conversation-id", "conversation-cli"),
    ],
)
def test_cli_history_commands_do_not_initialize_or_create_db(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)

    def fail_initialize_database(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("history commands must not initialize SQLite")

    monkeypatch.setattr(jarvis_cli, "initialize_database", fail_initialize_database)

    with history_server() as (base_url, _records):
        rc, _out, _err = run_cli(
            capsys,
            "--config",
            str(config_path),
            *command,
            "--url",
            base_url,
        )

    assert rc == 0
    assert not db_path.exists()
