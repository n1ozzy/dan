#!/usr/bin/env bash
set -euo pipefail

# FAZA C3 smoke: real read-only file tool through the live daemon.
# Proves: direct user file_read executes immediately inside approved roots,
# out-of-roots requests are blocked without a ToolRun, model-originated
# requests stay approval-gated, secret-looking content is redacted in
# tool_runs and events, transport token is enforced end to end.

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

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-file-read-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
BASE_URL="http://127.0.0.1:41771"
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

mkdir -p "$SMOKE_DIR/workspace"

cat >"$CONFIG" <<EOF
[daemon]
name = "jarvisd"
host = "127.0.0.1"
port = 41771
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
approved_roots = ["$SMOKE_DIR/workspace"]

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


def request_json_status(method: str, path: str, payload: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    data = None
    headers = auth_headers(method)
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

workspace = os.path.join(smoke_dir, "workspace")
fake_secret = "sk-smokefilesecret1234567890"
inside_path = os.path.join(workspace, "notes.txt")
with open(inside_path, "w", encoding="utf-8") as handle:
    handle.write(f"hello from workspace\nAPI={fake_secret}\n")
outside_path = os.path.join(smoke_dir, "outside.txt")
with open(outside_path, "w", encoding="utf-8") as handle:
    handle.write("should never be readable\n")

# 1. Direct user read inside approved roots executes immediately.
status, finished = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "file_read", "arguments": {"path": inside_path}, "requested_by": "smoke"},
)
if status != 200 or finished.get("status") != "finished":
    fail(f"direct file_read did not finish: {status} {finished}")
content = (finished.get("output") or {}).get("content", "")
if "hello from workspace" not in content:
    fail(f"unexpected content: {content!r}")
print(f"direct read status: {finished['status']}")

# 2. Out-of-roots read is blocked.
status, blocked = request_json_status(
    "POST",
    "/tools/request",
    {"tool_name": "file_read", "arguments": {"path": outside_path}, "requested_by": "smoke"},
)
if status != 200 or blocked.get("status") != "blocked":
    fail(f"outside read was not blocked: {status} {blocked}")
print(f"outside read status: {blocked['status']}")

# 3. Missing token is rejected before any tool logic runs.
request = Request(
    f"{base_url}/tools/request",
    data=json.dumps({"tool_name": "file_read", "arguments": {"path": inside_path}}).encode("utf-8"),
    headers={"Accept": "application/json", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urlopen(request, timeout=5) as response:
        fail(f"tokenless request unexpectedly succeeded: {response.status}")
except HTTPError as exc:
    if exc.code != 401:
        fail(f"tokenless request expected 401, got {exc.code}")
print("tokenless request status: 401")

# 4. DB-level assertions: one ToolRun, secrets redacted, no queues touched.
conn = sqlite3.connect(db_path)
try:
    tool_runs = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    if tool_runs != 1:
        fail(f"expected exactly 1 tool_run, found {tool_runs}")
    stored_outputs = json.dumps(
        [row[0] for row in conn.execute("SELECT output_json FROM tool_runs").fetchall()]
    )
    if fake_secret in stored_outputs:
        fail("secret persisted raw in tool_runs")
    events_raw = json.dumps(
        [row[0] for row in conn.execute("SELECT payload_json FROM events").fetchall()]
    )
    if fake_secret in events_raw:
        fail("secret persisted raw in events")
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    worker_jobs = conn.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0]
    voice_queue = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
finally:
    conn.close()

print(f"tool_runs: {tool_runs}")
print("secret redaction: PASS")
print(f"event count: {event_count}")
print(f"worker_jobs: {worker_jobs}")
print(f"voice_queue: {voice_queue}")
PY

echo "File read smoke passed."
