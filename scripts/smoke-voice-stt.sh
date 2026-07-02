#!/usr/bin/env bash
set -euo pipefail

# G4b STT smoke: the full capture->transcript flow through a REAL daemon,
# with a fake sox (no microphone) and the mock STT engine (no whisper).
# Proves the mandatory hallucination firewall end to end: a silent capture
# is dropped by the energy gate BEFORE any engine runs, a voiced capture
# becomes exactly one input.voice.transcribed event, and the transcript is
# redacted at rest (the mock's default text carries a fake sk-* secret).

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
PORT="41793"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-voice-stt-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
FAKE_SOX="$SMOKE_DIR/fake-sox"
ARGV_FILE="$SMOKE_DIR/sox-argv.txt"
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

# Fake sox with a spawn counter: capture #1 is digital silence (must be
# dropped by the gate), capture #2 is loud noise (must be transcribed).
# WAV before argv so tests never race the fake mid-write.
cat >"$FAKE_SOX" <<EOF
#!/bin/bash
count_file="$SMOKE_DIR/sox-count"
count=\$(cat "\$count_file" 2>/dev/null || echo 0)
count=\$((count + 1))
echo "\$count" > "\$count_file"
out=""
for arg in "\$@"; do
  case "\$arg" in *.wav) out="\$arg";; esac
done
if [ -n "\$out" ]; then
  if [ "\$count" -eq 1 ]; then
    head -c 32000 /dev/zero > "\$out"
  else
    head -c 32000 /dev/urandom > "\$out"
  fi
fi
printf '%s\t' "\$@" >> $ARGV_FILE
printf '\n' >> $ARGV_FILE
trap 'exit 0' INT TERM
sleep 60 &
wait \$!
EOF
chmod 700 "$FAKE_SOX"

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
recorder = "sox"
recorder_binary = "$FAKE_SOX"
ptt_hold_ttl_seconds = 30
listen_lock_ttl_seconds = 600

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

BASE_URL="$BASE_URL" DB_PATH="$DB_PATH" SMOKE_DIR="$SMOKE_DIR" ARGV_FILE="$ARGV_FILE" "$PYTHON" <<'PY'
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
argv_file = os.environ["ARGV_FILE"]


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


def request_json_status(method, path, payload=None, *, timeout=10):
    headers = {"Accept": "application/json"}
    if method in {"POST", "PATCH", "DELETE"}:
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


def spawn_count():
    if not os.path.exists(argv_file):
        return 0
    with open(argv_file, "r", encoding="utf-8") as handle:
        return len([line for line in handle.read().splitlines() if line.strip()])


def transcribed_events():
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type = 'input.voice.transcribed' ORDER BY id"
        ).fetchall()
    return [json.loads(str(row[0])) for row in rows]


def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def ptt_cycle(expected_spawns):
    status, down = request_json_status("POST", "/voice/ptt/down", {})
    if status != 200:
        fail(f"ptt down failed: {status} {down}")
    if not wait_for(lambda: spawn_count() == expected_spawns):
        fail(f"sox spawn {expected_spawns} missing: {spawn_count()}")
    status, up = request_json_status("POST", "/voice/ptt/up", {})
    if status != 200:
        fail(f"ptt up failed: {status} {up}")


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

# 1. A silent capture is dropped by the gate: no transcript event at all.
ptt_cycle(expected_spawns=1)
time.sleep(1.0)  # give the pipeline time to (wrongly) transcribe
if transcribed_events():
    fail(f"silence produced a transcript: {transcribed_events()}")
print("silent capture -> gate drop, no transcript PASS")

# 2. A voiced capture becomes exactly one transcript event.
ptt_cycle(expected_spawns=2)
if not wait_for(lambda: len(transcribed_events()) == 1):
    fail(f"voiced capture produced no transcript: {transcribed_events()}")
events = transcribed_events()
if events[0].get("engine") != "mock":
    fail(f"unexpected engine in event: {events[0]}")
if events[0].get("duration_seconds", 0) <= 0:
    fail(f"missing capture stats in event: {events[0]}")
print("voiced capture -> one input.voice.transcribed event PASS")

# 3. The transcript at rest is redacted (mock default carries sk-*), and
#    nothing about voice ever hit the queue or turns (no anti-echo yet).
text = events[0].get("text", "")
if "sk-mock" in text:
    fail(f"secret survived in the persisted transcript: {text!r}")
if "[REDACTED]" not in text:
    fail(f"expected redaction placeholder in transcript: {text!r}")
with sqlite3.connect(db_path) as conn:
    (queue_count,) = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()
    (turn_count,) = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
if queue_count != 0:
    fail(f"voice_queue is not empty: {queue_count}")
if turn_count != 0:
    fail(f"a transcript became a turn before the anti-echo gate exists: {turn_count}")
print("transcript redacted at rest, no queue/turn side effects PASS")

status, health = request_json_status("GET", "/health")
if status != 200 or health.get("ok") is not True:
    fail(f"daemon unhealthy after captures: {status} {health}")
print("daemon healthy after both cycles PASS")

print("voice stt smoke passed")
PY

echo "Voice STT smoke passed"
