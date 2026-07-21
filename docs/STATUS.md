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
- Live config is `~/.dan/config.toml`, outside the repo. `config/dan.example.toml`
  is only the shipped template; it does not describe the running daemon.

## Owner runtime override — 2026-07-13

- One shared DAN/Jarvis canon:
  `$HOME/Documents/dev/dan/config/persona/DAN.md`, loaded fail-loud.
- One persistent `claude_cli` stream-json process; durable resume/checkpoint
  state is an execution cache, not memory. Provider chains remain disabled.
- Model-originated tools execute directly and finish the turn; no approval row
  or `awaiting_approval` branch is active for them.
- `ToolPermissionPolicy.decide()` returns ALLOW for every risk class and every
  source; `ToolRegistry.request_tool()` ignores the policy, the source and the
  approval gate. The `require_approval_for_*` flags are inert configuration
  compatibility fields rendered as runtime state.
- What still refuses work lives inside the tools: approved-root containment, the
  `shell_read` allowlist (opt-out: `security.shell_read_unrestricted`, default
  false), the scrubbed environment, git hardening, runtime/output bounds.
- **On this machine two of those are already off (measured 2026-07-21 in
  `~/.dan/config.toml`).** `shell_read_unrestricted = true`, so the allowlist
  refuses nothing; `auto_approve_mode = 'all'` and
  `destructive_tools_enabled = true`; `api_token_required = false`. That is the
  owner's deliberate choice, the same way compiled memory is turned on below —
  a shipping default is not a description of the running daemon. But note the
  knock-on effect, which nobody chose: with the allowlist off, the git hardening
  in `shell_tool.py` stops being exhaustive. Mechanism and `file:line` evidence:
  `docs/SECURITY_MODEL.md` §2, "The `shell_read` allowlist and its opt-out".
- **`approved_roots` is the one barrier that is real, and it is set wide.**
  Measured 2026-07-21: `["~", "/tmp", "/Volumes", "/Applications"]`. The file
  tools do enforce it, exactly as `SECURITY_MODEL.md` says — but "contained to
  approved roots" on this machine means the whole home directory. Do not quote
  the containment as if it scoped DAN to the project.
- Secret redaction IS live (`dan/security/redaction.py`) plus a 4096-char cap on
  persisted strings.
- Memory/history are untrusted context data and cannot replace the system persona.
- Any doc that promises an approval gate is historical evidence, not the active
  branch contract.

## Git Snapshot

- Branch: `agent/dan-release1-integration` (the only working branch; `main`,
  `rescue/*` and `spike/*` are dormant).
- Release 1 tag: `dan-v1-foundation-candidate` → commit `1852d7f docs: record
  operator sign-off for live voice routes` (annotated tag, verified 2026-07-21).
- **No HEAD SHA here** — it would be stale one commit later. Read it with
  `git rev-parse --short HEAD`.
- **"The only working branch" describes branches, not checkouts.** Run
  `git worktree list` before trusting a doc you opened by path: a second
  worktree keeps its own copy of `docs/`, which every correction made here
  bypasses. (2026-07-21: two extra worktrees were registered, one of them
  already `prunable`, against the owner's standing "no worktrees" rule.)
- Test evidence must come from a command run in the current task, never from
  a number quoted in this file.

## Current Status

- `memory_blocks` remain preserved legacy infrastructure.
- Auto-memory extraction is not implemented in the daemon.
- Real live voice still requires manual validation; automated tests mock TTS.

## Memory OS Guarantees

- Compiled memory ships default-off (`config/dan.example.toml`
  `compiled_context_enabled = false`). Ozzy's live `~/.dan/config.toml` turns it
  ON on this machine — "default-off" is a shipping default, not a description of
  the running daemon.
- Config-based dev/local enablement exists.
- Session/profile scoped enablement exists and is internal-only.
- Request-scoped override exists and is internal-only.
- Operator env controls exist: `DAN_COMPILED_MEMORY_ENABLED` and
  `DAN_COMPILED_MEMORY_FORCE_DISABLED` (read in `dan/daemon/app.py`). No panel,
  public API, user-facing or global production enablement exists.
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

## Memory OS rollout handoff — snapshot (superseded 2026-07-18, historical)

Frozen record of the `MEMORY-OS-FINAL-HANDOFF-01` checkpoint, kept because later
audits quote its numbers. **Every line in this section describes that snapshot,
not this branch.** Do not read it as current state; the current reading is
"Memory OS Guarantees" above.

- Branch: `rescue/audt-gpt5.5pro-limit-cdn`.
- HEAD: `58cca12 docs: finalize Memory OS rollout handoff`.
- `MEMORY-CONTEXT-ROLLOUT-READINESS-01` completed as a read-only audit —
  focused validation: 176 passed; memory/context regression: 426 passed; no
  files changed; no commit made. Those counts were measured at that snapshot
  and are not evidence for any later branch.
- Memory OS compiled-memory policy docs, docs status refresh, session/profile
  scoped enablement, kill switch, rollout precedence matrix tests, and final
  handoff docs are committed at the snapshot above.
- Guarantees as claimed at that snapshot: compiled memory remains default-off;
  config-based dev/local enablement exists; session/profile scoped enablement
  exists and is internal-only; request-scoped override exists and is
  internal-only; no env, panel, public API, user-facing, or global production
  enablement exists.
- Two of those claims have since stopped being true. Operator env controls
  shipped (`DAN_COMPILED_MEMORY_ENABLED`, `DAN_COMPILED_MEMORY_FORCE_DISABLED`),
  so the env half of the last bullet is dead; and the live `~/.dan/config.toml`
  on this machine turns compiled memory ON, so "default-off" now only describes
  the shipped template.

## Latest Known Rescue Fix

- RESCUE-VOICE-01: ptt_down must not cancel active speech.

This checkout contains a regression-test name for that behavior. The historical
`docs/JARVIS_FIX_TASKS_HANDOFF.md` still describes the older opposite behavior,
so treat that handoff as historical evidence only.

## Runtime Settings / PTT Review Blockers (pre-Release-1, not re-verified)

Historical review, 2026-07-08, of `80dcbb5` on the retired branch
`spike/jarvis-local-runtime-check`. It has NOT been re-run against
`agent/dan-release1-integration` — treat every item as "unknown today", not as a
live blocker. It found these contract mismatches at the time:

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
