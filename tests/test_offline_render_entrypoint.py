"""The catalog's offline_pipeline route must reach a real pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.voice.pipelines import render_offline
from dan.voice.pipelines.chatterbox_v3 import (
    ChatterboxV3ZanetaPipeline,
    PipelineCapabilityError,
)


class _RecordingPipeline:
    live_capable = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, object]] = []

    def render(self, text: str, output: Path, *, manifest: object) -> object:
        self.calls.append((text, Path(output), manifest))
        return "artifact"


def test_zaneta_routes_through_catalog_to_chatterbox_pipeline(tmp_path: Path) -> None:
    pipeline = _RecordingPipeline()
    sentinel_manifest = object()
    loaded: list[Path] = []

    def manifest_loader(path: str | Path) -> object:
        loaded.append(Path(path))
        return sentinel_manifest

    artifact = render_offline(
        "zaneta",
        "Zażółć gęślą jaźń.",
        tmp_path / "zaneta.wav",
        pipeline_factories={"chatterbox-v3-zaneta": lambda: pipeline},
        manifest_loader=manifest_loader,
    )

    assert artifact == "artifact"
    assert pipeline.calls == [
        ("Zażółć gęślą jaźń.", tmp_path / "zaneta.wav", sentinel_manifest)
    ]
    assert loaded and loaded[0].name == "chatterbox-v3-zaneta.toml"


def test_default_zaneta_factory_is_the_pinned_chatterbox_pipeline() -> None:
    from dan.voice.pipelines import OFFLINE_PIPELINE_FACTORIES

    pipeline = OFFLINE_PIPELINE_FACTORIES["chatterbox-v3-zaneta"]()
    assert isinstance(pipeline, ChatterboxV3ZanetaPipeline)


def test_persona_without_offline_route_fails_visibly(tmp_path: Path) -> None:
    with pytest.raises(PipelineCapabilityError, match="offline"):
        render_offline("dan", "test", tmp_path / "dan.wav")


def test_unknown_persona_fails_visibly(tmp_path: Path) -> None:
    with pytest.raises(PipelineCapabilityError, match="nie-ma-takiej"):
        render_offline("nie-ma-takiej", "test", tmp_path / "x.wav")


def test_unregistered_pipeline_name_fails_instead_of_substituting(
    tmp_path: Path,
) -> None:
    catalog = {"zaneta": {"offline_pipeline": "totally-unknown-pipeline"}}
    with pytest.raises(PipelineCapabilityError, match="totally-unknown-pipeline"):
        render_offline(
            "zaneta",
            "test",
            tmp_path / "x.wav",
            personas=catalog,
            manifest_loader=lambda path: object(),
        )
