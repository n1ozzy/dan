# Text Runtime Smoke

This runbook covers the manual text-only smoke for DAN v4.1:

```bash
scripts/smoke-text-runtime.sh
```

To keep the generated files for inspection:

```bash
SMOKE_KEEP_ARTIFACTS=1 scripts/smoke-text-runtime.sh
```

## What It Proves

- A temporary `dand` can start from a temporary config.
- The CLI can send text through `POST /input/text`.
- The mock brain returns a `DAN mock response`.
- CLI conversation history shows the created conversation.
- CLI turn history shows the created turn.
- Events are visible through `events after`.
- The smoke uses a temporary DB and temporary runtime under its smoke directory.
- The smoke does not touch real ~/.dan.
- The smoke stops only the daemon child process it started.

## What It Does Not Test

- This smoke does not use launchd.
- This smoke does not use voice.
- This smoke does not use tools.
- This smoke does not use workers.
- This smoke does not use real providers.
- It does not start the panel, audio runtime, TTS/STT, Claude, Codex, Groq, OpenAI, Ollama, or any provider subprocess.
- It does not test WebSocket, SSE, launch installation, migrations, or schema changes.

## Kept Artifacts

With `SMOKE_KEEP_ARTIFACTS=1`, the script prints the smoke directory and leaves it in place. Inspect:

- `dan-smoke.toml` for the temporary config.
- `home/dan.db` for the temporary SQLite database.
- `logs/` and `runtime/` for temporary runtime paths.
- `input.json`, `conversations.json`, `turns.json`, and `events.json` for CLI responses.
- `dand.log` for daemon output.

Without `SMOKE_KEEP_ARTIFACTS=1`, the script removes only its own temporary smoke directory.

## Common Failures

- Port already in use: the fixed smoke port `127.0.0.1:41749` is occupied.
- `.venv` missing: the script falls back to `python3`; create the venv if dependencies are not available there.
- Daemon health timeout: inspect `dand.log` in a kept smoke directory.
- Permission denied on script: run `chmod +x scripts/smoke-text-runtime.sh`.

Reminder: this is a text-only mock brain smoke.
