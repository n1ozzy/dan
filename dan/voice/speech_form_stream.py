"""Route only the model's spoken form ([[GŁOS]] block) out of a delta stream.

To keep first-sound while speaking a redacted form, the model opens its answer
with a ``[[GŁOS]]…[[/GŁOS]]`` block. This router forwards only that inner text
to the speech consumer as deltas arrive, and drops the rich chat text that
follows. Markers can be split across delta boundaries, so it holds back any
suffix that might be the start of a marker until the next delta decides it.
"""

from __future__ import annotations

from collections.abc import Callable

_OPEN = "[[GŁOS]]"
_CLOSE = "[[/GŁOS]]"

_BEFORE = "before"
_INSIDE = "inside"
_AFTER = "after"
_PASSTHROUGH = "passthrough"

# First-sound safety valve: the block must OPEN the answer, so if no open
# marker showed up within this many buffered chars the model is not complying —
# switch to raw passthrough instead of staying silent until finalize.
_PASSTHROUGH_AFTER_CHARS = 64


def _held_back_len(buffer: str, marker: str) -> int:
    """Length of the longest suffix of ``buffer`` that is a prefix of ``marker``.

    That suffix might be the start of a marker split across the next delta, so
    the caller keeps it buffered instead of acting on it yet.
    """

    max_k = min(len(buffer), len(marker) - 1)
    for k in range(max_k, 0, -1):
        if buffer[-k:] == marker[:k]:
            return k
    return 0


class SpeechFormStreamRouter:
    """Feed raw stream deltas in; only the [[GŁOS]] inner text reaches TTS."""

    def __init__(self, on_speech: Callable[[str], None]) -> None:
        self._on_speech = on_speech
        self._buffer = ""
        self._state = _BEFORE

    def feed(self, delta: str) -> None:
        if not delta:
            return
        self._buffer += delta
        # Re-run the state machine until the buffer can make no more progress;
        # a single delta may open and close the block at once.
        while True:
            if self._state == _AFTER:
                self._buffer = ""
                return
            if self._state == _BEFORE:
                if not self._advance_before():
                    return
                continue
            if self._state == _INSIDE:
                if not self._advance_inside():
                    return
                continue
            if self._state == _PASSTHROUGH:
                self._advance_passthrough()
                return
            return

    def _advance_before(self) -> bool:
        index = self._buffer.find(_OPEN)
        if index >= 0:
            # Everything up to the marker is pre-block text (not speech); drop it.
            self._buffer = self._buffer[index + len(_OPEN) :]
            self._state = _INSIDE
            return True
        if len(self._buffer) > _PASSTHROUGH_AFTER_CHARS:
            # The model ignored the instruction (the block must open the
            # answer). Fall through to raw streaming so live speech is not
            # silent until finalize; markers are still stripped if they show
            # up late. finalize() only flushes the chunker tail, so nothing
            # is spoken twice.
            self._state = _PASSTHROUGH
            return True
        # No full open marker yet and still within the threshold: hold the
        # whole buffer — a compliant block may still open the answer.
        return False

    def _advance_inside(self) -> bool:
        index = self._buffer.find(_CLOSE)
        if index >= 0:
            self._emit(self._buffer[:index])
            self._buffer = self._buffer[index + len(_CLOSE) :]
            self._state = _AFTER
            return True
        # No full close marker: emit the safe prefix, hold back a suffix that
        # might be the start of the close marker.
        keep = _held_back_len(self._buffer, _CLOSE)
        safe = self._buffer[: len(self._buffer) - keep] if keep else self._buffer
        self._emit(safe)
        self._buffer = self._buffer[len(self._buffer) - keep :] if keep else ""
        return False

    def _advance_passthrough(self) -> None:
        # Raw streaming, minus the markers themselves: strip any complete
        # marker, hold back a suffix that might be the start of one.
        for marker in (_OPEN, _CLOSE):
            self._buffer = self._buffer.replace(marker, "")
        keep = max(
            _held_back_len(self._buffer, _OPEN),
            _held_back_len(self._buffer, _CLOSE),
        )
        safe = self._buffer[: len(self._buffer) - keep] if keep else self._buffer
        self._emit(safe)
        self._buffer = self._buffer[len(self._buffer) - keep :] if keep else ""

    def _emit(self, text: str) -> None:
        if text:
            self._on_speech(text)


__all__ = ["SpeechFormStreamRouter"]
