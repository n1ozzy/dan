# DAN Release 1 Audit Remediation — Batch 2 Runtime and Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dand` the truthful owner of the exact TTS identity, child processes, PTT lifecycle, scheduled jobs, synthesis/playback shutdown, native playback recovery, and every installed host artifact, with deterministic runtime-tool visibility, bounded recovery, reversible installation, and accurate macOS permission reporting.

**Architecture:** One frozen Supertonic runtime identity binds executable SHA-256, semver, model, revision, manifest, child argv, resolver snapshots, and engine validation. `ChildSupervisor` owns that child plus a continuous watchdog and restart budget. `DaemonApp` owns and closes the PTT activation gate, source-scoped listening leases, scheduler, and a fail-closed broker/synthesis quiescence barrier. The one `CoreAudioPlayer` resets its native backend after a duration-bounded failure so the broker can fail one row and continue. A staged Python install plan owns every installed path, one deterministic runtime PATH, and the inverse manifest used by uninstall. Host HOME changes remain a separate deployment action.

**Tech Stack:** exact build-evidence-bound CPython runtime (currently 3.14.6, never a hard-coded py311 wheelhouse), threading/monotonic clocks, subprocess, local HTTP, SQLite-backed state, launchd plist, shell adapters, JSON manifests, macOS Accessibility/TCC probes, pytest, ruff.

## Global Constraints

- Start only after Batch 1 is GREEN. Reuse its `IntakeGate`; do not invent a second restart/intake flag.
- Exactly one `dand` owns one Supertonic serve child and one PTT monitor. Never adopt an unrelated process listening on the expected port and never fall back to a parallel TTS CLI.
- Tests inject clocks, waiters, subprocesses, permission probes, and filesystem roots. They must not touch live `launchd`, TCC, microphone, audio, or `$HOME`.
- Repository fixes do not mutate the active `~/.dan` or `~/.claude/settings.json`. Applying a generated install plan to real HOME is a separately authorized deployment.
- Plan authoring and automated Batch 2 tests do not restart the live daemon, reload launchd, probe a real audio device, or invoke the active voice broker; runtime restart/deployment remains a separately authorized action.
- Versioned release directories are immutable. Wrappers are atomically switched; do not move a built virtualenv between paths because absolute shebangs make it non-relocatable.
- Every task receives RED/GREEN evidence and two independent reviews before the next task begins.
- `dan/daemon/restart.py` coordinates restart intent and closes Batch 1's durable `IntakeGate`; `DaemonApp.stop()` owns in-process shutdown; `dan/api/routes_runtime.py` only validates/projects HTTP state; launchd alone resurrects the exited process. No layer may call `launchctl`, `pkill`, or create a second restart flag from the request path.
- `dan/install/manifest.py` owns the strict install manifest and installed-release identity schemas. `dan/install/__init__.py` owns plan/render/verify/apply/rollback orchestration, while `scripts/install.sh` and `scripts/uninstall.sh` are argument-only adapters. The shell scripts may not perform hidden filesystem mutations.
- `dan/install/preflight.py` remains the existing read-only host preflight aggregator. `dan/input/hotkey.py::accessibility_trust_state(checker=...)` remains the Accessibility primitive; preflight adds the other no-prompt probes and reports the resolved `sys.executable` that macOS actually attributes.
- Wheelhouse download/lock preparation remains exclusively Batch 4 Task 4.6. Batch 2 consumes only an injected, already verified local release artifact/wheelhouse fixture and must have no network fallback or second lock format. The Batch 4 build gate passes its verified 40-lowercase-hex source commit, 64-lowercase-hex artifact digest, and exact absolute interpreter plus implementation/version/cache-tag/SHA-256 metadata unchanged; the installer never discovers release identity from `.git`, filenames, environment defaults, `pyproject.toml`'s minimum version, or runtime status.

Run `dan_batch2_isolation` immediately before every executable RED/GREEN command block below. It creates a new HOME and external evidence/tool-cache root for that one block; fixed reusable `/private/tmp/dan-batch2-home` paths are forbidden:

```bash
dan_batch2_isolation() {
  umask 077
  DAN_BATCH2_TEST_HOME="$(mktemp -d /private/tmp/dan-batch2-home.XXXXXX)" || return 1
  DAN_BATCH2_EVIDENCE_ROOT="$(mktemp -d /private/tmp/dan-batch2-evidence.XXXXXX)" || return 1
  mkdir -p "$DAN_BATCH2_TEST_HOME/.cache" "$DAN_BATCH2_TEST_HOME/.config" \
    "$DAN_BATCH2_TEST_HOME/.local/share" "$DAN_BATCH2_EVIDENCE_ROOT/runtime"
  export DAN_BATCH2_TEST_HOME DAN_BATCH2_EVIDENCE_ROOT
  export RUFF_CACHE_DIR="$DAN_BATCH2_EVIDENCE_ROOT/ruff-cache"
}

dan_batch2_env() {
  env -u DAN_CONFIG -u VOICE_CONFIG_DIR \
    HOME="$DAN_BATCH2_TEST_HOME" XDG_CACHE_HOME="$DAN_BATCH2_TEST_HOME/.cache" \
    XDG_CONFIG_HOME="$DAN_BATCH2_TEST_HOME/.config" \
    XDG_DATA_HOME="$DAN_BATCH2_TEST_HOME/.local/share" \
    TMPDIR="$DAN_BATCH2_EVIDENCE_ROOT" \
    DAN_RUNTIME_DIR="$DAN_BATCH2_EVIDENCE_ROOT/runtime" \
    DAN_DB_PATH="$DAN_BATCH2_EVIDENCE_ROOT/dan.sqlite3" \
    DAN_RELEASE_EVIDENCE_ROOT="$DAN_BATCH2_EVIDENCE_ROOT" \
    DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 "$@"
}
```

---

## Task 2.1: Supervise the TTS child with a bounded watchdog

**Files:**

- Modify: `dan/daemon/supervisor.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/voice/assets.py`
- Modify: `dan/voice/service.py`
- Modify: `dan/voice/tts.py`
- Modify: `dan/config.py`
- Modify: `config/dan.example.toml`
- Modify: `config/voice/custom_styles/manifest.json`
- Modify: `tests/test_voice_assets.py`
- Modify: `tests/test_voice_service.py`
- Modify: `tests/test_engine_supervisor.py`
- Modify: `tests/test_voice_tts_supertonic.py`

- [ ] **Step 1: Write RED watchdog and ownership tests**

```python
def test_watchdog_restarts_dead_child_without_ensure_running(fake_clock: FakeClock) -> None:
    supervisor, child = running_supervisor(fake_clock, restart_limit=2)
    child.exit(1)
    fake_clock.advance(supervisor.poll_interval)
    assert supervisor.status(child.name).restart_count == 1
    assert supervisor.status(child.name).state == "running"


def test_restart_budget_exhaustion_marks_child_degraded(fake_clock: FakeClock) -> None:
    supervisor, child = running_supervisor(fake_clock, restart_limit=1)
    child.exit(1)
    fake_clock.advance(supervisor.poll_interval)
    supervisor.current_process(child.name).exit(1)
    fake_clock.advance(supervisor.poll_interval)
    status = supervisor.status(child.name)
    assert status.degraded is True
    assert status.restart_count == 1
```

```python
def test_path_shadow_cannot_replace_the_release_supertonic(
    installed_venv: Path,
    path_shadow: Path,
    voice_manifest: AssetManifest,
) -> None:
    identity = resolve_supertonic_runtime_identity(
        explicit_binary="",
        interpreter=installed_venv / "bin/python",
        manifest=voice_manifest,
        environ_path=str(path_shadow),
    )
    assert identity.executable == (installed_venv / "bin/supertonic").resolve()
    assert identity.executable_sha256 == sha256_file(identity.executable)


def test_snapshot_binds_the_exact_supertonic_runtime_identity(
    bound_voice_stack: BoundVoiceStack,
) -> None:
    snapshot = bound_voice_stack.resolver.resolve(bound_voice_stack.intent)
    identity = bound_voice_stack.identity
    assert snapshot.engine_version == f"{identity.semver}+{identity.model_revision}"
    assert snapshot.asset_sha256["engine.supertonic.executable"] == identity.executable_sha256
    assert snapshot.asset_sha256["engine.supertonic.custom-style-manifest"] == identity.manifest_sha256


def test_same_semver_with_wrong_binary_hash_or_revision_is_rejected(
    bound_voice_stack: BoundVoiceStack,
) -> None:
    poisoned = bound_voice_stack.snapshot_with_same_semver_and_wrong_identity()
    with pytest.raises(TTSEngineError, match="runtime identity"):
        bound_voice_stack.engine.synthesize("Nie ten runtime.", poisoned)


def test_poisoned_model_cache_or_distribution_is_rejected_before_child_start(
    bound_voice_stack: BoundVoiceStack,
) -> None:
    bound_voice_stack.replace_cached_model_file_with_same_name()
    with pytest.raises(TTSEngineError, match="model artifact"):
        bound_voice_stack.start_child()
    assert bound_voice_stack.child_spawn_count == 0

    bound_voice_stack.restore_model_cache()
    bound_voice_stack.replace_installed_supertonic_module()
    with pytest.raises(TTSEngineError, match="distribution fingerprint"):
        bound_voice_stack.start_child()
    assert bound_voice_stack.child_spawn_count == 0
```

Add `test_degraded_child_does_not_enter_respawn_loop`, `test_supertonic_serve_failure_never_invokes_parallel_cli`, `test_asset_manifest_requires_canonical_model_and_exact_runtime_file_hashes`, `test_every_required_model_file_metadata_binds_manifest_revision`, `test_supertonic_model_revision_override_is_rejected`, `test_bare_or_relative_supertonic_binary_is_rejected`, `test_configured_serve_model_must_match_manifest_model`, `test_voice_enabled_supertonic_without_supervised_route_fails_startup`, `test_supervised_child_argv_and_environment_use_bound_identity`, `test_health_2xx_from_wrong_pid_or_argv_is_not_ready`, `test_snapshot_revision_suffix_mismatch_is_rejected`, `test_concurrent_watchdog_and_ensure_running_create_one_pid`, `test_stop_watchdog_joins_before_child_killpg`, `test_deliberate_stop_never_respawns`, and preserve the existing foreign-port-owner rejection test. Replace the old test which deliberately accepts an arbitrary `+revision` suffix; exact revision equality is now required.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_engine_supervisor.py tests/test_voice_assets.py \
  tests/test_voice_service.py tests/test_voice_tts_supertonic.py
```

Expected: no autonomous restart and current CLI fallback observation; a PATH-shadow binary with matching semver is accepted, `AssetManifest` drops the declared model, and revision suffixes are not verified.

- [ ] **Step 3: Implement status and watchdog lifecycle**

```python
@dataclass(frozen=True)
class ChildStatus:
    name: str
    state: Literal["stopped", "starting", "running", "degraded"]
    pid: int | None
    restart_count: int
    restart_limit: int
    last_exit_code: int | None
    last_error: str | None
    degraded: bool


class ChildSupervisor:
    def start_watchdog(self) -> None: ...
    def stop_watchdog(self) -> None: ...
    def status(self, name: str) -> ChildStatus: ...
```

Use an injected monotonic clock and interruptible waiter. Count only restarts after an unexpected exit; a deliberate stop does not consume budget. When exhausted, set degraded and stop spawning until an explicit daemon lifecycle restart.

Serialize register, ensure, poll/reap, restart-budget mutation, watchdog start/stop, and `stop_all()` behind one lifecycle lock and explicit `running/stopping/stopped` supervisor state. `stop_watchdog()` signals the injected waiter and joins the watchdog before any child process-group termination; once stopping begins, neither the watchdog nor a concurrent `ensure_running()` may spawn. `stop_all()` kills each owned process group, waits for every child PID to be reaped, verifies the owned listener is gone, and returns a typed containment result. Concurrent watchdog/manual ensure can produce exactly one live PID.

Define one immutable `SupertonicRuntimeIdentity` during daemon voice-stack construction and inject that same object into the child specification, `SupertonicEngine`, and `build_voice_resolver()`; none of those consumers may resolve or infer identity again. It contains the resolved executable, executable SHA-256, exact probed semver, a strict installed-distribution fingerprint, canonical model from `config/voice/custom_styles/manifest.json`, the exact runtime model argument derived from that canonical model, model revision, exact model-cache root, canonical model-tree hash, and manifest SHA-256. `AssetManifest` must parse and retain its existing non-empty `model` field and a new exact mapping of required runtime model files to SHA-256. The configured serve model must equal the manifest-derived runtime model or startup fails.

The executable resolver accepts only an explicit absolute executable or `Path(sys.executable).resolve().parent / "supertonic"`; bare names, relative paths, ambient PATH lookup, and a same-semver shadow binary are rejected. Resolve symlinks once, hash the resulting regular executable, run its bounded `version` probe once, and freeze the result before starting the child or admitting speech. Locate the `supertonic` distribution only under that same interpreter/venv, strictly verify every file and size/hash declared by its installed `RECORD`, and hash the canonical verified record; the console-script hash alone is insufficient because it imports site-packages. The resolver adds the executable, distribution fingerprint, verified model tree, and custom-style manifest as engine assets, so every immutable `RenderSnapshot` carries their hashes. Its `engine_version` is exactly `<semver>+<model_revision>`; `SupertonicEngine` compares the complete string plus all identity hashes and never strips or ignores the revision suffix.

Extend the existing voice manifest rather than create a second model authority. It lists the exact runtime files Supertonic loads (`config.json`, required ONNX/config/index files, and required base voice styles) with SHA-256 and retains the canonical Hugging Face commit. At startup, require the package's own pinned repo/revision for `supertonic-3` to equal that manifest, reject `SUPERTONIC_MODEL_REVISION` and arbitrary `SUPERTONIC_CACHE_DIR` overrides, validate every required cache file plus its Hugging Face metadata commit, reject missing/extra runtime files inside the locked model set, and compute one canonical model-tree hash. Supply the validated absolute cache root explicitly to the child. A poisoned cache with the same model name, semver, or metadata string fails before spawn. Automated tests build tiny fake distribution/cache trees; they do not hash or load the live model.

`ChildSupervisor` launches only `identity.executable` with the manifest-derived model argument and the sanitized, identity-bound cache environment. A health HTTP 2xx is necessary but never sufficient: readiness also requires the owned live child PID, the frozen executable/argv/environment, validated distribution/model tree, and port ownership to match the child specification. A foreign process or identity mismatch is degraded/fail-closed and cannot admit queue work. Do not invent a model claim in the upstream health response if Supertonic does not expose one; prove the loaded inputs from the exact owned argv/environment, installed package revision pin, cache metadata commits, and content hashes.

`SupertonicEngine.synthesize()` must require that supervised serve identity. Remove `_synth_cli` fallback from the active path and reject a foreign port owner without adopting or killing it. Identity creation or validation failure aborts daemon voice startup before `VoiceService` exists; a queued snapshot whose complete identity differs is marked failed and never rendered.

Make the canonical defaults operational rather than silently selecting a nonexistent external route: voice-enabled Supertonic defaults to loopback `http://127.0.0.1:7788`, `supertonic_serve_autostart=true`, and the manifest-derived `supertonic-3` model in both `dan/config.py` and `config/dan.example.toml`. An explicit empty/non-loopback route, autostart false, model mismatch, or invalid identity fails voice startup loudly; it never falls back to CLI or claims readiness.

- [ ] **Step 4: Verify GREEN and shutdown ordering**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_engine_supervisor.py tests/test_voice_assets.py \
  tests/test_voice_service.py tests/test_voice_tts_supertonic.py
.venv/bin/ruff check dan/daemon/supervisor.py dan/daemon/app.py dan/config.py \
  dan/voice/assets.py dan/voice/service.py dan/voice/tts.py tests/test_voice_assets.py \
  tests/test_voice_service.py tests/test_engine_supervisor.py tests/test_voice_tts_supertonic.py
git diff --check
```

## Task 2.2: Own and cancel the PTT activation grace timer

**Files:**

- Modify: `dan/input/hotkey.py`
- Modify: `dan/daemon/app.py`
- Modify: `tests/test_daemon_hotkey.py`
- Modify: `tests/test_voice_listening.py`
- Modify: `tests/test_engine_supervisor.py`

- [ ] **Step 1: Write RED timer-generation tests**

```python
def test_second_down_cancels_previous_grace_timer(gate: PttActivationGate) -> None:
    first = gate.down(source="global_hotkey")
    second = gate.down(source="global_hotkey")
    first.fire()
    assert gate.active_generation == second.generation
    assert gate.arm_count == 0


def test_pending_grace_timer_cannot_arm_after_shutdown(app: DaemonApp) -> None:
    timer = app.ptt_activation_gate.down(source="global_hotkey")
    app.stop()
    timer.fire()
    assert app.listening_leases.active() == ()
```

Add the equivalent restart test.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_daemon_hotkey.py tests/test_voice_listening.py tests/test_engine_supervisor.py
```

Expected: stale first-timer callback and post-stop/post-restart timer tests fail because the current gate does not own a closeable generation.

- [ ] **Step 3: Implement explicit ownership**

```python
class PttActivationGate:
    def cancel(self) -> None: ...
    def close(self) -> None: ...
```

Each scheduled callback captures a monotonically increasing generation and exits when stale or closed. `DaemonApp` stores `ptt_activation_gate`, closes it before stopping the monitor/children, and creates a new instance only during a clean start.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_daemon_hotkey.py tests/test_voice_listening.py tests/test_engine_supervisor.py
.venv/bin/ruff check dan/input/hotkey.py dan/daemon/app.py tests/test_daemon_hotkey.py
git diff --check
```

## Task 2.3: Release listening leases by source

**Files:**

- Modify: `dan/voice/listening.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/api/routes_voice.py`
- Modify: `tests/test_listening_leases.py`
- Modify: `tests/test_voice_listening.py`
- Modify: `tests/test_api_smoke.py` (real HTTP test using its existing `running_server` and `request_json` helpers)

- [ ] **Step 1: Write RED source-isolation tests**

```python
def test_hotkey_up_cannot_release_panel_hold(manager: ListeningLeaseManager) -> None:
    manager.acquire(mode="hold", source="panel")
    manager.acquire(mode="hold", source="global_hotkey")
    manager.release(mode="hold", source="global_hotkey")
    assert manager.active_sources(mode="hold") == ("panel",)


def test_ptt_up_requires_allowed_source(tmp_path: Path) -> None:
    app, _ = build_voice_app(tmp_path)
    try:
        with running_server(app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/voice/ptt/up",
                {"source": "unknown"},
            )
        assert status == 400
        assert payload["status"] == 400
        assert "Unknown listening source" in str(payload["error"])
    finally:
        app.close()
```

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_listening_leases.py tests/test_voice_listening.py tests/test_api_smoke.py
```

Expected: source-isolation and invalid-up-source assertions fail against the current release-without-source behavior.

- [ ] **Step 3: Change the release signature and route validation**

Target signature:

```python
def release(self, *, mode: str, source: str) -> tuple[ListeningLease, ...]:
    ...
```

`post_ptt_up()` validates the same source allowlist as down. The global hotkey always uses `global_hotkey`; the panel uses its own explicit source.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_listening_leases.py tests/test_voice_listening.py tests/test_api_smoke.py
.venv/bin/ruff check dan/voice/listening.py dan/api/routes_voice.py \
  tests/test_listening_leases.py tests/test_voice_listening.py tests/test_api_smoke.py
git diff --check
```

## Task 2.4: Wire the scheduler and close durable intake before restart drain

**Files:**

- Modify: `dan/jobs/scheduler.py`
- Modify: `dan/jobs/standup.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/daemon/restart.py`
- Modify: `dan/voice/broker.py`
- Modify: `dan/paths.py`
- Modify: `dan/api/routes_runtime.py`
- Modify: `tests/test_voice_broker.py`
- Modify: `tests/test_dan_lifecycle.py`
- Modify: `tests/test_jobs_scheduler.py`
- Modify: `tests/test_engine_supervisor.py`
- Modify: `tests/test_api_smoke.py`

- [ ] **Step 1: Write RED lifecycle tests**

```python
def test_daemon_constructs_starts_and_snapshots_standup_scheduler(app: DaemonApp) -> None:
    app.start()
    status = app.snapshot_state()["jobs"]
    assert status["scheduler_state"] == "running"
    assert status["jobs"]["standup"]["registered"] is True


def test_restart_closes_durable_intake_before_scheduler_children_and_exit(
    restart_fixture: RestartFixture,
) -> None:
    coordinator = RestartCoordinator(
        restart_fixture.app,
        exit_fn=restart_fixture.exit,
        sleep=lambda _: None,
        operation_id_factory=lambda: "restart-01",
    )
    coordinator.request_restart(reason="test", synchronous=True)
    assert restart_fixture.order.index("intake.close:restart-01") < restart_fixture.order.index("scheduler.stop")
    assert restart_fixture.order.index("scheduler.stop") < restart_fixture.order.index("children.stop")
    assert restart_fixture.order[-1] == "exit:86"


def test_failed_drain_contains_children_before_exit_86(
    restart_fixture: RestartFixture,
) -> None:
    restart_fixture.app.stop_error = RuntimeError("drain failed")
    coordinator = restart_fixture.coordinator()
    coordinator.request_restart(reason="test", synchronous=True)
    assert restart_fixture.intake.snapshot().closed is True
    assert restart_fixture.order.index("watchdog.join") < restart_fixture.order.index("children.killpg")
    assert restart_fixture.order.index("children.reaped") < restart_fixture.order.index("exit:86")
    assert restart_fixture.supervised_child_pids() == ()
    assert restart_fixture.exits == [RESTART_EXIT_CODE]
```

```python
def test_stop_with_live_prefetch_preserves_owner_and_refuses_second_executor(
    blocked_prefetch_broker: BlockedPrefetchBroker,
) -> None:
    blocked_prefetch_broker.start_and_wait_for_prefetch()
    with pytest.raises(VoiceBrokerShutdownTimeout):
        blocked_prefetch_broker.broker.stop(join_timeout=0.025)
    with pytest.raises(VoiceBrokerOwnershipError):
        blocked_prefetch_broker.broker.start()
    assert blocked_prefetch_broker.engine.max_concurrent_synthesis == 1


def test_daemon_does_not_drop_broker_or_close_engine_after_failed_quiesce(
    app_with_blocked_synthesis: DaemonApp,
) -> None:
    broker = app_with_blocked_synthesis.voice_broker
    engine = app_with_blocked_synthesis.voice_engine
    with pytest.raises(VoiceBrokerShutdownTimeout):
        app_with_blocked_synthesis.stop()
    assert app_with_blocked_synthesis.voice_broker is broker
    assert app_with_blocked_synthesis.voice_engine is engine
    assert engine.close_calls == 0
    with pytest.raises(DaemonLifecycleError, match="voice owner"):
        app_with_blocked_synthesis.start()


def test_scheduler_tick_racing_intake_close_cannot_enqueue(
    scheduler_intake_race: SchedulerIntakeRace,
) -> None:
    admitted = scheduler_intake_race.start_admitted_tick()
    scheduler_intake_race.close_intake()
    rejected = scheduler_intake_race.start_tick_after_close()
    scheduler_intake_race.stop_and_join()
    assert admitted.finished is True
    assert rejected.submitted is False
    assert scheduler_intake_race.submissions_after_close == 0
```

`RestartFixture.order` is an injected recording fixture, not a new production `DaemonApp.lifecycle_trace` API. Also add `test_restart_refuses_to_drain_or_exit_when_durable_intake_close_fails`, `test_failed_child_reap_blocks_exit_86`, `test_duplicate_restart_reuses_operation_id_and_starts_one_drain`, `test_scheduler_never_submits_after_stop` using an injected waiter, `test_scheduler_stop_joins_admitted_tick`, `test_blocked_current_synthesis_and_prefetch_never_overlap_after_stop_start`, `test_quiescent_broker_stop_joins_executor_before_restart`, `test_engine_close_occurs_only_after_synthesis_quiesces`, and an API smoke assertion that `get_runtime_settings(app)["runtime"]` projects the effective `intake`, `restart`, `jobs`, and `children` values from `app.snapshot_state()`.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_jobs_scheduler.py tests/test_engine_supervisor.py tests/test_api_smoke.py \
  tests/test_voice_broker.py tests/test_dan_lifecycle.py
```

Expected: scheduler lifecycle/projection assertions fail; restart ordering fails because the current coordinator waits and calls `app.stop()` without first closing Batch 1's durable intake. The blocked-prefetch fixture also proves `ThreadPoolExecutor.shutdown(wait=False)` can leave old synthesis alive while `start()` creates a new executor, and `DaemonApp.stop()` discards the only broker reference despite failed quiescence.

- [ ] **Step 3: Implement the owned runner and restart coordinator ordering**

```python
class JobScheduler:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> SchedulerStatus: ...
```

Create `JobScheduler` after storage, Batch 1's `IntakeGate`, and `VoiceService` are ready. Every scheduled submission acquires an intake admission lease before invoking the brain or `VoiceService`; the scheduler has no bypass path. A tick admitted before durable close may finish, while a tick linearized after close is rejected without generation or enqueue. `JobScheduler.stop()` prevents new admission and joins its in-flight admitted tick before returning. Resolve the `StandupJob` text provider through the existing brain/conversation path, not a second model/provider. `DaemonApp.snapshot_state()` is the daemon-owned source for jobs, intake, restart, and child status; `_runtime_projection(app)` in `dan/api/routes_runtime.py` renders those effective values for the existing System/runtime surface and creates no session, process, or optimistic state.

Make broker shutdown a bounded ownership barrier, not a fire-and-forget executor shutdown. Track direct and prefetched synthesis under one condition plus explicit futures. `VoiceBroker.stop()` sets the stop flag, stops playback, cancels futures which have not started, joins the broker thread, and waits within the same injected monotonic deadline for every running synthesis to leave the engine. It returns only after the broker thread and synthesis executor are quiescent. If the deadline expires, raise `VoiceBrokerShutdownTimeout`, retain the thread/executor/future references and owner state, and make every later `start()` fail with `VoiceBrokerOwnershipError` until a subsequent stop proves quiescence. Never create a replacement executor merely because the broker loop thread ended while a prefetched future still runs.

`DaemonApp.stop()` clears `voice_broker`, closes `voice_engine`, stops the supervised TTS child, and permits an in-process `start()` only after that ownership barrier succeeds. On broker timeout it retains broker/engine/player references and does not call `engine.close()`, but it must enter emergency child containment: stop and join the watchdog, kill every supervised process group, reap every child PID, verify owned listeners are gone, and retry broker quiescence. It does not emit `daemon.stopped` or permit direct restart while any ownership proof is incomplete. Killing the supervised child is allowed here because it releases a synthesis blocked on that child; closing the in-process engine while its worker remains alive is not.

The external `RestartCoordinator` performs exit `86` after a failed normal drain only when the typed containment result proves watchdog joined, all supervised children reaped, and no old listener owner remains. If containment or reap cannot be proven, it keeps intake durably closed, records failed shutdown, and does not exit—launchd must never be invited to start a replacement beside an escaped child. Tests use blocked fake synthesis/children and never touch TTS or audio.

`RestartCoordinator.request_restart()` performs this exact sequence:

1. Under its existing once-only lock, set `operation_id = operation_id_factory()` and synchronously call Batch 1's `app.intake_gate.close(operation_id=operation_id, reason=reason)`. Verify the returned durable snapshot is closed before returning an accepted response or starting the drain thread. If this close fails, clear the tentative restart state, raise to `post_runtime_restart()`, and do not drain or exit.
2. Return exactly `{"ok": True, "restarting": True, "already_restarting": already_restarting, "operation_id": operation_id, "exit_code": RESTART_EXIT_CODE}` and, after the injected response-flush delay, call only `app.stop(reason=reason)`. A duplicate request returns the same operation ID with `already_restarting=True` and never closes, drains, or spawns twice. Do not return or persist the free-form reason.
3. `DaemonApp.stop()` stops the scheduler first, then drains/cancels in-flight voice work through the existing gateway/service owners and proves the broker/synthesis ownership barrier, then stops supervised children and hotkey, then brain/workers/storage. It never reopens intake and never closes an engine still used by an unjoined synthesis worker.
4. After `app.stop()` returns, call `exit_fn(RESTART_EXIT_CODE)`. If it raises after intake was durably closed, first run the emergency containment contract above and call exit only when its typed result proves every supervised child/process group reaped and listener released; otherwise remain stopped with intake closed. launchd is the only resurrection owner. `DaemonApp.start()` is the only path allowed to reopen the same `IntakeGate`, and only after a complete successful startup.

`post_runtime_restart(app, request_payload)` remains a thin validator/delegator in `dan/api/routes_runtime.py`; it does not close intake itself and never calls `launchctl` or `pkill`. The existing public `RestartCoordinator(app, *, exit_fn, sleep, flush_seconds)` interface is preserved and adds only injectable `operation_id_factory`. Its new `snapshot()` returns exactly `{"restarting": bool, "operation_id": str | None, "intake_closed": bool, "intake_operation_id": str | None}` from coordinator state plus the durable intake snapshot. `DaemonApp.snapshot_state()` embeds that value at `restart`, and the route only projects it. Neither response nor snapshot contains reason text.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_jobs_scheduler.py tests/test_engine_supervisor.py tests/test_api_smoke.py \
  tests/test_voice_broker.py tests/test_dan_lifecycle.py
.venv/bin/ruff check dan/jobs dan/daemon/app.py dan/daemon/restart.py dan/voice/broker.py \
  dan/paths.py dan/api/routes_runtime.py tests/test_jobs_scheduler.py \
  tests/test_engine_supervisor.py tests/test_api_smoke.py tests/test_voice_broker.py \
  tests/test_dan_lifecycle.py
git diff --check
```

Expected: tests prove the durable intake close precedes scheduler/drain/children/exit, a close failure prevents shutdown, a later drain failure leaves intake closed, and no production lifecycle-trace or second restart flag was introduced. A timed-out synthesis retains the sole owner and blocks in-process restart; a normal stop joins every synthesis worker before engine/child teardown and allows exactly one later broker owner.

## Task 2.5: Manifest every staged install artifact

Confirmed live blockers: the installed wrapper does not establish PATH, launchd supplies only `/usr/bin:/bin:/usr/sbin:/sbin`, and the required binary is currently at `$HOME/.homebrew/bin/ffmpeg`; `mlx-whisper` therefore fails with `Errno 2`. Separately, both repository and production venvs use CPython 3.14.6 (`sys.implementation.cache_tag == "cpython-314"`) and no `python3.11` exists, so a macos-arm64-py311 assumption is false. This task fixes the installed contract from build evidence, not the live host in place.

**Files:**

- Create: `dan/install/manifest.py`
- Create: `dan/install/runtime_env.py`
- Modify: `dan/install/__init__.py`
- Modify: `dan/install/__main__.py`
- Modify: `dan/install/adapters.py`
- Modify: `dan/install/launchd.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/api/routes_runtime.py`
- Modify: `scripts/install.sh`
- Delete: `scripts/dand`
- Delete: `scripts/install-launchd.sh`
- Modify: `launchd/com.dan.dand.plist.example`
- Modify: `config/dan.example.toml`
- Modify: `tests/test_installer_atomicity.py`
- Modify: `tests/test_launchd_single_owner.py`
- Modify: `tests/test_launchd_assets.py`
- Modify: `tests/test_scaffold_contracts.py`
- Modify: `tests/test_api_smoke.py`

- [ ] **Step 1: Write RED completeness and rollback tests**

```python
def test_manifest_targets_equal_planned_and_applied_targets(install_fixture: InstallFixture) -> None:
    planned = install_fixture.plan.planned_targets()
    report = install_fixture.plan.apply(backup_root=install_fixture.backup_root)
    manifest = load_install_manifest(Path(report.manifest_path), home=install_fixture.home)
    assert planned == report.applied_targets() == manifest.target_paths()
    assert len(planned) == len(set(planned))
    assert CURRENT_RELEASE_RELPATH in planned
    assert install_fixture.plan.rendered_tree_members() == report.applied_tree_members()
    assert report.applied_tree_members() == manifest.release_tree_members()


def test_partial_apply_failure_rolls_back_prior_artifacts(tmp_path: Path) -> None:
    plan = failing_plan_after_third_write(tmp_path)
    before = tree_digest(plan.target_root)
    with pytest.raises(InstallApplyError):
        plan.apply()
    assert tree_digest(plan.target_root) == before


def test_apply_blocks_when_parent_becomes_symlink_after_preflight(
    install_fixture: InstallFixture,
) -> None:
    install_fixture.swap_parent_for_symlink_after_preflight()
    with pytest.raises(InstallTreeDrift):
        install_fixture.plan.apply(backup_root=install_fixture.backup_root)
    assert install_fixture.outside_tree_digest_unchanged()


def test_runtime_startup_projects_loaded_current_release_identity(
    installed_app: DaemonApp,
) -> None:
    payload = get_runtime_startup(installed_app)
    assert payload["release"]["commit_sha"] == installed_app.runtime_release_identity.commit_sha
    assert payload["release"]["artifact_sha256"] == installed_app.runtime_release_identity.artifact_sha256
    assert payload["release"]["installed_at_utc"] == (
        installed_app.runtime_release_identity.installed_at_utc
    )
    assert payload["release"]["install_manifest_sha256"] == sha256_file(
        Path(installed_app.install_manifest_path)
    )


def test_installer_requires_explicit_verified_release_provenance(
    install_cli_fixture: InstallCliFixture,
) -> None:
    for missing in ("commit_sha", "artifact_sha256"):
        result = install_cli_fixture.run_without(missing)
        assert result.returncode != 0
        assert install_cli_fixture.home_unchanged()
    result = install_cli_fixture.run(
        commit_sha="a" * 40,
        artifact_sha256=install_cli_fixture.actual_artifact_sha256,
    )
    assert result.returncode == 0
    assert install_cli_fixture.manifest.release.commit_sha == "a" * 40
    assert (
        install_cli_fixture.manifest.release.artifact_sha256
        == install_cli_fixture.actual_artifact_sha256
    )
    assert install_cli_fixture.current_release.commit_sha == "a" * 40
    assert (
        install_cli_fixture.current_release.artifact_sha256
        == install_cli_fixture.actual_artifact_sha256
    )


def test_installer_rejects_git_derived_or_mismatched_provenance(
    install_cli_fixture: InstallCliFixture,
) -> None:
    install_cli_fixture.checkout_git_head = "b" * 40
    result = install_cli_fixture.run(commit_sha="not-a-40-hex", artifact_sha256="0" * 64)
    assert result.returncode != 0
    assert install_cli_fixture.git_was_not_invoked()
    assert install_cli_fixture.home_unchanged()


def test_installer_binds_exact_build_interpreter_identity(
    install_cli_fixture: InstallCliFixture,
) -> None:
    expected = install_cli_fixture.verified_build_interpreter
    result = install_cli_fixture.run(python_identity=expected)
    assert result.returncode == 0
    assert install_cli_fixture.manifest.release.python == expected
    assert install_cli_fixture.current_release.python == expected
    assert install_cli_fixture.runtime_startup["release"]["python"] == {
        "implementation": expected.implementation,
        "version": expected.version,
        "tag": expected.tag,
        "sha256": expected.sha256,
    }


@pytest.mark.parametrize(
    "field",
    ("executable", "implementation", "version", "tag", "sha256"),
)
def test_installer_rejects_each_interpreter_mismatch_before_mutation(
    install_cli_fixture: InstallCliFixture,
    field: str,
) -> None:
    supplied = install_cli_fixture.verified_build_interpreter.with_mismatch(field)
    result = install_cli_fixture.run(python_identity=supplied)
    assert result.returncode != 0
    assert install_cli_fixture.home_unchanged()


def test_wrapper_and_launchd_share_the_deterministic_runtime_path(
    install_fixture: InstallFixture,
) -> None:
    install_fixture.plan.render(install_fixture.staging)
    expected = ":".join(
        (
            f"{install_fixture.home}/.dan/bin",
            f"{install_fixture.home}/.homebrew/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
    )
    assert rendered_wrapper_path(install_fixture.staging) == expected
    assert rendered_launchd_path(install_fixture.staging) == expected


def test_install_preflight_fails_closed_when_ffmpeg_is_not_verified(
    install_fixture: InstallFixture,
) -> None:
    install_fixture.ffmpeg_probe.result = RuntimeExecutableState.MISSING
    report = install_fixture.plan.preflight()
    assert report.check("ffmpeg_visible").ok is False
    assert report.ok is False
    assert install_fixture.ffmpeg_probe.paths == [install_fixture.plan.runtime_path]
```

Add exact tests named `test_manifest_declares_current_release_without_content_hash`, `test_current_release_binds_final_manifest_hash`, `test_current_release_is_fsynced_after_manifest`, `test_current_release_parent_fsync_failure_restores_prior_control_records`, `test_unprovable_control_record_durability_blocks_launchd_restart`, `test_install_cli_rejects_missing_or_noncanonical_commit_sha`, `test_install_cli_rejects_missing_or_mismatched_artifact_sha256`, `test_install_cli_rejects_missing_python_metadata`, `test_installer_accepts_cpython_3146_without_py311_assumption`, `test_installed_venv_python_resolves_to_verified_interpreter`, `test_install_wrapper_forwards_verified_provenance_unchanged`, `test_install_wrapper_forwards_python_metadata_unchanged`, `test_ffmpeg_probe_ignores_ambient_path_and_never_calls_git`, and `test_ffmpeg_probe_accepts_executable_from_each_declared_homebrew_directory`, plus strict-parser tests for unknown keys, duplicate/traversing paths, malformed SHA-256, malformed interpreter version/tag/path, illegal type/action pairs, manifest-target mismatch, a missing/duplicate `current-release.json` target, wrong-mode rejection, stage-only no-target-mutation, invalid-present installed identity or actual-runtime-interpreter mismatch failing daemon startup, and missing identity projecting `status="unknown"` without inventing the checkout HEAD or Python version.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py \
  tests/test_launchd_assets.py tests/test_api_smoke.py
```

Expected: target-set, installed-identity, strict-parser, parent-swap, deterministic-PATH, and fail-closed ffmpeg checks fail against the current `InstallEntry`/`InstallReport` model, wrapper/plist rendering, and `/runtime/startup` payload.

- [ ] **Step 3: Implement staged release ownership**

The Python plan must own all writes. Preserve the current `python -m dan.install --stage-only|--apply` mode interface and add required `--python ABSOLUTE_PATH`, `--python-implementation cpython`, `--python-version MAJOR.MINOR.MICRO`, `--python-tag CACHE_TAG`, `--python-sha256 SHA256_64_LOWER_HEX`, `--home ABSOLUTE_PATH`, `--artifact ABSOLUTE_PATH`, `--commit-sha COMMIT_SHA_40_LOWER_HEX`, `--artifact-sha256 SHA256_64_LOWER_HEX`, and `--wheelhouse ABSOLUTE_PATH` inputs. `scripts/install.sh` parses only those explicit arguments and `--no-launchd`, then `exec`s the supplied interpreter with `-m dan.install --apply` (or `--stage-only`) and every value byte-for-byte unchanged. It has no discovery/default path and may not pre-create a venv, wrapper, config, hook, directory, backup, manifest, or identity. Before any HOME mutation, the Python CLI requires `--python` to be an absolute normalized path with no `.`/`..`, verifies it equals the non-resolved absolute `sys.executable` (preserving the selected venv rather than collapsing it to the base interpreter), resolves its target strictly only for regular-file/executable checks and byte hashing, and requires exact equality with the supplied implementation, `platform.python_version()`, `sys.implementation.cache_tag`, and SHA-256. It requires the explicit commit SHA to match `[0-9a-f]{40}`, the artifact digest to match `[0-9a-f]{64}`, recomputes the artifact SHA-256, and rejects every mismatch. It must not run git or derive provenance from the checkout, tag, artifact/wheel filename, ambient environment, `pyproject.toml`'s Python floor, installed files, or runtime fields. Only `installed_at_utc` and `install_id` are generated locally, through injected clock/ID factories; all build-supplied values plus those generated values construct `InstallReleaseInput`. It builds the venv directly at its final versioned destination using this exact selected interpreter and only `pip --no-index --find-links WHEELHOUSE ARTIFACT`; it never moves a venv. After creation, it requires the final venv interpreter path to be a manifest-owned member of the immutable release tree and to reproduce the same implementation/version/tag/resolved-binary-SHA record; its lexical path is expected to differ from the build interpreter. Batch 2 tests inject a local fixture artifact/wheelhouse and subprocess recorder. Batch 2 performs no download and has no editable-install, Python-version guess, or network fallback. Batch 4 Task 4.6 alone creates `scripts/dan-wheelhouse-prepare` and the lock, verifies the hash-locked wheelhouse/artifact, reads the build report's verified `subject_sha` and interpreter record, and calls this unchanged install transaction with every field. Batch 4 must not infer a different commit or Python from filenames, substitute identity fields, or define a second manifest/interpreter/identity schema.

Delete the competing repo wrapper `scripts/dand` and mutating `scripts/install-launchd.sh`; they cannot safely forward the mandatory artifact, commit, interpreter, and wheelhouse evidence and therefore are not retained as compatibility installers. `scripts/install.sh` plus `python -m dan.install` is the only install entry. Rewrite `tests/test_launchd_assets.py` and `tests/test_scaffold_contracts.py` to assert the Python-rendered wrapper/plist contract and absence of legacy mutators instead of treating those scripts as product assets. Task 2.6 removes the matching legacy uninstaller and updates operator documentation after the inverse path exists.

`dan/install/runtime_env.py` is the sole runtime-PATH source. It renders exactly `$HOME/.dan/bin:$HOME/.homebrew/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`, with `$HOME` replaced by the validated absolute install HOME and with no ambient `PATH` append. The staged `dan`/`dand` wrappers export that exact value before executing their absolute release interpreter; `dan/install/launchd.py` substitutes it into the plist's sole `__RUNTIME_PATH__` placeholder as `EnvironmentVariables.PATH`. Both rendered bytes are normal manifest-owned, hashed artifacts, so wrapper/plist drift fails verify/apply. The template contains no second hard-coded path list and verify rejects an unsubstituted placeholder.

The same module exposes `probe_runtime_executable("ffmpeg", home=home, runner=runner)`. It searches only the ordered rendered PATH, never calls `git` or a shell, resolves the first candidate strictly, requires an executable regular file after resolution, and invokes `[resolved_ffmpeg, "-version"]` with the deterministic PATH, explicit HOME, closed stdin, captured output, and a five-second timeout through an injected runner. Missing, non-executable, non-regular, resolution error, timeout, signal, or non-zero exit is a failed `ffmpeg_visible` install preflight check; no later path or ambient executable silently rescues an invalid first candidate. Tests inject the runner and fake executable tree, so no real ffmpeg, playback, microphone, or CoreAudio process runs. `InstallPlan.preflight()`, the Task 2.8 host report, and `dan doctor` expose the same check and resolved path; `installable` is false unless it is verified.

```python
# dan/install/runtime_env.py
RUNTIME_PATH_COMPONENTS = (
    ".dan/bin",
    ".homebrew/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


class RuntimeExecutableState(StrEnum):
    VERIFIED = "verified"
    MISSING = "missing"
    INVALID = "invalid"
    ERROR = "error"


@dataclass(frozen=True)
class RuntimeExecutableCheck:
    name: str
    state: RuntimeExecutableState
    runtime_path: str
    resolved_path: str | None
    detail: str

    @property
    def ok(self) -> bool: ...


class RuntimeExecutableProbe(Protocol):
    def __call__(
        self,
        name: Literal["ffmpeg"],
        *,
        home: Path,
    ) -> RuntimeExecutableCheck: ...


def runtime_path_for_home(home: Path) -> str: ...
def probe_runtime_executable(
    name: Literal["ffmpeg"],
    *,
    home: Path,
    runner: SubprocessRunner = subprocess.run,
) -> RuntimeExecutableCheck: ...
```

`dan/install/manifest.py` owns these exact public interfaces; `dan/install/__init__.py` re-exports them for compatibility rather than defining a second schema:

```python
INSTALL_MANIFEST_RELPATH = ".dan/install-manifest.json"
CURRENT_RELEASE_RELPATH = ".dan/current-release.json"


@dataclass(frozen=True)
class PathFingerprint:
    kind: Literal["absent", "regular", "symlink", "directory"]
    sha256: str | None
    mode: int | None


@dataclass(frozen=True)
class TreeMember:
    relpath: str
    kind: Literal["regular", "symlink", "directory"]
    sha256: str | None
    mode: int


@dataclass(frozen=True)
class InstallEntry:
    target_relpath: str
    kind: Literal[
        "release_tree", "venv", "wrapper", "config", "hook", "adapter", "plist", "managed_doc", "current_release"
    ]
    staged_source: str
    before: PathFingerprint
    after: PathFingerprint
    backup_relpath: str | None
    operation: Literal["create", "replace"]
    inverse: Literal["remove", "restore_backup"]
    tree_members: tuple[TreeMember, ...] = ()


@dataclass(frozen=True)
class ReleaseInterpreterInput:
    executable: str
    implementation: Literal["cpython"]
    version: str
    tag: str
    sha256: str


@dataclass(frozen=True)
class InstallReleaseInput:
    schema_version: Literal[1]
    commit_sha: str
    artifact_sha256: str
    python: ReleaseInterpreterInput
    installed_at_utc: str
    install_id: str


@dataclass(frozen=True)
class RuntimeReleaseIdentity:
    schema_version: Literal[1]
    commit_sha: str
    artifact_sha256: str
    python: ReleaseInterpreterInput
    install_manifest_sha256: str
    installed_at_utc: str
    install_id: str

    def canonical_sha256(self) -> str: ...


@dataclass(frozen=True)
class InstallManifest:
    schema_version: Literal[2]
    home: str
    backup_root: str
    planned_targets: tuple[str, ...]
    entries: tuple[InstallEntry, ...]
    release: InstallReleaseInput

    def target_paths(self) -> tuple[str, ...]: ...
    def release_tree_members(self) -> tuple[TreeMember, ...]: ...


class InstallPlan:
    def __init__(
        self,
        home: Path,
        *,
        release: InstallReleaseInput,
        artifact: Path,
        wheelhouse: Path,
        include_launchd: bool = True,
        runtime_executable_probe: RuntimeExecutableProbe = probe_runtime_executable,
    ) -> None: ...

    def planned_targets(self) -> tuple[str, ...]: ...
    def rendered_tree_members(self) -> tuple[TreeMember, ...]: ...
    def apply(self, backup_root: Path) -> InstallReport: ...


class InstallReport:
    manifest_path: str
    manifest_sha256: str

    def applied_targets(self) -> tuple[str, ...]: ...
    def applied_tree_members(self) -> tuple[TreeMember, ...]: ...


def load_install_manifest(path: Path, *, home: Path) -> InstallManifest: ...
def load_current_release(home: Path) -> RuntimeReleaseIdentity | None: ...
def uninstall_from_manifest(manifest_path: Path, *, home: Path) -> UninstallReport: ...
```

All stored target, tree-member, and backup paths are normalized POSIX paths relative to the exact supplied HOME, owning tree root, or backup root; absolute paths, empty segments, `.`, `..`, duplicate/overlapping ownership, unknown fields, and unsupported schema versions fail closed. `InstallReleaseInput.commit_sha` and `RuntimeReleaseIdentity.commit_sha` are exactly 40 lowercase hex characters; every SHA-256 value is exactly 64 lowercase hex characters. `ReleaseInterpreterInput.executable` is the exact absolute normalized build-venv executable path from evidence (symlink allowed, never rewritten to its base path), `implementation` is exactly `cpython`, `version` is canonical `MAJOR.MINOR.MICRO`, and `tag` is the exact non-empty `sys.implementation.cache_tag` recorded by the build gate (currently `cpython-314`, never inferred from `Requires-Python` or a wheelhouse directory name). Its SHA-256 hashes the strictly resolved regular executable bytes. The strict constructors and parsers enforce these shapes, so the manifest and current-release identity cannot launder invalid CLI provenance. `PathFingerprint.sha256` hashes regular-file bytes, raw symlink-target bytes, or the canonical sorted `TreeMember` list for an immutable release-tree directory; a symlink is never followed. Ordinary created structural directories use `kind="directory"`, `sha256=None`, an exact mode, and uninstall removes them only with anchored `rmdir` after proving they are empty. A `release_tree` entry owns every descendant through its complete `tree_members`, stores their type/hash/mode plus the canonical tree hash, and forbids a second entry for any descendant. Existing directories are never replaced. `InstallManifest.target_paths()` is derived from its top-level entries and must equal `planned_targets`; exact release-tree leaf coverage is separately the equality between the rendered, applied, and serialized `tree_members`. `INSTALL_MANIFEST_RELPATH` is the transaction control record and is intentionally outside the product-target equality; it is backed up/written/restored by the transaction and removed last by uninstall.

`CURRENT_RELEASE_RELPATH` remains in the exact planned/applied/manifest target tuple with kind `current_release`, action, inverse, and mode `0600`, but its `after.sha256` is `None` by schema and is the only regular-file exception to the normal after-hash requirement. This avoids a circular hash: the manifest contains the exact `InstallReleaseInput` (`commit_sha`, artifact SHA-256, complete `ReleaseInterpreterInput`, install timestamp, and install ID), but not the final identity's content hash or `install_manifest_sha256`. The parser rejects `sha256 != None` for this special entry and rejects a missing/duplicate current-release entry.

`InstallPlan.preflight()` snapshots every parent component and destination with `lstat`. Apply anchors operations to directory file descriptors opened beneath the validated HOME with `O_DIRECTORY | O_NOFOLLOW`; it hashes regular files through `openat`/`dir_fd`, reads symlinks without following them, revalidates parent inode/type plus destination type/hash immediately before each `os.replace(..., src_dir_fd=..., dst_dir_fd=...)`, and aborts on any drift. A destination symlink may be replaced only when its recorded `before.kind == "symlink"` and its raw target hash still matches. Backups use the same anchored checks beneath the exact backup root. Partial failure rolls back only entries whose recorded post-apply type/hash still match; drift stops and is reported instead of deleting somebody else's bytes.

Build the venv directly inside the immutable release tree at its final versioned layout; never move it between paths. `render()` and `verify()` check every staged regular-file type, hash, mode, plus the final venv interpreter against all five build-evidence interpreter fields. Apply immutable release content first, then adapters/config/hooks/plist. Next atomically write and fsync the final strict install manifest, hash those exact bytes, construct `RuntimeReleaseIdentity` by adding that `install_manifest_sha256` to the manifest's `InstallReleaseInput`, and atomically write/fsync mode-`0600` `CURRENT_RELEASE_RELPATH` last. Fsync both parent directories. Any failure through the final current-release parent-directory fsync triggers anchored restoration of the prior manifest and identity followed by fsync of both restored parents. The installer may report success or load/restart launchd only after all new control-record fsyncs succeed. If restoration or its fsync cannot be proven, return an explicit `indeterminate_control_record_durability` failure, leave launchd untouched, and make later install/uninstall preflight refuse mutation until the two strict control records are reconciled; never declare success from matching in-memory bytes alone. The installed identity binds the exact commit SHA, release artifact SHA-256, interpreter path/implementation/version/tag/SHA-256, install ID, timestamp, and final manifest SHA-256; none can come from caller-controlled runtime status fields.

`DaemonApp.start()` reads `CURRENT_RELEASE_RELPATH` once through `load_current_release()`, strictly loads the current manifest, recomputes its file SHA-256, and requires matching install ID, commit SHA, artifact SHA-256, complete build-interpreter record, timestamp, and `install_manifest_sha256`. It probes the actual `sys.executable` through an injected interpreter-identity reader, requires that lexical path to be the manifest-owned final venv interpreter inside the immutable release tree, and requires its implementation, version, cache tag, and resolved binary SHA-256 to match the build record before startup completes; it does not require the final venv path to equal the build-venv path. A present malformed/mismatched identity fails startup loudly; an absent identity is explicit unknown for source-checkout development. `get_runtime_startup(app)` in the existing `dan/api/routes_runtime.py` projects that loaded `runtime_release_identity` snapshot as `release.status`, `release.commit_sha`, `release.artifact_sha256`, `release.python.{implementation,version,tag,sha256}`, `release.install_manifest_sha256`, `release.install_id`, `release.installed_at_utc`, and canonical identity SHA-256. The timestamp is the exact normalized identity value, not route-call time. It never runs git, guesses Python from packaging metadata, or rereads a swapped file mid-process.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py \
  tests/test_launchd_assets.py tests/test_scaffold_contracts.py tests/test_api_smoke.py
.venv/bin/ruff check dan/install dan/daemon/app.py dan/api/routes_runtime.py \
  tests/test_installer_atomicity.py tests/test_api_smoke.py
git diff --check
```

## Task 2.6: Uninstall through the inverse manifest

**Files:**

- Modify: `dan/install/manifest.py`
- Modify: `dan/install/__init__.py`
- Modify: `dan/install/__main__.py`
- Modify: `scripts/uninstall.sh`
- Delete: `scripts/uninstall-launchd.sh`
- Modify: `scripts/dan-panel`
- Modify: `README.md`
- Modify: `docs/PRZENOSZENIE.md`
- Modify: `docs/LAUNCH_SUPERVISION.md`
- Modify: `docs/CO-JEST-GDZIE.md`
- Modify: `docs/runbooks/ACCESSIBILITY_TCC.md`
- Modify: `docs/runbooks/PANEL_MENUBAR.md`
- Modify: `docs/runbooks/TERMINAL_AUTOMATION_TCC.md`
- Modify: `docs/runbooks/SCREEN_RECORDING_TCC.md`
- Modify: `docs/runbooks/LAUNCHD.md`
- Modify: `tests/test_installer_atomicity.py`
- Modify: `tests/test_launchd_assets.py`
- Modify: `tests/test_docs_commands.py`

- [ ] **Step 1: Write RED inverse-operation tests**

```python
def test_uninstall_restores_every_replaced_path_from_backup(installed_fixture: InstalledFixture) -> None:
    report = uninstall_from_manifest(installed_fixture.manifest, home=installed_fixture.home)
    assert tree_digest(installed_fixture.home) == installed_fixture.preinstall_digest
    assert all(item.result == "restored" for item in report.items if item.action == "restore_backup")


def test_uninstall_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    manifest = malicious_manifest(tmp_path, target="../../outside")
    with pytest.raises(UnsafeInstallPath):
        uninstall_from_manifest(manifest, home=tmp_path / "home")


@pytest.mark.parametrize("drift", ["type", "hash"])
def test_uninstall_refuses_regular_target_type_or_hash_drift(
    installed_fixture: InstalledFixture,
    drift: str,
) -> None:
    installed_fixture.drift_regular_target(drift)
    with pytest.raises(InstallTreeDrift):
        uninstall_from_manifest(installed_fixture.manifest, home=installed_fixture.home)
    assert installed_fixture.drifted_target_untouched()


@pytest.mark.parametrize(
    "drift",
    ["type", "mode", "install_id", "manifest_sha", "python_identity"],
)
def test_uninstall_refuses_current_release_identity_drift(
    installed_fixture: InstalledFixture,
    drift: str,
) -> None:
    installed_fixture.drift_current_release(drift)
    with pytest.raises(InstallTreeDrift):
        uninstall_from_manifest(installed_fixture.manifest, home=installed_fixture.home)
    assert installed_fixture.current_release_untouched()


def test_uninstall_refuses_backup_symlink_or_hash_drift(
    installed_fixture: InstalledFixture,
) -> None:
    installed_fixture.replace_backup_with_symlink()
    with pytest.raises(InstallTreeDrift):
        uninstall_from_manifest(installed_fixture.manifest, home=installed_fixture.home)
    assert installed_fixture.outside_tree_digest_unchanged()
```

Also add `test_uninstall_revalidates_parent_chain_immediately_before_mutation`, `test_uninstall_refuses_manifest_home_mismatch`, and prove that user databases, migration checkpoints, backup archives, unrelated config, and `~/.claude/archive` are preserved.
Add `test_no_legacy_launchd_installer_uninstaller_or_repo_wrapper_remains` and doc-command tests proving every active install/uninstall/runbook path uses only `scripts/install.sh`, `scripts/uninstall.sh`, or the explicit Python module with the mandatory release evidence.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_installer_atomicity.py
```

Expected: inverse restore, strict containment, current-target drift, backup drift, and immediate parent-chain revalidation tests fail against the embedded shell uninstaller/current rollback path.

- [ ] **Step 3: Implement strict inverse-manifest uninstall**

Add `--uninstall` to the current mutually exclusive `python -m dan.install` mode group. `scripts/uninstall.sh` requires explicit `--python ABSOLUTE_PATH` and `--home ABSOLUTE_PATH`, then `exec`s that interpreter with `-m dan.install --uninstall --python ABSOLUTE_PATH --home ABSOLUTE_PATH --manifest ABSOLUTE_PATH/.dan/install-manifest.json`; it performs no discovery or mutation itself. The Python CLI requires the non-resolved absolute `sys.executable` to equal `--python`, requires that path to be the still-manifest-owned final release venv interpreter, strictly parses the manifest, and requires the executing interpreter's implementation/version/cache-tag/resolved-binary SHA to match the recorded `ReleaseInterpreterInput` metadata (the build-venv lexical path itself is provenance, not the uninstall path). It requires the recorded HOME to equal explicit `--home`, validates the complete target and backup set before mutation, then revalidates the anchored parent inode/type and target fingerprint immediately before every relative unlink/replace. `restore_backup` accepts only the recorded backup kind/hash/mode beneath the exact backup root; `remove` accepts only the recorded post-install kind/hash/mode. A regular file cannot stand in for a symlink or vice versa. The immutable release-tree inverse walks only the serialized `tree_members`, validates every leaf and the canonical tree hash first, removes recorded leaves in reverse depth through anchored directory FDs, and uses `rmdir` for recorded empty directories; it never uses `rm -rf` or discovers extra HOME paths. The `current_release` inverse is the one non-hash special case: immediately before mutation it requires a mode-`0600` regular file, strict `RuntimeReleaseIdentity`, the manifest's exact install ID/commit/artifact/interpreter/timestamp, and `install_manifest_sha256` equal to the hash of the still-open validated manifest. On any drift it stops without following or deleting the drifted path and emits a non-zero report. It applies the recorded `CURRENT_RELEASE_RELPATH` inverse only after those checks and removes the control manifest itself only after every inverse succeeds. It never discovers paths by scanning HOME.

Delete `scripts/uninstall-launchd.sh` once this inverse exists. Rewrite every live README, migration guide, launch supervision document, TCC/runbook command, inventory table, panel comment, and test named in this task so none points to `scripts/dand`, `scripts/install-launchd.sh`, or `scripts/uninstall-launchd.sh`. Historical git history is not rewritten. No compatibility shim may bypass the manifest or omit explicit interpreter/HOME arguments.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_installer_atomicity.py tests/test_launchd_assets.py tests/test_docs_commands.py
.venv/bin/ruff check dan/install tests/test_installer_atomicity.py
test ! -e scripts/dand
test ! -e scripts/install-launchd.sh
test ! -e scripts/uninstall-launchd.sh
! rg -n 'scripts/(dand|install-launchd\.sh|uninstall-launchd\.sh)' \
  README.md docs scripts launchd -g '!docs/superpowers/plans/**'
git diff --check
```

## Task 2.7: Bind Claude MessageDisplay to the installed DAN hook

**Files:**

- Modify: `dan/install/__init__.py`
- Modify: `dan/install/adapters.py`
- Modify: `integrations/claude/hooks/tts-message-display.sh`
- Modify: `tests/test_hook_fail_open.py`
- Modify: `tests/test_installer_atomicity.py`

- [ ] **Step 1: Write RED structural-merge tests**

```python
def test_installer_binds_message_display_to_installed_dan_hook(tmp_path: Path) -> None:
    settings = install_into_fixture_home(tmp_path).claude_settings
    commands = message_display_commands(settings)
    assert commands == (str(tmp_path / ".dan/integrations/claude/tts-message-display.sh"),)
    assert "dan_core" not in json.dumps(settings)


def test_claude_settings_merge_preserves_unrelated_hooks(tmp_path: Path) -> None:
    before = settings_with_unrelated_hooks(tmp_path)
    after = merge_claude_settings(before, dan_hook_path(tmp_path))
    assert after["hooks"]["PreToolUse"] == before["hooks"]["PreToolUse"]
```

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_hook_fail_open.py tests/test_installer_atomicity.py
```

Expected: installed command/path and unrelated-hook preservation assertions fail against the current shell/merge behavior.

- [ ] **Step 3: Implement structural merge**

The installed hook invokes only `dan speak` and remains fail-open for Claude. Never replace the whole settings file or delete unrelated user hooks.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_hook_fail_open.py tests/test_installer_atomicity.py
.venv/bin/ruff check dan/install tests/test_hook_fail_open.py
git diff --check
```

- [ ] **Step 5: Record the deployment boundary**

Expected repository result: generated fixture HOME points to the installed DAN hook and contains no active `dan_core.say`. Do **not** edit the real `~/.claude/settings.json`; that is an explicit post-GREEN deploy step.

## Task 2.8: Report all required macOS permissions without prompting

**Files:**

- Modify: `dan/install/preflight.py`
- Modify: `dan/input/hotkey.py`
- Modify: `dan/install/__init__.py`
- Modify: `dan/cli.py`
- Create: `tests/test_install_preflight.py`
- Modify: `tests/test_cli_config.py`
- Modify: `tests/test_daemon_hotkey.py`

- [ ] **Step 1: Write RED tri-state tests**

```python
@pytest.mark.parametrize("kind", tuple(PermissionKind))
@pytest.mark.parametrize(("native", "expected"), [(True, "granted"), (False, "denied"), (None, "unknown")])
def test_preflight_reports_every_permission_tri_state(
    kind: PermissionKind,
    native: bool | None,
    expected: PermissionState,
) -> None:
    probes = permission_probes_fixture(overrides={kind: lambda: native})
    report = collect_permission_preflight(probes=probes, executable=Path(sys.executable))
    assert report.by_kind(kind).state == expected


def test_permission_report_uses_resolved_sys_executable_identity() -> None:
    report = collect_permission_preflight(probes=all_granted_probes())
    assert {item.responsible_executable for item in report.checks} == {
        str(Path(sys.executable).resolve(strict=True))
    }


def test_automation_probe_never_asks_user(native_automation: RecordingAutomationProbe) -> None:
    automation_permission_state(checker=native_automation)
    assert native_automation.ask_user_if_needed_calls == [False]


def test_host_preflight_keeps_ffmpeg_failure_separate_from_tcc(
    missing_ffmpeg_probe: RuntimeExecutableProbe,
) -> None:
    report = build_report(
        permission_probes=all_granted_probes(),
        runtime_executable_probe=missing_ffmpeg_probe,
    )
    assert report.install.check("ffmpeg_visible").ok is False
    assert report.installable is False
    assert report.permission_ready is True
```

Add `test_accessibility_uses_ax_non_prompt_probe`, `test_screen_recording_uses_preflight_not_request_api`, `test_microphone_reads_authorization_without_request_access`, `test_unknown_permission_is_never_reported_as_granted`, `test_doctor_and_installer_share_identical_permission_report`, and `test_doctor_and_installer_share_identical_ffmpeg_runtime_path`. Automated tests inject every native adapter and runtime-executable probe and assert that no prompt/request, real ffmpeg, git, microphone, or audio function is called.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_install_preflight.py tests/test_cli_config.py tests/test_daemon_hotkey.py
```

Expected: `tests/test_install_preflight.py` fails because the four-permission report does not exist; the existing `accessibility_trust_state(checker=...)` tests remain the compatibility baseline.

- [ ] **Step 3: Implement the existing preflight aggregator and four fixed probes**

Keep the current public entry point and extend it only with injectable probes and executable identity:

```python
class PermissionKind(StrEnum):
    ACCESSIBILITY = "accessibility"
    SCREEN_RECORDING = "screen_recording"
    MICROPHONE = "microphone"
    AUTOMATION = "automation"


class PermissionState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PermissionCheck:
    kind: PermissionKind
    state: PermissionState
    responsible_executable: str
    probe: str
    target: str | None = None


@dataclass(frozen=True)
class PermissionProbes:
    accessibility: Callable[[], bool | None]
    screen_recording: Callable[[], bool | None]
    microphone: Callable[[], bool | None]
    automation: Callable[[], bool | None]


@dataclass(frozen=True)
class PermissionPreflightReport:
    checks: tuple[PermissionCheck, ...]

    def by_kind(self, kind: PermissionKind) -> PermissionCheck: ...


@dataclass(frozen=True)
class HostPreflightReport:
    install: PreflightReport
    permissions: PermissionPreflightReport
    installable: bool
    permission_ready: bool


def collect_permission_preflight(
    *,
    probes: PermissionProbes | None = None,
    executable: Path | None = None,
) -> PermissionPreflightReport: ...


def build_report(
    home: Path | None = None,
    *,
    include_launchd: bool = True,
    permission_probes: PermissionProbes | None = None,
    runtime_executable_probe: RuntimeExecutableProbe = probe_runtime_executable,
    executable: Path | None = None,
) -> HostPreflightReport: ...
```

The fixed native probes are:

1. **Accessibility:** call the existing `dan.input.hotkey.accessibility_trust_state(checker=AXIsProcessTrusted)` path. It uses `AXIsProcessTrusted`, never `AXIsProcessTrustedWithOptions` with a prompt key, and maps `trusted/untrusted/unknown` to `granted/denied/unknown`.
2. **Screen Recording:** call Quartz `CGPreflightScreenCaptureAccess()` only. Never call `CGRequestScreenCaptureAccess()`. `True` is granted, `False` is denied/not granted, and import/call failure is unknown.
3. **Microphone:** read `AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)` only. Map authorized to granted; denied/restricted to denied; not-determined or an unknown value to unknown. Never call `requestAccessForMediaType`.
4. **Automation:** call `AEDeterminePermissionToAutomateTarget` for bundle ID `com.apple.systemevents` with `askUserIfNeeded=False`. OSStatus zero is granted, `errAEEventNotPermitted` is denied, and missing target/framework or any other status is unknown. Never send an Apple event during preflight.

The report order is exactly the enum order above. The authoritative responsible identity on every check is `str(Path(executable or sys.executable).resolve(strict=True))`; a stable wrapper path may be reported separately as invocation context but must never replace `sys.executable` as the TCC identity. `python -m dan.install.preflight`, the install CLI preflight phase, and `dan doctor` all call this same `build_report()` and serialize the same permission objects plus Task 2.5's `ffmpeg_visible` result and deterministic runtime PATH. None exposes a `--prompt`, ambient-PATH, or git-discovery option. `HostPreflightReport.installable` is the existing filesystem/install-plan result, including fail-closed ffmpeg verification, and does not become false merely because a not-yet-installed executable lacks TCC grants; `permission_ready` is separately true only when all four states are granted. Doctor reports both, while installer gates mutation only on `installable`, avoiding a grant-before-install deadlock.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_install_preflight.py tests/test_cli_config.py tests/test_daemon_hotkey.py
.venv/bin/ruff check dan/install/preflight.py dan/input/hotkey.py dan/cli.py \
  tests/test_install_preflight.py tests/test_daemon_hotkey.py
git diff --check
```

## Task 2.9: Bound native playback and recover the single CoreAudio owner

Confirmed live blocker: `CoreAudioPlayer` currently waits a hard-coded 300 seconds, and timeout leaves `_started` plus the backend graph stale so repeated requests fail. This task uses only fakes to repair the code path; it does not restart the daemon, touch the broker process, or play audio.

**Files:**

- Modify: `dan/voice/player.py`
- Read only: `dan/voice/broker.py`
- Read only: `dan/voice/queue.py`
- Create: `tests/test_voice_player.py`
- Modify: `tests/test_voice_broker.py`

- [ ] **Step 1: Write RED deadline, reset, and queue-truth tests**

```python
def test_native_timeout_uses_injected_audio_deadline_and_fully_resets(
    wav_chunk: SynthesizedChunk,
) -> None:
    backend = RecoveringFakeBackend()
    waiter = ScriptedCompletionWaiter(results=[False, True])
    player = CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _: 0.025,
        completion_waiter=waiter,
    )
    with pytest.raises(CoreAudioPlayerError, match="completion timed out"):
        player.play(wav_chunk, should_play=lambda: True, on_started=lambda: None)
    assert waiter.timeouts == [0.025]
    assert backend.stop_calls == 1
    assert backend.recover_calls == 1

    player.play(next_wav_chunk(), should_play=lambda: True, on_started=lambda: None)
    assert backend.start_calls == 2
    assert waiter.timeouts[1] == 0.025


def test_native_route_loss_recovers_before_the_next_request(
    wav_chunk: SynthesizedChunk,
) -> None:
    backend = RecoveringFakeBackend(fail_first_play=NativePlaybackRouteLost("route lost"))
    player = CoreAudioPlayer(backend=backend, deadline_for_audio=lambda _: 0.025)
    with pytest.raises(CoreAudioPlayerError, match="route lost"):
        player.play(wav_chunk, should_play=lambda: True, on_started=lambda: None)
    assert backend.recover_calls == 1
    player.play(next_wav_chunk(), should_play=lambda: True, on_started=lambda: None)
    assert backend.completed_plays == 1


def test_broker_marks_timed_out_row_failed_and_completes_the_next_row(
    recovery_broker_fixture: RecoveryBrokerFixture,
) -> None:
    first, second = recovery_broker_fixture.enqueue_two()
    assert recovery_broker_fixture.broker.drain_all() == 1
    assert recovery_broker_fixture.row(first.id) == {
        "status": "failed",
        "playback_confirmed": 0,
        "playback_completed": True,
    }
    assert recovery_broker_fixture.row(second.id) == {
        "status": "done",
        "playback_confirmed": 1,
        "playback_completed": True,
    }
    assert recovery_broker_fixture.waiter.timeouts[0] < 1.0
```

Also add exact tests named `test_wav_deadline_is_duration_derived_clamped_and_never_300_seconds`, `test_timeout_recovery_clears_started_completion_cancel_and_active_buffer_state`, `test_late_completion_from_dead_backend_cannot_finish_the_next_request`, `test_recovery_failure_still_leaves_player_stopped_for_retry`, and `test_route_loss_row_error_is_persisted_without_stalling_broker`. All backends, completion waiters, WAVs, clocks, and queue connections are fakes under the audio guard; no AVFoundation, CoreAudio device, Supertonic, `ffmpeg`, `afplay`, or `say` process may run.

- [ ] **Step 2: Verify RED**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_voice_player.py tests/test_voice_broker.py
```

Expected: the timeout tests observe the current hard-coded 300-second wait, `_started`/backend state remains stale after failure, and the deterministic second request cannot prove recovery.

- [ ] **Step 3: Implement duration-bounded recovery without a second audio owner**

```python
PLAYBACK_TIMEOUT_MULTIPLIER = 2.0
PLAYBACK_TIMEOUT_GRACE_SECONDS = 2.0
PLAYBACK_TIMEOUT_MIN_SECONDS = 3.0
PLAYBACK_TIMEOUT_MAX_SECONDS = 60.0


class NativePlaybackRouteLost(CoreAudioPlayerError): ...


def wav_duration_seconds(audio: bytes) -> float: ...
def playback_deadline_seconds(audio: bytes) -> float: ...
def wait_for_event(event: threading.Event, timeout_seconds: float) -> bool: ...


class NativeAudioBackend(Protocol):
    def start(self) -> None: ...
    def make_buffer(self, audio: bytes) -> Any: ...
    def play(self, buffer: Any, completion: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...
    def recover(self) -> None: ...


class CoreAudioPlayer:
    def __init__(
        self,
        *,
        backend: NativeAudioBackend | None = None,
        deadline_for_audio: Callable[[bytes], float] = playback_deadline_seconds,
        completion_waiter: Callable[[threading.Event, float], bool] = wait_for_event,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None: ...
```

`wav_duration_seconds()` strictly parses the in-memory WAV header and derives `frames / sample_rate`; invalid, empty, non-finite, or non-positive duration raises before native playback. The default deadline is `min(60.0, max(3.0, duration * 2.0 + 2.0))`. `CoreAudioPlayer` calls the injected deadline exactly once per chunk, rejects a non-finite, non-positive, or greater-than-60-second injected value, and passes it to the injected waiter—there is no fixed 300-second branch.

The current `_play_lock` remains the single playback owner. A completion timeout or `NativePlaybackRouteLost` takes the schedule/state locks in the established order, invalidates that buffer generation, best-effort stops the node, calls `backend.recover()`, and always clears `_started`, `_current_completion`, `_current_cancelled`, `_active_buffers`, and `_last_completed_at` before raising `CoreAudioPlayerError`. A late callback captures only its retired generation/event and cannot touch the next request. Recovery failure is attached to the raised error but still leaves player state stopped; the next request calls `backend.start()` again rather than trusting stale state.

`_AVFoundationBackend.recover()` is the full native reset boundary: stop/reset the player node, stop/reset the engine, discard the old node/engine/connection format, construct and attach a fresh node to a fresh engine, and leave it unstarted. Native engine-start, scheduling, and route/device failures are normalized to `NativePlaybackRouteLost`; invalid WAV remains a decode error. Do not spawn a second player, broker, `afplay`, or fallback backend. The existing `VoiceBroker` exception path remains the queue owner: it persists the current row as `failed` with `playback_confirmed=0` and completion timestamp, then immediately advances to the already-prefetched/next row, which uses the recovered same `CoreAudioPlayer` instance.

- [ ] **Step 4: Verify GREEN**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_voice_player.py tests/test_voice_broker.py
.venv/bin/ruff check dan/voice/player.py tests/test_voice_player.py tests/test_voice_broker.py
git diff --check
```

Expected: deadline and route-loss failures reset the native owner, persist the first row as failed within the injected bound, and allow the next row to finish; the guard proves no real audio edge was touched.

- [ ] **Step 5: Run the full Batch 2 gate**

```bash
dan_batch2_isolation
dan_batch2_env .venv/bin/python -m pytest -q \
  -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_engine_supervisor.py tests/test_voice_assets.py tests/test_voice_service.py \
  tests/test_voice_tts_supertonic.py \
  tests/test_daemon_hotkey.py tests/test_voice_listening.py tests/test_listening_leases.py \
  tests/test_jobs_scheduler.py tests/test_api_smoke.py tests/test_installer_atomicity.py \
  tests/test_launchd_single_owner.py tests/test_launchd_assets.py tests/test_hook_fail_open.py \
  tests/test_install_preflight.py tests/test_cli_config.py tests/test_voice_player.py \
  tests/test_voice_broker.py tests/test_dan_lifecycle.py \
  tests/test_scaffold_contracts.py tests/test_docs_commands.py
.venv/bin/ruff check dan/daemon dan/input dan/jobs dan/install dan/voice/listening.py \
  dan/voice/assets.py dan/voice/service.py dan/voice/tts.py dan/voice/player.py \
  dan/voice/broker.py tests/test_engine_supervisor.py tests/test_voice_assets.py \
  tests/test_voice_service.py tests/test_installer_atomicity.py tests/test_voice_player.py \
  tests/test_voice_broker.py tests/test_dan_lifecycle.py
git diff --check
```

Expected: all pass without touching live HOME, launchd, TCC, microphone, or audio. A separate host-deployment plan is required before applying these artifacts to production.
