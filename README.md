# Jarvis v4.1 Runtime

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

Prompt 01 creates scaffold and contracts only. It does not start the daemon,
panel, broker, listener, workers, TTS, STT or provider integrations.

## Development

Install the package editable into the repo venv so the `jarvisd` entry
point exists (`.venv/bin/jarvisd`) and `python -m jarvis.cli` works from
any cwd:

```sh
.venv/bin/pip install -e .
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
