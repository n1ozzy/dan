#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

if ! command -v claude >/dev/null 2>&1; then
  printf 'Claude CLI not found on PATH.\n' >&2
  exit 2
fi

HOST="127.0.0.1"
PORT="41750"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-claude-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-claude-smoke.toml"
DAEMON_LOG="$SMOKE_DIR/dand.log"
INPUT_JSON="$SMOKE_DIR/input.json"
EVENTS_JSON="$SMOKE_DIR/events.json"
DAEMON_PID=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [ -n "${DAEMON_PID:-}" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    kill "$DAEMON_PID" 2>/dev/null || true
    wait "$DAEMON_PID" 2>/dev/null || true
  fi

  if [ "${SMOKE_KEEP_ARTIFACTS:-}" = "1" ]; then
    printf 'Keeping smoke directory: %s\n' "$SMOKE_DIR"
  else
    rm -rf "$SMOKE_DIR"
  fi

  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$ROOT"

"$PYTHON_BIN" - "$HOST" "$PORT" <<'PY'
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
name = "dand"
host = "$HOST"
port = $PORT
log_level = "INFO"

[database]
path = "$SMOKE_DIR/home/dan.db"
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
command = "claude"
args = ["-p"]
model = "claude-cli"
timeout_seconds = 180

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
label = "com.dan.dand.claude-smoke"
install_automatically = false
EOF

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" daemon run >"$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!

wait_for_health() {
  attempt=1
  while [ "$attempt" -le 40 ]; do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
      printf 'Temporary dand exited before health became ready.\n' >&2
      printf 'Daemon log: %s\n' "$DAEMON_LOG" >&2
      sed -n '1,120p' "$DAEMON_LOG" >&2 || true
      return 1
    fi

    if "$PYTHON_BIN" - "$BASE_URL" <<'PY' >/dev/null 2>&1
import json
import sys
from urllib.request import urlopen

with urlopen(f"{sys.argv[1]}/health", timeout=0.5) as response:
    payload = json.loads(response.read().decode("utf-8"))
if payload.get("ok") is not True:
    raise SystemExit(1)
if payload.get("service") != "dand":
    raise SystemExit(1)
PY
    then
      return 0
    fi

    sleep 0.25
    attempt=$((attempt + 1))
  done

  printf 'Timed out waiting for %s/health.\n' "$BASE_URL" >&2
  printf 'Daemon log: %s\n' "$DAEMON_LOG" >&2
  sed -n '1,120p' "$DAEMON_LOG" >&2 || true
  return 1
}

wait_for_health

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" input text "Kim jesteś?" --url "$BASE_URL" --timeout 180 >"$INPUT_JSON"

TURN_ID="$("$PYTHON_BIN" - "$INPUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload.get("ok") is not True:
    raise SystemExit("input response did not set ok=true")
if payload.get("brain_adapter") != "claude_cli":
    raise SystemExit("input response did not use claude_cli")
if payload.get("brain_model") != "claude-cli":
    raise SystemExit("input response did not report claude-cli")
final_text = payload.get("final_text")
if not isinstance(final_text, str) or not final_text.strip():
    raise SystemExit("input response did not contain final_text")
turn_id = payload.get("turn_id")
if not isinstance(turn_id, str) or not turn_id:
    raise SystemExit("input response did not contain turn_id")
print(turn_id)
PY
)"

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" events after --id 0 --url "$BASE_URL" >"$EVENTS_JSON"

EVENT_COUNT="$("$PYTHON_BIN" - "$EVENTS_JSON" "$TURN_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
events = payload.get("events")
if not isinstance(events, list) or not events:
    raise SystemExit("no events returned")
turn_id = sys.argv[2]
turn_events = [event for event in events if isinstance(event, dict) and event.get("turn_id") == turn_id]
types = [event.get("type") for event in turn_events]
required = {"brain.requested", "brain.responded", "turn.finished"}
missing = sorted(required.difference(types))
if missing:
    raise SystemExit(f"missing turn events: {', '.join(missing)}")

def payload_for(event_type):
    matches = [event.get("payload") for event in turn_events if event.get("type") == event_type]
    if len(matches) != 1 or not isinstance(matches[0], dict):
        raise SystemExit(f"unexpected payload count for {event_type}")
    return matches[0]

requested = payload_for("brain.requested")
responded = payload_for("brain.responded")
finished = payload_for("turn.finished")
if requested.get("model") != "claude-cli":
    raise SystemExit("brain.requested did not report claude-cli")
if responded.get("model") != requested.get("model"):
    raise SystemExit("brain.responded model did not match brain.requested")
if finished.get("brain_model") != responded.get("model"):
    raise SystemExit("turn.finished brain_model did not match brain.responded")

state_edges = []
for event in turn_events:
    payload = event.get("payload")
    if event.get("type") == "state.changed" and isinstance(payload, dict):
        old_state = payload.get("old_state")
        new_state = payload.get("new_state")
        if isinstance(old_state, str) and isinstance(new_state, str):
            state_edges.append(f"{old_state} -> {new_state}")
if "IDLE -> THINKING" not in state_edges:
    raise SystemExit("missing IDLE -> THINKING state change")
if "THINKING -> IDLE" not in state_edges:
    raise SystemExit("missing THINKING -> IDLE state change")

print(len(events))
PY
)"

printf 'Claude CLI brain smoke passed.\n'
printf 'smoke directory: %s\n' "$SMOKE_DIR"
printf 'daemon pid: %s\n' "$DAEMON_PID"
printf 'turn_id: %s\n' "$TURN_ID"
printf 'event count: %s\n' "$EVENT_COUNT"
