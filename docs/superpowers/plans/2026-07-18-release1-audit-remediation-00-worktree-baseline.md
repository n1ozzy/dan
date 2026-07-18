# DAN Release 1 Audit Remediation — Batch 0 Worktree and Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze and assign ownership of the dirty worktree surface, create an immutable checkpoint of the current Release 1 state, remove only safe caches from the legacy namespace, and build a baseline v2 that actually blocks audio and binds tests to the checkout and interpreter.

**Architecture:** Batch 0 deploys nothing. The read-only checkpoint and hygiene scanner establish the input evidence; controlled cleanup may affect only caches under the precisely resolved local `jarvis/`. The fail-closed audio guard is enforced at production execution boundaries and by the test plugin. The baseline records the SHAs of the checkout, interpreter, collection, and guard.

**Tech Stack:** Python 3.11+, git plumbing, importlib, pathlib, pytest plugin API, subprocess fakes, JSON, SHA-256.

## Global Constraints

- Do not edit or stage Fable's current changes, particularly `dan/panel/`, panel tests, `AGENTS.md`, `CLAUDE.md`, voice config, and docs.
- Task 0.1 may begin only after the status fingerprint is stable across two consecutive reads and Ozzy/Fable confirms the owner of every dirty path.
- The historical Task 1 manifest and candidate tag are read-only. The new checkpoint must be created under a new name using exclusive creation.
- Cleanup must not use globs or `rm -rf`; it must plan exact paths, verify their type and root, and only then remove `.pyc`, `__pycache__`, and `.DS_Store`.
- No test may invoke real CoreAudio, `afplay`, `say`, the Supertonic CLI, or Supertonic serve.

---

## Task 0.1: Freeze ownership and write an immutable release checkpoint

**Files:**

- Create: `dan/release/__init__.py`
- Create: `dan/release/checkpoint.py`
- Create: `scripts/dan-release-checkpoint`
- Create: `tests/test_release_checkpoint.py`
- Read only: existing Task 1 inventory and `dan-v1-foundation-candidate`

- [ ] **Step 1: Capture the pre-edit evidence**

```bash
git branch --show-current
git rev-parse HEAD
git status --short
git rev-parse dan-v1-foundation-candidate^{}
```

Expected: branch `agent/dan-release1-integration`; current HEAD recorded; candidate still resolves to `1852d7f...`. If dirty paths change between two reads or ownership is unknown, stop.

- [ ] **Step 2: Write the RED checkpoint contract**

```python
def test_checkpoint_binds_head_status_candidate_and_inventory_sha(tmp_path: Path) -> None:
    report = capture_release_checkpoint(repo=fixture_repo(tmp_path))
    assert report.branch == "agent/dan-release1-integration"
    assert report.head == git_head(report.repo)
    assert report.candidate.target_sha == git_tag_target(report.repo, report.candidate.name)
    assert report.inventory.sha256 == sha256_file(report.inventory.path)
    assert report.dirty_paths == tuple(sorted(report.dirty_paths))


def test_checkpoint_refuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "checkpoint.json"
    output.write_text("historical", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_checkpoint_exclusive(output, checkpoint_fixture())
    assert output.read_text(encoding="utf-8") == "historical"
```

- [ ] **Step 3: Verify RED**

```bash
env HOME=/private/tmp/dan-partia0-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_checkpoint.py
```

Expected: import failure for `dan.release.checkpoint`.

- [ ] **Step 4: Implement the immutable model and writer**

```python
@dataclass(frozen=True)
class ReleaseCheckpoint:
    schema_version: int
    created_at: str
    repo: str
    branch: str
    head: str
    dirty_paths: tuple[str, ...]
    ownership: Mapping[str, str]
    candidate: CandidateRef
    inventory: EvidenceRef


def write_checkpoint_exclusive(path: Path, checkpoint: ReleaseCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(asdict(checkpoint), handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)
```

`capture_release_checkpoint()` must use the existing inventory builder, resolve the exact annotated/lightweight tag target, reject an ownership map that omits any dirty path, and store only hashes/paths/status—not private contents.

- [ ] **Step 5: Verify GREEN and review**

```bash
env HOME=/private/tmp/dan-partia0-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_checkpoint.py
.venv/bin/ruff check dan/release/checkpoint.py tests/test_release_checkpoint.py
git diff --check
```

Expected: all pass. Spec reviewer must prove the old manifest and candidate tag cannot be overwritten.

- [ ] **Step 6: Generate the real checkpoint only after ownership freeze**

```bash
.venv/bin/python scripts/dan-release-checkpoint \
  --repo . \
  --candidate-tag dan-v1-foundation-candidate \
  --ownership-file /private/tmp/dan-release1-path-owners.json \
  --output /Users/n1_ozzy/.dan/migration/checkpoints/release1-remediation-precode.json
```

Expected: a new exclusive evidence file outside the repository. This mutates only the private release-evidence area, never active runtime configuration, databases, processes, or audio. Do not run while Fable's patch is still changing.

## Task 0.2: Detect and remove only safe legacy checkout caches

**Files:**

- Create: `dan/release/checkout_hygiene.py`
- Create: `scripts/dan-checkout-hygiene`
- Create: `tests/test_checkout_hygiene.py`
- Modify: `tests/test_imports.py`

- [ ] **Step 1: Write RED tests for physical namespace and safe plan**

```python
def test_cleanup_plan_targets_only_safe_cache_entries(tmp_path: Path) -> None:
    legacy = tmp_path / "jarvis"
    cache = legacy / "sub" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-311.pyc").write_bytes(b"pyc")
    (legacy / "keep.py").write_text("keep = True\n", encoding="utf-8")
    plan = plan_safe_cache_removal(repo=tmp_path, legacy_root=legacy)
    assert all(item.kind in {"pyc", "pycache", "ds_store"} for item in plan.items)
    assert legacy / "keep.py" not in {item.path for item in plan.items}


def test_final_import_surface_rejects_physical_jarvis_namespace(tmp_path: Path) -> None:
    (tmp_path / "jarvis").mkdir()
    finding = scan_checkout_hygiene(tmp_path)
    assert finding.legacy_namespace_present is True
```

- [ ] **Step 2: Verify RED, implement exact-root scanner, verify GREEN**

```bash
env HOME=/private/tmp/dan-partia0-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_checkout_hygiene.py tests/test_imports.py
```

Implementation contract:

```python
SAFE_CACHE_NAMES = frozenset({"__pycache__", ".DS_Store"})


def plan_safe_cache_removal(*, repo: Path, legacy_root: Path) -> HygienePlan:
    repo = repo.resolve(strict=True)
    legacy_root = legacy_root.resolve(strict=True)
    if legacy_root.parent != repo or legacy_root.name != "jarvis":
        raise UnsafeCleanupTarget(str(legacy_root))
    # Enumerate entries, reject symlinks/non-cache files, return exact paths.
```

Apply mode must re-resolve every path immediately before deletion, refuse symlinks, remove files first and then empty `__pycache__` directories. It must refuse to remove the top-level `jarvis/` if any non-cache entry remains.

- [ ] **Step 3: Run controlled local cleanup and re-check imports**

```bash
.venv/bin/python scripts/dan-checkout-hygiene --repo . --json
.venv/bin/python scripts/dan-checkout-hygiene --repo . --apply-safe-cache --json
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_imports.py
```

Expected: apply report lists every removed path; `find_spec("jarvis") is None`. If any source file or symlink exists, stop and report it instead of deleting.

## Task 0.3: Add a fail-closed audio execution boundary

**Files:**

- Modify: `dan/audio/__init__.py`
- Create: `dan/audio/execution.py`
- Create: `tests/audio_guard_plugin.py`
- Create: `tests/test_audio_execution_guard.py`
- Modify: `dan/voice/player.py`
- Modify: `dan/voice/tts.py`
- Modify: `dan/voice/recorder.py`
- Modify: `dan/migration/test_safety.py`
- Modify: `tests/test_test_safety.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write RED tests at the real execution edges**

```python
def test_disable_audio_blocks_coreaudio_and_supertonic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    with pytest.raises(AudioExecutionDisabled):
        CoreAudioPlayer().play(Path("never.wav"))
    with pytest.raises(AudioExecutionDisabled):
        supertonic_fixture().synthesize("never", output_path=Path("never.wav"))


def test_disable_mic_blocks_sox_before_file_or_process_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_DISABLE_MIC", "1")
    recorder = sox_recorder_fixture()
    with pytest.raises(MicrophoneExecutionDisabled):
        recorder.start()
    assert recorder.workdir_entries() == ()
    assert recorder.spawn_count == 0


def test_non_audio_subprocess_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_DISABLE_AUDIO", "1")
    completed = subprocess.run([sys.executable, "-c", "print('ok')"], check=True)
    assert completed.returncode == 0
```

Add a fixture test whose test body imports a helper containing `afplay`; classification must be `live/manual` even though the string is not in the test body.

- [ ] **Step 2: Verify RED**

```bash
env HOME=/private/tmp/dan-partia0-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_audio_execution_guard.py tests/test_test_safety.py
```

Expected: missing `dan.audio.execution` and imported-helper classification failure.

- [ ] **Step 3: Implement one guard, no product test mode**

```python
class AudioExecutionDisabled(RuntimeError):
    pass


class MicrophoneExecutionDisabled(RuntimeError):
    pass


def assert_audio_execution_allowed(*, operation: str) -> None:
    if os.environ.get("DAN_DISABLE_AUDIO") == "1":
        raise AudioExecutionDisabled(f"audio execution disabled: {operation}")


def assert_microphone_execution_allowed(*, operation: str) -> None:
    if os.environ.get("DAN_DISABLE_MIC") == "1":
        raise MicrophoneExecutionDisabled(f"microphone execution disabled: {operation}")
```

Call the audio guard immediately before native player initialization/playback and before any Supertonic serve/CLI synthesis subprocess. Call the microphone guard before the recorder creates a WAV or spawns `sox`. The pytest plugin additionally denies known audio executables on `subprocess.Popen` and records that it loaded; it must not introduce a product-facing `mock` or `test` provider.

- [ ] **Step 4: Verify GREEN and static-classifier coverage**

```bash
env HOME=/private/tmp/dan-partia0-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p tests.audio_guard_plugin -p no:cacheprovider \
  tests/test_audio_execution_guard.py tests/test_test_safety.py tests/test_audio_player.py \
  tests/test_voice_tts_supertonic.py tests/test_voice_recorder.py
.venv/bin/ruff check dan/audio dan/voice/player.py dan/voice/tts.py \
  dan/voice/recorder.py dan/migration/test_safety.py tests/audio_guard_plugin.py \
  tests/test_audio_execution_guard.py
git diff --check
```

Expected: guard-loaded marker is present and no real audio edge executes.

## Task 0.4: Bind baseline v2 to checkout, interpreter and collected node IDs

**Files:**

- Modify: `scripts/dan-test-baseline`
- Modify: `dan/migration/test_safety.py`
- Modify: `tests/test_test_safety.py`

- [ ] **Step 1: Write RED report-v2 tests**

```python
def test_report_v2_binds_guard_checkout_and_interpreter(tmp_path: Path) -> None:
    report = run_baseline_fixture(tmp_path)
    assert report["schema_version"] == 2
    assert report["checkout"]["head"] == fixture_head(tmp_path)
    assert report["interpreter"]["realpath"] == str(Path(sys.executable).resolve())
    assert report["audio_guard"]["loaded"] is True
    assert report["collection"]["nodeids_sha256"] == sha256_lines(report["collection"]["nodeids"])


def test_collection_and_execution_use_same_controlled_command(tmp_path: Path) -> None:
    report = run_baseline_fixture(tmp_path)
    assert report["collection"]["command_sha256"] == report["execution"]["command_sha256"]
```

- [ ] **Step 2: Verify RED, implement v2, then verify focused GREEN**

```bash
env HOME=/private/tmp/dan-partia0-home DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_test_safety.py
```

The command builder must use `Path(sys.executable).resolve() -m pytest`, explicitly load `tests.audio_guard_plugin`, prepend a temporary directory of fake audio executables to `PATH`, use the same base argv for collect and execute, and reject interpreters/shebangs resolving into legacy repo roots.

- [ ] **Step 3: Run the real baseline v2**

```bash
env HOME=/private/tmp/dan-release1-baseline-home \
  DAN_TEST_REPORT_HOME=/private/tmp/dan-release1-baseline-report \
  DAN_DISABLE_AUDIO=1 DAN_DISABLE_MIC=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python scripts/dan-test-baseline
```

Expected: report schema 2; exact collected/passed/failed node sets; guard loaded; checkout/interpreter hashes match. Any failed node blocks Batch 1.

- [ ] **Step 4: Batch 0 review gate**

```bash
.venv/bin/ruff check dan/release dan/audio dan/migration/test_safety.py tests
git diff --check
git status --short
```

Reviewers must verify: no Fable-owned file entered the diff; no historical evidence was overwritten; cleanup was exact-root and recoverable from its report; audio was not started; baseline is reproducible from a clean checkout.
