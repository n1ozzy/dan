# DAN Release 1 Audit Remediation — Batch 3 Persona, Configuration, and Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the persistent Claude conversation while applying fresh canon before the next turn, resolve effective configuration through one typed source, eliminate dead voice/persona/external-publisher projections, pin the Żaneta acceptance gate, and make voice cancellation, events, and tombstones atomic.

**Architecture:** The persona hash identifies the exact rendered bootstrap of a persistent Claude transport. A hash change closes that transport and starts a new session before any user text is written; conversation state is reconstructed from persona-free durable history. `ConfigStore` becomes the only typed owner-aware resolver, and runtime APIs project only the daemon's local native voice owners. The Żaneta gate is an isolated, hash-pinned executable with a strict JSON protocol. Cancellation linearizes generation registration, tombstone creation, queue updates, and event insertion around one database transaction before the sole player stop path.

**Tech Stack:** Python 3.11+, Claude CLI stream-json transport, SHA-256, TOML, dataclasses, SQLite transactions, isolated Python `-I`, JSON schema validation, pytest, ruff.

## Global Constraints

- The approved product behavior is one persistent Claude CLI conversation. Do not reintroduce cold-per-turn execution, provider chains, session switching, or a second model.
- After Fable hands off the active instruction files, `AGENTS.md` must state the persistent-transport and pre-input persona-recycle contract. `CLAUDE.md` remains a thin pointer to `AGENTS.md`; it must not restate or weaken that contract.
- `config/persona/DAN.md` is the only character canon. Do not copy, summarize, sanitize, classify, or rewrite it. Missing/empty/invalid canon fails before provider execution.
- The session bootstrap hash covers the exact rendered persona payload, including owner interpolation. Keep the raw canon revision as separate diagnostic evidence.
- On persona drift, do not use `--resume`, do not append the new canon to an old session, and do not replay a checkpoint containing the old persona.
- `ConfigStore` is extended rather than wrapped by a competing resolver. `config/dan.example.toml` is documentation and never an effective runtime source.
- Product code and tests use English identifiers and error contracts. Do not add narrative comments that restate the code; comments are reserved for non-obvious invariants, races, and platform workarounds.
- Persona verification is text-only. Do not start TTS, the voice broker, or audible tests.
- Every automated command reuses the operator-supplied, Batch 0-validated `DAN_RELEASE_EVIDENCE_ROOT` and calls `dan_new_evidence` first. Fixture HOME, runtime, pytest temp files, and reports stay under the fresh task root; never use active `~/.dan`.
- Batch 3 follows Batch 2 by default. A Batch 3 task may overlap in time with Batch 2 only when an explicit ownership map proves the exact files are disjoint.

---

## Task 3.1: Recycle persistent Claude transport before a persona-drift turn

**Files:**

- Modify after explicit Fable handoff: `AGENTS.md`
- Modify after explicit Fable handoff: `CLAUDE.md`
- Modify: `dan/brain/claude_cli_adapter.py`
- Create: `dan/persona_doctor.py`
- Create: `scripts/persona-doctor.sh`
- Modify: `tests/test_brain_cli_persistent_session.py`
- Modify: `tests/test_context_builder.py`
- Modify: `tests/test_persona_assets.py`
- Modify: `tests/test_persona_privacy.py`
- Modify: `tests/test_runtime_persona_projection.py`
- Create: `tests/test_persona_doctor.py`

**Ownership precondition:** Record the Fable handoff fingerprint for `AGENTS.md` and `CLAUDE.md`. If either file changes after handoff and before this task starts, stop and obtain a new handoff. `AGENTS.md` is the active instruction authority; `CLAUDE.md` may only delegate to it.

- [ ] **Step 1: Map the active instruction route before editing**

Confirm both active routes:

```text
CLAUDE.md -> AGENTS.md -> persistent Claude transport contract
config/persona/DAN.md -> render_persona -> ContextBuilder.build_request
-> ClaudeCliAdapter.generate -> _generate_persistent
-> Claude CLI system-prompt argv + stream-json stdin
```

Record current branch, SHA, dirty overlap, active adapter selection, persistent session state path, and any postprocessor. Stop if another agent owns `claude_cli_adapter.py`.

- [ ] **Step 2: Write RED transport-ordering tests**

Use the existing `FakePersistentProcess`, `RecordingFactory`, `request(...)`, `_process`, `_session_id`, `factory.commands`, and `process.stdin.writes` boundaries from `tests/test_brain_cli_persistent_session.py`; do not invent public adapter properties for the test:

```python
def test_persona_change_recycles_transport_before_next_stdin_write(tmp_path: Path) -> None:
    old_process = FakePersistentProcess([[result_line("first")]])
    new_process = FakePersistentProcess([[result_line("second")]])
    factory = RecordingFactory(old_process, new_process)
    adapter = ClaudeCliAdapter(process_factory=factory, state_path=tmp_path / "state.json")

    adapter.generate(request("first", turn_id="t1", persona="canon-v1"))
    old_session_id = adapter._session_id
    adapter.generate(request("second", turn_id="t2", persona="canon-v2"))

    assert old_process.terminated == 1
    assert len(old_process.stdin.writes) == 1
    assert adapter._process is new_process
    assert adapter._session_id != old_session_id
    assert len(new_process.stdin.writes) == 1
    assert "second" in new_process.stdin.writes[0]


def test_persona_change_mints_fresh_spawn_without_resume_or_old_canon(tmp_path: Path) -> None:
    first = FakePersistentProcess([[result_line("first")]])
    second = FakePersistentProcess([[result_line("second")]])
    factory = RecordingFactory(first, second)
    adapter = ClaudeCliAdapter(process_factory=factory, state_path=tmp_path / "state.json")

    adapter.generate(request("first", turn_id="t1", persona="canon-v1"))
    adapter.generate(request("second", turn_id="t2", persona="canon-v2"))

    second_command = factory.commands[1]
    assert "--resume" not in second_command
    assert "--session-id" in second_command
    system_prompt = second_command[second_command.index("--system-prompt") + 1]
    assert system_prompt.startswith("canon-v2")
    assert "canon-v1" not in system_prompt
```

Add:

- `test_same_persona_hash_keeps_incremental_persistent_transport`;
- `test_owner_interpolation_change_triggers_recycle`;
- `test_persona_hash_is_not_updated_when_drift_spawn_fails_before_bootstrap`;
- `test_failed_model_response_keeps_hash_of_successful_new_bootstrap`;
- `test_completed_checkpoint_contains_no_persona_message`;
- `test_restart_with_same_bootstrap_hash_may_resume_durable_session`;
- `test_persona_doctor_checks_the_single_canon_without_provider_or_audio`.

Use these current-boundary failure tests for the two hash cases:

```python
def test_persona_hash_is_not_updated_when_drift_spawn_fails_before_bootstrap(tmp_path: Path) -> None:
    first = FakePersistentProcess([[result_line("first")]])

    class FailSecondSpawnFactory(RecordingFactory):
        def __call__(self, command: list[str]) -> FakePersistentProcess:
            if self.commands:
                self.commands.append(list(command))
                raise OSError("spawn failed")
            return super().__call__(command)

    factory = FailSecondSpawnFactory(first)
    adapter = ClaudeCliAdapter(process_factory=factory, state_path=tmp_path / "state.json")
    adapter.generate(request("first", turn_id="t1", persona="canon-v1"))
    old_hash = adapter._persona_hash

    with pytest.raises(BrainAdapterError, match="failed to run"):
        adapter.generate(request("second", turn_id="t2", persona="canon-v2"))
    assert adapter._persona_hash == old_hash


def test_failed_model_response_keeps_hash_of_successful_new_bootstrap(tmp_path: Path) -> None:
    class FailingGenerationProcess(FakePersistentProcess):
        def accept_message(self, _value: str) -> None:
            raise BrainAdapterError("model rejected")

    first = FakePersistentProcess([[result_line("first")]])
    second = FailingGenerationProcess([])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(first, second),
        state_path=tmp_path / "state.json",
    )
    adapter.generate(request("first", turn_id="t1", persona="canon-v1"))

    with pytest.raises(BrainAdapterError, match="model rejected"):
        adapter.generate(request("second", turn_id="t2", persona="canon-v2"))
    assert adapter._persona_hash == hashlib.sha256(b"canon-v2").hexdigest()
```

Add an active-instruction contract test to `tests/test_persona_doctor.py`:

```python
def test_active_agent_instructions_require_persistent_transport_and_pre_input_recycle(repo_root: Path) -> None:
    agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    claude = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "persistent Claude CLI" in agents
    assert "before the next input" in agents
    assert "persona" in agents and "recycle" in agents
    assert "cold Claude CLI" not in agents
    assert claude.count("AGENTS.md") == 1
    assert "config/" not in claude
    assert "- " not in claude
    assert "persistent Claude CLI" not in claude
    assert "cold Claude CLI" not in claude
```

- [ ] **Step 3: Verify RED**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin tests/test_brain_cli_persistent_session.py
```

Expected: the current code fails because the second input is written to the old process and no second fresh spawn exists.

- [ ] **Step 4: Implement pre-send drift handling**

At the start of `_generate_persistent`, compare `_request_persona_hash(request)` with `_persona_hash` before choosing durable resume or calling `_send_generation`. On drift, call the existing `_stop_process()`, mint a new `_session_id`, reset generation/conversation resume state, and call the existing `_start_process(request, resume=False)`. The fresh canon is delivered by `_start_process` through `--system-prompt`; user text remains stream-json stdin. Never use `--resume` for a drifted hash.

Set `_persona_hash` immediately after `_start_process(request, resume=False)` returns successfully, because that successful spawn establishes the exact system-prompt bootstrap. A spawn failure leaves the previous hash unchanged. A later model-response failure does not erase the hash of the successfully bootstrapped replacement session. Remove the current response-tail assignment at the end of `_generate_persistent`.

Change `_format_completed_checkpoint()` so it serializes only completed user, assistant, and tool evidence; it must never call `format_cli_user_prompt(request)` or include any `kind=persona` message. The new transport receives current canon in its bootstrap and current conversation evidence separately.

Add a frozen `PersonaDoctorReport` with exact fields `canon_path: str`, `canon_version: str`, `rendered_sha256: str`, `active_routes: tuple[str, ...]`, and `errors: tuple[str, ...]`. Add the exact entry point `inspect_persona_route(*, repo: Path, owner_path: Path) -> PersonaDoctorReport`.

The doctor verifies that the sole canon exists and has a valid version, renders it with an isolated owner fixture, builds a real `ContextBuilder` request, and proves the exact rendered canon is the first persona/system payload. It scans active route configuration for a second canon, sanitizer, rewriter, or tame fallback. It never starts Claude, TTS, audio, or runtime. `scripts/persona-doctor.sh` is a thin wrapper around `.venv/bin/python -m dan.persona_doctor`.

- [ ] **Step 5: Verify GREEN and persona invariants**

Before editing `AGENTS.md` or `CLAUDE.md`, re-check the recorded Fable fingerprint. Add one short branch-contract bullet to `AGENTS.md` stating one persistent Claude CLI transport and mandatory recycle before the next input when the freshly rendered persona hash changes. Reduce `CLAUDE.md` to its heading plus one pointer to `AGENTS.md`; it must contain no operational bullet, config path, persona copy, or transport wording. Do not copy persona content into either file.

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_brain_cli_persistent_session.py tests/test_context_builder.py \
  tests/test_persona_assets.py tests/test_persona_privacy.py \
  tests/test_runtime_persona_projection.py tests/test_persona_doctor.py
zsh scripts/persona-doctor.sh
.venv/bin/ruff check dan/brain/claude_cli_adapter.py dan/persona_doctor.py \
  tests/test_brain_cli_persistent_session.py tests/test_persona_doctor.py
git diff --check
```

Expected: persistent reuse on equal hash, pre-send recycle on drift, no old canon replay, and persona doctor GREEN without audio.

## Task 3.2: Resolve effective configuration through one typed ConfigStore

**Files:**

- Modify: `dan/config_registry.py`
- Modify: `dan/config.py`
- Modify: `dan/voice/resolver.py`
- Modify: `dan/api/routes_settings.py`
- Modify: `dan/daemon/lifecycle.py`
- Modify: `dan/daemon/app.py`
- Modify: `tests/test_config_registry.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli_config.py`
- Modify: `tests/test_api_smoke.py`

- [ ] **Step 1: Write RED source/owner/type tests**

```python
def test_example_config_cannot_be_effective_runtime_source(store: ConfigStore) -> None:
    explained = store.explain("voice.personas")
    assert explained.source != "config/dan.example.toml"
    assert explained.owner == "voice_catalog"


def test_load_explain_and_http_settings_share_value_owner_revision(app: DaemonApp) -> None:
    store = ConfigStore(app.config.source_path)
    explained = store.explain("brain.model")
    with running_server(app) as base_url:
        status, payload = request_json("GET", f"{base_url}/settings")
        runtime_status, runtime_payload = request_json("GET", f"{base_url}/runtime/settings")

    routed = payload["settings"]["brain.model"]
    assert status == runtime_status == 200
    assert app.config.brain.default_model == explained.value == routed["value"]
    assert routed["owner"] == explained.owner
    assert routed["revision"] == explained.revision
    assert runtime_payload["revision"] == explained.revision
```

Add wrong-owner and wrong-type TOML rejection tests. The HTTP tests belong in `tests/test_api_smoke.py` and must use its real `app`, `running_server`, and `request_json` helpers, which exercise `dan/daemon/lifecycle.py::_dispatch`. Do not introduce a `TestClient`, `runtime_fixture.client`, or framework router abstraction.

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_config_registry.py tests/test_config.py tests/test_cli_config.py tests/test_api_smoke.py
```

- [ ] **Step 3: Implement one typed snapshot**

```python
@dataclass(frozen=True)
class EffectiveConfigSnapshot:
    values: Mapping[str, object]
    explanations: Mapping[str, ConfigExplanation]
    revision: str


class VoiceCatalog:
    def config_projection(self) -> Mapping[str, object]:
        return {
            "voice.personas": self.persona_projection(),
            "voice.mastering": self.mastering_projection(),
            "voice.speeds": self.speed_projection(),
            "voice.pronunciations": self.pronunciation_projection(),
            "voice.default_engine": self.default_engine,
        }
```

`ConfigStore.resolve()` parses and validates installation-owned keys with the same registry parsers used by `explain()`. It merges voice-catalog-owned projections from `config/voice/`, rejects owner violations, and returns the snapshot consumed by `load_config`, daemon startup, CLI explain, `GET /settings`, and `GET /runtime/settings`. Keep the HTTP dispatch in the existing `dan/daemon/lifecycle.py::_dispatch`; `dan/api/routes_settings.py` remains the payload/business-logic module and its `register_routes()` no-op is not a router.

- [ ] **Step 4: Verify GREEN and compatibility errors**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_config_registry.py tests/test_config.py tests/test_cli_config.py tests/test_api_smoke.py
.venv/bin/ruff check dan/config_registry.py dan/config.py dan/voice/resolver.py \
  dan/api/routes_settings.py dan/daemon/lifecycle.py tests/test_config_registry.py \
  tests/test_api_smoke.py
git diff --check
```

## Task 3.3: Remove dead persona/playback keys from the backend contract

**Files:**

- Modify: `dan/config.py`
- Modify: `dan/config_registry.py`
- Modify: `dan/brain/context_builder.py`
- Modify: `dan/voice/player.py`
- Modify: `dan/api/routes_runtime.py`
- Modify: `dan/api/routes_voice.py`
- Modify: `config/dan.example.toml`
- Delete: `scripts/smoke-persona-profile.sh`
- Modify: `tests/test_config_registry.py`
- Modify: `tests/test_api_smoke.py`
- Modify: `tests/test_voice_api_contract.py`
- Modify: `tests/test_shared_voice_runtime_truth.py`
- Modify: `tests/test_audio_player.py`

Do not edit panel assets in this task; their projection removal belongs to Batch 4 after Fable ownership transfer.

**Dependency:** Task 3.3 starts only after Batch 2 Task 2.1 is GREEN because its readiness contract consumes `ChildSupervisor.status("supertonic")`.

- [ ] **Step 1: Write RED backend-contract tests**

```python
def _json_key_paths(value: object, prefix: tuple[str, ...] = ()) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = (*prefix, key)
            paths.add(".".join(path))
            paths.update(_json_key_paths(child, path))
    elif isinstance(value, list):
        for child in value:
            paths.update(_json_key_paths(child, prefix))
    return paths


def test_runtime_and_voice_contracts_have_no_dead_persona_or_playback_keys(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        runtime_status, runtime_payload = request_json("GET", f"{base_url}/runtime/settings")
        voice_status, voice_payload = request_json("GET", f"{base_url}/voice/runtime")

    assert runtime_status == voice_status == 200
    forbidden = {
        "persona.profile",
        "persona_profile",
        "voice.playback_binary",
        "playback_binary",
    }
    assert forbidden.isdisjoint(_json_key_paths(runtime_payload))
    assert forbidden.isdisjoint(_json_key_paths(voice_payload))


@pytest.mark.parametrize("key", ["persona.profile", "voice.playback_binary"])
def test_config_store_rejects_removed_key(config_path: Path, key: str) -> None:
    with pytest.raises(ConfigWriteRejected, match="removed configuration key"):
        ConfigStore(config_path).set_many({key: "legacy"})


def test_playback_readiness_uses_live_player_and_supervised_tts(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        runtime_status, runtime_payload = request_json("GET", f"{base_url}/runtime/settings")
        voice_status, voice_payload = request_json("GET", f"{base_url}/voice/runtime")
    assert runtime_status == voice_status == 200
    playback = voice_payload["voice_runtime"]["groups"]["playback"]
    assert playback["effective"]["broker"] == type(app.voice_broker).__name__
    assert playback["effective"]["publisher_mode"] == "local"
    assert runtime_payload["runtime"]["children"] == app.snapshot_state()["children"]


def test_voice_runtime_has_only_local_native_publisher_truth(app: DaemonApp) -> None:
    app.voice_cancellation = None
    with running_server(app) as base_url:
        runtime_status, runtime_payload = request_json("GET", f"{base_url}/runtime/settings")
        voice_status, voice_payload = request_json("GET", f"{base_url}/voice/runtime")
    assert runtime_status == voice_status == 200
    encoded = json.dumps([runtime_payload, voice_payload], ensure_ascii=False)
    assert "external_shared" not in encoded
    assert "External shared" not in encoded
    assert _recursive_values_for_key(voice_payload, "publisher_mode") == {"local"}
    warning = _warning(runtime_payload, "barge_in_cancel_unavailable")
    assert "local" in warning["reason"].lower()
    assert "shared" not in json.dumps(warning).lower()


def test_daemon_and_routes_have_no_voice_publisher_contract(app: DaemonApp) -> None:
    assert not hasattr(app, "voice_publisher")
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/voice/speak",
            {"text": "Lokalna ścieżka.", "session": "api-contract"},
        )
    assert status == 201
    assert payload["status"] == "queued"
```

The HTTP fixtures mock TTS/audio at the external edge. `_recursive_values_for_key()` walks dictionaries and lists and returns only values for the exact key; `_warning()` selects the existing warning by ID. Add a source-contract test over `dan/daemon/app.py`, `dan/api/routes_voice.py`, and `dan/api/routes_runtime.py` rejecting the identifier `voice_publisher`, so a dead attribute cannot be reintroduced behind an unexercised route.

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_config_registry.py tests/test_api_smoke.py \
  tests/test_voice_api_contract.py tests/test_shared_voice_runtime_truth.py \
  tests/test_audio_player.py
```

Expected: at least one removed key remains in a structural HTTP projection or is still accepted by `ConfigStore`; the cancellation-unavailable branch still emits the false `External shared playback` warning even though the daemon owns a local broker.

- [ ] **Step 3: Remove active projections**

Remove `persona.profile` from config/context/runtime contracts. Persona selection is not configurable; `ContextBuilder` always uses the sole canon loader. Remove `voice.playback_binary`; expose a typed `CoreAudioPlayer.status()` and Batch 2 child status instead. Reject deprecated keys with a stable migration error instead of silently accepting them.

Delete the obsolete `scripts/smoke-persona-profile.sh`; its route no longer exists. Replace its release purpose with `scripts/persona-doctor.sh` and ensure the active-reference audit rejects any installed copy still invoking the deleted persona-profile route.

Remove the remaining external/shared-publisher fiction from runtime warning generation. `DaemonApp` has only `voice_service`, `voice_broker`, `voice_player`, and `voice_cancellation`; no route or projection may read `voice_publisher`, return `external_shared`, or describe local native playback as publication to a shared broker. Every `publisher_mode` value is exactly `local`. When the cancellation coordinator is unavailable, report that local cancellation is unavailable and keep acknowledgement/playback truth derived from the local queue/player lifecycle; do not claim the local broker lacks its own acknowledgement contract.

- [ ] **Step 4: Verify GREEN**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_config_registry.py tests/test_context_builder.py tests/test_api_smoke.py \
  tests/test_voice_api_contract.py tests/test_shared_voice_runtime_truth.py \
  tests/test_audio_player.py
zsh scripts/persona-doctor.sh
.venv/bin/ruff check dan/config.py dan/config_registry.py dan/brain/context_builder.py \
  dan/voice/player.py dan/api/routes_runtime.py dan/api/routes_voice.py
git diff --check
```

## Task 3.4: Pin the Żaneta acceptance-gate artifact identity

**Files:**

- Modify: `config/voice/pipelines/chatterbox-v3-zaneta.toml`
- Modify: `dan/voice/pipelines/chatterbox_v3.py`
- Modify: `tests/test_chatterbox_v3_pipeline.py`

**Precondition:** The operator supplies the exact gate artifact intended for acceptance on the release Mac. Here `M5` means the physical Apple-silicon hardware profile used for release evidence, not an engine, model, or voice identifier. If the artifact is absent, stop this task with a visible blocked finding; do not fabricate a scorer or weaken the release gate.

Before editing, record `git status --short -- config/voice/pipelines/chatterbox-v3-zaneta.toml dan/voice/pipelines/chatterbox_v3.py tests/test_chatterbox_v3_pipeline.py`. Any dirty path without an explicit owner handoff is `STOP`.

- [ ] **Step 1: Record provenance outside private content**

Compute and review:

```bash
test -n "$DAN_ZANETA_ACCEPTANCE_GATE"
test -f "$DAN_ZANETA_ACCEPTANCE_GATE"
shasum -a 256 "$DAN_ZANETA_ACCEPTANCE_GATE"
```

Add these manifest fields, plus a `sha256` field containing the exact 64-character lowercase value printed by `shasum`:

```toml
[acceptance.gate]
logical_path = "zaneta/acceptance-gate-v1.py"
version = 1
result_schema_version = 1
```

The implementation diff must contain the reviewed real hash. An omitted, empty, example, or all-zero hash fails manifest loading and cannot be committed.

- [ ] **Step 2: Write RED identity tests**

```python
def test_runtime_rejects_acceptance_gate_with_wrong_bytes(manifest: PipelineManifest) -> None:
    manifest.acceptance_gate.write_text("changed", encoding="utf-8")
    with pytest.raises(PipelineCapabilityError, match="acceptance gate SHA-256"):
        verify_pinned_runtime(manifest)


def test_runtime_rejects_same_bytes_at_wrong_logical_identity(manifest: PipelineManifest) -> None:
    moved = manifest.acceptance_gate.parent / "renamed.py"
    moved.write_bytes(manifest.acceptance_gate.read_bytes())
    with pytest.raises(PipelineCapabilityError, match="logical path"):
        verify_pinned_runtime(replace(manifest, acceptance_gate=moved))
```

Add manifest version/hash-format tests.

- [ ] **Step 3: Verify RED**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin tests/test_chatterbox_v3_pipeline.py
```

Expected: the current runtime accepts at least one wrong-byte, wrong-logical-path, symlink, or malformed-hash fixture for a finding-specific failure.

- [ ] **Step 4: Implement regular-file, logical-path, and hash checks**

Resolve the environment-provided gate path, reject symlinks and non-regular files, require its exact `zaneta/acceptance-gate-v1.py` logical identity, and compare bytes with `hmac.compare_digest(actual_sha, expected_sha)`. Reject omitted, empty, example, uppercase, non-hex, all-zero, or wrong-length hashes during manifest loading.

- [ ] **Step 5: Verify GREEN**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin tests/test_chatterbox_v3_pipeline.py
.venv/bin/ruff check dan/voice/pipelines/chatterbox_v3.py tests/test_chatterbox_v3_pipeline.py
git diff --check
```

## Task 3.5: Require an isolated structured Żaneta result

**Files:**

- Modify: `dan/voice/pipelines/chatterbox_v3.py`
- Modify: `tests/test_chatterbox_v3_pipeline.py`

**External boundary:** The approved gate artifact remains read-only in this repository task. If it does not already implement the versioned stdin/stdout protocol below, stop with a protocol-mismatch finding and open a separately owned plan in the artifact's source repository; do not patch an unknown external path from this plan.

- [ ] **Step 1: Write hostile RED protocol tests**

```python
def test_ratio_text_alone_can_never_accept_candidate(manifest: PipelineManifest) -> None:
    fake_gate_stdout(manifest, "RATIO 1.0\n")
    with pytest.raises(PipelineCapabilityError, match="JSON"):
        run_acceptance_gate(candidate_wav(), "test", manifest)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01])
def test_structured_score_must_be_finite_and_in_unit_interval(score: float) -> None:
    with pytest.raises(PipelineCapabilityError):
        parse_acceptance_result(result_payload(score=score), expected_fixture())
```

Add tests for exact candidate SHA, text SHA, gate version, schema version, single JSON object, isolated `-I`, minimal environment, and no inherited token/config variables.

Add a capture test proving the text is absent from process arguments and the gate receives exactly one canonical JSON request on stdin:

```python
def test_gate_receives_text_only_through_canonical_json_stdin(
    captured_invocation: CapturedGateInvocation,
    manifest: PipelineManifest,
) -> None:
    text = "Żaneta acceptance phrase"
    candidate = candidate_wav()
    run_acceptance_gate(candidate, text, manifest)
    assert text not in captured_invocation.argv
    assert captured_invocation.argv == [
        str(manifest.python_executable),
        "-I",
        str(manifest.acceptance_gate),
        str(candidate),
    ]
    assert captured_invocation.stdin == canonical_gate_request(
        schema_version=1,
        text=text,
        text_sha256=sha256_text(text),
        candidate_wav_sha256=sha256_file(candidate),
    )
```

- [ ] **Step 2: Implement the exact result contract**

```json
{
  "schema_version": 1,
  "gate_version": 1,
  "candidate_wav_sha256": "64 lowercase hex characters",
  "text_sha256": "64 lowercase hex characters",
  "score": 0.94
}
```

Run with candidate path only in argv and the canonical request on stdin:

```python
request_payload = {
    "schema_version": 1,
    "candidate_wav_sha256": sha256_file(candidate),
    "text": text,
    "text_sha256": sha256_text(text),
}
request_bytes = (
    json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    + "\n"
).encode("utf-8")
argv = [str(manifest.python_executable), "-I", str(manifest.acceptance_gate), str(candidate)]
env = {
    "PATH": str(manifest.python_executable.parent),
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "PYTHONNOUSERSITE": "1",
}
completed = subprocess.run(
    argv,
    input=request_bytes,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    check=False,
)
```

Parse exactly one JSON object, reject extra stdout, booleans as numbers, non-finite/out-of-range scores, and any provenance mismatch.

- [ ] **Step 3: Verify GREEN**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin tests/test_chatterbox_v3_pipeline.py
.venv/bin/ruff check dan/voice/pipelines/chatterbox_v3.py tests/test_chatterbox_v3_pipeline.py
git diff --check
```

Automated GREEN proves protocol integrity, not audible quality. Batch 4 Task 4.6 implements the post-deployment live `dan-voice-acceptance:v2` producer with the shared Batch 0 `ReleaseEvidenceEnvelope`. It consumes the strict verified deployment receipt and binds deployment ID/time, actual M5 hardware identity, candidate commit SHA, installed artifact/runtime SHA, candidate WAV SHA, text SHA, gate-result SHA, playback receipt/event SHA, and the operator listening decision. It stores no raw phrase or audio bytes. A mock run can never produce `voice_acceptance_m5` evidence, and candidate evaluation does not consume this post-deploy report.

## Task 3.6: Make cancellation, events, and tombstones one atomic boundary

**Files:**

- Modify: `dan/brain/claude_cli_adapter.py`
- Modify: `dan/voice/cancellation.py`
- Modify: `dan/voice/queue.py`
- Modify: `dan/store/event_store.py`
- Modify: `tests/test_brain_cli_streaming.py`
- Modify: `tests/test_brain_cli_persistent_session.py`
- Modify: `tests/test_voice_cancellation.py`
- Modify: `tests/test_voice_queue.py`
- Modify: `tests/test_event_store.py`

`dan/voice/service.py` is explicitly out of scope: its current `cancel_session()` only delegates to `VoiceQueue.cancel_session()` and does not publish cancellation events.

- [ ] **Step 1: Write RED concurrency and event-set tests**

```python
def test_cancel_events_equal_exact_returned_request_ids(queue: VoiceQueue) -> None:
    ids = enqueue_session_fixture(queue, session_id="s1", count=3)
    returned = queue.cancel_session("s1")
    events = queue.events(kind="cancelled", session_id="s1")
    assert set(returned) == set(ids) == {event.request_id for event in events}


def test_concurrent_enqueue_cancelled_in_database_is_always_returned(queue_factory: QueueFactory) -> None:
    outcome = run_interleaved_enqueue_and_cancel(queue_factory)
    cancelled_in_db = set(outcome.rows_with_state("cancelled"))
    assert cancelled_in_db == set(outcome.returned_ids)


def test_cancel_failure_rolls_back_queue_state_and_events(queue: VoiceQueue) -> None:
    request_ids = enqueue_session_fixture(queue, session_id="s1", count=2)
    queue.fail_event_insert_after_update()
    with pytest.raises(sqlite3.DatabaseError):
        queue.cancel_session("s1")
    assert queue.states(request_ids) == ("queued", "queued")
    assert queue.events(kind="cancelled", session_id="s1") == ()


def test_cancel_request_event_failure_rolls_back_public_cancel(
    queue: VoiceQueue,
) -> None:
    request = enqueue_session_fixture(queue, session_id="s1", count=1)[0]
    queue.fail_event_insert_after_update()
    with pytest.raises(sqlite3.DatabaseError):
        queue.cancel_request(request)
    assert queue.states([request]) == ("queued",)
    assert queue.events(kind="cancelled", session_id="s1") == ()


def test_cancel_rejects_event_store_on_different_connection(
    queue_connection: sqlite3.Connection,
    other_connection: sqlite3.Connection,
) -> None:
    queue = VoiceQueue(queue_connection, event_store=EventStore(other_connection))
    with pytest.raises(VoiceQueueError, match="same SQLite connection"):
        queue.cancel_session("s1")


def test_enqueue_between_cancel_selection_and_tombstone_cannot_survive(
    cancellation_race: CancellationRaceFixture,
) -> None:
    cancellation_race.pause_after_active_rows_selected()
    cancelling = cancellation_race.start_cancel(turn_id="turn-race")
    enqueueing = cancellation_race.start_enqueue(turn_id="turn-race")
    cancellation_race.release_cancel()
    cancelling.join(timeout=2)
    enqueueing.join(timeout=2)
    assert cancellation_race.active_rows("turn-race") == ()
    assert cancellation_race.is_tombstoned("turn-race") is True
    assert cancellation_race.enqueue_outcome in {"cancelled", "rejected"}


def test_generation_register_racing_cancel_linearizes_once(
    generation_cancel_race: GenerationCancelRace,
) -> None:
    outcome = generation_cancel_race.run(turn_id="turn-generation-race")
    assert outcome.cancel_calls == 1
    assert outcome.tombstoned is True
    assert outcome.active_queue_rows == ()


def test_stale_generation_unregister_cannot_delete_replacement_handle(
    registry: GenerationRegistry,
) -> None:
    first = registry.register("same-turn", lambda: None)
    registry.cancel_all()
    second_cancelled = Mock()
    second = registry.register("same-turn", second_cancelled)
    registry.unregister(first)
    assert registry.registration_active(second) is True
    registry.cancel_all()
    second_cancelled.assert_called_once_with()
```

- [ ] **Step 2: Verify RED**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_brain_cli_streaming.py tests/test_brain_cli_persistent_session.py \
  tests/test_voice_cancellation.py tests/test_voice_queue.py tests/test_event_store.py
```

Expected: the race fixture shows selection can precede `BEGIN IMMEDIATE`, or the injected event insert failure leaves cancelled queue rows committed. The coordinator race reproduces the current two-transaction hole: a row enqueued after `_cancel_queued()` but before `_tombstone_turns()` remains `queued` even though the turn ends tombstoned.

- [ ] **Step 3: Implement one queue/event transaction**

Add an event-store primitive that never opens or commits its own transaction:

```python
def append_in_transaction(
    self,
    type: str,
    source: str,
    payload: Mapping[str, Any],
    correlation_id: str | None = None,
    turn_id: str | None = None,
) -> Event:
    if not self._conn.in_transaction:
        raise EventStoreError("append_in_transaction requires an active transaction")
    return self._insert_event(type, source, payload, correlation_id, turn_id)
```

Expose `EventStore.connection` as a read-only identity check. Refactor `EventStore.append()` to own `with self._conn:` and delegate the insert to the same private `_insert_event(...)`. Both `VoiceQueue.cancel_session()` and the public-API-backed `cancel_request()` must call `_begin_immediate()` before selecting their rows, resolve a real `EventStore(self._conn)` when no store was injected, and reject an injected store whose `connection is not self._conn` or which lacks the transactional primitive. Update only the selected active set, call `event_store.append_in_transaction(...)` once for every actually updated request ID, and commit once. The no-row path must also end the `BEGIN IMMEDIATE` transaction before returning. Any select, update, or event error rolls back both tables. Do not publish cancellation events after commit. Preserve the existing route status/response contracts; this task changes durability, not HTTP shape.

Add one queue-owned primitive used by `CancellationCoordinator` which, under a single `BEGIN IMMEDIATE`, selects the complete active set, inserts or refreshes `cancelled_turns` for the union of active session IDs and generation turn IDs, updates every selected queue row, and appends the exact cancellation event set before one commit. Tombstone insert, queue transition, returned request IDs, and event rows are one transaction; there is no coordinator-side `_cancel_queued()` followed by a second `_tombstone_turns()` connection. When it returns, no `queued`, `synthesizing`, or `speaking` row from the cancelled set may exist.

Make `GenerationRegistry` and `CancellationCoordinator` expose one linearization boundary for a registration racing cancellation. `register()` returns an opaque `GenerationRegistration` token containing a unique generation ID; `unregister()` accepts only that token and removes a handle only when the token still owns the turn slot. Update both stateless/streaming and persistent Claude paths to retain and unregister their exact token. A stale `finally` from cancelled generation A must never delete replacement generation B registered under the same turn ID.

A registration ordered before the cancellation boundary has its handle fired exactly once and its turn ID included in the same tombstone transaction; a registration ordered after it belongs to the next generation epoch. Hold the registry's cancelling epoch until the queue transaction has captured every ID admitted to that epoch. A competing enqueue blocks behind SQLite's write transaction and, after commit, either observes its row cancelled or its tombstone and raises `VoiceQueueCancelledError`; it may never commit a live late row. Keep the existing playback ordering: persist the terminal queue state before invoking the sole broker/player stop path, so a killed playback cannot be rewritten as failed.

- [ ] **Step 4: Verify GREEN and full Batch 3 gate**

```bash
dan_new_evidence batch3-command
env HOME="$DAN_TEST_HOME" TMPDIR="$DAN_TEST_RUNTIME" DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONNOUSERSITE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider -p tests.audio_guard_plugin \
  tests/test_brain_cli_persistent_session.py tests/test_context_builder.py \
  tests/test_persona_assets.py tests/test_persona_privacy.py \
  tests/test_runtime_persona_projection.py tests/test_persona_doctor.py \
  tests/test_config_registry.py tests/test_config.py tests/test_cli_config.py tests/test_api_smoke.py \
  tests/test_voice_api_contract.py tests/test_shared_voice_runtime_truth.py tests/test_audio_player.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_brain_cli_streaming.py \
  tests/test_voice_cancellation.py \
  tests/test_voice_queue.py tests/test_event_store.py
zsh scripts/persona-doctor.sh
.venv/bin/ruff check dan/brain dan/persona_doctor.py dan/config.py dan/config_registry.py dan/voice \
  dan/api/routes_runtime.py dan/api/routes_voice.py tests/test_brain_cli_persistent_session.py \
  tests/test_brain_cli_streaming.py tests/test_chatterbox_v3_pipeline.py \
  tests/test_voice_cancellation.py \
  tests/test_voice_queue.py tests/test_event_store.py
git diff --check
```

Expected: all automated checks pass with audio disabled. Batch completion is blocked if the real approved Żaneta gate artifact has not been pinned; no fake or placeholder hash may satisfy the gate.
