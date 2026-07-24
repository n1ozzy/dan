"""Immutable contracts for DAN's offline/storytelling prosody renderer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SceneLine:
    """One authored line with explicit, context-specific direction."""

    index: int
    persona: str
    text: str
    tempo_start: float | None = None
    tempo_end: float | None = None
    emotion: str = "neutral"
    tone: str = "neutral"
    pause_after: float | None = None
    gap_before: float = 0.0
    take_count: int | None = None
    seeds: tuple[int, ...] = ()


@dataclass(frozen=True)
class SegmentPlan:
    """One complete Supertonic request within an authored utterance."""

    id: str
    index: int
    text: str
    split_reason: str
    internal_gap_after: float
    effective_speed: float
    tempo_start: float
    tempo_end: float
    seed_candidates: tuple[int, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SegmentPlan":
        return cls(
            id=str(payload["id"]),
            index=int(payload["index"]),
            text=str(payload["text"]),
            split_reason=str(payload["split_reason"]),
            internal_gap_after=float(payload["internal_gap_after"]),
            effective_speed=float(payload["effective_speed"]),
            tempo_start=float(payload["tempo_start"]),
            tempo_end=float(payload["tempo_end"]),
            seed_candidates=tuple(int(value) for value in payload["seed_candidates"]),
        )


@dataclass(frozen=True)
class UtterancePlan:
    """Deterministic rendering plan for one authored scene line."""

    id: str
    index: int
    persona: str
    voice: str
    mastering_profile: str
    dsp: str
    original_text: str
    spoken_text: str
    base_speed: float
    effective_speed: float
    tempo_start: float
    tempo_end: float
    emotion: str
    tone: str
    pause_after: float
    gap_before: float
    previous_context: str | None
    next_context: str | None
    segments: tuple[SegmentPlan, ...]
    notes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UtterancePlan":
        return cls(
            id=str(payload["id"]),
            index=int(payload["index"]),
            persona=str(payload["persona"]),
            voice=str(payload["voice"]),
            mastering_profile=str(payload["mastering_profile"]),
            dsp=str(payload.get("dsp", "none")),
            original_text=str(payload["original_text"]),
            spoken_text=str(payload["spoken_text"]),
            base_speed=float(payload["base_speed"]),
            effective_speed=float(payload["effective_speed"]),
            tempo_start=float(payload["tempo_start"]),
            tempo_end=float(payload["tempo_end"]),
            emotion=str(payload["emotion"]),
            tone=str(payload["tone"]),
            pause_after=float(payload["pause_after"]),
            gap_before=float(payload["gap_before"]),
            previous_context=(
                str(payload["previous_context"])
                if payload.get("previous_context") is not None
                else None
            ),
            next_context=(
                str(payload["next_context"])
                if payload.get("next_context") is not None
                else None
            ),
            segments=tuple(SegmentPlan.from_dict(item) for item in payload["segments"]),
            notes=tuple(str(item) for item in payload.get("notes", ())),
        )


@dataclass(frozen=True)
class ScenePlan:
    """Full offline scene plan. It is safe to serialize before synthesis."""

    schema_version: int
    source_name: str
    settings: dict[str, Any]
    utterances: tuple[UtterancePlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScenePlan":
        return cls(
            schema_version=int(payload["schema_version"]),
            source_name=str(payload["source_name"]),
            settings=dict(payload.get("settings", {})),
            utterances=tuple(
                UtterancePlan.from_dict(item) for item in payload["utterances"]
            ),
        )


@dataclass(frozen=True)
class TakeMetrics:
    """Auditable technical signals for one deterministic take."""

    duration_seconds: float
    peak: float
    rms: float
    clipping_ratio: float
    voiced_ratio: float
    silence_ratio: float
    leading_silence_seconds: float
    trailing_silence_seconds: float
    max_internal_silence_seconds: float
    tail_energy_ratio: float
    speech_rate_chars_per_second: float
    f0_mean_hz: float
    f0_std_hz: float
    f0_tail_slope: float
    score: float
    hard_failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TakeCandidate:
    """One rendered seed and the evidence used to keep or reject it."""

    seed: int
    raw_path: Path
    raw_sha256: str
    preview_path: Path
    preview_sha256: str
    metrics: TakeMetrics
    selected: bool = False
    selection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class SelectedSegment:
    """Selected and post-processed audio for one segment."""

    segment_id: str
    seed: int
    raw_path: Path
    processed_path: Path
    raw_sha256: str
    processed_sha256: str
    text_sha256: str
    directed_snapshot_json: str
    snapshot_json: str
    gain_db: float
    mastering_profile: str
    selection_reason: str
    candidates: tuple[TakeCandidate, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class RenderResult:
    """Paths and facts produced by a complete offline scene render."""

    output_dir: Path
    plan_path: Path
    manifest_path: Path
    final_wav_path: Path
    final_wav_sha256: str
    utterance_paths: tuple[Path, ...]
    selected_segments: tuple[SelectedSegment, ...]

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


__all__ = [
    "RenderResult",
    "SceneLine",
    "ScenePlan",
    "SegmentPlan",
    "SelectedSegment",
    "TakeCandidate",
    "TakeMetrics",
    "UtterancePlan",
]
