"""Parse small, auditable offline scene scripts.

The format intentionally stays boring::

    dan|Dobra... zaczynamy.
    danusia;tempo=0.96;tempo_end=0.90;emotion=contempt;tone=dark;pause=0.26|Naprawdę?
    dan;takes=8;seeds=1,17,42,91|Sprawdzam.

Everything before the first ``|`` is control data. Everything after it is the
spoken text. Unknown options are rejected instead of being silently ignored.
No option is inferred from punctuation or line length.
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path

from dan.voice.models import EMOTIONS, RESOLVED_TONES

from .models import SceneLine


class SceneParseError(ValueError):
    """The authored scene cannot be converted into deterministic input."""


_ALLOWED_OPTIONS = frozenset(
    {
        "tempo",
        "tempo_start",
        "tempo_end",
        "emotion",
        "tone",
        "pause",
        "pause_after",
        "gap",
        "gap_before",
        "takes",
        "seeds",
    }
)


def parse_scene_file(path: str | Path) -> tuple[SceneLine, ...]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise SceneParseError(f"could not read scene {source}: {exc}") from exc
    return parse_scene_text(text, source_name=str(source))


def parse_scene_text(text: str, *, source_name: str = "<memory>") -> tuple[SceneLine, ...]:
    if not isinstance(text, str):
        raise SceneParseError("scene must be UTF-8 text")

    lines: list[SceneLine] = []
    for physical_line, raw_line in enumerate(text.splitlines(), start=1):
        normalized = unicodedata.normalize("NFC", raw_line).strip()
        if not normalized or normalized.startswith("#"):
            continue
        if "|" not in normalized:
            raise SceneParseError(
                f"{source_name}:{physical_line}: expected 'persona|spoken text'"
            )
        control, spoken = normalized.split("|", 1)
        spoken = spoken.strip()
        if not spoken:
            raise SceneParseError(f"{source_name}:{physical_line}: spoken text is empty")

        parts = [part.strip() for part in control.split(";")]
        persona = parts[0]
        if not persona:
            raise SceneParseError(f"{source_name}:{physical_line}: persona is empty")

        options: dict[str, str] = {}
        for part in parts[1:]:
            if not part:
                continue
            if "=" not in part:
                raise SceneParseError(
                    f"{source_name}:{physical_line}: option {part!r} must be key=value"
                )
            key, value = (token.strip() for token in part.split("=", 1))
            key = key.lower()
            if key not in _ALLOWED_OPTIONS:
                allowed = ", ".join(sorted(_ALLOWED_OPTIONS))
                raise SceneParseError(
                    f"{source_name}:{physical_line}: unknown option {key!r}; "
                    f"allowed: {allowed}"
                )
            if key in options:
                raise SceneParseError(
                    f"{source_name}:{physical_line}: duplicate option {key!r}"
                )
            if not value:
                raise SceneParseError(
                    f"{source_name}:{physical_line}: option {key!r} has no value"
                )
            options[key] = value

        if "tempo" in options and "tempo_start" in options:
            raise SceneParseError(
                f"{source_name}:{physical_line}: tempo and tempo_start are aliases; use one"
            )
        if "pause" in options and "pause_after" in options:
            raise SceneParseError(
                f"{source_name}:{physical_line}: pause and pause_after are aliases; use one"
            )
        if "gap" in options and "gap_before" in options:
            raise SceneParseError(
                f"{source_name}:{physical_line}: gap and gap_before are aliases; use one"
            )

        tempo_start = _optional_float(
            options.get("tempo", options.get("tempo_start")),
            name="tempo",
            source_name=source_name,
            physical_line=physical_line,
            minimum=0.6,
            maximum=1.4,
        )
        tempo_end = _optional_float(
            options.get("tempo_end"),
            name="tempo_end",
            source_name=source_name,
            physical_line=physical_line,
            minimum=0.6,
            maximum=1.4,
        )
        emotion = _enum_value(
            options.get("emotion", "neutral"),
            name="emotion",
            allowed=EMOTIONS,
            source_name=source_name,
            physical_line=physical_line,
        )
        tone = _enum_value(
            options.get("tone", "neutral"),
            name="tone",
            allowed=RESOLVED_TONES,
            source_name=source_name,
            physical_line=physical_line,
        )
        pause_after = _optional_float(
            options.get("pause", options.get("pause_after")),
            name="pause",
            source_name=source_name,
            physical_line=physical_line,
            minimum=0.0,
            maximum=2.0,
        )
        gap_before = _optional_float(
            options.get("gap", options.get("gap_before")),
            name="gap",
            source_name=source_name,
            physical_line=physical_line,
            minimum=0.0,
            maximum=5.0,
        )
        if gap_before is None:
            gap_before = 0.0

        take_count = _optional_int(
            options.get("takes"),
            name="takes",
            source_name=source_name,
            physical_line=physical_line,
            minimum=1,
            maximum=32,
        )
        seeds = _parse_seeds(
            options.get("seeds"),
            source_name=source_name,
            physical_line=physical_line,
        )

        lines.append(
            SceneLine(
                index=len(lines),
                persona=persona,
                text=spoken,
                tempo_start=tempo_start,
                tempo_end=tempo_end,
                emotion=emotion,
                tone=tone,
                pause_after=pause_after,
                gap_before=gap_before,
                take_count=take_count,
                seeds=seeds,
            )
        )

    if not lines:
        raise SceneParseError(f"{source_name}: scene contains no speakable lines")
    return tuple(lines)


def _optional_float(
    raw: str | None,
    *,
    name: str,
    source_name: str,
    physical_line: int,
    minimum: float,
    maximum: float,
) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise SceneParseError(
            f"{source_name}:{physical_line}: {name} must be a number"
        ) from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise SceneParseError(
            f"{source_name}:{physical_line}: {name} must be between "
            f"{minimum:g} and {maximum:g}"
        )
    return value


def _enum_value(
    raw: str,
    *,
    name: str,
    allowed: frozenset[str],
    source_name: str,
    physical_line: int,
) -> str:
    value = str(raw).strip().lower()
    if value not in allowed:
        raise SceneParseError(
            f"{source_name}:{physical_line}: {name} must be one of: "
            + ", ".join(sorted(allowed))
        )
    return value


def _optional_int(
    raw: str | None,
    *,
    name: str,
    source_name: str,
    physical_line: int,
    minimum: int,
    maximum: int,
) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise SceneParseError(
            f"{source_name}:{physical_line}: {name} must be an integer"
        ) from exc
    if not minimum <= value <= maximum:
        raise SceneParseError(
            f"{source_name}:{physical_line}: {name} must be between {minimum} and {maximum}"
        )
    return value


def _parse_seeds(
    raw: str | None,
    *,
    source_name: str,
    physical_line: int,
) -> tuple[int, ...]:
    if raw is None:
        return ()
    seeds: list[int] = []
    seen: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            seed = int(token, 10)
        except ValueError as exc:
            raise SceneParseError(
                f"{source_name}:{physical_line}: seed {token!r} is not an integer"
            ) from exc
        if not 0 <= seed <= (2**32) - 1:
            raise SceneParseError(
                f"{source_name}:{physical_line}: seed {seed} is outside uint32"
            )
        if seed not in seen:
            seen.add(seed)
            seeds.append(seed)
    if not seeds:
        raise SceneParseError(f"{source_name}:{physical_line}: seeds list is empty")
    return tuple(seeds)


__all__ = ["SceneParseError", "parse_scene_file", "parse_scene_text"]
