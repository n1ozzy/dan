# Task 7 Report: Persistent Voice Snapshots and Native Sole Player

## Status

`DONE_WITH_CONCERNS` candidate on `agent/dan-release1-integration`, based on clean
starting HEAD `d6e815649d98`. The core Task 7 runtime is implemented. The user stopped
the final long verification pass and explicitly requested report plus commit without a
new full baseline.

There is no known migration, snapshot, or cancellation blocker. The unresolved concerns
are verification and stale Task 8-owned API/diagnostic projections, listed below.

## Implemented

- Added schema version 5 with complete persisted intent fields, canonical
  `render_snapshot_json`, synthesis/playback timestamps, `playback_confirmed`, strict
  status transitions, immutable snapshots, and a DB-level complete snapshot guard.
- Rebuilt v4 `voice_queue` rows as `legacy-migration` / `legacy-unresolved`, kept them
  non-playable, preserved data, and made the migration idempotent.
- Reserved `legacy-unresolved` at the database boundary even if a runtime caller spoofs
  `source='legacy-migration'`.
- Added `VoiceService` as the only resolver and enqueue boundary. It resolves once,
  validates once, then commits intent and snapshot together under bounded admission.
- Added deterministic lane ordering (`live`, `normal`, `background`), priority/creation
  ordering, global/session backpressure, and one-request TTS prefetch.
- Added production resolver composition from Task 6's versioned catalog and verified
  custom-style manifest. Custom styles are frozen as explicit verified repository paths,
  with the matching snapshot SHA required by TTS.
- Made TTS synthesis snapshot-only. Supertonic warm-serve may fall back only to the
  pinned Supertonic CLI using the same snapshot.
- Added one daemon-lifetime `CoreAudioPlayer` based on one `AVAudioEngine` and one
  `AVAudioPlayerNode`; no subprocess player remains in the Task 7 playback path.
- Added schedule/stop serialization so cancellation between the DB playback-start edge
  and native schedule cannot start a late audio buffer.
- Made `VoiceBroker` the only queue consumer and the only caller of the audio player.
  Native completion is the only path to `done` and `playback_confirmed=1`.
- Recovery requeues interrupted synthesis and fails uncertain interrupted playback, so a
  restart cannot replay an audio buffer that may already have sounded.
- Cancellation is generation, queue, then playback-owner stop. It cancels queued,
  synthesizing, and speaking rows and leaves no confirmed tail.
- Removed `dan/voice/shared_broker.py`, `dan/voice/shared_voice.py`, and obsolete shared
  tests. Replaced them with removal and sole-owner architecture tests.
- Added the pinned `pyobjc-framework-AVFoundation==12.2.1` voice extra.

## TDD Evidence

### Observed RED

- Initial Task 7 snapshot/service RED: missing `QueueBackpressure` import and seven
  queue/snapshot failures for missing v5 columns and APIs.
- Player/broker RED: missing native player module and broker/player ownership contract.
- Snapshot-only TTS RED: eight failures from resolver/player ownership still living in
  TTS.
- Native cutover RED: four failures from present shared modules, duplicate call sites,
  and missing daemon-native owners.
- Cancellation owner RED: `5 failed, 4 passed`; coordinator still accepted `engine=`
  instead of `playback_owner=`.
- Explicit custom-style snapshot RED: two failures proved the resolver stored `M2M1`
  rather than an explicit path and TTS remapped through its current manifest.
- Legacy marker spoof RED: one failure proved a runtime insert could claim migration
  source and write `legacy-unresolved`.
- Cancellation race RED: one failure proved stop between `on_started` and native schedule
  still scheduled a buffer.
- Integration RED: three failures exposed an external Claude test edge and two raw test
  inserts with open SQLite transactions. A faulthandler stack confirmed the gateway was
  waiting in `ClaudeCliAdapter`, not cancellation or the voice DB.

### Observed GREEN

- Snapshot/service first phase: `13 passed`.
- Native player/broker phase: `27 passed`.
- Snapshot-only TTS combined phase: `40 passed`.
- Shared removal/native daemon cutover phase: `5 passed`.
- Task 7 focused suite at the main green checkpoint: `83 passed in 1.14s`.
- Cancellation suite after owner cutover: `9 passed`.
- Anti-echo suite after removing the shared spoken ring: `16 passed`.
- Broker survivability/restart suite: `7 passed`.
- Speech stream service-boundary suite: `11 passed`.
- Versioned catalog/custom-path and persona compatibility suite:
  `148 passed in 1.22s`.
- Latest targeted migration suite after marker hardening: `8 passed`.
- Latest targeted no-tail player/broker checks: `6 passed`.
- Latest targeted gateway/API regression rerun: `3 passed in 1.65s`.
- Task 7 changed-file Ruff check reported `All checks passed!` before the final no-tail
  lock and one streaming test adapter update.

## Commands and Results

Representative commands executed:

```text
pytest -q tests/test_voice_snapshot_queue.py tests/test_voice_service.py \
  tests/test_voice_queue.py tests/test_voice_broker.py \
  tests/test_voice_tts_supertonic.py tests/test_audio_player.py \
  tests/test_voice_cancellation.py tests/test_voice_fix04.py \
  tests/test_voice_anti_echo.py tests/test_shared_voice_broker.py \
  tests/test_shared_voice_runtime_truth.py
# 83 passed in 1.14s

$HOME/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_assets.py \
  tests/test_voice_route_matrix.py tests/test_voice_catalog.py \
  tests/test_voice_tts_supertonic.py tests/test_config.py \
  tests/test_config_registry.py tests/test_voice_resolver.py \
  tests/test_voice_persona_binding.py
# 148 passed in 1.22s

pytest -q tests/test_voice_snapshot_queue.py
# 8 passed

pytest -q tests/test_audio_player.py \
  tests/test_voice_broker.py::test_cancellation_stops_native_player_and_never_confirms_tail
# 6 passed

pytest -q \
  tests/test_voice_turn_gateway.py::test_daemon_barge_in_cancels_pending_speech_before_the_new_turn \
  tests/test_api_smoke.py::test_ptt_down_acquires_lease_without_cancelling_current_speech \
  tests/test_api_smoke.py::test_get_runtime_settings_does_not_apply_stale_barge_in_to_later_text_turn
# 3 passed in 1.65s
```

The exact broad removal scan was run and did not pass. Core Task 7 modules no longer use
the shared runtime or subprocess players, but matches remain in Task 8-owned API/runtime
diagnostics, panel/config compatibility surfaces, migration inventory, and deliberate
test-safety fixtures.

The exact `ruff check dan/voice` command was also attempted. Ruff was absent from the
machine and repository venv, so versions 0.15.22 and 0.6.9 were installed under `/tmp`
only. The whole-directory command reports pre-existing lint in untouched voice modules.
The Task 7 changed-file lint set passed.

## Verification Not Completed

Per the user's stop instruction, no new full non-live baseline was started after the
latest fixes. A previous full Task 6 baseline at this branch's parent recorded 2492
isolated tests and 270 known failures, but Task 7 has no final baseline/delta result.

The following final commands were therefore not completed after the last edits:

- cold-HOME Task 6 covering suite;
- `python -m compileall -q dan tests`;
- full `scripts/dan-test-baseline --compare ...`;
- a fresh complete Task 7 focused rerun after the final no-tail lock;
- a fresh Ruff rerun after the last two small edits.

`git diff --check` initially found one blank EOF line in
`tests/test_voice_anti_echo.py`; it was removed before commit preparation.

## Changed Files

Core production:

- `dan/store/schema.sql`, `dan/store/migrations.py`
- `dan/voice/models.py`, `resolver.py`, `service.py`, `queue.py`, `speech.py`
- `dan/voice/tts.py`, `player.py`, `broker.py`, `cancellation.py`, `anti_echo.py`
- `dan/daemon/app.py`, `pyproject.toml`
- removed `dan/voice/shared_broker.py`, `dan/voice/shared_voice.py`

Tests:

- added `tests/test_voice_snapshot_queue.py`, `tests/test_voice_service.py`,
  `tests/test_audio_player.py`, `tests/voice_helpers.py`
- rewrote Task 7 queue/broker/TTS/shared runtime tests
- updated affected cancellation, anti-echo, daemon, streaming, route-matrix, resolver,
  persona, gateway, listening, capture, and API integration tests to submit complete
  snapshots through the service contract
- removed `tests/test_shared_voice.py`

## Risks and Self-Review

1. Task 8 owns `dan/api/routes_voice.py`. It and the older runtime diagnostic projection
   still describe `external_shared` and `playback_binary`; Task 7 did not expand scope
   into those files. They must be replaced by Task 8's API contract before those
   diagnostics can be considered truthful.
2. The final full baseline/delta, cold-HOME suite, and compileall are absent because the
   user explicitly stopped the run. The latest small update in
   `tests/test_streaming_turn_speech.py` is syntactically straightforward but did not get
   its own post-edit test run.
3. AVFoundation is tested only through a fake external-edge backend, as required. No live
   audio or manual hardware test was performed. PyObjC selector behavior remains the
   principal platform integration risk.
4. The complete snapshot stores explicit custom-style paths and hashes. If a versioned
   asset is later removed from the repository, replay fails closed rather than silently
   selecting a replacement.
5. No unrelated worktree changes existed at start, and no user changes were reverted.
