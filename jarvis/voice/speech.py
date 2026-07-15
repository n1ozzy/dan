"""Speech routing: legacy VoiceQueue or one utterance per model response.

Implements the G0 contract legs the orchestrator needs: sentence-cut the
canonical answer into VoiceRequests (tool-call blocks never spoken), arm
the filler timer while generation runs, and — since G4d — consume adapter
deltas live through a SpeechStreamSession, so the first sentence is queued
while the model is still generating (first-sound requirement, §4a). All
persistence goes through the VoiceQueue; deltas themselves are transport,
not truth, and are never persisted. The pipeline owns no audio and no
state beyond the timer.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from typing import Any

from jarvis.config import DEFAULT_VOICE_FILLERS
from jarvis.events.types import EventType
from jarvis.logging import get_logger
from jarvis.store.db import close_quietly
from jarvis.store.event_store import create_event_store
from jarvis.voice.chunker import SentenceChunker
from jarvis.voice.queue import VoiceQueue
from jarvis.voice.shared_broker import SharedBrokerClient


_LOGGER = get_logger("voice.speech")

DEFAULT_FILLER_AFTER_MS = 800
DEFAULT_FILLERS = DEFAULT_VOICE_FILLERS


def _record_shared_publish(
    connection_factory: Callable[[], Any],
    *,
    request_path: Any,
    turn_id: str,
    lane: str,
) -> None:
    """Persist only the lifecycle fact Jarvis owns: external publication.

    The shared broker exposes no per-request acknowledgement or cancellation,
    so this deliberately emits no started/finished/cancelled fiction.
    """

    conn = connection_factory()
    try:
        create_event_store(conn).append(
            EventType.VOICE_SPEAK_QUEUED,
            "voice.shared_publisher",
            {
                "request_id": str(getattr(request_path, "stem", "") or "external"),
                "turn_id": turn_id,
                "kind": lane,
                "lane": lane,
                "seq": 0,
                "transport": "external_shared_broker",
                "delivery_state": "published",
                "interrupt_policy": "uninterruptible",
                "acknowledgement": "unavailable",
                "cancel_supported": False,
            },
            correlation_id=turn_id,
            turn_id=turn_id,
        )
    except Exception:  # noqa: BLE001 — observability must not silence speech
        _LOGGER.exception("shared speech publish event could not be persisted")
    finally:
        close_quietly(conn)


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


class SpeechStreamSession:
    """One turn's delta consumer: deltas -> chunker -> VoiceQueue, live.

    `feed()` is called from whatever thread runs the adapter; each completed
    sentence is enqueued immediately (G0 §5) and the filler is disarmed the
    moment the first meaningful delta arrives (G0 §6), before a full sentence
    is required. Speaking is best effort by contract: any queue failure disables
    the session with a log and must never fail the turn. `finalize(final_text)`
    closes the stream —
    if no delta ever arrived (a non-streaming adapter), the canonical text
    is chunked after the fact, which is exactly the old speak_text path.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        turn_id: str,
        min_chars: int,
        filler_timer: Any | None = None,
        enabled: bool = True,
        shared_broker: Any | None = None,
    ) -> None:
        self._connect = connection_factory
        self._turn_id = turn_id
        self._chunker = SentenceChunker(min_chars=min_chars)
        self._filler_timer = filler_timer
        self._enabled = enabled
        self._shared_broker = shared_broker
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
        if self._shared_broker is not None:
            # The shared broker needs one whole request so it can group sentences
            # for natural inter-sentence prosody. The canonical speech_text passed
            # to finalize is authoritative; transport deltas are never published.
            return
        try:
            self._enqueue(self._chunker.feed(delta))
        except Exception:  # noqa: BLE001 — speech must never fail generation
            _LOGGER.exception("streamed sentence enqueue failed; muting this turn.")
            self._enabled = False

    def finalize(self, final_text: str, *, lane: str = "final") -> int:
        if not self._enabled or self._finalized:
            return 0
        self._finalized = True
        try:
            if self._shared_broker is not None:
                text = str(final_text or "").strip()
                if not text:
                    return 0
                request_path = self._shared_broker.enqueue(
                    text=text,
                    session=self._turn_id,
                    priority=0,
                    lane=lane,
                )
                _record_shared_publish(
                    self._connect,
                    request_path=request_path,
                    turn_id=self._turn_id,
                    lane=lane,
                )
                self._seq = 1
                self._disarm_filler()
                return self._seq
            if self._fed_any:
                chunks = self._chunker.flush()
            else:
                # Degradation path (G0 §2): no deltas ever arrived, so the
                # canonical text is sentence-cut in one piece after the fact.
                chunker = self._chunker
                chunks = chunker.feed(str(final_text or ""))
                chunks.extend(chunker.flush())
            self._enqueue(chunks)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("final sentence enqueue failed; turn stays unspoken.")
            self._enabled = False
        return self._seq

    # -- internals -----------------------------------------------------------

    def _enqueue(self, chunks: list[str]) -> None:
        if not chunks:
            return
        conn = self._connect()
        try:
            queue = VoiceQueue(conn, event_store=create_event_store(conn))
            for chunk in chunks:
                queue.enqueue(
                    text=chunk,
                    turn_id=self._turn_id,
                    kind="sentence",
                    seq=self._seq,
                )
                self._seq += 1
        finally:
            close_quietly(conn)
        self._disarm_filler()

    def _disarm_filler(self) -> None:
        if self._filler_timer is not None and not self._filler_disarmed:
            # G0 §6: once real stream output exists, filler must stay out.
            self._filler_timer.disarm()
            self._filler_disarmed = True


class SpeechPipeline:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        config: Any,
        shared_broker: Any | None = None,
    ) -> None:
        self._connect = connection_factory
        self._config = config
        self._shared_broker = (
            shared_broker or SharedBrokerClient(config)
            if bool(getattr(config, "broker_enabled", False))
            else None
        )
        self._filler_rotation = itertools.count()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False)) and bool(
            getattr(self._config, "speak_responses", False)
        )

    def speak_text(self, *, turn_id: str, text: str, lane: str = "final") -> int:
        """Sentence-cut the canonical text and enqueue one request per chunk."""

        if not self.enabled or not isinstance(text, str) or not text.strip():
            return 0
        if self._shared_broker is not None:
            request_path = self._shared_broker.enqueue(
                text=text.strip(),
                session=turn_id,
                priority=0,
                lane=lane,
            )
            _record_shared_publish(
                self._connect,
                request_path=request_path,
                turn_id=turn_id,
                lane=lane,
            )
            return 1
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

    def start_stream(
        self, *, turn_id: str, filler_timer: Any | None = None
    ) -> SpeechStreamSession:
        """Open a delta consumer for one turn (no-op when speech is off)."""

        return SpeechStreamSession(
            self._connect,
            turn_id=turn_id,
            min_chars=int(getattr(self._config, "min_sentence_chars", 12)),
            filler_timer=filler_timer,
            enabled=self.enabled,
            shared_broker=self._shared_broker,
        )

    def arm_filler(self, *, turn_id: str):
        """Arm the one-shot filler for a turn about to hit the brain."""

        if not self.enabled or self._shared_broker is not None:
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
            except Exception:  # noqa: BLE001 — runs on a Timer thread
                # A tombstoned (barge-in-cancelled) turn refuses the filler, and
                # any queue hiccup here must never crash the timer thread (FIX-09).
                _LOGGER.debug("filler enqueue skipped for turn %s.", turn_id)
            finally:
                close_quietly(conn)

        return FillerTimer(fire, max(delay_ms, 0) / 1000.0)


__all__ = ["DEFAULT_FILLERS", "FillerTimer", "SpeechPipeline", "SpeechStreamSession"]
