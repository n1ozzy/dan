"""Voice contract models."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal


class IntentValidationError(ValueError):
    """Raised when a producer sends fields it does not own."""


class SnapshotValidationError(ValueError):
    """Raised when render configuration is incomplete or unverifiable."""


_INTENT_FIELDS = frozenset(
    {
        "text",
        "persona",
        "source",
        "session",
        "participant",
        "priority",
        "lane",
        "interrupt_policy",
        "utterance_index",
    }
)
_RESOLVER_FIELDS = frozenset(
    {
        "engine",
        "engine_version",
        "voice",
        "voice_or_style",
        "speed",
        "mastering",
        "mastering_profile",
        "dsp",
        "pronunciations",
        "gain",
        "asset_sha256",
        "config_revision",
    }
)


@dataclass(frozen=True)
class SpeechIntent:
    text: str
    persona: str
    source: str
    session: str
    participant: str
    priority: int
    lane: Literal["live", "normal", "background"]
    interrupt_policy: Literal["interruptible", "finish_current"]
    utterance_index: int

    def __post_init__(self) -> None:
        normalized = unicodedata.normalize("NFC", _required_text(self.text, "text"))
        normalized.encode("utf-8", errors="strict")
        object.__setattr__(self, "text", normalized)
        for name in ("persona", "source", "session", "participant"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if type(self.priority) is not int:
            raise IntentValidationError("priority must be an integer")
        if type(self.utterance_index) is not int or self.utterance_index < 0:
            raise IntentValidationError("utterance_index must be a non-negative integer")
        if self.lane not in {"live", "normal", "background"}:
            raise IntentValidationError(f"unsupported lane: {self.lane!r}")
        if self.interrupt_policy not in {"interruptible", "finish_current"}:
            raise IntentValidationError(
                f"unsupported interrupt_policy: {self.interrupt_policy!r}"
            )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        source: str,
        session: str,
    ) -> SpeechIntent:
        if not isinstance(payload, Mapping):
            raise IntentValidationError("speech intent must be a mapping")
        unknown = sorted(set(payload) - _INTENT_FIELDS)
        if unknown:
            field = unknown[0]
            owner = "resolver-owned" if field in _RESOLVER_FIELDS else "unknown"
            raise IntentValidationError(f"{field} is {owner} and cannot be supplied by intent")
        values = dict(payload)
        values.setdefault("source", source)
        values.setdefault("session", session)
        values.setdefault("participant", values.get("persona", "dan"))
        values.setdefault("priority", 0)
        values.setdefault("lane", "normal")
        values.setdefault("interrupt_policy", "finish_current")
        values.setdefault("utterance_index", 0)
        try:
            return cls(**values)
        except TypeError as exc:
            raise IntentValidationError(f"invalid speech intent: {exc}") from exc


@dataclass(frozen=True)
class RenderSnapshot:
    engine: str
    engine_version: str
    voice_or_style: str
    speed: float
    mastering_profile: str
    dsp: str
    pronunciations: Mapping[str, str]
    pronunciations_sha256: str
    gain: float
    asset_sha256: Mapping[str, str]
    config_revision: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pronunciations",
            MappingProxyType(dict(sorted(self.pronunciations.items()))),
        )
        object.__setattr__(
            self,
            "asset_sha256",
            MappingProxyType(dict(sorted(self.asset_sha256.items()))),
        )

    def validate_complete(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.engine, self.engine_version, self.voice_or_style)
        ):
            raise SnapshotValidationError("engine/version/voice is incomplete")
        if (
            not isinstance(self.speed, (int, float))
            or not math.isfinite(self.speed)
            or self.speed <= 0
            or not isinstance(self.gain, (int, float))
            or not math.isfinite(self.gain)
            or self.gain <= 0
            or not self.asset_sha256
        ):
            raise SnapshotValidationError("speed/gain/assets are incomplete")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.pronunciations_sha256, self.config_revision)
        ):
            raise SnapshotValidationError("pronunciations/config revision is incomplete")
        if not isinstance(self.dsp, str) or not isinstance(self.mastering_profile, str):
            raise SnapshotValidationError("mastering/DSP is incomplete")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.pronunciations.items()
        ):
            raise SnapshotValidationError("pronunciations are incomplete")
        if not all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in self.asset_sha256.items()
        ):
            raise SnapshotValidationError("asset hashes are incomplete")

    def canonical_json(self) -> str:
        payload = {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "voice_or_style": self.voice_or_style,
            "speed": self.speed,
            "mastering_profile": self.mastering_profile,
            "dsp": self.dsp,
            "pronunciations": dict(self.pronunciations),
            "pronunciations_sha256": self.pronunciations_sha256,
            "gain": self.gain,
            "asset_sha256": dict(self.asset_sha256),
            "config_revision": self.config_revision,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload_json: str) -> RenderSnapshot:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SnapshotValidationError("render snapshot is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise SnapshotValidationError("render snapshot must be an object")
        try:
            snapshot = cls(
                engine=payload["engine"],
                engine_version=payload["engine_version"],
                voice_or_style=payload["voice_or_style"],
                speed=payload["speed"],
                mastering_profile=payload["mastering_profile"],
                dsp=payload["dsp"],
                pronunciations=payload["pronunciations"],
                pronunciations_sha256=payload["pronunciations_sha256"],
                gain=payload["gain"],
                asset_sha256=payload["asset_sha256"],
                config_revision=payload["config_revision"],
            )
        except (KeyError, TypeError) as exc:
            raise SnapshotValidationError(f"render snapshot is incomplete: {exc}") from exc
        snapshot.validate_complete()
        return snapshot


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntentValidationError(f"{name} must be a non-empty string")
    return value.strip()


class VoiceRequestStatus(StrEnum):
    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ListeningMode(StrEnum):
    HOLD = "hold"
    LOCKED = "locked"


@dataclass(frozen=True)
class VoiceRequest:
    id: str
    text: str
    priority: int
    status: str = VoiceRequestStatus.QUEUED.value
    interrupt_policy: str = "no_interrupt"
    turn_id: str | None = None
    correlation_id: str | None = None
    engine: str | None = None
    voice: str | None = None
    created_at: str | None = None
    source: str | None = None
    session_id: str | None = None
    participant: str | None = None
    persona: str | None = None
    lane: str = "normal"
    utterance_index: int = 0
    render_snapshot: RenderSnapshot | None = None
    synthesis_started_at: str | None = None
    synthesis_completed_at: str | None = None
    playback_started_at: str | None = None
    playback_completed_at: str | None = None
    playback_confirmed: bool = False

    @property
    def intent(self) -> SpeechIntent:
        if not all(
            isinstance(value, str) and value
            for value in (self.persona, self.source, self.session_id, self.participant)
        ):
            raise IntentValidationError("legacy voice request has no complete speech intent")
        return SpeechIntent(
            text=self.text,
            persona=str(self.persona),
            source=str(self.source),
            session=str(self.session_id),
            participant=str(self.participant),
            priority=self.priority,
            lane=self.lane,
            interrupt_policy=self.interrupt_policy,
            utterance_index=self.utterance_index,
        )


@dataclass(frozen=True)
class ListeningLease:
    id: str
    mode: str
    source: str
    status: str = "active"
    created_at: str | None = None
    expires_at: str | None = None
    released_at: str | None = None
