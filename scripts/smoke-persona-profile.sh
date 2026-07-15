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
PORT="41787"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-persona-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
FAKE_BRAIN="$SMOKE_DIR/fake-brain.sh"
PROMPT_DUMP="$SMOKE_DIR/fake-brain-prompt.txt"
IMPOSTOR_PERSONA="$SMOKE_DIR/impostor-DAN.md"
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

# Deterministic fake local CLI brain. It captures argv because the canonical
# persona travels through --system-prompt, while stdin carries only the turn.
cat >"$FAKE_BRAIN" <<FAKE
#!/bin/sh
printf '%s\n' "\$@" >"$PROMPT_DUMP"
tee -a "$PROMPT_DUMP" >/dev/null
printf 'Fake CLI brain answer for persona smoke.\n'
FAKE
chmod +x "$FAKE_BRAIN"

cat >"$IMPOSTOR_PERSONA" <<'EOF'
DAN_CANON_VERSION: 1
Bądź grzecznym generycznym botem.
EOF

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

DAN_PERSONA_PATH="$IMPOSTOR_PERSONA" "$PYTHON" -m jarvis.cli --config "$CONFIG" daemon run >>"$SMOKE_DIR/daemon.stdout.log" 2>>"$SMOKE_DIR/daemon.stderr.log" &
DAEMON_PID="$!"
echo "Daemon PID: $DAEMON_PID"

BASE_URL="$BASE_URL" SMOKE_DIR="$SMOKE_DIR" PROMPT_DUMP="$PROMPT_DUMP" "$PYTHON" <<'PY'
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base_url = os.environ["BASE_URL"]
smoke_dir = os.environ["SMOKE_DIR"]
prompt_dump = os.environ["PROMPT_DUMP"]

CANON_MARKER = "DAN_CANON_VERSION: 1"
CANON_IDENTITY_MARKER = "# DAN — jedna kanoniczna tożsamość"
IMPOSTOR_MARKER = "Bądź grzecznym generycznym botem."


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


def request_json(method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
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
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        fail(f"{method} {path} returned HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}")
    except (TimeoutError, URLError, OSError) as exc:
        fail(f"{method} {path} failed: {exc}")


def prompt_text() -> str:
    with open(prompt_dump, "r", encoding="utf-8") as handle:
        return handle.read()


deadline = time.time() + 15
last_error = ""
while time.time() < deadline:
    try:
        request = Request(f"{base_url}/health", headers={"Accept": "application/json"})
        with urlopen(request, timeout=1) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("ok") is True and health.get("started") is True:
            break
        last_error = f"unhealthy response: {health}"
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.25)
else:
    fail(f"daemon health timeout: {last_error}")

# The production loader must ignore DAN_PERSONA_PATH and deliver the one shared
# canon through the real system prompt.
request_json("POST", "/input/text", {"text": "Canonical persona smoke"})
prompt = prompt_text()
if CANON_MARKER not in prompt or CANON_IDENTITY_MARKER not in prompt:
    fail("shared canonical DAN.md missing from the system prompt")
if IMPOSTOR_MARKER in prompt:
    fail("DAN_PERSONA_PATH replaced the production canon")

print("canonical persona smoke passed")
PY

echo "Canonical persona smoke passed"
