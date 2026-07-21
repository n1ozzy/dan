# Jarvis v4.2 Reviewer Handoff

> ## ⛔ HISTORICAL HANDOFF (2026-07-03) — NOT A DESCRIPTION OF CURRENT BEHAVIOUR
>
> **Classification: historical.** Superseded by the Release 1 cutover
> (2026-07-18) and the 2026-07-21 audit. Kept as the record of where the project
> stood after FAZY A–H. **Do not orient a review on it and do not treat its
> safety boundaries as the ones in force.**
>
> Concretely wrong today:
>
> - **"Model-originated tool calls go: PermissionPolicy(source) → approval →
>   explicit execute → ToolRun → continuation; never auto-execute" is FALSE.**
>   `ToolPermissionPolicy.decide()` returns ALLOW unconditionally,
>   `ToolRegistry.request_tool()` ignores its policy/source/approval arguments
>   and executes immediately, and `ApprovalGate` is no longer in the execution
>   path. AGENTS.md forbids adding that gate back.
> - **Naming:** `jarvisd` → `dand`, `~/.jarvis/` → `~/.dan/`,
>   `com.ozzy.jarvisd` → `com.dan.dand`, API `127.0.0.1:41741`. The token file is
>   `~/.dan/runtime/api-token`.
> - **`scripts/jarvis-panel` no longer exists** — the panel is `scripts/dan-panel`
>   (launchd label `com.dan.panel`). `scripts/jarvis-dan-report` does still exist.
> - **`FIXME.md` no longer exists**, so "FIXME.md is the source of truth" points
>   at nothing. Current status lives in `docs/STATUS.md`.
> - **Voice is no longer "deferred by decree".** The voice stack is live: `dand`
>   owns audio, speech goes through `dan speak`, casting canon is
>   `config/voice/personas.toml`. See `docs/GLOS-I-KOLEJKA.md`.
> - The test/smoke counts below ("1322 tests", "22/22 smoke" on 2026-07-02) are a
>   2026-07-02 snapshot, not present-day evidence. (22 `scripts/smoke-*.sh` files
>   do still exist.)
>
> Current truth: `AGENTS.md`, `docs/PROJECT_RULES.md`, `docs/STATUS.md`,
> `docs/CO-JEST-GDZIE.md`, and the code under `dan/`.

## Purpose

- This document is for future model/human review.
- It summarizes current state, completed milestones, manual smoke results,
  known risks, and recommended next steps.
- It is not an execution roadmap by itself; the plan-of-record is
  `docs/MASTER_PLAN.md`.
- It does not supersede `docs/CONTRACTS.md`, `docs/DECISIONS.md`,
  `docs/SECURITY_MODEL.md`, or `docs/MACOS_OPERATOR_CONTRACT.md`.

## Source-of-truth warning

- Current Jarvis docs and current code are authoritative.
- JARVIS-V3-EXECUTION-ROADMAP.md is historical only.
- The legacy DAN checkout under `~/Documents/dev` is read-only reference;
  decree §7.6: nothing of it is deleted, stopped, or reused as runtime.

## Current state (2026-07-03, FAZY A–H closed)

- **Last recorded full gate:** 1322 tests and 22/22 smoke scripts green
  (`scripts/smoke-*.sh`) on 2026-07-02. Later fix work used focused tests; do
  not treat this file as fresh test evidence unless the commands below are
  re-run.
- **Security/robustness hardening in progress** — `FIXME.md` is the source of
  truth. Completed there: FIX-01..11 plus FIX-16..17. Open after this doc sync:
  FIX-12 minimal CI, FIX-13 SQLite backup/restore, FIX-15 Supertonic v3/model
  asset audit. Full `pytest` runs only after big tasks.
- FAZY A–F closed: hardening (fail-closed roots, realpath containment,
  transport token), permission model, real file/shell tools behind the
  approval loop, operator adapters, WebSocket `/stream`, brain switch,
  launchd lifecycle, e2e MVP smoke.
- Voice track G0–G4 live and gated: GATE G4 closed, Gate G safety review
  passed (`docs/reviews/2026-07-02-gate-g-voice-safety-review.md`,
  retention = option A unchanged). G5 voice-clone deferred by decree §7.8;
  the MLX chatterbox model (M1) stays on disk and must not be deleted.
- H1 menu-bar shell: `scripts/jarvis-panel` — NSStatusItem (JARVIS wordmark
  template icon) + NSPopover 480×760 + WKWebView rendering the same static
  cockpit assets; token seeded from `~/.jarvis/runtime/api-token`; thin
  client, zero authority (ADR-002). Cockpit is operator-first: basic view
  (conversation input with Enter-to-send, tool approvals, readable history)
  plus an advanced toggle for API/health/memory/tools/settings/events/runtime.
- H2 diagnose-only DAN report: `scripts/jarvis-dan-report`
  (`jarvis/diagnostics/legacy_dan.py`) inventories DAN leftovers, split
  into DAN junk vs Jarvis assets; structurally incapable of deleting
  (source-contract test). Snapshot:
  `docs/reviews/2026-07-02-legacy-dan-leftovers.md`.
- Reviewers should verify the actual `HEAD` with `git log --oneline -5`;
  do not trust this file over the checkout.

## Manual smoke results known

- All 22 smoke harnesses in `scripts/` pass on the current HEAD; they use
  fake/mock brains and audio only (live mic/speaker/GUI is Ozzy-only by
  decree). Fake brains in smokes must speak the Claude CLI `stream-json`
  protocol where the harness requires it.
- Panel visual verification is Ozzy-only: popover rendering, template icon
  in the menu bar, dark chrome.

## Current safety boundaries

- `jarvisd` owns truth; the panel and cockpit are clients only.
- Mutating endpoints require the local transport token; WebSocket
  authenticates via the `jarvis-token.<token>` subprotocol; localhost only.
- Model-originated tool calls go: PermissionPolicy(source) → approval →
  explicit execute → ToolRun → continuation; never auto-execute.
- `file_read` outside approved roots is BLOCKED, symlink escapes are
  BLOCKED (fail-closed, realpath-based; tested).
- Runtime conflicts and legacy leftovers are report-only; nothing is ever
  auto-killed or auto-deleted.
- Banned TTS engines by decree: edgeTTS, piper, XTTS (see
  `tests/test_voice_broker.py`); chatterbox is reserved for G5.
- Package pins unchanged; `pyobjc==12.2.1` lives in the `[panel]` extra so
  the daemon and test suite never import AppKit/WebKit.
- Schema and migrations are frozen (guarded by
  `tests/git_guards.py::assert_schema_and_migrations_unchanged`).

## Known open items / review priorities

- **Panel content redesign (post-MVP backlog):** Ozzy's 2026-07-02 review —
  the operator wants model/provider/effort switching and richer voice controls
  in the panel. PTT/listening mode is already delivered through the existing
  G2 lease endpoints (`/voice/ptt/down|up`, `/voice/listen/lock|unlock`,
  `/voice/listening`); the global hold-to-talk hotkey lives in
  `jarvis.panel.menubar_app`. Model/provider/effort switching and deeper voice
  settings need new daemon endpoints and a scoped design, not panel-side hacks.
  The basic/advanced split is v1 of that redesign.
- Gate G review §7 optional follow-ups (only with Ozzy's green light;
  retention is CLOSED as option A): recorder-vs-lease health check, dead
  `LISTENING_LEASE_CANCELLED` type, degenerate rule for 3+ letters.
- G5 voice-clone: deferred by decree §7.8 — do not start it in review.

## Reviewer checklist

```sh
git status --short
git log --oneline -n 20
.venv/bin/python -m pytest -q          # full suite, ~2.5 min
for s in scripts/smoke-*.sh; do "$s" >/dev/null 2>&1 && echo "PASS $s" || echo "FAIL $s"; done
```

Key contracts and runbooks:

```sh
sed -n '1,240p' docs/CONTRACTS.md
sed -n '1,240p' docs/DECISIONS.md
sed -n '1,240p' docs/SECURITY_MODEL.md
sed -n '1,260p' docs/MACOS_OPERATOR_CONTRACT.md
sed -n '1,240p' docs/PANEL_CONTRACT.md
ls docs/runbooks
```

## Runbook index

- `docs/runbooks/ACCESSIBILITY_TCC.md` — Accessibility permission flow.
- `docs/runbooks/BRAIN_ADAPTERS.md` — CLI brain adapters and switching.
- `docs/runbooks/E2E_MVP_SMOKE.md` — operator acceptance smoke, §6 map.
- `docs/runbooks/G4_LIVE_GATE.md` — live voice gate procedure (Ozzy-only).
- `docs/runbooks/LAUNCHD.md` — launchd lifecycle, never auto-install.
- `docs/runbooks/MEMORY_API.md` — memory blocks API/CLI.
- `docs/runbooks/PANEL_COCKPIT.md` — static cockpit, routes, stream.
- `docs/runbooks/PANEL_MENUBAR.md` — H1 menu-bar shell install/run.
- `docs/runbooks/PROVIDER_SMOKE.md` — real-provider manual smoke.
- `docs/runbooks/SCREEN_RECORDING_TCC.md` — screen capture permission.
- `docs/runbooks/SQLITE_BACKUP_AND_RECOVERY.md` — `dan.db` backup/restore
  (added after this handoff was written).
- `docs/runbooks/TERMINAL_AUTOMATION_TCC.md` — terminal automation TCC.
- `docs/runbooks/TEXT_RUNTIME_SMOKE.md` — text pipeline smoke.
- `docs/runbooks/TOOLS_AND_APPROVALS.md` — **legacy** approval API reference
  only; the approval loop it documents is no longer in the tool path.

## What not to do during review

- Do not start G5 / voice cloning.
- Do not run live mic/speaker/GUI tests — Ozzy-only by decree.
- Do not clean legacy DAN processes, files, or models — report only
  (`scripts/jarvis-dan-report`); deleting is Ozzy's manual decision.
- Do not use old DAN as runtime or copy its code (decree §7.6).
- Do not modify schema/migrations or package pins.
- Do not run real provider subprocesses unless doing manual smoke.
- Do not treat the cockpit/panel as a source of truth.

## Handoff prompt for reviewer (SPENT — do not reuse)

> This prompt was consumed by the 2026-07-03 review. It is preserved for the
> record only. Reusing it would send a reviewer looking for an approval loop
> that no longer exists and for a voice gate that has since been passed.

```text
Review this repository as Jarvis v4.2 after FAZY A–H. Use docs/REVIEW_HANDOFF.md as orientation, but verify against current code. Plan-of-record: docs/MASTER_PLAN.md. Focus on the approval loop, PermissionPolicy, transport auth, voice gate boundaries (G0–G4 live, G5 deferred), and the thin-client panel. Do not implement changes unless explicitly asked.
```
