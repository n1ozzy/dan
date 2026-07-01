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

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-tools-approvals-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
BASE_URL="http://127.0.0.1:41769"
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

# database.path and runtime.home are smoke-local; runtime.logs_dir,
# runtime.runtime_dir and runtime.pid_file stay inside SMOKE_DIR.
cat >"$CONFIG" <<EOF
[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = 41769
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


def api_token() -> str | None:
    token_path = os.path.join(smoke_dir, "runtime", "api-token")
    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        return None
    return token or None


def auth_headers(method: str) -> dict:
    headers = {"Accept": "application/json"}
    token = api_token()
    if token and method in {"POST", "PATCH", "DELETE"}:
        headers["X-Jarvis-Token"] = token
    return headers


def raw_request_json(method: str, path: str, payload: dict | None = None, timeout: float = 5) -> dict:
    data = None
    headers = auth_headers(method)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_json_status(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = auth_headers(method)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": str(exc)}
        return exc.code, body
    except (TimeoutError, URLError, OSError) as exc:
        fail(f"{method} {path} failed: {exc}")


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    status, body = request_json_status(method, path, payload)
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

tools_payload = request_json("GET", "/tools")
tools = {tool["name"]: tool for tool in tools_payload.get("tools", [])}
for name in ("echo", "system_status", "approval_probe"):
    if name not in tools:
        fail(f"GET /tools missing {name}: {tools_payload}")
if tools["approval_probe"].get("risk") != "shell_read":
    fail(f"approval_probe risk is not shell_read: {tools['approval_probe']}")

echo_payload = request_json(
    "POST",
    "/tools/request",
    {
        "tool_name": "echo",
        "arguments": {"text": "hello tools smoke"},
        "requested_by": "manual_smoke",
    },
)
if echo_payload.get("status") != "finished":
    fail(f"echo did not finish: {echo_payload}")
echo_output = echo_payload.get("output") or {}
if echo_output.get("arguments", {}).get("text") != "hello tools smoke" and echo_output.get("ok") is not True:
    fail(f"echo output did not reflect arguments: {echo_payload}")

approval_payload = request_json(
    "POST",
    "/tools/request",
    {
        "tool_name": "approval_probe",
        "arguments": {"purpose": "manual smoke approval"},
        "requested_by": "manual_smoke",
    },
)
if approval_payload.get("status") != "approval_required":
    fail(f"approval_probe did not require approval: {approval_payload}")
approval_id = approval_payload.get("approval_id")
if not isinstance(approval_id, str) or not approval_id:
    fail(f"approval_probe did not return approval_id: {approval_payload}")
if approval_payload.get("output") is not None:
    fail(f"approval_probe unexpectedly executed: {approval_payload}")

approvals_payload = request_json("GET", "/approvals")
pending = approvals_payload.get("approvals", [])
if not any(approval.get("id") == approval_id and approval.get("status") == "pending" for approval in pending):
    fail(f"GET /approvals did not include pending approval {approval_id}: {approvals_payload}")

approved_payload = request_json(
    "POST",
    f"/approvals/{approval_id}/approve",
    {"reason": "manual smoke approve endpoint check"},
)
if approved_payload.get("approval", {}).get("status") != "approved":
    fail(f"approve endpoint did not approve: {approved_payload}")

with sqlite3.connect(db_path) as conn:
    probe_runs_before_execute = conn.execute(
        "SELECT COUNT(*) FROM tool_runs WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()[0]
if probe_runs_before_execute != 0:
    fail(f"approve endpoint unexpectedly executed approval_probe: {probe_runs_before_execute}")

execute_payload = request_json("POST", f"/approvals/{approval_id}/execute")
if execute_payload.get("ok") is not True:
    fail(f"execute endpoint did not return ok true: {execute_payload}")
if execute_payload.get("approval_id") != approval_id:
    fail(f"execute endpoint returned wrong approval_id: {execute_payload}")
execute_result = execute_payload.get("result") or {}
if execute_result.get("ok") is not True:
    fail(f"approval_probe execute result was not harmless ok true: {execute_payload}")
execute_run = execute_payload.get("tool_run") or {}
if execute_run.get("approval_id") != approval_id or execute_run.get("status") != "finished":
    fail(f"execute endpoint did not return finished tool_run for approval: {execute_payload}")

duplicate_status, duplicate_payload = request_json_status("POST", f"/approvals/{approval_id}/execute")
if duplicate_status != 409:
    fail(f"duplicate execute did not return 409: status={duplicate_status} body={duplicate_payload}")
if "already executed" not in str(duplicate_payload.get("error", "")):
    fail(f"duplicate execute did not report duplicate prevention: {duplicate_payload}")

reject_request = request_json(
    "POST",
    "/tools/request",
    {
        "tool_name": "approval_probe",
        "arguments": {"purpose": "manual smoke reject"},
        "requested_by": "manual_smoke",
    },
)
reject_id = reject_request.get("approval_id")
if reject_request.get("status") != "approval_required" or not isinstance(reject_id, str):
    fail(f"second approval_probe did not create rejectable approval: {reject_request}")
rejected_payload = request_json(
    "POST",
    f"/approvals/{reject_id}/reject",
    {"reason": "manual smoke reject endpoint check"},
)
if rejected_payload.get("approval", {}).get("status") != "rejected":
    fail(f"reject endpoint did not reject: {rejected_payload}")
rejected_execute_status, rejected_execute_payload = request_json_status(
    "POST",
    f"/approvals/{reject_id}/execute",
)
if rejected_execute_status != 409:
    fail(
        "rejected approval execute did not return 409: "
        f"status={rejected_execute_status} body={rejected_execute_payload}"
    )

# events after-style check via GET /events?after_id=0.
events_payload = request_json("GET", "/events?after_id=0&limit=100")
events = events_payload.get("events", [])
event_types = {event.get("type") for event in events}
required_events = {
    "tool.requested",
    "tool.started",
    "tool.finished",
    "approval.created",
    "approval.approved",
    "approval.rejected",
}
missing_events = sorted(required_events - event_types)
if missing_events:
    fail(f"GET /events missing {missing_events}: {events_payload}")

with sqlite3.connect(db_path) as conn:
    worker_jobs = conn.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0]
    voice_queue = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    probe_runs = conn.execute(
        "SELECT COUNT(*) FROM tool_runs WHERE tool_name = 'approval_probe'"
    ).fetchone()[0]
    probe_approval_runs = conn.execute(
        "SELECT COUNT(*) FROM tool_runs WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()[0]
    rejected_runs = conn.execute(
        "SELECT COUNT(*) FROM tool_runs WHERE approval_id = ?",
        (reject_id,),
    ).fetchone()[0]
    echo_runs = conn.execute("SELECT COUNT(*) FROM tool_runs WHERE tool_name = 'echo'").fetchone()[0]

if worker_jobs != 0:
    fail(f"worker_jobs touched unexpectedly: {worker_jobs}")
if voice_queue != 0:
    fail(f"voice_queue touched unexpectedly: {voice_queue}")
if probe_runs != 1:
    fail(f"approval_probe tool run count unexpected: {probe_runs}")
if probe_approval_runs != 1:
    fail(f"approval_probe approval tool_run count unexpected: {probe_approval_runs}")
if rejected_runs != 0:
    fail(f"rejected approval executed unexpectedly: {rejected_runs}")
if echo_runs != 1:
    fail(f"echo tool run count unexpected: {echo_runs}")

print("Tools approvals smoke passed")
print(f"smoke directory: {smoke_dir}")
print(f"daemon pid: {daemon_pid}")
print(f"echo tool run id/status: {echo_payload.get('id')}/{echo_payload.get('status')}")
print(f"approval id: {approval_id}")
print(f"approval execute run id/status: {execute_run.get('id')}/{execute_run.get('status')}")
print(f"duplicate execute status: {duplicate_status}")
print(f"rejected execute status: {rejected_execute_status}")
print(f"event count: {len(events)}")
print("worker_jobs: 0")
print("voice_queue: 0")
PY
