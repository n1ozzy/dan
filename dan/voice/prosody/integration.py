"""Narrow integration with DAN's versioned voice catalog.

Kept separate from ``dan.voice.service`` so an offline render does not import
the daemon queue/event graph merely to resolve an immutable voice snapshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dan.voice.assets import load_asset_manifest, load_voice_catalog, verify_assets
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceResolver


def build_offline_voice_resolver(
    config: Any,
    *,
    repo_root: str | Path,
    voice_root: str | Path,
) -> VoiceResolver:
    root = Path(repo_root).expanduser().resolve()
    catalog_root = Path(voice_root).expanduser().resolve()
    catalog = load_voice_catalog(catalog_root).voice_catalog

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


__all__ = ["build_offline_voice_resolver"]
