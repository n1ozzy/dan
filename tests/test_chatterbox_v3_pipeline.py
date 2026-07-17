from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import wave
from pathlib import Path

import pytest

from dan.voice.pipelines.chatterbox_v3 import (
    AcceptanceError,
    ChatterboxV3ZanetaPipeline,
    PipelineCapabilityError,
    PipelineManifest,
    load_pipeline_manifest,
    verify_reference_rights_and_hash,
)


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = ROOT / "config" / "voice" / "pipelines" / "chatterbox-v3-zaneta.toml"
SOURCE_REVISION = "65b18437192794391a0308a8f705b1e33e633948"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
REFERENCE_SHA256 = "06f54e0f140c8caeb8911cea60918c29c5ffac30bd0b2018e18d01715b1b986c"


def _write_pcm16(path: Path, *, channels: int = 1, rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * channels * 32)


def _manifest(tmp_path: Path, reference: Path) -> PipelineManifest:
    model = tmp_path / MODEL_REVISION
    model.mkdir()
    for name in ("t3_mtl23ls_v3.safetensors", "s3gen_v3.pt"):
        (model / name).write_bytes(b"model")
    metadata = tmp_path / "direct_url.json"
    metadata.write_text(
        json.dumps({"vcs_info": {"commit_id": SOURCE_REVISION, "vcs": "git"}}),
        encoding="utf-8",
    )
    gate = tmp_path / "gate.py"
    gate.write_text("# test gate\n", encoding="utf-8")
    return PipelineManifest(
        name="chatterbox-v3-zaneta",
        source_revision=SOURCE_REVISION,
        model_revision=MODEL_REVISION,
        source_metadata_path=metadata,
        model_path=model,
        python_executable=Path(os.sys.executable),
        acceptance_gate=gate,
        reference_path=reference,
        reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        reference_license_decision="local-only",
        exaggeration=0.6,
        cfg_weight=0.5,
        temperature=0.8,
        seed=730_711,
        max_attempts=3,
        acceptance_threshold=0.9,
        sample_rate=24000,
    )


def test_versioned_manifest_pins_sources_parameters_and_local_inputs(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "zaneta-reference.wav"
    _write_pcm16(reference)
    env = {
        "DAN_CHATTERBOX_V3_DIRECT_URL": str(tmp_path / "direct_url.json"),
        "DAN_CHATTERBOX_V3_MODEL_DIR": str(tmp_path / MODEL_REVISION),
        "DAN_CHATTERBOX_V3_PYTHON": os.sys.executable,
        "DAN_ZANETA_ACCEPTANCE_GATE": str(tmp_path / "gate.py"),
        "DAN_ZANETA_REFERENCE_WAV": str(reference),
    }
    (tmp_path / "direct_url.json").write_text("{}", encoding="utf-8")
    (tmp_path / MODEL_REVISION).mkdir()
    (tmp_path / "gate.py").write_text("", encoding="utf-8")

    manifest = load_pipeline_manifest(PIPELINE_PATH, environ=env)

    assert manifest.source_revision == SOURCE_REVISION
    assert manifest.model_revision == MODEL_REVISION
    assert manifest.reference_sha256 == REFERENCE_SHA256
    assert (manifest.exaggeration, manifest.cfg_weight, manifest.temperature) == (
        0.6,
        0.5,
        0.8,
    )
    assert manifest.seed == 730_711
    assert manifest.acceptance_threshold == 0.9
    assert manifest.sample_rate == 24000


def test_cold_home_has_no_reference_or_model_cache_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    cached = fake_home / ".cache" / "huggingface" / "hub" / MODEL_REVISION
    cached.mkdir(parents=True)
    (fake_home / ".config" / "voice").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    with pytest.raises(PipelineCapabilityError, match="DAN_ZANETA_REFERENCE_WAV"):
        load_pipeline_manifest(PIPELINE_PATH, environ={})


def test_missing_or_mismatched_reference_fails_clearly(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)

    reference.unlink()
    with pytest.raises(PipelineCapabilityError, match="local Zaneta reference is missing"):
        verify_reference_rights_and_hash(manifest)

    _write_pcm16(reference)
    bad = dataclasses.replace(manifest, reference_sha256="0" * 64)
    with pytest.raises(PipelineCapabilityError, match="reference SHA-256 mismatch"):
        verify_reference_rights_and_hash(bad)


def test_pipeline_rejects_candidates_below_hard_threshold(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    pipeline = ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: 0.899)

    with pytest.raises(AcceptanceError, match=">= 0.900"):
        pipeline.render("Test Żanety", output, manifest=manifest)
    assert not output.exists()
    assert not output.with_suffix(".wav.manifest.json").exists()


def test_pipeline_publishes_only_accepted_pcm16_with_seed_manifest(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"
    scores = iter((0.82, 0.94))

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    pipeline = ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: next(scores))

    artifact = pipeline.render("Test Żanety", output, manifest=manifest)

    assert artifact.path == output
    assert artifact.seed == manifest.seed + 1
    assert artifact.acceptance_score == 0.94
    with wave.open(str(output), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (
            1,
            2,
            24000,
        )
    payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    assert payload["seed"] == manifest.seed + 1
    assert payload["acceptance"]["score"] == 0.94
    assert payload["acceptance"]["threshold"] == 0.9
    assert payload["source_revision"] == SOURCE_REVISION
    assert payload["model_revision"] == MODEL_REVISION
    assert payload["reference_sha256"] == manifest.reference_sha256
    assert payload["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
