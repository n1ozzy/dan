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
stdout as the final response text after removing any valid Jarvis tool-call
blocks.

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

## Explicit Tool Request Blocks

Claude CLI and Codex CLI adapters parse explicit tool requests from stdout
using this block syntax:

```text
<jarvis_tool_call>{"name":"tool_name","arguments":{...}}</jarvis_tool_call>
```

Accepted JSON fields are:

- `name`: required string.
- `arguments`: optional object, defaults to `{}`.
- `id`: optional string.
- `risk`: optional string, defaults to `safe_read`; the registry still owns
  the effective approval risk during capture.

Valid blocks become `BrainResponse.tool_calls` and are removed from the visible
response text. If the visible text would be empty, the adapter returns
`Jarvis requested tool approval.` as deterministic text. Adapter metadata
includes `parsed_tool_call_count`.

Malformed blocks, missing `name`, and non-object `arguments` are not fatal.
They are removed from visible text, recorded in
`raw_metadata["tool_call_parse_errors"]`, and never executed.

Tool requests are not executed automatically. The text turn pipeline records
model-originated requests as approvals when possible. A human or explicit
client must approve and then call `POST /approvals/{id}/execute`; approval
alone does not run the tool.

The provider prompt tells models not to claim a requested tool has already run,
not to request dangerous shell/file/network/system mutation, and not to expose
hidden chain-of-thought. This parser is only an approval-capture path, not
autonomous tool use.

## Smoke Testing

First verify the text runtime baseline still works with mock:

```bash
scripts/smoke-text-runtime.sh
```

For a provider smoke, use [PROVIDER_SMOKE.md](PROVIDER_SMOKE.md). The smoke
uses a temporary config, DB, runtime, and logs; it does not touch real
`~/.jarvis`. Send one text input with a long client timeout:

```bash
python -m jarvis.cli --config "$CONFIG" input text "Kim jesteś?" --url "$BASE_URL" --timeout 180
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
- HTTP client timeout while the provider CLI is still running: retry the client
  call with `--timeout 180` and inspect the daemon log before assuming the
  provider failed.

This runbook covers text-only brain adapters. It does not enable tools, workers,
voice, launchd, WebSocket, SSE, or provider SDK network adapters.
