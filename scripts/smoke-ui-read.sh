#!/usr/bin/env bash
set -euo pipefail

# FAZA D1 smoke: read-only Accessibility tools through the daemon, on the
# fake backend (deterministic fixture; no TCC needed). Proves: direct user
# ui_active_app/ui_read_window execute immediately, every payload announces
# backend=fake, the secure text field value from the fixture never appears
# in API responses, tool_runs or events, and the tools land in the registry
# with risk=ui_read.

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

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-ui-read-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
BASE_URL="http://127.0.0.1:41772"
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
port = 41772
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

SECURE_FIXTURE_VALUE = "fake-secure-value"


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

# 1. The ui tools are registered with risk=ui_read.
status, listing = request_json_status("GET", "/tools")
if status != 200:
    fail(f"GET /tools failed: {status}")
tools = {tool["name"]: tool for tool in listing.get("tools", [])}
for name in ("ui_active_app", "ui_read_window"):
    if name not in tools or tools[name].get("risk") != "ui_read":
        fail(f"{name} missing or wrong risk: {tools.get(name)}")
print("tools listed: ui_active_app + ui_read_window (risk=ui_read)")

# 2. Direct user ui_active_app executes immediately on the fake backend.
status, active = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "ui_active_app", "arguments": {}, "requested_by": "smoke"},
)
if status != 200 or active.get("status") != "finished":
    fail(f"ui_active_app did not finish: {status} {active}")
output = active.get("output") or {}
if output.get("backend") != "fake":
    fail(f"expected fake backend, got: {output.get('backend')}")
app_name = (output.get("app") or {}).get("app_name")
if not app_name:
    fail(f"ui_active_app returned no app name: {output}")
print(f"ui_active_app: finished, backend=fake, app={app_name}")

# 3. Direct user ui_read_window executes; the secure field value never leaks.
status, window = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "ui_read_window", "arguments": {}, "requested_by": "smoke"},
)
if status != 200 or window.get("status") != "finished":
    fail(f"ui_read_window did not finish: {status} {window}")
dumped = json.dumps(window, ensure_ascii=False)
if SECURE_FIXTURE_VALUE in dumped:
    fail("secure field value leaked into the API response")
elements = ((window.get("output") or {}).get("window") or {}).get("elements") or []
secure_elements = [element for element in elements if element.get("secure")]
if not secure_elements:
    fail(f"fixture secure element missing from the snapshot: {elements}")
if any(element.get("value") is not None for element in secure_elements):
    fail(f"secure element kept a value: {secure_elements}")
print(f"ui_read_window: finished, elements={len(elements)}, secure value stripped PASS")

# 4. DB-level assertions: two ToolRuns, secure value absent everywhere.
conn = sqlite3.connect(db_path)
try:
    tool_runs = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    if tool_runs != 2:
        fail(f"expected exactly 2 tool_runs, found {tool_runs}")
    stored_outputs = json.dumps(
        [row[0] for row in conn.execute("SELECT output_json FROM tool_runs").fetchall()]
    )
    if SECURE_FIXTURE_VALUE in stored_outputs:
        fail("secure field value persisted in tool_runs")
    events_raw = json.dumps(
        [row[0] for row in conn.execute("SELECT payload_json FROM events").fetchall()]
    )
    if SECURE_FIXTURE_VALUE in events_raw:
        fail("secure field value persisted in events")
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
finally:
    conn.close()

print(f"tool_runs: {tool_runs}")
print("secure value in DB: ABSENT (PASS)")
print(f"event count: {event_count}")
PY

echo "UI read smoke passed."
