# Status

Classification: current.

## Release 1 cutover — 2026-07-18

- Production runtime: this repo at `~/Documents/dev/DAN`, branch
  `agent/dan-release1-integration`, tag `dan-v1-foundation-candidate` (`1852d7f`).
- Daemon: launchd `com.dan.dand` → `~/.dan/bin/dand` (venv `~/.dan/venv`),
  API `127.0.0.1:41741`; supertonic serve (:7788) is dand's supervised child.
- Persona canon: `config/persona/DAN.md` IN THIS REPO; voice canon:
  `config/voice/personas.toml` (old `~/.config/voice` bridge retired).
- Old stack (jarvisd/screen, voice_broker, dev/dan feeder) stopped and parked in
  `~/Documents/DAN-migration-backups/`.
- 7-day observation window in progress (until ~2026-07-25); donor deletion only
  after operator sign-off.
- Sections below are older snapshots; `dev/dan` paths in them are historical.

## Owner runtime override — 2026-07-13

- One shared DAN/Jarvis canon:
  `$HOME/Documents/dev/dan/config/persona/DAN.md`, loaded fail-loud.
- One persistent `claude_cli` stream-json process; durable resume/checkpoint
  state is an execution cache, not memory. Provider chains remain disabled.
- Model-originated tools execute directly and finish the turn; no approval row
  or `awaiting_approval` branch is active for them.
- Memory/history are untrusted context data and cannot replace the system persona.
- The older approval sections below are historical snapshot evidence, not the
  active branch contract.

## Git Snapshot (superseded 2026-07-18 — see "Release 1 cutover" above)

- Branch: `spike/jarvis-local-runtime-check`
- HEAD before this docs refresh: `80dcbb5 Stabilize runtime settings panel and PTT contracts`
- Current scope: docs-only review status refresh for the runtime settings panel
  and PTT contracts. No runtime, code, config, schema, API, panel, provider,
  voice, or env behavior changes.

## Current Status (superseded 2026-07-18 — see "Release 1 cutover" above)

- Historical baseline labels from the earlier rescue checkpoint were: core
  tests green, tools/approvals green, daemon/security/db green, and voice
  unit/mock tests green. They are not fresh evidence for this branch state.
- Real live voice still requires manual validation.
- Runtime settings/PTT review is not clean at this checkpoint. The current code
  still needs a follow-up implementation/test pass before the runtime settings
  and panel PTT path can be treated as merge-ready.
- `MEMORY-CONTEXT-ROLLOUT-READINESS-01` completed as a read-only audit:
  focused validation: 176 passed; memory/context regression: 426 passed; no
  files changed; no commit made.
- Memory OS compiled-memory policy docs, docs status refresh, session/profile
  scoped enablement, kill switch, rollout precedence matrix tests, and final
  handoff docs are committed at the snapshot above.
- Current `memory_blocks` remain preserved legacy infrastructure.
- Auto-memory extraction is not implemented yet.
- No runtime behavior changed by MEMORY-DESIGN-01.
- This checkpoint is the docs-only final handoff after the compiled-memory
  runtime rollout safety workstream.

## Memory OS Guarantees

- Compiled memory remains default-off.
- Config-based dev/local enablement exists.
- Session/profile scoped enablement exists and is internal-only.
- Request-scoped override exists and is internal-only.
- No env, panel, public API, user-facing, or global production enablement exists.
- `[memory].enabled=false` is an absolute compiled-memory disable.
- `compiled_memory_force_disabled` disables compiled memory regardless of config,
  session/profile, or request override.
- Request override False disables one request.
- Request override True cannot bypass the kill switch or `[memory].enabled=false`.
- Empty session/profile allow-list enables zero sessions and does not globally leak.
- `None` allow-list preserves established global config behavior.
- Final BrainRequest output is prompt-safe.
- Diagnostics are redacted and outside model-visible context.
- Compiler failure fails closed.
- Context build remains read-only.
- Policy docs are protected by contract tests.

These are status labels for the current branch checkpoint. Fresh test evidence
must come from commands in the current task, not from this file alone.

## Latest Known Rescue Fix

- RESCUE-VOICE-01: ptt_down must not cancel active speech.

This checkout contains a regression-test name for that behavior. The historical
`docs/JARVIS_FIX_TASKS_HANDOFF.md` still describes the older opposite behavior,
so treat that handoff as historical evidence only.

## Runtime Settings / PTT Review Blockers

The 2026-07-08 review of `80dcbb5` found these unresolved contract mismatches:

- PTT-down no longer cancels active speech by contract, but
  `test_get_runtime_settings_turn_trace_records_ptt_down_barge_in` still
  expects a `cancellation` response payload, a `voice.speak.cancelled` event,
  and interrupted-turn trace fields. Either the smoke test must be updated to
  the non-cancelling contract, or the route must intentionally restore the
  cancellation signal.
- A missing stored `brain.model` must still surface the structured
  `brain_model_missing` warning when the settings preview is invalid. Showing a
  config/default effective model must not hide the blocker unless that warning
  and test contract are deliberately changed together.
- Panel PTT test calls must use an allowed listening source. The backend accepts
  only `ptt`, `global_hotkey`, and `lock`; a source such as `panel_test` is
  rejected before a hold lease is created.
- Panel PTT hotkey validation must match `jarvis.panel.hotkey.parse_hotkey`.
  Backend tokens are side-specific modifiers such as `left_cmd`,
  `right_shift`, `left_option`, and their supported aliases; generic tokens
  such as `cmd`, `shift`, `space`, or arbitrary single-character keys must not
  be shown as applyable unless backend support is added.

## Guardrail Baseline

- `AGENTS.md`, `docs/PROJECT_RULES.md`, and this file win over old handoffs and
  roadmaps.
- Provider sessions are not Jarvis memory.
- Panel/cockpit clients do not own source-of-truth state.
- Automated CI must stay mock/unit safe and must not run live voice, launchctl,
  provider smoke, or live networked provider behavior.
