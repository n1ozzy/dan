# Jarvis POC Runtime Settings Gap Audit

Status: Spike / analysis. Non-authoritative. Do not treat this as a runtime
contract until promoted into current contract docs.

Scope: current code/config/routes/panel only. No daemon, provider, mic, speaker,
or network runtime was started.

## Current Coverage

The current POC inventory is useful as a read-only safety map, not as an
editable settings model.

- Panel overview reads existing daemon surfaces: `/health`, `/state`,
  `/settings`, `/brain/adapters`, `/audio/devices`, `/voice/listening`,
  `/voice/queue`, `/tools`, and `/events`.
- Audio/device coverage exists through `AudioDeviceManager` and
  `/audio/devices`: backend, input/output device names, transport, preferred
  input, output policy, Bluetooth mic allowance, warnings, and observed devices.
- Brain coverage exists through `BrainManager` and `/brain/adapters`: registered
  adapters, active adapter, default adapter, and adapter-advertised model list.
  Current implemented adapters are mock, Claude CLI, Claude warm prototype when
  selected, and Codex CLI. OpenAI is an unavailable placeholder. Ollama,
  llama.cpp, Grok, Bielik, and Mistral are not current adapters.
- Voice runtime coverage exists in backend code: listening leases, sox capture,
  MLX Whisper STT, energy/VAD gate, junk transcript filter, anti-echo,
  mic-side barge-in, speech queue, filler, Supertonic TTS, playback process kill,
  and queue tombstones.
- Tools coverage exists as registered tool specs and risk classes. There is a
  permission policy for network-class tools, but no registered network/internet
  tool in the current registry.
- Persona coverage exists through `persona.profile` in the settings table;
  `ContextBuilder` validates the profile and reads it per turn.
- Logs/errors are visible only as recent events and panel-side summaries. There
  is no typed trace/debug health projection.

## Missing Capability Groups

Future settings UI should not group the runtime as just "Voice / Audio" and
"Brain / Provider". A real local Mac voice agent needs these sections:

1. Capture / Input
   - Current: audio backend, selected input, preferred input, Bluetooth policy,
     sox recorder, sample rate, highpass, gain, PTT hold/locked leases.
   - Missing: stable CoreAudio device UID, live route-change status, input
     permission/TCC status, active recorder process status, capture latency,
     capture format validation, hotkey monitor status, and mute/ducking policy.

2. STT / Transcription
   - Current: mock and MLX Whisper engine code; model, language, timeout, one
     dedicated MLX worker thread, transient private WAVs.
   - Missing: typed endpoint showing selected STT engine/model/language,
     package availability, model-cache availability, last STT failure, STT warm
     state, and engine-specific capability schema. faster-whisper and
     whisper.cpp are not current engines.

3. Endpointing / VAD
   - Current: `CaptureGate` thresholds for RMS, voiced seconds, voiced ratio,
     minimum capture duration, plus junk/degenerated transcript filters.
   - Missing: explicit endpointing strategy. Current capture is mostly
     lease/segment bounded, not a streaming endpointer. There is no realtime
     speech-start/speech-end state, no adaptive VAD, no no-speech timeout, and
     no UI-visible VAD decisions beyond event summaries.

4. Turn Manager
   - Current: accepted transcripts pass through anti-echo, then gateway starts
     normal turn orchestration on a worker with bounded busy retry.
   - Missing: typed voice-turn state, dropped-transcript counters, busy retry
     state, last gate decision, and a unified turn-manager view that shows how
     text, voice, approvals, tool continuations, and cancellation interact.

5. Brain / Provider
   - Current: adapter switch, registered adapter list, current/default adapter,
     stateless CLI adapters, context budget inside config/context builder.
   - Missing: provider capability matrix: provider -> models -> context window,
     streaming support, tool-call format, effort/temperature/fast-mode support,
     local/remote/network requirement, credentials status, warm-session status,
     and compatibility invalidation when provider/model changes.

6. TTS / Voice Model
   - Current: mock and Supertonic engines; Chatterbox reserved; banned engines
     fail; Supertonic voice/lang/steps/speed/short-sentence speed/pronunciation
     data exist in config.
   - Missing: typed endpoint showing selected TTS engine, available voices,
     engine package/binary/player availability, model cache status, language
     support, cold/warm mode, ElevenLabs status if it is ever reintroduced, and
     per-engine option validation.

7. Playback
   - Current: Supertonic writes transient WAV, then uses configured `play`
     binary; broker is the only speaker; current player process can be killed.
   - Missing: output device UID, active output route, playback engine status,
     current chunk id, audio stream latency, pad policy visibility, volume/duck
     policy, and whether playback follows system output after device changes.

8. Queue / Barge-in
   - Current: persisted queue, statuses, interruptible filler, tombstones,
     anti-echo corpus based on spoken rows, generation/queue/playback
     cancellation.
   - Missing: typed cancellation policy endpoint, active generation handle
     count, current playback row, tombstone count, cancellation reason history,
     and whether PTT down, accepted transcript, or manual cancel owns a given
     cancellation.

9. Tools / Internet
   - Current: tool specs and risk classes. No current network tool registered.
   - Missing: typed policy projection for approval requirements, blocked
     reasons, approved roots, internet/network capability, credential status,
     and per-provider tool compatibility.

10. Logs / Trace
    - Current: recent events and redacted summaries.
    - Missing: typed latest runtime/voice/provider/tool errors, trace ids,
      active turn id, health of subprocess dependencies, last config validation
      error, and "test/debug" status. A panel search over recent events is not a
      trace system.

## Wrong Assumptions Found

- `/settings` is not a typed settings registry. It is a generic DB key/value
  table and accepts arbitrary JSON keys. Editable UI must not infer config
  authority from those keys.
- "Voice / Audio" is too broad. Capture, STT, endpointing, TTS, playback, and
  queue/barge-in have different owners and compatibility constraints.
- "TTS engine/provider" and "STT/transcription engine" are config-backed today,
  but not safely exposed through a read endpoint. The panel correctly marks
  them not exposed; future UI needs backend projection.
- "Brain model" is not a global setting once multiple providers exist. Model,
  effort, context window, streaming, and tool-call support are provider-specific.
- The current "internet/network capability" view cannot be inferred from
  `require_approval_for_network`; no current registered network tool means
  network policy exists without an actual internet tool surface.
- The current logs overview is a heuristic over recent events. It should not be
  treated as operational trace, error taxonomy, or readiness status.
- app.js is growing into a mixed client, state mapper, renderer, event
  summarizer, and settings inventory module. The runtime overview helpers are
  acceptable for POC, but should not be expanded much further in one file.

## High-Risk Invalid Combinations

- `voice.enabled=true` with `default_tts=supertonic` but missing `supertonic`
  binary or non-executable playback binary: daemon startup fails.
- `voice.enabled=true` with `default_stt=mlx_whisper` but missing
  `mlx_whisper` package/model/runtime dir: daemon startup or first transcription
  fails.
- Any future editable `default_stt` set to `faster-whisper`, `whispercpp`, or
  another string while only mock/MLX Whisper are implemented: startup failure.
- Any future editable `default_tts` set to Chatterbox before G5: explicit
  reserved-engine failure. edgeTTS, piper, and XTTS are banned and must remain
  hard failures, not fallbacks.
- `recorder=sox` without a usable input device after policy application:
  listening lease may exist but capture fails closed.
- Bluetooth microphone allowed accidentally with the current policy intent:
  lower quality echo-prone capture can degrade VAD/STT and barge-in behavior.
- Supertonic speed raised globally without short-sentence guard: current comments
  document clipped final phonemes and near-silent short outputs.
- Switching provider/model while stale effort/fast/context/tool settings remain:
  invalid flags, degraded context, or provider errors.
- Provider marked as supporting tools/streaming when adapter only degrades to
  final text: the voice UX expects first-sound streaming and may silently get
  delayed speech.
- Raw `/settings` edits of `persona.profile` to a missing or invalid profile:
  runtime falls back to base persona, so UI must show effective value, not just
  requested value.

## Compatibility Guards Needed

Backend-owned guards should validate and expose:

- Capture backend -> input policy -> device eligibility -> recorder command.
- STT engine -> model -> language -> timeout -> VAD thresholds -> package/model
  availability.
- TTS engine -> voice/model -> language -> rate/speed -> playback binary ->
  output route -> package/model availability.
- Provider -> model -> context window -> effort/fast support -> streaming support
  -> tool-call support -> credential/network requirements.
- Tool capability -> approval policy -> required credentials -> provider support.
- Persona requested profile -> effective profile -> file existence.

When a parent setting changes, the backend should invalidate or coerce
unsupported child options and report the effective result. The panel should
render the backend result, not own this logic.

## Backend-Owned Data

These should be projected by daemon routes before editable UI:

- Effective config snapshot with redaction and source metadata
  (`config`, `settings table`, `default`, `runtime detected`).
- Capability schemas for each provider, STT engine, TTS engine, recorder,
  playback engine, and tool family.
- Effective voice runtime status: capture, STT, VAD, gateway, queue, TTS,
  playback, cancellation, and last failures.
- Dependency readiness: binaries, packages, model caches, permissions, device
  route, API credentials, and network availability.
- Effective option values after compatibility guards, including reset/coercion
  reasons.

## Recommended Next 5 POC Tasks

1. Add a read-only backend runtime settings projection endpoint.
   - Shape it as typed groups matching this audit.
   - Include value, source, effective value, editable-later flag, and redacted
     diagnostics.
   - Do not reuse raw `/settings` as the typed contract.

2. Add backend capability schemas and compatibility guards for Brain/Provider.
   - Start with mock, Claude CLI, Codex CLI, and unavailable OpenAI placeholder.
   - Explicitly represent model, effort, context, streaming, tools, credentials,
     and network requirements.

3. Add backend voice runtime projection.
   - Split Capture/Input, STT, Endpointing/VAD, Turn Manager, TTS, Playback, and
     Queue/Barge-in.
   - Include dependency readiness and last failure per group.

4. Refactor panel runtime overview out of the main app.js growth path.
   - Keep app.js as orchestrator/binder.
   - Move pure mapping/render helpers into a small module only after the static
     asset packaging approach supports it, or create a clearly bounded section
     object in the same file as an intermediate POC step.

5. Add edit-intent design for settings without implementing edits.
   - Define backend mutation endpoints and invalidation semantics first.
   - Start with one low-risk group, such as persona profile or brain adapter,
     before touching live voice/audio.

## Avoid Porting Blindly To Main

- Do not port panel-side guesses about provider, TTS, STT, or policy
  compatibility.
- Do not port the raw settings editor as the basis for product settings.
- Do not collapse all audio/voice controls into one "Voice / Audio" section.
- Do not expose secrets, full CLI args, headers, cookies, or provider env values
  in settings projection.
- Do not add editable voice toggles that can start mic/speaker/provider activity
  from automated checks.
- Do not add provider/model switches without backend invalidation of stale model,
  effort, fast-mode, context, and tool options.
- Do not make the panel the source of truth for dependencies, compatibility, or
  effective runtime state.
- Do not keep growing app.js indefinitely; POC logic is already near the point
  where runtime overview should be isolated.
