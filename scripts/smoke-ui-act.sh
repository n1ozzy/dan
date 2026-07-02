#!/usr/bin/env bash
set -euo pipefail

# FAZA D2 smoke: UI action tools through the daemon on the fake actor.
# Proves: ui_click/ui_type never execute without an explicit approve+execute
# step even for a direct user command, the full lifecycle works end to end,
# typed text is not echoed back into tool outputs, and the tools carry
# risk=ui_act. The auto-blocked and secure-field cells are unit-tested
# (tests/test_ui_act_policy.py, tests/test_ui_act_tools.py).

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

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-ui-act-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
BASE_URL="http://127.0.0.1:41773"
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
name = "jarvisd"
host = "127.0.0.1"
port = 41773
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

TYPED_MARKER = "smoke-typed-text-marker"


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
        headers["X-Jarvis-Token"] = token
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


def finished_runs(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM tool_runs WHERE status = 'finished'").fetchone()[0]


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

# 1. Action tools are registered with risk=ui_act.
status, listing = request_json_status("GET", "/tools")
if status != 200:
    fail(f"GET /tools failed: {status}")
tools = {tool["name"]: tool for tool in listing.get("tools", [])}
for name in ("ui_click", "ui_type", "ui_focus_app"):
    if name not in tools or tools[name].get("risk") != "ui_act":
        fail(f"{name} missing or wrong risk: {tools.get(name)}")
print("tools listed: ui_click + ui_type + ui_focus_app (risk=ui_act)")

# 2. ui_click for a direct user command still requires approval and does
#    not execute before the explicit execute step.
status, click_requested = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "ui_click", "arguments": {"label": "Zaloguj"}, "requested_by": "smoke"},
)
if status != 200 or click_requested.get("status") != "approval_required":
    fail(f"ui_click did not require approval: {status} {click_requested}")
conn = sqlite3.connect(db_path)
try:
    if finished_runs(conn) != 0:
        fail("a tool run finished before any approval was decided")
finally:
    conn.close()
approval_id = click_requested["approval_id"]
status, _ = request_json_status("POST", f"/approvals/{approval_id}/approve")
if status != 200:
    fail(f"ui_click approve failed: {status}")
conn = sqlite3.connect(db_path)
try:
    if finished_runs(conn) != 0:
        fail("ui_click executed after approve without the explicit execute step")
finally:
    conn.close()
status, click_executed = request_json_status("POST", f"/approvals/{approval_id}/execute")
if status != 200 or not click_executed.get("ok"):
    fail(f"ui_click execute failed: {status} {click_executed}")
result = click_executed.get("result") or {}
if result.get("backend") != "fake" or not result.get("clicked"):
    fail(f"unexpected ui_click result: {result}")
print("ui_click lifecycle: approval -> approve -> execute -> clicked PASS")

# 3. ui_type lifecycle; typed text is not echoed into the output.
status, type_requested = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "ui_type", "arguments": {"text": TYPED_MARKER}, "requested_by": "smoke"},
)
if status != 200 or type_requested.get("status") != "approval_required":
    fail(f"ui_type did not require approval: {status} {type_requested}")
approval_id = type_requested["approval_id"]
request_json_status("POST", f"/approvals/{approval_id}/approve")
status, type_executed = request_json_status("POST", f"/approvals/{approval_id}/execute")
if status != 200 or not type_executed.get("ok"):
    fail(f"ui_type execute failed: {status} {type_executed}")
result = type_executed.get("result") or {}
if result.get("chars_typed") != len(TYPED_MARKER):
    fail(f"unexpected ui_type result: {result}")
if TYPED_MARKER in json.dumps(result):
    fail("typed text was echoed back in the ui_type result")
print("ui_type lifecycle: approval -> execute, no text echo PASS")

# 4. DB-level assertions.
conn = sqlite3.connect(db_path)
try:
    if finished_runs(conn) != 2:
        fail("expected exactly 2 finished tool_runs")
    outputs = json.dumps(
        [row[0] for row in conn.execute("SELECT output_json FROM tool_runs").fetchall()]
    )
    if TYPED_MARKER in outputs:
        fail("typed text persisted in tool_runs outputs")
    approvals = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'approved'").fetchone()[0]
    if approvals != 2:
        fail(f"expected 2 approved approvals, found {approvals}")
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
finally:
    conn.close()

print("tool_runs finished: 2, approvals approved: 2")
print("typed text in outputs: ABSENT (PASS)")
print(f"event count: {event_count}")
PY

echo "UI act smoke passed."
