"""Read-only WebSocket event stream for the local Jarvis daemon (RFC 6455).

Design: docs/DECISIONS.md ADR-019. The stream pushes persisted events to the
cockpit; it never mutates state. The handshake sits behind the transport
token (fail-closed), server frames are never masked, and any client data
frame closes the connection — approvals and actions stay on the POST
endpoints.

Implemented on the stdlib socket owned by ``ThreadingHTTPServer`` so the
zero-runtime-dependency rule holds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import select
import socket
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Any

from jarvis.logging import get_logger
from jarvis.security.redaction import redact_secrets
from jarvis.security.transport import API_TOKEN_HEADER
from jarvis.store.db import close_quietly, connect_db
from jarvis.store.event_store import create_event_store

if TYPE_CHECKING:
    from jarvis.daemon.app import DaemonApp
    from jarvis.events.models import Event


_WS_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

CLOSE_NORMAL = 1000
CLOSE_POLICY_VIOLATION = 1002
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_MESSAGE_TOO_BIG = 1009

STREAM_SUBPROTOCOL = "jarvis.v1"
TOKEN_SUBPROTOCOL_PREFIX = "jarvis-token."
MAX_TOKEN_CANDIDATES = 8

# The stream tails the append-only events table; the poll interval bounds
# push latency, the batch limit bounds a single replay burst.
STREAM_POLL_INTERVAL_SECONDS = 0.25
STREAM_BATCH_LIMIT = 200
STREAM_PING_INTERVAL_SECONDS = 20.0
MAX_CLIENT_BUFFER_BYTES = 65536
MAX_CONTROL_PAYLOAD_BYTES = 125


class WebSocketHandshakeError(Exception):
    """Raised when an HTTP request cannot be upgraded to a websocket."""


class WebSocketProtocolError(Exception):
    """Raised when a connected client violates the framing protocol."""

    def __init__(self, close_code: int, message: str):
        super().__init__(message)
        self.close_code = close_code


def websocket_accept_key(key: str) -> str:
    """Compute the Sec-WebSocket-Accept value for a client key (RFC 6455 §4.2.2)."""

    digest = hashlib.sha1((key + _WS_ACCEPT_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_websocket_upgrade(headers: Mapping[str, Any]) -> str:
    """Validate upgrade headers and return the Sec-WebSocket-Key, fail-closed."""

    upgrade = str(headers.get("Upgrade") or "").strip().lower()
    if upgrade != "websocket":
        raise WebSocketHandshakeError("WebSocket upgrade required (Upgrade: websocket).")

    connection = str(headers.get("Connection") or "")
    connection_tokens = {part.strip().lower() for part in connection.split(",")}
    if "upgrade" not in connection_tokens:
        raise WebSocketHandshakeError("Connection header must include 'Upgrade'.")

    version = str(headers.get("Sec-WebSocket-Version") or "").strip()
    if version != "13":
        raise WebSocketHandshakeError("Unsupported Sec-WebSocket-Version; only 13 is supported.")

    key = str(headers.get("Sec-WebSocket-Key") or "").strip()
    if not key:
        raise WebSocketHandshakeError("Sec-WebSocket-Key is required.")
    return key


def _offered_subprotocols(headers: Mapping[str, Any]) -> list[str]:
    raw = headers.get("Sec-WebSocket-Protocol")
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    else:  # pragma: no cover - Message.get returns str; defensive only.
        values = [str(raw)]
    offered: list[str] = []
    for value in values:
        for part in value.split(","):
            normalized = part.strip()
            if normalized:
                offered.append(normalized)
    return offered


def extract_token_candidates(headers: Mapping[str, Any]) -> list[str]:
    """Collect transport-token candidates from the handshake request.

    Browsers cannot set custom headers on WebSocket connects, so the cockpit
    smuggles the token as a ``jarvis-token.<token>`` subprotocol entry;
    CLI/tests use the regular header. Candidates are capped so a hostile
    header cannot force unbounded comparisons.
    """

    candidates: list[str] = []
    header_token = headers.get(API_TOKEN_HEADER)
    if isinstance(header_token, str) and header_token.strip():
        candidates.append(header_token.strip())
    for protocol in _offered_subprotocols(headers):
        if protocol.startswith(TOKEN_SUBPROTOCOL_PREFIX):
            token = protocol[len(TOKEN_SUBPROTOCOL_PREFIX) :].strip()
            if token:
                candidates.append(token)
    return candidates[:MAX_TOKEN_CANDIDATES]


def select_subprotocol(headers: Mapping[str, Any]) -> str | None:
    """Echo the stream subprotocol iff the client offered it; never the token."""

    if STREAM_SUBPROTOCOL in _offered_subprotocols(headers):
        return STREAM_SUBPROTOCOL
    return None


def encode_frame(opcode: int, payload: bytes) -> bytes:
    """Encode a single unfragmented, unmasked server frame."""

    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length <= 125:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


def encode_text(text: str) -> bytes:
    return encode_frame(OP_TEXT, text.encode("utf-8"))


def encode_close(code: int, reason: str = "") -> bytes:
    payload = struct.pack("!H", code) + reason.encode("utf-8")[:MAX_CONTROL_PAYLOAD_BYTES - 2]
    return encode_frame(OP_CLOSE, payload)


@dataclass(frozen=True)
class Frame:
    opcode: int
    payload: bytes
    fin: bool


class FrameParser:
    """Incremental parser for client frames (which must be masked)."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        if len(self._buffer) > MAX_CLIENT_BUFFER_BYTES:
            raise WebSocketHandshakeError("Client frame exceeds the stream buffer limit.")
        frames: list[Frame] = []
        while True:
            frame = self._parse_one()
            if frame is None:
                return frames
            frames.append(frame)

    def _parse_one(self) -> Frame | None:
        buffer = self._buffer
        if len(buffer) < 2:
            return None
        first, second = buffer[0], buffer[1]
        if first & 0x70:
            raise WebSocketHandshakeError("Reserved websocket bits must be zero.")
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        if not masked:
            raise WebSocketHandshakeError("Client frames must be masked (RFC 6455 §5.1).")

        length = second & 0x7F
        offset = 2
        if length == 126:
            if len(buffer) < 4:
                return None
            length = struct.unpack("!H", bytes(buffer[2:4]))[0]
            offset = 4
        elif length == 127:
            if len(buffer) < 10:
                return None
            length = struct.unpack("!Q", bytes(buffer[2:10]))[0]
            offset = 10

        if opcode >= OP_CLOSE and (length > MAX_CONTROL_PAYLOAD_BYTES or not fin):
            raise WebSocketHandshakeError("Control frames must be short and unfragmented.")
        if length > MAX_CLIENT_BUFFER_BYTES:
            raise WebSocketHandshakeError("Client frame exceeds the stream buffer limit.")

        total = offset + 4 + length
        if len(buffer) < total:
            return None
        mask = bytes(buffer[offset : offset + 4])
        raw = bytes(buffer[offset + 4 : total])
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        del buffer[:total]
        return Frame(opcode=opcode, payload=payload, fin=fin)


def stream_event_dict(event: Event) -> dict[str, Any]:
    """Shape a persisted event for the stream.

    Two deliberate deviations from ``GET /events`` (ADR-019):
    - bulk tool output never rides the stream (``ui_read_window`` output is
      on-screen text); the payload carries ``output_omitted`` instead, and
    - payloads pass through ``redact_secrets`` again, so the stream stays
      redacted even for rows that reached the DB outside EventStore.append.
    """

    payload = dict(event.payload)
    if "output" in payload:
        payload.pop("output")
        payload["output_omitted"] = True
    return {
        "id": event.id,
        "created_at": event.created_at,
        "type": event.type,
        "source": event.source,
        "correlation_id": event.correlation_id,
        "turn_id": event.turn_id,
        "payload": redact_secrets(payload),
    }


class EventStreamSession:
    """Tails the events table over one accepted websocket connection.

    Runs on the request-handler thread that ``ThreadingHTTPServer`` already
    dedicates to the connection. Reads client bytes through the handler's
    buffered ``rfile`` (which may hold pipelined frames) and writes frames
    with ``socket.sendall``.
    """

    def __init__(
        self,
        connection: socket.socket,
        rfile: IO[bytes],
        app: DaemonApp,
        *,
        after_id: int | None,
    ):
        self._connection = connection
        self._rfile = rfile
        self._app = app
        self._after_id = after_id
        self._parser = FrameParser()
        self._logger = get_logger(__name__)

    def run(self) -> None:
        conn = connect_db(self._app.paths.db_path)
        try:
            store = create_event_store(conn)
            latest = store.latest(1)
            latest_id = latest[0].id if latest else 0
            last_id = self._after_id if self._after_id is not None else latest_id

            if not self._consume_handshake_leftovers():
                return
            self._send(
                encode_text(
                    _dumps(
                        {
                            "type": "stream.hello",
                            "latest_event_id": latest_id,
                            "start_after_id": last_id,
                        }
                    )
                )
            )

            last_ping = time.monotonic()
            while True:
                last_id = self._push_new_events(store, last_id)
                if not self._drain_client_input():
                    return
                if time.monotonic() - last_ping >= STREAM_PING_INTERVAL_SECONDS:
                    self._send(encode_frame(OP_PING, b"jarvisd"))
                    last_ping = time.monotonic()
        except WebSocketProtocolError as exc:
            self._close(exc.close_code, str(exc))
        except (OSError, ValueError):
            # Peer went away mid-write/read; nothing to clean beyond the DB handle.
            return
        finally:
            close_quietly(conn)

    def _push_new_events(self, store: Any, last_id: int) -> int:
        while True:
            events = store.list_after(last_id, limit=STREAM_BATCH_LIMIT)
            for event in events:
                self._send(encode_text(_dumps({"type": "event", "event": stream_event_dict(event)})))
                last_id = event.id
            if len(events) < STREAM_BATCH_LIMIT:
                return last_id

    def _consume_handshake_leftovers(self) -> bool:
        """Drain bytes the HTTP header reader over-buffered, without blocking.

        A conforming client sends nothing before our 101 (RFC 6455 §4.1), but
        anything that did arrive sits inside the handler's BufferedReader,
        invisible to select() on the raw socket. Reading rfile under a socket
        timeout is not an option: one timeout permanently poisons SocketIO
        ("cannot read from timed out object"), so the buffer is emptied here
        with a non-blocking read and rfile is never touched again.
        """

        self._connection.settimeout(0.0)
        try:
            while True:
                try:
                    data = self._rfile.read1(4096)
                except BlockingIOError:
                    return True
                if not data:
                    # Either the buffer is drained (would-block) or the peer
                    # is already gone; the select() loop settles which.
                    return True
                if not self._handle_client_bytes(data):
                    return False
        finally:
            self._connection.settimeout(None)

    def _drain_client_input(self) -> bool:
        """Read pending client bytes; returns False when the session should end."""

        ready, _, _ = select.select([self._connection], [], [], STREAM_POLL_INTERVAL_SECONDS)
        if not ready:
            return True
        chunk = self._connection.recv(4096)
        if chunk == b"":
            return False
        return self._handle_client_bytes(chunk)

    def _handle_client_bytes(self, chunk: bytes) -> bool:
        try:
            frames = self._parser.feed(chunk)
        except WebSocketHandshakeError as exc:
            raise WebSocketProtocolError(CLOSE_POLICY_VIOLATION, str(exc)) from exc

        for frame in frames:
            if frame.opcode == OP_CLOSE:
                self._close(CLOSE_NORMAL, "bye")
                return False
            if frame.opcode == OP_PING:
                self._send(encode_frame(OP_PONG, frame.payload))
                continue
            if frame.opcode == OP_PONG:
                continue
            # TEXT/BINARY/CONTINUATION: the stream is read-only by design
            # (ADR-019) — mutations stay on the POST endpoints.
            raise WebSocketProtocolError(
                CLOSE_UNSUPPORTED_DATA,
                "The event stream is read-only; use the HTTP API for actions.",
            )
        return True

    def _send(self, frame: bytes) -> None:
        self._connection.sendall(frame)

    def _close(self, code: int, reason: str) -> None:
        try:
            self._send(encode_close(code, reason))
        except OSError:
            pass


def _dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "CLOSE_NORMAL",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_UNSUPPORTED_DATA",
    "EventStreamSession",
    "Frame",
    "FrameParser",
    "MAX_CLIENT_BUFFER_BYTES",
    "OP_BINARY",
    "OP_CLOSE",
    "OP_CONTINUATION",
    "OP_PING",
    "OP_PONG",
    "OP_TEXT",
    "STREAM_BATCH_LIMIT",
    "STREAM_PING_INTERVAL_SECONDS",
    "STREAM_POLL_INTERVAL_SECONDS",
    "STREAM_SUBPROTOCOL",
    "TOKEN_SUBPROTOCOL_PREFIX",
    "WebSocketHandshakeError",
    "WebSocketProtocolError",
    "encode_close",
    "encode_frame",
    "encode_text",
    "extract_token_candidates",
    "select_subprotocol",
    "stream_event_dict",
    "validate_websocket_upgrade",
    "websocket_accept_key",
]
