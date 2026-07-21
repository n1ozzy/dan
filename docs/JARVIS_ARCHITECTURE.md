# DAN Architecture

Classification: technical architecture.
Scope: the whole runtime shipped from this repo on branch
`agent/dan-release1-integration`. Module paths below were re-verified against
the tree on 2026-07-21. (This file keeps its `JARVIS_ARCHITECTURE.md` name for
link stability; Jarvis is an alias of DAN, not a separate system.)

## System overview

DAN is a local, single-user assistant runtime. The central process is `dand`
(package `dan-runtime`, entry point `dan.cli:daemon_main`, launchd label
`com.dan.dand`, HTTP API on `127.0.0.1:41741`). It owns durable state and
exposes APIs for clients, tools, voice, memory, settings and event streams.

The system is intentionally daemon-centered. Clients render state and send intents. They do not own canonical data. The single Claude CLI provider session is a daemon-owned execution cache and cannot be treated as memory.

## Runtime ownership model

Core laws:

- `dand` owns truth.
- The panel is a client.
- The single `claude_cli` adapter owns one serialized persistent process.
- Provider sessions are not DAN's memory.
- Workers are silent candidate producers.
- The voice broker is the only speaker.
- `/tmp` is not a source of truth.
- Legacy DAN (the pre-daemon scripts) is reference-only.

## Main module map

| Area | Paths |
|---|---|
| Daemon | `dan/daemon/` |
| API | `dan/api/` |
| Brain | `dan/brain/` |
| Turns | `dan/turns/` |
| Memory | `dan/memory/` |
| Storage | `dan/store/` |
| Events | `dan/events/` |
| Tools | `dan/tools/` |
| Voice | `dan/voice/` |
| Audio | `dan/audio/` |
| Input (global hotkey) | `dan/input/` |
| Panel | `dan/panel/` |
| macOS | `dan/macos/` |
| Workers | `dan/workers/` |
| Runtime supervision | `dan/runtime/` |
| Security | `dan/security/` |
| Config | `dan/config.py`, `dan/config_registry.py`, `config/` |

## Daemon lifecycle

`dan/daemon/app.py` wires the application object and owns runtime dependencies. `dan/daemon/lifecycle.py` exposes HTTP lifecycle and routing helpers. `dan/daemon/supervisor.py` owns supervised child processes, `dan/daemon/restart.py` the drain-and-exit restart, and `dan/daemon/intake.py` the intake gate.

The daemon coordinates:

- DB connection and repositories.
- Event store and event bus.
- Brain manager and adapters.
- ContextBuilder.
- Turn orchestrator.
- Tool registry and tool-run recorder (the approval gate is held only as a
  compatibility surface — see "Tool system" below).
- Memory managers and repositories.
- Worker broker.
- Voice queue, broker, listening leases, recorder/STT/TTS plumbing.
- The global PTT hotkey monitor and supervised child processes.
- Settings and runtime state.

## API layer

The API layer is split by concern (`dan/api/`):

- `routes_health.py`
- `routes_state.py`
- `routes_runtime.py` — by far the largest; also carries the approval and
  panel/runtime endpoints. There is no separate `routes_approvals.py`.
- `routes_settings.py`
- `routes_history.py`
- `routes_input.py`
- `routes_intake.py`
- `routes_sessions.py`
- `routes_brain.py`
- `routes_tools.py`
- `routes_events.py`
- `routes_workers.py`
- `routes_memory.py`
- `routes_audio.py`
- `routes_voice.py`
- `event_safety.py` — the shared client-safe payload projection used by both
  `routes_events.py` and the websocket.
- `websocket.py`
- `client.py`

`lifecycle.py` handles request dispatch, local-host/CORS/token checks, request body limits, and WebSocket stream session limits. The transport token header is `X-DAN-Token` (`dan/security/transport.py`).

## Brain layer

The brain layer has one production adapter, `claude_cli` (the config loader
pins `brain.default_adapter` to it). It keeps one serialized `stream-json`
process alive for the active DAN conversation. The initial generation receives
the fresh canonical DAN system prompt and complete context; healthy later
generations receive only new input or tool continuations. Durable state lives
under the runtime directory (`~/.dan/runtime/`) with mode 0600.

Primary files:

- `dan/brain/base.py`
- `dan/brain/manager.py`
- `dan/brain/context_builder.py`
- `dan/brain/claude_cli_adapter.py`
- `dan/brain/tool_call_parser.py`

Other adapters in `dan/brain/` (codex_cli, ollama, openai, qwen, eco, mock,
test, sync) exist as alternates/test doubles, not as the production route.

`BrainRequest` contains:

- `turn_id`
- `conversation_id`
- `input_text`
- `context_messages`
- `memory_blocks`
- `available_tools`
- `settings`
- `metadata`

The adapter receives a fully assembled DAN-owned request at bootstrap. Its
provider session is only an execution cache: DAN's conversation storage and
memory remain authoritative. A RESUMED provider session keeps its ORIGINAL
system prompt and tool set — our prompt only rides along as
`--append-system-prompt` — so a poisoned checkpoint survives restarts and is
recovered by quarantining `~/.dan/runtime/claude-session.json`
([ODZYSKIWANIE.md](ODZYSKIWANIE.md)).

## Context construction

`ContextBuilder` builds DAN-owned `BrainRequest` objects. The complete
request bootstraps or rebuilds a provider session; healthy continuations send
only the new input.

It assembles:

- the persona message — ONE canon, `config/persona/DAN.md`, loaded fail-closed
  and required to carry the literal header `DAN_CANON_VERSION: 1`
  (`dan/persona.py`). Per-profile personas are gone; `persona_profile` is
  pinned to `dan`. The only substitution is `{{ owner.display_name }}` from
  `~/.dan/owner.toml`.
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

## Tool system

Tools live under `dan/tools/`. The registry exposes tool specs, executes
requests and records every run in `tool_runs` plus the `tool.*` events.

**There is no permission gate in front of a tool.**
`ToolPermissionPolicy.decide()` returns ALLOW unconditionally for every risk
class and every source, `ToolRegistry.request_tool()` ignores its
`permission_policy` / `source` / `approval_gate` arguments, and
`DaemonApp.request_tool()` deletes the source before executing. The
`require_approval_for_*` flags and `destructive_tools_enabled` are
configuration compatibility fields rendered as runtime state; they block
nothing. `ApprovalGate` persists rows only for the explicit approve/execute
routes and the memory-save proposal path.

Containment therefore lives INSIDE each tool: approved-root checks
(`file_read`, `file_write`, `shell_read` cwd), the exact-match `shell_read`
allowlist with its `security.shell_read_unrestricted` opt-out, a scrubbed
environment plus git hardening (`core.fsmonitor`, `core.hooksPath`,
`protocol.ext` disarmed), size/time bounds, and the secure-field and
control-character refusals in `ui_*` and `terminal_*`. Secret redaction and the
4096-char persistence cap are real and applied to every recorded payload.

Important files:

- `dan/tools/registry.py`
- `dan/tools/permissions.py` (classification vocabulary only)
- `dan/tools/file_tool.py`
- `dan/tools/shell_tool.py`
- `dan/tools/terminal_tool.py`
- `dan/tools/screen_tool.py`
- `dan/tools/ui_tool.py`
- `dan/tools/web_tool.py`
- `dan/tools/memory_tool.py`, `dan/tools/memory_recall_tool.py`
- `dan/tools/system_tool.py`

## Worker model

Workers live under `dan/workers/`. They are silent. They can produce candidates, not committed facts or speech.

The worker broker owns job lifecycle and persistence — workers hold no DB handle, no event store, no memory manager and no voice queue. A worker result becomes an inactive memory candidate written by the broker; it is auto-promoted only when `memory.worker_candidates_require_promotion` is false, which is what the shipped example config sets.

## Voice subsystem

Voice is split into deterministic pieces:

- queue: persisted voice requests
- broker: the only speaker
- chunker: sentence streaming
- listening leases: PTT/locked listening state
- recorder: mic capture behind lease policy
- STT: transcription pipeline
- TTS: synthesis/player adapter
- anti-echo: prevents DAN hearing itself as user input
- cancellation: barge-in and interrupt handling
- gateway: accepted transcript to normal turn pipeline
- resolver/service: persona + pronunciation catalog resolved into the immutable
  per-utterance render snapshot

Primary files:

- `dan/voice/broker.py`
- `dan/voice/queue.py`
- `dan/voice/listening.py`
- `dan/voice/recorder.py`
- `dan/voice/stt.py`
- `dan/voice/tts.py`
- `dan/voice/anti_echo.py`
- `dan/voice/cancellation.py`
- `dan/voice/gateway.py`
- `dan/voice/resolver.py`, `dan/voice/service.py`, `dan/voice/persona_editor.py`

The voice catalog is data in this repo: `config/voice/personas.toml` and
`config/voice/pronunciations.toml`. Never hardcode voice values.
Automated tests must not run live mic/speaker behavior.

## Panel subsystem

The panel is a client. It renders daemon state and sends intents.

Primary files:

- `dan/panel/menubar_app.py`
- `dan/panel/hotkey.py`
- `dan/panel/assets/`
- `dan/panel/webview_bridge.py`

The machine-wide PTT event tap itself is daemon-owned (`dan/input/hotkey.py`,
`dan/input/macos_event_tap.py`): exactly one `CGEventTap`, guarded by an
exclusive `flock` on a lock file in `~/.dan/runtime/`, and a second owner fails
loudly with `SingleOwnerError`. Missing Accessibility permission is a health
fact, not a crash. Panel work must not move canonical state out of the daemon.

## Storage model

SQLite is the local source of durable truth at `~/.dan/dan.db`. Schema lives in `dan/store/schema.sql`; migrations live in `dan/store/migrations.py`.

Major durable areas:

- schema version
- intake gate + intake leases
- events
- conversations
- turns
- memory blocks
- Memory OS observations/candidates/items/evidence/topics/usage/review decisions
- memory archive documents + FTS index
- settings
- approvals/tool runs
- worker jobs
- voice queue (with its immutable render snapshot and status triggers),
  cancelled-turn tombstones, listening leases, audio snapshots
- runtime process observations

Schema and migration changes require explicit scope.

## Security model

DAN's security is built around a local-only runtime, the transport token,
per-tool containment, redaction and fail-closed startup. It is NOT built around
an approval gate or a source-sensitive policy — those do not exist in the
running code (see "Tool system"). Details and the current threat picture:
[SECURITY_MODEL.md](SECURITY_MODEL.md).

Do not bypass:

- local host checks
- transport token checks (`X-DAN-Token`, or the `dan-token.<token>`
  subprotocol on the websocket)
- approved-root containment inside the tools
- the `shell_read` allowlist unless `security.shell_read_unrestricted` is on
- the scrubbed environment and git hardening
- redaction and the persisted-string cap
- compiled memory default-off behavior
- compiled memory kill-switch precedence
- voice broker ownership (one speaker) and hotkey single-owner locking

## Testing model

The test suite covers API hardening, transport token behavior, context building, Memory OS, compiler contracts, tool behaviour, redaction, voice components, panel assets, launchd assets, and smoke scripts.

Live voice, mic, speaker, launchctl, provider CLIs, and network behavior do not belong in automated CI. Tests MUST mock the TTS layer — never spawn a real player or synthesizer.

## Known limitations

- Compiled memory is not globally or production enabled.
- Memory OS does not yet have production usage ledger events.
- Dev/local config enablement exists, still default-off.
- Session/profile scoped enablement exists and is internal-only.
- Request-scoped override exists and is internal-only.
- `compiled_memory_force_disabled` disables compiled memory regardless of config, session/profile, or request override.
- Two operator ENV controls do exist and are read at config load
  (`DAN_COMPILED_MEMORY_ENABLED`, `DAN_COMPILED_MEMORY_FORCE_DISABLED` —
  `dan/config.py::compiled_memory_operator_env_controls`). There is no panel,
  public API or user-facing compiled-memory toggle.
- Panel controls for compiled memory do not exist.
- Background memory summarization/consolidation is not active.
