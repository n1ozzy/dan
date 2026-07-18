from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import stat
import wave
from pathlib import Path

import pytest

import dan.voice.pipelines.chatterbox_v3 as chatterbox_v3
from dan.voice.pipelines.chatterbox_v3 import (
    AcceptanceError,
    ChatterboxV3ZanetaPipeline,
    PipelineCapabilityError,
    PipelineManifest,
    load_pipeline_manifest,
    run_pinned_generator,
    verify_pinned_runtime,
    verify_reference_rights_and_hash,
)


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = ROOT / "config" / "voice" / "pipelines" / "chatterbox-v3-zaneta.toml"
SOURCE_REVISION = "65b18437192794391a0308a8f705b1e33e633948"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
MODEL_REPO_ID = "ResembleAI/chatterbox"
MODEL_LOCK_NAME = "snapshot-lock.json"
REFERENCE_SHA256 = "06f54e0f140c8caeb8911cea60918c29c5ffac30bd0b2018e18d01715b1b986c"
PYTHON_VERSION = "3.14.6"
PACKAGE_TREE_SHA256 = "4142a07efda1a0778c709bd70135e868ed25ab73c64de60b05a0bd63dde29b43"
MODEL_FILES = {
    "Cangjie5_TC.json": "7073fd9de919443ae88e0bd2449917a65fe54898a4413ed1edcc4b67f28bce8c",
    "conds.pt": "6552d70568833628ba019c6b03459e77fe71ca197d5c560cef9411bee9d87f4e",
    "grapheme_mtl_merged_expanded_v1.json": "69632f47220a788a52ce2661d096453c5655e9bf25289d89a8d832c46ee07dbf",
    "s3gen.pt": "9b9ff07e60b20c136e2b1b3d7563a24604e8d2c4c267888d1ee929dd0151d2a3",
    "s3gen_v3.pt": "f7abce4b196dae2d08d9296cbebc6521b046079577643b42a19a03499d08721e",
    "t3_mtl23ls_v3.safetensors": "5abca8321ede76f8e61f1cc0d19aea6c946b28871017ce8726f8a69203f05953",
    "ve.pt": "4b16d836bc598509860f6fa068165a8bb5e9ac84f05582dfcf278a5a372879f1",
}


def _write_pcm16(path: Path, *, channels: int = 1, rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * channels * 32)


def _probe_python(
    path: Path,
    *,
    package_tree_sha256: str,
    direct_url_path: Path,
    reported_executable: Path | None = None,
) -> tuple[Path, str]:
    payload = {
        "python_executable": str((reported_executable or path).resolve()),
        "python_version": PYTHON_VERSION,
        "package_name": "chatterbox-tts",
        "package_version": "0.1.7",
        "package_source_revision": SOURCE_REVISION,
        "package_tree_sha256": package_tree_sha256,
        "direct_url_path": str(direct_url_path.resolve()),
    }
    body = "#!/bin/sh\nprintf '%s\\n' " + repr(json.dumps(payload)) + "\n"
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _write_snapshot_lock(
    model_path: Path,
    model_files: dict[str, str],
    *,
    repo_id: str = MODEL_REPO_ID,
    revision: str = MODEL_REVISION,
) -> Path:
    lock_path = model_path / MODEL_LOCK_NAME
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo_id": repo_id,
                "revision": revision,
                "files": model_files,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return lock_path


def _manifest(tmp_path: Path, reference: Path) -> PipelineManifest:
    model = tmp_path / MODEL_REVISION
    model.mkdir()
    model_files = {}
    for name in MODEL_FILES:
        path = model / name
        path.write_bytes(f"model:{name}".encode("utf-8"))
        model_files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_snapshot_lock(model, model_files)
    metadata = tmp_path / "direct_url.json"
    metadata.write_text(
        json.dumps({"vcs_info": {"commit_id": SOURCE_REVISION, "vcs": "git"}}),
        encoding="utf-8",
    )
    python, python_sha256 = _probe_python(
        tmp_path / "chatterbox-python",
        package_tree_sha256=PACKAGE_TREE_SHA256,
        direct_url_path=metadata,
    )
    gate = tmp_path / "gate.py"
    gate.write_text("# test gate\n", encoding="utf-8")
    return PipelineManifest(
        name="chatterbox-v3-zaneta",
        source_revision=SOURCE_REVISION,
        model_revision=MODEL_REVISION,
        source_metadata_path=metadata,
        model_path=model,
        model_files=model_files,
        python_executable=python,
        python_version=PYTHON_VERSION,
        python_sha256=python_sha256,
        package_name="chatterbox-tts",
        package_version="0.1.7",
        package_tree_sha256=PACKAGE_TREE_SHA256,
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
        channels=1,
        sample_width_bytes=2,
        network_fallback=False,
        publish_below_threshold=False,
        output_manifest=True,
    )


def test_versioned_manifest_pins_sources_parameters_and_local_inputs(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "zaneta-reference.wav"
    _write_pcm16(reference)
    python_link = tmp_path / "chatterbox-python"
    python_link.symlink_to(Path(os.sys.executable))
    env = {
        "DAN_CHATTERBOX_V3_DIRECT_URL": str(tmp_path / "direct_url.json"),
        "DAN_CHATTERBOX_V3_MODEL_DIR": str(tmp_path / MODEL_REVISION),
        "DAN_CHATTERBOX_V3_PYTHON": str(python_link),
        "DAN_ZANETA_ACCEPTANCE_GATE": str(tmp_path / "gate.py"),
        "DAN_ZANETA_REFERENCE_WAV": str(reference),
    }
    (tmp_path / "direct_url.json").write_text("{}", encoding="utf-8")
    (tmp_path / MODEL_REVISION).mkdir()
    (tmp_path / "gate.py").write_text("", encoding="utf-8")

    manifest = load_pipeline_manifest(PIPELINE_PATH, environ=env)

    assert manifest.source_revision == SOURCE_REVISION
    assert manifest.model_revision == MODEL_REVISION
    assert manifest.model_repo_id == MODEL_REPO_ID
    assert manifest.model_lock_path == tmp_path / MODEL_REVISION / MODEL_LOCK_NAME
    assert manifest.reference_sha256 == REFERENCE_SHA256
    assert (manifest.exaggeration, manifest.cfg_weight, manifest.temperature) == (
        0.6,
        0.5,
        0.8,
    )
    assert manifest.seed == 730_711
    assert manifest.acceptance_threshold == 0.9
    assert manifest.sample_rate == 24000
    assert manifest.channels == 1
    assert manifest.sample_width_bytes == 2
    assert manifest.network_fallback is False
    assert manifest.publish_below_threshold is False
    assert manifest.output_manifest is True
    assert manifest.python_version == PYTHON_VERSION
    assert manifest.package_name == "chatterbox-tts"
    assert manifest.package_version == "0.1.7"
    assert manifest.package_tree_sha256 == PACKAGE_TREE_SHA256
    assert dict(manifest.model_files) == MODEL_FILES
    assert manifest.python_executable == python_link.absolute()


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        ("network_fallback = false", "network_fallback = true", "network_fallback"),
        ("redistribute = false", "redistribute = true", "redistribute"),
        ("sample_rate = 24000", "sample_rate = 22050", "sample_rate"),
        ("channels = 1", "channels = 2", "channels"),
        ("sample_width_bytes = 2", "sample_width_bytes = 4", "sample_width_bytes"),
        ("publish_below_threshold = false", "publish_below_threshold = true", "publish_below_threshold"),
        ("output_manifest = true", "output_manifest = false", "output_manifest"),
    ),
)
def test_manifest_fails_closed_on_unsupported_contract_values(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    altered = tmp_path / "pipeline.toml"
    altered.write_text(
        PIPELINE_PATH.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
    )
    env = {
        "DAN_CHATTERBOX_V3_DIRECT_URL": str(tmp_path / "direct_url.json"),
        "DAN_CHATTERBOX_V3_MODEL_DIR": str(tmp_path / MODEL_REVISION),
        "DAN_CHATTERBOX_V3_PYTHON": os.sys.executable,
        "DAN_ZANETA_ACCEPTANCE_GATE": str(tmp_path / "gate.py"),
        "DAN_ZANETA_REFERENCE_WAV": str(tmp_path / "reference.wav"),
    }

    with pytest.raises(PipelineCapabilityError, match=message):
        load_pipeline_manifest(altered, environ=env)


@pytest.mark.parametrize(
    ("field", "valid", "invalid"),
    (
        ("exaggeration", "0.6", "nan"),
        ("exaggeration", "0.6", "inf"),
        ("exaggeration", "0.6", "-inf"),
        ("cfg_weight", "0.5", "nan"),
        ("cfg_weight", "0.5", "inf"),
        ("cfg_weight", "0.5", "-inf"),
        ("temperature", "0.8", "nan"),
        ("temperature", "0.8", "inf"),
        ("temperature", "0.8", "-inf"),
        ("threshold", "0.9", "nan"),
        ("threshold", "0.9", "inf"),
        ("threshold", "0.9", "-inf"),
    ),
)
def test_manifest_rejects_every_non_finite_config_float(
    tmp_path: Path, field: str, valid: str, invalid: str
) -> None:
    altered = tmp_path / "pipeline.toml"
    altered.write_text(
        PIPELINE_PATH.read_text(encoding="utf-8").replace(
            f"{field} = {valid}", f"{field} = {invalid}"
        ),
        encoding="utf-8",
    )
    env = {
        "DAN_CHATTERBOX_V3_DIRECT_URL": str(tmp_path / "direct_url.json"),
        "DAN_CHATTERBOX_V3_MODEL_DIR": str(tmp_path / MODEL_REVISION),
        "DAN_CHATTERBOX_V3_PYTHON": os.sys.executable,
        "DAN_ZANETA_ACCEPTANCE_GATE": str(tmp_path / "gate.py"),
        "DAN_ZANETA_REFERENCE_WAV": str(tmp_path / "reference.wav"),
    }

    with pytest.raises(PipelineCapabilityError, match=rf"{field}.*finite"):
        load_pipeline_manifest(altered, environ=env)


def test_manifest_rejects_configured_threshold_below_hard_floor(tmp_path: Path) -> None:
    altered = tmp_path / "pipeline.toml"
    altered.write_text(
        PIPELINE_PATH.read_text(encoding="utf-8").replace(
            "threshold = 0.9", "threshold = 0.899"
        ),
        encoding="utf-8",
    )
    env = {
        "DAN_CHATTERBOX_V3_DIRECT_URL": str(tmp_path / "direct_url.json"),
        "DAN_CHATTERBOX_V3_MODEL_DIR": str(tmp_path / MODEL_REVISION),
        "DAN_CHATTERBOX_V3_PYTHON": os.sys.executable,
        "DAN_ZANETA_ACCEPTANCE_GATE": str(tmp_path / "gate.py"),
        "DAN_ZANETA_REFERENCE_WAV": str(tmp_path / "reference.wav"),
    }

    with pytest.raises(PipelineCapabilityError, match="threshold.*at least 0.9"):
        load_pipeline_manifest(altered, environ=env)


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


def test_pinned_runtime_rejects_wrong_package_and_model_bytes(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)

    verify_pinned_runtime(manifest)

    bad_package = copy.copy(manifest)
    object.__setattr__(bad_package, "package_tree_sha256", "0" * 64)
    with pytest.raises(PipelineCapabilityError, match="package tree SHA-256 mismatch"):
        verify_pinned_runtime(bad_package)

    model_file = manifest.model_path / "s3gen_v3.pt"
    model_file.write_bytes(b"wrong model bytes")
    with pytest.raises(PipelineCapabilityError, match="model asset SHA-256 mismatch.*s3gen_v3.pt"):
        verify_pinned_runtime(manifest)


def test_pinned_runtime_rejects_mismatched_interpreter_provenance(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    wrong = tmp_path / "different-python"
    python, python_sha256 = _probe_python(
        tmp_path / "mismatched-python",
        package_tree_sha256=PACKAGE_TREE_SHA256,
        direct_url_path=manifest.source_metadata_path,
        reported_executable=wrong,
    )
    mismatched = copy.copy(manifest)
    object.__setattr__(mismatched, "python_executable", python)
    object.__setattr__(mismatched, "python_sha256", python_sha256)

    with pytest.raises(PipelineCapabilityError, match="interpreter provenance mismatch"):
        verify_pinned_runtime(mismatched)


def test_pinned_runtime_requires_snapshot_lock(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    (manifest.model_path / MODEL_LOCK_NAME).unlink()

    with pytest.raises(PipelineCapabilityError, match="snapshot lock.*missing"):
        verify_pinned_runtime(manifest)


def test_pinned_runtime_rejects_forged_snapshot_revision(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    _write_snapshot_lock(
        manifest.model_path,
        dict(manifest.model_files),
        revision="0" * 40,
    )

    with pytest.raises(PipelineCapabilityError, match="snapshot revision mismatch"):
        verify_pinned_runtime(manifest)


def test_pinned_runtime_rejects_mismatched_snapshot_file_map(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    forged_files = dict(manifest.model_files)
    forged_files["s3gen_v3.pt"] = "0" * 64
    _write_snapshot_lock(manifest.model_path, forged_files)

    with pytest.raises(PipelineCapabilityError, match="snapshot file map mismatch"):
        verify_pinned_runtime(manifest)


def test_generator_uses_verified_isolated_package_despite_hostile_import_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    hostile = tmp_path / "hostile-cwd"
    hostile_package = hostile / "chatterbox"
    hostile_package.mkdir(parents=True)
    (hostile_package / "__init__.py").write_text(
        "raise RuntimeError('hostile chatterbox imported')\n", encoding="utf-8"
    )
    monkeypatch.chdir(hostile)
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    monkeypatch.setenv("PYTHONHOME", str(hostile / "fake-home"))
    calls: list[tuple[list[str], dict[str, object]]] = []

    def external_edge(argv: list[str], **kwargs: object):
        calls.append((argv, kwargs))
        if argv[-1] == manifest.package_name:
            payload = {
                "python_executable": str(manifest.python_executable.resolve()),
                "python_version": manifest.python_version,
                "package_name": manifest.package_name,
                "package_version": manifest.package_version,
                "package_source_revision": manifest.source_revision,
                "package_tree_sha256": manifest.package_tree_sha256,
                "direct_url_path": str(manifest.source_metadata_path.resolve()),
            }
            return chatterbox_v3.subprocess.CompletedProcess(
                argv, 0, json.dumps(payload), ""
            )
        return chatterbox_v3.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(chatterbox_v3.subprocess, "run", external_edge)

    verify_pinned_runtime(manifest)
    run_pinned_generator("Izolowany test", tmp_path / "candidate.wav", manifest, 730_711)

    assert len(calls) == 2
    for argv, kwargs in calls:
        assert argv[:2] == [str(manifest.python_executable), "-I"]
        assert Path(str(kwargs["cwd"])).resolve() == manifest.model_path.resolve()
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert not any(key.upper().startswith("PYTHON") for key in environment)
    generator_argv = calls[1][0]
    assert "from chatterbox.mtl_tts import ChatterboxMultilingualTTS" in generator_argv[3]


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


@pytest.mark.parametrize("score", (float("nan"), float("inf"), float("-inf")))
def test_pipeline_rejects_non_finite_acceptance_scores(
    tmp_path: Path, score: float
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    pipeline = ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: score)

    with pytest.raises(PipelineCapabilityError, match="acceptance score.*finite"):
        pipeline.render("Test wyniku", output, manifest=manifest)

    assert not output.exists()
    assert not output.with_suffix(".wav.manifest.json").exists()


@pytest.mark.parametrize("threshold", (float("nan"), float("inf"), float("-inf"), 0.899))
def test_pipeline_rejects_invalid_runtime_threshold(
    tmp_path: Path, threshold: float
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = dataclasses.replace(_manifest(tmp_path, reference), acceptance_threshold=threshold)

    with pytest.raises(PipelineCapabilityError, match="acceptance_threshold"):
        ChatterboxV3ZanetaPipeline().render(
            "Test progu", tmp_path / "zaneta.wav", manifest=manifest
        )


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


def test_manifest_write_failure_never_publishes_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    def fail_manifest(*args, **kwargs) -> None:
        raise OSError("injected manifest write failure")

    monkeypatch.setattr(
        "dan.voice.pipelines.chatterbox_v3._write_output_manifest",
        fail_manifest,
    )
    pipeline = ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: 0.95)

    with pytest.raises(OSError, match="injected manifest write failure"):
        pipeline.render("Test publikacji", output, manifest=manifest)

    assert not output.exists()
    assert not output.with_suffix(".wav.manifest.json").exists()


def test_publication_replaces_final_manifest_before_final_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"
    manifest_path = output.with_suffix(".wav.manifest.json")
    final_destinations: list[Path] = []
    real_replace = os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        destination_path = Path(destination)
        if destination_path in {manifest_path, output}:
            final_destinations.append(destination_path)
        real_replace(source, destination)

    monkeypatch.setattr(chatterbox_v3.os, "replace", recording_replace)

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: 0.95).render(
        "Test kolejności", output, manifest=manifest
    )

    assert final_destinations == [manifest_path, output]


def test_keyboard_interrupt_before_final_manifest_never_exposes_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"
    manifest_path = output.with_suffix(".wav.manifest.json")
    real_replace = os.replace

    def interrupt_before_manifest(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == manifest_path:
            raise KeyboardInterrupt("injected before final manifest")
        real_replace(source, destination)

    monkeypatch.setattr(chatterbox_v3.os, "replace", interrupt_before_manifest)

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    with pytest.raises(KeyboardInterrupt, match="before final manifest"):
        ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: 0.95).render(
            "Test przerwania", output, manifest=manifest
        )

    manifest_visible = manifest_path.exists()
    wav_published = output.exists()
    assert manifest_visible is False
    assert wav_published is False


@pytest.mark.parametrize(
    "failure_point",
    ("output_backup", "manifest_backup", "final_manifest", "final_wav"),
)
def test_base_exception_during_publication_restores_previous_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    reference = tmp_path / "reference.wav"
    _write_pcm16(reference)
    manifest = _manifest(tmp_path, reference)
    output = tmp_path / "zaneta.wav"
    manifest_path = output.with_suffix(".wav.manifest.json")
    old_wav = b"previous-wav"
    old_manifest = b'{"previous": true}\n'
    output.write_bytes(old_wav)
    manifest_path.write_bytes(old_manifest)
    real_replace = os.replace
    injected = False

    def classify(destination: Path) -> str | None:
        if destination == manifest_path:
            return "final_manifest"
        if destination == output:
            return "final_wav"
        if destination.name.startswith(f".{manifest_path.name}.") and destination.name.endswith(
            ".backup"
        ):
            return "manifest_backup"
        if destination.name.startswith(f".{output.name}.") and destination.name.endswith(
            ".backup"
        ):
            return "output_backup"
        return None

    def interrupt_after_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal injected
        real_replace(source, destination)
        if not injected and classify(Path(destination)) == failure_point:
            injected = True
            raise KeyboardInterrupt(f"injected after {failure_point}")

    monkeypatch.setattr(chatterbox_v3.os, "replace", interrupt_after_replace)

    def runner(text: str, candidate: Path, runtime: PipelineManifest, seed: int) -> None:
        _write_pcm16(candidate, rate=runtime.sample_rate)

    with pytest.raises(KeyboardInterrupt, match=failure_point):
        ChatterboxV3ZanetaPipeline(runner=runner, scorer=lambda *_: 0.95).render(
            "Test rollbacku", output, manifest=manifest
        )

    assert injected is True
    assert output.read_bytes() == old_wav
    assert manifest_path.read_bytes() == old_manifest
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.name.endswith((".backup", ".staged")) or ".candidate.wav" in path.name
    ]
