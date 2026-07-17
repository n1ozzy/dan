#!/usr/bin/env bash
set -euo pipefail

# FAZA D5 smoke: terminal tools through the daemon, on the fake backend
# (deterministic fixture; no TCC needed). Proves: direct user
# terminal_read_screen executes immediately and the secret-looking fixture
# line is redacted in tool_runs/events; terminal_paste NEVER executes
# without an explicit approve+execute (and never echoes its text);
# control characters and Terminal.app paste fail cleanly; both tools land
# in the registry with their own risk classes (terminal_read vs
# terminal_write — never merged, ADR-021). (The model-AP / auto-B matrix
# cells are covered by tests/test_terminal_policy.py — /tools/request
# always assigns the direct_user_command source at the entry point.)

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

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-terminal-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
PORT=41797
BASE_URL="http://127.0.0.1:$PORT"
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

cat >"$CONFIG" <<EOF
[daemon]
name = "dand"
host = "127.0.0.1"
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
ui_read_backend = "fake"
screen_read_backend = "fake"
terminal_backend = "fake"

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
echo "Config: $CONFIG"
echo "Starting daemon: python -m dan.cli --config \"$CONFIG\" daemon run"
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

FIXTURE_SECRET = "sk-faketerminalsecret1234567890"
PASTE_TEXT = "echo DAN_D5_SMOKE_MARKER"


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


def request_json_status(method: str, path: str, payload: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    token = api_token()
    if token and method in {"POST", "PATCH", "DELETE"}:
        headers["X-DAN-Token"] = token
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


def finished_runs() -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM tool_runs WHERE status = 'finished'").fetchone()[0]
    finally:
        conn.close()


# Wait for daemon health.
deadline = time.time() + 30
while True:
    try:
        with urlopen(f"{base_url}/health", timeout=0.5) as response:
            if response.status == 200:
                break
    except Exception:
        pass
    if time.time() > deadline:
        fail("daemon did not become healthy in 30s")
    time.sleep(0.3)

# 1. Both tools are registered, each with its OWN risk class (never merged).
status, listing = request_json_status("GET", "/tools")
if status != 200:
    fail(f"GET /tools failed: {status}")
tools = {tool["name"]: tool for tool in listing.get("tools", [])}
if "terminal_read_screen" not in tools or tools["terminal_read_screen"].get("risk") != "terminal_read":
    fail(f"terminal_read_screen missing or wrong risk: {tools.get('terminal_read_screen')}")
if "terminal_paste" not in tools or tools["terminal_paste"].get("risk") != "terminal_write":
    fail(f"terminal_paste missing or wrong risk: {tools.get('terminal_paste')}")
print("tools listed: terminal_read_screen (terminal_read) + terminal_paste (terminal_write)")

# 2. Direct user terminal_read_screen executes immediately on the fake backend.
status, read = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "terminal_read_screen", "arguments": {"app": "iTerm2"}, "requested_by": "smoke"},
)
if status != 200 or read.get("status") != "finished":
    fail(f"terminal_read_screen did not finish: {status} {read}")
output = read.get("output") or {}
if output.get("backend") != "fake":
    fail(f"expected fake backend, got: {output.get('backend')}")
screen = output.get("screen") or {}
if screen.get("app") != "iTerm2" or not screen.get("lines"):
    fail(f"terminal_read_screen returned no lines: {output}")
print(f"terminal_read_screen: finished, backend=fake, lines={screen.get('line_count')}")

# 3. terminal_paste for a direct user command requires approval and does not
#    execute before the explicit execute step.
runs_before = finished_runs()
status, paste_requested = request_json_status(
    "POST",
    "/tools/request",
    {
        "tool_name": "terminal_paste",
        "arguments": {"app": "iTerm2", "text": PASTE_TEXT},
        "requested_by": "smoke",
    },
)
if status != 200 or paste_requested.get("status") != "approval_required":
    fail(f"terminal_paste did not require approval: {status} {paste_requested}")
if finished_runs() != runs_before:
    fail("a tool run finished before any approval was decided")

approval_id = paste_requested["approval_id"]
status, _ = request_json_status("POST", f"/approvals/{approval_id}/approve")
if status != 200:
    fail(f"approve failed: {status}")
if finished_runs() != runs_before:
    fail("terminal_paste executed after approve without the explicit execute step")

status, paste_executed = request_json_status("POST", f"/approvals/{approval_id}/execute")
if status != 200 or not paste_executed.get("ok"):
    fail(f"terminal_paste execute failed: {status} {paste_executed}")
result = paste_executed.get("result") or {}
if not result.get("pasted") or result.get("chars_pasted") != len(PASTE_TEXT):
    fail(f"terminal_paste reported a wrong result: {result}")
if PASTE_TEXT in json.dumps(result):
    fail("terminal_paste echoed the pasted text in its output")
print("terminal_paste lifecycle: approval -> approve -> execute, no text echo PASS")

# 4. Control characters fail cleanly: the embedded newline would submit the
#    command, so the tool refuses it at execute time.
status, bad_requested = request_json_status(
    "POST",
    "/tools/request",
    {
        "tool_name": "terminal_paste",
        "arguments": {"app": "iTerm2", "text": "rm -rf /\n"},
        "requested_by": "smoke",
    },
)
if status != 200 or bad_requested.get("status") != "approval_required":
    fail(f"control-char paste skipped the approval gate: {status} {bad_requested}")
bad_id = bad_requested["approval_id"]
request_json_status("POST", f"/approvals/{bad_id}/approve")
status, bad_executed = request_json_status("POST", f"/approvals/{bad_id}/execute")
bad_blob = json.dumps(bad_executed)
if status == 200 and bad_executed.get("ok") and (bad_executed.get("result") or {}).get("pasted"):
    fail(f"control-char paste executed: {bad_executed}")
if "control character" not in bad_blob:
    fail(f"control-char paste failed for the wrong reason: {bad_executed}")
print("terminal_paste: control characters rejected")

# 5. Terminal.app paste is unsupported (no paste-without-execute verb).
status, term_requested = request_json_status(
    "POST",
    "/tools/request",
    {
        "tool_name": "terminal_paste",
        "arguments": {"app": "Terminal", "text": "echo hi"},
        "requested_by": "smoke",
    },
)
if status != 200 or term_requested.get("status") != "approval_required":
    fail(f"Terminal.app paste skipped the approval gate: {status} {term_requested}")
term_id = term_requested["approval_id"]
request_json_status("POST", f"/approvals/{term_id}/approve")
status, term_executed = request_json_status("POST", f"/approvals/{term_id}/execute")
if status == 200 and term_executed.get("ok") and (term_executed.get("result") or {}).get("pasted"):
    fail(f"Terminal.app paste executed: {term_executed}")
if "paste without executing" not in json.dumps(term_executed):
    fail(f"Terminal.app paste failed for the wrong reason: {term_executed}")
print("terminal_paste: Terminal.app refused (paste-without-execute only)")

# 6. Unknown app fails cleanly on the read side too.
status, bad_app = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "terminal_read_screen", "arguments": {"app": "Safari"}, "requested_by": "smoke"},
)
if status != 200 or bad_app.get("status") != "failed":
    fail(f"unknown app did not fail cleanly: {status} {bad_app}")
print("terminal_read_screen: unknown app rejected")

# 7. DB-level assertions: the fixture secret and the pasted text never
#    persist unredacted / echoed in outputs.
conn = sqlite3.connect(db_path)
try:
    stored_outputs = json.dumps(
        [row[0] for row in conn.execute("SELECT output_json FROM tool_runs").fetchall()]
    )
    events_raw = json.dumps(
        [row[0] for row in conn.execute("SELECT payload_json FROM events").fetchall()]
    )
    total_finished = finished_runs()
finally:
    conn.close()

if FIXTURE_SECRET in stored_outputs:
    fail("fixture secret persisted unredacted in tool_runs")
if "[REDACTED]" not in stored_outputs:
    fail("tool_runs output shows no redaction marker for the fixture secret")
if FIXTURE_SECRET in events_raw:
    fail("fixture secret persisted unredacted in events")
if PASTE_TEXT in stored_outputs:
    fail("pasted text echoed into tool_runs output")
if total_finished != 2:
    fail(f"expected exactly 2 finished tool_runs (read + paste), found {total_finished}")

print(f"finished tool_runs: {total_finished}")
print("fixture secret in DB: REDACTED (PASS)")
PY

echo "Terminal smoke passed."
