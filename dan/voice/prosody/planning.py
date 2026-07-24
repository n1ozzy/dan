"""Deterministic execution planning for explicitly directed speech.

The planner owns technical work only:

* validate the authored direction;
* preserve neighboring dialogue as auditable context;
* split only when the active Supertonic transport limit requires it;
* distribute one authored tempo contour across technical segments;
* select deterministic seed candidates.

It never invents acting from punctuation, line length, persona or word lists.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dan.voice.models import EMOTIONS, RESOLVED_TONES, TEMPO_MAX, TEMPO_MIN

from .models import SceneLine, ScenePlan, SegmentPlan, UtterancePlan


class ProsodyPlanError(ValueError):
    """The current catalog and scene cannot produce an honest render plan."""


DEFAULT_SEED_POOL: tuple[int, ...] = (1, 17, 42, 91, 137, 233, 377, 610, 987, 1597)


@dataclass(frozen=True)
class DirectorSettings:
    """Technical renderer limits, deliberately free of artistic presets."""

    hard_max_chars: int = 400
    default_take_count: int = 6
    seed_pool: tuple[int, ...] = DEFAULT_SEED_POOL

    def __post_init__(self) -> None:
        if type(self.hard_max_chars) is not int or self.hard_max_chars <= 0:
            raise ProsodyPlanError("hard_max_chars must be a positive integer")
        if not 1 <= self.default_take_count <= len(self.seed_pool):
            raise ProsodyPlanError("default_take_count is outside the seed pool")
        if len(set(self.seed_pool)) != len(self.seed_pool):
            raise ProsodyPlanError("seed_pool contains duplicates")
        if not all(
            type(seed) is int and 0 <= seed <= (2**32) - 1
            for seed in self.seed_pool
        ):
            raise ProsodyPlanError("seed_pool must contain uint32 integers")


@dataclass(frozen=True)
class _Boundary:
    position: int
    reason: str
    priority: int


_BOUNDARY_PATTERNS: tuple[tuple[re.Pattern[str], str, int], ...] = (
    (re.compile(r"\n+"), "paragraph", 0),
    (re.compile(r"(?<=[.!?…])\s+"), "sentence", 1),
    (re.compile(r"(?<=[;:])\s+"), "clause", 2),
    (re.compile(r"\s+[—–-]\s+"), "dash", 3),
    (re.compile(r"(?<=,)\s+"), "comma", 4),
    (re.compile(r"\s+"), "word", 5),
)


class ProsodyDirector:
    """Compile authored scene direction into an immutable synthesis plan."""

    def __init__(
        self,
        personas: Mapping[str, Mapping[str, Any]],
        *,
        settings: DirectorSettings | None = None,
    ) -> None:
        self._personas = personas
        self.settings = settings or DirectorSettings()

    def plan(
        self,
        lines: Sequence[SceneLine],
        *,
        source_name: str,
    ) -> ScenePlan:
        if not lines:
            raise ProsodyPlanError("scene has no lines")

        utterances: list[UtterancePlan] = []
        for position, line in enumerate(lines):
            previous_line = lines[position - 1] if position > 0 else None
            next_line = lines[position + 1] if position + 1 < len(lines) else None
            utterances.append(
                self._plan_line(
                    line,
                    previous_line=previous_line,
                    next_line=next_line,
                )
            )

        settings_payload = {
            "hard_max_chars": self.settings.hard_max_chars,
            "default_take_count": self.settings.default_take_count,
            "seed_pool": list(self.settings.seed_pool),
            "direction_policy": "explicit_controls_no_text_inference",
            "technical_split_policy": "semantic_boundary_at_engine_limit",
            "internal_gap_policy": "none_added",
            "context_window": "previous_and_next_line_recorded_not_spoken",
            "quality_path": "offline_storytelling",
        }
        return ScenePlan(
            schema_version=2,
            source_name=source_name,
            settings=settings_payload,
            utterances=tuple(utterances),
        )

    def _plan_line(
        self,
        line: SceneLine,
        *,
        previous_line: SceneLine | None,
        next_line: SceneLine | None,
    ) -> UtterancePlan:
        spec = self._personas.get(line.persona)
        if spec is None:
            raise ProsodyPlanError(f"unknown voice persona: {line.persona!r}")

        voice = _required_text(spec, "voice", persona=line.persona)
        profile = _required_text(spec, "mastering", persona=line.persona).lower()
        dsp = _required_text(spec, "dsp", persona=line.persona)
        base_speed = _positive_number(spec.get("speed"), "speed", persona=line.persona)
        spoken = _normalize_spoken_text(line.text)

        tempo_start = float(line.tempo_start if line.tempo_start is not None else 1.0)
        tempo_end = float(line.tempo_end if line.tempo_end is not None else tempo_start)
        for name, value in (("tempo_start", tempo_start), ("tempo_end", tempo_end)):
            if not math.isfinite(value) or not TEMPO_MIN <= value <= TEMPO_MAX:
                raise ProsodyPlanError(
                    f"{name} for {line.persona!r} must be between "
                    f"{TEMPO_MIN} and {TEMPO_MAX}"
                )
        if line.emotion not in EMOTIONS:
            raise ProsodyPlanError(f"unsupported emotion: {line.emotion!r}")
        if line.tone not in RESOLVED_TONES:
            raise ProsodyPlanError(f"unsupported explicit tone: {line.tone!r}")

        effective_speed = base_speed * tempo_start
        if not 0.45 <= effective_speed <= 2.0 or not math.isfinite(effective_speed):
            raise ProsodyPlanError(
                f"resolved speed for {line.persona!r} is unsafe: {effective_speed!r}"
            )

        pause_after = float(line.pause_after if line.pause_after is not None else 0.0)
        chunks = self._split_for_engine(spoken)
        contour = _distribute_tempo_contour(
            chunks,
            tempo_start=tempo_start,
            tempo_end=tempo_end,
        )
        utterance_id = _stable_id("u", line.index, line.persona, spoken)
        seeds = self._seed_candidates(line)
        segments = tuple(
            SegmentPlan(
                id=f"{utterance_id}-s{segment_index + 1:02d}",
                index=segment_index,
                text=segment_text,
                split_reason=split_reason,
                # The renderer must not invent a breath duration. Conservative
                # trim already retains the source take's own edge room.
                internal_gap_after=0.0,
                effective_speed=base_speed * segment_tempo_start,
                tempo_start=segment_tempo_start,
                tempo_end=segment_tempo_end,
                seed_candidates=seeds,
            )
            for segment_index, (
                (segment_text, split_reason),
                (segment_tempo_start, segment_tempo_end),
            ) in enumerate(zip(chunks, contour, strict=True))
        )

        notes: list[str] = []
        if len(segments) > 1:
            notes.append(
                f"technical split into {len(segments)} semantic segments at active engine limit"
            )
        if line.tempo_start is None:
            notes.append("tempo_start defaulted to neutral 1.0; no acting inferred")
        if line.pause_after is None:
            notes.append("pause_after defaulted to 0.0; no punctuation pause inferred")

        return UtterancePlan(
            id=utterance_id,
            index=line.index,
            persona=line.persona,
            voice=voice,
            mastering_profile=profile,
            dsp=dsp,
            original_text=line.text,
            spoken_text=spoken,
            base_speed=base_speed,
            effective_speed=effective_speed,
            tempo_start=tempo_start,
            tempo_end=tempo_end,
            emotion=line.emotion,
            tone=line.tone,
            pause_after=pause_after,
            gap_before=float(line.gap_before),
            previous_context=_context_row(previous_line),
            next_context=_context_row(next_line),
            segments=segments,
            notes=tuple(notes),
        )

    def _seed_candidates(self, line: SceneLine) -> tuple[int, ...]:
        pool = line.seeds or self.settings.seed_pool
        take_count = line.take_count or self.settings.default_take_count
        if line.seeds and line.take_count is None:
            take_count = len(line.seeds)
        if take_count > len(pool):
            if line.seeds:
                raise ProsodyPlanError(
                    f"line {line.index + 1}: takes={take_count} but only "
                    f"{len(pool)} explicit seeds"
                )
            take_count = len(pool)
        return tuple(pool[:take_count])

    def _split_for_engine(self, text: str) -> list[tuple[str, str]]:
        if len(text) <= self.settings.hard_max_chars:
            return [(text, "whole_thought")]

        chunks: list[tuple[str, str]] = []
        remaining = text
        while len(remaining) > self.settings.hard_max_chars:
            boundary = self._choose_boundary(remaining)
            head = remaining[: boundary.position].strip()
            tail = remaining[boundary.position :].strip()
            if not head or not tail:
                raise ProsodyPlanError("offline segmenter produced an empty chunk")
            chunks.append((head, boundary.reason))
            remaining = tail
        chunks.append((remaining, "tail"))
        return chunks

    def _choose_boundary(self, text: str) -> _Boundary:
        hard_limit = min(self.settings.hard_max_chars, len(text) - 1)
        window = text[: hard_limit + 1]
        candidates: list[_Boundary] = []
        for pattern, reason, priority in _BOUNDARY_PATTERNS:
            for match in pattern.finditer(window):
                position = match.end()
                if 0 < position <= hard_limit:
                    candidates.append(
                        _Boundary(
                            position=position,
                            reason=reason,
                            priority=priority,
                        )
                    )
        if not candidates:
            raise ProsodyPlanError(
                "no semantic boundary exists before the active Supertonic "
                f"limit ({self.settings.hard_max_chars} characters); rewrite "
                "the authored text instead of cutting a word"
            )

        # Semantic quality wins. Within the same boundary class, use the
        # furthest safe boundary so no unrelated target-length preset exists.
        return min(candidates, key=lambda item: (item.priority, -item.position))


def _distribute_tempo_contour(
    chunks: Sequence[tuple[str, str]],
    *,
    tempo_start: float,
    tempo_end: float,
) -> tuple[tuple[float, float], ...]:
    weights = [max(1, len(text)) for text, _ in chunks]
    total = float(sum(weights))
    cursor = 0.0
    contour: list[tuple[float, float]] = []
    for weight in weights:
        start_fraction = cursor / total
        cursor += weight
        end_fraction = cursor / total
        start = tempo_start + ((tempo_end - tempo_start) * start_fraction)
        end = tempo_start + ((tempo_end - tempo_start) * end_fraction)
        contour.append((start, end))
    return tuple(contour)


def _context_row(line: SceneLine | None) -> str | None:
    if line is None:
        return None
    return f"{line.persona}|{_normalize_spoken_text(line.text)}"


def _normalize_spoken_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", str(text or ""))
    normalized = re.sub(r"[\t\r\f\v]+", " ", normalized)
    normalized = re.sub(r"[ ]{2,}", " ", normalized)
    normalized = re.sub(r" *\n+ *", "\n", normalized)
    normalized = normalized.strip()
    if not normalized:
        raise ProsodyPlanError("spoken text is empty")
    return normalized


def _required_text(spec: Mapping[str, Any], key: str, *, persona: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProsodyPlanError(f"persona {persona!r} has no {key}")
    return value.strip()


def _positive_number(value: Any, key: str, *, persona: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProsodyPlanError(f"persona {persona!r} has invalid {key}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ProsodyPlanError(f"persona {persona!r} has invalid {key}")
    return number


def _stable_id(prefix: str, index: int, persona: str, text: str) -> str:
    digest = hashlib.sha256(f"{persona}\0{text}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{index + 1:03d}-{persona}-{digest}"


__all__ = [
    "DEFAULT_SEED_POOL",
    "DirectorSettings",
    "ProsodyDirector",
    "ProsodyPlanError",
]
