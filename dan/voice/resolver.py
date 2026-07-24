"""The sole authority that turns speech intent into immutable render truth."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dan.voice.models import (
    SEED_MAX,
    TEMPO_MAX,
    TEMPO_MIN,
    RenderSnapshot,
    SnapshotValidationError,
    SpeechIntent,
)

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
    assets: Mapping[str, AssetMetadata]
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
            "assets",
            MappingProxyType(dict(sorted(self.assets.items()))),
        )

    @property
    def asset_sha256(self) -> Mapping[str, str]:
        return MappingProxyType(
            {name: asset.sha256 for name, asset in self.assets.items()}
        )

    @classmethod
    def from_directory(cls, directory: str | Path, *, strict: bool = True) -> VoiceCatalog:
        root = Path(directory).expanduser()
        personas_path = root / "personas.toml"
        pronunciations_path = root / "pronunciations.toml"
        # One read per file: content and frozen SHA must describe the same
        # bytes, otherwise a concurrent write between the two reads produces a
        # catalog whose integrity check passes while its routes are stale.
        personas_bytes = _read_bytes(personas_path, strict=strict)
        pronunciations_bytes = _read_bytes(pronunciations_path, strict=strict)
        personas_raw = _parse_toml(personas_bytes, personas_path, strict=strict)
        pronunciations_raw = _parse_toml(
            pronunciations_bytes, pronunciations_path, strict=strict
        )
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

        assets: dict[str, AssetMetadata] = {}
        if personas_bytes is not None:
            assets["voice.personas"] = AssetMetadata(
                path=personas_path,
                sha256=hashlib.sha256(personas_bytes).hexdigest(),
            )
        if pronunciations_bytes is not None:
            assets["voice.pronunciations"] = AssetMetadata(
                path=pronunciations_path,
                sha256=hashlib.sha256(pronunciations_bytes).hexdigest(),
            )
        pronunciation_json = _canonical_json(pronunciations)
        pronunciation_hash = hashlib.sha256(pronunciation_json.encode("utf-8")).hexdigest()
        revision_payload = {
            "personas": personas,
            "pronunciations": pronunciations,
            "assets": {name: asset.sha256 for name, asset in assets.items()},
        }
        revision = hashlib.sha256(_canonical_json(revision_payload).encode("utf-8")).hexdigest()
        return cls(
            personas=personas,
            pronunciations=pronunciations,
            pronunciations_sha256=pronunciation_hash,
            assets=assets,
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
        engine_name = _required_spec_text(spec, "engine")
        engine = self._engines.get(engine_name)
        if engine is None:
            raise SnapshotValidationError(f"unregistered voice engine: {engine_name}")
        version = str(engine.version or "").strip()
        if not version:
            raise SnapshotValidationError(f"voice engine {engine_name!r} has no version")

        asset_hashes: dict[str, str] = {}
        for name, asset in self._catalog.assets.items():
            actual = _sha256_file(asset.path)
            if actual != asset.sha256:
                raise SnapshotValidationError(
                    f"SHA-256 mismatch for catalog asset {name}: "
                    f"expected {asset.sha256}, got {actual}"
                )
            asset_hashes[name] = actual
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
        configured_voice = _required_spec_text(spec, "voice")
        custom_style = engine.assets.get(f"voice:{configured_voice}")
        voice_or_style = (
            str(custom_style.path.resolve())
            if custom_style is not None
            else configured_voice
        )
        tempo_end = intent.tempo_end
        if tempo_end is None:
            # No hidden acting preset: a flat contour is the honest default.
            # A producer/director must author every non-neutral ending pace.
            tempo_end = intent.tempo
        if not TEMPO_MIN <= tempo_end <= TEMPO_MAX:
            raise SnapshotValidationError(
                f"resolved tempo_end {tempo_end:g} is outside {TEMPO_MIN}-{TEMPO_MAX}"
            )
        snapshot = RenderSnapshot(
            engine=engine_name,
            engine_version=version,
            voice_or_style=voice_or_style,
            # The persona's canonical pace times the producer's emotional
            # tempo (SpeechIntent.tempo, validated to a narrow band) — the
            # snapshot carries the final speed, so playback needs no replay
            # of the intent.
            speed=_positive_float(spec.get("speed"), "speed") * intent.tempo,
            mastering_profile=_required_spec_text(spec, "mastering"),
            dsp=_required_spec_text(spec, "dsp"),
            pronunciations=self._catalog.pronunciations,
            pronunciations_sha256=self._catalog.pronunciations_sha256,
            gain=gain,
            asset_sha256=asset_hashes,
            config_revision=config_revision,
            seed=_seed(spec.get("seed")),
            emotion=intent.emotion,
            tempo_start=intent.tempo,
            tempo_end=tempo_end,
            tone=intent.tone,
            pause_after=intent.pause_after,
        )
        snapshot.validate_complete()
        return snapshot

def _read_bytes(path: Path, *, strict: bool) -> bytes | None:
    """Read the file once; None means "absent or unreadable, non-strict"."""

    try:
        with path.open("rb") as handle:
            return handle.read()
    except FileNotFoundError:
        if strict:
            raise VoiceResolverError(f"voice catalog file does not exist: {path}") from None
        return None
    except OSError as exc:
        if strict:
            raise VoiceResolverError(f"could not load voice catalog file {path}: {exc}") from exc
        return None


def _parse_toml(payload: bytes | None, path: Path, *, strict: bool) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        data = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
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


def _seed(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= SEED_MAX:
        raise SnapshotValidationError(
            f"voice persona seed must be an integer between 0 and {SEED_MAX}"
        )
    return value


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
