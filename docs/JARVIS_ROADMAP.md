# Jarvis Roadmap

Classification: planning.
This document does not override code, tests, `AGENTS.md`, or project rules.

## Done

### Runtime foundation

- Repo scaffold.
- Config and runtime paths.
- SQLite schema and migrations.
- Event store and bus.
- Runtime state machine.
- Daemon API.
- Runtime supervisor endpoints.

### Brain and text pipeline

- Brain adapter interface.
- Jarvis-owned ContextBuilder.
- Conversation and turn repositories.
- Text turn pipeline.
- CLI text input.
- Conversation history.
- CLI provider adapters.
- Adapter switching through settings.

### Tools and approvals

- Tool registry.
- Approval gate.
- Approved tool execution.
- Model tool-call parsing.
- Source-sensitive permission policy.
- Read-only and approval-gated local tools.

### Panel and runtime clients

- Static cockpit.
- WebSocket streaming.
- Native menu-bar panel shell.
- Basic operator-first panel controls.
- Global PTT hotkey logic.

### Voice G0-G4

- Voice streaming contract.
- Audio device manager.
- Listening leases and PTT API.
- Voice queue and TTS broker.
- Supertonic TTS and sox playback path.
- Sox recorder path.
- MLX Whisper STT path.
- Anti-echo and barge-in cancellation.
- Streaming deltas into live speech.

### Memory OS

- Memory OS contract.
- Schema foundation.
- Candidate inbox.
- Evidence ledger.
- Candidate activation.
- `memory_save` routed through Memory OS.
- MemoryCompiler contract and implementation.
- Golden compiler scenarios.
- Preview API and hardening.
- ContextBuilder wiring, default-off.
- Runtime dependency wiring, default-off.
- Final context shape tests.
- Governance tests.
- Safe compiled-memory context diagnostics.

## Now

### Documentation package integration

`MEMORY-CONTEXT-OBSERVE-01` is implemented at `bd18d3b`. The current work is docs-only integration/review for the generated Jarvis documentation package.

Goal:

- Keep branch/head references current.
- Keep implemented, planned, deferred, and unknown states separate.
- Preserve compiled-memory default-off status.
- Avoid claiming env/config/panel/API enablement exists.

## Next

### Dev/local compiled memory enablement

Add explicit local enablement only after the docs package is reviewed and committed.

Rules:

- Default remains off.
- No production default-on.
- No panel/global switch yet.
- Smoke tests must prove safe behavior.

### Runtime smoke for compiled memory

Prove:

- daemon starts;
- default path has no compiled memory;
- explicit dev/local path includes only safe memory;
- unsafe memory remains excluded;
- fail-closed works;
- diagnostics are redacted.

### Scoped enablement

Add session/profile/allowlist enablement after dev/local path is stable.

## Later

- Usage ledger for memory selection.
- Memory audit UI.
- Topic documents and background consolidation.
- Panel controls for memory review and enablement.
- Formal Memory OS policy document.
- Provider hardening and manual smoke coverage.
- Runtime ergonomics and packaging.
- More complete docs maintenance pipeline.

## Do not do yet

- Do not enable compiled memory globally.
- Do not add user-facing compiled-memory switch before dev/local smoke.
- Do not start G5 voice clone work.
- Do not treat provider sessions as memory.
- Do not let workers commit facts directly.
- Do not move source-of-truth state into panel.
- Do not rewrite schema/migrations casually.
- Do not mix voice/panel/provider work into Memory OS safety tasks.
