"""Anti-echo policy placeholder."""

from __future__ import annotations


class AntiEchoPolicy:
    def accepts_transcript(self, transcript: str) -> bool:
        raise NotImplementedError("anti-echo policy is not implemented yet")
