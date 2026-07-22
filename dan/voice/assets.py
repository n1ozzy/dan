"""Versioned voice catalog and redistributable asset verification."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from dan.voice.resolver import VoiceCatalog


LicenseDecision = Literal["redistributable", "installer-fetch", "local-only"]


class AssetVerificationError(RuntimeError):
    """A declared voice asset cannot be used exactly as versioned."""


@dataclass(frozen=True)
class VoiceAsset:
    name: str
    path: Path
    sha256: str
    source: str
    recipe: Mapping[str, Any]
    model_revision: str
    license_decision: LicenseDecision

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipe", MappingProxyType(dict(self.recipe)))


@dataclass(frozen=True)
class AssetManifest:
    path: Path
    schema_version: int
    model_revision: str
    license_path: Path
    notices_path: Path
    assets: tuple[VoiceAsset, ...]


@dataclass(frozen=True)
class VersionedVoiceCatalog:
    voice_catalog: VoiceCatalog
    gains: Mapping[str, float]
    gain_fallback: str
    duplicate_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "gains", MappingProxyType(dict(sorted(self.gains.items()))))

    @property
    def personas(self) -> Mapping[str, Mapping[str, Any]]:
        return self.voice_catalog.personas

    @property
    def pronunciations(self) -> Mapping[str, str]:
        return self.voice_catalog.pronunciations

    def gain_for(self, voice: str, mastering: str) -> float | None:
        return self.gains.get(f"{voice}|{mastering or 'raw'}")


def sha256_file(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise AssetVerificationError(f"could not hash voice asset {path}: {exc}") from exc


def load_asset_manifest(path: str | Path) -> AssetManifest:
    manifest_path = Path(path).resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetVerificationError(
            f"could not load asset manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise AssetVerificationError("voice asset manifest must be a JSON object")
    root = manifest_path.parent
    model_revision = _required_text(payload, "model_revision")
    assets_raw = payload.get("assets")
    if not isinstance(assets_raw, list):
        raise AssetVerificationError("voice asset manifest assets must be a list")
    assets: list[VoiceAsset] = []
    names: set[str] = set()
    for raw in assets_raw:
        if not isinstance(raw, dict):
            raise AssetVerificationError("voice asset row must be an object")
        name = _required_text(raw, "name")
        if name in names:
            raise AssetVerificationError(f"duplicate voice asset name: {name}")
        names.add(name)
        decision = _required_text(raw, "license_decision")
        if decision not in {"redistributable", "installer-fetch", "local-only"}:
            raise AssetVerificationError(f"invalid license decision for {name}: {decision}")
        recipe = raw.get("recipe")
        if not isinstance(recipe, dict) or not recipe:
            raise AssetVerificationError(f"voice asset {name} has no deterministic recipe")
        assets.append(
            VoiceAsset(
                name=name,
                path=(root / _required_text(raw, "path")).resolve(),
                sha256=_required_sha(raw, "sha256"),
                source=_required_text(raw, "source"),
                recipe=recipe,
                model_revision=_required_text(raw, "model_revision"),
                license_decision=decision,  # type: ignore[arg-type]
            )
        )
    return AssetManifest(
        path=manifest_path,
        schema_version=int(payload.get("schema_version", 0)),
        model_revision=model_revision,
        license_path=(root / _required_text(payload, "license_path")).resolve(),
        notices_path=(root / _required_text(payload, "notices_path")).resolve(),
        assets=tuple(assets),
    )


def verify_assets(manifest: AssetManifest, *, repo_root: Path) -> None:
    root = repo_root.resolve()
    for supporting in (manifest.license_path, manifest.notices_path):
        _require_within_repo(supporting, root)
        if not supporting.is_file():
            raise AssetVerificationError(f"missing voice asset notice: {supporting}")
    for asset in manifest.assets:
        _require_within_repo(asset.path, root)
        if asset.license_decision != "redistributable":
            raise AssetVerificationError(
                f"voice asset {asset.name} is {asset.license_decision}; "
                "no repository file is usable"
            )
        if not asset.path.is_file():
            raise AssetVerificationError(f"missing voice asset {asset.name}: {asset.path}")
        actual = sha256_file(asset.path)
        if actual != asset.sha256:
            raise AssetVerificationError(
                f"SHA-256 mismatch for voice asset {asset.name}: "
                f"expected {asset.sha256}, got {actual}"
            )
        if asset.model_revision != manifest.model_revision:
            raise AssetVerificationError(
                f"model revision mismatch for voice asset {asset.name}: {asset.model_revision}"
            )
    declared_json = {asset.path.resolve() for asset in manifest.assets}
    versioned_json = {
        path.resolve()
        for path in manifest.path.parent.glob("*.json")
        if path.resolve() != manifest.path.resolve()
    }
    extra = sorted(path.name for path in versioned_json - declared_json)
    missing = sorted(path.name for path in declared_json - versioned_json)
    if extra:
        raise AssetVerificationError(
            f"unmanifested voice asset JSON: {', '.join(extra)}"
        )
    if missing:
        raise AssetVerificationError(
            f"manifested voice asset JSON is outside the exact set: {', '.join(missing)}"
        )


def load_voice_catalog(directory: str | Path) -> VersionedVoiceCatalog:
    root = Path(directory).resolve()
    voice_catalog = VoiceCatalog.from_directory(root, strict=True)
    gains_path = root / "gains.json"
    try:
        gains_raw = json.loads(gains_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetVerificationError(f"could not load voice gains {gains_path}: {exc}") from exc
    if not isinstance(gains_raw, dict):
        raise AssetVerificationError("voice gains must be a JSON object")
    gains: dict[str, float] = {}
    for key, value in gains_raw.items():
        try:
            gains[str(key)] = float(value)
        except (TypeError, ValueError) as exc:
            raise AssetVerificationError(f"invalid measured gain for {key!r}") from exc
    for name, spec in voice_catalog.personas.items():
        missing = {"engine", "voice", "speed", "seed", "mastering", "dsp"} - set(spec)
        if missing:
            raise AssetVerificationError(
                f"voice persona {name!r} is missing strict fields: {', '.join(sorted(missing))}"
            )
    return VersionedVoiceCatalog(
        voice_catalog=voice_catalog,
        gains=gains,
        gain_fallback="loudnorm=I=-14:TP=-2.0:LRA=7,aresample=44100",
    )


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssetVerificationError(f"asset manifest field {key!r} is required")
    return value.strip()


def _required_sha(payload: Mapping[str, Any], key: str) -> str:
    value = _required_text(payload, key).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise AssetVerificationError(f"asset manifest field {key!r} must be SHA-256")
    return value


def _require_within_repo(path: Path, repo_root: Path) -> None:
    try:
        path.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise AssetVerificationError(f"voice asset escapes repository root: {path}") from exc


def _repo_root_for(path: Path) -> Path:
    for parent in (path.parent, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise AssetVerificationError(f"could not find repository root for {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dan.voice.assets")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify":
        manifest = load_asset_manifest(args.manifest)
        verify_assets(manifest, repo_root=_repo_root_for(manifest.path))
        print(f"verified {len(manifest.assets)} voice assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AssetManifest",
    "AssetVerificationError",
    "VersionedVoiceCatalog",
    "VoiceAsset",
    "load_asset_manifest",
    "load_voice_catalog",
    "sha256_file",
    "verify_assets",
]
