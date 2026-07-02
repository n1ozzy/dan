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

HOST="127.0.0.1"
PORT="41779"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-brain-switch-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
FAKE_BRAIN="$SMOKE_DIR/fake-brain.sh"
PROMPT_DUMP="$SMOKE_DIR/fake-brain-prompt.txt"
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

# Deterministic fake local CLI brain (pattern from smoke-tool-continuation.sh).
# Dumps the full stateless prompt so the smoke can prove the post-switch turn
# still carries the pre-switch conversation history. No providers, no network.
cat >"$FAKE_BRAIN" <<FAKE
#!/bin/sh
tee "$PROMPT_DUMP" >/dev/null
printf 'Fake CLI brain answer after switch.\n'
FAKE
chmod +x "$FAKE_BRAIN"

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

start_daemon() {
  "$PYTHON" -m jarvis.cli --config "$CONFIG" daemon run >>"$SMOKE_DIR/daemon.stdout.log" 2>>"$SMOKE_DIR/daemon.stderr.log" &
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
echo "Fake brain: $FAKE_BRAIN"

start_daemon

BASE_URL="$BASE_URL" DB_PATH="$DB_PATH" SMOKE_DIR="$SMOKE_DIR" PROMPT_DUMP="$PROMPT_DUMP" "$PYTHON" <<'PY'
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
prompt_dump = os.environ["PROMPT_DUMP"]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    for label, name in (("daemon stdout", "daemon.stdout.log"), ("daemon stderr", "daemon.stderr.log")):
        path = os.path.join(smoke_dir, name)
        if os.path.exists(path):
            print(f"--- {label} ---", file=sys.stderr)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                print(handle.read()[-4000:], file=sys.stderr)
    raise SystemExit(1)


def api_token() -> str | None:
    token_path = os.path.join(smoke_dir, "runtime", "api-token")
    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        return None
    return token or None


def request_json_status(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    with_token: bool = True,
    timeout: float = 30,
) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    token = api_token()
    if with_token and token and method in {"POST", "PATCH", "DELETE"}:
        headers["X-Jarvis-Token"] = token
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


def request_json(method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    status, body = request_json_status(method, path, payload, timeout=timeout)
    if status >= 400:
        fail(f"{method} {path} returned HTTP {status}: {body}")
    return body


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

# 1. Both adapters registered; mock is the config default and current.
adapters_payload = request_json("GET", "/brain/adapters")
names = sorted(adapter["name"] for adapter in adapters_payload.get("adapters", []))
if names != ["claude_cli", "mock"]:
    fail(f"unexpected adapters: {adapters_payload}")
if adapters_payload.get("current") != "mock" or adapters_payload.get("default") != "mock":
    fail(f"unexpected current/default adapter: {adapters_payload}")

current_payload = request_json("GET", "/brain/current")
if current_payload.get("adapter") != "mock":
    fail(f"/brain/current is not mock before switch: {current_payload}")

# 2. First turn on mock plants the history marker.
marker = "BRAIN_SWITCH_SMOKE_MARKER_E1"
turn1 = request_json("POST", "/input/text", {"text": f"Remember this marker: {marker}"})
conversation_id = turn1.get("conversation_id")
if not isinstance(conversation_id, str) or not conversation_id:
    fail(f"first turn has no conversation_id: {turn1}")
if turn1.get("brain_adapter") != "mock":
    fail(f"first turn did not run on mock: {turn1}")

# 3. Switch is a mutation: tokenless request must be rejected before routing.
status, body = request_json_status(
    "POST", "/brain/switch", {"adapter": "claude_cli"}, with_token=False
)
if status != 401:
    fail(f"tokenless switch did not return 401: status={status} body={body}")

# 4. Unknown adapter fails closed without touching state.
status, body = request_json_status("POST", "/brain/switch", {"adapter": "bogus"})
if status != 404:
    fail(f"unknown adapter switch did not return 404: status={status} body={body}")
if request_json("GET", "/brain/current").get("adapter") != "mock":
    fail("failed switch changed the current adapter")

# 5. Real switch: mock -> claude_cli (fake local CLI).
switch_payload = request_json("POST", "/brain/switch", {"adapter": "claude_cli"})
if switch_payload.get("previous") != "mock" or switch_payload.get("adapter") != "claude_cli":
    fail(f"switch payload mismatch: {switch_payload}")
if switch_payload.get("changed") is not True:
    fail(f"switch did not report changed: {switch_payload}")
if request_json("GET", "/brain/current").get("adapter") != "claude_cli":
    fail("/brain/current did not follow the switch")
if request_json("GET", "/state").get("brain_adapter") != "claude_cli":
    fail("/state brain_adapter did not follow the switch")

# 6. Post-switch turn in the same conversation: history survives the switch.
turn2 = request_json(
    "POST",
    "/input/text",
    {"text": "What marker did I give you?", "conversation_id": conversation_id},
)
if turn2.get("conversation_id") != conversation_id:
    fail(f"second turn changed conversation: {turn2}")
if turn2.get("brain_adapter") != "claude_cli":
    fail(f"second turn did not run on claude_cli: {turn2}")
if "Fake CLI brain answer" not in str(turn2.get("final_text", "")):
    fail(f"second turn final_text is not the fake CLI answer: {turn2}")

with open(prompt_dump, "r", encoding="utf-8") as handle:
    prompt = handle.read()
if marker not in prompt:
    fail("pre-switch history marker missing from the post-switch CLI prompt")

# 7. Durable truth: persisted setting + audit event in the daemon DB.
with sqlite3.connect(db_path) as conn:
    setting_row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'brain.current_adapter'"
    ).fetchone()
    switch_events = conn.execute(
        "SELECT payload_json FROM events WHERE type = 'brain.switched'"
    ).fetchall()
if setting_row is None or json.loads(setting_row[0]) != "claude_cli":
    fail(f"persisted brain adapter setting mismatch: {setting_row}")
if len(switch_events) != 1:
    fail(f"expected exactly one brain.switched event: {switch_events}")
event_payload = json.loads(switch_events[0][0])
if event_payload.get("from") != "mock" or event_payload.get("to") != "claude_cli":
    fail(f"brain.switched payload mismatch: {event_payload}")

print("phase 1 (switch) passed")
print(f"conversation id: {conversation_id}")
PY

echo "Restarting daemon to verify the persisted brain choice..."
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

# 8. The restarted daemon restores the persisted switch (jarvisd owns truth).
current_payload = request_json("/brain/current")
if current_payload.get("adapter") != "claude_cli":
    fail(f"restart lost the persisted brain adapter: {current_payload}")
if request_json("/state").get("brain_adapter") != "claude_cli":
    fail("/state brain_adapter wrong after restart")

# 9. Conversation history survived both the switch and the restart.
with sqlite3.connect(db_path) as conn:
    rows = conn.execute(
        "SELECT brain_adapter FROM turns ORDER BY created_at"
    ).fetchall()
adapters = [row[0] for row in rows]
if adapters != ["mock", "claude_cli"]:
    fail(f"turn history mismatch after restart: {adapters}")

print("phase 2 (restart persistence) passed")
PY

echo "Brain switch smoke passed"
