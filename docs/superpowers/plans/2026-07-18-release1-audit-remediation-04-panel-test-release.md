# DAN Release 1 Audit Remediation — Batch 4 Panel, Test Safety, and Release Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the panel render only daemon-owned truth, remove the provider/mock/dev product maze, package every required release resource exactly once, and prove a clean-clone offline build/install with a fail-closed active-HOME release audit.

**Architecture:** The daemon exposes one Claude runtime snapshot and explicit source-health states. The panel never creates product state locally. Build configuration force-includes canonical repo resources into one wheel resource tree without source copies. A hash-locked wheelhouse supports a network-free clean-clone gate. Release audit uses a SHA-bound checkpoint inventory and scans active HOME roots by default.

**Tech Stack:** Vanilla JavaScript/HTML/CSS panel, Python local API, pytest, static asset tests, hatchling, sdist/wheel, importlib.resources, SHA-256 wheelhouse lock, git clean-clone fixtures, JSON release reports.

## Global Constraints

- Panel tasks are blocked until Fable's patch is stable, committed or explicitly handed over, and the exact panel paths are assigned to this batch. Do not edit through an active owner.
- Batch 3 must be GREEN before removing backend provider/config projections. Batch 2 installer ownership must be GREEN before the offline install gate.
- Keep the one persistent Claude CLI conversation and its model/effort/fast controls. Remove provider switching, mock/dev product modes, and disabled-by-policy controls—not the approved Claude settings.
- The panel is a renderer/client. It cannot own chats, queue rows, session identity, playback, child processes, or optimistic message state.
- Test doubles enter through constructor injection or fixtures, never through product config or a user-visible mock provider.
- The wheel contains one canon and one versioned resource tree. Do not add a second source copy of `DAN.md`.
- Build/release gates are offline. A separate preparation command may download wheels, but its output is hash-locked and reviewed before the gate runs.
- Active HOME findings are fatal by default. Reports store paths, kinds, hashes and finding codes, not private file contents.
- Production code uses self-explanatory English names. Add comments only for non-obvious invariants, races, or platform constraints.

---

## Task 4.1: Render text/history/voice/queue state only from daemon evidence

**Ownership precondition:** The following paths are no longer being edited by Fable and have been explicitly assigned to this task.

**Files:**

- Modify: `dan/panel/assets/app.js`
- Modify: `tests/test_panel_operator_api.py`
- Modify: `tests/test_panel_assets.py`

- [ ] **Step 1: Record the handoff fingerprint**

```bash
git rev-parse HEAD
git status --short -- dan/panel/assets/app.js tests/test_panel_operator_api.py tests/test_panel_assets.py
shasum -a 256 dan/panel/assets/app.js tests/test_panel_operator_api.py tests/test_panel_assets.py
```

Expected: values match the ownership handoff. Any later drift before edit is `STOP`.

- [ ] **Step 2: Write RED truthfulness tests**

```python
def test_failed_text_post_never_leaves_local_user_bubble(panel_harness: PanelHarness) -> None:
    panel_harness.post_text_fails(status=503, code="intake_closed")
    panel_harness.send_text("blocked")
    assert panel_harness.local_pending_bubbles() == []
    assert panel_harness.composer_text() == "blocked"
    assert panel_harness.visible_error_code() == "intake_closed"


def test_queue_transport_failure_is_unavailable_not_empty(panel_harness: PanelHarness) -> None:
    panel_harness.queue_snapshot([{"id": "q1", "state": "queued"}])
    panel_harness.queue_request_fails()
    assert panel_harness.visible_queue_ids() == ["q1"]
    assert panel_harness.queue_source_status() == "unknown"
```

Add:

- `test_send_text_renders_only_daemon_returned_turn`;
- `test_history_transport_failure_preserves_snapshot_and_marks_unknown`;
- `test_voice_transport_failure_is_unknown_not_disabled`;
- `test_successful_post_clears_composer_only_after_daemon_accepts`.

- [ ] **Step 3: Verify RED**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_panel_operator_api.py tests/test_panel_assets.py
```

Expected: optimistic bubble and empty-on-error assertions fail.

- [ ] **Step 4: Remove local product-state mutation**

Delete `appendPendingUserBubble()` and its call from `sendTextInput()`. Keep composer text until the daemon accepts the request. On acceptance, render the returned server turn or refresh the daemon-owned history; on rejection, keep the last confirmed snapshot and show the stable error.

Represent every fetched surface as:

```javascript
{
  sourceStatus: "ok" | "unknown" | "error",
  snapshot: lastConfirmedSnapshot,
  error: null | { code, message }
}
```

Do not replace a failed history/queue/voice fetch with `[]`, `disabled`, or an invented local row.

- [ ] **Step 5: Verify GREEN and review UX failure states**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_panel_operator_api.py tests/test_panel_assets.py
.venv/bin/ruff check tests/test_panel_operator_api.py tests/test_panel_assets.py
git diff --check
```

## Task 4.2: Expose one product brain provider in backend/API

**Files:**

- Modify: `dan/brain/manager.py`
- Modify: `dan/brain/auto_detect.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/api/routes_brain.py`
- Modify: `dan/api/routes_runtime.py`
- Modify: `dan/daemon/lifecycle.py`
- Modify: `dan/config.py`
- Delete: `scripts/smoke-brain-switch.sh`
- Modify: `tests/test_brain_manager.py`
- Modify: `tests/test_brain_api.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_runtime_settings_legacy_approval.py`

- [ ] **Step 1: Write RED single-provider tests**

```python
def test_product_manager_exposes_only_claude_cli(config: DANConfig) -> None:
    manager = BrainManager.from_config(config)
    assert manager.product_adapter_id == "claude_cli"
    assert not hasattr(manager, "switch_product_adapter")


def test_brain_switch_route_is_not_registered(app: DaemonApp) -> None:
    routes = {(route.method, route.path) for route in app.router.routes}
    assert ("POST", "/brain/switch") not in routes
```

Add tests proving product config has no `codex_cli`, `test`, `default_adapter`, or provider-session map, and runtime apply rejects `brain.provider` while retaining Claude model/effort/fast keys.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_brain_manager.py tests/test_brain_api.py tests/test_config.py \
  tests/test_runtime_settings_legacy_approval.py
```

- [ ] **Step 3: Remove active provider switching**

`BrainManager.from_config()` constructs exactly one `ClaudeCliAdapter`. Keep a constructor-injected `BrainAdapter` seam for tests without exposing it in product config. Remove list/switch/restore-provider methods and routes. Runtime status emits one typed Claude snapshot:

```python
@dataclass(frozen=True)
class ClaudeRuntimeStatus:
    model: str
    effort: str
    fast: bool
    session_id: str | None
    transport_state: str
    last_error: str | None
```

Delete `scripts/smoke-brain-switch.sh`; the route it exercises no longer exists. The release audit must reject any active installed copy that still invokes `/brain/switch`.

- [ ] **Step 4: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_brain_manager.py tests/test_brain_api.py tests/test_config.py \
  tests/test_runtime_settings_legacy_approval.py tests/test_brain_cli_persistent_session.py
.venv/bin/ruff check dan/brain/manager.py dan/brain/auto_detect.py dan/daemon/app.py \
  dan/api/routes_brain.py dan/api/routes_runtime.py tests/test_brain_manager.py
git diff --check
```

## Task 4.3: Remove provider/mock/disabled product UI

**Ownership precondition:** Fable has handed over `app.js`, `index.html`, `styles.css`, menubar code, and panel tests.

**Files:**

- Modify: `dan/panel/assets/app.js`
- Modify: `dan/panel/assets/index.html`
- Modify: `dan/panel/assets/styles.css`
- Modify: `dan/panel/menubar_app.py` only if it projects provider controls
- Modify: `tests/test_panel_assets.py`
- Modify: `tests/test_panel_operator_api.py`

- [ ] **Step 1: Write RED absence and snapshot tests**

```python
def test_panel_has_no_provider_picker_or_switch_request(panel_assets: PanelAssets) -> None:
    assert "provider-picker" not in panel_assets.html
    assert "/brain/switch" not in panel_assets.javascript
    assert "switchBrain" not in panel_assets.javascript


def test_panel_projects_single_daemon_brain_snapshot(panel_harness: PanelHarness) -> None:
    panel_harness.brain_status(model="claude-opus", effort="max", fast=False, session_id="s1")
    assert panel_harness.brain_fields() == {
        "model": "claude-opus", "effort": "max", "fast": "off", "session": "s1"
    }
```

Add absence tests for mock/dev warnings and disabled-by-policy rows.

- [ ] **Step 2: Verify RED, delete dead UI paths, verify GREEN**

Remove provider picker, switch calls, provider preview evaluator, mock/developer warnings, and disabled controls. Render the daemon-owned Claude snapshot and preserve supported model/effort/fast intent controls.

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_panel_assets.py tests/test_panel_operator_api.py
.venv/bin/ruff check dan/panel/menubar_app.py tests/test_panel_assets.py tests/test_panel_operator_api.py
git diff --check
```

## Task 4.4: Remove mock TTS and recorder modes from the product path

**Files:**

- Modify: `dan/config.py`
- Modify: `dan/config_registry.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/voice/tts.py`
- Modify: `dan/voice/recorder.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/voice.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_voice_tts_supertonic.py`
- Modify: `tests/test_voice_recorder.py`
- Create: `tests/test_daemon_voice_wiring.py`

- [ ] **Step 1: Write RED product-boundary tests**

```python
def test_product_config_rejects_mock_tts_and_recorder(tmp_path: Path) -> None:
    config_path = write_voice_config(tmp_path, default_tts="mock", recorder="mock")
    with pytest.raises(ConfigValidationError, match="test double"):
        load_config(config_path)


def test_product_builders_never_construct_mock_engines() -> None:
    with pytest.raises(TTSEngineError):
        build_tts_engine("mock")
    with pytest.raises(RecorderBackendError):
        build_recorder("mock", config=production_config(), input_device_provider=lambda: None)


def test_daemon_tests_inject_voice_doubles_without_product_config(tmp_path: Path) -> None:
    app = build_daemon_app(
        config=production_voice_config(tmp_path),
        tts_engine=FakeTTSEngine(),
        recorder=FakeRecorder(),
    )
    assert app.voice_engine.name == "fake"
    assert app.voice_recorder.name == "fake"
```

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_config.py tests/test_voice_tts_supertonic.py tests/test_voice_recorder.py \
  tests/test_daemon_voice_wiring.py
```

- [ ] **Step 3: Move doubles out of product configuration**

Set product defaults to `supertonic` and `sox`. Remove `"mock"` branches and exported mock classes from `dan.voice.tts` and `dan.voice.recorder`. Keep test doubles in `tests/fakes/voice.py` and inject them through explicit Python constructor arguments or factories that are absent from TOML, API, CLI, and panel contracts. Reject legacy `voice.default_tts="mock"` or `voice.recorder="mock"` with a migration-specific validation error.

- [ ] **Step 4: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_config.py tests/test_voice_tts_supertonic.py tests/test_voice_recorder.py \
  tests/test_daemon_voice_wiring.py
.venv/bin/ruff check dan/config.py dan/config_registry.py dan/daemon/app.py \
  dan/voice/tts.py dan/voice/recorder.py tests/fakes/voice.py tests/test_daemon_voice_wiring.py
git diff --check
```

## Task 4.5: Package every required release resource exactly once

**Files:**

- Modify: `pyproject.toml`
- Modify: `dan/persona.py`
- Modify: `dan/install/adapters.py`
- Modify: `dan/install/launchd.py`
- Create: `dan/release/package_audit.py`
- Create: `tests/test_release_package.py`
- Modify: `tests/test_voice_assets.py`

- [ ] **Step 1: Write RED artifact-content tests**

```python
def test_wheel_contains_single_canon_voice_assets_integrations_and_launchd(built_wheel: Path) -> None:
    names = wheel_names(built_wheel)
    assert sum(name.endswith("config/persona/DAN.md") for name in names) == 1
    assert required_voice_resources() <= set(names)
    assert required_integration_resources() <= set(names)
    assert "launchd/com.dan.dand.plist.example" in names


def test_installed_default_paths_resolve_inside_artifact(installed_wheel: InstalledWheel) -> None:
    assert installed_wheel.run("from dan.persona import DEFAULT_CANON_PATH; print(DEFAULT_CANON_PATH)").is_inside_site_packages
```

Add sdist/wheel parity and no-`jarvis`/private-files tests.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_package.py tests/test_voice_assets.py
```

Expected: current build backend/module/resource omissions.

- [ ] **Step 3: Configure a single generated resource tree**

Use hatch `force-include` to map the canonical repo files directly into `dan/_release/` at build time; do not create a second checked-in copy. Include:

- `config/persona/DAN.md`;
- `config/voice/**` excluding private samples;
- asset manifest/notices required by the runtime;
- `integrations/**` required by installer adapters;
- `launchd/com.dan.dand.plist.example`.

Resolve installed defaults through `importlib.resources.files("dan").joinpath("_release", ...)`. In a source checkout only, resolve the same canonical files directly from the repository root; never copy them. Fail loudly when neither the installed resource nor the exact source-checkout canon exists. Tests cover both layouts and prove each layout contains exactly one effective canon. `package_audit.py` compares archive entries with the exact allowlist and rejects secrets, private voice material, caches, absolute-user paths, or legacy package names.

- [ ] **Step 4: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_package.py tests/test_voice_assets.py
.venv/bin/ruff check dan/release/package_audit.py dan/persona.py dan/install/adapters.py \
  dan/install/launchd.py tests/test_release_package.py
git diff --check
```

## Task 4.6: Build and install from a hash-locked offline wheelhouse

**Files:**

- Create: `release/wheelhouse-macos-arm64-py311.lock`
- Create: `dan/release/build_gate.py`
- Create: `scripts/dan-wheelhouse-prepare`
- Create: `scripts/dan-release-build-gate`
- Create: `tests/test_release_build_gate.py`
- Modify: `scripts/install.sh`
- Modify: `tests/test_installer_atomicity.py`

- [ ] **Step 1: Write RED lock/build/install tests**

```python
def test_wheelhouse_rejects_missing_or_unhashed_transitive_wheel(tmp_path: Path) -> None:
    wheelhouse, lock = incomplete_wheelhouse(tmp_path)
    with pytest.raises(WheelhouseIntegrityError):
        verify_wheelhouse_manifest(wheelhouse, lock)


def test_wheelhouse_lock_matches_pinned_project_requirements(tmp_path: Path) -> None:
    wheelhouse, lock = complete_wheelhouse(tmp_path)
    requirements = load_pinned_requirements(Path("pyproject.toml"))
    assert verify_wheelhouse_manifest(wheelhouse, lock).projects == requirements


def test_installer_is_no_index_non_editable(install_script: str) -> None:
    assert "--no-index" in install_script
    assert "--find-links" in install_script
    assert "pip install -e" not in install_script
    assert "pip install --upgrade" not in install_script
```

Add wrapper-no-checkout-path and clean-clone build/install/doctor/package-audit tests.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_release_build_gate.py tests/test_installer_atomicity.py
```

- [ ] **Step 3: Implement explicit online preparation and offline consumption**

`scripts/dan-wheelhouse-prepare` is the only network-capable step. It resolves the pinned build/runtime requirements for macOS arm64 Python 3.11, downloads every transitive wheel, and writes a sorted lock containing filename, project, version, tags, size, and SHA-256. Review and commit the lock only when it contains real hashes; no placeholder entries are allowed.

`scripts/dan-release-build-gate` performs, with network disabled:

1. verify every wheel and reject extras;
2. build sdist/wheel with `--no-isolation` using locked build dependencies;
3. install the wheel into an empty venv using `--no-index --find-links`;
4. run import, doctor, package audit, persona doctor, and no-legacy checks;
5. emit a SHA-bound JSON report.

The installer consumes the wheel, never editable source, and stable wrappers contain no checkout path.

- [ ] **Step 4: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_release_build_gate.py tests/test_installer_atomicity.py
.venv/bin/ruff check dan/release/build_gate.py tests/test_release_build_gate.py
git diff --check
```

## Task 4.7: Make active-HOME and asset findings fatal in release audit

**Files:**

- Modify: `dan/release_audit.py`
- Modify: `scripts/dan-release-audit`
- Modify: `tests/test_active_reference_scan.py`
- Modify: `tests/test_release_privacy.py`
- Modify: `tests/test_voice_assets.py`

- [ ] **Step 1: Write RED default-HOME and provenance tests**

```python
def test_cli_defaults_to_active_home(cli: ReleaseAuditCLI, tmp_path: Path) -> None:
    result = cli.run(repo=fixture_repo(tmp_path), env={"HOME": str(tmp_path / "home")})
    assert result.report.scanned_home == str(tmp_path / "home")


def test_active_home_legacy_reference_always_fails(audit_fixture: AuditFixture) -> None:
    audit_fixture.active_hook.write_text("python -m dan_core.say", encoding="utf-8")
    result = audit_fixture.run()
    assert result.exit_code != 0
    assert "active_legacy_reference" in result.finding_codes
```

Add tests loading production roots from the current checkpoint inventory, requiring each released asset row to carry source, recipe, SHA-256, and license decision, and rejecting active installed copies of `smoke-brain-switch.sh` or `smoke-persona-profile.sh` that still invoke removed routes.

- [ ] **Step 2: Verify RED and implement fail-closed defaults**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_active_reference_scan.py tests/test_release_privacy.py tests/test_voice_assets.py
```

Default to `$HOME` and active roots from the SHA-bound Batch 0 checkpoint. Remove the opt-in fatality of `--strict-home`; an active legacy finding is always fatal. Archive roots remain structurally excluded. A narrowly named `--skip-home-for-unit-fixture` may exist only behind a test-only Python API, not the release CLI.

- [ ] **Step 3: Verify GREEN and run full Batch 4 gate**

```bash
env HOME=/private/tmp/dan-batch4-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_panel_operator_api.py tests/test_panel_assets.py \
  tests/test_brain_manager.py tests/test_brain_api.py tests/test_config.py \
  tests/test_runtime_settings_legacy_approval.py tests/test_voice_tts_supertonic.py \
  tests/test_voice_recorder.py tests/test_daemon_voice_wiring.py tests/test_release_package.py \
  tests/test_voice_assets.py tests/test_release_build_gate.py tests/test_installer_atomicity.py \
  tests/test_active_reference_scan.py tests/test_release_privacy.py \
  tests/test_imports.py tests/test_checkout_hygiene.py tests/test_test_safety.py \
  tests/test_audio_execution_guard.py
.venv/bin/ruff check dan/panel dan/brain dan/release dan/release_audit.py tests/test_panel_assets.py \
  tests/test_release_package.py tests/test_release_build_gate.py tests/test_active_reference_scan.py
git diff --check
```

- [ ] **Step 4: Re-run the complete baseline and offline clean-clone gate**

```bash
env HOME=/private/tmp/dan-release1-full-home \
  DAN_TEST_REPORT_HOME=/private/tmp/dan-release1-full-report \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python scripts/dan-test-baseline

env HOME=/private/tmp/dan-release1-build-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python scripts/dan-release-build-gate \
  --repo . \
  --wheelhouse /Users/n1_ozzy/.dan/release/wheelhouse \
  --lock release/wheelhouse-macos-arm64-py311.lock \
  --report /Users/n1_ozzy/.dan/release/build-gate.json
```

Expected: exact full-suite node set GREEN, offline gate GREEN, and reports bound to the same HEAD. Any code change after these reports invalidates them.
