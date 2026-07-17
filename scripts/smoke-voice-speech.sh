#!/usr/bin/env bash
set -euo pipefail

# G3 speech smoke: finished turns are sentence-chunked into the persisted
# voice_queue and played (mock engine) by the broker in order; a slow brain
# triggers exactly one filler; tool-call blocks are never spoken.

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
PORT="41773"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-voice-speech-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
FAKE_BRAIN="$SMOKE_DIR/fake-brain.sh"
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

# Fake CLI brain: SLOW_TURN sleeps past the filler threshold; TOOL_TURN puts
# a tool-call block in the middle of spoken text; anything else answers with
# two clean sentences.
cat >"$FAKE_BRAIN" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
# Since G4d a speech-enabled daemon calls the CLI in stream-json mode; the
# fake answers in whichever format it was asked for (fake-brain pattern).
STREAM=0
for arg in "$@"; do
  if [ "$arg" = "stream-json" ]; then STREAM=1; fi
done
emit() {
  if [ "$STREAM" = "1" ]; then
    escaped="$(printf '%s' "$1" | sed 's/"/\\"/g')"
    printf '{"type":"result","subtype":"success","is_error":false,"result":"%s"}\n' "$escaped"
  else
    printf '%s\n' "$1"
  fi
}
PROMPT="$(cat)"
if printf '%s' "$PROMPT" | tail -n 6 | grep -q "SLOW_TURN"; then
  sleep 2
  emit 'Wolna odpowiedz przyszla teraz. Drugie zdanie wolnej odpowiedzi.'
elif printf '%s' "$PROMPT" | tail -n 6 | grep -q "TOOL_TURN"; then
  emit 'Zaraz sprawdze plik dla ciebie. <dan_tool_call>{"name":"approval_probe","arguments":{"reason":"voice smoke"}}</dan_tool_call>'
else
  emit 'Pierwsze zdanie odpowiedzi glosowej. Drugie zdanie odpowiedzi glosowej.'
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
enabled = true
speak_responses = true
broker_enabled = true
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true
recorder = "mock"
filler_after_ms = 800
fillers = ["A spierdalaj."]

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


def post(path: str, payload: dict, timeout: float = 60) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-DAN-Token": api_token(),
    }
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, TimeoutError, URLError, OSError) as exc:
        fail(f"POST {path} failed: {exc}")


def rows(turn_id: str):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT text, status, metadata_json FROM voice_queue WHERE turn_id = ? ORDER BY rowid",
            (turn_id,),
        ).fetchall()


def wait_all_done(turn_id: str, minimum: int, timeout: float = 15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = rows(turn_id)
        if len(current) >= minimum and all(r[1] == "done" for r in current):
            return current
        time.sleep(0.1)
    fail(f"voice queue for {turn_id} did not drain: {rows(turn_id)}")


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

# 1. A finished turn is spoken sentence by sentence, in order, to done.
turn = post("/input/text", {"text": "Opowiedz mi cos krotkiego"})
turn_id = turn.get("turn_id")
spoken = wait_all_done(turn_id, minimum=2)
texts = [r[0] for r in spoken]
if texts != [
    "Pierwsze zdanie odpowiedzi glosowej.",
    "Drugie zdanie odpowiedzi glosowej.",
]:
    fail(f"unexpected spoken sentences: {texts}")
print("finished turn spoken in order PASS")

# 2. A slow brain triggers exactly one filler before the real sentences.
slow = post("/input/text", {"text": "SLOW_TURN prosze o wolna odpowiedz"})
slow_rows = wait_all_done(slow.get("turn_id"), minimum=3)
kinds = [json.loads(r[2]).get("kind") for r in slow_rows]
if kinds.count("filler") != 1:
    fail(f"expected exactly one filler: {slow_rows}")
if kinds[0] != "filler":
    fail(f"filler did not precede the real sentences: {kinds}")
print("slow turn fired exactly one filler PASS")

# 3. Tool-call blocks are never spoken.
tool = post("/input/text", {"text": "TOOL_TURN zrob cos narzedziem"})
tool_id = tool.get("turn_id")
deadline = time.time() + 10
tool_rows = []
while time.time() < deadline:
    tool_rows = rows(tool_id)
    if tool_rows and all(r[1] == "done" for r in tool_rows):
        break
    time.sleep(0.1)
if not tool_rows:
    fail("tool turn produced no spoken text at all")
for text, _, _ in tool_rows:
    if "tool_call" in text or "approval_probe" in text:
        fail(f"tool call block leaked into speech: {tool_rows}")
print("tool-call block never spoken PASS")

# 4. Events: the frozen voice.speak.* family is on the audit trail.
with sqlite3.connect(db_path) as conn:
    event_types = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT type FROM events WHERE type LIKE 'voice.speak.%'"
        ).fetchall()
    }
for expected in ("voice.speak.queued", "voice.speak.started", "voice.speak.finished"):
    if expected not in event_types:
        fail(f"missing event {expected}: {event_types}")
print("voice.speak.* events present PASS")

print("voice speech smoke passed")
PY

echo "Voice speech smoke passed"
