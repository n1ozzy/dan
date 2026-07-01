# Jarvis v4.1 Reviewer Handoff

## Purpose

- This document is for future model/human review.
- It summarizes current state, completed milestones, manual smoke results,
  known risks, and recommended next prompts.
- It is not an execution roadmap by itself.
- It does not supersede `docs/CONTRACTS.md`, `docs/DECISIONS.md`,
  `docs/SECURITY_MODEL.md`, or `docs/MACOS_OPERATOR_CONTRACT.md`.

## Source-of-truth warning

- Jarvis v4.1 docs and current code are authoritative.
- JARVIS-V3-EXECUTION-ROADMAP.md is historical only.
- /Users/n1_ozzy/Documents/dev/dan is read-only legacy reference only.

## Current HEAD

Reviewer command:

```sh
git rev-parse HEAD
```

Current known commit:

```text
8225520af661f0f3ad13d3423ea4249d4714287b
docs: separate operator examples from commitments
```

Reviewers should verify the current `HEAD` before using this document. Treat
this section as orientation, not proof that the checkout is still at this commit.

## Completed milestones

- Contracts/scaffold/config/schema/EventStore/state machine/daemon API.
- RuntimeSupervisor report-only.
- Brain interface, MockBrainAdapter, Claude CLI/Codex CLI safe subprocess
  adapter foundation.
- Text turn pipeline and CLI input.
- Read-only conversations/turns API and CLI.
- Tool registry, ApprovalGate, safe placeholders.
- Execute-approved endpoint.
- Model tool-call capture.
- Provider tool-call parser.
- Memory API/CLI.
- Memory runtime smoke harness.
- Static cockpit.
- Localhost-only CORS for cockpit.
- Central EventStore secret redaction.
- Prompt 19A: approval approve/reject decision events.
- Prompt 19B: `PermissionPolicy` on model-originated tool-call path.
- Prompt 19C: `awaiting_approval` turn status with
  `/state.pending_approval_count`; runtime remains in the canonical state set.
- Prompt 20A: macOS operator contract added before Prompt 19D-mini so
  continuation design accounts for future one-shot tools and longer operator
  sessions.
- Prompt 20A-FIX: operator examples separated from implementation commitments;
  concrete macOS capabilities require later scoped prompts, contracts, tests,
  and permission policy before implementation.
- Prompt 19D-mini: approved, explicitly executed one-shot tool results continue
  the original `awaiting_approval` turn through a continuation brain request.
  The same turn is updated to `finished` on continuation success; continuation
  failure leaves the tool run recorded and the turn `awaiting_approval` with
  predictable `tool_result_continuation` error metadata.

## Manual smoke results known

- Text runtime smoke passed.
- Claude CLI brain smoke passed.
- Tools approval smoke passed.
- Provider tool-call capture smoke passed.
- Memory runtime smoke passed.
- Static cockpit manual smoke passed enough to confirm health/state, input via
  mock, history/turns, events, tools load, and runtime read-only behavior.
- CORS confirmed with `curl` and an `Origin` header.
- `voice_queue` and `worker_jobs` remained `0` during relevant smokes.

## Current safety boundaries

- `jarvisd` owns truth.
- The panel is client only.
- Provider sessions are not memory.
- Model-originated tool calls are classified through `PermissionPolicy`; only
  non-blocked registered calls become approvals, never execution.
- Approval execute is explicit.
- Runtime conflicts are report-only.
- Voice/workers/launchd are not active.
- EventStore now redacts secrets before persistence.

## Known open risks / review priorities

- H5: API auth/CSRF/Origin/Host hardening still needed before dangerous tools.
- H6/M2/M3: file/shell safety needs `realpath`, fail-closed roots, and write
  restrictions before real file/shell tools.
- M4: CLI model labeling vs actual provider model may need review.
- M5: recent-turn ordering by second timestamp/random uuid may need rowid
  ordering.
- M7: `ps` timeout in runtime supervisor if still unresolved.
- Tool schema validation if still unresolved.
- HookRouter not implemented yet.
- Workers not implemented yet.
- Voice not implemented yet.

## Recommended next prompt sequence

- Prompt 20B: macOS capability inventory and permission model.
- Then consider HookRouter foundation.
- Future work list to keep separate from Prompt 20B: HookRouter, workers,
  macOS Accessibility read-only/action, ScreenCaptureKit/Vision, SMS/browser/
  passkey examples/classes, and voice.

## Reviewer checklist

Start with repository state:

```sh
git status --short
git log --oneline -n 20
git diff --stat
```

Use targeted tests from `README.md` and `docs/runbooks/*` before broadening.
Useful starting point:

```sh
.venv/bin/python -m pytest \
  tests/test_tool_result_continuation.py \
  tests/test_awaiting_approval_status.py \
  tests/test_model_tool_permission_policy.py \
  tests/test_approval_events.py \
  tests/test_secret_redaction.py \
  tests/test_event_store.py \
  tests/test_api_cors.py \
  tests/test_panel_assets.py \
  tests/test_api_smoke.py \
  tests/test_memory_api.py \
  tests/test_tool_permissions.py \
  tests/test_text_turn_pipeline.py \
  tests/test_brain_cli_adapters.py \
  tests/test_history_api.py \
  tests/test_turn_repository.py \
  tests/test_context_builder.py \
  tests/test_memory_manager.py \
  tests/test_runtime_supervisor.py \
  tests/test_state_machine.py \
  tests/test_db_schema.py \
  tests/test_config.py \
  tests/test_imports.py \
  tests/test_scaffold_contracts.py \
  -v
```

Inspect project contracts and runbooks:

```sh
sed -n '1,240p' docs/CONTRACTS.md
sed -n '1,240p' docs/DECISIONS.md
sed -n '1,240p' docs/SECURITY_MODEL.md
sed -n '1,260p' docs/MACOS_OPERATOR_CONTRACT.md
sed -n '1,240p' docs/PANEL_CONTRACT.md
ls docs/runbooks
```

Inspect current implementation focus areas:

```sh
sed -n '1,240p' jarvis/store/event_store.py
sed -n '1,240p' jarvis/security/redaction.py
sed -n '1,260p' jarvis/turns/orchestrator.py
sed -n '1,240p' jarvis/tools/registry.py
sed -n '1,240p' jarvis/tools/permissions.py
sed -n '1,240p' jarvis/daemon/lifecycle.py
```

## What not to do during review

- Do not start voice.
- Do not run `launchctl`.
- Do not clean legacy processes automatically.
- Do not use old DAN as runtime.
- Do not modify schema unless explicitly scoped.
- Do not run real provider subprocesses unless doing manual smoke.
- Do not treat cockpit as source of truth.
- Do not broaden scope into workers/voice while reviewing approval loop.

## Handoff prompt for reviewer

```text
Review this repository as Jarvis v4.1. Use docs/REVIEW_HANDOFF.md as orientation, but verify against current code. Focus on approval loop, PermissionPolicy, EventStore redaction, transport safety, and context/memory correctness. Do not implement changes unless explicitly asked.
```
