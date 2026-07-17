#!/usr/bin/env bash
set -euo pipefail

# FAZA D4 smoke: screen_read tools through the daemon, on the fake backend
# (deterministic fixture; no TCC needed). Proves: direct user
# screen_read_window/screen_ocr_region execute immediately, every payload
# announces backend=fake, the secret-looking fixture line is redacted in
# tool_runs and events, invalid regions fail cleanly, and the tools land in
# the registry with risk=screen_read. (The model-AP / auto-B matrix cells
# are covered by tests/test_screen_read_policy.py — /tools/request always
# assigns the direct_user_command source at the entry point.)

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

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-screen-read-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
PORT=41793
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

FIXTURE_SECRET = "sk-fakescreensecret1234567890"


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

# 1. The screen tools are registered with risk=screen_read.
status, listing = request_json_status("GET", "/tools")
if status != 200:
    fail(f"GET /tools failed: {status}")
tools = {tool["name"]: tool for tool in listing.get("tools", [])}
for name in ("screen_read_window", "screen_ocr_region"):
    if name not in tools or tools[name].get("risk") != "screen_read":
        fail(f"{name} missing or wrong risk: {tools.get(name)}")
print("tools listed: screen_read_window + screen_ocr_region (risk=screen_read)")

# 2. Direct user screen_read_window executes immediately on the fake backend,
#    and the secret-looking fixture line is redacted in the API response.
status, window = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "screen_read_window", "arguments": {}, "requested_by": "smoke"},
)
if status != 200 or window.get("status") != "finished":
    fail(f"screen_read_window did not finish: {status} {window}")
output = window.get("output") or {}
if output.get("backend") != "fake":
    fail(f"expected fake backend, got: {output.get('backend')}")
screen = output.get("screen") or {}
if screen.get("source") != "window" or not screen.get("lines"):
    fail(f"screen_read_window returned no OCR lines: {output}")
print(f"screen_read_window: finished, backend=fake, lines={screen.get('line_count')}")

# 3. Direct user screen_ocr_region echoes the region and returns lines.
status, region = request_json_status(
    "POST",
    "/tools/request",
    {
        "tool_name": "screen_ocr_region",
        "arguments": {"x": 10, "y": 20, "width": 640, "height": 480},
        "requested_by": "smoke",
    },
)
if status != 200 or region.get("status") != "finished":
    fail(f"screen_ocr_region did not finish: {status} {region}")
region_echo = ((region.get("output") or {}).get("screen") or {}).get("region")
if region_echo != {"x": 10, "y": 20, "width": 640, "height": 480}:
    fail(f"screen_ocr_region echoed a wrong region: {region_echo}")
print("screen_ocr_region: finished, region echoed")

# 4. Invalid region fails cleanly and executes nothing.
status, bad = request_json_status(
    "POST",
    "/tools/request",
    {
        "tool_name": "screen_ocr_region",
        "arguments": {"x": -5, "y": 0, "width": 100, "height": 100},
        "requested_by": "smoke",
    },
)
if status != 200 or bad.get("status") != "failed":
    fail(f"invalid region did not fail cleanly: {status} {bad}")
print("screen_ocr_region: invalid region rejected")

# 5. DB-level assertions: the fixture secret never persists unredacted.
conn = sqlite3.connect(db_path)
try:
    stored_outputs = json.dumps(
        [row[0] for row in conn.execute("SELECT output_json FROM tool_runs").fetchall()]
    )
    events_raw = json.dumps(
        [row[0] for row in conn.execute("SELECT payload_json FROM events").fetchall()]
    )
    finished_runs = conn.execute(
        "SELECT COUNT(*) FROM tool_runs WHERE status = 'finished'"
    ).fetchone()[0]
finally:
    conn.close()

if FIXTURE_SECRET in stored_outputs:
    fail("fixture secret persisted unredacted in tool_runs")
if "[REDACTED]" not in stored_outputs:
    fail("tool_runs output shows no redaction marker for the fixture secret")
if FIXTURE_SECRET in events_raw:
    fail("fixture secret persisted unredacted in events")
if finished_runs != 2:
    fail(f"expected exactly 2 finished tool_runs, found {finished_runs}")

print(f"finished tool_runs: {finished_runs}")
print("fixture secret in DB: REDACTED (PASS)")
PY

echo "Screen read smoke passed."
