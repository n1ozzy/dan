# DAN Release 1 Audit Remediation — Batch 3 Persona, Configuration, and Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the persistent Claude conversation while applying fresh canon before the next turn, resolve effective configuration through one typed source, eliminate dead voice/persona projections, pin the Żaneta acceptance gate, and make voice cancellation atomic.

**Architecture:** The persona hash identifies the exact rendered bootstrap of a persistent Claude transport. A hash change closes that transport and starts a new session before any user text is written; conversation state is reconstructed from persona-free durable history. `ConfigStore` becomes the only typed owner-aware resolver, and the voice catalog provides its versioned projection. The Żaneta gate is an isolated, hash-pinned executable with a strict JSON protocol. Queue cancellation selects, updates, and emits events under one transaction.

**Tech Stack:** Python 3.11+, Claude CLI stream-json transport, SHA-256, TOML, dataclasses, SQLite transactions, isolated Python `-I`, JSON schema validation, pytest, ruff.

## Global Constraints

- The approved product behavior is one persistent Claude CLI conversation. Do not reintroduce cold-per-turn execution, provider chains, session switching, or a second model.
- `config/persona/DAN.md` is the only character canon. Do not copy, summarize, sanitize, classify, or rewrite it. Missing/empty/invalid canon fails before provider execution.
- The session bootstrap hash covers the exact rendered persona payload, including owner interpolation. Keep the raw canon revision as separate diagnostic evidence.
- On persona drift, do not use `--resume`, do not append the new canon to an old session, and do not replay a checkpoint containing the old persona.
- `ConfigStore` is extended rather than wrapped by a competing resolver. `config/dan.example.toml` is documentation and never an effective runtime source.
- Product code and tests use English identifiers and error contracts. Do not add narrative comments that restate the code; comments are reserved for non-obvious invariants, races, and platform workarounds.
- Persona verification is text-only. Do not start TTS, the voice broker, or audible tests.
- Batch 3 follows Batch 2 by default. A Batch 3 task may overlap in time with Batch 2 only when an explicit ownership map proves the exact files are disjoint.

---

## Task 3.1: Recycle persistent Claude transport before a persona-drift turn

**Files:**

- Modify: `dan/brain/claude_cli_adapter.py`
- Create: `dan/persona_doctor.py`
- Create: `scripts/persona-doctor.sh`
- Modify: `tests/test_brain_cli_persistent_session.py`
- Modify: `tests/test_context_builder.py`
- Modify: `tests/test_persona_assets.py`
- Modify: `tests/test_persona_privacy.py`
- Modify: `tests/test_runtime_persona_projection.py`
- Create: `tests/test_persona_doctor.py`

- [ ] **Step 1: Map the active instruction route before editing**

Confirm the request path:

```text
config/persona/DAN.md -> render_persona -> ContextBuilder.build_request
-> ClaudeCliAdapter.generate -> _generate_persistent -> Claude CLI stdin
```

Record current branch, SHA, dirty overlap, active adapter selection, persistent session state path, and any postprocessor. Stop if another agent owns `claude_cli_adapter.py`.

- [ ] **Step 2: Write RED transport-ordering tests**

```python
def test_persona_change_recycles_transport_before_next_stdin_write(adapter: ClaudeCliAdapter) -> None:
    first = request_with_persona("canon-v1", text="first")
    second = request_with_persona("canon-v2", text="second")
    adapter.generate(first)
    old_process = adapter.persistent_process
    adapter.generate(second)
    assert old_process.closed_before_write("second")
    assert adapter.persistent_process.bootstrap_contains("canon-v2")


def test_persona_change_mints_new_session_without_resume_or_old_checkpoint(adapter: ClaudeCliAdapter) -> None:
    adapter.generate(request_with_persona("canon-v1", text="first"))
    old_session = adapter.session_id
    adapter.generate(request_with_persona("canon-v2", text="second"))
    assert adapter.session_id != old_session
    assert "--resume" not in adapter.last_spawn_argv
    assert "canon-v1" not in adapter.last_bootstrap_payload
```

Add:

- `test_same_persona_hash_keeps_incremental_persistent_transport`;
- `test_owner_interpolation_change_triggers_recycle`;
- `test_persona_hash_is_not_updated_after_failed_response`;
- `test_completed_checkpoint_contains_no_persona_message`;
- `test_restart_with_same_bootstrap_hash_may_resume_durable_session`;
- `test_persona_doctor_checks_the_single_canon_without_provider_or_audio`.

- [ ] **Step 3: Verify RED**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_brain_cli_persistent_session.py
```

Expected: the second input reaches the old process and `_persona_hash` changes only after the response.

- [ ] **Step 4: Implement pre-send drift handling**

```python
def _ensure_persona_bootstrap(self, request: BrainRequest) -> None:
    requested_hash = _request_persona_hash(request)
    if self._process is None:
        self._start_persistent_transport(request, persona_hash=requested_hash)
        return
    if requested_hash != self._persona_hash:
        self._close_persistent_transport(reason="persona_changed")
        self._session_id = self._new_session_id()
        self._start_persistent_transport(request, persona_hash=requested_hash, allow_resume=False)
```

Call this before `_send_generation`. Set `_persona_hash` only after a successful bootstrap write, not after a model response. Persist it as the identity of the session bootstrap.

Change `_format_completed_checkpoint()` so it serializes only completed user, assistant, and tool evidence; it must never call `format_cli_user_prompt(request)` or include any `kind=persona` message. The new transport receives current canon in its bootstrap and current conversation evidence separately.

Add a text-only doctor entry point:

```python
@dataclass(frozen=True)
class PersonaDoctorReport:
    canon_path: str
    canon_version: str
    rendered_sha256: str
    active_routes: tuple[str, ...]
    errors: tuple[str, ...]


def inspect_persona_route(*, repo: Path, owner_path: Path) -> PersonaDoctorReport:
    ...
```

The doctor verifies that the sole canon exists and has a valid version, renders it with an isolated owner fixture, builds a real `ContextBuilder` request, and proves the exact rendered canon is the first persona/system payload. It scans active route configuration for a second canon, sanitizer, rewriter, or tame fallback. It never starts Claude, TTS, audio, or runtime. `scripts/persona-doctor.sh` is a thin wrapper around `.venv/bin/python -m dan.persona_doctor`.

- [ ] **Step 5: Verify GREEN and persona invariants**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
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
- Modify: `dan/daemon/app.py`
- Modify: `tests/test_config_registry.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli_config.py`
- Modify: settings-route tests

- [ ] **Step 1: Write RED source/owner/type tests**

```python
def test_example_config_cannot_be_effective_runtime_source(store: ConfigStore) -> None:
    explained = store.explain("voice.personas")
    assert explained.source != "config/dan.example.toml"
    assert explained.owner == "voice_catalog"


def test_load_explain_and_settings_share_value_owner_revision(runtime_fixture: RuntimeFixture) -> None:
    loaded = load_config(runtime_fixture.installation_toml)
    explained = runtime_fixture.store.explain("brain.model")
    routed = runtime_fixture.client.get("/settings").json()["brain"]["model"]
    assert (loaded.brain.model, explained.value, routed["value"]) == (explained.value,) * 3
    assert routed["owner"] == explained.owner
    assert routed["revision"] == explained.revision
```

Add wrong-owner and wrong-type TOML rejection tests.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
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

`ConfigStore.resolve()` parses and validates installation-owned keys with the same registry parsers used by `explain()`. It merges voice-catalog-owned projections from `config/voice/`, rejects owner violations, and returns the snapshot consumed by `load_config`, daemon startup, CLI explain, and `/settings`.

- [ ] **Step 4: Verify GREEN and compatibility errors**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_config_registry.py tests/test_config.py tests/test_cli_config.py tests/test_api_smoke.py
.venv/bin/ruff check dan/config_registry.py dan/config.py dan/voice/resolver.py \
  dan/api/routes_settings.py tests/test_config_registry.py
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
- Modify: `tests/test_shared_voice_runtime_truth.py`
- Modify: `tests/test_audio_player.py`

Do not edit panel assets in this task; their projection removal belongs to Batch 4 after Fable ownership transfer.

**Dependency:** Task 3.3 starts only after Batch 2 Task 2.1 is GREEN because its readiness contract consumes `ChildSupervisor.status("supertonic")`.

- [ ] **Step 1: Write RED backend-contract tests**

```python
def test_runtime_contract_has_no_persona_profile_or_playback_binary(client: TestClient) -> None:
    payload = client.get("/runtime").json()
    serialized = json.dumps(payload, sort_keys=True)
    assert "persona.profile" not in serialized
    assert "playback_binary" not in serialized


def test_playback_readiness_uses_live_player_and_supervised_tts(app: DaemonApp) -> None:
    status = app.voice_runtime_status()
    assert status["playback"] == app.voice_player.status().to_mapping()
    assert status["tts_child"] == app.child_supervisor.status("supertonic").to_mapping()
```

- [ ] **Step 2: Verify RED and remove active projections**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_config_registry.py tests/test_api_smoke.py \
  tests/test_shared_voice_runtime_truth.py tests/test_audio_player.py
```

Remove `persona.profile` from config/context/runtime contracts. Persona selection is not configurable; `ContextBuilder` always uses the sole canon loader. Remove `voice.playback_binary`; expose a typed `CoreAudioPlayer.status()` and Batch 2 child status instead. Reject deprecated keys with a stable migration error instead of silently accepting them.

Delete the obsolete `scripts/smoke-persona-profile.sh`; its route no longer exists. Replace its release purpose with `scripts/persona-doctor.sh` and ensure the active-reference audit rejects any installed copy still invoking the deleted persona-profile route.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_config_registry.py tests/test_context_builder.py tests/test_api_smoke.py \
  tests/test_shared_voice_runtime_truth.py tests/test_audio_player.py
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
- External read-only input: the operator-approved M5 acceptance-gate file

**Precondition:** The operator supplies the exact gate artifact intended for M5 acceptance. If it is absent, stop this task with a visible blocked finding; do not fabricate a scorer or weaken the release gate.

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

- [ ] **Step 3: Verify RED, implement regular-file/path/hash checks, verify GREEN**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chatterbox_v3_pipeline.py
.venv/bin/ruff check dan/voice/pipelines/chatterbox_v3.py tests/test_chatterbox_v3_pipeline.py
git diff --check
```

Resolve the environment-provided gate path, reject symlinks and non-regular files, require its basename/logical identity contract, and compare bytes with `hmac.compare_digest(actual_sha, expected_sha)`.

## Task 3.5: Require an isolated structured Żaneta result

**Files:**

- Modify: `dan/voice/pipelines/chatterbox_v3.py`
- Modify: the approved gate artifact only in its separately owned source repository, if protocol adaptation is required
- Modify: `tests/test_chatterbox_v3_pipeline.py`

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

Run:

```python
argv = [str(manifest.python_executable), "-I", str(manifest.acceptance_gate), str(candidate), text]
env = {
    "PATH": str(manifest.python_executable.parent),
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "PYTHONNOUSERSITE": "1",
}
```

Parse exactly one JSON object, reject extra stdout, booleans as numbers, non-finite/out-of-range scores, and any provenance mismatch.

- [ ] **Step 3: Verify GREEN**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chatterbox_v3_pipeline.py
.venv/bin/ruff check dan/voice/pipelines/chatterbox_v3.py tests/test_chatterbox_v3_pipeline.py
git diff --check
```

Automated GREEN proves protocol integrity, not audible quality. Real M5 execution and listening remain manual release evidence.

## Task 3.6: Make `cancel_session` atomic

**Files:**

- Modify: `dan/voice/queue.py`
- Modify: `dan/voice/service.py` only if event publication currently occurs outside the queue transaction contract
- Modify: `tests/test_voice_queue.py`
- Modify: `tests/test_voice_service.py`

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
```

- [ ] **Step 2: Verify RED and implement one transaction**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_voice_queue.py tests/test_voice_service.py
```

Acquire `BEGIN IMMEDIATE` before selection. Prefer `UPDATE ... RETURNING request_id`; if the supported SQLite contract requires select/update, perform both under the same transaction and derive events from `cursor.rowcount` plus the selected IDs. Commit state and event records together; rollback both on failure.

- [ ] **Step 3: Verify GREEN and full Batch 3 gate**

```bash
env HOME=/private/tmp/dan-batch3-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_brain_cli_persistent_session.py tests/test_context_builder.py \
  tests/test_persona_assets.py tests/test_persona_privacy.py \
  tests/test_runtime_persona_projection.py tests/test_persona_doctor.py \
  tests/test_config_registry.py tests/test_config.py tests/test_cli_config.py tests/test_api_smoke.py \
  tests/test_shared_voice_runtime_truth.py tests/test_audio_player.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_queue.py tests/test_voice_service.py
zsh scripts/persona-doctor.sh
.venv/bin/ruff check dan/brain dan/persona_doctor.py dan/config.py dan/config_registry.py dan/voice \
  dan/api/routes_runtime.py dan/api/routes_voice.py tests/test_brain_cli_persistent_session.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_queue.py
git diff --check
```

Expected: all automated checks pass with audio disabled. Batch completion is blocked if the real approved Żaneta gate artifact has not been pinned; no fake or placeholder hash may satisfy the gate.
