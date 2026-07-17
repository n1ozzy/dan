"""Pinned, offline-only Chatterbox V3 rendering for Zaneta."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class PipelineCapabilityError(RuntimeError):
    """A required local-only pipeline capability is unavailable or untrusted."""


class AcceptanceError(RuntimeError):
    """No generated candidate met the hard acceptance threshold."""


@dataclass(frozen=True)
class PipelineManifest:
    name: str
    source_revision: str
    model_revision: str
    source_metadata_path: Path
    model_path: Path
    python_executable: Path
    acceptance_gate: Path
    reference_path: Path
    reference_sha256: str
    reference_license_decision: Literal["local-only"]
    exaggeration: float
    cfg_weight: float
    temperature: float
    seed: int
    max_attempts: int
    acceptance_threshold: float
    sample_rate: int


@dataclass(frozen=True)
class RenderArtifact:
    path: Path
    sha256: str
    seed: int
    acceptance_score: float
    manifest_path: Path


Runner = Callable[[str, Path, PipelineManifest, int], None]
Scorer = Callable[[Path, str, PipelineManifest], float]


def load_pipeline_manifest(
    path: str | Path, *, environ: Mapping[str, str] | None = None
) -> PipelineManifest:
    manifest_path = Path(path)
    try:
        with manifest_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PipelineCapabilityError(
            f"could not load pipeline manifest {manifest_path}: {exc}"
        ) from exc
    env = os.environ if environ is None else environ
    runtime = _required_table(raw, "runtime")
    reference = _required_table(raw, "reference")
    synthesis = _required_table(raw, "synthesis")
    acceptance = _required_table(raw, "acceptance")
    reference_path = _required_env_path(env, _required_text(reference, "path_env"))
    return PipelineManifest(
        name=_required_text(raw, "name"),
        source_revision=_required_revision(raw, "source_revision"),
        model_revision=_required_revision(raw, "model_revision"),
        source_metadata_path=_required_env_path(
            env, _required_text(runtime, "source_metadata_env")
        ),
        model_path=_required_env_path(env, _required_text(runtime, "model_path_env")),
        python_executable=_required_env_path(env, _required_text(runtime, "python_env")),
        acceptance_gate=_required_env_path(env, _required_text(runtime, "acceptance_gate_env")),
        reference_path=reference_path,
        reference_sha256=_required_sha(reference, "sha256"),
        reference_license_decision=_required_local_only(reference),
        exaggeration=float(synthesis["exaggeration"]),
        cfg_weight=float(synthesis["cfg_weight"]),
        temperature=float(synthesis["temperature"]),
        seed=int(synthesis["seed"]),
        max_attempts=int(synthesis["max_attempts"]),
        acceptance_threshold=float(acceptance["threshold"]),
        sample_rate=int(synthesis["sample_rate"]),
    )


def verify_reference_rights_and_hash(manifest: PipelineManifest) -> None:
    if manifest.reference_license_decision != "local-only":
        raise PipelineCapabilityError("Zaneta reference must remain local-only")
    if not manifest.reference_path.is_file():
        raise PipelineCapabilityError(
            f"local Zaneta reference is missing: {manifest.reference_path}"
        )
    actual = _sha256_file(manifest.reference_path)
    if actual != manifest.reference_sha256:
        raise PipelineCapabilityError(
            "reference SHA-256 mismatch: "
            f"expected {manifest.reference_sha256}, got {actual}"
        )


def verify_pinned_runtime(manifest: PipelineManifest) -> None:
    try:
        source_metadata = json.loads(manifest.source_metadata_path.read_text(encoding="utf-8"))
        actual_source = source_metadata["vcs_info"]["commit_id"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PipelineCapabilityError(
            f"could not verify Chatterbox source metadata: {manifest.source_metadata_path}"
        ) from exc
    if actual_source != manifest.source_revision:
        raise PipelineCapabilityError(
            "Chatterbox source revision mismatch: "
            f"expected {manifest.source_revision}, got {actual_source}"
        )
    if manifest.model_path.resolve().name != manifest.model_revision:
        raise PipelineCapabilityError(
            f"Chatterbox model snapshot mismatch: expected directory {manifest.model_revision}"
        )
    for filename in ("t3_mtl23ls_v3.safetensors", "s3gen_v3.pt"):
        if not (manifest.model_path / filename).is_file():
            raise PipelineCapabilityError(f"pinned Chatterbox model asset is missing: {filename}")
    if not manifest.python_executable.is_file() or not os.access(
        manifest.python_executable, os.X_OK
    ):
        raise PipelineCapabilityError(
            f"Chatterbox Python is not executable: {manifest.python_executable}"
        )
    if not manifest.acceptance_gate.is_file():
        raise PipelineCapabilityError(
            f"Zaneta acceptance gate is missing: {manifest.acceptance_gate}"
        )


class ChatterboxV3ZanetaPipeline:
    live_capable = False

    def __init__(self, *, runner: Runner | None = None, scorer: Scorer | None = None) -> None:
        self._runner = runner or run_pinned_generator
        self._scorer = scorer or run_acceptance_gate

    def render(
        self, text: str, output: Path, *, manifest: PipelineManifest
    ) -> RenderArtifact:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Zaneta render text must be non-empty")
        if manifest.acceptance_threshold < 0.9:
            raise PipelineCapabilityError("Zaneta acceptance threshold may not be below 0.9")
        if manifest.max_attempts < 1:
            raise PipelineCapabilityError("Zaneta max_attempts must be positive")
        verify_reference_rights_and_hash(manifest)
        verify_pinned_runtime(manifest)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        last_score = 0.0
        for attempt in range(manifest.max_attempts):
            seed = manifest.seed + attempt
            candidate = output.parent / f".{output.name}.seed-{seed}.candidate.wav"
            candidate.unlink(missing_ok=True)
            try:
                self._runner(text, candidate, manifest, seed)
                _verify_pcm16(candidate, sample_rate=manifest.sample_rate)
                score = float(self._scorer(candidate, text, manifest))
                last_score = score
                if score < manifest.acceptance_threshold:
                    continue
                os.replace(candidate, output)
                output_sha = _sha256_file(output)
                manifest_path = output.with_suffix(output.suffix + ".manifest.json")
                _write_output_manifest(
                    manifest_path,
                    text=text,
                    output=output,
                    output_sha=output_sha,
                    seed=seed,
                    score=score,
                    manifest=manifest,
                )
                return RenderArtifact(output, output_sha, seed, score, manifest_path)
            finally:
                candidate.unlink(missing_ok=True)
        raise AcceptanceError(
            "Zaneta candidate did not reach hard acceptance "
            f">= {manifest.acceptance_threshold:.3f}; best={last_score:.3f}"
        )


def run_pinned_generator(
    text: str, output: Path, manifest: PipelineManifest, seed: int
) -> None:
    worker = r'''
import json, os, random, sys, wave
cfg = json.loads(sys.argv[1])
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.5")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.4")
import numpy as np
import torch
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
seed = int(cfg["seed"])
random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
device = "mps" if torch.backends.mps.is_available() else "cpu"
model = ChatterboxMultilingualTTS.from_local(cfg["model"], device, t3_model="v3")
sd = torch.load(os.path.join(cfg["model"], "s3gen_v3.pt"), map_location="cpu", weights_only=True)
model.s3gen.load_state_dict(sd, strict=False); model.s3gen.to(device).eval()
wav = model.generate(cfg["text"], language_id="pl", audio_prompt_path=cfg["reference"],
    exaggeration=cfg["exaggeration"], cfg_weight=cfg["cfg_weight"], temperature=cfg["temperature"])
audio = wav.detach().cpu().numpy().squeeze()
i16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
with wave.open(cfg["output"], "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(int(cfg["sample_rate"]))
    handle.writeframes(i16.tobytes())
'''
    payload = {
        "text": text,
        "output": str(output),
        "model": str(manifest.model_path),
        "reference": str(manifest.reference_path),
        "exaggeration": manifest.exaggeration,
        "cfg_weight": manifest.cfg_weight,
        "temperature": manifest.temperature,
        "sample_rate": manifest.sample_rate,
        "seed": seed,
    }
    env = dict(os.environ)
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    result = subprocess.run(
        [str(manifest.python_executable), "-c", worker, json.dumps(payload, ensure_ascii=False)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise PipelineCapabilityError(
            f"pinned Chatterbox generator failed ({result.returncode}): "
            f"{(result.stderr or '').strip()[:300]}"
        )


def run_acceptance_gate(candidate: Path, text: str, manifest: PipelineManifest) -> float:
    env = dict(os.environ)
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    result = subprocess.run(
        [
            str(manifest.python_executable),
            str(manifest.acceptance_gate),
            str(candidate),
            text,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise PipelineCapabilityError(
            f"Zaneta acceptance gate failed ({result.returncode}): "
            f"{(result.stderr or '').strip()[:300]}"
        )
    match = re.search(r"RATIO\s+([0-9.]+)", result.stdout or "")
    if match is None:
        raise PipelineCapabilityError("Zaneta acceptance gate returned no RATIO")
    return float(match.group(1))


def _verify_pcm16(path: Path, *, sample_rate: int) -> None:
    try:
        with wave.open(str(path), "rb") as handle:
            actual = (handle.getnchannels(), handle.getsampwidth(), handle.getframerate())
    except (OSError, wave.Error) as exc:
        raise PipelineCapabilityError(f"generator produced invalid WAV: {path}") from exc
    expected = (1, 2, sample_rate)
    if actual != expected:
        raise PipelineCapabilityError(
            f"generator WAV must be mono PCM16 at {sample_rate} Hz; got {actual}"
        )


def _write_output_manifest(
    path: Path,
    *,
    text: str,
    output: Path,
    output_sha: str,
    seed: int,
    score: float,
    manifest: PipelineManifest,
) -> None:
    payload = {
        "schema_version": 1,
        "pipeline": manifest.name,
        "source_revision": manifest.source_revision,
        "model_revision": manifest.model_revision,
        "reference_sha256": manifest.reference_sha256,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "output": output.name,
        "output_sha256": output_sha,
        "seed": seed,
        "synthesis": {
            "exaggeration": manifest.exaggeration,
            "cfg_weight": manifest.cfg_weight,
            "temperature": manifest.temperature,
        },
        "wave": {"channels": 1, "sample_width_bytes": 2, "sample_rate": manifest.sample_rate},
        "acceptance": {"score": score, "threshold": manifest.acceptance_threshold},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _required_table(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PipelineCapabilityError(f"pipeline table {key!r} is required")
    return value


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineCapabilityError(f"pipeline field {key!r} is required")
    return value.strip()


def _required_sha(payload: Mapping[str, Any], key: str) -> str:
    value = _required_text(payload, key).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PipelineCapabilityError(f"pipeline field {key!r} must be SHA-256")
    return value


def _required_revision(payload: Mapping[str, Any], key: str) -> str:
    value = _required_text(payload, key).lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise PipelineCapabilityError(f"pipeline field {key!r} must be a 40-character revision")
    return value


def _required_env_path(environ: Mapping[str, str], key: str) -> Path:
    value = environ.get(key)
    if not value:
        raise PipelineCapabilityError(f"required local pipeline path is not configured: {key}")
    return Path(value).expanduser().resolve()


def _required_local_only(payload: Mapping[str, Any]) -> Literal["local-only"]:
    value = _required_text(payload, "license_decision")
    if value != "local-only":
        raise PipelineCapabilityError("Zaneta reference license_decision must be local-only")
    return "local-only"


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PipelineCapabilityError(f"could not hash local pipeline file {path}: {exc}") from exc


__all__ = [
    "AcceptanceError",
    "ChatterboxV3ZanetaPipeline",
    "PipelineCapabilityError",
    "PipelineManifest",
    "RenderArtifact",
    "load_pipeline_manifest",
    "run_acceptance_gate",
    "run_pinned_generator",
    "verify_pinned_runtime",
    "verify_reference_rights_and_hash",
]
