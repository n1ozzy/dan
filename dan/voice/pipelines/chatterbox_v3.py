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
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
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
    model_files: Mapping[str, str] = field(default_factory=dict)
    python_version: str = ""
    python_sha256: str = ""
    package_name: str = ""
    package_version: str = ""
    package_tree_sha256: str = ""
    channels: int = 0
    sample_width_bytes: int = 0
    network_fallback: bool = True
    publish_below_threshold: bool = True
    output_manifest: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "model_files",
            MappingProxyType(dict(sorted(self.model_files.items()))),
        )


@dataclass(frozen=True)
class RenderArtifact:
    path: Path
    sha256: str
    seed: int
    acceptance_score: float
    manifest_path: Path


Runner = Callable[[str, Path, PipelineManifest, int], None]
Scorer = Callable[[Path, str, PipelineManifest], float]

_MODEL_SAMPLE_RATE = 24_000
_MODEL_FILES = frozenset(
    {
        "Cangjie5_TC.json",
        "conds.pt",
        "grapheme_mtl_merged_expanded_v1.json",
        "s3gen.pt",
        "s3gen_v3.pt",
        "t3_mtl23ls_v3.safetensors",
        "ve.pt",
    }
)

_PYTHON_PROVENANCE_PROBE = r'''
import hashlib
import json
import pathlib
import platform
import sys
from importlib import metadata

package_name = sys.argv[1]
dist = metadata.distribution(package_name)
import chatterbox

package_root = pathlib.Path(chatterbox.__file__).resolve().parent
files = sorted(
    path for path in package_root.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
)
tree = hashlib.sha256()
for path in files:
    tree.update(str(path.relative_to(package_root)).encode("utf-8") + b"\0")
    tree.update(hashlib.sha256(path.read_bytes()).digest())
direct_url_path = pathlib.Path(dist._path) / "direct_url.json"
direct_url = json.loads(direct_url_path.read_text(encoding="utf-8"))
print(json.dumps({
    "python_executable": sys.executable,
    "python_version": platform.python_version(),
    "package_name": dist.metadata["Name"],
    "package_version": dist.version,
    "package_source_revision": direct_url["vcs_info"]["commit_id"],
    "package_tree_sha256": tree.hexdigest(),
    "direct_url_path": str(direct_url_path.resolve()),
}, sort_keys=True))
'''


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
    model_files = _required_hash_table(raw, "model_files")
    reference = _required_table(raw, "reference")
    synthesis = _required_table(raw, "synthesis")
    acceptance = _required_table(raw, "acceptance")
    _require_exact_int(raw, "schema_version", 1)
    _require_exact_bool(runtime, "network_fallback", False)
    _require_exact_bool(reference, "redistribute", False)
    _require_exact_text(synthesis, "language", "pl")
    _require_exact_int(synthesis, "sample_rate", _MODEL_SAMPLE_RATE)
    _require_exact_int(synthesis, "channels", 1)
    _require_exact_int(synthesis, "sample_width_bytes", 2)
    _require_exact_bool(acceptance, "publish_below_threshold", False)
    _require_exact_bool(acceptance, "output_manifest", True)
    reference_path = _required_env_path(env, _required_text(reference, "path_env"))
    return PipelineManifest(
        name=_required_text(raw, "name"),
        source_revision=_required_revision(raw, "source_revision"),
        model_revision=_required_revision(raw, "model_revision"),
        source_metadata_path=_required_env_path(
            env, _required_text(runtime, "source_metadata_env")
        ),
        model_path=_required_env_path(env, _required_text(runtime, "model_path_env")),
        model_files=model_files,
        python_executable=_required_env_executable(
            env, _required_text(runtime, "python_env")
        ),
        python_version=_required_text(runtime, "python_version"),
        python_sha256=_required_sha(runtime, "python_sha256"),
        package_name=_required_text(runtime, "package_name"),
        package_version=_required_text(runtime, "package_version"),
        package_tree_sha256=_required_sha(runtime, "package_tree_sha256"),
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
        channels=int(synthesis["channels"]),
        sample_width_bytes=int(synthesis["sample_width_bytes"]),
        network_fallback=False,
        publish_below_threshold=False,
        output_manifest=True,
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
    if set(manifest.model_files) != _MODEL_FILES:
        raise PipelineCapabilityError(
            "Chatterbox model file set mismatch: "
            f"expected {sorted(_MODEL_FILES)}, got {sorted(manifest.model_files)}"
        )
    for filename, expected_sha in manifest.model_files.items():
        model_file = manifest.model_path / filename
        if not model_file.is_file():
            raise PipelineCapabilityError(f"pinned Chatterbox model asset is missing: {filename}")
        actual_sha = _sha256_file(model_file)
        if actual_sha != expected_sha:
            raise PipelineCapabilityError(
                "model asset SHA-256 mismatch for "
                f"{filename}: expected {expected_sha}, got {actual_sha}"
            )
    if not manifest.python_executable.is_file() or not os.access(
        manifest.python_executable, os.X_OK
    ):
        raise PipelineCapabilityError(
            f"Chatterbox Python is not executable: {manifest.python_executable}"
        )
    actual_python_sha = _sha256_file(manifest.python_executable.resolve())
    if actual_python_sha != manifest.python_sha256:
        raise PipelineCapabilityError(
            "Chatterbox Python SHA-256 mismatch: "
            f"expected {manifest.python_sha256}, got {actual_python_sha}"
        )
    provenance = _probe_python_provenance(manifest)
    reported_executable = Path(_required_probe_text(provenance, "python_executable"))
    if reported_executable.absolute() != manifest.python_executable.absolute():
        raise PipelineCapabilityError(
            "interpreter provenance mismatch: "
            f"expected {manifest.python_executable.absolute()}, "
            f"got {reported_executable.absolute()}"
        )
    expected_provenance = {
        "python_version": manifest.python_version,
        "package_name": manifest.package_name,
        "package_version": manifest.package_version,
        "package_source_revision": manifest.source_revision,
        "package_tree_sha256": manifest.package_tree_sha256,
    }
    for key, expected in expected_provenance.items():
        actual = _required_probe_text(provenance, key)
        if actual != expected:
            label = key.replace("_", " ")
            if key == "package_tree_sha256":
                label = "package tree SHA-256"
            raise PipelineCapabilityError(
                f"Chatterbox {label} mismatch: expected {expected}, got {actual}"
            )
    direct_url_path = Path(_required_probe_text(provenance, "direct_url_path"))
    if direct_url_path.resolve() != manifest.source_metadata_path.resolve():
        raise PipelineCapabilityError(
            "Chatterbox package metadata provenance mismatch: "
            f"expected {manifest.source_metadata_path.resolve()}, "
            f"got {direct_url_path.resolve()}"
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
        _verify_manifest_contract(manifest)
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
                output_sha = _sha256_file(candidate)
                manifest_path = output.with_suffix(output.suffix + ".manifest.json")
                _publish_accepted_candidate(
                    candidate,
                    output,
                    manifest_path,
                    text=text,
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
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _publish_accepted_candidate(
    candidate: Path,
    output: Path,
    manifest_path: Path,
    *,
    text: str,
    output_sha: str,
    seed: int,
    score: float,
    manifest: PipelineManifest,
) -> None:
    token = hashlib.sha256(f"{output}:{seed}".encode()).hexdigest()[:16]
    staged_manifest = manifest_path.parent / f".{manifest_path.name}.{token}.staged"
    output_backup = output.parent / f".{output.name}.{token}.backup"
    manifest_backup = manifest_path.parent / f".{manifest_path.name}.{token}.backup"
    for path in (staged_manifest, output_backup, manifest_backup):
        path.unlink(missing_ok=True)
    _write_output_manifest(
        staged_manifest,
        text=text,
        output=output,
        output_sha=output_sha,
        seed=seed,
        score=score,
        manifest=manifest,
    )
    output_had_previous = output.exists()
    manifest_had_previous = manifest_path.exists()
    try:
        if output_had_previous:
            os.replace(output, output_backup)
        if manifest_had_previous:
            os.replace(manifest_path, manifest_backup)
        os.replace(candidate, output)
        os.replace(staged_manifest, manifest_path)
    except Exception:
        output.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        if output_had_previous and output_backup.exists():
            os.replace(output_backup, output)
        if manifest_had_previous and manifest_backup.exists():
            os.replace(manifest_backup, manifest_path)
        raise
    finally:
        staged_manifest.unlink(missing_ok=True)
        output_backup.unlink(missing_ok=True)
        manifest_backup.unlink(missing_ok=True)


def _required_table(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PipelineCapabilityError(f"pipeline table {key!r} is required")
    return value


def _required_hash_table(payload: Mapping[str, Any], key: str) -> Mapping[str, str]:
    value = _required_table(payload, key)
    return MappingProxyType(
        {str(name): _required_sha(value, str(name)) for name in sorted(value)}
    )


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


def _required_env_executable(environ: Mapping[str, str], key: str) -> Path:
    value = environ.get(key)
    if not value:
        raise PipelineCapabilityError(f"required local pipeline path is not configured: {key}")
    return Path(value).expanduser().absolute()


def _required_local_only(payload: Mapping[str, Any]) -> Literal["local-only"]:
    value = _required_text(payload, "license_decision")
    if value != "local-only":
        raise PipelineCapabilityError("Zaneta reference license_decision must be local-only")
    return "local-only"


def _require_exact_bool(payload: Mapping[str, Any], key: str, expected: bool) -> None:
    value = payload.get(key)
    if not isinstance(value, bool) or value is not expected:
        raise PipelineCapabilityError(
            f"pipeline field {key!r} must be {str(expected).lower()}"
        )


def _require_exact_int(payload: Mapping[str, Any], key: str, expected: int) -> None:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value != expected:
        raise PipelineCapabilityError(f"pipeline field {key!r} must be {expected}")


def _require_exact_text(payload: Mapping[str, Any], key: str, expected: str) -> None:
    value = payload.get(key)
    if value != expected:
        raise PipelineCapabilityError(f"pipeline field {key!r} must be {expected!r}")


def _verify_manifest_contract(manifest: PipelineManifest) -> None:
    expected = {
        "network_fallback": False,
        "sample_rate": _MODEL_SAMPLE_RATE,
        "channels": 1,
        "sample_width_bytes": 2,
        "publish_below_threshold": False,
        "output_manifest": True,
    }
    for field_name, expected_value in expected.items():
        if getattr(manifest, field_name) != expected_value:
            raise PipelineCapabilityError(
                f"unsupported pipeline contract {field_name}: "
                f"expected {expected_value!r}"
            )


def _probe_python_provenance(manifest: PipelineManifest) -> Mapping[str, Any]:
    env = dict(os.environ)
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    result = subprocess.run(
        [
            str(manifest.python_executable),
            "-I",
            "-c",
            _PYTHON_PROVENANCE_PROBE,
            manifest.package_name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    if result.returncode != 0:
        raise PipelineCapabilityError(
            f"Chatterbox provenance probe failed ({result.returncode}): "
            f"{(result.stderr or '').strip()[:300]}"
        )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PipelineCapabilityError("Chatterbox provenance probe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PipelineCapabilityError("Chatterbox provenance probe returned invalid payload")
    return payload


def _required_probe_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PipelineCapabilityError(f"Chatterbox provenance is missing {key}")
    return value


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
