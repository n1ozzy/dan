#!/usr/bin/env bash
set -euo pipefail

# G4d streaming smoke: on_delta end to end through a REAL daemon with a
# FAKE claude CLI (fake-brain pattern — no real provider, no sound). The
# fake emits stream-json deltas, sleeps, then emits the final result.
# Proves: the first sentence is queued while the CLI is still running
# (first-sound requirement, §4a), the finished turn's final_text is the
# canonical result text, nothing is double-enqueued, and no delta is ever
# persisted (events stay within the frozen families).

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
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-voice-stream-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
FAKE_CLAUDE="$SMOKE_DIR/fake-claude"
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

# Fake claude CLI: swallows stdin/args, streams two sentence deltas, then
# sleeps before the canonical result — that sleep is the window in which
# the first sentence MUST already sit in voice_queue.
cat >"$FAKE_CLAUDE" <<'EOF'
#!/bin/bash
cat > /dev/null
printf '%s\n' '{"type":"system","subtype":"init"}'
printf '%s\n' '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Pierwsze zdanie strumienia. "}}}'
sleep 0.3
printf '%s\n' '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Drugie zdanie strumienia."}}}'
sleep 2.5
printf '%s\n' '{"type":"result","subtype":"success","is_error":false,"result":"Pierwsze zdanie strumienia. Drugie zdanie strumienia.","usage":{"input_tokens":10,"output_tokens":8}}'
exit 0
EOF
chmod 700 "$FAKE_CLAUDE"

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
default_adapter = "claude_cli"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[brain.claude_cli]
enabled = true
command = "$FAKE_CLAUDE"
args = ["-p"]
model = "fake-claude-stream"
timeout_seconds = 30

[memory]
enabled = true
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true

[voice]
enabled = true
speak_responses = true
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true

[audio]
enabled = true
backend = "fake"
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = true
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
import threading
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


def query(sql):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchall()


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

# POST the turn on a side thread: the request blocks until the turn is done,
# and the whole point is to observe the queue WHILE the fake CLI still runs.
result_box = {}


def post_turn() -> None:
    data = json.dumps({"text": "Opowiedz coś strumieniem."}).encode("utf-8")
    request = Request(
        f"{base_url}/input/text",
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Jarvis-Token": api_token(),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result_box["status"] = response.status
            result_box["payload"] = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        result_box["status"] = exc.code
        result_box["payload"] = {"error": exc.read().decode("utf-8", "replace")}
    except (TimeoutError, URLError, OSError) as exc:
        result_box["status"] = 0
        result_box["payload"] = {"error": str(exc)}


thread = threading.Thread(target=post_turn, daemon=True)
thread.start()

# 1. First-sound: the first sentence lands in voice_queue while the turn is
#    still running (the fake CLI sleeps 2.5 s before its result event).
first_seen = None
deadline = time.time() + 20
while time.time() < deadline:
    rows = query("SELECT text FROM voice_queue ORDER BY rowid")
    turn_rows = query("SELECT status FROM turns")
    if rows:
        turn_running = bool(turn_rows) and turn_rows[0][0] not in ("finished", "failed")
        first_seen = (rows[0][0], turn_running)
        break
    time.sleep(0.05)
if first_seen is None:
    fail("no sentence was queued at all")
if first_seen[0] != "Pierwsze zdanie strumienia.":
    fail(f"unexpected first queued sentence: {first_seen[0]!r}")
if not first_seen[1]:
    fail("first sentence was queued only after the turn ended (no live streaming)")
print("first sentence queued while generation was still running PASS")

thread.join(timeout=60)
if result_box.get("status") != 200:
    fail(f"text turn failed: {result_box}")

# 2. Canonical truth: final_text comes from the result event, and the queue
#    holds each sentence exactly once (no double-speak at finish).
payload = result_box["payload"]
expected = "Pierwsze zdanie strumienia. Drugie zdanie strumienia."
if payload.get("final_text") != expected:
    fail(f"final_text is not the canonical result text: {payload.get('final_text')!r}")
texts = [row[0] for row in query("SELECT text FROM voice_queue ORDER BY rowid")]
if texts != ["Pierwsze zdanie strumienia.", "Drugie zdanie strumienia."]:
    fail(f"queue does not hold each sentence exactly once: {texts}")
print("canonical final_text and exactly-once sentence queue PASS")

# 3. Deltas are transport, not truth: no event type mentions deltas and the
#    audit trail records the canonical text length in brain.responded.
event_types = {row[0] for row in query("SELECT DISTINCT type FROM events")}
if any("delta" in event_type for event_type in event_types):
    fail(f"a delta event leaked into the audit trail: {sorted(event_types)}")
responded = query(
    "SELECT payload_json FROM events WHERE type = 'brain.responded' ORDER BY id"
)
if len(responded) != 1:
    fail(f"expected exactly one brain.responded event: {len(responded)}")
if json.loads(responded[0][0]).get("text_length") != len(expected):
    fail("brain.responded does not carry the canonical text length")
print("no delta persisted; brain.responded is canonical PASS")

status_request = Request(f"{base_url}/health", headers={"Accept": "application/json"})
with urlopen(status_request, timeout=5) as response:
    health = json.loads(response.read().decode("utf-8"))
if health.get("ok") is not True:
    fail(f"daemon unhealthy after the streamed turn: {health}")
print("daemon healthy after the streamed turn PASS")

print("voice stream smoke passed")
PY

echo "Voice stream smoke passed"
