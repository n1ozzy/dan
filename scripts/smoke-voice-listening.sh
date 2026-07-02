#!/usr/bin/env bash
set -euo pipefail

# G2 listening/PTT smoke: leases live in the DB, ptt up never clears a
# locked lease, stale holds expire (short TTL), mutations need the token.

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
PORT="41777"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-voice-listening-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
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

cat >"$CONFIG" <<EOF
[daemon]
name = "jarvisd"
host = "$HOST"
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
enabled = true
speak_responses = false
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true
recorder = "mock"
ptt_hold_ttl_seconds = 2
listen_lock_ttl_seconds = 600

[audio]
enabled = false
backend = "fake"
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
pid_file = "$SMOKE_DIR/runtime/jarvisd.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd.smoke"
install_automatically = false
EOF

echo "Smoke directory: $SMOKE_DIR"
"$PYTHON" -m jarvis.cli --config "$CONFIG" daemon run >"$SMOKE_DIR/daemon.stdout.log" 2>"$SMOKE_DIR/daemon.stderr.log" &
DAEMON_PID="$!"
echo "Daemon PID: $DAEMON_PID"

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


def api_token() -> str:
    with open(os.path.join(smoke_dir, "runtime", "api-token"), "r", encoding="utf-8") as handle:
        return handle.read().strip()


def request_json_status(method, path, payload=None, *, with_token=True, timeout=10):
    headers = {"Accept": "application/json"}
    if with_token and method in {"POST", "PATCH", "DELETE"}:
        headers["X-Jarvis-Token"] = api_token()
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


deadline = time.time() + 15
last_error = ""
while time.time() < deadline:
    try:
        request = Request(f"{base_url}/health", headers={"Accept": "application/json"})
        with urlopen(request, timeout=1) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("ok") is True and health.get("started") is True:
            break
        last_error = f"unhealthy: {health}"
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.25)
else:
    fail(f"daemon health timeout: {last_error}")

# 1. Mutations require the transport token.
status, _ = request_json_status("POST", "/voice/ptt/down", {}, with_token=False)
if status != 401:
    fail(f"tokenless ptt down did not return 401: {status}")
print("tokenless ptt -> 401 PASS")

# 2. PTT down -> listening; lock -> two active leases.
status, down = request_json_status("POST", "/voice/ptt/down", {})
if status != 200 or down.get("lease", {}).get("mode") != "hold":
    fail(f"ptt down failed: {status} {down}")
status, lock = request_json_status("POST", "/voice/listen/lock", {})
if status != 200 or lock.get("lease", {}).get("mode") != "locked":
    fail(f"listen lock failed: {status} {lock}")
status, listening = request_json_status("GET", "/voice/listening")
if listening.get("listening") is not True or len(listening.get("leases", [])) != 2:
    fail(f"expected two active leases: {listening}")
print("ptt down + lock -> two active leases PASS")

# 3. PTT up releases the hold but NEVER the locked lease.
status, up = request_json_status("POST", "/voice/ptt/up", {})
if status != 200 or up.get("released") != 1:
    fail(f"ptt up failed: {status} {up}")
status, listening = request_json_status("GET", "/voice/listening")
modes = [lease.get("mode") for lease in listening.get("leases", [])]
if listening.get("listening") is not True or modes != ["locked"]:
    fail(f"locked lease did not survive ptt up: {listening}")
print("ptt up keeps the locked lease PASS")

# 4. Unlock stops listening.
status, unlock = request_json_status("POST", "/voice/listen/unlock", {})
if status != 200 or unlock.get("released") != 1:
    fail(f"unlock failed: {status} {unlock}")
status, listening = request_json_status("GET", "/voice/listening")
if listening.get("listening") is not False:
    fail(f"still listening after unlock: {listening}")
print("unlock stops listening PASS")

# 5. A stale hold expires (2s TTL) instead of listening forever.
request_json_status("POST", "/voice/ptt/down", {})
time.sleep(3)
status, listening = request_json_status("GET", "/voice/listening")
if listening.get("listening") is not False:
    fail(f"stale hold did not expire: {listening}")
with sqlite3.connect(db_path) as conn:
    statuses = sorted(
        status for (status,) in conn.execute("SELECT status FROM listening_leases").fetchall()
    )
    events = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT type FROM events WHERE type LIKE 'listening.%'"
        ).fetchall()
    }
if statuses != ["expired", "released", "released"]:
    fail(f"unexpected lease statuses: {statuses}")
for expected in ("listening.lease.created", "listening.lease.released", "listening.lease.expired"):
    if expected not in events:
        fail(f"missing lease event {expected}: {events}")
print("stale hold expires with events PASS")

print("voice listening smoke passed")
PY

echo "Voice listening smoke passed"
