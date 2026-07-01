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
PORT="41772"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-tool-continuation-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
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

# Deterministic fake local CLI brain. First call: emit a model-originated
# approval_probe tool-call block. Continuation call (marker present in the
# prompt): emit the plain continuation answer. No real providers, no network.
cat >"$FAKE_BRAIN" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
PROMPT="$(cat)"
if printf '%s' "$PROMPT" | grep -q "Continuation after approved tool execution"; then
  printf 'Continuation smoke answer: approved tool result received.\n'
else
  printf 'Need continuation tool approval.\n'
  printf '<jarvis_tool_call>{"name":"approval_probe","arguments":{"reason":"continuation smoke"}}</jarvis_tool_call>\n'
fi
FAKE
chmod +x "$FAKE_BRAIN"

# database.path and runtime.home are smoke-local; runtime.logs_dir,
# runtime.runtime_dir and runtime.pid_file stay inside SMOKE_DIR.
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

echo "Smoke directory: $SMOKE_DIR"
echo "Config: $CONFIG"
echo "Fake brain: $FAKE_BRAIN"
echo "Starting daemon: python -m jarvis.cli --config \"$CONFIG\" daemon run"
"$PYTHON" -m jarvis.cli --config "$CONFIG" daemon run >"$SMOKE_DIR/daemon.stdout.log" 2>"$SMOKE_DIR/daemon.stderr.log" &
DAEMON_PID="$!"
echo "Daemon PID: $DAEMON_PID"

BASE_URL="$BASE_URL" DB_PATH="$DB_PATH" DAEMON_PID="$DAEMON_PID" SMOKE_DIR="$SMOKE_DIR" "$PYTHON" <<'PY'
import json
import os
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base_url = os.environ["BASE_URL"]
db_path = os.environ["DB_PATH"]
daemon_pid = os.environ["DAEMON_PID"]
smoke_dir = os.environ["SMOKE_DIR"]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    stdout_log = os.path.join(smoke_dir, "daemon.stdout.log")
    stderr_log = os.path.join(smoke_dir, "daemon.stderr.log")
    for label, path in (("daemon stdout", stdout_log), ("daemon stderr", stderr_log)):
        if os.path.exists(path):
            print(f"--- {label} ---", file=sys.stderr)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                print(handle.read()[-4000:], file=sys.stderr)
    raise SystemExit(1)


def raw_request_json(method: str, path: str, payload: dict | None = None, timeout: float = 5) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_json_status(method: str, path: str, payload: dict | None = None, timeout: float = 30) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
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
        health = raw_request_json("GET", "/health", timeout=1)
        if health.get("ok") is True and health.get("started") is True:
            break
        last_error = f"unhealthy response: {health}"
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.25)
else:
    fail(f"daemon health timeout: {last_error}")

# 1. Model-originated tool request through the text pipeline: the fake local
#    CLI brain answers with an approval_probe tool-call block, so the turn
#    must persist as awaiting_approval with one pending approval.
input_payload = request_json(
    "POST",
    "/input/text",
    {"text": "Trigger the continuation demo tool"},
)
turn_id = input_payload.get("turn_id")
if not isinstance(turn_id, str) or not turn_id:
    fail(f"/input/text did not return turn_id: {input_payload}")
if input_payload.get("turn", {}).get("status") != "awaiting_approval":
    fail(f"turn is not awaiting_approval: {input_payload}")
tool_calls = input_payload.get("tool_calls", [])
if len(tool_calls) != 1 or tool_calls[0].get("tool_name") != "approval_probe":
    fail(f"expected one approval_probe tool call: {input_payload}")
if tool_calls[0].get("status") != "approval_required":
    fail(f"tool call is not approval_required: {input_payload}")
approvals = input_payload.get("approvals", [])
if len(approvals) != 1 or approvals[0].get("status") != "pending":
    fail(f"expected one pending approval: {input_payload}")
approval_id = approvals[0].get("id")
if not isinstance(approval_id, str) or not approval_id:
    fail(f"approval id missing: {input_payload}")

state_payload = request_json("GET", "/state")
if state_payload.get("pending_approval_count") != 1:
    fail(f"pending_approval_count is not 1: {state_payload}")

with sqlite3.connect(db_path) as conn:
    persisted_status = conn.execute(
        "SELECT status FROM turns WHERE id = ?", (turn_id,)
    ).fetchone()[0]
    runs_before_approve = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
if persisted_status != "awaiting_approval":
    fail(f"persisted turn status is not awaiting_approval: {persisted_status}")
if runs_before_approve != 0:
    fail(f"tool_runs created before approval: {runs_before_approve}")

# 2. Approve does not execute.
approved_payload = request_json(
    "POST",
    f"/approvals/{approval_id}/approve",
    {"reason": "manual continuation smoke approve"},
)
if approved_payload.get("approval", {}).get("status") != "approved":
    fail(f"approve endpoint did not approve: {approved_payload}")

with sqlite3.connect(db_path) as conn:
    runs_after_approve = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    status_after_approve = conn.execute(
        "SELECT status FROM turns WHERE id = ?", (turn_id,)
    ).fetchone()[0]
if runs_after_approve != 0:
    fail(f"approve unexpectedly executed the tool: {runs_after_approve}")
if status_after_approve != "awaiting_approval":
    fail(f"approve changed turn status unexpectedly: {status_after_approve}")

# 3. Explicit execute-approved runs the tool once and continues the turn.
execute_payload = request_json("POST", f"/approvals/{approval_id}/execute")
if execute_payload.get("ok") is not True:
    fail(f"execute endpoint did not return ok true: {execute_payload}")
execute_run = execute_payload.get("tool_run") or {}
if execute_run.get("approval_id") != approval_id or execute_run.get("status") != "finished":
    fail(f"execute did not record finished tool_run: {execute_payload}")
continuation = execute_payload.get("continuation") or {}
if continuation.get("applied") is not True or continuation.get("status") != "finished":
    fail(f"continuation was not applied/finished: {execute_payload}")
if continuation.get("turn_id") != turn_id:
    fail(f"continuation turn_id mismatch: {execute_payload}")
continuation_text = continuation.get("final_text") or ""
if "Continuation smoke answer" not in continuation_text:
    fail(f"continuation final_text is not the fake brain answer: {execute_payload}")

with sqlite3.connect(db_path) as conn:
    row = conn.execute(
        "SELECT status, final_text, metadata_json FROM turns WHERE id = ?",
        (turn_id,),
    ).fetchone()
final_status, final_text, metadata_json = row
if final_status != "finished":
    fail(f"original turn did not become finished: {final_status}")
if final_text != continuation_text:
    fail(f"persisted final_text differs from continuation answer: {final_text!r}")
metadata = json.loads(metadata_json or "{}")
continuation_meta = metadata.get("tool_result_continuation") or {}
if continuation_meta.get("approval_id") != approval_id:
    fail(f"tool_result_continuation approval_id mismatch: {continuation_meta}")
if continuation_meta.get("tool_name") != "approval_probe":
    fail(f"tool_result_continuation tool_name mismatch: {continuation_meta}")
if continuation_meta.get("status") != "finished":
    fail(f"tool_result_continuation status is not finished: {continuation_meta}")
if continuation_meta.get("continuation_eligible") is not True:
    fail(f"tool_result_continuation is not continuation_eligible: {continuation_meta}")

state_after = request_json("GET", "/state")
if state_after.get("pending_approval_count") != 0:
    fail(f"pending_approval_count did not return to 0: {state_after}")

# 4. Duplicate execute: no duplicate ToolRun, no duplicate continuation.
duplicate_status, duplicate_payload = request_json_status(
    "POST", f"/approvals/{approval_id}/execute"
)
if duplicate_status != 409:
    fail(f"duplicate execute did not return 409: status={duplicate_status} body={duplicate_payload}")
if "already executed" not in str(duplicate_payload.get("error", "")):
    fail(f"duplicate execute did not report duplicate prevention: {duplicate_payload}")

# 5. Timeline and side-effect checks.
events_payload = request_json("GET", "/events?after_id=0&limit=200")
events = events_payload.get("events", [])
event_types = [event.get("type") for event in events]
required_events = {
    "brain.requested",
    "brain.responded",
    "turn.finished",
    "approval.created",
    "approval.approved",
    "tool.started",
    "tool.finished",
}
missing_events = sorted(required_events - set(event_types))
if missing_events:
    fail(f"GET /events missing {missing_events}: {event_types}")
brain_requested_count = event_types.count("brain.requested")
if brain_requested_count != 2:
    fail(f"expected 2 brain.requested events (turn + continuation): {brain_requested_count}")

with sqlite3.connect(db_path) as conn:
    tool_runs = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    approval_runs = conn.execute(
        "SELECT COUNT(*) FROM tool_runs WHERE approval_id = ?", (approval_id,)
    ).fetchone()[0]
    worker_jobs = conn.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0]
    voice_queue = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]

if tool_runs != 1:
    fail(f"tool_runs count is not exactly 1: {tool_runs}")
if approval_runs != 1:
    fail(f"tool_runs for approval is not exactly 1: {approval_runs}")
if worker_jobs != 0:
    fail(f"worker_jobs touched unexpectedly: {worker_jobs}")
if voice_queue != 0:
    fail(f"voice_queue touched unexpectedly: {voice_queue}")

print("Tool continuation smoke passed")
print(f"smoke directory: {smoke_dir}")
print(f"daemon pid: {daemon_pid}")
print(f"turn id: {turn_id}")
print(f"approval id: {approval_id}")
print(f"tool run id/status: {execute_run.get('id')}/{execute_run.get('status')}")
print(f"continuation status: {continuation.get('status')}")
print(f"final_text: {final_text}")
print(f"duplicate execute status: {duplicate_status}")
print(f"event count: {len(events)}")
print("tool_runs: 1")
print("worker_jobs: 0")
print("voice_queue: 0")
PY
