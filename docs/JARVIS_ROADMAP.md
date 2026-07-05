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
- Config-based dev/local compiled memory enablement.
- Request-scoped compiled memory override.
- Formal compiled memory context policy contract.

## Now

### Compiled memory policy contract

`MEMORY-CONTEXT-POLICY-01` formalizes the compiled memory context policy after config-based dev/local enablement and request-scoped override support.

Goal:

- Preserve compiled-memory default-off status.
- Keep implemented, planned, deferred, and unknown states separate.
- Document prompt-visible output, governance exclusions, diagnostics redaction, and fail-closed/read-only behavior.
- Make clear that env/panel/API/user-facing enablement remains future.

## Next

### Additional compiled memory smoke

Extend runtime smoke coverage only when a scoped task needs broader proof around existing config-based local enablement or request-scoped override behavior.

Rules:

- Default remains off.
- No production default-on.
- No env/global switch yet.
- No panel/API/user-facing switch yet.
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

Add broader session/profile/allowlist enablement after the request-scoped internal path is stable.

## Later

- Usage ledger for memory selection.
- Memory audit UI.
- Topic documents and background consolidation.
- Panel controls for memory review and enablement.
- Env/panel/API/user-facing enablement remains future.
- Provider hardening and manual smoke coverage.
- Runtime ergonomics and packaging.
- More complete docs maintenance pipeline.

## Do not do yet

- Do not enable compiled memory globally.
- Do not add env, panel, API, or user-facing compiled-memory enablement casually.
- Do not start G5 voice clone work.
- Do not treat provider sessions as memory.
- Do not let workers commit facts directly.
- Do not move source-of-truth state into panel.
- Do not rewrite schema/migrations casually.
- Do not mix voice/panel/provider work into Memory OS safety tasks.
