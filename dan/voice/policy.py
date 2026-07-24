"""Non-negotiable public voice-cast boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


OWNER_VOICE_PERSONAS = frozenset({"dan", "danusia"})


class VoiceCastPolicyError(ValueError):
    """A catalog or request crosses the two-person owner cast boundary."""


def validate_owner_cast(personas: Mapping[str, Any]) -> None:
    actual = frozenset(str(name) for name in personas)
    if actual == OWNER_VOICE_PERSONAS:
        return
    missing = sorted(OWNER_VOICE_PERSONAS - actual)
    unexpected = sorted(actual - OWNER_VOICE_PERSONAS)
    details = []
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if unexpected:
        details.append(f"unexpected: {', '.join(unexpected)}")
    raise VoiceCastPolicyError(
        "voice catalog must contain exactly dan and danusia"
        + (f" ({'; '.join(details)})" if details else "")
    )


def require_owner_persona(persona: str) -> str:
    normalized = str(persona).strip()
    if normalized not in OWNER_VOICE_PERSONAS:
        raise VoiceCastPolicyError(
            f"voice persona must be dan or danusia, got {normalized!r}"
        )
    return normalized


__all__ = [
    "OWNER_VOICE_PERSONAS",
    "VoiceCastPolicyError",
    "require_owner_persona",
    "validate_owner_cast",
]
