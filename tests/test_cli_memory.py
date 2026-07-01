"""Prompt 16 CLI memory client tests."""

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
def memory_server(
    *,
    status: int = 200,
    response_payload: dict[str, Any] | None = None,
) -> Iterator[tuple[str, list[RecordedRequest]]]:
    records: list[RecordedRequest] = []
    payload = response_payload or {"memory": [], "active_only": False, "limit": 100}

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
            self._record_json_body("POST")
            self._write_json(status, payload)

        def do_PATCH(self) -> None:
            self._record_json_body("PATCH")
            self._write_json(status, payload)

        def do_DELETE(self) -> None:
            records.append(RecordedRequest(method="DELETE", path=self.path))
            self._write_json(status, payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

        def _record_json_body(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            records.append(
                RecordedRequest(
                    method=method,
                    path=self.path,
                    headers=dict(self.headers),
                    json=json.loads(body.decode("utf-8")),
                )
            )

        def _write_json(self, response_status: int, response_payload: dict[str, Any]) -> None:
            body = json.dumps(response_payload).encode("utf-8")
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-cli-memory-test", daemon=True)
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


def test_cli_memory_list_sends_get_to_memory(capsys: pytest.CaptureFixture[str]) -> None:
    with memory_server() as (base_url, records):
        rc, _out, _err = run_cli(capsys, *config_args(), "memory", "list", "--url", base_url)

    assert rc == 0
    assert records == [
        {
            "method": "GET",
            "path": "/memory",
            "query": {},
            "raw_path": "/memory",
        }
    ]


def test_cli_memory_list_sends_filters_when_provided(capsys: pytest.CaptureFixture[str]) -> None:
    with memory_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "list",
            "--active-only",
            "--kind",
            "fact",
            "--limit",
            "25",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["path"] == "/memory"
    assert records[0]["query"]["active_only"] == ["true"]
    assert records[0]["query"]["kind"] == ["fact"]
    assert records[0]["query"]["limit"] == ["25"]


def test_cli_memory_create_sends_post_to_memory(capsys: pytest.CaptureFixture[str]) -> None:
    response = {"memory": {"id": "memory-1"}}

    with memory_server(status=201, response_payload=response) as (base_url, records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "create",
            "--kind",
            "fact",
            "--title",
            "Fact",
            "--body",
            "Body",
            "--url",
            base_url,
        )

    assert rc == 0
    assert err == ""
    assert json.loads(out) == response
    assert records[0]["method"] == "POST"
    assert records[0]["path"] == "/memory"
    assert records[0]["json"] == {
        "kind": "fact",
        "title": "Fact",
        "body": "Body",
        "priority": 0,
    }


def test_cli_memory_create_sends_metadata_json(capsys: pytest.CaptureFixture[str]) -> None:
    with memory_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "create",
            "--kind",
            "project",
            "--title",
            "Project",
            "--body",
            "Body",
            "--metadata-json",
            '{"repo":"jarvis"}',
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["json"]["metadata"] == {"repo": "jarvis"}


def test_cli_memory_create_rejects_invalid_metadata_json_locally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with memory_server() as (base_url, records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "create",
            "--kind",
            "fact",
            "--title",
            "Bad",
            "--body",
            "Body",
            "--metadata-json",
            "{not-json",
            "--url",
            base_url,
        )

    assert rc != 0
    assert out == ""
    assert json.loads(err)["error"] == "invalid_metadata_json"
    assert records == []


def test_cli_memory_create_rejects_non_object_metadata_json_locally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with memory_server() as (base_url, records):
        rc, out, err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "create",
            "--kind",
            "fact",
            "--title",
            "Bad",
            "--body",
            "Body",
            "--metadata-json",
            '["not-object"]',
            "--url",
            base_url,
        )

    assert rc != 0
    assert out == ""
    assert json.loads(err)["error"] == "invalid_metadata_json"
    assert records == []


def test_cli_memory_show_sends_get_to_memory_id(capsys: pytest.CaptureFixture[str]) -> None:
    with memory_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "show",
            "--id",
            "memory-1",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["method"] == "GET"
    assert records[0]["path"] == "/memory/memory-1"


def test_cli_memory_update_sends_patch_to_memory_id(capsys: pytest.CaptureFixture[str]) -> None:
    with memory_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "update",
            "--id",
            "memory-1",
            "--title",
            "Updated",
            "--body",
            "Updated body",
            "--priority",
            "7",
            "--active",
            "false",
            "--metadata-json",
            '{"updated":true}',
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["method"] == "PATCH"
    assert records[0]["path"] == "/memory/memory-1"
    assert records[0]["json"] == {
        "title": "Updated",
        "body": "Updated body",
        "priority": 7,
        "active": False,
        "metadata": {"updated": True},
    }


def test_cli_memory_disable_sends_delete_to_memory_id(capsys: pytest.CaptureFixture[str]) -> None:
    with memory_server() as (base_url, records):
        rc, _out, _err = run_cli(
            capsys,
            *config_args(),
            "memory",
            "disable",
            "--id",
            "memory-1",
            "--url",
            base_url,
        )

    assert rc == 0
    assert records[0]["method"] == "DELETE"
    assert records[0]["path"] == "/memory/memory-1"


def test_cli_memory_exits_nonzero_and_preserves_json_on_http_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {"error": "Daemon app is not started.", "status": 503}

    with memory_server(status=503, response_payload=response) as (base_url, _records):
        rc, out, err = run_cli(capsys, *config_args(), "memory", "list", "--url", base_url)

    assert rc != 0
    assert err == ""
    assert json.loads(out) == response


def test_cli_memory_unreachable_daemon_returns_nonzero_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out, err = run_cli(
        capsys,
        *config_args(),
        "memory",
        "list",
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
        ("memory", "list"),
        ("memory", "create", "--kind", "fact", "--title", "Fact", "--body", "Body"),
        ("memory", "show", "--id", "memory-1"),
        ("memory", "update", "--id", "memory-1", "--title", "Updated"),
        ("memory", "disable", "--id", "memory-1"),
    ],
)
def test_cli_memory_commands_do_not_start_daemon(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: tuple[str, ...],
) -> None:
    def fail_create_daemon_app(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("memory commands must not start DaemonApp")

    monkeypatch.setattr(jarvis_cli, "create_daemon_app", fail_create_daemon_app)

    with memory_server() as (base_url, _records):
        rc, _out, _err = run_cli(capsys, *config_args(), *command, "--url", base_url)

    assert rc == 0


@pytest.mark.parametrize(
    "command",
    [
        ("memory", "list"),
        ("memory", "create", "--kind", "fact", "--title", "Fact", "--body", "Body"),
        ("memory", "show", "--id", "memory-1"),
        ("memory", "update", "--id", "memory-1", "--title", "Updated"),
        ("memory", "disable", "--id", "memory-1"),
    ],
)
def test_cli_memory_commands_do_not_initialize_or_create_db(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    db_path = tmp_path / "home" / "jarvis.db"
    config_path = write_config(tmp_path / "jarvis.toml", db_path)

    def fail_initialize_database(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("memory commands must not initialize SQLite")

    monkeypatch.setattr(jarvis_cli, "initialize_database", fail_initialize_database)

    with memory_server() as (base_url, _records):
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
