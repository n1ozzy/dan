"""Voice activity detection placeholder."""

from __future__ import annotations


class VoiceActivityDetector:
    def is_speech(self, audio: bytes) -> bool:
        raise NotImplementedError("voice activity detection is not implemented yet")
