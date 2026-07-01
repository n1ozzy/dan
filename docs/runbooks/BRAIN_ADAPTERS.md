# Brain Adapters

Jarvis v4.1 brain adapters are stateless text generators. Jarvis owns
conversation history, memory, events, turns, tools, workers, and voice. A brain
adapter receives a full `BrainRequest` each turn and returns only a
`BrainResponse`.

## Defaults

- `mock` is the default adapter.
- `claude_cli` and `codex_cli` are optional.
- Provider sessions are not Jarvis memory.
- Jarvis sends full context each turn.
- Adapters do not write the database, append events, enqueue voice, run tools,
  run workers, mutate the panel, or preserve provider-side session state.
- Do not use `--dangerously-skip-permissions`.

## Enable Claude CLI

In a local config file:

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
timeout_seconds = 120
```

The adapter sends the formatted Jarvis request to the command on stdin and uses
stdout as the final response text.

## Enable Codex CLI

In a local config file:

```toml
[brain]
default_adapter = "codex_cli"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[brain.codex_cli]
enabled = true
command = "codex"
args = []
model = "codex-cli"
timeout_seconds = 120
```

Keep CLI args minimal. Do not add file-write, shell-execution, unrestricted
tool-use, repo-editing, or permission-bypass flags.

## Smoke Testing

First verify the text runtime baseline still works with mock:

```bash
scripts/smoke-text-runtime.sh
```

For a provider smoke, use a temporary config modeled on the smoke runbook, set
`runtime.home`, `runtime.logs_dir`, `runtime.runtime_dir`, `runtime.pid_file`,
and `database.path` to temporary paths, then enable exactly one CLI adapter in
that temp config. Send one text input with:

```bash
python -m jarvis.cli --config "$CONFIG" input text "Kim jesteś?" --url "$BASE_URL"
```

Stop only the daemon process you started for that smoke.

## Troubleshooting

- Executable missing: install the CLI or set `[brain.<adapter>].command` to the
  executable path. The adapter checks this only at generation time.
- Timeout: increase `[brain.<adapter>].timeout_seconds` or verify the provider
  CLI does not wait for interactive input.
- Empty output: the CLI returned success but no stdout; check CLI args and
  provider authentication.
- Non-zero exit: stderr is redacted before it is included in `BrainAdapterError`.

This runbook covers text-only brain adapters. It does not enable tools, workers,
voice, launchd, WebSocket, SSE, or provider SDK network adapters.
