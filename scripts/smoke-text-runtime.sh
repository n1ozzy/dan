#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

HOST="127.0.0.1"
PORT="41749"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-text-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DAEMON_LOG="$SMOKE_DIR/dand.log"
INPUT_JSON="$SMOKE_DIR/input.json"
CONVERSATIONS_JSON="$SMOKE_DIR/conversations.json"
TURNS_JSON="$SMOKE_DIR/turns.json"
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

printf 'Using CLI form: python -m dan.cli\n'

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

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" input text "Kim jesteś?" --url "$BASE_URL" >"$INPUT_JSON"

CONVERSATION_ID="$("$PYTHON_BIN" - "$INPUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload.get("ok") is not True:
    raise SystemExit("input response did not set ok=true")
final_text = payload.get("final_text")
if not isinstance(final_text, str) or "DAN mock response" not in final_text:
    raise SystemExit("input response did not contain MockBrain final_text")
conversation_id = payload.get("conversation_id")
if not isinstance(conversation_id, str) or not conversation_id:
    raise SystemExit("input response did not contain conversation_id")
turn_id = payload.get("turn_id")
if not isinstance(turn_id, str) or not turn_id:
    raise SystemExit("input response did not contain turn_id")
print(conversation_id)
PY
)"

TURN_ID="$("$PYTHON_BIN" - "$INPUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["turn_id"])
PY
)"

FINAL_TEXT="$("$PYTHON_BIN" - "$INPUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["final_text"])
PY
)"

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" conversations list --url "$BASE_URL" >"$CONVERSATIONS_JSON"
"$PYTHON_BIN" - "$CONVERSATIONS_JSON" "$CONVERSATION_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
conversations = payload.get("conversations")
if not isinstance(conversations, list) or not conversations:
    raise SystemExit("no conversations returned")
conversation_id = sys.argv[2]
if not any(item.get("id") == conversation_id for item in conversations if isinstance(item, dict)):
    raise SystemExit(f"conversation not found in history: {conversation_id}")
PY

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" turns list --conversation-id "$CONVERSATION_ID" --url "$BASE_URL" >"$TURNS_JSON"
"$PYTHON_BIN" - "$TURNS_JSON" "$TURN_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
turns = payload.get("turns")
if not isinstance(turns, list) or not turns:
    raise SystemExit("no turns returned")
turn_id = sys.argv[2]
if not any(item.get("id") == turn_id for item in turns if isinstance(item, dict)):
    raise SystemExit(f"turn not found in history: {turn_id}")
PY

"$PYTHON_BIN" -m dan.cli --config "$CONFIG" events after --id 0 --url "$BASE_URL" >"$EVENTS_JSON"

EVENT_COUNT="$("$PYTHON_BIN" - "$EVENTS_JSON" "$TURN_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
events = payload.get("events")
if not isinstance(events, list) or not events:
    raise SystemExit("no events returned")
types = [event.get("type") for event in events if isinstance(event, dict)]
required = {"daemon.started", "input.text.received", "brain.responded", "turn.finished"}
missing = sorted(required.difference(types))
if missing:
    raise SystemExit(f"missing events: {', '.join(missing)}")
turn_id = sys.argv[2]
if not any(event.get("turn_id") == turn_id for event in events if isinstance(event, dict)):
    raise SystemExit(f"no event references turn_id: {turn_id}")
print(len(events))
PY
)"

printf 'Text runtime smoke passed.\n'
printf 'smoke directory: %s\n' "$SMOKE_DIR"
printf 'daemon pid: %s\n' "$DAEMON_PID"
printf 'conversation_id: %s\n' "$CONVERSATION_ID"
printf 'turn_id: %s\n' "$TURN_ID"
printf 'final_text: %s\n' "$FINAL_TEXT"
printf 'event count: %s\n' "$EVENT_COUNT"
