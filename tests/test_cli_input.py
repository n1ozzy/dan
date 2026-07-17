"""Prompt 11B CLI text input client tests."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from dan import cli as dan_cli
from tests.test_api_smoke import write_config


ROOT = Path(__file__).resolve().parents[1]


class RecordedRequest(dict[str, Any]):
    pass


@contextmanager
def input_server(
    *,
    status: int = 200,
    response_payload: dict[str, Any] | None = None,
) -> Iterator[tuple[str, list[RecordedRequest]]]:
    records: list[RecordedRequest] = []
    payload = response_payload or {"ok": True, "turn_id": "turn-cli"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            records.append(
                RecordedRequest(
                    method="POST",
                    path=self.path,
                    headers=dict(self.headers),
                    json=json.loads(body.decode("utf-8")),
                )
            )
            self._write_json(status, payload)

        def do_GET(self) -> None:
            records.append(RecordedRequest(method="GET", path=self.path))
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
    thread = threading.Thread(target=server.serve_forever, name="dan-cli-input-test", daemon=True)
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
    rc = dan_cli.main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def config_args() -> tuple[str, str]:
    return "--config", str(ROOT / "config" / "dan.example.toml")


def unused_local_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
    return f"http://{host}:{port}"


def test_cli_input_text_sends_post_to_input_text(capsys: pytest.CaptureFixture[str]) -> None:
    with input_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "input",
            "text",
            "Hello DAN",
            "--url",
            base_url,
        )

    assert rc == 0
    assert len(records) == 1
    assert records[0]["method"] == "POST"
    assert records[0]["path"] == "/input/text"


def test_cli_input_text_sends_source_cli(capsys: pytest.CaptureFixture[str]) -> None:
    with input_server() as (base_url, records):
        rc, _out, _err = run_cli(capsys, *config_args(), "input", "text", "Source", "--url", base_url)

    assert rc == 0
    assert records[0]["json"]["source"] == "cli"


def test_cli_input_text_sends_conversation_id_when_provided(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with input_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "input",
            "text",
            "Continue",
            "--conversation-id",
            "abc123",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["json"]["conversation_id"] == "abc123"


def test_cli_input_text_sends_metadata_json(capsys: pytest.CaptureFixture[str]) -> None:
    with input_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "input",
            "text",
            "Test",
            "--metadata-json",
            '{"origin":"manual-smoke"}',
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["json"]["metadata"] == {"origin": "manual-smoke"}


def test_cli_input_text_rejects_invalid_metadata_json_locally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with input_server() as (base_url, records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "input",
            "text",
            "Bad metadata",
            "--metadata-json",
            "{not-json",
            "--url",
            base_url,
        )

    assert rc != 0
    assert out == ""
    assert json.loads(err)["error"] == "invalid_metadata_json"
    assert records == []


def test_cli_input_text_rejects_non_object_metadata_json_locally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with input_server() as (base_url, records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "input",
            "text",
            "Bad metadata",
            "--metadata-json",
            '["not-object"]',
            "--url",
            base_url,
        )

    assert rc != 0
    assert out == ""
    assert json.loads(err)["error"] == "invalid_metadata_json"
    assert records == []


def test_cli_input_text_unreachable_daemon_returns_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out, err = run_cli(
        capsys,
        *config_args(),
        "input",
        "text",
        "Ping",
        "--url",
        unused_local_url(),
        "--timeout",
        "0.2",
    )

    assert rc != 0
    assert out == ""
    assert json.loads(err)["error"] == "daemon_unreachable"


def test_cli_input_text_http_409_preserves_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {"error": "Another text turn is already running.", "status": 409}

    with input_server(status=409, response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(capsys, *config_args(), "input", "text", "Busy", "--url", base_url)

    assert rc != 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_input_text_http_503_preserves_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {"error": "Daemon app is not started.", "status": 503}

    with input_server(status=503, response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(capsys, *config_args(), "input", "text", "Wait", "--url", base_url)

    assert rc != 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_input_text_http_200_prints_json_response(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {"ok": True, "turn_id": "turn-ok", "final_text": "pong"}

    with input_server(status=200, response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(capsys, *config_args(), "input", "text", "Ping", "--url", base_url)

    assert rc == 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_input_text_does_not_start_daemon(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create_daemon_app(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("input text must not start DaemonApp")

    monkeypatch.setattr(dan_cli, "create_daemon_app", fail_create_daemon_app)

    with input_server() as (base_url, _records):
        rc, _out, _err = run_cli(capsys, *config_args(), "input", "text", "No start", "--url", base_url)

    assert rc == 0


def test_cli_input_text_does_not_initialize_or_create_db(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(tmp_path / "dan.toml", db_path)

    def fail_initialize_database(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("input text must not initialize SQLite")

    monkeypatch.setattr(dan_cli, "initialize_database", fail_initialize_database)

    with input_server() as (base_url, _records):
        rc, _out, _err = run_cli(
            capsys,
            "--config",
            str(config_path),
            "input",
            "text",
            "No DB",
            "--url",
            base_url,
        )

    assert rc == 0
    assert not db_path.exists()
