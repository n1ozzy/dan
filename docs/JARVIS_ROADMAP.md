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
- Authoritative docs status refresh after readiness audit.
- Session/profile scoped compiled memory enablement, internal-only.
- Compiled memory force-disable / kill switch.
- Compiled memory rollout precedence matrix tests.

## Now

### Final Memory OS handoff

`MEMORY-OS-FINAL-HANDOFF-01` records the completed compiled-memory runtime rollout safety workstream in authoritative docs. Runtime/config/ContextBuilder/test work is complete through session/profile scoped enablement, compiled-memory force-disable, and rollout precedence matrix coverage.

Goal:

- Preserve compiled-memory default-off status.
- Keep completed internal safety wiring separate from future rollout features.
- Document prompt-visible output, governance exclusions, diagnostics redaction, fail-closed/read-only behavior, and kill-switch precedence.
- Make clear that env, public API, panel, user-facing, and global production enablement remain future.

## Next

### Future compiled memory rollout tasks

Future compiled-memory rollout work must be split into separate scoped tasks:

- optional env enablement;
- optional internal API enablement;
- optional panel toggle;
- production rollout plan;
- observability dashboard, if needed.

Rules:

- Default remains off.
- `[memory].enabled=false` and `compiled_memory_force_disabled` remain absolute disables.
- No production default-on.
- No env/global switch without explicit scope.
- No public API, panel, or user-facing switch without explicit scope.
- Smoke tests must prove safe behavior.

## Later

- Usage ledger for memory selection.
- Memory audit UI.
- Topic documents and background consolidation.
- Panel controls for memory review and enablement.
- Env/public API/panel/user-facing enablement remains future.
- Provider hardening and manual smoke coverage.
- Runtime ergonomics and packaging.
- More complete docs maintenance pipeline.

## Do not do yet

- Do not enable compiled memory globally.
- Do not add env, panel, public API, or user-facing compiled-memory enablement casually.
- Do not bypass compiled-memory governance exclusions.
- Do not expose raw evidence, IDs, secrets, diagnostics internals, skipped items, or compiler internals to the model.
- Do not start G5 voice clone work.
- Do not treat provider sessions as memory.
- Do not let workers commit facts directly.
- Do not move source-of-truth state into panel.
- Do not rewrite schema/migrations casually.
- Do not mix voice/panel/provider work into Memory OS safety tasks.
