from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dan.voice.assets import (
    AssetVerificationError,
    load_asset_manifest,
    sha256_file,
    verify_assets,
)


ROOT = Path(__file__).resolve().parents[1]
STYLE_ROOT = ROOT / "config" / "voice" / "custom_styles"
EXPECTED_STYLES = {
    "F1F2",
    "F1M1",
    "F2EXTF",
    "F2EXTR",
    "F2EXTX",
    "F2F1",
    "F2F1L",
    "F2F4",
    "F2M1",
    "F2M3",
    "F2RYTM1",
    "F4F1",
    "F4F2",
    "FTRIO",
    "M1M3",
    "M2M1",
    "M3M2",
    "M3RYTF2",
    "ROBOT",
    "ROBOT75",
}
SUPERTONIC_REVISION = "724fb5abbf5502583fb520898d45929e62f02c0b"


def test_twenty_custom_styles_are_versioned_and_hash_valid() -> None:
    manifest = load_asset_manifest(STYLE_ROOT / "manifest.json")

    assert {asset.name for asset in manifest.assets} == EXPECTED_STYLES
    assert len(manifest.assets) == 20
    assert all(asset.sha256 == sha256_file(asset.path) for asset in manifest.assets)
    assert all(asset.source and asset.recipe for asset in manifest.assets)
    assert all(asset.model_revision == SUPERTONIC_REVISION for asset in manifest.assets)
    assert all(asset.license_decision == "redistributable" for asset in manifest.assets)
    verify_assets(manifest, repo_root=ROOT)


def test_openrail_license_and_notices_are_versioned() -> None:
    manifest = load_asset_manifest(STYLE_ROOT / "manifest.json")
    license_text = manifest.license_path.read_text(encoding="utf-8")
    notices = manifest.notices_path.read_text(encoding="utf-8")

    assert "Open RAIL-M License" in license_text
    assert "Copyright (c) 2026 Supertone Inc." in notices
    assert SUPERTONIC_REVISION in notices


def test_verifier_never_falls_back_to_home_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    cached = fake_home / ".cache" / "supertonic3" / "custom_styles" / "ONLY.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(cached.read_bytes()).hexdigest()
    manifest_path = tmp_path / "repo" / "config" / "voice" / "custom_styles" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_revision": SUPERTONIC_REVISION,
                "license_path": "LICENSE-OpenRAIL-M.txt",
                "notices_path": "NOTICE.txt",
                "assets": [
                    {
                        "name": "ONLY",
                        "path": "ONLY.json",
                        "sha256": digest,
                        "source": "test",
                        "recipe": {"kind": "weighted", "parts": {"M1": 1.0}},
                        "model_revision": SUPERTONIC_REVISION,
                        "license_decision": "redistributable",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(fake_home))

    manifest = load_asset_manifest(manifest_path)

    with pytest.raises(AssetVerificationError, match="missing voice asset"):
        verify_assets(manifest, repo_root=tmp_path / "repo")


def test_repository_versions_no_reference_or_generated_wav() -> None:
    assert not list((ROOT / "config" / "voice").rglob("*.wav"))
