#!/bin/bash
# tts-message-display — Claude MessageDisplay hook, DAN runtime edition.
#
# Contract (preserves the 2026-07-18 voice-marker canon):
#   * Speaks ONLY segments explicitly marked [[GŁOS]] ... [[/GŁOS]]
#     (closing tag optional, ASCII [[GLOS]] accepted). No marker = silence.
#   * FAIL-OPEN: always exits 0, hard wall-clock budget < 1 second. A dead,
#     missing or hanging daemon/CLI can never break a Claude turn and NEVER
#     triggers any legacy audio path — there is no fallback engine here.
#   * Speech goes through the one product CLI:
#       dan speak --json --as dan --session claude-hook --source hook --stdin
#   * Switches: `dan voice hook off` writes voice.hook_enabled=false into
#     ~/.dan/config.toml — this script honors it. Session override is the
#     explicit environment variable DAN_VOICE_HOOK=off. No /tmp state files
#     are read or written.
#   * Local log: ~/.dan/logs/hook-message-display.log
set -u

log() {
  [ -d "$HOME/.dan/logs" ] || mkdir -p "$HOME/.dan/logs" 2>/dev/null || return 0
  printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$HOME/.dan/logs/hook-message-display.log" 2>/dev/null
}

# Explicit session override: hard off without touching any config.
if [ "${DAN_VOICE_HOOK:-}" = "off" ]; then
  exit 0
fi

PAYLOAD="$(cat 2>/dev/null || true)"
[ -n "$PAYLOAD" ] || exit 0

PYBIN="$(command -v python3 2>/dev/null || true)"
[ -n "$PYBIN" ] || exit 0

# Extract [[GŁOS]] segments from the MessageDisplay JSON (delta, then
# message_text). Prints the joined speech text or nothing.
SPEECH="$(printf '%s' "$PAYLOAD" | "$PYBIN" -c '
import json, re, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
text = data.get("delta") or data.get("message_text") or ""
if not isinstance(text, str) or not text:
    raise SystemExit(0)
pattern = re.compile(
    r"\[\[G(?:Ł|L)OS\]\](.*?)(?:\[\[/G(?:Ł|L)OS\]\]|$)",
    re.DOTALL | re.IGNORECASE,
)
segments = [s.strip() for s in pattern.findall(text) if s.strip()]
if segments:
    sys.stdout.write("\n".join(segments))
' 2>/dev/null || true)"
[ -n "$SPEECH" ] || exit 0

# Installation switch: dan voice hook on|off -> voice.hook_enabled.
CONFIG="$HOME/.dan/config.toml"
if [ -f "$CONFIG" ]; then
  if grep -Eq '^[[:space:]]*hook_enabled[[:space:]]*=[[:space:]]*false' "$CONFIG" 2>/dev/null; then
    log "skip: voice.hook_enabled=false"
    exit 0
  fi
fi

# Resolve the product CLI; nothing found = silent fail-open.
DAN_BIN="$HOME/.dan/bin/dan"
if [ ! -x "$DAN_BIN" ]; then
  DAN_BIN="$(command -v dan 2>/dev/null || true)"
fi
if [ -z "$DAN_BIN" ]; then
  log "skip: no dan CLI"
  exit 0
fi

SESSION="${DAN_VOICE_HOOK_SESSION:-claude-hook}"
[ -d "$HOME/.dan/logs" ] || mkdir -p "$HOME/.dan/logs" 2>/dev/null || exit 0

# Hard sub-second budget: run the CLI in the background and kill it if it
# does not finish in time. The hook result never depends on the CLI result.
printf '%s' "$SPEECH" | "$DAN_BIN" speak --json --as dan \
  --session "$SESSION" --source hook --stdin \
  >> "$HOME/.dan/logs/hook-message-display.log" 2>&1 &
CLI_PID=$!

WAITED=0
while kill -0 "$CLI_PID" 2>/dev/null; do
  if [ "$WAITED" -ge 12 ]; then
    kill -9 "$CLI_PID" 2>/dev/null
    log "timeout: dan speak killed after budget (fail-open)"
    break
  fi
  sleep 0.05
  WAITED=$((WAITED + 1))
done
wait "$CLI_PID" 2>/dev/null

exit 0
