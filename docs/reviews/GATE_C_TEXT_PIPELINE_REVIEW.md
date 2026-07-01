# Gate C Review — Text Turn Pipeline (Prompt 11)

- **Scope:** `POST /input/text` text turn pipeline and its supporting wiring.
- **Reviewed at commit:** `32e0df5 feat: add text turn pipeline` (pre-review HEAD).
- **Standard:** Jarvis v4.1 (README + `docs/`), not the historical V3 roadmap.
- **Outcome:** **PASS with one tiny fix applied** (HTTP error mapping) plus test hardening.

## Files inspected

Runtime: `jarvis/turns/orchestrator.py`, `jarvis/turns/repository.py`,
`jarvis/turns/models.py`, `jarvis/api/routes_input.py`, `jarvis/daemon/app.py`,
`jarvis/daemon/lifecycle.py`, `jarvis/daemon/state_machine.py`,
`jarvis/brain/context_builder.py`, `jarvis/brain/base.py`,
`jarvis/events/models.py`, `jarvis/store/repositories.py`.

Tests: `tests/test_text_turn_pipeline.py`, `tests/test_api_smoke.py`,
`tests/test_context_builder.py`, `tests/test_turn_repository.py`.

## Method

Static read of the pipeline plus its state-machine, event, and repository
contracts; cross-checked every Gate C focus area against existing tests;
established a green baseline (`58 passed` on the two HTTP suites; `277 passed`
on the full required set) before and after changes.

## Findings summary

| # | Area | Verdict |
|---|------|---------|
| F1 | Busy/non-IDLE `TurnOrchestratorError` mapped to HTTP 500 instead of 409 | **Fixed (tiny, allowed fix B)** |
| O1 | Failure recovery could theoretically strand `THINKING` if the `THINKING→ERROR` event append itself fails | Observation only — not fixed (out of scope) |
| O2 | Un-started/uninitialised `DaemonAppError` maps to 400 in the generic tuple | Observation only — not practically reachable when `started=True` |
| — | Test gaps in Gate C focus areas 1/3/6 | **Closed with regression tests (allowed fix A)** |

## Per-area review (Gate C checklist)

1. **`POST /input/text` validation — PASS.** Invalid JSON → 400 (`_read_json_body`
   raises `ValueError("Malformed JSON …")`); non-object JSON and blank/non-string
   `text` → 400 (`_validate_request_payload`); metadata must be a JSON object;
   `conversation_id` must be a non-empty string; unknown extra keys are ignored and
   never leak into turn metadata (`dict(metadata)` copies only the metadata mapping).
   *Gap closed:* added parametrised tests for invalid `metadata` and
   `conversation_id` types (previously only `text` was covered).

2. **App lifecycle — PASS.** `handle_text_input` refuses when `not self.started`
   (`DaemonAppNotStartedError` → 503) and creates no turn; the success path requires a
   started app; the runtime returns to `IDLE` after a turn and `/health` stays `ok`.

3. **Locking / concurrency — PASS.** `text_turn_lock.acquire(blocking=False)` →
   `DaemonAppBusyError` → 409 with no turn created; the lock is released in a `finally`
   on **both** success and failure. *Gap closed:* added a test that drives a real
   failure through `DaemonApp.handle_text_input` and asserts the lock is free and the
   runtime is back at `IDLE` afterwards.

4. **Turn persistence — PASS.** One input → exactly one turn; conversation is created
   when omitted and reused when a known id is supplied (`get_or_create`); `final_text`
   survives a reload; the current turn is excluded from recent-context history
   (`ContextBuilder._build_recent_turn_messages(exclude_turn_id=…)`); context and brain
   failures mark the turn `failed` with an auditable `error`.

5. **Event timeline — PASS.** Ordered subsequence
   `input.text.received → turn.started → turn.context.built → brain.requested →
   brain.responded → turn.finished` is emitted, with `state.changed` allowed in between.
   All lifecycle events carry `correlation_id = turn_id` and `turn_id = turn_id`;
   events are published to the `EventBus`; a failing subscriber is swallowed
   (`_append_event` guards `bus.publish`) and does not fail the turn.

6. **Runtime state — PASS.** Success path is `IDLE → THINKING → IDLE`. The failure
   path recovers via `THINKING → ERROR → IDLE` (both transitions are always permitted
   by `RuntimeStateMachine.allowed_targets`), so a failed turn does **not** strand the
   runtime in `THINKING`. *Gap closed:* both failure tests now assert the final
   persisted runtime state is `IDLE`.

7. **Boundaries — PASS.** A turn creates no `voice_queue`, `tool_runs`, or
   `worker_jobs` rows; the conversation/turn repositories never touch the event store
   (the orchestrator owns all lifecycle events); `context_snapshot` carries
   `provider_sessions_are_memory = False`; only `MockBrainAdapter` is exercised — no
   network, subprocess, or real provider execution.

8. **Error handling — PASS (after F1).** No response body contains a stack trace
   (`TurnOrchestratorError` → generic "Text turn failed."; the catch-all → "Internal
   server error"). Validation errors never create a turn. Failures after turn creation
   leave a `failed` turn plus `turn.failed` / `error.raised` (and `brain.failed` where
   relevant) events.

## F1 — HTTP mapping for busy/non-IDLE (fixed)

**Before:** the orchestrator's precondition
`if self._state_machine.state is not RuntimeState.IDLE: raise TurnOrchestratorError(...)`
was caught in `lifecycle._dispatch` by `except TurnOrchestratorError → 500`. A request
that arrives while the runtime is busy (e.g. left in `THINKING`/`ERROR` by another
actor) therefore returned **500 Internal Server Error**, even though nothing had
actually failed and no turn was created. Gate C focus area 8 and allowed fix B call for
**409**.

**Fix (minimal, allowed fix B):**

- `jarvis/turns/orchestrator.py`: added `TurnOrchestratorBusyError(TurnOrchestratorError)`
  and raised it (instead of the base class) from the non-IDLE precondition only. It stays
  a subclass so every existing `except TurnOrchestratorError` / `pytest.raises` keeps
  working, and the check still runs **before** any conversation/turn is created.
- `jarvis/daemon/lifecycle.py`: added `except TurnOrchestratorBusyError → 409`, placed
  before the generic `except TurnOrchestratorError → 500`.

The orchestrator was **not** rewritten, the state machine was **not** changed, and no
new runtime state was added. Genuine failures (context build, brain generation, generic)
still raise the base `TurnOrchestratorError` and still map to 500.

## Test hardening added (allowed fix A)

- `test_non_idle_runtime_returns_409_and_creates_no_turn` — HTTP-level proof of F1,
  distinct from the lock-held 409 path.
- `test_handle_text_on_non_idle_runtime_raises_busy_error_without_turn` — orchestrator
  level; asserts the subclass relationship and that no turn is created.
- `test_text_turn_lock_released_after_failure` — lock is released and runtime is `IDLE`
  after a real failure through `DaemonApp.handle_text_input`.
- `test_invalid_metadata_or_conversation_id_returns_400_and_creates_no_turn` —
  parametrised metadata/`conversation_id` validation.
- Strengthened both failure tests to assert `final_runtime_state == "IDLE"`.

No forbidden-string check and no schema/migration guard was weakened.

## Observations intentionally not fixed

- **O1 — defensive stranding.** In `_recover_runtime_after_failure`, if the
  `THINKING→ERROR` transition's own event append raises, the method returns and leaves
  the runtime in `THINKING`. This requires an event-store/DB failure *during* recovery,
  is not reachable in any obvious/tested way, and a "hard reset" fix would bypass the
  event-sourced state machine — which is explicitly out of scope. Documented for a
  future durability pass.
- **O2 — uninitialised app → 400.** The `_require_*` guards raise `DaemonAppError`
  (→ 400) if a started app somehow lacks a connection/brain/context builder. This is not
  reachable through normal wiring (`start()` already requires the event store and state
  machine), so it is left as-is.

## Verification

- `python -m compileall jarvis` → OK.
- Full required suite → **277 passed** (`test_text_turn_pipeline.py`: 28 passed).
- `jarvis.cli config show | paths show | doctor | db status` → OK
  (`doctor.config_ok = true`, `db status.db_exists = false` — no daemon/DB was started).
- `git diff --check` → clean; schema/migrations unchanged; no forbidden runtime strings.

## Recommendation for the next Codex prompt

The Jarvis-owned text turn pipeline is Gate C clean. The next increment should extend
**inputs into the same single-turn pipeline without adding a second timeline**:

- Add a `source="cli"` text-input path (CLI command → `DaemonApp.handle_text_input`)
  reusing the existing orchestrator, lock, events, and 409/503 semantics.
- Add read-only turn/conversation history endpoints (`GET /conversations`,
  `GET /turns?conversation_id=…`) backed by the existing repositories.
- Keep deferring voice/tools/workers/providers/launchd until their own gates.
