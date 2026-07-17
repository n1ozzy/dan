"""The sole authority that turns speech intent into immutable render truth."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dan.voice.models import RenderSnapshot, SnapshotValidationError, SpeechIntent


class VoiceResolverError(RuntimeError):
    """Voice catalog or engine metadata cannot produce a valid snapshot."""


@dataclass(frozen=True)
class AssetMetadata:
    path: Path
    sha256: str

    @classmethod
    def from_path(cls, path: str | Path) -> AssetMetadata:
        resolved = Path(path)
        return cls(path=resolved, sha256=_sha256_file(resolved))


@dataclass(frozen=True)
class EngineMetadata:
    version: str
    assets: Mapping[str, AssetMetadata]

    def __post_init__(self) -> None:
        object.__setattr__(self, "assets", MappingProxyType(dict(self.assets)))


@dataclass(frozen=True)
class VoiceCatalog:
    personas: Mapping[str, Mapping[str, Any]]
    pronunciations: Mapping[str, str]
    pronunciations_sha256: str
    asset_sha256: Mapping[str, str]
    revision: str

    def __post_init__(self) -> None:
        frozen_personas = {
            str(name): MappingProxyType(dict(spec)) for name, spec in self.personas.items()
        }
        object.__setattr__(self, "personas", MappingProxyType(frozen_personas))
        object.__setattr__(
            self,
            "pronunciations",
            MappingProxyType(dict(sorted(self.pronunciations.items()))),
        )
        object.__setattr__(
            self,
            "asset_sha256",
            MappingProxyType(dict(sorted(self.asset_sha256.items()))),
        )

    @classmethod
    def from_directory(cls, directory: str | Path, *, strict: bool = True) -> VoiceCatalog:
        root = Path(directory).expanduser()
        personas_path = root / "personas.toml"
        pronunciations_path = root / "pronunciations.toml"
        personas_raw = _read_toml(personas_path, strict=strict)
        pronunciations_raw = _read_toml(pronunciations_path, strict=strict)
        personas = {
            str(name): dict(spec)
            for name, spec in personas_raw.items()
            if isinstance(name, str) and isinstance(spec, Mapping)
        }
        pronunciations = {
            str(key).lower(): str(value)
            for key, value in pronunciations_raw.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if strict and not personas:
            raise VoiceResolverError(f"voice catalog has no personas: {personas_path}")

        assets: dict[str, str] = {}
        if personas_path.is_file():
            assets["voice.personas"] = _sha256_file(personas_path)
        if pronunciations_path.is_file():
            assets["voice.pronunciations"] = _sha256_file(pronunciations_path)
        pronunciation_json = _canonical_json(pronunciations)
        pronunciation_hash = hashlib.sha256(pronunciation_json.encode("utf-8")).hexdigest()
        revision_payload = {
            "personas": personas,
            "pronunciations": pronunciations,
            "assets": assets,
        }
        revision = hashlib.sha256(_canonical_json(revision_payload).encode("utf-8")).hexdigest()
        return cls(
            personas=personas,
            pronunciations=pronunciations,
            pronunciations_sha256=pronunciation_hash,
            asset_sha256=assets,
            revision=revision,
        )


class VoiceResolver:
    """Resolve all engine-owned fields exactly once from immutable inputs."""

    def __init__(
        self,
        catalog: VoiceCatalog,
        installation_config: Any,
        engine_registry: Mapping[str, EngineMetadata],
    ) -> None:
        self._catalog = catalog
        self._installation_config = installation_config
        self._engines = MappingProxyType(dict(engine_registry))

    def resolve_mapping(self, payload: Mapping[str, Any]) -> RenderSnapshot:
        source = str(payload.get("source", "unknown"))
        session = str(payload.get("session", "unknown"))
        return self.resolve(SpeechIntent.from_mapping(payload, source=source, session=session))

    def resolve(self, intent: SpeechIntent) -> RenderSnapshot:
        spec = self._catalog.personas.get(intent.persona)
        if spec is None:
            raise SnapshotValidationError(f"unknown voice persona: {intent.persona}")
        engine_name = _required_spec_text(spec, "engine", default="supertonic")
        engine = self._engines.get(engine_name)
        if engine is None:
            raise SnapshotValidationError(f"unregistered voice engine: {engine_name}")
        version = str(engine.version or "").strip()
        if not version:
            raise SnapshotValidationError(f"voice engine {engine_name!r} has no version")

        asset_hashes = dict(self._catalog.asset_sha256)
        for name, asset in engine.assets.items():
            actual = _sha256_file(asset.path)
            if actual != asset.sha256:
                raise SnapshotValidationError(
                    f"SHA-256 mismatch for {engine_name} asset {name}: "
                    f"expected {asset.sha256}, got {actual}"
                )
            asset_hashes[f"engine.{engine_name}.{name}"] = actual
        if not engine.assets:
            raise SnapshotValidationError(f"voice engine {engine_name!r} has no verified assets")

        gain = _installation_value(self._installation_config, "voice.output_gain", 1.0)
        try:
            gain = float(gain)
        except (TypeError, ValueError) as exc:
            raise SnapshotValidationError("voice.output_gain must be a number") from exc
        config_revision = hashlib.sha256(
            f"{self._catalog.revision}:{_config_revision(self._installation_config)}".encode()
        ).hexdigest()
        snapshot = RenderSnapshot(
            engine=engine_name,
            engine_version=version,
            voice_or_style=_required_spec_text(spec, "voice"),
            speed=_positive_float(spec.get("speed", 1.0), "speed"),
            mastering_profile=str(spec.get("mastering", "raw")),
            dsp=str(spec.get("dsp", "none")),
            pronunciations=self._catalog.pronunciations,
            pronunciations_sha256=self._catalog.pronunciations_sha256,
            gain=gain,
            asset_sha256=asset_hashes,
            config_revision=config_revision,
        )
        snapshot.validate_complete()
        return snapshot

    @staticmethod
    def compatibility_voice_config(catalog: VoiceCatalog, voice_config: Any) -> Any:
        """One temporary legacy projection, implemented only from catalog truth."""

        shared_voices: dict[str, str] = {}
        shared_mastering: dict[str, str] = {}
        shared_speeds: dict[str, float] = {}
        for name, spec in catalog.personas.items():
            voice = spec.get("voice")
            mastering = spec.get("mastering")
            speed = spec.get("speed")
            if isinstance(voice, str) and voice.strip():
                shared_voices[name] = voice.strip()
            if isinstance(mastering, str):
                normalized = mastering.strip()
                shared_mastering[name] = (
                    "" if normalized.lower() in {"raw", "none", ""} else normalized
                )
            if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
                shared_speeds[name] = float(speed)
        return dataclasses.replace(
            voice_config,
            tts_pronunciations={
                **dict(catalog.pronunciations),
                **dict(getattr(voice_config, "tts_pronunciations", {}) or {}),
            },
            persona_voices={
                **shared_voices,
                **dict(getattr(voice_config, "persona_voices", {}) or {}),
            },
            persona_mastering={
                **shared_mastering,
                **dict(getattr(voice_config, "persona_mastering", {}) or {}),
            },
            persona_speeds={
                **shared_speeds,
                **dict(getattr(voice_config, "persona_speeds", {}) or {}),
            },
        )

    @staticmethod
    def compatibility_snapshot(voice_config: Any, intent: SpeechIntent) -> RenderSnapshot:
        """Project legacy ``VoiceConfig`` through the same snapshot contract."""

        persona = intent.persona
        voice = dict(getattr(voice_config, "persona_voices", {}) or {}).get(
            persona, getattr(voice_config, "supertonic_voice", "M1")
        )
        speed = dict(getattr(voice_config, "persona_speeds", {}) or {}).get(
            persona, getattr(voice_config, "supertonic_speed", 1.0)
        )
        mastering = dict(getattr(voice_config, "persona_mastering", {}) or {}).get(
            persona, getattr(voice_config, "mastering_profile", "")
        )
        pronunciations = {
            str(key).lower(): str(value)
            for key, value in dict(
                getattr(voice_config, "tts_pronunciations", {}) or {}
            ).items()
        }
        config_payload = {
            "engine": getattr(voice_config, "default_tts", "supertonic"),
            "voice": voice,
            "speed": speed,
            "mastering": mastering,
            "pronunciations": pronunciations,
            "gain": getattr(voice_config, "output_gain", 1.0),
        }
        revision = hashlib.sha256(_canonical_json(config_payload).encode()).hexdigest()
        snapshot = RenderSnapshot(
            engine=str(config_payload["engine"] or "supertonic"),
            engine_version="compatibility-unverified",
            voice_or_style=str(voice or "M1"),
            speed=_positive_float(speed, "speed"),
            mastering_profile=str(mastering or "raw"),
            dsp="none",
            pronunciations=pronunciations,
            pronunciations_sha256=hashlib.sha256(
                _canonical_json(pronunciations).encode()
            ).hexdigest(),
            gain=_positive_float(config_payload["gain"], "gain"),
            asset_sha256={"compatibility.config": revision},
            config_revision=revision,
        )
        snapshot.validate_complete()
        return snapshot


def _read_toml(path: Path, *, strict: bool) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        if strict:
            raise VoiceResolverError(f"voice catalog file does not exist: {path}") from None
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        if strict:
            raise VoiceResolverError(f"could not load voice catalog file {path}: {exc}") from exc
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SnapshotValidationError(f"could not hash voice asset {path}: {exc}") from exc


def _required_spec_text(
    spec: Mapping[str, Any], key: str, *, default: str | None = None
) -> str:
    value = spec.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError(f"voice persona {key} is missing")
    return value.strip()


def _positive_float(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise SnapshotValidationError(f"voice persona {name} must be greater than zero")
    return float(value)


def _installation_value(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    if isinstance(config, Mapping):
        current: Any = config
        for segment in key.split("."):
            if not isinstance(current, Mapping) or segment not in current:
                return default
            current = current[segment]
        return current
    return default


def _config_revision(config: Any) -> str:
    revision = getattr(config, "revision", None)
    if isinstance(revision, str) and revision:
        return revision
    if isinstance(config, Mapping):
        return hashlib.sha256(_canonical_json(config).encode()).hexdigest()
    return hashlib.sha256(repr(config).encode()).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "AssetMetadata",
    "EngineMetadata",
    "SnapshotValidationError",
    "VoiceCatalog",
    "VoiceResolver",
    "VoiceResolverError",
]
