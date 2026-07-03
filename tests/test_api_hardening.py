"""FIX-06: local API hardening.

Four defenses on the loopback HTTP server: reject foreign Host headers (DNS
rebinding), time out slow/partial requests (slowloris), cap concurrent /stream
sessions, and close the connection on an unauthorized mutation so a rejected
body cannot desync a keep-alive connection.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from jarvis.daemon import lifecycle
from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.daemon.lifecycle import build_server
from tests.test_api_smoke import config_text
from tests.test_api_stream import StreamClient
from tests.test_api_transport_token import token_required_config_text


def _make_app(tmp_path: Path, text: str) -> DaemonApp:
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(text, encoding="utf-8")
    app = create_daemon_app(config_path)
    app.start()
    return app


@pytest.fixture
def open_app(tmp_path: Path) -> Iterator[DaemonApp]:
    app = _make_app(tmp_path, config_text(tmp_path / "home" / "jarvis.db"))
    try:
        yield app
    finally:
        app.stop(reason="test teardown")
        app.close()


@pytest.fixture
def token_app(tmp_path: Path) -> Iterator[DaemonApp]:
    app = _make_app(tmp_path, token_required_config_text(tmp_path / "home" / "jarvis.db"))
    try:
        yield app
    finally:
        app.stop(reason="test teardown")
        app.close()


@contextmanager
def running_server(app: DaemonApp) -> Iterator[tuple[str, int]]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-hardening-http", daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield host, port
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _send_raw(
    host: str,
    port: int,
    request: bytes,
    *,
    read_timeout: float = 3.0,
) -> tuple[int | None, dict[str, str], bytes]:
    with socket.create_connection((host, port), timeout=read_timeout) as sock:
        sock.sendall(request)
        sock.settimeout(read_timeout)
        raw = b""
        try:
            while b"\r\n\r\n" not in raw:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        except (TimeoutError, socket.timeout):
            pass
    head, _, body = raw.partition(b"\r\n\r\n")
    if not head:
        return None, {}, b""
    status_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
    fields = status_line.split(" ")
    status = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None
    headers = {}
    for line in header_lines:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


def test_foreign_host_header_is_rejected(open_app: DaemonApp) -> None:
    # DNS rebinding: a browser on evil.example.com (resolved to 127.0.0.1) sends
    # requests with a foreign Host. Loopback binding alone does not stop it.
    with running_server(open_app) as (host, port):
        status, _, _ = _send_raw(
            host, port, b"GET /health HTTP/1.1\r\nHost: evil.example.com\r\n\r\n"
        )
    assert status == 403


def test_local_host_header_is_accepted(open_app: DaemonApp) -> None:
    # Regression guard: legitimate loopback Host must still be served.
    with running_server(open_app) as (host, port):
        status, _, _ = _send_raw(
            host, port, f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode()
        )
    assert status == 200


def test_slow_client_without_full_request_is_disconnected(
    open_app: DaemonApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Slowloris: a client that opens the socket and dribbles a partial request
    # must not hold a worker thread forever — the socket timeout drops it.
    monkeypatch.setattr(lifecycle, "HANDLER_TIMEOUT_SECONDS", 0.5, raising=False)
    with running_server(open_app) as (host, port):
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"GET /health HTTP/1.1\r\n")  # no terminating CRLF-CRLF
            sock.settimeout(3.0)
            try:
                data = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                pytest.fail("server kept the slow connection open past its timeout")
    assert data == b""  # server closed the connection


def test_stream_session_cap_rejects_overflow(
    open_app: DaemonApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each /stream session holds its own SQLite handle and a worker thread;
    # concurrent sessions must be capped, with overflow refused before upgrade.
    monkeypatch.setattr(lifecycle, "MAX_STREAM_SESSIONS", 1, raising=False)
    with running_server(open_app) as (host, port):
        first = StreamClient(host, port)
        try:
            assert first.handshake(upgrade=True) == 101
            overflow = StreamClient(host, port)
            try:
                assert overflow.handshake(upgrade=True) == 503
            finally:
                overflow.close()
        finally:
            first.close()


def test_unauthorized_mutation_closes_connection(token_app: DaemonApp) -> None:
    # A mutation rejected with 401 must close the connection so its unread body
    # cannot desync the next request on a kept-alive connection.
    body = b'{"text":"hello"}'
    request = (
        "POST /input/text HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    with running_server(token_app) as (host, port):
        status, headers, _ = _send_raw(host, port, request)
    assert status == 401
    assert headers.get("connection", "").lower() == "close"
