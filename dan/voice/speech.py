"""Speech chunking and producer intents; persistence belongs to VoiceService."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from typing import Any

from dan.config import DEFAULT_VOICE_FILLERS
from dan.logging import get_logger
from dan.voice.chunker import SentenceChunker
from dan.voice.models import SpeechIntent

_LOGGER = get_logger("voice.speech")
DEFAULT_FILLER_AFTER_MS = 800
DEFAULT_FILLERS = DEFAULT_VOICE_FILLERS


class FillerTimer:
    """At most one filler per turn; disarm wins if it arrives first."""

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


class SpeechStreamSession:
    def __init__(
        self,
        *,
        voice_service: Any,
        turn_id: str,
        min_chars: int,
        filler_timer: Any | None = None,
        enabled: bool = True,
        persona: str = "dan",
        source: str = "dand",
    ) -> None:
        self._voice_service = voice_service
        self._turn_id = turn_id
        self._chunker = SentenceChunker(min_chars=min_chars)
        self._filler_timer = filler_timer
        self._enabled = enabled
        self._persona = persona
        self._source = source
        self._fed_any = False
        self._filler_disarmed = False
        self._seq = 0
        self._finalized = False

    def feed(self, delta: str) -> None:
        if not self._enabled or not isinstance(delta, str) or not delta:
            return
        if delta.strip():
            self._disarm_filler()
        self._fed_any = True
        try:
            self._submit(self._chunker.feed(delta), lane="live")
        except Exception:  # noqa: BLE001 - voice remains best effort for a turn
            _LOGGER.exception("streamed speech submit failed; muting this turn")
            self._enabled = False

    def finalize(self, final_text: str, *, lane: str = "final") -> int:
        if not self._enabled or self._finalized:
            return 0
        self._finalized = True
        try:
            if self._fed_any:
                chunks = self._chunker.flush()
            else:
                chunks = self._chunker.feed(str(final_text or ""))
                chunks.extend(self._chunker.flush())
            self._submit(chunks, lane=_queue_lane(lane))
        except Exception:  # noqa: BLE001 - speech must not fail the text turn
            _LOGGER.exception("final speech submit failed; turn stays unspoken")
            self._enabled = False
        return self._seq

    def _submit(self, chunks: list[str], *, lane: str) -> None:
        for chunk in chunks:
            self._voice_service.submit(
                _intent(
                    text=chunk,
                    persona=self._persona,
                    source=self._source,
                    session=self._turn_id,
                    lane=lane,
                    utterance_index=self._seq,
                )
            )
            self._seq += 1
        if chunks:
            self._disarm_filler()

    def _disarm_filler(self) -> None:
        if self._filler_timer is not None and not self._filler_disarmed:
            self._filler_timer.disarm()
            self._filler_disarmed = True


class SpeechPipeline:
    def __init__(
        self,
        *,
        config: Any,
        voice_service: Any | None = None,
    ) -> None:
        self._config = config
        self._voice_service = voice_service
        self._filler_rotation = itertools.count()
        if self.enabled and self._voice_service is None:
            raise TypeError("enabled SpeechPipeline requires VoiceService")

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False)) and bool(
            getattr(self._config, "speak_responses", False)
        )

    def speak_text(self, *, turn_id: str, text: str, lane: str = "final") -> int:
        if not self.enabled or not isinstance(text, str) or not text.strip():
            return 0
        chunker = SentenceChunker(
            min_chars=int(getattr(self._config, "min_sentence_chars", 12))
        )
        chunks = chunker.feed(text)
        chunks.extend(chunker.flush())
        for seq, chunk in enumerate(chunks):
            self._voice_service.submit(
                _intent(
                    text=chunk,
                    persona="dan",
                    source="dand",
                    session=turn_id,
                    lane=_queue_lane(lane),
                    utterance_index=seq,
                )
            )
        return len(chunks)

    def start_stream(
        self,
        *,
        turn_id: str,
        filler_timer: Any | None = None,
    ) -> SpeechStreamSession:
        return SpeechStreamSession(
            voice_service=self._voice_service,
            turn_id=turn_id,
            min_chars=int(getattr(self._config, "min_sentence_chars", 12)),
            filler_timer=filler_timer,
            enabled=self.enabled,
        )

    def arm_filler(self, *, turn_id: str):
        if not self.enabled:
            return _NullTimer()
        fillers = tuple(getattr(self._config, "fillers", DEFAULT_FILLERS)) or DEFAULT_FILLERS
        delay_ms = int(getattr(self._config, "filler_after_ms", DEFAULT_FILLER_AFTER_MS))
        filler_text = fillers[next(self._filler_rotation) % len(fillers)]

        def fire() -> None:
            try:
                self._voice_service.submit(
                    _intent(
                        text=filler_text,
                        persona="dan",
                        source="dand",
                        session=turn_id,
                        lane="live",
                        utterance_index=0,
                        interrupt_policy="interruptible",
                    )
                )
            except Exception:  # noqa: BLE001 - timer must stay isolated
                _LOGGER.debug("filler submit skipped for turn %s", turn_id)

        return FillerTimer(fire, max(delay_ms, 0) / 1000.0)


def _queue_lane(lane: str) -> str:
    if lane == "background":
        return "background"
    if lane in {"live", "commentary"}:
        return "live"
    return "normal"


def _intent(
    *,
    text: str,
    persona: str,
    source: str,
    session: str,
    lane: str,
    utterance_index: int,
    interrupt_policy: str = "finish_current",
) -> SpeechIntent:
    return SpeechIntent(
        text=text,
        persona=persona,
        source=source,
        session=session,
        participant=persona,
        priority=0,
        lane=lane,
        interrupt_policy=interrupt_policy,
        utterance_index=utterance_index,
    )


__all__ = ["DEFAULT_FILLERS", "FillerTimer", "SpeechPipeline", "SpeechStreamSession"]
