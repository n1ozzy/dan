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
PORT="41783"
BASE_URL="http://$HOST:$PORT"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dan-worker-jobs-smoke.XXXXXX")"
CONFIG="$SMOKE_DIR/dan-smoke.toml"
DB_PATH="$SMOKE_DIR/dan-smoke.db"
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


def request_json_status(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    with_token: bool = True,
    timeout: float = 30,
) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    token = api_token()
    if with_token and token and method in {"POST", "PATCH", "DELETE"}:
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


def request_json(method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    status, body = request_json_status(method, path, payload, timeout=timeout)
    if status >= 400:
        fail(f"{method} {path} returned HTTP {status}: {body}")
    return body


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

secret = "sk-test-1234567890abcdef1234567890abcdef"

# 1. Job creation is a mutation: tokenless request rejected before routing.
status, body = request_json_status(
    "POST",
    "/workers/jobs",
    {"worker_kind": "mock", "prompt": "x", "requested_by": "smoke"},
    with_token=False,
)
if status != 401:
    fail(f"tokenless job create did not return 401: status={status} body={body}")

# 2. Unknown worker kind fails closed.
status, body = request_json_status(
    "POST",
    "/workers/jobs",
    {"worker_kind": "bogus", "prompt": "x", "requested_by": "smoke"},
)
if status != 404:
    fail(f"unknown worker kind did not return 404: status={status} body={body}")

# 3. Real mock job; the prompt carries a fake secret to prove redaction at rest.
create_payload = request_json(
    "POST",
    "/workers/jobs",
    {
        "worker_kind": "mock",
        "prompt": f"Summarize the notes protected by {secret} for the smoke run",
        "requested_by": "smoke",
    },
)
job = create_payload.get("job") or {}
job_id = job.get("id")
if not isinstance(job_id, str) or not job_id:
    fail(f"job create returned no id: {create_payload}")

deadline = time.time() + 15
final_job = None
while time.time() < deadline:
    detail = request_json("GET", f"/workers/jobs/{job_id}")
    final_job = detail.get("job") or {}
    if final_job.get("status") in {"succeeded", "failed", "cancelled"}:
        break
    time.sleep(0.2)
if final_job is None or final_job.get("status") != "succeeded":
    fail(f"worker job did not succeed: {final_job}")
if not final_job.get("result_summary"):
    fail(f"succeeded job has no result_summary: {final_job}")
candidate_id = (final_job.get("metadata") or {}).get("memory_candidate_id")
if not isinstance(candidate_id, str) or not candidate_id:
    fail(f"succeeded job has no memory_candidate_id: {final_job}")

listing = request_json("GET", "/workers/jobs?status=succeeded")
if [item.get("id") for item in listing.get("jobs", [])] != [job_id]:
    fail(f"succeeded filter mismatch: {listing}")

# 4. The candidate is INACTIVE memory: worker output never enters context
#    without a human decision (ADR-009).
memory_payload = request_json("GET", "/memory")
blocks = memory_payload.get("memory", memory_payload.get("blocks", []))
candidates = [block for block in blocks if block.get("id") == candidate_id]
if len(candidates) != 1:
    fail(f"memory candidate not listed: {memory_payload}")
candidate = candidates[0]
if candidate.get("active") is not False:
    fail(f"memory candidate is not inactive: {candidate}")
if candidate.get("metadata", {}).get("candidate") is not True:
    fail(f"memory candidate flag missing: {candidate}")

# 5. Human promotes the candidate through the existing memory PATCH.
promoted_payload = request_json(
    "PATCH", f"/memory/{candidate_id}", {"active": True}
)
promoted = promoted_payload.get("memory", promoted_payload.get("block", {}))
if promoted.get("active") is not True:
    fail(f"promotion did not activate the block: {promoted_payload}")
if promoted.get("metadata", {}).get("candidate") is not False:
    fail(f"promotion did not clear the candidate flag: {promoted_payload}")

# 6. Durable truth in the daemon DB: statuses, events, redaction, silence.
with sqlite3.connect(db_path) as conn:
    job_row = conn.execute(
        "SELECT status, prompt, result_summary FROM worker_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    event_types = [
        str(row[0])
        for row in conn.execute("SELECT type FROM events ORDER BY id").fetchall()
    ]
    event_dump = conn.execute("SELECT GROUP_CONCAT(payload_json) FROM events").fetchone()[0]
    memory_dump = conn.execute(
        "SELECT GROUP_CONCAT(title || ' ' || body) FROM memory_blocks"
    ).fetchone()[0]
    voice_count = conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]

if job_row is None or job_row[0] != "succeeded":
    fail(f"worker_jobs row is not succeeded: {job_row}")
if secret in (job_row[1] or "") or secret in (job_row[2] or ""):
    fail("raw secret persisted in worker_jobs prompt/result_summary")
if secret in (event_dump or ""):
    fail("raw secret leaked into events")
if secret in (memory_dump or ""):
    fail("raw secret leaked into memory_blocks")

required_events = {
    "worker.job.created",
    "worker.job.started",
    "worker.job.finished",
    "memory.candidate.created",
    "memory.candidate.promoted",
}
missing = sorted(required_events - set(event_types))
if missing:
    fail(f"events missing {missing}: {event_types}")

# The worker stayed mute and never became a turn participant.
if voice_count != 0:
    fail(f"voice_queue touched by worker: {voice_count}")
if turn_count != 0:
    fail(f"turns touched by worker: {turn_count}")

print("Worker jobs smoke passed")
print(f"job id: {job_id}")
print(f"candidate id: {candidate_id}")
print(f"result_summary: {job_row[2]}")
print(f"event count: {len(event_types)}")
print("voice_queue: 0")
print("turns: 0")
PY
