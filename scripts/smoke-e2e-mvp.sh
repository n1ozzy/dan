#!/usr/bin/env bash
set -euo pipefail

# F1 e2e MVP smoke: one temporary daemon walks the operator acceptance
# scenario from MASTER_PLAN §6 end to end. Fake CLI brain + real repo tools,
# no providers, no network beyond localhost. See docs/runbooks/E2E_MVP_SMOKE.md
# for the criterion-by-criterion map.

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

HOST="127.0.0.1"
PORT="41799"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-e2e-mvp-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
FAKE_BRAIN="$SMOKE_DIR/fake-brain.sh"
PROMPT_DUMP="$SMOKE_DIR/fake-brain-prompt.txt"
WORKSPACE="$SMOKE_DIR/workspace"
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

"$PYTHON" - "$HOST" "$PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    if sock.connect_ex((host, port)) == 0:
        raise SystemExit(f"Port already in use: {host}:{port}")
PY

mkdir -p "$WORKSPACE"
# The note holds a secret so every read proves at-rest redaction (§6.13).
cat >"$WORKSPACE/secret-note.txt" <<'NOTE'
operator note with a secret: sk-e2emvpsecret1234567890
NOTE

# Deterministic fake local CLI brain:
# - continuation prompt -> plain continuation answer,
# - E2E_TOOL_TURN in the latest input -> model-originated file_read tool call,
# - anything else -> plain answer. Every call dumps the full prompt so the
# smoke can prove history survives brain switches and restarts.
cat >"$FAKE_BRAIN" <<FAKE
#!/usr/bin/env bash
set -euo pipefail
PROMPT="\$(cat)"
printf '%s' "\$PROMPT" >"$PROMPT_DUMP"
if printf '%s' "\$PROMPT" | grep -q "Continuation after approved tool execution"; then
  printf 'E2E continuation: approved tool result received.\n'
elif printf '%s' "\$PROMPT" | tail -n 6 | grep -q "E2E_TOOL_TURN"; then
  printf 'Need to read the secret note.\n'
  printf '<dan_tool_call>{"name":"file_read","arguments":{"path":"$WORKSPACE/secret-note.txt"}}</dan_tool_call>\n'
else
  printf 'E2E plain answer.\n'
fi
FAKE
chmod +x "$FAKE_BRAIN"

cat >"$CONFIG" <<EOF
[daemon]
name = "dand"
host = "$HOST"
port = $PORT
log_level = "INFO"

[database]
path = "$DB_PATH"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "claude_cli"
default_model = "fake-brain"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[brain.claude_cli]
enabled = true
command = "$FAKE_BRAIN"
args = []
model = "fake-brain"
timeout_seconds = 30

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
approved_roots = ["$WORKSPACE"]

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

start_daemon() {
  "$PYTHON" -m dan.cli --config "$CONFIG" daemon run >>"$SMOKE_DIR/daemon.stdout.log" 2>>"$SMOKE_DIR/daemon.stderr.log" &
  DAEMON_PID="$!"
  echo "Daemon PID: $DAEMON_PID"
}

stop_daemon() {
  if [ -n "${DAEMON_PID:-}" ] && kill -0 "$DAEMON_PID" >/dev/null 2>&1; then
    kill "$DAEMON_PID" >/dev/null 2>&1 || true
    wait "$DAEMON_PID" >/dev/null 2>&1 || true
  fi
  DAEMON_PID=""
}

echo "Smoke directory: $SMOKE_DIR"
echo "Config: $CONFIG"

start_daemon

BASE_URL="$BASE_URL" PORT="$PORT" DB_PATH="$DB_PATH" SMOKE_DIR="$SMOKE_DIR" \
PROMPT_DUMP="$PROMPT_DUMP" WORKSPACE="$WORKSPACE" "$PYTHON" <<'PY'
import base64
import hashlib
import json
import os
import sqlite3
import struct
import socket as socket_module
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base_url = os.environ["BASE_URL"]
port = int(os.environ["PORT"])
db_path = os.environ["DB_PATH"]
smoke_dir = os.environ["SMOKE_DIR"]
prompt_dump = os.environ["PROMPT_DUMP"]
workspace = os.environ["WORKSPACE"]

SECRET = "sk-e2emvpsecret1234567890"
HISTORY_MARKER = "E2E_HISTORY_MARKER_F1"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_KEY = base64.b64encode(b"e2e-mvp-smoke-ws").decode("ascii")


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
    with open(os.path.join(smoke_dir, "runtime", "api-token"), "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        fail("api token file is empty")
    return token


def request_json_status(method, path, payload=None, *, with_token=True, timeout=30):
    headers = {"Accept": "application/json"}
    if with_token and method in {"POST", "PATCH", "DELETE"}:
        headers["X-DAN-Token"] = api_token()
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": str(exc)}
        return exc.code, body
    except (TimeoutError, URLError, OSError) as exc:
        fail(f"{method} {path} failed: {exc}")


def request_json(method, path, payload=None, timeout=30):
    status, body = request_json_status(method, path, payload, timeout=timeout)
    if status >= 400:
        fail(f"{method} {path} returned HTTP {status}: {body}")
    return body


def wait_healthy(label: str) -> None:
    deadline = time.time() + 15
    last_error = ""
    while time.time() < deadline:
        try:
            request = Request(f"{base_url}/health", headers={"Accept": "application/json"})
            with urlopen(request, timeout=1) as response:
                health = json.loads(response.read().decode("utf-8"))
            if health.get("ok") is True and health.get("started") is True:
                return
            last_error = f"unhealthy response: {health}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    fail(f"daemon health timeout ({label}): {last_error}")


class StreamClient:
    """Minimal read-only websocket client (pattern from smoke-stream.sh)."""

    def __init__(self):
        self.sock = socket_module.create_connection(("127.0.0.1", port), timeout=5)
        self.buffer = bytearray()

    def handshake(self, after_id=None):
        path = "/stream" if after_id is None else f"/stream?after_id={after_id}"
        lines = [
            f"GET {path} HTTP/1.1",
            "Host: 127.0.0.1",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {WS_KEY}",
            "Sec-WebSocket-Version: 13",
            f"X-DAN-Token: {api_token()}",
            "Sec-WebSocket-Protocol: dan.v1",
        ]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        head, _, rest = raw.partition(b"\r\n\r\n")
        status = int(head.decode("iso-8859-1").split("\r\n")[0].split(" ")[1])
        self.buffer.extend(rest)
        expected = base64.b64encode(
            hashlib.sha1((WS_KEY + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if expected not in head.decode("iso-8859-1"):
            fail("websocket accept key mismatch")
        return status

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
        deadline = time.monotonic() + timeout
        while True:
            frame = self._parse()
            if frame is not None:
                opcode, payload = frame
                if opcode != 0x1:
                    continue
                return json.loads(payload.decode("utf-8"))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("timed out waiting for a websocket frame")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                fail("server closed the stream unexpectedly")
            self.buffer.extend(chunk)

    def wait_event(self, event_type, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.recv_json(timeout=max(0.1, deadline - time.monotonic()))
            if frame.get("type") == "event" and frame.get("event", {}).get("type") == event_type:
                return frame["event"]
        fail(f"stream did not deliver {event_type} in time")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


wait_healthy("initial start")
print("[1] health + started PASS (§6.1)")

# --- One input = one turn; events explain the lifecycle (§6.2, §6.3) ---
turn1 = request_json(
    "POST", "/input/text", {"text": f"Remember this marker: {HISTORY_MARKER}"}
)
conversation_c = turn1.get("conversation_id")
if not conversation_c:
    fail(f"no conversation_id: {turn1}")
if turn1.get("turn", {}).get("status") != "finished":
    fail(f"turn one did not finish: {turn1}")
turns = request_json("GET", f"/turns?conversation_id={conversation_c}&limit=10").get("turns", [])
if len(turns) != 1:
    fail(f"expected exactly one turn, got {len(turns)}")

events = request_json("GET", "/events?after_id=0&limit=200").get("events", [])
event_types = {event.get("type") for event in events}
for expected in ("brain.requested", "brain.responded", "turn.finished"):
    if expected not in event_types:
        fail(f"lifecycle event missing: {expected} (have {sorted(event_types)})")
print("[2] one input -> one finished turn, lifecycle events present PASS (§6.2, §6.3)")

# --- Mutations require the transport token (§6.7) ---
status, _ = request_json_status(
    "POST", "/input/text", {"text": "no token"}, with_token=False
)
if status != 401:
    fail(f"tokenless input did not return 401: {status}")
print("[3] tokenless mutation -> 401 PASS (§6.7)")

# --- Live stream carries daemon truth (§6.4) ---
stream = StreamClient()
if stream.handshake() != 101:
    fail("stream handshake failed")
hello = stream.recv_json()
if hello.get("type") != "stream.hello":
    fail(f"missing hello frame: {hello}")

# --- Model-originated tool call: policy -> approval -> explicit execute ->
# --- ToolRun -> continuation (§6.5); redaction at rest (§6.13) ---
tool_turn = request_json(
    "POST", "/input/text", {"text": "E2E_TOOL_TURN please read the secret note"}
)
if tool_turn.get("turn", {}).get("status") != "awaiting_approval":
    fail(f"tool turn is not awaiting_approval: {tool_turn}")
approvals = tool_turn.get("approvals", [])
if len(approvals) != 1 or approvals[0].get("status") != "pending":
    fail(f"expected one pending approval: {tool_turn}")
approval_id = approvals[0]["id"]

# The live stream (connected before this turn) must push the approval event.
stream.wait_event("approval.created")
print("[4] live stream pushed approval.created PASS (§6.4)")

with sqlite3.connect(db_path) as conn:
    if conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] != 0:
        fail("tool executed before approval")

approve = request_json("POST", f"/approvals/{approval_id}/approve", {"reason": "e2e"})
if approve.get("approval", {}).get("status") != "approved":
    fail(f"approve failed: {approve}")
with sqlite3.connect(db_path) as conn:
    if conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] != 0:
        fail("approve executed the tool (it must not)")

executed = request_json("POST", f"/approvals/{approval_id}/execute")
if executed.get("ok") is not True:
    fail(f"execute failed: {executed}")
if (executed.get("tool_run") or {}).get("status") != "finished":
    fail(f"tool_run not finished: {executed}")
continuation = executed.get("continuation") or {}
if continuation.get("status") != "finished":
    fail(f"continuation did not finish the turn: {continuation}")
print("[5] model tool call -> approval -> explicit execute -> continuation PASS (§6.5)")

# Duplicate execute conflicts without a second ToolRun (§6.10).
status, dup = request_json_status("POST", f"/approvals/{approval_id}/execute")
if status != 409:
    fail(f"duplicate execute did not return 409: {status} {dup}")
with sqlite3.connect(db_path) as conn:
    if conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] != 1:
        fail("duplicate execute created a second tool_run")
print("[6] duplicate execute -> 409, single ToolRun PASS (§6.10)")

# Secrets never persist raw (§6.13): the note content passed through the tool.
with sqlite3.connect(db_path) as conn:
    for table, column in (("tool_runs", "output_json"), ("events", "payload_json")):
        rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
        for (value,) in rows:
            if value and SECRET in str(value):
                fail(f"raw secret persisted in {table}.{column}")
print("[7] secret redacted at rest in tool_runs and events PASS (§6.13)")

# --- Rejected approval never executes (§6.10) ---
reject_turn = request_json(
    "POST", "/input/text", {"text": "E2E_TOOL_TURN read the note again"}
)
reject_approvals = reject_turn.get("approvals", [])
if len(reject_approvals) != 1:
    fail(f"expected one approval on reject turn: {reject_turn}")
reject_id = reject_approvals[0]["id"]
rejected = request_json("POST", f"/approvals/{reject_id}/reject", {"reason": "e2e reject"})
if rejected.get("approval", {}).get("status") != "rejected":
    fail(f"reject failed: {rejected}")
status, after_reject = request_json_status("POST", f"/approvals/{reject_id}/execute")
if status not in {400, 404, 409}:
    fail(f"executing a rejected approval must fail: {status} {after_reject}")
with sqlite3.connect(db_path) as conn:
    if conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] != 1:
        fail("rejected approval produced a tool_run")
print("[8] rejected approval never executes PASS (§6.10)")

# --- Direct file_read: inside roots finishes, outside is blocked (§6.6) ---
outside_path = os.path.join(smoke_dir, "outside.txt")
with open(outside_path, "w", encoding="utf-8") as handle:
    handle.write("must stay unreadable\n")
status, inside = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "file_read", "arguments": {"path": os.path.join(workspace, "secret-note.txt")}, "requested_by": "smoke"},
)
if status != 200 or inside.get("status") != "finished":
    fail(f"in-roots file_read did not finish: {status} {inside}")
status, blocked = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "file_read", "arguments": {"path": outside_path}, "requested_by": "smoke"},
)
if status != 200 or blocked.get("status") != "blocked":
    fail(f"out-of-roots file_read was not blocked: {status} {blocked}")
print("[9] file_read outside approved_roots -> blocked PASS (§6.6)")

# --- Brain switch keeps history (§6.11) ---
switch = request_json("POST", "/brain/switch", {"adapter": "mock"})
if switch.get("adapter") != "mock":
    fail(f"switch to mock failed: {switch}")
mock_turn = request_json(
    "POST", "/input/text", {"text": "Hello on mock", "conversation_id": conversation_c}
)
if mock_turn.get("brain_adapter") != "mock":
    fail(f"turn did not run on mock: {mock_turn}")
request_json("POST", "/brain/switch", {"adapter": "claude_cli"})
back_turn = request_json(
    "POST", "/input/text", {"text": "What marker did I give you?", "conversation_id": conversation_c}
)
if back_turn.get("brain_adapter") != "claude_cli":
    fail(f"turn did not run on claude_cli after switch back: {back_turn}")
with open(prompt_dump, "r", encoding="utf-8") as handle:
    prompt = handle.read()
if HISTORY_MARKER not in prompt:
    fail("history marker missing from the post-switch prompt")
if request_json("GET", "/state").get("brain_adapter") != "claude_cli":
    fail("/state brain_adapter did not follow the switch")
print("[10] brain switch keeps conversation history PASS (§6.11)")

# --- Worker job: silent, result becomes an inactive memory candidate (§6.12) ---
job = request_json(
    "POST",
    "/workers/jobs",
    {"worker_kind": "mock", "prompt": "e2e worker job", "requested_by": "smoke"},
)
job_id = job.get("job", {}).get("id") or job.get("id")
if not job_id:
    fail(f"no job id: {job}")
deadline = time.time() + 15
job_status = None
while time.time() < deadline:
    job_payload = request_json("GET", f"/workers/jobs/{job_id}")
    job_status = job_payload.get("job", {}).get("status") or job_payload.get("status")
    if job_status in {"succeeded", "failed"}:
        break
    time.sleep(0.25)
if job_status != "succeeded":
    fail(f"worker job did not succeed: {job_status}")
with sqlite3.connect(db_path) as conn:
    candidate_rows = conn.execute(
        "SELECT active FROM memory_blocks WHERE metadata_json LIKE '%candidate%'"
    ).fetchall()
    voice_rows = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
if not candidate_rows or any(active for (active,) in candidate_rows):
    fail(f"worker candidate missing or active: {candidate_rows}")
if voice_rows != 0:
    fail("worker touched the voice queue")
# Turns so far: marker, tool loop, rejected tool, mock, switch-back = 5.
if turn_count != 5:
    fail(f"worker created turns: {turn_count}")
print("[11] worker job silent, candidate inactive PASS (§6.12)")

# --- Runtime conflicts are report-only (§6.15) ---
runtime = request_json("GET", "/runtime/processes")
if runtime.get("report_only") is not True or runtime.get("cleanup_automated") is not False:
    fail(f"runtime processes are not report-only: {runtime}")
print("[12] runtime conflicts report-only PASS (§6.15)")

stream.close()
print("phase 1 passed")
PY

echo "Restarting daemon to verify durable truth..."
stop_daemon
start_daemon

BASE_URL="$BASE_URL" DB_PATH="$DB_PATH" SMOKE_DIR="$SMOKE_DIR" "$PYTHON" <<'PY'
import json
import os
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base_url = os.environ["BASE_URL"]
db_path = os.environ["DB_PATH"]
smoke_dir = os.environ["SMOKE_DIR"]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    for label, name in (("daemon stdout", "daemon.stdout.log"), ("daemon stderr", "daemon.stderr.log")):
        path = os.path.join(smoke_dir, name)
        if os.path.exists(path):
            print(f"--- {label} ---", file=sys.stderr)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                print(handle.read()[-4000:], file=sys.stderr)
    raise SystemExit(1)


def request_json(path: str, timeout: float = 5) -> dict:
    request = Request(f"{base_url}{path}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, TimeoutError, URLError, OSError) as exc:
        raise RuntimeError(f"GET {path} failed: {exc}") from exc


deadline = time.time() + 15
last_error = ""
while time.time() < deadline:
    try:
        health = request_json("/health", timeout=1)
        if health.get("ok") is True and health.get("started") is True:
            break
        last_error = f"unhealthy response: {health}"
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.25)
else:
    fail(f"daemon health timeout after restart: {last_error}")

# History and the switched adapter survive the restart (§6.2, §6.11).
if request_json("/state").get("brain_adapter") != "claude_cli":
    fail("restart lost the persisted brain adapter")
with sqlite3.connect(db_path) as conn:
    turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    finished = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE status = 'finished'"
    ).fetchone()[0]
if turn_count != 5:
    fail(f"turn history lost on restart: {turn_count}")
if finished < 4:
    fail(f"finished turns missing after restart: {finished}")
print("[13] restart: history + persisted adapter survive PASS (§6.2, §6.11)")
print("phase 2 passed")
PY

echo "E2E MVP smoke passed"
