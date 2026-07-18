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
- Batch 2 Task 2.8 owns the non-prompt macOS permission model and responsible-executable reporting. This batch removes fake security selectors but consumes that single Accessibility, Screen Recording, Microphone, and Automation preflight contract; it does not create a second TCC authority or request a prompt.
- The wheel contains one canon and one versioned resource tree. Do not add a second source copy of `DAN.md`.
- Build/release gates are offline. A separate preparation command may download wheels, but its output is hash-locked and reviewed before the gate runs.
- Active HOME findings are fatal by default. Reports store paths, kinds, hashes and finding codes, not private file contents.
- Automated gates reuse the operator-supplied, Batch 0-validated `DAN_RELEASE_EVIDENCE_ROOT` and call the Batch 0 `dan_new_evidence` helper before every RED/GREEN/gate command. Every fixture HOME, runtime directory, pytest temp tree, and report lives under that fresh task root; fixed reusable `/private/tmp/dan-batch4-home` or `/private/tmp/dan-release1-*` roots are forbidden.
- Every release report uses the unchanged versioned SHA-bound `ReleaseEvidenceEnvelope` defined in Batch 0. Raw command output, acceptance text, audio bytes, tokens, and private file contents never enter evidence JSON.
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
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
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
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_panel_operator_api.py tests/test_panel_assets.py
.venv/bin/ruff check tests/test_panel_operator_api.py tests/test_panel_assets.py
git diff --check
```

## Task 4.2: Keep only the persistent Claude product backend and delete provider routes

**Files:**

- Modify: `dan/brain/manager.py`
- Modify: `dan/brain/__init__.py`
- Delete: `dan/brain/auto_detect.py`
- Delete: `dan/brain/codex_cli_adapter.py`
- Delete: `dan/brain/codex_cli_contract.py`
- Delete: `dan/brain/eco_brain_adapter.py`
- Delete: `dan/brain/groq_adapter.py`
- Delete: `dan/brain/mock_adapter.py`
- Delete: `dan/brain/ollama_adapter.py`
- Delete: `dan/brain/openai_adapter.py`
- Delete: `dan/brain/qwen_adapter.py`
- Delete: `dan/brain/test_adapter.py`
- Modify: `dan/brain/context_builder.py`
- Modify: `dan/daemon/app.py`
- Delete: `dan/api/routes_brain.py`
- Modify: `dan/api/__init__.py`
- Modify: `dan/api/routes_runtime.py`
- Modify: `dan/daemon/lifecycle.py`
- Modify: `dan/config.py`
- Delete: `scripts/smoke-brain-switch.sh`
- Delete: `scripts/smoke-e2e-mvp.sh`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/brain.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_brain_manager.py`
- Modify: `tests/test_brain_api.py`
- Modify: `tests/test_brain_cli_adapters.py`
- Modify: `tests/test_brain_cli_streaming.py`
- Delete: `tests/test_brain_new_adapters.py`
- Modify: `tests/test_context_builder.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_api_smoke.py`
- Modify: `tests/test_api_transport_token.py`
- Modify: `tests/test_awaiting_approval_status.py`
- Modify: `tests/test_memory_api.py`
- Modify: `tests/test_no_approval_surface.py`
- Modify: `tests/test_text_turn_pipeline.py`
- Modify: `tests/test_turn_state_consistency.py`
- Modify: `tests/test_voice_turn_gateway.py`
- Modify: `tests/test_runtime_settings_legacy_approval.py`
- Modify: `tests/test_imports.py`
- Modify: `tests/test_smoke_script.py`

- [ ] **Step 1: Write RED single-provider tests**

```python
def test_product_manager_exposes_only_claude_cli(config: DANConfig) -> None:
    manager = BrainManager.from_config(config)
    assert manager.product_adapter_id == "claude_cli"
    assert not hasattr(manager, "switch_product_adapter")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/brain/adapters", None),
        ("GET", "/brain/current", None),
        ("POST", "/brain/switch", {"adapter": "claude_cli"}),
    ],
)
def test_removed_brain_provider_routes_return_404(
    app: DaemonApp,
    method: str,
    path: str,
    body: object | None,
) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(method, f"{base_url}{path}", body)
    assert status == 404
    assert payload["status"] == 404
```

These HTTP tests live in `tests/test_api_smoke.py` and exercise the real `dan/daemon/lifecycle.py::_dispatch`; no `app.router`, route registry, or framework `TestClient` exists. Add tests proving product config and `ContextBuilder` snapshots have no `codex_cli`, `test`, `mock`, `default_adapter`, `brain_adapter`, provider map, or provider-session map, and runtime apply rejects `brain.provider` while retaining Claude model/effort/fast keys. `tests/fakes/brain.py` owns the hermetic `BrainAdapter` used by tests. Remove Codex/mock streaming cases from `tests/test_brain_cli_streaming.py`, retain its Claude persistent-stream coverage, and import any generic deterministic adapter only from `tests/fakes/brain.py`.

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_brain_manager.py tests/test_brain_api.py tests/test_brain_cli_streaming.py \
  tests/test_context_builder.py tests/test_config.py tests/test_api_smoke.py \
  tests/test_runtime_settings_legacy_approval.py tests/test_imports.py tests/test_smoke_script.py
```

Expected: the three provider routes still return `200`, provider adapters remain importable, and product config still accepts at least one removed selector.

- [ ] **Step 3: Remove active provider switching**

`BrainManager.from_config()` constructs exactly one `ClaudeCliAdapter`. Change direct construction to accept exactly one constructor-injected `BrainAdapter` for tests, not an iterable/default-provider map, without exposing that seam in product config. Delete the non-Claude product adapters and auto-detection modules above; tests import their hermetic adapter only from `tests/fakes/brain.py`. Remove adapter list/get-by-name/switch/restore methods and every `adapter_name` parameter from generate, streaming, and session-snapshot paths. Remove the three hard-coded branches plus imports from `dan/daemon/lifecycle.py::_dispatch`. Delete `dan/api/routes_brain.py`; its `register_routes()` was a no-op and never owned HTTP dispatch. Remove `brain_adapter` and provider-session metadata from `ContextBuilder`; model/effort/fast remain ordinary Claude settings. Runtime status emits one typed Claude snapshot:

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

Delete `scripts/smoke-brain-switch.sh` and `scripts/smoke-e2e-mvp.sh`; both encode the removed provider-switch product. The release audit must reject any active installed copy that invokes `/brain/adapters`, `/brain/current`, or `/brain/switch`.

- [ ] **Step 4: Verify GREEN**

```bash
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_brain_manager.py tests/test_brain_api.py tests/test_brain_cli_adapters.py \
  tests/test_brain_cli_streaming.py tests/test_context_builder.py \
  tests/test_config.py tests/test_api_smoke.py tests/test_api_transport_token.py \
  tests/test_runtime_settings_legacy_approval.py tests/test_brain_cli_persistent_session.py \
  tests/test_imports.py tests/test_smoke_script.py
.venv/bin/ruff check dan/brain/manager.py dan/brain/context_builder.py dan/brain/__init__.py \
  dan/daemon/app.py \
  dan/daemon/lifecycle.py dan/api/__init__.py dan/api/routes_runtime.py \
  tests/fakes/brain.py tests/test_brain_manager.py tests/test_brain_cli_streaming.py \
  tests/test_context_builder.py tests/test_api_smoke.py
git diff --check
```

## Task 4.3: Remove provider/mock/disabled product UI

**Ownership precondition:** Fable has handed over `app.js`, `index.html`, `styles.css`, menubar code, and panel tests.

**Files:**

- Modify: `dan/panel/assets/app.js`
- Modify: `dan/panel/assets/index.html`
- Modify: `dan/panel/assets/styles.css`
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

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_panel_assets.py tests/test_panel_operator_api.py
```

Expected: provider picker/switch strings, developer warnings, or disabled-by-policy rows remain in the rendered assets.

- [ ] **Step 3: Delete dead UI paths**

Remove provider picker, switch calls, provider preview evaluator, mock/developer warnings, and disabled controls. Render the daemon-owned Claude snapshot and preserve supported model/effort/fast intent controls.

- [ ] **Step 4: Verify GREEN**

```bash
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_panel_assets.py tests/test_panel_operator_api.py
.venv/bin/ruff check tests/test_panel_assets.py tests/test_panel_operator_api.py
git diff --check
```

## Task 4.4: Remove every product-selectable mock/fake mode

**Files:**

- Modify: `dan/config.py`
- Modify: `dan/config_registry.py`
- Create: `dan/daemon/components.py`
- Modify: `dan/daemon/app.py`
- Modify: `dan/voice/tts.py`
- Modify: `dan/voice/stt.py`
- Modify: `dan/voice/recorder.py`
- Modify: `dan/voice/player.py`
- Modify: `dan/audio/devices.py`
- Modify: `dan/macos/accessibility.py`
- Modify: `dan/macos/screen.py`
- Modify: `dan/macos/terminal.py`
- Modify: `dan/api/routes_voice.py`
- Modify: `dan/api/routes_runtime.py`
- Delete: `dan/workers/mock_worker.py`
- Modify: `dan/workers/__init__.py`
- Modify: `scripts/dan-voice-acceptance`
- Delete: `scripts/smoke-audio-devices.sh`
- Delete: `scripts/smoke-claude-cli-brain.sh`
- Delete: `scripts/smoke-file-read.sh`
- Delete: `scripts/smoke-memory-runtime.sh`
- Delete: `scripts/smoke-screen-read.sh`
- Delete: `scripts/smoke-stream.sh`
- Delete: `scripts/smoke-terminal.sh`
- Delete: `scripts/smoke-text-runtime.sh`
- Delete: `scripts/smoke-tool-continuation.sh`
- Delete: `scripts/smoke-tools-approvals.sh`
- Delete: `scripts/smoke-ui-act.sh`
- Delete: `scripts/smoke-ui-read.sh`
- Delete: `scripts/smoke-voice-listening.sh`
- Delete: `scripts/smoke-voice-recorder.sh`
- Delete: `scripts/smoke-voice-speech.sh`
- Delete: `scripts/smoke-voice-stream.sh`
- Delete: `scripts/smoke-voice-stt.sh`
- Delete: `scripts/smoke-voice-turn.sh`
- Delete: `scripts/smoke-worker-jobs.sh`
- Modify: `docs/DOCS_INDEX.md`
- Modify: `docs/superpowers/plans/2026-07-16-dan-foundation-release-1.md`
- Modify: `docs/MACOS_OPERATOR_CONTRACT.md`
- Modify: `docs/PRODUCT.md`
- Modify: `docs/TURN_PIPELINE.md`
- Modify: `docs/runbooks/ACCESSIBILITY_TCC.md`
- Modify: `docs/runbooks/BRAIN_ADAPTERS.md`
- Modify: `docs/runbooks/E2E_MVP_SMOKE.md`
- Modify: `docs/runbooks/MEMORY_API.md`
- Modify: `docs/runbooks/PANEL_COCKPIT.md`
- Modify: `docs/runbooks/PROVIDER_SMOKE.md`
- Modify: `docs/runbooks/SCREEN_RECORDING_TCC.md`
- Modify: `docs/runbooks/TEXT_RUNTIME_SMOKE.md`
- Modify: `docs/runbooks/TOOLS_AND_APPROVALS.md`
- Modify: `tests/fakes/__init__.py`
- Create: `tests/fakes/voice.py`
- Create: `tests/fakes/audio.py`
- Create: `tests/fakes/security.py`
- Create: `tests/fakes/workers.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_api_smoke.py`
- Modify: `tests/test_voice_api_contract.py`
- Modify: `tests/test_shared_voice_runtime_truth.py`
- Modify: `tests/test_voice_tts_supertonic.py`
- Modify: `tests/test_voice_stt.py`
- Modify: `tests/test_voice_recorder.py`
- Modify: `tests/test_audio_player.py`
- Modify: `tests/test_audio_devices.py`
- Modify: `tests/test_listening_leases.py`
- Modify: `tests/test_voice_turn_gateway.py`
- Modify: `tests/test_voice_capture_gate.py`
- Modify: `tests/test_voice_transcription.py`
- Modify: `tests/test_voice_broker.py`
- Modify: `tests/test_voice_fix04.py`
- Modify: `tests/test_ui_read_tools.py`
- Modify: `tests/test_ui_act_tools.py`
- Modify: `tests/test_screen_tools.py`
- Modify: `tests/test_terminal_tools.py`
- Modify: `tests/test_worker_jobs.py`
- Create: `tests/test_daemon_voice_wiring.py`
- Create: `tests/test_daemon_audio_wiring.py`
- Create: `tests/test_daemon_security_wiring.py`
- Create: `tests/test_current_runbooks.py`
- Modify: `tests/test_smoke_script.py`

**Permission dependency:** Batch 2 Task 2.8 supplies the only `PermissionState`/preflight implementation and reports the stable `~/.dan/bin/dand` wrapper plus resolved installed Python executable. This task removes fake backends but neither requests TCC nor duplicates permission ownership. Tests inject permission probe results and never touch live Accessibility, Screen Recording, Microphone, Automation, audio, or launchd.

- [ ] **Step 1: Write RED product-boundary tests**

```python
@pytest.mark.parametrize(
    "override",
    [
        {"voice.default_tts": "mock"},
        {"voice.default_stt": "mock"},
        {"voice.recorder": "mock"},
        {"audio.backend": "fake"},
        {"security.ui_read_backend": "fake"},
        {"security.ui_act_backend": "fake"},
        {"security.screen_read_backend": "fake"},
        {"security.terminal_backend": "fake"},
    ],
)
def test_product_config_rejects_test_double_selectors(
    tmp_path: Path,
    override: dict[str, str],
) -> None:
    config_path = write_config_overrides(tmp_path, override)
    with pytest.raises(ConfigError, match="removed test-double selector"):
        load_config(config_path)


def test_product_defaults_are_real_backends() -> None:
    config = DANConfig()
    assert config.voice.default_tts == "supertonic"
    assert config.voice.default_stt == "mlx_whisper"
    assert config.voice.recorder == "sox"
    assert config.audio.backend == "native"
    assert config.security.ui_read_backend == "ax"
    assert (config.security.ui_act_backend or config.security.ui_read_backend) == "ax"
    assert config.security.screen_read_backend == "native"
    assert config.security.terminal_backend == "osascript"


def test_product_modules_export_no_test_double_classes() -> None:
    assert not hasattr(dan.voice.tts, "MockTTSEngine")
    assert not hasattr(dan.voice.stt, "MockSTTEngine")
    assert not hasattr(dan.voice.recorder, "MockRecorder")
    assert not hasattr(dan.voice.player, "MockAudioPlayer")
    assert not hasattr(dan.macos.accessibility, "FakeAccessibilityReader")
    assert not hasattr(dan.macos.accessibility, "FakeAccessibilityActor")
    assert not hasattr(dan.macos.screen, "FakeScreenReader")
    assert not hasattr(dan.macos.terminal, "FakeTerminalBridge")


def test_current_runbooks_reference_no_deleted_smoke_or_selector(repo_root: Path) -> None:
    current_runbooks = current_runbook_paths(repo_root / "docs/DOCS_INDEX.md")
    deleted_scripts = deleted_product_smoke_basenames()
    forbidden_config = {
        'default_adapter = "mock"',
        'default_adapter = "codex_cli"',
        'backend = "fake"',
        'default_tts = "mock"',
        'default_stt = "mock"',
        'recorder = "mock"',
    }
    for path in current_runbooks:
        text = path.read_text(encoding="utf-8")
        assert all(script not in text for script in deleted_scripts)
        assert all(fragment not in text for fragment in forbidden_config)
        if "pytest" in text:
            assert "-p tests.audio_guard_plugin" in text

    brain = (repo_root / "docs/runbooks/BRAIN_ADAPTERS.md").read_text(encoding="utf-8")
    provider = (repo_root / "docs/runbooks/PROVIDER_SMOKE.md").read_text(encoding="utf-8")
    assert "persistent Claude CLI" in brain
    assert "persistent Claude CLI" in provider
    assert "codex_cli" not in brain + provider

    index = (repo_root / "docs/DOCS_INDEX.md").read_text(encoding="utf-8")
    assert docs_index_section(index, "Current Reference Docs").isdisjoint(
        {"docs/PRODUCT.md", "docs/TURN_PIPELINE.md"}
    )
    assert {"docs/PRODUCT.md", "docs/TURN_PIPELINE.md"} <= docs_index_section(
        index,
        "Historical/Legacy Docs",
    )
    macos_contract = (repo_root / "docs/MACOS_OPERATOR_CONTRACT.md").read_text(
        encoding="utf-8"
    )
    assert "model-originated tools execute directly" in macos_contract

    original_release_plan = (
        repo_root / "docs/superpowers/plans/2026-07-16-dan-foundation-release-1.md"
    ).read_text(encoding="utf-8")
    assert "SUPERSEDED — DO NOT EXECUTE" in original_release_plan[:1000]
    assert "2026-07-18-release1-audit-remediation.md" in original_release_plan[:1000]
```

Add structural HTTP assertions through `running_server`/`request_json` proving `GET /runtime/settings` and `GET /voice/runtime` contain no selectable `mock`, `fake`, developer/test provider, or audio/security test-backend value. Add a release-script test proving no surviving product script writes any removed selector into TOML.

- [ ] **Step 2: Write RED Python-only override tests**

Define the production seam without production doubles:

```python
@dataclass(frozen=True)
class RuntimeComponentOverrides:
    tts_engine: TTSEngine | None = None
    stt_engine: STTEngine | None = None
    recorder: Recorder | None = None
    audio_player: AudioPlayer | None = None
    audio_devices: AudioDeviceManager | None = None
    ui_reader: AccessibilityReader | None = None
    ui_actor: AccessibilityActor | None = None
    screen_reader: ScreenReader | None = None
    terminal_bridge: TerminalBridge | None = None
```

Add `STTEngine` and `Recorder` protocols in their existing modules. Product callers pass no overrides. Tests use `RuntimeComponentOverrides` with classes imported only from `tests/fakes/voice.py`, `tests/fakes/audio.py`, and `tests/fakes/security.py`:

```python
def test_daemon_accepts_python_only_test_components(config_path: Path) -> None:
    overrides = RuntimeComponentOverrides(
        tts_engine=FakeTTSEngine(),
        stt_engine=FakeSTTEngine(),
        recorder=FakeRecorder(),
        audio_player=FakeAudioPlayer(),
        audio_devices=FakeAudioDeviceManager(),
        ui_reader=FakeAccessibilityReader(),
        ui_actor=FakeAccessibilityActor(),
        screen_reader=FakeScreenReader(),
        terminal_bridge=FakeTerminalBridge(),
    )
    app = create_daemon_app(config_path, component_overrides=overrides)
    assert app.voice_engine is overrides.tts_engine
    assert app.voice_recorder is overrides.recorder
    assert app.voice_player is overrides.audio_player
```

The override type is accepted only by `create_daemon_app(...)` and `create_daemon_app_from_config(...)`; it is absent from TOML, registry keys, CLI flags, environment switches, HTTP payloads, and panel controls.

- [ ] **Step 3: Verify RED**

```bash
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_config.py tests/test_api_smoke.py tests/test_voice_api_contract.py \
  tests/test_voice_tts_supertonic.py tests/test_voice_stt.py tests/test_voice_recorder.py \
  tests/test_audio_player.py tests/test_audio_devices.py tests/test_ui_read_tools.py \
  tests/test_ui_act_tools.py tests/test_screen_tools.py tests/test_terminal_tools.py \
  tests/test_daemon_voice_wiring.py tests/test_daemon_audio_wiring.py \
  tests/test_daemon_security_wiring.py tests/test_current_runbooks.py \
  tests/test_smoke_script.py
```

Expected: at least one product config accepts each removed selector, production factories construct test doubles, and no typed all-component override seam exists.

- [ ] **Step 4: Remove product doubles and migrate hermetic tests**

Remove all mock/fake classes, exports, readiness branches, config parser choices, runtime projections, and daemon construction branches from the files above. Product factories accept only `supertonic`, `mlx_whisper`, `sox`, native CoreAudio/device discovery, AX, native screen capture, and osascript. When voice is disabled, `create_daemon_app_from_config()` must not initialize recorder, microphone, TTS, STT, or playback merely to satisfy a former mock default.

Keep workers disabled with `worker_broker=None`; delete the production `MockWorker` and move its deterministic test behavior to `tests/fakes/workers.py`. Migrate every named test to the typed Python override or direct test fake. Delete the listed mock/fake shell smokes rather than hiding a test mode behind environment variables. `scripts/dan-voice-acceptance` becomes explicit live-only operator tooling: no `--mock` default, no import from `tests`, no speech on import/no-argument/default execution, and no report under active HOME. It may speak only during the explicitly invoked, receipt-bound Task 4.6 flow. Task 4.6 completes its `dan-voice-acceptance:v2` evidence contract.

Update documentation in the same deletion task. `docs/DOCS_INDEX.md` must stop classifying dead operational instructions as current. Add a top-of-file `SUPERSEDED — DO NOT EXECUTE` banner to the original `2026-07-16-dan-foundation-release-1.md` and point only to this remediation execution index; its corrected branch note does not rescue the later old-`jarvis` merge, worktree removal, cutover, deployment, or tag commands. Rewrite `BRAIN_ADAPTERS.md` and `PROVIDER_SMOKE.md` around the one persistent Claude CLI runtime; rewrite the panel, text, memory, Accessibility, and Screen Recording runbooks to use current daemon APIs, guarded tests, and Batch 2 non-prompt permission reporting instead of deleted fake smokes. Mark `E2E_MVP_SMOKE.md` and `TOOLS_AND_APPROVALS.md` visibly historical/superseded and move them from Current Runbooks to Historical/Legacy Docs. The frozen v4.1 `PRODUCT.md` and `TURN_PIPELINE.md` bodies remain evidence, but add a visible superseded banner and move them from Current Reference Docs to Historical/Legacy Docs because they prescribe stateless Claude/Codex/mock brains and approval capture. Add a narrow supersession note to the still-authoritative `MACOS_OPERATOR_CONTRACT.md`: its future operator/TCC boundaries remain useful, but model-originated tools on this branch execute directly and the old approval/awaiting-approval path is historical. Preserve `docs/DECISIONS.md` and `docs/MASTER_PLAN.md` as classified history rather than bulk-rewriting old ADR/plan evidence. No document still classified current may name a deleted script, provider switch, Codex/mock product selector, fake security/audio backend, or model-tool approval execution path without an explicit historical/superseded boundary.

For the real security backends, consume Batch 2's non-prompt tri-state permission report. `denied` and `unknown` remain distinguishable and candidate evidence fails when a required capability lacks `granted`; no code path calls a permission request API.

- [ ] **Step 5: Verify GREEN**

```bash
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_config.py tests/test_api_smoke.py tests/test_voice_api_contract.py \
  tests/test_shared_voice_runtime_truth.py tests/test_voice_tts_supertonic.py \
  tests/test_voice_stt.py tests/test_voice_recorder.py tests/test_audio_player.py \
  tests/test_audio_devices.py tests/test_listening_leases.py tests/test_voice_turn_gateway.py \
  tests/test_voice_capture_gate.py tests/test_voice_transcription.py tests/test_voice_broker.py \
  tests/test_voice_fix04.py tests/test_ui_read_tools.py tests/test_ui_act_tools.py \
  tests/test_screen_tools.py tests/test_terminal_tools.py tests/test_worker_jobs.py \
  tests/test_daemon_voice_wiring.py tests/test_daemon_audio_wiring.py \
  tests/test_daemon_security_wiring.py tests/test_current_runbooks.py \
  tests/test_smoke_script.py
.venv/bin/ruff check dan/config.py dan/config_registry.py dan/daemon/components.py \
  dan/daemon/app.py dan/voice dan/audio/devices.py dan/macos/accessibility.py \
  dan/macos/screen.py dan/macos/terminal.py dan/api/routes_voice.py \
  dan/api/routes_runtime.py tests/fakes tests/test_daemon_voice_wiring.py \
  tests/test_daemon_audio_wiring.py tests/test_daemon_security_wiring.py \
  tests/test_current_runbooks.py
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
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin tests/test_release_package.py tests/test_voice_assets.py
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
dan_new_evidence batch4-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin tests/test_release_package.py tests/test_voice_assets.py
.venv/bin/ruff check dan/release/package_audit.py dan/persona.py dan/install/adapters.py \
  dan/install/launchd.py tests/test_release_package.py
git diff --check
```

## Task 4.6: Build, install, identify, and report from a hash-locked wheelhouse

**Files:**

- Create: `release/wheelhouse-macos-arm64.lock`
- Read-only dependencies from Batch 0: `dan/release/evidence.py` and `dan/release/producer_ids.py`
- Read-only dependencies from Batch 2: `dan/install/manifest.py`, `dan/install/__init__.py`, `dan/install/__main__.py`, `scripts/install.sh`, `dan/daemon/app.py`, and `dan/api/routes_runtime.py`
- Create: `dan/release/deployment_receipt.py`
- Create: `dan/release/build_gate.py`
- Create: `scripts/dan-wheelhouse-prepare`
- Create: `scripts/dan-release-build-gate`
- Modify: `scripts/dan-voice-acceptance`
- Modify: `dan/api/client.py`
- Modify: `tests/test_release_evidence.py`
- Create: `tests/test_release_build_gate.py`
- Create: `tests/test_deployment_receipt.py`
- Create: `tests/test_daemon_client.py`

**Interfaces:**

- Consumes without changing: Batch 0 `ReleaseEvidenceEnvelope` schema version 1 and its `validate_evidence_root`, `canonical_envelope_sha256`, `write_evidence_envelope_exclusive`, and `read_evidence_envelope` functions.
- Consumes without changing: Batch 2 `InstallReleaseInput`, `RuntimeReleaseIdentity`, `load_current_release()`, hash-locked installer CLI, daemon startup loading, and `GET /runtime/startup -> release.commit_sha` projection. This batch must not add another identity module, manifest schema, installer write, or runtime projection.
- Produces the single strict consumer contract for Batch 0 envelope kind `deployment_receipt`, producer `dan-deployment-receipt:v1`; Batch 4 creates only the immutable view and parser/validator, while Batch 5 extends the same module with read-only capture.
- Produces: `dan-release-build-gate:v1` evidence kind `offline_clean_clone_build`.
- Produces after verified deployment receipt: `dan-voice-acceptance:v2` evidence kind `voice_acceptance_m5`.
- Produces only the client-side helper `DaemonClient.runtime_startup()` for the Batch 2 endpoint `GET /runtime/startup -> release.commit_sha`.

- [ ] **Step 1: Write RED shared-envelope and deployment-receipt contract tests**

```python
def test_build_gate_uses_batch0_release_evidence_envelope() -> None:
    envelope = build_gate_envelope_fixture(
        producer_id=RELEASE_BUILD_GATE_PRODUCER_ID,
        kind="offline_clean_clone_build",
        commit_sha="a" * 40,
        artifact_sha256="b" * 64,
    )
    assert RELEASE_BUILD_GATE_PRODUCER_ID == "dan-release-build-gate:v1"
    assert envelope.schema_version == 1
    assert envelope.subject_sha == "a" * 40
    assert envelope.artifact_sha256 == "b" * 64
    assert envelope.status in {"green", "red", "unknown"}
    assert envelope.report_sha256 == canonical_envelope_sha256(envelope)


def test_build_gate_records_hashed_commands_as_input_evidence(
    build_gate_report: ReleaseEvidenceEnvelope,
) -> None:
    roles = {item.role for item in build_gate_report.input_evidence}
    assert {"command-argv", "command-stdout", "command-stderr", "wheelhouse-lock"} <= roles
    assert "commands" in build_gate_report.result
    assert "stdout" not in build_gate_report.result
    assert "stderr" not in build_gate_report.result


def test_deployment_receipt_is_a_strict_batch0_envelope_view(
    deployment_receipt_envelope: ReleaseEvidenceEnvelope,
) -> None:
    receipt = validate_deployment_receipt(deployment_receipt_envelope)
    assert receipt.envelope.kind == "deployment_receipt"
    assert DEPLOYMENT_RECEIPT_PRODUCER_ID == "dan-deployment-receipt:v1"
    assert receipt.envelope.producer_id == DEPLOYMENT_RECEIPT_PRODUCER_ID
    assert receipt.candidate_sha == receipt.envelope.subject_sha
    assert receipt.artifact_sha256 == receipt.envelope.artifact_sha256
    assert receipt.candidate_sha == receipt.installed_release_sha
    assert receipt.candidate_sha == receipt.runtime_release_sha
    assert receipt.deployment_id == receipt.install_id
    assert receipt.deployed_at_utc == receipt.installed_at_utc


def test_deployment_receipt_rejects_unknown_result_key(
    deployment_receipt_envelope: ReleaseEvidenceEnvelope,
) -> None:
    envelope = envelope_with_result_item(
        deployment_receipt_envelope,
        "caller_claimed_green",
        True,
    )
    with pytest.raises(InvalidDeploymentReceipt):
        validate_deployment_receipt(envelope)
```

Do not add a second evidence schema. Put bounded command timestamps/exit codes in `result["commands"]`; put argv/stdout/stderr and source-report hashes in Batch 0 `input_evidence`; use `status`, `finding_codes`, `unknown_evidence`, and `report_sha256` exactly as defined in Batch 0. Do not store raw stdout/stderr, acceptance text, audio, tokens, HOME file contents, or full environment variables.

`dan/release/deployment_receipt.py` defines local kind `DEPLOYMENT_RECEIPT_KIND = "deployment_receipt"`, imports and may re-export `DEPLOYMENT_RECEIPT_PRODUCER_ID` from Batch 0 `dan/release/producer_ids.py`, and defines frozen `DeploymentReceipt`, `validate_deployment_receipt(envelope)`, and `read_deployment_receipt(path, *, evidence_root)`. It must not spell or own a second producer-ID literal. The view retains its source `ReleaseEvidenceEnvelope`, projects `candidate_sha` only from `subject_sha` and `artifact_sha256` only from the non-null envelope field, and parses exactly these result keys with no extras: `deployment_id`, `deployed_at_utc`, `install_id`, `installed_at_utc`, `release_timezone`, `candidate_tag`, `installed_identity_sha256`, `installed_manifest_sha256`, `installed_release_sha`, `runtime_release_sha`, and `login_cycle_id`. Validation requires envelope status `green`, empty findings/unknowns, canonical envelope hash, exact field types, normalized UTC timestamps, `release_timezone == "Europe/Warsaw"`, non-empty deployment/cycle IDs, valid SHA-256 fields, `candidate_tag` matching `dan-v1-foundation-candidate\.[1-9][0-9]*`, `created_at_utc >= deployed_at_utc`, `deployment_id == install_id`, `deployed_at_utc == installed_at_utc`, and equality of candidate, installed-release, and runtime-release SHAs. It also requires exactly one hashed input for each role `candidate-ref`, `installed-identity`, `install-manifest`, `runtime-startup`, and `login-cycle`; the candidate-ref evidence binds that exact tag name and its resolved target. Add named rejection tests for wrong kind/producer/status, missing or extra result keys, duplicate/missing input roles, invalid timestamps/hashes, invalid candidate tag, candidate-ref name/target mismatch, null artifact SHA, `deployment_id != install_id`, `deployed_at_utc != installed_at_utc`, and every identity mismatch. Batch 4 adds no receipt capture function and no receipt CLI.

- [ ] **Step 2: Write RED Batch 2 identity-consumption and client tests**

```python
def test_build_gate_consumes_batch2_installer_identity(
    clean_clone_install: CleanCloneInstall,
) -> None:
    result = clean_clone_install.run()
    assert result.installer_argv.value("--commit-sha") == result.candidate_sha
    assert result.installer_argv.value("--artifact") == str(result.built_wheel)
    assert result.installer_argv.value("--artifact-sha256") == sha256_file(
        result.built_wheel
    )
    assert result.installer_argv.value("--wheelhouse") == str(result.wheelhouse)
    identity = load_current_release(result.home)
    assert identity is not None
    assert identity.commit_sha == result.candidate_sha
    assert identity.artifact_sha256 == sha256_file(result.built_wheel)
    assert identity.install_manifest_sha256 == sha256_file(result.install_manifest)


def test_daemon_client_reads_batch2_runtime_release_projection(
    installed_app: DaemonApp,
) -> None:
    with running_server(installed_app) as base_url:
        payload = DaemonClient(
            base_url,
            token=installed_app.api_token,
        ).runtime_startup()
    identity = installed_app.runtime_release_identity
    assert payload["release"]["commit_sha"] == identity.commit_sha
    assert payload["release"]["artifact_sha256"] == identity.artifact_sha256
```

Do not modify the Batch 2 installer, manifest, `RuntimeReleaseIdentity`, daemon loader, or route. The clean-clone gate passes the final candidate SHA, built artifact path/hash, reviewed wheelhouse, explicit interpreter, and isolated HOME through Batch 2's existing CLI contract, then consumes `load_current_release()` and the runtime projection. If Batch 2 has no explicit trusted input for one of those values, stop and correct the upstream contract instead of deriving a checkout SHA in Batch 4. Add exact consumer tests named `test_build_gate_rejects_missing_batch2_current_release`, `test_build_gate_rejects_batch2_identity_artifact_mismatch`, and `test_voice_acceptance_rejects_batch2_runtime_release_status_unknown`.

Add `DaemonClient.runtime_startup()` as `return self.get("/runtime/startup")`; it consumes the existing token-protected Batch 2 route. It does not add a generic `/runtime` route or project identity itself.

- [ ] **Step 3: Write RED lock/build/live-acceptance tests**

```python
def test_wheelhouse_rejects_missing_or_unhashed_transitive_wheel(tmp_path: Path) -> None:
    wheelhouse, lock = incomplete_wheelhouse(tmp_path)
    with pytest.raises(WheelhouseIntegrityError):
        verify_wheelhouse_manifest(wheelhouse, lock)


def test_wheelhouse_rejects_release_interpreter_mismatch(
    reviewed_wheelhouse: ReviewedWheelhouse,
    other_python: Path,
) -> None:
    with pytest.raises(ReleaseInterpreterMismatch):
        verify_wheelhouse_manifest(
            reviewed_wheelhouse.root,
            reviewed_wheelhouse.lock,
            release_python=other_python,
        )


def test_installer_is_no_index_non_editable(install_script: str) -> None:
    assert "--no-index" in install_script
    assert "--find-links" in install_script
    assert "pip install -e" not in install_script
    assert "pip install --upgrade" not in install_script


def test_voice_acceptance_v2_reads_canonical_json_stdin_not_phrase_argv(
    captured_invocation: CapturedAcceptanceInvocation,
    deployment_receipt_path: Path,
    deployment_receipt: DeploymentReceipt,
) -> None:
    request = voice_acceptance_request_fixture(
        text="M5 live acceptance",
        operator_decision="accept",
    )
    run_voice_acceptance_v2(
        request,
        deployment_receipt_path=deployment_receipt_path,
        captured=captured_invocation,
    )
    assert "M5 live acceptance" not in captured_invocation.argv
    assert captured_invocation.stdin == canonical_json_bytes(request)
    assert VOICE_ACCEPTANCE_PRODUCER_ID == "dan-voice-acceptance:v2"
    assert captured_invocation.report.producer_id == VOICE_ACCEPTANCE_PRODUCER_ID
    assert captured_invocation.report.kind == "voice_acceptance_m5"
    assert captured_invocation.report.subject_sha == deployment_receipt.candidate_sha
    assert captured_invocation.report.artifact_sha256 == deployment_receipt.artifact_sha256
    assert captured_invocation.report.result["deployment_id"] == deployment_receipt.deployment_id
    assert captured_invocation.report.created_at_utc > deployment_receipt.deployed_at_utc
```

Add exact rejection tests named `test_voice_acceptance_rejects_missing_or_unverified_deployment_receipt`, `test_voice_acceptance_rejects_predeployment_timestamp`, `test_voice_acceptance_rejects_non_m5_hardware`, `test_voice_acceptance_rejects_runtime_identity_mismatch`, `test_voice_acceptance_requires_operator_listening_decision`, and `test_voice_acceptance_rejects_mock_request`. Build tests cover wrapper checkout-path absence, clean-clone build/install/doctor/package audit, and exact lock/project parity.

- [ ] **Step 4: Implement producers and strict offline consumption**

`scripts/dan-wheelhouse-prepare` is the only network-capable step. Before writing, it validates the absolute output root with Batch 0 protected-root/symlink checks and requires it below the operator-supplied external evidence root; it refuses an existing wheelhouse or lock output. It requires one operator-supplied absolute `--python` executable and resolves pinned build/runtime requirements for macOS arm64 with that exact interpreter. The generated lock records the interpreter implementation, full version, cache tag, ABI tag, platform tag, and SHA-256 of the resolved interpreter binary before the sorted wheel records containing filename, project, version, tags, size, and SHA-256. It downloads every transitive wheel compatible with that recorded interpreter and rejects sdists. The committed lock is updated only after reviewing the generated diff; no placeholder hashes, inferred minor version, ambient `python`, or PATH lookup is allowed.

`scripts/dan-release-build-gate` runs network-disabled, requires the same absolute `--python`, and imports `RELEASE_BUILD_GATE_PRODUCER_ID` from Batch 0 rather than owning the literal. It emits that producer through `ReleaseEvidenceEnvelope`: revalidate the external wheelhouse root; re-hash the resolved interpreter binary; require every recorded interpreter field to equal values queried from that executable; verify every wheel against the interpreter's supported tags; and reject missing files, extras, sdists, or any interpreter/lock mismatch. Build sdist/wheel with locked build dependencies and `--no-isolation`; create the verifier venv with that interpreter; install the wheel with `--no-index --find-links`; then invoke the unchanged Batch 2 installer CLI against a second isolated HOME using the exact candidate SHA, built artifact/hash, same interpreter, and reviewed wheelhouse. Consume Batch 2 `load_current_release()` and startup projection; do not write identity JSON directly. Run import, doctor, package audit, persona doctor, and no-legacy checks, and bind the report to the exact clean-clone commit, built wheel SHA-256, interpreter metadata hash, and reviewed lock SHA-256.

`scripts/dan-voice-acceptance` implements only the central `VOICE_ACCEPTANCE_PRODUCER_ID` (`dan-voice-acceptance:v2`) imported from Batch 0. It is a post-deployment producer: `--deployment-receipt` and `--evidence-output` are the only control paths in argv, while one canonical JSON object on stdin carries the acceptance text and explicit operator listening decision; neither value may appear in argv. The producer calls `read_deployment_receipt()` rather than parsing receipt JSON itself, requires its own `created_at_utc > receipt.deployed_at_utc`, and verifies `DaemonClient.runtime_startup().release` matches the receipt's candidate commit and installed artifact/runtime hashes. It verifies real arm64 Apple M5 hardware without recording serial identifiers, routes speech only through `dan speak`/the token-authenticated daemon API, and requires the operator listening decision. The envelope uses `subject_sha=receipt.candidate_sha`, `artifact_sha256=receipt.artifact_sha256`, hashes the receipt/runtime response in `input_evidence`, and stores `deployment_id`, hardware-summary SHA, candidate WAV SHA, text SHA, gate-result SHA, playback receipt/event SHA, and decision in `result`—not raw text or audio. It has no `--mock`, test import, direct TTS, `afplay`, or automatic fallback. Candidate evaluation excludes this post-deploy report; Batch 5 observation/final gates require it.

- [ ] **Step 5: Run the explicit online wheelhouse preparation**

This is the only network-capable command and therefore requires its own operator-approved execution window:

```bash
: "${DAN_RELEASE_PYTHON:?set to the reviewed absolute release interpreter}"
test "${DAN_RELEASE_PYTHON#/}" != "$DAN_RELEASE_PYTHON"
test -x "$DAN_RELEASE_PYTHON"
DAN_RELEASE_WHEELHOUSE_ROOT="$(mktemp -d "${DAN_RELEASE_EVIDENCE_ROOT%/}/wheelhouse-prep.XXXXXX")"
scripts/dan-wheelhouse-prepare \
  --project . \
  --platform macos-arm64 \
  --python "$DAN_RELEASE_PYTHON" \
  --output "$DAN_RELEASE_WHEELHOUSE_ROOT/wheelhouse" \
  --lock-output "$DAN_RELEASE_WHEELHOUSE_ROOT/generated-wheelhouse.lock"
diff -u release/wheelhouse-macos-arm64.lock \
  "$DAN_RELEASE_WHEELHOUSE_ROOT/generated-wheelhouse.lock"
```

Expected: the lock describes the exact reviewed release interpreter rather than a guessed Python minor; every required project has one compatible hashed wheel; no sdists or extras exist; and the diff is either empty or explicitly reviewed before the committed lock changes. Record the absolute `DAN_RELEASE_PYTHON` and `DAN_RELEASE_WHEELHOUSE_ROOT` plus the resolved interpreter SHA-256 in the batch handoff. They are immutable input to subsequent offline gates and are not the report/evidence root.

- [ ] **Step 6: Verify GREEN with fresh fixture and evidence roots**

```bash
dan_new_evidence task-4.6-green
: "${DAN_RELEASE_WHEELHOUSE_ROOT:?set to the reviewed external preparation root from Step 5}"
: "${DAN_RELEASE_PYTHON:?set to the exact reviewed absolute release interpreter from Step 5}"
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_release_evidence.py tests/test_release_build_gate.py \
  tests/test_deployment_receipt.py tests/test_daemon_client.py

env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-release-build-gate \
  --repo . \
  --python "$DAN_RELEASE_PYTHON" \
  --wheelhouse "$DAN_RELEASE_WHEELHOUSE_ROOT/wheelhouse" \
  --lock release/wheelhouse-macos-arm64.lock \
  --report "$DAN_TASK_EVIDENCE_ROOT/offline-clean-clone-build.json"

.venv/bin/ruff check dan/release/evidence.py dan/release/deployment_receipt.py \
  dan/release/build_gate.py dan/api/client.py tests/test_release_evidence.py \
  tests/test_release_build_gate.py tests/test_deployment_receipt.py \
  tests/test_daemon_client.py
git diff --check
```

Expected: unit gates GREEN, offline report producer ID `dan-release-build-gate:v1`, report subject equals the clean-clone commit and wheel SHA-256, and no automated output exists under active `~/.dan`.

## Task 4.7: Make active-HOME and asset findings fatal in release audit

**Files:**

- Read: `dan/release/deployment_receipt.py` (strict contract created by Task 4.6)
- Read: `dan/release/producer_ids.py` (central Batch 0 producer constants)
- Read: `dan/api/client.py` (`DaemonClient.runtime_startup()` created by Task 4.6)
- Modify: `dan/release_audit.py`
- Modify: `scripts/dan-release-audit`
- Modify: `tests/test_release_evidence.py`
- Modify: `tests/test_active_reference_scan.py`
- Modify: `tests/test_release_privacy.py`
- Modify: `tests/test_voice_assets.py`

- [ ] **Step 1: Write RED default-HOME and provenance tests**

```python
def test_cli_defaults_to_active_home(
    cli: ReleaseAuditCLI,
    tmp_path: Path,
    deployment_receipt: DeploymentReceipt,
) -> None:
    result = cli.run(
        repo=fixture_repo(tmp_path),
        deployment_receipt=deployment_receipt,
        env={"HOME": str(tmp_path / "home")},
    )
    assert result.report.result["scanned_home"] == str(tmp_path / "home")


def test_active_home_legacy_reference_always_fails(
    audit_fixture: AuditFixture,
    deployment_receipt: DeploymentReceipt,
) -> None:
    audit_fixture.active_hook.write_text("python -m dan_core.say", encoding="utf-8")
    result = audit_fixture.run(deployment_receipt=deployment_receipt)
    assert result.exit_code != 0
    assert "active_legacy_reference" in result.report.finding_codes


def test_release_audit_v2_is_sha_bound_and_written_outside_active_home(
    audit_fixture: AuditFixture,
    evidence_root: Path,
    deployment_receipt: DeploymentReceipt,
) -> None:
    result = audit_fixture.run(
        evidence_root=evidence_root,
        deployment_receipt=deployment_receipt,
    )
    assert RELEASE_AUDIT_PRODUCER_ID == "dan-release-audit:v2"
    assert result.report.producer_id == RELEASE_AUDIT_PRODUCER_ID
    assert result.report.kind == "active_home_release_audit"
    assert result.report.subject_sha == deployment_receipt.candidate_sha
    assert result.report.artifact_sha256 == deployment_receipt.artifact_sha256
    assert result.report.result["deployment_id"] == deployment_receipt.deployment_id
    assert result.report.created_at_utc > deployment_receipt.deployed_at_utc
    assert "deployment-receipt" in {item.role for item in result.report.input_evidence}
    assert result.report_path.is_relative_to(evidence_root)
    assert not result.report_path.is_relative_to(audit_fixture.active_home)


```

Add tests loading production roots from the current checkpoint inventory, requiring each released asset row to carry source, recipe, SHA-256, and license decision, and rejecting active installed copies that:

- invoke `/brain/adapters`, `/brain/current`, `/brain/switch`, or the deleted persona-profile route;
- select `mock`/`fake` brain, TTS, STT, recorder, audio, UI-read, UI-act, screen, terminal, or worker backends;
- contain any shell smoke deleted by Tasks 3.3, 4.2, or 4.4;
- disagree with strict `current-release.json`, install-manifest SHA-256, built artifact SHA-256, or `GET /runtime/startup.release.commit_sha`.

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence task-4.7-red
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_release_evidence.py \
  tests/test_active_reference_scan.py tests/test_release_privacy.py tests/test_voice_assets.py
```

Expected: active legacy findings remain non-fatal or the audit report is unversioned/unbound.

- [ ] **Step 3: Implement `dan-release-audit:v2`**

Default the read-only scan target to `$HOME` plus active roots from the SHA-bound Batch 0 checkpoint. Remove opt-in fatality from `--strict-home`; every active legacy, dead-route, mock/fake selector, asset-provenance, release-identity, or installed-artifact mismatch is fatal. Archive roots remain structurally excluded. A narrowly named `skip_home_for_unit_fixture` may exist only in the test fixture API, not in the CLI.

`scripts/dan-release-audit` is a post-deployment producer and imports central `RELEASE_AUDIT_PRODUCER_ID` (`dan-release-audit:v2`) from Batch 0 rather than owning a literal. It requires the strict verified deployment receipt plus the external validated `DAN_RELEASE_EVIDENCE_ROOT`, calls Task 4.6 `read_deployment_receipt()` rather than defining another parser, rejects missing/unverified receipts and `created_at_utc <= deployed_at_utc`, writes exclusively to the evidence root, and emits the shared `ReleaseEvidenceEnvelope`. Use `subject_sha=receipt.candidate_sha`, `artifact_sha256=receipt.artifact_sha256`, hash the receipt and live `DaemonClient.runtime_startup()` response into `input_evidence`, and record `deployment_id`, `deployed_at_utc`, installed runtime commit/artifact hashes, and scan summary in `result`. The live runtime identity must match the receipt before the scan can be green. The active HOME is scanned but never mutated and never receives the report.

Batch 4 does not run the real active-HOME audit and does not aggregate remediation-batch reports. Batch 5 runs `dan-release-audit:v2` only after verified deployment receipt, excludes it from candidate-intent evaluation, and requires it in observation/final gates. Batch 5 also owns the fixed recipes, final-HEAD reruns, and the four aggregation IDs `dan-release-report:batch1_data_cutover:v1`, `dan-release-report:batch2_runtime_host:v1`, `dan-release-report:batch3_persona_config_voice:v1`, and `dan-release-report:batch4_panel_test_release:v1`. This task only implements and hermetically tests the audit producer through the unchanged Batch 0 envelope.

- [ ] **Step 4: Verify GREEN and run the full Batch 4 unit gate**

```bash
dan_new_evidence task-4.7-green
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_panel_operator_api.py tests/test_panel_assets.py \
  tests/test_brain_manager.py tests/test_brain_api.py tests/test_brain_cli_adapters.py \
  tests/test_brain_cli_streaming.py tests/test_brain_cli_persistent_session.py \
  tests/test_context_builder.py tests/test_config.py \
  tests/test_api_smoke.py tests/test_api_transport_token.py tests/test_awaiting_approval_status.py \
  tests/test_memory_api.py tests/test_no_approval_surface.py tests/test_text_turn_pipeline.py \
  tests/test_turn_state_consistency.py tests/test_runtime_settings_legacy_approval.py \
  tests/test_voice_api_contract.py tests/test_shared_voice_runtime_truth.py \
  tests/test_voice_tts_supertonic.py tests/test_voice_stt.py tests/test_voice_recorder.py \
  tests/test_audio_player.py tests/test_audio_devices.py tests/test_listening_leases.py \
  tests/test_voice_turn_gateway.py tests/test_voice_capture_gate.py \
  tests/test_voice_transcription.py tests/test_voice_broker.py tests/test_voice_fix04.py \
  tests/test_ui_read_tools.py tests/test_ui_act_tools.py tests/test_screen_tools.py \
  tests/test_terminal_tools.py tests/test_worker_jobs.py tests/test_daemon_voice_wiring.py \
  tests/test_daemon_audio_wiring.py tests/test_daemon_security_wiring.py tests/test_smoke_script.py \
  tests/test_current_runbooks.py \
  tests/test_release_package.py tests/test_voice_assets.py tests/test_release_evidence.py \
  tests/test_release_build_gate.py tests/test_deployment_receipt.py \
  tests/test_daemon_client.py \
  tests/test_active_reference_scan.py tests/test_release_privacy.py \
  tests/test_imports.py tests/test_checkout_hygiene.py tests/test_test_safety.py \
  tests/test_audio_execution_guard.py
.venv/bin/ruff check dan/panel dan/brain dan/release dan/release_audit.py tests/test_panel_assets.py \
  tests/test_release_package.py tests/test_release_evidence.py \
  tests/test_release_build_gate.py tests/test_active_reference_scan.py
git diff --check
```

- [ ] **Step 5: Re-run baseline and offline build only**

Do not run `dan-release-audit:v2` or `dan-voice-acceptance:v2` in Batch 4: neither has a verified deployment receipt yet. Batch 5 owns both post-deployment invocations.

```bash
dan_new_evidence task-4.7-final
DAN_TEST_REPORT_HOME="$(mktemp -d "$DAN_TASK_EVIDENCE_ROOT/test-report.XXXXXX")"
: "${DAN_RELEASE_WHEELHOUSE_ROOT:?set to the reviewed external Task 4.6 preparation root}"
: "${DAN_RELEASE_PYTHON:?set to the exact reviewed absolute release interpreter from Task 4.6}"

env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" \
  DAN_TEST_REPORT_HOME="$DAN_TEST_REPORT_HOME" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python scripts/dan-test-baseline

env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" \
  DAN_RELEASE_EVIDENCE_ROOT="$DAN_RELEASE_EVIDENCE_ROOT" \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python scripts/dan-release-build-gate \
  --repo . \
  --python "$DAN_RELEASE_PYTHON" \
  --wheelhouse "$DAN_RELEASE_WHEELHOUSE_ROOT/wheelhouse" \
  --lock release/wheelhouse-macos-arm64.lock \
  --report "$DAN_TASK_EVIDENCE_ROOT/offline-clean-clone-build.json"

```

Expected: baseline GREEN and producer ID exactly `dan-release-build-gate:v1`; the report binds the current clean commit and built artifact and lives under the fresh external evidence root. Any code change invalidates it. Batch 5 reruns it on final HEAD, then runs the post-deployment audit and live M5 acceptance after the verified receipt exists.
