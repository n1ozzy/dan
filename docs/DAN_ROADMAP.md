# DAN Roadmap

Classification: planning.
This document does not override code, tests, `AGENTS.md`, or project rules.
The "Done" list records what was built, not how it behaves today: Release 1
(2026-07-18) removed the approval gate from the tool path. For the running
behaviour read `docs/STATUS.md` and `docs/SECURITY_MODEL.md`.

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
- DAN-owned ContextBuilder.
- Conversation and turn repositories.
- Text turn pipeline.
- CLI text input.
- Conversation history.
- CLI provider adapters.
- Adapter switching through settings.

### Tools

- Tool registry.
- Model tool-call parsing.
- Local tools (file, shell_read, UI, screen, terminal, memory), each enforcing
  its own guards: approved roots, the `shell_read` allowlist, a scrubbed
  environment, git hardening, runtime/output bounds.
- Built and then RETIRED from the execution path in Release 1: the approval
  gate, approved-tool execution and the source-sensitive permission policy.
  `ApprovalGate` and `ToolPermissionPolicy` still exist as classes but neither
  blocks nor gates a tool run.

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
- Supertonic TTS. Playback is `CoreAudioPlayer` (native), not the original sox
  `play` path.
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

### Release 1 observation window

Release 1 cut over on 2026-07-18 (`docs/STATUS.md`). The current work is running
the production daemon on `agent/dan-release1-integration` and keeping the old
stack parked until the operator signs off on donor deletion.

The Memory OS workstream (`MEMORY-OS-FINAL-HANDOFF-01`) closed before that
cutover. Compiled memory shipped default-off; Ozzy's live `~/.dan/config.toml`
enables it.

## Next

### Future compiled memory rollout tasks

Future compiled-memory rollout work must be split into separate scoped tasks:

- optional internal API enablement;
- optional panel toggle;
- production rollout plan;
- observability dashboard, if needed.

Env enablement is no longer future work: `DAN_COMPILED_MEMORY_ENABLED` and
`DAN_COMPILED_MEMORY_FORCE_DISABLED` are already read at daemon construction.

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
- Env/public API/panel/user-facing enablement remains future — as this roadmap
  was written. **Correction 2026-07-21:** env enablement shipped
  (`DAN_COMPILED_MEMORY_ENABLED`, `DAN_COMPILED_MEMORY_FORCE_DISABLED`); only
  public API, panel and user-facing enablement are still future.
- Provider hardening and manual smoke coverage.
- Runtime ergonomics and packaging.
- More complete docs maintenance pipeline.

## Do not do yet

- Do not enable compiled memory globally.
- Do not add env, panel, public API, or user-facing compiled-memory enablement casually.
  The two operator env variables that already exist
  (`DAN_COMPILED_MEMORY_ENABLED`, `DAN_COMPILED_MEMORY_FORCE_DISABLED`) are the
  whole of the env surface; widening it needs a scoped task like any other.
- Do not bypass compiled-memory governance exclusions.
- Do not expose raw evidence, IDs, secrets, diagnostics internals, skipped items, or compiler internals to the model.
- Do not start G5 voice clone work.
- Do not treat provider sessions as memory.
- Do not let workers commit facts directly.
- Do not move source-of-truth state into panel.
- Do not rewrite schema/migrations casually.
- Do not mix voice/panel/provider work into Memory OS safety tasks.
