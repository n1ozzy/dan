# Brain Adapters

DAN v4.1 brain adapters are stateless text generators. DAN owns
conversation history, memory, events, turns, tools, workers, and voice. A brain
adapter receives a full `BrainRequest` each turn and returns only a
`BrainResponse`.

## Defaults

- `mock` is the default adapter.
- `claude_cli` and `codex_cli` are optional.
- Provider sessions are not DAN memory.
- DAN sends full context each turn.
- Adapters do not write the database, append events, enqueue voice, run tools,
  run workers, or mutate the panel.
- Do not use `--dangerously-skip-permissions`.

> **Correction (2026-07-21): "adapters preserve no provider-side session state"
> used to be listed above and is FALSE for `claude_cli`.** `ClaudeCliAdapter`
> keeps ONE persistent provider session for the daemon's lifetime, with a durable
> checkpoint in `~/.dan/runtime/claude-session.json`, rejoined with `--resume`.
> That is provider-side session state, and it has a sharp operational edge: a
> **resumed** session keeps its ORIGINAL system prompt and its ORIGINAL tool set,
> because our prompt only rides along as `--append-system-prompt` (the bootstrap
> `--system-prompt` runs only when there is no checkpoint). So a poisoned or
> foreign checkpoint survives every restart, and the fix is to quarantine
> `claude-session.json` and restart — see `docs/ODZYSKIWANIE.md`.
>
> Also do not strip `--tools ""` or `--setting-sources ""` from the argv: they
> disable Claude's native tools and isolate the subprocess from the operator's
> CLAUDE.md and settings. Both ARE present in the live argv — they sit after a
> very large system prompt, so a truncated `ps` hides them. That is not evidence
> they are missing.

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

The adapter sends the formatted DAN request to the command on stdin and uses
stdout as the final response text after removing any valid DAN tool-call
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
<dan_tool_call>{"name":"tool_name","arguments":{...}}</dan_tool_call>
```

Accepted JSON fields are:

- `name`: required string.
- `arguments`: optional object, defaults to `{}`.
- `id`: optional string.
- `risk`: optional string, provider-supplied metadata recorded for audit only;
  it drives no decision.

Valid blocks become `BrainResponse.tool_calls` and are removed from the visible
response text. If the visible text would be empty, the adapter returns
`DAN requested tool approval.` as deterministic text (an unverified leftover
string from the approval era — check the adapter before quoting it). Adapter
metadata includes `parsed_tool_call_count`.

Malformed blocks, missing `name`, and non-object `arguments` are not fatal.
They are removed from visible text, recorded in
`raw_metadata["tool_call_parse_errors"]`, and never executed.

> **Corrected 2026-07-21.** The two paragraphs that used to sit here said tool
> requests are captured as approvals and need `POST /approvals/{id}/execute`.
> That is FALSE on this branch: `ToolRegistry.request_tool()` ignores its
> policy/source/approval arguments and executes immediately, and
> `ToolPermissionPolicy.decide()` returns ALLOW unconditionally. Model-originated
> tools **do** run and return their real result — `AGENTS.md` makes that the
> branch contract and forbids putting an approval row back in the path.

Parsed tool calls execute directly and are recorded in `tool_runs`/`events`
(redacted, with long strings capped). What can still refuse a call is the tool
itself: approved-root containment, the `shell_read` allowlist, the scrubbed
environment, git hardening, and the size/timeout bounds.

The provider prompt tells models not to claim a requested tool has already run
and not to expose hidden chain-of-thought.

## Smoke Testing

First verify the text runtime baseline still works with mock:

```bash
scripts/smoke-text-runtime.sh
```

For a provider smoke, use [PROVIDER_SMOKE.md](PROVIDER_SMOKE.md). The smoke
uses a temporary config, DB, runtime, and logs; it does not touch real
`~/.dan`. Send one text input with a long client timeout:

```bash
python -m dan.cli --config "$CONFIG" input text "Kim jesteś?" --url "$BASE_URL" --timeout 180
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
