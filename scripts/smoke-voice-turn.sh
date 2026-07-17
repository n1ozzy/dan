#!/usr/bin/env bash
set -euo pipefail

# G4c voice-turn smoke: anti-echo + barge-in through a REAL daemon, with a
# fake sox (no microphone), the mock STT engine (no whisper) and the mock
# TTS engine (no sound). Proves on the public daemon surface only:
#   1. an accepted transcript becomes exactly one VOICE turn (ADR-011),
#   2. user speech while speech is pending = barge-in: the pending
#      VoiceRequests flip to cancelled with voice.speak.cancelled events
#      BEFORE the new turn starts,
#   3. a transcript matching recently spoken text is rejected by the
#      anti-echo gate: no new turn, no cancellation — the system is never
#      cancelled by its own echo.

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
PORT="41797"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-voice-turn-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
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

# Fake sox: every capture is loud noise, so the energy gate always accepts
# and the mock STT engine returns its constant transcript.
cat >"$FAKE_SOX" <<EOF
#!/bin/bash
out=""
for arg in "\$@"; do
  case "\$arg" in *.wav) out="\$arg";; esac
done
if [ -n "\$out" ]; then
  head -c 32000 /dev/urandom > "\$out"
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
name = "dand"
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
speak_responses = true
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
pid_file = "$SMOKE_DIR/runtime/dand.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.dan.dand.smoke"
install_automatically = false
EOF

echo "Smoke directory: $SMOKE_DIR"
"$PYTHON" -m dan.cli --config "$CONFIG" daemon run >"$SMOKE_DIR/daemon.stdout.log" 2>"$SMOKE_DIR/daemon.stderr.log" &
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


def spawn_count():
    if not os.path.exists(argv_file):
        return 0
    with open(argv_file, "r", encoding="utf-8") as handle:
        return len([line for line in handle.read().splitlines() if line.strip()])


def query(sql):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchall()


def turns():
    return query("SELECT source, status FROM turns ORDER BY rowid")


def queue_rows():
    return query("SELECT turn_id, status FROM voice_queue ORDER BY rowid")


def cancelled_event_count():
    return query("SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'")[0][0]


def wait_for(predicate, timeout=15.0):
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

# 1. A text turn queues its spoken response (broker off: rows stay queued).
status, turn1 = request_json_status(
    "POST",
    "/input/text",
    {"text": "Zaplanuj jutrzejsze porządki w garażu oraz zakupy."},
)
if status != 200:
    fail(f"text turn failed: {status} {turn1}")
if not wait_for(lambda: len(queue_rows()) >= 1):
    fail(f"finished text turn queued no speech: {queue_rows()}")
pending_before = [row for row in queue_rows() if row[1] == "queued"]
if not pending_before:
    fail(f"expected queued speech before barge-in: {queue_rows()}")
print("text turn queued its spoken response PASS")

# 2. User speech while speech is pending = barge-in: pending rows flip to
#    cancelled (with events) and the transcript becomes a VOICE turn.
ptt_cycle(expected_spawns=1)
if not wait_for(lambda: len(turns()) == 2):
    fail(f"transcript did not become a voice turn: {turns()}")
if turns()[1][0] != "voice":
    fail(f"second turn has wrong source: {turns()}")
if not wait_for(lambda: turns()[1][1] == "finished"):
    fail(f"voice turn did not finish: {turns()}")

rows = query("SELECT id FROM turns ORDER BY rowid")
old_turn_id = rows[0][0]
old_rows = [row for row in queue_rows() if row[0] == old_turn_id]
if not old_rows or any(status != "cancelled" for _, status in old_rows):
    fail(f"barge-in did not cancel the pending speech: {queue_rows()}")
events_after_barge_in = cancelled_event_count()
if events_after_barge_in < len(old_rows):
    fail(f"missing voice.speak.cancelled events: {events_after_barge_in}")
print("barge-in cancelled pending speech and started a voice turn PASS")

# 3. A SECOND identical transcript. The anti-echo corpus is now spoken_at rows
#    (FIX-09), NOT merely cancelled ones — and with the broker off nothing has
#    reached the speaker yet, so the corpus is empty: capture #2 is not an echo,
#    it barge-ins the pending speech and becomes one more voice turn.
turn_count_before = len(turns())
ptt_cycle(expected_spawns=2)
# anti-echo runs BEFORE barge-in — give the pipeline a moment to process.
time.sleep(2.0)
if len(turns()) != turn_count_before + 1:
    fail(f"expected one more voice turn after capture #2: {turns()}")
if not wait_for(lambda: turns()[-1][1] == "finished"):
    fail(f"voice turn #2 did not finish: {turns()}")

# 4. Capture #3 must be rejected as an echo. FIX-09: only rows the broker
#    actually played (spoken_at set) seed the echo corpus — a 'queued' row a
#    barge-in flipped to 'cancelled' never made a sound. The broker is off in
#    this smoke, so model "DAN really played its speech" by stamping
#    spoken_at on the rows already sent; only then is capture #3 a true echo.
query("UPDATE voice_queue SET spoken_at = updated_at WHERE spoken_at IS NULL")
turn_count_before = len(turns())
cancelled_before = cancelled_event_count()
ptt_cycle(expected_spawns=3)
time.sleep(2.0)
if len(turns()) != turn_count_before:
    fail(f"an echo became a turn (anti-echo failed): {turns()}")
if cancelled_event_count() != cancelled_before:
    fail("an echo triggered barge-in cancellation (anti-echo ran too late)")
transcribed = query("SELECT COUNT(*) FROM events WHERE type = 'input.voice.transcribed'")[0][0]
if transcribed != 3:
    fail(f"expected 3 transcript events in the audit trail: {transcribed}")
print("echo transcript rejected: no turn, no cancellation PASS")

status, health = request_json_status("GET", "/health")
if status != 200 or health.get("ok") is not True:
    fail(f"daemon unhealthy after captures: {status} {health}")
print("daemon healthy after all cycles PASS")

print("voice turn smoke passed")
PY

echo "Voice turn smoke passed"
