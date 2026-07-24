 
set -u

log() {
  [ -d "$HOME/.dan/logs" ] || mkdir -p "$HOME/.dan/logs" 2>/dev/null || return 0
  printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$HOME/.dan/logs/hook-message-display.log" 2>/dev/null
}
 
if [ "${DAN_VOICE_HOOK:-}" = "off" ]; then
  exit 0
fi

PAYLOAD="$(cat 2>/dev/null || true)"
[ -n "$PAYLOAD" ] || exit 0

PYBIN="$(command -v python3 2>/dev/null || true)"
[ -n "$PYBIN" ] || exit 0
 
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
 
CONFIG="$HOME/.dan/config.toml"
if [ -f "$CONFIG" ]; then
  if grep -Eq '^[[:space:]]*hook_enabled[[:space:]]*=[[:space:]]*false' "$CONFIG" 2>/dev/null; then
    log "skip: voice.hook_enabled=false"
    exit 0
  fi
fi
 
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
