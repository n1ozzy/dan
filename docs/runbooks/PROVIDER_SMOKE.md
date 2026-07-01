# Provider Brain Smoke

This runbook covers a manual, text-only Claude CLI brain smoke for Jarvis v4.1.
The local Claude CLI provider smoke passed manually on the local machine: a
`POST /input/text` turn returned `final_text` successfully through
`claude_cli`.

## Scope

- Use a temporary config, temporary DB, temporary runtime, and temporary logs.
- Do not touch real `~/.jarvis`.
- Start only one temporary `jarvisd` child process for the smoke.
- Stop only that child process.
- Do not use launchd.
- Do not use voice, audio, tools, workers, panel, WebSocket, or SSE.
- Do not use provider permission-bypass flags such as `--dangerously-skip-permissions`.

## Claude CLI Config

Use this brain section in the temporary config:

```toml
[brain]
default_adapter = "claude_cli"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[brain.claude_cli]
enabled = true
command = "claude"
args = ["-p"]
model = "claude-cli"
timeout_seconds = 180
```

Recommended input CLI timeout:

```bash
python -m jarvis.cli --config "$CONFIG" input text "Kim jesteś?" --url "$BASE_URL" --timeout 180
```

## Expected Events

For a successful turn, inspect `python -m jarvis.cli --config "$CONFIG" events after --id 0 --url "$BASE_URL"` and confirm:

- `brain.requested`
- `brain.responded`
- `turn.finished`
- `state.changed` with `IDLE -> THINKING`
- `state.changed` with `THINKING -> IDLE`

The normal successful turn should report matching model metadata:

- `brain.requested` payload `model = "claude-cli"`
- `brain.responded` payload `model = "claude-cli"`
- `turn.finished` payload `brain_model = "claude-cli"`

## Scripted Smoke

If `claude` is installed and authenticated, run:

```bash
scripts/smoke-claude-cli-brain.sh
```

Keep artifacts for inspection:

```bash
SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-claude-cli-brain.sh
```

The script uses only `claude -p`, temporary Jarvis paths, and a single child
`jarvisd` process.

## Common Failures

- HTTP client timeout while the provider CLI is still running; use `--timeout 180`.
- Missing Claude auth; run the provider login flow outside Jarvis.
- Missing executable; `command -v claude` must resolve.
- Provider output empty; check CLI auth, prompt handling, and stdout behavior.
- Non-zero provider exit; inspect redacted stderr in the daemon log.
