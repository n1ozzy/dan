"""Speech pipeline: turn text -> sentence chunks -> VoiceQueue (G3).

Implements the G0 contract legs the orchestrator needs: sentence-cut the
canonical answer into VoiceRequests (tool-call blocks never spoken) and arm
the filler timer while generation runs. All persistence goes through the
VoiceQueue; the pipeline owns no audio and no state beyond the timer.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from typing import Any

from jarvis.store.db import close_quietly
from jarvis.store.event_store import create_event_store
from jarvis.voice.chunker import SentenceChunker
from jarvis.voice.queue import VoiceQueue


DEFAULT_FILLER_AFTER_MS = 1200
DEFAULT_FILLERS = ("Już sprawdzam.", "Chwila.")


class FillerTimer:
    """At most one filler per turn; disarm() wins if it comes first."""

    def __init__(self, fire: Callable[[], None], delay_seconds: float) -> None:
        self._lock = threading.Lock()
        self._fired = False
        self._disarmed = False
        self._fire = fire
        self._timer = threading.Timer(delay_seconds, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def _on_timeout(self) -> None:
        with self._lock:
            if self._disarmed or self._fired:
                return
            self._fired = True
        self._fire()

    def disarm(self) -> None:
        with self._lock:
            self._disarmed = True
        self._timer.cancel()


class _NullTimer:
    def disarm(self) -> None:
        return None


class SpeechPipeline:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        config: Any,
    ) -> None:
        self._connect = connection_factory
        self._config = config
        self._filler_rotation = itertools.count()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False)) and bool(
            getattr(self._config, "speak_responses", False)
        )

    def speak_text(self, *, turn_id: str, text: str) -> int:
        """Sentence-cut the canonical text and enqueue one request per chunk."""

        if not self.enabled or not isinstance(text, str) or not text.strip():
            return 0
        chunker = SentenceChunker(
            min_chars=int(getattr(self._config, "min_sentence_chars", 12))
        )
        chunks = chunker.feed(text)
        chunks.extend(chunker.flush())
        if not chunks:
            return 0
        conn = self._connect()
        try:
            queue = VoiceQueue(conn, event_store=create_event_store(conn))
            for seq, chunk in enumerate(chunks):
                queue.enqueue(
                    text=chunk,
                    turn_id=turn_id,
                    kind="sentence",
                    seq=seq,
                )
        finally:
            close_quietly(conn)
        return len(chunks)

    def arm_filler(self, *, turn_id: str):
        """Arm the one-shot filler for a turn about to hit the brain."""

        if not self.enabled:
            return _NullTimer()
        fillers = tuple(getattr(self._config, "fillers", DEFAULT_FILLERS)) or DEFAULT_FILLERS
        delay_ms = int(getattr(self._config, "filler_after_ms", DEFAULT_FILLER_AFTER_MS))
        index = next(self._filler_rotation) % len(fillers)
        filler_text = fillers[index]

        def fire() -> None:
            conn = self._connect()
            try:
                queue = VoiceQueue(conn, event_store=create_event_store(conn))
                queue.enqueue(
                    text=filler_text,
                    turn_id=turn_id,
                    kind="filler",
                    seq=-1,  # a filler always precedes the real sentences
                    interrupt_policy="interruptible",
                )
            finally:
                close_quietly(conn)

        return FillerTimer(fire, max(delay_ms, 0) / 1000.0)


__all__ = ["DEFAULT_FILLERS", "FillerTimer", "SpeechPipeline"]
