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
