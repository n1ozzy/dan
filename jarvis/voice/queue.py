"""Persisted voice queue placeholder."""

from __future__ import annotations

from jarvis.voice.models import VoiceRequest


class VoiceQueue:
    def enqueue(self, request: VoiceRequest) -> VoiceRequest:
        raise NotImplementedError("voice queue persistence is not implemented yet")

    def next(self) -> VoiceRequest | None:
        raise NotImplementedError("voice queue reads are not implemented yet")
