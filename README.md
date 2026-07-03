# Jarvis v4.2 Runtime

Jarvis is `jarvisd`: a local, single-user runtime where the daemon owns the
truth.

- `jarvisd` owns conversation, events, memory, approvals, worker jobs, voice
  queue and runtime state.
- The panel is a client. It renders daemon state and sends intents only.
- Brain adapters are stateless. Provider sessions are not Jarvis memory.
- Workers are silent. They can produce candidates, not committed facts or speech.
- The broker is the sole speaker.
- `/tmp` is not a source of truth.
- Legacy DAN is reference-only. Concepts may be re-expressed; old runtime code
  is not copied into this package.

Current post-A-H state:

- `scripts/jarvisd` / `.venv/bin/jarvisd` start the daemon.
- `scripts/jarvis-panel` starts the native menu-bar panel thin client.
- Text input, conversation history, memory, events, approvals, tool execution,
  WebSocket streaming, provider CLI adapters, launchd assets, voice queue,
  PTT/listening leases, recorder/STT/TTS plumbing and mock/fake smoke harnesses
  are live.
- Voice clone (G5), real Claude/Codex background workers, OpenAI adapter,
  memory summarization and the WebView bridge remain deferred/backlog items.

## Development

Install the package editable into the repo venv so the `jarvisd` entry
point exists (`.venv/bin/jarvisd`) and `python -m jarvis.cli` works from
any cwd:

```sh
.venv/bin/pip install -e .
```

Run the daemon from the repo:

```sh
scripts/jarvisd
```

Or through the installed console script:

```sh
.venv/bin/jarvisd
```

Useful local checks:

```sh
.venv/bin/python -m jarvis.cli config show
.venv/bin/python -m jarvis.cli health
scripts/jarvis-panel
```

- Reviewer orientation: `docs/REVIEW_HANDOFF.md`
- Brain adapter setup: `docs/runbooks/BRAIN_ADAPTERS.md`
- Manual text runtime smoke: `docs/runbooks/TEXT_RUNTIME_SMOKE.md`
- Manual provider brain smoke: `docs/runbooks/PROVIDER_SMOKE.md`
- Tool registry, approval gate and manual tools smoke:
  `docs/runbooks/TOOLS_AND_APPROVALS.md`
- macOS operator contract: `docs/MACOS_OPERATOR_CONTRACT.md`
- Memory API and CLI: `docs/runbooks/MEMORY_API.md`
- Manual memory runtime smoke: `docs/runbooks/MEMORY_API.md#manual-smoke`
- Static development cockpit: `docs/runbooks/PANEL_COCKPIT.md`
