"""Task 8: `dan speak` machine contract against a real local daemon API."""

from __future__ import annotations

import io
import json
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan import cli as dan_cli
from dan.daemon.app import DaemonApp

from tests.test_api_smoke import running_server
from tests.test_voice_api_contract import SPEAK_TEXT, make_voice_app, queue_rows


@pytest.fixture
def voice_app(tmp_path: Path) -> Iterator[DaemonApp]:
    app = make_voice_app(tmp_path)
    try:
        yield app
    finally:
        app.close()


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    rc = dan_cli.main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def set_stdin_bytes(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))


def speak_args(app: DaemonApp, base_url: str, *extra: str) -> list[str]:
    return [
        "--config",
        str(app.config.source_path),
        "speak",
        "--json",
        "--as",
        "dan",
        "--session",
        "smoke",
        "--source",
        "codex",
        "--url",
        base_url,
        *extra,
    ]


def unused_local_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
    return f"http://{host}:{port}"


def assert_single_json_object(out: str) -> dict[str, object]:
    stripped = out.strip()
    assert stripped
    assert "\n" not in stripped, "JSON mode must print exactly one object"
    payload = json.loads(stripped)
    assert isinstance(payload, dict)
    return payload


def test_speak_json_stdin_contract(
    voice_app: DaemonApp,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_stdin_bytes(monkeypatch, f"{SPEAK_TEXT}\n".encode("utf-8"))
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(capsys, *speak_args(voice_app, base_url, "--stdin"))

    payload = assert_single_json_object(out)
    assert rc == 0
    assert payload["status"] == "queued"
    rows = {row["id"]: row for row in queue_rows(voice_app)}
    assert rows[payload["request_id"]]["text"] == SPEAK_TEXT
    assert rows[payload["request_id"]]["status"] == "queued"
    assert rows[payload["request_id"]]["session_id"] == "smoke"


def test_speak_stdin_normalizes_nfc(
    voice_app: DaemonApp,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "Zażółć.")
    assert decomposed != "Zażółć."
    set_stdin_bytes(monkeypatch, decomposed.encode("utf-8"))
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(capsys, *speak_args(voice_app, base_url, "--stdin"))

    payload = assert_single_json_object(out)
    assert rc == 0
    rows = {row["id"]: row for row in queue_rows(voice_app)}
    assert rows[payload["request_id"]]["text"] == "Zażółć."


def test_speak_text_argument_without_stdin(
    voice_app: DaemonApp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(
            capsys, *speak_args(voice_app, base_url, SPEAK_TEXT)
        )

    payload = assert_single_json_object(out)
    assert rc == 0
    rows = {row["id"]: row for row in queue_rows(voice_app)}
    assert rows[payload["request_id"]]["text"] == SPEAK_TEXT


def test_speak_nonzero_means_not_accepted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from dan.voice.resolver import VoiceResolverError

    def broken_resolve(intent: object) -> object:
        raise VoiceResolverError("voice asset missing for persona 'dan'")

    app = make_voice_app(
        tmp_path, resolver=SimpleNamespace(resolve=broken_resolve)
    )
    try:
        set_stdin_bytes(monkeypatch, b"test")
        with running_server(app) as base_url:
            rc, out, _err = run_cli(capsys, *speak_args(app, base_url, "--stdin"))

        payload = assert_single_json_object(out)
        assert rc != 0
        assert "error" in payload
        assert queue_rows(app) == []
    finally:
        app.close()


def test_speak_rejects_invalid_utf8_stdin_locally(
    voice_app: DaemonApp,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_stdin_bytes(monkeypatch, b"\xff\xfe broken")
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(capsys, *speak_args(voice_app, base_url, "--stdin"))

    payload = assert_single_json_object(out)
    assert rc != 0
    assert "error" in payload
    assert queue_rows(voice_app) == []


def test_speak_requires_some_text(
    voice_app: DaemonApp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with running_server(voice_app) as base_url:
        rc, out, _err = run_cli(capsys, *speak_args(voice_app, base_url))

    payload = assert_single_json_object(out)
    assert rc != 0
    assert "error" in payload
    assert queue_rows(voice_app) == []


def test_speak_unreachable_daemon_prints_one_json_error(
    voice_app: DaemonApp,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_stdin_bytes(monkeypatch, b"ping")
    rc, out, _err = run_cli(
        capsys,
        "--config",
        str(voice_app.config.source_path),
        "speak",
        "--json",
        "--as",
        "dan",
        "--stdin",
        "--url",
        unused_local_url(),
        "--timeout",
        "0.2",
    )

    payload = assert_single_json_object(out)
    assert rc != 0
    assert payload["error"] == "daemon_unreachable"
    assert queue_rows(voice_app) == []
