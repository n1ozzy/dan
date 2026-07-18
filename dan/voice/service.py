"""Single producer boundary for voice resolution and queue admission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dan.voice.assets import load_asset_manifest, load_voice_catalog, verify_assets
from dan.voice.models import SpeechIntent, VoiceRequest
from dan.voice.queue import VoiceQueue
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceResolver


class VoiceService:
    def __init__(self, queue: VoiceQueue, resolver: Any) -> None:
        if resolver is None or not callable(getattr(resolver, "resolve", None)):
            raise TypeError("VoiceService requires one resolver dependency")
        self.queue = queue
        self._resolver = resolver

    def submit(self, intent: SpeechIntent) -> VoiceRequest:
        snapshot = self._resolver.resolve(intent)
        snapshot.validate_complete()
        return self.queue.enqueue(intent, snapshot)

    def cancel_session(self, session_id: str, *, reason: str) -> list[str]:
        return self.queue.cancel_session(session_id, reason=reason)

    def cancel_request(self, request_id: str, *, reason: str) -> bool:
        return self.queue.cancel_request(request_id, reason=reason)


def build_voice_resolver(config: Any, *, repo_root: Path | None = None) -> VoiceResolver:
    """Compose the strict resolver from Task 6's versioned repository assets."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    voice_root = root / "config" / "voice"
    catalog = load_voice_catalog(voice_root).voice_catalog
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


__all__ = ["VoiceService", "build_voice_resolver"]
