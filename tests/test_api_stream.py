"""WebSocket /stream contract tests (FAZA D3).

Design: docs/DECISIONS.md ADR-019. The stream is a read-only push of
persisted (already redacted) events. The handshake is fail-closed behind
the transport token; bulk tool output never rides the stream.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan.api.websocket import (
    CLOSE_POLICY_VIOLATION,
    CLOSE_UNSUPPORTED_DATA,
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    STREAM_SUBPROTOCOL,
    TOKEN_SUBPROTOCOL_PREFIX,
    FrameParser,
    WebSocketHandshakeError,
    encode_frame,
    extract_token_candidates,
    select_subprotocol,
    stream_event_dict,
    validate_websocket_upgrade,
    websocket_accept_key,
)
from dan.daemon.app import DaemonApp, create_daemon_app
from dan.daemon.lifecycle import build_server
from dan.events.models import Event
from dan.security.redaction import REDACTION_PLACEHOLDER
from dan.security.transport import API_TOKEN_HEADER
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import config_text
from tests.test_api_transport_token import token_required_config_text


RFC_SAMPLE_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
RFC_SAMPLE_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
CLIENT_MASK = b"\x11\x22\x33\x44"


# ---------------------------------------------------------------------------
# Unit: handshake helpers and frame codec
# ---------------------------------------------------------------------------


def test_accept_key_matches_rfc6455_sample_vector() -> None:
    assert websocket_accept_key(RFC_SAMPLE_KEY) == RFC_SAMPLE_ACCEPT


def _upgrade_headers(**overrides: str | None) -> dict[str, str]:
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": RFC_SAMPLE_KEY,
        "Sec-WebSocket-Version": "13",
    }
    for name, value in overrides.items():
        header_name = name.replace("_", "-")
        if value is None:
            headers.pop(header_name, None)
        else:
            headers[header_name] = value
    return headers


def test_validate_upgrade_returns_key_for_valid_request() -> None:
    assert validate_websocket_upgrade(_upgrade_headers()) == RFC_SAMPLE_KEY


@pytest.mark.parametrize(
    "overrides",
    [
        {"Upgrade": None},
        {"Upgrade": "h2c"},
        {"Connection": None},
        {"Connection": "keep-alive"},
        {"Sec_WebSocket_Key": None},
        {"Sec_WebSocket_Key": " "},
        {"Sec_WebSocket_Version": None},
        {"Sec_WebSocket_Version": "8"},
    ],
)
def test_validate_upgrade_rejects_malformed_requests(overrides: dict[str, str | None]) -> None:
    with pytest.raises(WebSocketHandshakeError):
        validate_websocket_upgrade(_upgrade_headers(**overrides))


def test_validate_upgrade_accepts_multi_value_connection_header() -> None:
    headers = _upgrade_headers(Connection="keep-alive, Upgrade")
    assert validate_websocket_upgrade(headers) == RFC_SAMPLE_KEY


def test_extract_token_candidates_reads_header_and_subprotocol() -> None:
    headers = {
        API_TOKEN_HEADER: "header-token",
        "Sec-WebSocket-Protocol": (
            f"{STREAM_SUBPROTOCOL}, {TOKEN_SUBPROTOCOL_PREFIX}sub-token"
        ),
    }
    candidates = extract_token_candidates(headers)
    assert "header-token" in candidates
    assert "sub-token" in candidates
    assert STREAM_SUBPROTOCOL not in candidates


def test_extract_token_candidates_is_empty_without_credentials() -> None:
    assert extract_token_candidates({}) == []
    assert extract_token_candidates({"Sec-WebSocket-Protocol": STREAM_SUBPROTOCOL}) == []


def test_select_subprotocol_echoes_stream_protocol_only_when_offered() -> None:
    offered = {"Sec-WebSocket-Protocol": f"{STREAM_SUBPROTOCOL}, {TOKEN_SUBPROTOCOL_PREFIX}x"}
    assert select_subprotocol(offered) == STREAM_SUBPROTOCOL
    assert select_subprotocol({"Sec-WebSocket-Protocol": f"{TOKEN_SUBPROTOCOL_PREFIX}x"}) is None
    assert select_subprotocol({}) is None


def _mask_payload(payload: bytes, mask: bytes = CLIENT_MASK) -> bytes:
    return bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


def client_frame(opcode: int, payload: bytes, *, masked: bool = True, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | opcode])
    length = len(payload)
    mask_bit = 0x80 if masked else 0
    if length <= 125:
        head += bytes([mask_bit | length])
    elif length <= 0xFFFF:
        head += bytes([mask_bit | 126]) + struct.pack("!H", length)
    else:
        head += bytes([mask_bit | 127]) + struct.pack("!Q", length)
    if masked:
        return head + CLIENT_MASK + _mask_payload(payload)
    return head + payload


def test_frame_parser_decodes_masked_client_frame() -> None:
    parser = FrameParser()
    frames = parser.feed(client_frame(OP_TEXT, b"hello"))
    assert [(frame.opcode, frame.payload) for frame in frames] == [(OP_TEXT, b"hello")]


def test_frame_parser_handles_partial_and_batched_input() -> None:
    parser = FrameParser()
    raw = client_frame(OP_TEXT, b"first") + client_frame(OP_PING, b"pi")
    assert parser.feed(raw[:3]) == []
    frames = parser.feed(raw[3:])
    assert [(frame.opcode, frame.payload) for frame in frames] == [
        (OP_TEXT, b"first"),
        (OP_PING, b"pi"),
    ]


def test_frame_parser_rejects_unmasked_client_frame() -> None:
    parser = FrameParser()
    with pytest.raises(WebSocketHandshakeError):
        parser.feed(client_frame(OP_TEXT, b"nope", masked=False))


def test_frame_parser_rejects_oversized_control_frame() -> None:
    parser = FrameParser()
    with pytest.raises(WebSocketHandshakeError):
        parser.feed(client_frame(OP_PING, b"x" * 126))


def test_encode_frame_roundtrips_through_client_view() -> None:
    frame = encode_frame(OP_TEXT, "żółty stream".encode("utf-8"))
    fin_opcode, length = frame[0], frame[1]
    assert fin_opcode == 0x80 | OP_TEXT
    assert length & 0x80 == 0  # server frames are never masked
    assert frame[2:] == "żółty stream".encode("utf-8")


def test_stream_event_dict_omits_bulk_output() -> None:
    event = Event(
        id=7,
        created_at="2026-07-02T20:00:00+00:00",
        type="tool.finished",
        source="tool_registry",
        correlation_id=None,
        turn_id=None,
        payload={"tool_name": "ui_read_window", "output": {"window": {"title": "secret"}}},
    )
    streamed = stream_event_dict(event)
    assert streamed["id"] == 7
    assert streamed["type"] == "tool.finished"
    assert streamed["payload"]["output"] == REDACTION_PLACEHOLDER
    assert streamed["payload"]["output_omitted"] is True
    assert streamed["payload"]["tool_name"] == "ui_read_window"


def test_stream_event_dict_adds_bounded_safe_tool_result_summary() -> None:
    secret_value = "screen-window-log-output-that-must-never-reach-the-panel"
    event = Event(
        id=9,
        created_at="2026-07-02T20:00:00+00:00",
        type="tool.finished",
        source="tool_registry",
        correlation_id="corr-9",
        turn_id="turn-9",
        payload={
            "tool_name": "ui_read_window",
            "screen": secret_value,
            "window": {"title": secret_value},
            "log": secret_value,
            "logs": [secret_value],
            "content": secret_value,
            "output": {
                "ok": True,
                "clicked": True,
                "chars_typed": 27,
                "line_count": 9,
                "truncated": False,
                "size_bytes": int("9" * 300),
                "screen": {"text": secret_value},
                "window": {"title": secret_value},
                "log": secret_value,
                "logs": [secret_value],
                "stdout": secret_value,
                "stderr": secret_value,
                "content": secret_value,
            },
        },
    )

    streamed = stream_event_dict(event)
    summary = streamed["payload"]["result_summary"]

    assert isinstance(summary, str)
    assert len(summary) <= 160
    assert "ok=true" in summary
    assert "clicked=true" in summary
    assert "chars_typed=27" in summary
    assert "line_count=9" in summary
    assert "truncated=false" in summary
    assert secret_value not in summary
    assert secret_value not in json.dumps(streamed)
    for forbidden in ("screen", "window", "log", "stdout", "stderr", "content"):
        assert forbidden not in summary.lower()


def test_stream_event_dict_never_trusts_stored_tool_result_summary() -> None:
    secret_value = "stored-summary-must-not-reach-the-panel"
    event = Event(
        id=10,
        created_at="2026-07-02T20:00:00+00:00",
        type="tool.finished",
        source="tool_registry",
        correlation_id=None,
        turn_id=None,
        payload={
            "tool_name": "ui_click",
            "result_summary": secret_value,
            "output": {"message": secret_value},
        },
    )

    streamed = stream_event_dict(event)

    assert "result_summary" not in streamed["payload"]
    assert secret_value not in json.dumps(streamed)


def test_stream_event_dict_redacts_secrets_defensively() -> None:
    event = Event(
        id=8,
        created_at="2026-07-02T20:00:00+00:00",
        type="error.raised",
        source="test",
        correlation_id=None,
        turn_id=None,
        payload={"detail": "leaked sk-abc123DEF456ghi789 value"},
    )
    streamed = stream_event_dict(event)
    assert "sk-abc123DEF456ghi789" not in json.dumps(streamed)


# ---------------------------------------------------------------------------
# Integration: daemon server with the fail-closed token default
# ---------------------------------------------------------------------------


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "dan.toml"
    path.write_text(
        token_required_config_text(tmp_path / "home" / "dan.db"),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def app(config_path: Path) -> Iterator[DaemonApp]:
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


@pytest.fixture
def server_address(app: DaemonApp) -> Iterator[tuple[str, int]]:
    server = build_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, name="dan-stream-http", daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class StreamClient:
    """Minimal RFC 6455 client for exercising the server implementation."""

    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.parser = _ServerFrameParser()
        self.status: int | None = None
        self.headers: dict[str, str] = {}

    def handshake(
        self,
        *,
        token: str | None = None,
        subprotocols: list[str] | None = None,
        path: str = "/stream",
        upgrade: bool = True,
    ) -> int:
        lines = [f"GET {path} HTTP/1.1", "Host: 127.0.0.1"]
        if upgrade:
            lines += [
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {RFC_SAMPLE_KEY}",
                "Sec-WebSocket-Version: 13",
            ]
        if token is not None:
            lines.append(f"{API_TOKEN_HEADER}: {token}")
        if subprotocols:
            lines.append(f"Sec-WebSocket-Protocol: {', '.join(subprotocols)}")
        request = "\r\n".join(lines) + "\r\n\r\n"
        self.sock.sendall(request.encode("utf-8"))

        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        head, _, rest = raw.partition(b"\r\n\r\n")
        status_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
        self.status = int(status_line.split(" ")[1])
        for line in header_lines:
            name, _, value = line.partition(":")
            self.headers[name.strip().lower()] = value.strip()
        if rest:
            self.parser.buffer.extend(rest)
        return self.status

    def recv_frame(self, timeout: float = 5.0) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        while True:
            frame = self.parser.next_frame()
            if frame is not None:
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("timed out waiting for a websocket frame")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("server closed the connection unexpectedly")
            self.parser.buffer.extend(chunk)

    def recv_json(self, timeout: float = 5.0) -> dict:
        opcode, payload = self.recv_frame(timeout)
        assert opcode == OP_TEXT, f"expected text frame, got opcode {opcode}"
        return json.loads(payload.decode("utf-8"))

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def expect_close(self, timeout: float = 5.0) -> int:
        while True:
            opcode, payload = self.recv_frame(timeout)
            if opcode == OP_CLOSE:
                assert len(payload) >= 2
                return struct.unpack("!H", payload[:2])[0]

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _ServerFrameParser:
    """Parses unmasked server frames on the client side."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def next_frame(self) -> tuple[int, bytes] | None:
        if len(self.buffer) < 2:
            return None
        opcode = self.buffer[0] & 0x0F
        length = self.buffer[1] & 0x7F
        assert self.buffer[1] & 0x80 == 0, "server frames must not be masked"
        offset = 2
        if length == 126:
            if len(self.buffer) < 4:
                return None
            length = struct.unpack("!H", bytes(self.buffer[2:4]))[0]
            offset = 4
        elif length == 127:
            if len(self.buffer) < 10:
                return None
            length = struct.unpack("!Q", bytes(self.buffer[2:10]))[0]
            offset = 10
        if len(self.buffer) < offset + length:
            return None
        payload = bytes(self.buffer[offset : offset + length])
        del self.buffer[: offset + length]
        return opcode, payload


@pytest.fixture
def stream_client(server_address: tuple[str, int]) -> Iterator[StreamClient]:
    clients: list[StreamClient] = []

    def factory() -> StreamClient:
        client = StreamClient(*server_address)
        clients.append(client)
        return client

    yield factory  # type: ignore[misc]
    for client in clients:
        client.close()


def test_stream_without_token_is_unauthorized(stream_client) -> None:
    client = stream_client()
    assert client.handshake() == 401


def test_stream_with_wrong_token_is_unauthorized(stream_client) -> None:
    client = stream_client()
    assert client.handshake(token="not-the-token") == 401


def test_stream_without_upgrade_headers_is_bad_request(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token, upgrade=False) == 400


def test_stream_rejects_invalid_after_id(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token, path="/stream?after_id=nope") == 400
    negative = stream_client()
    assert negative.handshake(token=app.api_token, path="/stream?after_id=-3") == 400


def test_stream_handshake_with_header_token_upgrades(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    assert client.headers["upgrade"].lower() == "websocket"
    assert client.headers["sec-websocket-accept"] == websocket_accept_key(RFC_SAMPLE_KEY)

    hello = client.recv_json()
    assert hello["type"] == "stream.hello"
    assert hello["latest_event_id"] >= 1
    assert hello["start_after_id"] == hello["latest_event_id"]


def test_stream_handshake_with_subprotocol_token_upgrades(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    status = client.handshake(
        subprotocols=[STREAM_SUBPROTOCOL, f"{TOKEN_SUBPROTOCOL_PREFIX}{app.api_token}"],
    )
    assert status == 101
    assert client.headers["sec-websocket-protocol"] == STREAM_SUBPROTOCOL
    assert client.recv_json()["type"] == "stream.hello"


def test_stream_replays_events_after_requested_id(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token, path="/stream?after_id=0") == 101
    hello = client.recv_json()
    assert hello["start_after_id"] == 0

    first = client.recv_json()
    assert first["type"] == "event"
    assert first["event"]["type"] == "daemon.started"
    assert first["event"]["id"] >= 1


def test_stream_survives_idle_polling_then_pushes(app: DaemonApp, stream_client) -> None:
    """Regression: idle poll cycles must not poison the connection.

    Reading the handler's rfile under a socket timeout marks SocketIO as
    timed out forever ("cannot read from timed out object"), which killed
    the stream after the first idle interval. The session must stay alive
    across many idle intervals and still deliver later events.
    """

    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    client.recv_json()  # hello

    # Wait through several poll intervals with no traffic in either direction.
    time.sleep(1.2)

    appended = app.event_store.append("error.raised", "test", {"detail": "after idle"})
    frame = client.recv_json()
    assert frame["event"]["id"] == appended.id
    assert frame["event"]["payload"]["detail"] == "after idle"


def test_stream_pushes_live_events(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    client.recv_json()  # hello

    appended = app.event_store.append("error.raised", "test", {"detail": "live push"})

    frame = client.recv_json()
    assert frame["type"] == "event"
    assert frame["event"]["id"] == appended.id
    assert frame["event"]["payload"]["detail"] == "live push"


def test_stream_preserves_event_order_across_replay_and_live(
    app: DaemonApp, stream_client
) -> None:
    for index in range(3):
        app.event_store.append("error.raised", "test", {"detail": f"pre-{index}"})

    client = stream_client()
    assert client.handshake(token=app.api_token, path="/stream?after_id=0") == 101
    client.recv_json()  # hello

    app.event_store.append("error.raised", "test", {"detail": "post"})

    seen_ids: list[int] = []
    details: list[str] = []
    deadline = time.monotonic() + 10
    while "post" not in details and time.monotonic() < deadline:
        frame = client.recv_json()
        seen_ids.append(frame["event"]["id"])
        details.append(str(frame["event"]["payload"].get("detail")))
    assert "post" in details
    assert seen_ids == sorted(seen_ids)


def test_stream_omits_tool_output_payloads(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    client.recv_json()  # hello

    app.event_store.append(
        "tool.finished",
        "tool_registry",
        {"tool_name": "ui_read_window", "output": {"window": {"title": "screen text"}}},
    )

    frame = client.recv_json()
    payload = frame["event"]["payload"]
    assert payload["output"] == REDACTION_PLACEHOLDER
    assert payload["output_omitted"] is True
    assert "screen text" not in json.dumps(frame)


def test_stream_redacts_secrets_even_for_raw_db_rows(app: DaemonApp, stream_client) -> None:
    # Bypass EventStore.append (which redacts at write time) to prove the
    # stream layer redacts independently.
    assert app.conn is not None
    with app.conn:
        app.conn.execute(
            """
            INSERT INTO events (created_at, type, source, correlation_id, turn_id, payload_json)
            VALUES ('2026-07-02T20:00:00+00:00', 'error.raised', 'test', NULL, NULL, ?)
            """,
            (json.dumps({"detail": "raw sk-rawsecret123456 leak"}),),
        )

    client = stream_client()
    assert client.handshake(token=app.api_token, path="/stream?after_id=0") == 101
    client.recv_json()  # hello

    found = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        frame = client.recv_json()
        text = json.dumps(frame)
        assert "sk-rawsecret123456" not in text
        if frame["event"]["payload"].get("detail", "").startswith("raw "):
            found = True
            break
    assert found


def test_stream_is_read_only_and_closes_on_client_data(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    client.recv_json()  # hello

    client.send_raw(client_frame(OP_TEXT, b'{"execute": "anything"}'))
    assert client.expect_close() == CLOSE_UNSUPPORTED_DATA


def test_stream_closes_on_unmasked_client_frame(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    client.recv_json()  # hello

    client.send_raw(client_frame(OP_TEXT, b"nope", masked=False))
    assert client.expect_close() == CLOSE_POLICY_VIOLATION


def test_stream_answers_client_ping_with_pong(app: DaemonApp, stream_client) -> None:
    client = stream_client()
    assert client.handshake(token=app.api_token) == 101
    client.recv_json()  # hello

    client.send_raw(client_frame(OP_PING, b"probe"))
    opcode, payload = client.recv_frame()
    assert opcode == OP_PONG
    assert payload == b"probe"


def test_stream_allows_tokenless_connect_when_token_not_required(tmp_path: Path) -> None:
    config = tmp_path / "dan.toml"
    config.write_text(config_text(tmp_path / "home" / "dan.db"), encoding="utf-8")
    daemon_app = create_daemon_app(config)
    daemon_app.start()
    server = build_server(daemon_app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = StreamClient(*server.server_address)
    try:
        assert client.handshake() == 101
        assert client.recv_json()["type"] == "stream.hello"
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        daemon_app.stop(reason="test teardown")
        daemon_app.close()


def test_schema_and_migrations_are_unchanged() -> None:
    ROOT = Path(__file__).resolve().parents[1]
    assert_schema_and_migrations_unchanged(ROOT)
