"""Offline, explicitly provisioned voice pipelines."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dan.voice.pipelines.chatterbox_v3 import (
    ChatterboxV3ZanetaPipeline,
    PipelineCapabilityError,
    load_pipeline_manifest,
)

# The only sanctioned offline routes; an offline_pipeline value outside this
# registry is a hard capability error, never a substitution (Task 6 contract).
OFFLINE_PIPELINE_FACTORIES: Mapping[str, Callable[[], Any]] = {
    "chatterbox-v3-zaneta": ChatterboxV3ZanetaPipeline,
}


def render_offline(
    persona: str,
    text: str,
    output: Path,
    *,
    repo_root: Path | None = None,
    personas: Mapping[str, Mapping[str, Any]] | None = None,
    pipeline_factories: Mapping[str, Callable[[], Any]] | None = None,
    manifest_loader: Callable[[Path], Any] | None = None,
) -> Any:
    """Render one persona's text through its catalog-declared offline pipeline."""

    root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
    if personas is None:
        from dan.voice.assets import load_voice_catalog

        personas = load_voice_catalog(root / "config" / "voice").personas
    spec = personas.get(persona)
    if spec is None:
        raise PipelineCapabilityError(
            f"persona {persona!r} is not in the voice catalog"
        )
    pipeline_name = str(spec.get("offline_pipeline") or "")
    if not pipeline_name:
        raise PipelineCapabilityError(
            f"persona {persona!r} has no offline pipeline route in the catalog"
        )
    factories = (
        OFFLINE_PIPELINE_FACTORIES if pipeline_factories is None else pipeline_factories
    )
    factory = factories.get(pipeline_name)
    if factory is None:
        raise PipelineCapabilityError(
            f"offline pipeline {pipeline_name!r} is not a registered pipeline; "
            "refusing to substitute another route"
        )
    loader = manifest_loader or load_pipeline_manifest
    manifest = loader(root / "config" / "voice" / "pipelines" / f"{pipeline_name}.toml")
    return factory().render(text, Path(output), manifest=manifest)


__all__ = [
    "ChatterboxV3ZanetaPipeline",
    "OFFLINE_PIPELINE_FACTORIES",
    "render_offline",
]
