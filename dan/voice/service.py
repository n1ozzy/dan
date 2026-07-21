"""Single producer boundary for voice resolution and queue admission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dan.voice.assets import load_asset_manifest, load_voice_catalog, verify_assets
from dan.voice.models import SpeechIntent, VoiceRequest
from dan.voice.queue import VoiceQueue
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceResolver


class VoiceService:
    def __init__(self, queue: VoiceQueue, resolver: Any, *, intake_gate: Any = None) -> None:
        self.queue = queue
        self._resolver = self._validated_resolver(resolver)
        self._intake_gate = intake_gate

    @staticmethod
    def _validated_resolver(resolver: Any) -> Any:
        if resolver is None or not callable(getattr(resolver, "resolve", None)):
            raise TypeError("VoiceService requires one resolver dependency")
        return resolver

    def replace_resolver(self, resolver: Any) -> None:
        """Hot-swap the resolver (catalog reload); a single attribute store,
        so in-flight submits keep the snapshot they already resolved."""

        self._resolver = self._validated_resolver(resolver)

    def submit(self, intent: SpeechIntent) -> VoiceRequest:
        return self._submit(intent)

    def submit_external(self, intent: SpeechIntent) -> VoiceRequest:
        if self._intake_gate is None:
            return self._submit(intent)
        with self._intake_gate.admit("voice_speak"):
            return self._submit(intent)

    def _submit(self, intent: SpeechIntent) -> VoiceRequest:
        snapshot = self._resolver.resolve(intent)
        snapshot.validate_complete()
        return self.queue.enqueue(intent, snapshot)

    def cancel_session(self, session_id: str, *, reason: str) -> list[str]:
        return self.queue.cancel_session(session_id, reason=reason)

    def cancel_request(self, request_id: str, *, reason: str) -> bool:
        return self.queue.cancel_request(request_id, reason=reason)


def default_voice_catalog_dir() -> Path:
    """The repo's voice catalog — the directory build_voice_resolver reads
    when no override is given, and the one the persona editor writes to."""

    return Path(__file__).resolve().parents[2] / "config" / "voice"


def custom_style_manifest_path(config: Any, *, repo_root: Path | None = None) -> Path:
    """Resolve the custom-style manifest exactly like build_voice_resolver does."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    manifest_setting = str(
        getattr(
            config.voice,
            "supertonic_custom_styles_manifest",
            "config/voice/custom_styles/manifest.json",
        )
        or "config/voice/custom_styles/manifest.json"
    )
    manifest_path = Path(manifest_setting).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    return manifest_path


def installed_custom_style_names(
    config: Any, *, repo_root: Path | None = None
) -> tuple[str, ...]:
    """Names of every verified custom style the resolver can actually render.

    This is the set the panel may offer: deriving it from personas.toml instead
    hid installed blends nobody routed yet and let a typo become a valid choice.
    """

    manifest = load_asset_manifest(
        custom_style_manifest_path(config, repo_root=repo_root)
    )
    return tuple(sorted({asset.name for asset in manifest.assets}))


def build_voice_resolver(
    config: Any,
    *,
    repo_root: Path | None = None,
    voice_root: Path | None = None,
) -> VoiceResolver:
    """Compose the strict resolver from Task 6's versioned repository assets."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    voice_root = Path(voice_root) if voice_root is not None else root / "config" / "voice"
    catalog = load_voice_catalog(voice_root).voice_catalog
    manifest_path = custom_style_manifest_path(config, repo_root=root)
    manifest = load_asset_manifest(manifest_path)
    verify_assets(manifest, repo_root=root)
    engine_assets = {
        f"voice:{asset.name}": AssetMetadata(path=asset.path, sha256=asset.sha256)
        for asset in manifest.assets
    }
    engine_assets["custom-style-manifest"] = AssetMetadata.from_path(manifest.path)
    installation_config = {
        "voice": {"output_gain": float(getattr(config.voice, "output_gain", 1.0))}
    }
    return VoiceResolver(
        catalog,
        installation_config,
        {
            "supertonic": EngineMetadata(
                version=f"1.3.1+{manifest.model_revision}",
                assets=engine_assets,
            )
        },
    )


__all__ = [
    "VoiceService",
    "build_voice_resolver",
    "custom_style_manifest_path",
    "default_voice_catalog_dir",
    "installed_custom_style_names",
]
