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
PORT="41789"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-memory-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-memory-smoke.toml"
DB_PATH="$SMOKE_DIR/home/jarvis.db"
DAEMON_LOG="$SMOKE_DIR/jarvisd.log"
MEMORY_CREATE_JSON="$SMOKE_DIR/memory-create.json"
MEMORY_ACTIVE_BEFORE_JSON="$SMOKE_DIR/memory-active-before.json"
FIRST_INPUT_JSON="$SMOKE_DIR/first-input.json"
MEMORY_DISABLE_JSON="$SMOKE_DIR/memory-disable.json"
MEMORY_ACTIVE_AFTER_JSON="$SMOKE_DIR/memory-active-after.json"
SECOND_INPUT_JSON="$SMOKE_DIR/second-input.json"
EVENTS_JSON="$SMOKE_DIR/events.json"
DAEMON_PID=""
MEMORY_ID=""
FIRST_TURN_ID=""
SECOND_TURN_ID=""

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

printf 'Using CLI form: python -m jarvis.cli\n'

if ! command -v claude >/dev/null 2>&1; then
  printf 'Claude CLI not found on PATH.\n' >&2
  exit 2
fi

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

# Smoke config sets database.path, runtime.home, runtime.logs_dir,
# runtime.runtime_dir, runtime.pid_file, voice.enabled = false and
# launchd.enabled = false inside SMOKE_DIR.
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
pid_file = "$SMOKE_DIR/runtime/jarvisd.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd.memory-smoke"
install_automatically = false
EOF

printf 'Smoke directory: %s\n' "$SMOKE_DIR"
printf 'Config: %s\n' "$CONFIG"
printf 'Starting daemon: python -m jarvis.cli --config "%s" daemon run\n' "$CONFIG"
"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" daemon run >"$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!
printf 'Daemon PID: %s\n' "$DAEMON_PID"

wait_for_health() {
  attempt=1
  while [ "$attempt" -le 60 ]; do
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
      printf 'Temporary jarvisd exited before health became ready.\n' >&2
      printf 'Daemon log: %s\n' "$DAEMON_LOG" >&2
      sed -n '1,160p' "$DAEMON_LOG" >&2 || true
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
if payload.get("service") != "jarvisd":
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
  sed -n '1,160p' "$DAEMON_LOG" >&2 || true
  return 1
}

wait_for_health

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" memory create \
  --kind fact \
  --title "Manual memory smoke phrase" \
  --body "Zapamiętana fraza testowa: fioletowy gołąb." \
  --priority 99 \
  --metadata-json '{"origin":"manual-memory-smoke"}' \
  --url "$BASE_URL" \
  --timeout 10 \
  >"$MEMORY_CREATE_JSON"

MEMORY_ID="$("$PYTHON_BIN" - "$MEMORY_CREATE_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
memory = payload.get("memory")
if not isinstance(memory, dict):
    raise SystemExit("memory create did not return memory object")
memory_id = memory.get("id")
if not isinstance(memory_id, str) or not memory_id:
    raise SystemExit("memory create did not return memory id")
if memory.get("active") is not True:
    raise SystemExit("created memory block is not active")
if "fioletowy gołąb" not in str(memory.get("body", "")):
    raise SystemExit("created memory block body is missing smoke phrase")
print(memory_id)
PY
)"

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" memory list \
  --active-only \
  --kind fact \
  --url "$BASE_URL" \
  --timeout 10 \
  >"$MEMORY_ACTIVE_BEFORE_JSON"

"$PYTHON_BIN" - "$MEMORY_ACTIVE_BEFORE_JSON" "$MEMORY_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
memory_id = sys.argv[2]
blocks = payload.get("memory")
if not isinstance(blocks, list):
    raise SystemExit("memory list did not return a list")
matches = [block for block in blocks if isinstance(block, dict) and block.get("id") == memory_id]
if len(matches) != 1:
    raise SystemExit("created memory block is missing from active memory list")
if "fioletowy gołąb" not in str(matches[0].get("body", "")):
    raise SystemExit("active memory list block is missing smoke phrase")
PY

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" input text \
  "Odpowiedz dokładnie zapamiętaną frazą z aktywnej pamięci Jarvisa. Jaka to fraza?" \
  --url "$BASE_URL" \
  --timeout 180 \
  >"$FIRST_INPUT_JSON"

FIRST_TURN_ID="$("$PYTHON_BIN" - "$FIRST_INPUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload.get("ok") is not True:
    raise SystemExit("first input response did not set ok=true")
if payload.get("brain_adapter") != "claude_cli":
    raise SystemExit("first input response did not use claude_cli")
if payload.get("brain_model") != "claude-cli":
    raise SystemExit("first input response did not report claude-cli")
final_text = payload.get("final_text")
if not isinstance(final_text, str) or "fioletowy gołąb" not in final_text.lower():
    raise SystemExit("first response did not mention the smoke phrase")
turn = payload.get("turn")
if not isinstance(turn, dict):
    raise SystemExit("first input response did not include turn object")
snapshot = turn.get("context_snapshot")
if not isinstance(snapshot, dict):
    raise SystemExit("first turn did not include context_snapshot")
if snapshot.get("memory_block_count") != 1:
    raise SystemExit(f"first turn memory_block_count was not 1: {snapshot}")
turn_id = payload.get("turn_id")
if not isinstance(turn_id, str) or not turn_id:
    raise SystemExit("first input response did not contain turn_id")
print(turn_id)
PY
)"

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" memory disable \
  --id "$MEMORY_ID" \
  --url "$BASE_URL" \
  --timeout 10 \
  >"$MEMORY_DISABLE_JSON"

"$PYTHON_BIN" - "$MEMORY_DISABLE_JSON" "$MEMORY_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
memory_id = sys.argv[2]
memory = payload.get("memory")
if not isinstance(memory, dict):
    raise SystemExit("memory disable did not return memory object")
if memory.get("id") != memory_id:
    raise SystemExit("memory disable returned the wrong memory id")
if memory.get("active") is not False:
    raise SystemExit("memory disable did not soft-disable the block")
PY

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" memory list \
  --active-only \
  --kind fact \
  --url "$BASE_URL" \
  --timeout 10 \
  >"$MEMORY_ACTIVE_AFTER_JSON"

"$PYTHON_BIN" - "$MEMORY_ACTIVE_AFTER_JSON" "$MEMORY_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
memory_id = sys.argv[2]
blocks = payload.get("memory")
if not isinstance(blocks, list):
    raise SystemExit("post-disable memory list did not return a list")
if any(isinstance(block, dict) and block.get("id") == memory_id for block in blocks):
    raise SystemExit("disabled memory block is still present in active memory list")
PY

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" input text \
  "Po wyłączeniu aktywnej pamięci odpowiedz krótko, czy aktywne bloki pamięci zawierają frazę z poprzedniej tury." \
  --url "$BASE_URL" \
  --timeout 180 \
  >"$SECOND_INPUT_JSON"

SECOND_TURN_ID="$("$PYTHON_BIN" - "$SECOND_INPUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload.get("ok") is not True:
    raise SystemExit("second input response did not set ok=true")
turn_id = payload.get("turn_id")
if not isinstance(turn_id, str) or not turn_id:
    raise SystemExit("second input response did not contain turn_id")
turn = payload.get("turn")
if not isinstance(turn, dict):
    raise SystemExit("second input response did not include turn object")
snapshot = turn.get("context_snapshot")
if not isinstance(snapshot, dict):
    raise SystemExit("second turn did not include context_snapshot")
if snapshot.get("memory_block_count") != 0:
    raise SystemExit(f"second turn memory_block_count was not 0: {snapshot}")
print(turn_id)
PY
)"

"$PYTHON_BIN" -m jarvis.cli --config "$CONFIG" events after \
  --id 0 \
  --limit 100 \
  --url "$BASE_URL" \
  >"$EVENTS_JSON"

EVENT_COUNT="$("$PYTHON_BIN" - "$EVENTS_JSON" "$MEMORY_ID" "$FIRST_TURN_ID" "$SECOND_TURN_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
memory_id = sys.argv[2]
first_turn_id = sys.argv[3]
second_turn_id = sys.argv[4]
events = payload.get("events")
if not isinstance(events, list) or not events:
    raise SystemExit("events after returned no events")

memory_events = [
    event for event in events
    if isinstance(event, dict)
    and event.get("type") == "memory.updated"
    and isinstance(event.get("payload"), dict)
    and event["payload"].get("block_id") == memory_id
]
actions = [event["payload"].get("action") for event in memory_events]
if "created" not in actions:
    raise SystemExit(f"memory.updated create event missing: {actions}")
if "disabled" not in actions:
    raise SystemExit(f"memory.updated disable event missing: {actions}")

first_context_events = [
    event for event in events
    if isinstance(event, dict)
    and event.get("type") == "turn.context.built"
    and event.get("turn_id") == first_turn_id
]
if len(first_context_events) != 1:
    raise SystemExit("first turn context event missing")
first_snapshot = first_context_events[0].get("payload", {}).get("context_snapshot")
if not isinstance(first_snapshot, dict) or first_snapshot.get("memory_block_count") != 1:
    raise SystemExit(f"first turn context event memory count unexpected: {first_snapshot}")

second_context_events = [
    event for event in events
    if isinstance(event, dict)
    and event.get("type") == "turn.context.built"
    and event.get("turn_id") == second_turn_id
]
if len(second_context_events) != 1:
    raise SystemExit("second turn context event missing")
second_snapshot = second_context_events[0].get("payload", {}).get("context_snapshot")
if not isinstance(second_snapshot, dict) or second_snapshot.get("memory_block_count") != 0:
    raise SystemExit(f"second turn context event memory count unexpected: {second_snapshot}")

print(len(events))
PY
)"

"$PYTHON_BIN" - "$DB_PATH" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
with sqlite3.connect(db_path) as conn:
    memory_blocks = conn.execute("SELECT COUNT(*) FROM memory_blocks").fetchone()[0]
    active_memory_blocks = conn.execute(
        "SELECT COUNT(*) FROM memory_blocks WHERE active = 1"
    ).fetchone()[0]
    voice_queue = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    worker_jobs = conn.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0]

if memory_blocks != 1:
    raise SystemExit(f"memory_blocks count was not 1: {memory_blocks}")
if active_memory_blocks != 0:
    raise SystemExit(f"active memory_blocks count was not 0: {active_memory_blocks}")
if voice_queue != 0:
    raise SystemExit(f"voice_queue touched unexpectedly: {voice_queue}")
if worker_jobs != 0:
    raise SystemExit(f"worker_jobs touched unexpectedly: {worker_jobs}")
PY

printf 'Memory runtime smoke passed.\n'
printf 'smoke directory: %s\n' "$SMOKE_DIR"
printf 'daemon pid: %s\n' "$DAEMON_PID"
printf 'memory_id: %s\n' "$MEMORY_ID"
printf 'first turn id: %s\n' "$FIRST_TURN_ID"
printf 'second turn id: %s\n' "$SECOND_TURN_ID"
printf 'event count: %s\n' "$EVENT_COUNT"
printf 'memory_blocks: 1\n'
printf 'voice_queue: 0\n'
printf 'worker_jobs: 0\n'
