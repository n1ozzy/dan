# Jarvis Architecture

Classification: technical architecture.
Scope: whole Jarvis runtime as represented by branch `rescue/audt-gpt5.5pro-limit-cdn` at HEAD `58cca12 docs: finalize Memory OS rollout handoff`.

## System overview

Jarvis is a local, single-user assistant runtime. The central process is `jarvisd`. It owns durable state and exposes APIs for clients, tools, voice, memory, settings, approvals, and event streams.

The system is intentionally daemon-centered. Clients render state and send intents. They do not own canonical data. Brain adapters are stateless request/response adapters and cannot be treated as memory.

## Runtime ownership model

Core laws:

- `jarvisd` owns truth.
- The panel is a client.
- Brain adapters are stateless.
- Provider sessions are not Jarvis memory.
- Workers are silent candidate producers.
- The voice broker is the only speaker.
- `/tmp` is not a source of truth.
- Legacy DAN is reference-only.

## Main module map

| Area | Paths |
|---|---|
| Daemon | `jarvis/daemon/` |
| API | `jarvis/api/` |
| Brain | `jarvis/brain/` |
| Turns | `jarvis/turns/` |
| Memory | `jarvis/memory/` |
| Storage | `jarvis/store/` |
| Events | `jarvis/events/` |
| Tools | `jarvis/tools/` |
| Voice | `jarvis/voice/` |
| Audio | `jarvis/audio/` |
| Panel | `jarvis/panel/` |
| macOS | `jarvis/macos/` |
| Workers | `jarvis/workers/` |
| Runtime | `jarvis/runtime/` |
| Security | `jarvis/security/` |
| Config | `jarvis/config.py`, `config/` |

## Daemon lifecycle

`jarvis/daemon/app.py` wires the application object and owns runtime dependencies. `jarvis/daemon/lifecycle.py` exposes HTTP lifecycle and routing helpers.

The daemon coordinates:

- DB connection and repositories.
- Event store and event bus.
- Brain manager and adapters.
- ContextBuilder.
- Turn orchestrator.
- Tool registry and approval gate.
- Memory managers and repositories.
- Worker broker.
- Voice queue, broker, listening leases, recorder/STT/TTS plumbing.
- Settings and runtime state.

## API layer

The API layer is split by concern:

- `routes_health.py`
- `routes_state.py`
- `routes_runtime.py`
- `routes_settings.py`
- `routes_history.py`
- `routes_input.py`
- `routes_brain.py`
- `routes_tools.py`
- `routes_approvals.py`
- `routes_workers.py`
- `routes_memory.py`
- `routes_audio.py`
- `routes_voice.py`
- `websocket.py`

`lifecycle.py` handles request dispatch, local-host/CORS/token checks, request body limits, and WebSocket stream session limits.

## Brain layer

The brain layer is stateless by design.

Primary files:

- `jarvis/brain/base.py`
- `jarvis/brain/manager.py`
- `jarvis/brain/context_builder.py`
- `jarvis/brain/mock_adapter.py`
- `jarvis/brain/claude_cli_adapter.py`
- `jarvis/brain/claude_cli_warm_adapter.py`
- `jarvis/brain/codex_cli_adapter.py`
- `jarvis/brain/openai_adapter.py`
- `jarvis/brain/tool_call_parser.py`

`BrainRequest` contains:

- `turn_id`
- `conversation_id`
- `input_text`
- `context_messages`
- `memory_blocks`
- `available_tools`
- `settings`
- `metadata`

Provider adapters receive a fully assembled Jarvis-owned request. They must not silently supply memory by preserving hidden provider session state.

## Context construction

`ContextBuilder` builds stateless `BrainRequest` objects from Jarvis-owned state.

It assembles:

- persona/profile message
- runtime state message
- available tools message
- recent turn messages
- active worker job context
- optional compiled memory context
- legacy `memory_blocks`
- request settings
- stable context snapshot metadata

Provider sessions are explicitly marked as not memory. User input is budget-capped to avoid unbounded prompt/stdin behavior.

## Memory OS integration

Memory OS is layered on top of the older `memory_blocks` path. The old path is preserved until an explicit cutover exists.

Current Memory OS flow:

1. Observation/candidate creation.
2. Evidence attachment.
3. Approval/rejection.
4. Activation into `memory_items`.
5. Deterministic compilation.
6. Optional ContextBuilder inclusion behind default-off config, session/profile, and request-scoped internal gates.
7. Absolute disable through `[memory].enabled=false` or `compiled_memory_force_disabled`.

The MemoryCompiler path is read-only during context build. Final `BrainRequest.context_messages` expose only prompt-safe compiled memory fields; diagnostics remain outside model-visible context and are redacted/coarse.

## Tool and approval system

Tools live under `jarvis/tools/`. The registry exposes tool specs, evaluates permissions, records approvals, executes approved requests, and records tool runs.

Permission design is source-sensitive. Model-originated calls are treated differently from user-initiated actions. Dangerous operations require approval or are blocked.

Important files:

- `jarvis/tools/permissions.py`
- `jarvis/tools/registry.py`
- `jarvis/tools/file_tool.py`
- `jarvis/tools/shell_tool.py`
- `jarvis/tools/terminal_tool.py`
- `jarvis/tools/screen_tool.py`
- `jarvis/tools/ui_tool.py`
- `jarvis/tools/memory_tool.py`

## Worker model

Workers live under `jarvis/workers/`. They are silent. They can produce candidates, not committed facts or speech.

The worker broker owns job lifecycle and persistence. Workers must not bypass memory approval or the broker/speaker model.

## Voice subsystem

Voice is split into deterministic pieces:

- queue: persisted voice requests
- broker: the only speaker
- chunker: sentence streaming
- listening leases: PTT/locked listening state
- recorder: mic capture behind lease policy
- STT: transcription pipeline
- TTS: synthesis/player adapter
- anti-echo: prevents Jarvis hearing itself as user input
- cancellation: barge-in and interrupt handling
- gateway: accepted transcript to normal turn pipeline

Primary files:

- `jarvis/voice/broker.py`
- `jarvis/voice/queue.py`
- `jarvis/voice/listening.py`
- `jarvis/voice/recorder.py`
- `jarvis/voice/stt.py`
- `jarvis/voice/tts.py`
- `jarvis/voice/anti_echo.py`
- `jarvis/voice/cancellation.py`
- `jarvis/voice/gateway.py`

Automated tests must not run live mic/speaker behavior.

## Panel subsystem

The panel is a client. It renders daemon state and sends intents.

Primary files:

- `jarvis/panel/menubar_app.py`
- `jarvis/panel/hotkey.py`
- `jarvis/panel/assets/`
- `jarvis/panel/webview_bridge.py`

Panel work must not move canonical state out of the daemon.

## Storage model

SQLite is the local source of durable truth. Schema lives in `jarvis/store/schema.sql`; migrations live in `jarvis/store/migrations.py`.

Major durable areas:

- schema version
- events
- conversations
- turns
- memory blocks
- Memory OS observations/candidates/items/evidence
- settings
- approvals/tool runs
- worker jobs
- voice queue/listening/audio snapshots

Schema and migration changes require explicit scope.

## Security model

Jarvis security is built around local-only runtime, token gates, approval gates, source-sensitive tool policy, redaction, and fail-closed behavior.

Do not bypass:

- local host checks
- transport token checks
- approval gates
- tool permission policy
- root containment
- redaction
- compiled memory default-off behavior
- compiled memory kill-switch precedence
- voice broker ownership

## Testing model

The test suite covers API hardening, transport token behavior, context building, Memory OS, compiler contracts, tool permissions, redaction, voice components, panel assets, launchd assets, and smoke scripts.

Live voice, mic, speaker, launchctl, provider CLIs, and network behavior do not belong in automated CI.

## Known limitations

- Compiled memory is not globally or production enabled.
- Memory OS does not yet have production usage ledger events.
- Dev/local config enablement exists, still default-off.
- Session/profile scoped enablement exists and is internal-only.
- Request-scoped override exists and is internal-only.
- `compiled_memory_force_disabled` disables compiled memory regardless of config, session/profile, or request override.
- No env, panel, public API, or user-facing compiled-memory toggle exists.
- Panel controls for compiled memory do not exist.
- Background memory summarization/consolidation is not active.
