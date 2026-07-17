#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  else
    echo "ERROR: python not found" >&2
    exit 1
  fi
fi

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-stream-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
PORT=41791
BASE_URL="http://127.0.0.1:$PORT"
DAEMON_PID=""

cleanup() {
  if [ -n "${DAEMON_PID:-}" ]; then
    if kill -0 "$DAEMON_PID" >/dev/null 2>&1; then
      kill "$DAEMON_PID" >/dev/null 2>&1 || true
      wait "$DAEMON_PID" >/dev/null 2>&1 || true
    fi
  fi

  if [ "${SMOKE_KEEP_ARTIFACTS:-0}" = "1" ]; then
    echo "Keeping smoke directory: $SMOKE_DIR"
  else
    rm -rf "$SMOKE_DIR"
  fi
}
trap cleanup EXIT HUP INT TERM

# security.api_token_required stays at the fail-closed default (true): this
# smoke proves the /stream handshake refuses tokenless clients.
cat >"$CONFIG" <<EOF
[daemon]
name = "dand"
host = "127.0.0.1"
port = $PORT
log_level = "INFO"

[database]
path = "$DB_PATH"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "mock"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[memory]
enabled = true
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true

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
api_base_url = "$BASE_URL"
width = 420
height = 620

[security]
localhost_only = true
require_approval_for_shell = true
require_approval_for_file_write = true
require_approval_for_network = true
destructive_tools_enabled = false

[runtime]
home = "$SMOKE_DIR/home"
logs_dir = "$SMOKE_DIR/logs"
runtime_dir = "$SMOKE_DIR/runtime"
pid_file = "$SMOKE_DIR/runtime/dand.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.dan.dand.smoke"
install_automatically = false
EOF

echo "Smoke directory: $SMOKE_DIR"
echo "Config: $CONFIG"
echo "Starting daemon: python -m dan.cli --config \"$CONFIG\" daemon run"
"$PYTHON" -m dan.cli --config "$CONFIG" daemon run >"$SMOKE_DIR/daemon.stdout.log" 2>"$SMOKE_DIR/daemon.stderr.log" &
DAEMON_PID="$!"
echo "Daemon PID: $DAEMON_PID"

BASE_URL="$BASE_URL" PORT="$PORT" DB_PATH="$DB_PATH" DAEMON_PID="$DAEMON_PID" SMOKE_DIR="$SMOKE_DIR" "$PYTHON" <<'PY'
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base_url = os.environ["BASE_URL"]
port = int(os.environ["PORT"])
smoke_dir = os.environ["SMOKE_DIR"]
daemon_pid = os.environ["DAEMON_PID"]

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_KEY = "c21va2Utc3RyZWFtLWtleQ=="
MASK = b"\x5a\xa5\x3c\xc3"
SECRET_MARKER = "sk-streamsmokesecret12345"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    for label, name in (("daemon stdout", "daemon.stdout.log"), ("daemon stderr", "daemon.stderr.log")):
        path = os.path.join(smoke_dir, name)
        if os.path.exists(path):
            print(f"--- {label} ---", file=sys.stderr)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                print(handle.read()[-4000:], file=sys.stderr)
    raise SystemExit(1)


def api_token() -> str:
    token_path = os.path.join(smoke_dir, "runtime", "api-token")
    with open(token_path, "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        fail("api token file is empty")
    return token


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if method in {"POST", "PATCH", "DELETE"}:
        headers["X-DAN-Token"] = api_token()
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        fail(f"{method} {path} returned HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}")
    except (TimeoutError, URLError, OSError) as exc:
        fail(f"{method} {path} failed: {exc}")


class StreamClient:
    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buffer = bytearray()
        self.status = None
        self.headers = {}

    def handshake(self, *, token=None, subprotocols=None):
        lines = [
            "GET /stream HTTP/1.1",
            "Host: 127.0.0.1",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {WS_KEY}",
            "Sec-WebSocket-Version: 13",
        ]
        if token is not None:
            lines.append(f"X-DAN-Token: {token}")
        if subprotocols:
            lines.append(f"Sec-WebSocket-Protocol: {', '.join(subprotocols)}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        head, _, rest = raw.partition(b"\r\n\r\n")
        head_lines = head.decode("iso-8859-1").split("\r\n")
        self.status = int(head_lines[0].split(" ")[1])
        for line in head_lines[1:]:
            name, _, value = line.partition(":")
            self.headers[name.strip().lower()] = value.strip()
        self.buffer.extend(rest)
        return self.status

    def recv_frame(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while True:
            frame = self._parse()
            if frame is not None:
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("timed out waiting for a websocket frame")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                fail("server closed the stream unexpectedly")
            self.buffer.extend(chunk)

    def _parse(self):
        if len(self.buffer) < 2:
            return None
        opcode = self.buffer[0] & 0x0F
        length = self.buffer[1] & 0x7F
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

    def recv_json(self, timeout=10.0):
        opcode, payload = self.recv_frame(timeout)
        if opcode != 0x1:
            fail(f"expected text frame, got opcode {opcode}")
        return json.loads(payload.decode("utf-8"))

    def wait_event(self, event_type, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.recv_json(timeout=deadline - time.monotonic())
            if frame.get("type") == "event" and frame["event"].get("type") == event_type:
                return frame["event"]
        fail(f"did not receive event {event_type} on the stream")

    def send_text(self, text):
        payload = text.encode("utf-8")
        header = bytes([0x81, 0x80 | len(payload)]) + MASK
        masked = bytes(b ^ MASK[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def expect_close(self, timeout=10.0):
        while True:
            opcode, payload = self.recv_frame(timeout)
            if opcode == 0x8:
                if len(payload) < 2:
                    fail("close frame without a status code")
                return struct.unpack("!H", payload[:2])[0]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


deadline = time.time() + 15
last_error = ""
while time.time() < deadline:
    try:
        request = Request(f"{base_url}/health", headers={"Accept": "application/json"})
        with urlopen(request, timeout=1) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("ok") is True and health.get("started") is True:
            break
        last_error = f"unhealthy response: {health}"
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.25)
else:
    fail(f"daemon health timeout: {last_error}")

# 1. Fail-closed handshake: no token and a wrong token both get 401.
no_token = StreamClient()
if no_token.handshake() != 401:
    fail(f"tokenless /stream handshake was not 401: {no_token.status}")
no_token.close()

bad_token = StreamClient()
if bad_token.handshake(token="not-the-real-token") != 401:
    fail(f"bad-token /stream handshake was not 401: {bad_token.status}")
bad_token.close()

# 2. Header-token handshake upgrades and says hello.
token = api_token()
client = StreamClient()
if client.handshake(token=token) != 101:
    fail(f"token /stream handshake was not 101: {client.status}")
expected_accept = base64.b64encode(hashlib.sha1((WS_KEY + WS_GUID).encode("ascii")).digest()).decode("ascii")
if client.headers.get("sec-websocket-accept") != expected_accept:
    fail(f"bad Sec-WebSocket-Accept: {client.headers}")
hello = client.recv_json()
if hello.get("type") != "stream.hello":
    fail(f"first frame was not stream.hello: {hello}")

# 3. Browser-style handshake: token via subprotocol, dan.v1 echoed back.
sub_client = StreamClient()
if sub_client.handshake(subprotocols=["dan.v1", f"dan-token.{token}"]) != 101:
    fail(f"subprotocol /stream handshake was not 101: {sub_client.status}")
if sub_client.headers.get("sec-websocket-protocol") != "dan.v1":
    fail(f"subprotocol response header wrong: {sub_client.headers}")
sub_hello = sub_client.recv_json()
if sub_hello.get("type") != "stream.hello":
    fail(f"subprotocol first frame was not stream.hello: {sub_hello}")
sub_client.close()

# 4. Live push: an approval_probe request emits approval.created on the
#    stream, with the secret-looking purpose redacted.
probe = request_json(
    "POST",
    "/tools/request",
    {
        "tool_name": "approval_probe",
        "arguments": {"purpose": f"stream smoke {SECRET_MARKER}"},
        "requested_by": "manual_smoke",
    },
)
if probe.get("status") != "approval_required":
    fail(f"approval_probe did not require approval: {probe}")
approval_id = probe["approval_id"]

created_event = client.wait_event("approval.created")
created_text = json.dumps(created_event)
if SECRET_MARKER in created_text:
    fail(f"secret leaked on the stream: {created_text}")
if "[REDACTED]" not in created_text:
    fail(f"approval.created payload was not visibly redacted: {created_text}")
if created_event.get("payload", {}).get("approval_id") != approval_id:
    fail(f"approval.created did not reference the approval: {created_event}")

# 5. Approve + execute: the stream carries tool.finished without bulk output.
approve = request_json("POST", f"/approvals/{approval_id}/approve", {"reason": "stream smoke"})
if approve.get("approval", {}).get("status") != "approved":
    fail(f"approve failed: {approve}")
execute = request_json("POST", f"/approvals/{approval_id}/execute")
if execute.get("ok") is not True:
    fail(f"execute failed: {execute}")

finished_event = client.wait_event("tool.finished")
payload = finished_event.get("payload", {})
if "output" in payload:
    fail(f"tool.finished on the stream carried bulk output: {payload}")
if payload.get("output_omitted") is not True:
    fail(f"tool.finished on the stream missed output_omitted: {payload}")

# 6. Read-only enforcement: a client data frame closes the stream with 1003.
client.send_text('{"execute": "anything"}')
close_code = client.expect_close()
if close_code != 1003:
    fail(f"read-only violation close code was not 1003: {close_code}")
client.close()

print("Stream smoke passed")
print(f"smoke directory: {smoke_dir}")
print(f"daemon pid: {daemon_pid}")
print(f"approval id: {approval_id}")
print(f"read-only close code: {close_code}")
PY
