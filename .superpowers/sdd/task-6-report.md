# Task 6 implementation report

## Status

Implemented the versioned voice catalog, measured gains, shared pronunciations,
20 licensed and hash-verified Supertonic custom styles, and an explicit offline-only
Chatterbox V3 Zaneta pipeline. No queue, persistence, live broker, player, or Task 7
snapshot behavior was added.

## Source reconciliation

Audited the active `~/.config/voice/personas.toml`, its six dated backups,
`gains.json`, `pronunciations.toml`, `state/overrides.json`, `dan_core/say.py`, both
identical `voice_turn.sh` copies, radio feeder/scenario references, panel voice data,
`~/.jarvis/jarvis.toml`, the 466-sample Voice Lab verdicts, custom-style cache,
Chatterbox V3 generators and metadata, the local Lily reference, and accepted Zaneta
outputs without playing audio.

Binding outcomes are recorded in `docs/migration/VOICE-DECISIONS.md`. The active
`dan=M3/raw/1.28` and `danusia=F4/clean/1.28` routes are preserved. Explicit
`M3=M3/raw/1.25`, `F4=F4/clean/1.25`, and `ksiadz=M1/raw/1.05` routes are present.
Jarvis-only pronunciation differences remain outside the shared dictionary. The old
comment claiming DSP belonged to the feeder was removed and corrected.

## TDD evidence

### RED

Command:

```text
pytest -q tests/test_voice_assets.py tests/test_voice_catalog.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_route_matrix.py
```

Expected result before implementation: collection stopped with four errors because
`dan.voice.assets` and `dan.voice.pipelines` did not exist. This was the intended
missing-behavior boundary, not an unrelated assertion or environment failure.

### GREEN

Focused command after minimal implementation and after refactor:

```text
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/pytest -q \
  tests/test_voice_assets.py tests/test_voice_catalog.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_route_matrix.py
```

Result: `16 passed in 0.07s`.

Cold-HOME command from the brief using the global Homebrew `pytest` failed before
collection because that interpreter obtains `httpx` from the user site, which vanishes
under an empty HOME. The same required cold-HOME test was rerun with the repository
venv so dependencies remained available while HOME stayed empty:

```text
HOME="$(mktemp -d)" /Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/pytest -q \
  tests/test_voice_assets.py tests/test_voice_catalog.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_route_matrix.py
```

Result: `16 passed in 0.12s`. Tests prove that files placed only under fake
`~/.cache` or `~/.config` are never used as fallback.

## Asset and pipeline evidence

- `python -m dan.voice.assets verify config/voice/custom_styles/manifest.json`:
  `verified 20 voice assets`.
- Exact style set, file SHA-256, deterministic recipe, source, pinned Supertonic
  revision `724fb5abbf5502583fb520898d45929e62f02c0b`, OpenRAIL-M license, and notice
  are tested.
- No reference or generated WAV is present under `config/voice`.
- Zaneta source commit is pinned to
  `65b18437192794391a0308a8f705b1e33e633948`; model snapshot is pinned to
  `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`.
- The reference stays local-only and must be provided through
  `DAN_ZANETA_REFERENCE_WAV` with expected SHA-256
  `06f54e0f140c8caeb8911cea60918c29c5ffac30bd0b2018e18d01715b1b986c`.
- Rendering sets Hugging Face and Transformers offline flags, verifies pinned local
  source metadata/model assets/reference hash, emits mono PCM16 at 24 kHz, uses
  deterministic seeds starting at `730711`, and publishes only at score `>=0.9` with
  a sidecar output manifest.

## Regression evidence

Relevant Task 5 voice/config suite:

```text
tests/test_voice_resolver.py tests/test_shared_voice.py
tests/test_shared_voice_broker.py tests/test_voice_persona_binding.py
tests/test_voice_tts_supertonic.py tests/test_config_registry.py
tests/test_persona_privacy.py tests/test_runtime_settings_legacy_approval.py
tests/test_shared_voice_runtime_truth.py tests/test_voice_fix04.py
tests/test_voice_broker.py
```

Result: `113 passed, 41 expected deprecation warnings in 3.74s`.

Repository isolated non-live regression:

```text
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python \
  scripts/dan-test-baseline --compare ~/.dan/migration/test-baseline.json
```

Result: exit `0`; `2448 collected`, `2448 isolated`, `0 live_manual`, `270` known
failures, `0` new failure IDs, `0` removed failure IDs, duration `366.228s`.

- `python -m compileall -q dan tests`: passed.
- `git diff --check`: passed before final report and scheduled for the final gate.
- Ruff is not installed in either available environment, so no Ruff result is claimed.

Final pre-commit combined gate after refactor and report completion: `129 passed,
41 expected deprecation warnings in 3.18s`; the repeated cold-HOME suite reported
`16 passed in 0.10s`; asset verification again reported `20` verified files; and
`git diff --check` passed.

## Changed files

- Replaced the two example voice TOMLs with versioned `personas.toml` and
  `pronunciations.toml`; added `gains.json` and the Zaneta pipeline TOML.
- Added 20 custom-style JSON files, `manifest.json`, full OpenRAIL-M license, and notice.
- Added `dan/voice/assets.py` and `dan/voice/pipelines/`.
- Added the four Task 6 test modules.
- Added `docs/migration/VOICE-DECISIONS.md` and this report.

## Self-review and risks

- Scope review: no files outside the user-owned Task 6 surfaces were modified.
- Source-of-truth review: the new catalog delegates runtime resolution to Task 5's
  `VoiceCatalog`/`VoiceResolver`; it does not introduce a second runtime resolver.
- Cache/network review: repository paths are explicit; no home cache search or network
  download fallback exists.
- Licensing review: only OpenRAIL-M-derived style JSON is redistributed. Lily reference
  WAVs and generated Zaneta WAVs remain local-only. Historical public audio exposure is
  still a release risk and was not copied into this worktree.
- Runtime review: no live/manual synthesis or audio playback was run, as required. The
  heavy Chatterbox generator therefore remains unexercised here; automated tests cover
  provisioning failures, pin/hash checks, PCM format, seed selection, acceptance, and
  output-manifest publication with deterministic fakes.
- Task boundary: Task 7 still owns persistent snapshots, VoiceService, enqueueing, and the
  sole playback path.

## Review-fix section (2026-07-18)

### RED failures reproduced

The review findings were reproduced before production edits with focused tests:

- Extra `EXTRA.json` beside a valid style manifest was accepted: the regression failed
  with `DID NOT RAISE AssetVerificationError`.
- M1 resolved `mastering = "raport"`, then synthesis failed with
  `unknown resolved mastering profile: 'raport'` before executing ffmpeg.
- A catalog custom style reached the Supertonic command as bare `M2M1`; the stronger
  warm-server regression also proved that no `--custom-style-path` command ran.
- The pipeline loader ignored all seven incompatible TOML mutations tested:
  `network_fallback`, reference `redistribute`, `sample_rate`, `channels`,
  `sample_width_bytes`, `publish_below_threshold`, and `output_manifest`.
- Package-tree corruption and mismatched interpreter provenance both produced
  `DID NOT RAISE`; model files were accepted by directory name and presence alone.
- A configured venv Python symlink resolved to the base Homebrew interpreter, proving
  that package provenance was not tied to the configured interpreter environment.
- Injecting `_write_output_manifest` failure after acceptance left `zaneta.wav`
  published without its manifest.

RED commands:

```text
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_voice_assets.py::test_verifier_rejects_extra_unmanifested_json

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_voice_tts_supertonic.py::test_m1_raport_profile_executes_supported_mastering_command \
  tests/test_voice_tts_supertonic.py::test_custom_style_synthesis_uses_manifest_verified_repo_path

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py::test_versioned_manifest_pins_sources_parameters_and_local_inputs \
  tests/test_chatterbox_v3_pipeline.py::test_manifest_fails_closed_on_unsupported_contract_values \
  tests/test_chatterbox_v3_pipeline.py::test_pinned_runtime_rejects_wrong_package_and_model_bytes \
  tests/test_chatterbox_v3_pipeline.py::test_pinned_runtime_rejects_mismatched_interpreter_provenance \
  tests/test_chatterbox_v3_pipeline.py::test_manifest_write_failure_never_publishes_wav
```

Observed RED summaries: `1 failed`, `2 failed`, and `11 failed`. The venv-path
regression was then tightened separately and failed with the configured symlink having
been replaced by the resolved base interpreter path.

### GREEN implementation and results

- Style verification now rejects extra JSON and TTS rechecks the selected style hash,
  passes its repository path through Supertonic's real `--custom-style-path` option,
  and bypasses the cache-backed warm server for custom styles. Built-in voices retain
  the existing warm-server path. Empty HOME and external cache deletion cannot affect
  this resolution.
- The accepted broker `raport` ffmpeg chain is supported by compatibility TTS. The test
  executes a real fake ffmpeg command, records `-af`, and proves the chain starts with
  `asetrate=44100*1.015` instead of failing as unknown.
- The Chatterbox manifest pins Python `3.14.6`, the interpreter SHA-256,
  `chatterbox-tts==0.1.7`, the full 50-file package-tree SHA-256, source commit, and
  full SHA-256 values for all seven model files used by the V3 load/generation path.
  The provenance probe runs under the configured venv with `-I` and must report the
  same interpreter path and package-owned `direct_url.json`.
- TOML contract values fail closed at load and render boundaries. Only offline,
  non-redistributed reference input, mono PCM16 at 24 kHz, threshold-only publication,
  and mandatory output manifests are supported.
- Accepted output is hashed while still hidden. Its manifest is written to staging
  before publication; pair publication backs up any previous pair and rolls back on
  failure, so manifest-write failure cannot leave a newly published WAV.
- Exact-set verification rejects unmanifested JSON. No-WAV coverage now scans
  `config/voice`, `dan/voice`, `docs/migration`, and `.superpowers/sdd`.

GREEN commands and output summaries:

```text
# Focused review-fix and config coverage
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_voice_assets.py tests/test_voice_catalog.py tests/test_voice_route_matrix.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_tts_supertonic.py \
  tests/test_config.py tests/test_config_registry.py tests/test_voice_resolver.py \
  tests/test_voice_persona_binding.py
# 139 passed, 32 expected deprecation warnings

# Cold HOME focused suite
HOME="$(mktemp -d)" /Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_voice_assets.py tests/test_voice_catalog.py tests/test_voice_route_matrix.py \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_tts_supertonic.py
# 61 passed, 26 expected deprecation warnings

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m dan.voice.assets verify \
  config/voice/custom_styles/manifest.json
# verified 20 voice assets

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m compileall -q dan tests
git diff --check
# both exit 0

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python scripts/dan-test-baseline \
  --compare /Users/n1_ozzy/.dan/migration/test-baseline.json
# exit 0; 2464 collected/isolated, 0 live_manual, 270 known failures,
# 0 new failure IDs, duration 366.426s
```

The local source-truth verification also hashed the real pinned model snapshot, invoked
the real configured Chatterbox venv provenance probe, and verified the local reference;
it printed `verified pinned Chatterbox runtime, model files, interpreter package, and
reference` without synthesizing or playing audio.

### Changed files and self-review

- `config/voice/pipelines/chatterbox-v3-zaneta.toml`
- `dan/voice/assets.py`
- `dan/voice/pipelines/chatterbox_v3.py`
- `dan/voice/tts.py`
- `tests/test_voice_assets.py`
- `tests/test_chatterbox_v3_pipeline.py`
- `tests/test_voice_tts_supertonic.py`
- `.superpowers/sdd/task-6-report.md`

No `dan/config.py` change remained necessary: adding a new runtime knob would have
expanded the strict config registry for no benefit, while the required source is the
repository manifest itself. No Task 7 persistence, snapshots, queue, broker, player,
or publication ownership was added. The only compatibility-TTS change is converting an
already resolved custom style name into its hash-verified repository asset argument.
The pinned Python/package/model hashes intentionally fail closed after any environment
upgrade until the versioned manifest is reconciled from new local source truth.

## Second review-fix section (2026-07-18)

### RED failures reproduced

All second-wave review findings were reproduced before their production edits.

1. Non-finite TOML synthesis/threshold values, non-finite scorer results, and a
   configured threshold below `0.9` were accepted. The focused command reported
   `20 failed, 15 deselected`.
2. Final publication replaced the WAV before its manifest and caught only
   `Exception`. Manifest-order, empty-target `KeyboardInterrupt`, and four
   replacement rollback injections reported `6 failed, 35 deselected`; the direct
   failure state was `manifest_visible == False` with `wav_published == True`.
3. The generation subprocess inherited hostile `PYTHONPATH`/`PYTHONHOME`, had no
   safe `cwd`, and omitted `-I`, despite the provenance probe using `-I`.
4. A missing snapshot lock, forged model revision, and mismatched lock file map were
   all accepted. The combined isolation/snapshot command reported
   `5 failed, 40 deselected`.
5. The old no-WAV test scanned only four selected directories and only lowercase
   `*.wav`. With an alternate Git index containing tracked
   `review-fixture/VOICE.WAV`, the repository-wide replacement test reported
   `1 failed` and named that path. The real index was not modified.
6. The old route matrix serialized a resolver snapshot and compared it back to the
   same object. The replacement test executed every route through the real resolver,
   engine factory, and synthesis boundary; RED reported `1 failed, 2 passed` when the
   first versioned DSP route reached runtime without an ffmpeg postprocess command.

RED commands:

```text
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py \
  -k 'non_finite or hard_floor or invalid_runtime_threshold'

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py \
  -k 'publication_replaces or keyboard_interrupt or base_exception'

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py \
  -k 'versioned_manifest or snapshot_lock or forged_snapshot or mismatched_snapshot or hostile_import'

GIT_INDEX_FILE=<alternate-index-with-review-fixture/VOICE.WAV> \
  /Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_voice_assets.py::test_repository_versions_no_reference_or_generated_wav

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_voice_route_matrix.py
```

### GREEN implementation

- Pipeline TOML float fields are type-checked and finite at load. The same finite
  checks run again at the render boundary for manually constructed manifests and
  scorer results. The configured and runtime acceptance threshold must be finite and
  at least `0.9`.
- Pair publication is deliberately not described as two-file atomicity. It hides an
  old WAV before its old manifest, publishes the new manifest before the new WAV, and
  catches `BaseException`. Rollback removes a new WAV before its manifest and restores
  an old manifest before its WAV. Failure injection after each replace proves the old
  pair is restored with no staged, candidate, or backup residue.
- Provenance probe and actual generation now use the same configured interpreter with
  `-I`, a verified model-directory `cwd`, offline flags, and an environment stripped
  of every inherited `PYTHON*` variable. A hostile cwd package and `PYTHONPATH` cannot
  substitute the verified `chatterbox-tts` package.
- The pipeline TOML pins `model_repo_id = "ResembleAI/chatterbox"` and
  `model_lock_name = "snapshot-lock.json"`. Runtime requires that local lock to contain
  schema version 1, the exact repository ID, exact model revision, and an exact file
  hash map equal to `[model_files]`, then separately hashes every on-disk model file.
  There is no network or HOME-cache fallback.
- `docs/migration/CHATTERBOX-V3-INSTALLER-CONTRACT.md` defines the installer outputs,
  lock format, environment paths, and runtime verification boundary.
- The no-WAV guard reads every path from `git ls-files -z` and rejects `.wav` suffixes
  case-insensitively without directory exclusions.
- The route matrix executes every catalog persona through `VoiceResolver`,
  `build_tts_engine`, and `SupertonicEngine.synthesize`; only final Supertonic/ffmpeg
  subprocesses and audio bytes are faked. It proves voice, speed, custom-style path,
  mastering, and DSP command behavior. Zaneta separately proves local-only Chatterbox
  capability failure followed by the explicit Supertonic live fallback.

### GREEN commands and results

```text
# Covering suites plus affected config/resolver tests
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_assets.py \
  tests/test_voice_route_matrix.py tests/test_voice_catalog.py \
  tests/test_voice_tts_supertonic.py tests/test_config.py \
  tests/test_config_registry.py tests/test_voice_resolver.py \
  tests/test_voice_persona_binding.py
# 167 passed, 34 expected deprecation warnings

# Cold HOME covering suite
HOME="$(mktemp -d)" \
  /Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m pytest -q \
  tests/test_chatterbox_v3_pipeline.py tests/test_voice_assets.py \
  tests/test_voice_route_matrix.py tests/test_voice_catalog.py \
  tests/test_voice_tts_supertonic.py
# 89 passed, 28 expected deprecation warnings

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m compileall -q dan tests
/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python -m dan.voice.assets verify \
  config/voice/custom_styles/manifest.json
git diff --check
# all exit 0; asset verifier printed "verified 20 voice assets"

/Users/n1_ozzy/Documents/dev/jarvis/.venv/bin/python scripts/dan-test-baseline \
  --compare /Users/n1_ozzy/.dan/migration/test-baseline.json
# exit 0; 2492 collected/isolated, 0 live_manual, 270 unchanged known failures,
# 0 new failure IDs, 0 removed failure IDs, duration 366.392s
```

### Changed files and self-review

- `config/voice/pipelines/chatterbox-v3-zaneta.toml`
- `dan/voice/pipelines/chatterbox_v3.py`
- `dan/voice/tts.py`
- `docs/migration/CHATTERBOX-V3-INSTALLER-CONTRACT.md`
- `tests/test_chatterbox_v3_pipeline.py`
- `tests/test_voice_assets.py`
- `tests/test_voice_route_matrix.py`
- `.superpowers/sdd/task-6-report.md`

Scope review found no Task 7 persistence, snapshot store, queue, broker, player, or
enqueue changes. No synthesis, playback, network access, or live/manual audio test ran.
The existing local Hugging Face snapshot predates this contract and currently lacks the
new `snapshot-lock.json`; the runtime therefore fails closed until the local installer
produces the documented lock. This is an intentional provenance gate, but it is an
operator-visible provisioning requirement.
