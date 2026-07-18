# DAN Release 1 Audit Remediation — Batch 2 Runtime and Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dand` the truthful owner of child processes, PTT lifecycle, scheduled jobs, and every installed host artifact, with bounded recovery, reversible installation, and accurate macOS permission reporting.

**Architecture:** `ChildSupervisor` owns a continuous watchdog and restart budget. `SupertonicEngine` consumes only the supervised serve child. `DaemonApp` owns and closes the PTT activation gate, source-scoped listening leases, and scheduler. A staged Python install plan owns every installed path and produces the inverse manifest used by uninstall. Host HOME changes remain a separate deployment action.

**Tech Stack:** Python 3.11+, threading/monotonic clocks, subprocess, local HTTP, SQLite-backed state, launchd plist, shell adapters, JSON manifests, macOS Accessibility/TCC probes, pytest, ruff.

## Global Constraints

- Start only after Batch 1 is GREEN. Reuse its `IntakeGate`; do not invent a second restart/intake flag.
- Exactly one `dand` owns one Supertonic serve child and one PTT monitor. Never adopt an unrelated process listening on the expected port and never fall back to a parallel TTS CLI.
- Tests inject clocks, waiters, subprocesses, permission probes, and filesystem roots. They must not touch live `launchd`, TCC, microphone, audio, or `$HOME`.
- Repository fixes do not mutate the active `~/.dan` or `~/.claude/settings.json`. Applying a generated install plan to real HOME is a separately authorized deployment.
- Versioned release directories are immutable. Wrappers are atomically switched; do not move a built virtualenv between paths because absolute shebangs make it non-relocatable.
- Every task receives RED/GREEN evidence and two independent reviews before the next task begins.

---

## Task 2.1: Supervise the TTS child with a bounded watchdog

**Files:**

- Modify: `dan/daemon/supervisor.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/voice/tts.py`
- Modify: `dan/config.py`
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

Add `test_degraded_child_does_not_enter_respawn_loop`, `test_supertonic_serve_failure_never_invokes_parallel_cli`, and preserve the existing foreign-port-owner rejection test.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_engine_supervisor.py tests/test_voice_tts_supertonic.py
```

Expected: no autonomous restart and current CLI fallback observation.

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

`SupertonicEngine.synthesize()` must require the supervised serve endpoint. Remove `_synth_cli` fallback from the active path and reject a foreign port owner without adopting or killing it.

- [ ] **Step 4: Verify GREEN and shutdown ordering**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_engine_supervisor.py tests/test_voice_tts_supertonic.py
.venv/bin/ruff check dan/daemon/supervisor.py dan/daemon/app.py dan/voice/tts.py \
  tests/test_engine_supervisor.py tests/test_voice_tts_supertonic.py
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

- [ ] **Step 2: Verify RED and implement explicit ownership**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_daemon_hotkey.py tests/test_voice_listening.py tests/test_engine_supervisor.py
```

```python
class PttActivationGate:
    def cancel(self) -> None: ...
    def close(self) -> None: ...
```

Each scheduled callback captures a monotonically increasing generation and exits when stale or closed. `DaemonApp` stores `ptt_activation_gate`, closes it before stopping the monitor/children, and creates a new instance only during a clean start.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
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

- [ ] **Step 1: Write RED source-isolation tests**

```python
def test_hotkey_up_cannot_release_panel_hold(manager: ListeningLeaseManager) -> None:
    manager.acquire(mode="hold", source="panel")
    manager.acquire(mode="hold", source="global_hotkey")
    manager.release(mode="hold", source="global_hotkey")
    assert manager.active_sources(mode="hold") == ("panel",)


def test_ptt_up_requires_allowed_source(client: TestClient) -> None:
    response = client.post("/voice/ptt/up", json={"source": "unknown"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_ptt_source"
```

- [ ] **Step 2: Verify RED, change the signature, verify GREEN**

Target signature:

```python
def release(self, *, mode: str, source: str) -> tuple[ListeningLease, ...]:
    ...
```

`post_ptt_up()` validates the same source allowlist as down. The global hotkey always uses `global_hotkey`; the panel uses its own explicit source.

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_listening_leases.py tests/test_voice_listening.py
.venv/bin/ruff check dan/voice/listening.py dan/api/routes_voice.py tests/test_listening_leases.py
git diff --check
```

## Task 2.4: Wire the standup scheduler into daemon lifecycle

**Files:**

- Modify: `dan/jobs/scheduler.py`
- Modify: `dan/jobs/standup.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/paths.py`
- Modify: `dan/api/routes_runtime.py`
- Modify: `tests/test_jobs_scheduler.py`
- Modify: `tests/test_api_smoke.py`

- [ ] **Step 1: Write RED lifecycle tests**

```python
def test_daemon_constructs_starts_and_exposes_standup_scheduler(app: DaemonApp) -> None:
    app.start()
    status = app.runtime_status()["jobs"]
    assert status["scheduler_state"] == "running"
    assert status["jobs"]["standup"]["registered"] is True


def test_daemon_stops_scheduler_before_storage_close(app: DaemonApp) -> None:
    app.stop()
    assert app.lifecycle_trace.index("scheduler_stopped") < app.lifecycle_trace.index("storage_closed")
```

Add `test_scheduler_never_submits_after_stop` using an injected waiter.

- [ ] **Step 2: Verify RED and implement the owned runner**

```python
class JobScheduler:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> SchedulerStatus: ...
```

Create it after storage and `VoiceService` are ready; stop it before storage. Resolve the `StandupJob` text provider through the existing brain/conversation path, not a second model/provider. Status must expose last run, next due, last error and running/stopped state without private text.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_jobs_scheduler.py tests/test_api_smoke.py
.venv/bin/ruff check dan/jobs dan/daemon/app.py tests/test_jobs_scheduler.py
git diff --check
```

## Task 2.5: Manifest every staged install artifact

**Files:**

- Modify: `dan/install/__init__.py`
- Modify: `dan/install/__main__.py`
- Modify: `dan/install/adapters.py`
- Modify: `scripts/install.sh`
- Modify: `config/dan.example.toml`
- Modify: `tests/test_installer_atomicity.py`
- Modify: `tests/test_launchd_single_owner.py`
- Modify: `tests/test_launchd_assets.py`

- [ ] **Step 1: Write RED completeness and rollback tests**

```python
def test_manifest_covers_every_installed_artifact(plan: InstallPlan) -> None:
    kinds = {entry.kind for entry in plan.manifest.entries}
    assert kinds == {
        "release_tree", "venv", "wrapper", "config", "hook", "adapter", "plist", "managed_doc"
    }


def test_partial_apply_failure_rolls_back_prior_artifacts(tmp_path: Path) -> None:
    plan = failing_plan_after_third_write(tmp_path)
    before = tree_digest(plan.target_root)
    with pytest.raises(InstallApplyError):
        plan.apply()
    assert tree_digest(plan.target_root) == before
```

Add wrong-mode rejection and stage-only no-target-mutation tests.

- [ ] **Step 2: Verify RED and implement staged release ownership**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py tests/test_launchd_assets.py
```

The Python plan must own all writes. `scripts/install.sh` becomes a thin argument/setup adapter and may not pre-create an unmanifested venv, wrapper, config or hook. Each entry stores target path, staged source, SHA-256, mode, ownership action, backup path and inverse action.

Build the venv directly at its immutable versioned final path under staging-compatible release layout; switch stable wrappers atomically only after verification. `verify()` checks regular-file type, hash and mode.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_installer_atomicity.py tests/test_launchd_single_owner.py tests/test_launchd_assets.py
.venv/bin/ruff check dan/install tests/test_installer_atomicity.py
git diff --check
```

## Task 2.6: Uninstall through the inverse manifest

**Files:**

- Modify: `dan/install/__init__.py`
- Modify: `dan/install/__main__.py`
- Modify: `scripts/uninstall.sh`
- Modify: `tests/test_installer_atomicity.py`

- [ ] **Step 1: Write RED inverse-operation tests**

```python
def test_uninstall_restores_every_replaced_path_from_backup(installed_fixture: InstalledFixture) -> None:
    report = uninstall(installed_fixture.manifest)
    assert tree_digest(installed_fixture.home) == installed_fixture.preinstall_digest
    assert all(item.result == "restored" for item in report.items if item.action == "restore-backup")


def test_uninstall_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    manifest = malicious_manifest(tmp_path, target="../../outside")
    with pytest.raises(UnsafeInstallPath):
        uninstall(manifest)
```

Also prove that user databases and backup archives are preserved.

- [ ] **Step 2: Implement and verify**

`scripts/uninstall.sh` calls the validated Python CLI only. `restore-backup` restores its recorded bytes/mode; `remove` deletes only a file whose current hash still matches the manifest, otherwise reports drift and stops.

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_installer_atomicity.py
.venv/bin/ruff check dan/install tests/test_installer_atomicity.py
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

- [ ] **Step 2: Verify RED, implement structural merge, verify GREEN**

The installed hook invokes only `dan speak` and remains fail-open for Claude. Never replace the whole settings file or delete unrelated user hooks.

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_hook_fail_open.py tests/test_installer_atomicity.py
.venv/bin/ruff check dan/install tests/test_hook_fail_open.py
git diff --check
```

- [ ] **Step 3: Record the deployment boundary**

Expected repository result: generated fixture HOME points to the installed DAN hook and contains no active `dan_core.say`. Do **not** edit the real `~/.claude/settings.json`; that is an explicit post-GREEN deploy step.

## Task 2.8: Report Accessibility/TCC as a tri-state preflight

**Files:**

- Create or consolidate: `dan/install/preflight.py`
- Modify: `dan/input/hotkey.py`
- Modify: `dan/install/__init__.py`
- Modify: `dan/cli.py`
- Modify: `tests/test_cli_config.py`
- Modify: `tests/test_daemon_hotkey.py`

- [ ] **Step 1: Write RED tri-state tests**

```python
@pytest.mark.parametrize(("native", "expected"), [(True, "granted"), (False, "denied"), (None, "unknown")])
def test_preflight_reports_accessibility_tri_state(native: bool | None, expected: str) -> None:
    report = accessibility_preflight(probe=lambda: native, prompt=False)
    assert report.state == expected


def test_unknown_permission_is_never_reported_as_granted() -> None:
    assert accessibility_preflight(probe=lambda: None, prompt=False).state == "unknown"
```

Doctor and installer must report the same state plus the responsible executable identity: versioned `~/.dan/.../venv/bin/python` and stable `~/.dan/bin/dand` wrapper. The probe must not trigger a TCC prompt.

- [ ] **Step 2: Verify RED, implement one preflight, verify GREEN**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cli_config.py tests/test_daemon_hotkey.py
.venv/bin/ruff check dan/install/preflight.py dan/input/hotkey.py dan/cli.py \
  tests/test_daemon_hotkey.py
git diff --check
```

- [ ] **Step 3: Run the full Batch 2 gate**

```bash
env HOME=/private/tmp/dan-batch2-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_engine_supervisor.py tests/test_voice_tts_supertonic.py \
  tests/test_daemon_hotkey.py tests/test_voice_listening.py tests/test_listening_leases.py \
  tests/test_jobs_scheduler.py tests/test_api_smoke.py tests/test_installer_atomicity.py \
  tests/test_launchd_single_owner.py tests/test_launchd_assets.py tests/test_hook_fail_open.py \
  tests/test_cli_config.py
.venv/bin/ruff check dan/daemon dan/input dan/jobs dan/install dan/voice/listening.py \
  dan/voice/tts.py tests/test_engine_supervisor.py tests/test_installer_atomicity.py
git diff --check
```

Expected: all pass without touching live HOME, launchd, TCC, microphone, or audio. A separate host-deployment plan is required before applying these artifacts to production.
