#!/usr/bin/env bash
set -euo pipefail

# G4a recorder smoke: leases drive a REAL SoxRecorder end to end, but the
# sox binary is a fake script — the smoke never opens a microphone and never
# records anything (same rule as every test). Proves: PTT down spawns sox
# with the policy-selected device and the §4a effect chain; PTT up stops it
# and leaves no WAV behind; the daemon dies at startup on a missing binary.

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
PORT="41791"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-voice-recorder-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/jarvis-smoke.toml"
DB_PATH="$SMOKE_DIR/jarvis-smoke.db"
FAKE_SOX="$SMOKE_DIR/fake-sox"
ARGV_FILE="$SMOKE_DIR/sox-argv.txt"
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

# Fake sox: fills the WAV target, dumps argv, then waits for SIGINT/SIGTERM.
# WAV before argv on purpose — once the argv line exists, the capture bytes
# are complete and stop() cannot race the fake mid-write.
cat >"$FAKE_SOX" <<EOF
#!/bin/bash
out=""
for arg in "\$@"; do
  case "\$arg" in *.wav) out="\$arg";; esac
done
if [ -n "\$out" ]; then head -c 8000 /dev/zero > "\$out"; fi
printf '%s\t' "\$@" >> $ARGV_FILE
printf '\n' >> $ARGV_FILE
trap 'exit 0' INT TERM
sleep 60 &
wait \$!
EOF
chmod 700 "$FAKE_SOX"

write_config() {
  local recorder_binary="$1"
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
enabled = true
speak_responses = false
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true
recorder = "sox"
recorder_binary = "$recorder_binary"
ptt_hold_ttl_seconds = 30
listen_lock_ttl_seconds = 600

[audio]
enabled = true
backend = "fake"
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = true
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
}

echo "Smoke directory: $SMOKE_DIR"

# 0. Fail-at-startup: a missing sox binary must kill the daemon loudly.
write_config "$SMOKE_DIR/no-such-sox"
if "$PYTHON" -m jarvis.cli --config "$CONFIG" daemon run >"$SMOKE_DIR/startup-fail.log" 2>&1; then
  echo "ERROR: daemon started despite a missing sox binary" >&2
  exit 1
fi
if ! grep -q "binary" "$SMOKE_DIR/startup-fail.log"; then
  echo "ERROR: startup failure did not mention the missing binary" >&2
  cat "$SMOKE_DIR/startup-fail.log" >&2
  exit 1
fi
echo "missing sox binary kills startup PASS"

write_config "$FAKE_SOX"
"$PYTHON" -m jarvis.cli --config "$CONFIG" daemon run >"$SMOKE_DIR/daemon.stdout.log" 2>"$SMOKE_DIR/daemon.stderr.log" &
DAEMON_PID="$!"
echo "Daemon PID: $DAEMON_PID"

BASE_URL="$BASE_URL" DB_PATH="$DB_PATH" SMOKE_DIR="$SMOKE_DIR" ARGV_FILE="$ARGV_FILE" "$PYTHON" <<'PY'
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
argv_file = os.environ["ARGV_FILE"]
voice_workdir = os.path.normpath(os.path.join(smoke_dir, "runtime", "voice"))


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


def request_json_status(method, path, payload=None, *, with_token=True, timeout=10):
    headers = {"Accept": "application/json"}
    if with_token and method in {"POST", "PATCH", "DELETE"}:
        headers["X-Jarvis-Token"] = api_token()
    data = None
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


def spawn_lines():
    if not os.path.exists(argv_file):
        return []
    with open(argv_file, "r", encoding="utf-8") as handle:
        return [line.rstrip("\t").split("\t") for line in handle.read().splitlines() if line.strip()]


def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def rec_wavs():
    if not os.path.isdir(voice_workdir):
        return []
    return [name for name in os.listdir(voice_workdir) if name.startswith("rec-") and name.endswith(".wav")]


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

# 1. Mutations require the transport token; no recorder may start.
status, _ = request_json_status("POST", "/voice/ptt/down", {}, with_token=False)
if status != 401:
    fail(f"tokenless ptt down did not return 401: {status}")
if spawn_lines():
    fail("tokenless request spawned the recorder")
print("tokenless ptt -> 401, no recorder spawn PASS")

# 2. PTT down spawns exactly one sox with the policy device and §4a chain.
status, down = request_json_status("POST", "/voice/ptt/down", {})
if status != 200 or down.get("lease", {}).get("mode") != "hold":
    fail(f"ptt down failed: {status} {down}")
if not wait_for(lambda: len(spawn_lines()) == 1):
    fail(f"sox recorder did not spawn: {spawn_lines()}")
args = spawn_lines()[0]
try:
    if args[args.index("-t") + 1] != "coreaudio":
        fail(f"recorder input is not coreaudio: {args}")
    # ADR-012: the fake audio fixture only offers the bluetooth mic; policy
    # (with allow_bluetooth_microphone=true) selects it and the recorder
    # must record from exactly that device.
    if args[args.index("-t") + 2] != "Fake BT Headset":
        fail(f"recorder ignored the policy device: {args}")
    if args[args.index("-r") + 1] != "16000" or args[args.index("-c") + 1] != "1":
        fail(f"recorder output shape is not 16 kHz mono: {args}")
    if args[args.index("highpass") + 1] != "80":
        fail(f"missing §4a highpass 80: {args}")
except ValueError as exc:
    fail(f"expected flag missing from sox argv {args}: {exc}")
wav = next((a for a in args if a.endswith(".wav")), "")
if os.path.normpath(os.path.dirname(wav)) != voice_workdir:
    fail(f"capture WAV is outside the runtime voice workdir: {wav}")
print("ptt down spawns sox with policy device + §4a chain PASS")

# 3. PTT up stops the recorder and leaves no WAV behind (RAM handoff only).
status, up = request_json_status("POST", "/voice/ptt/up", {})
if status != 200 or up.get("released") != 1:
    fail(f"ptt up failed: {status} {up}")
if not wait_for(lambda: not rec_wavs()):
    fail(f"capture WAV survived ptt up: {rec_wavs()}")
status, listening = request_json_status("GET", "/voice/listening")
if listening.get("listening") is not False:
    fail(f"still listening after ptt up: {listening}")
if len(spawn_lines()) != 1:
    fail(f"unexpected extra recorder spawns: {spawn_lines()}")
print("ptt up stops sox and cleans the workdir PASS")

# 4. Second cycle works (the recorder restarts cleanly) and nothing about
#    captures ever lands in the DB (audio is transport, not truth).
request_json_status("POST", "/voice/ptt/down", {})
if not wait_for(lambda: len(spawn_lines()) == 2):
    fail(f"second ptt down did not respawn sox: {spawn_lines()}")
request_json_status("POST", "/voice/ptt/up", {})
if not wait_for(lambda: not rec_wavs()):
    fail(f"capture WAV survived second cycle: {rec_wavs()}")
with sqlite3.connect(db_path) as conn:
    (queue_count,) = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()
    wav_rows = conn.execute(
        "SELECT COUNT(*) FROM events WHERE payload_json LIKE '%.wav%'"
    ).fetchone()
if queue_count != 0:
    fail(f"voice_queue is not empty: {queue_count}")
if wav_rows[0] != 0:
    fail(f"a capture path leaked into events: {wav_rows[0]}")
print("second cycle + no capture traces in DB PASS")

print("voice recorder smoke passed")
PY

echo "Voice recorder smoke passed"
