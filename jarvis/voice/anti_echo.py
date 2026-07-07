"""AntiEchoGate (G4c): echo of Jarvis's own TTS never becomes a turn.

Content anti-echo per AUDIO_RUNTIME §4: an incoming transcript is compared
against what the daemon recently sent to the speaker. The corpus is read
from the persisted voice_queue — daemon state, never a /tmp flag — and only
rows that actually reached playback: the broker stamps `spoken_at` the moment
it plays a chunk, so `spoken_at IS NOT NULL` is the truth of "made a sound",
independent of final status. A 'queued' row a barge-in flipped to 'cancelled'
never played (spoken_at NULL) and is excluded; a 'failed' row killed mid-play
(spoken_at set) did put audio in the air and is included (FIX-09).

The comparison is deterministic token overlap on normalized text, computed
row-by-row (not union) against each sentence Jarvis spoke in the window. The
best row overlap is used. To avoid rejecting conversational follow-ups that
reference Jarvis's key terms (short phrases like "file_read tool" hitting 1.0
overlap against the introducing sentence), a minimum token count is required
before rejection: echoes are typically longer sentences, follow-ups are short.
Threshold and min_echo_tokens are config data, calibrated at the G4 live gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.store.db import close_quietly
from jarvis.voice.transcription import normalize_phrase


DEFAULT_WINDOW_SECONDS = 30
DEFAULT_OVERLAP_THRESHOLD = 0.75
DEFAULT_MIN_ECHO_TOKENS = 8

# Corpus membership is decided by spoken_at, not status (FIX-09): the broker
# stamps spoken_at the moment a chunk reaches the speaker, so a NULL means the
# row never made a sound (a 'queued' row a barge-in flipped to 'cancelled'),
# while any non-NULL — including a 'failed' row killed mid-play — did echo.


@dataclass(frozen=True)
class EchoDecision:
    accepted: bool
    reason: str
    matched_text: str | None = None


class AntiEchoGate:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        config: Any,
    ) -> None:
        self._connect = connection_factory
        self._window_seconds = int(
            getattr(config, "anti_echo_window_seconds", DEFAULT_WINDOW_SECONDS)
            or DEFAULT_WINDOW_SECONDS
        )
        self._threshold = float(
            getattr(config, "anti_echo_overlap_threshold", DEFAULT_OVERLAP_THRESHOLD)
            or DEFAULT_OVERLAP_THRESHOLD
        )
        self._min_echo_tokens = int(
            getattr(config, "anti_echo_min_echo_tokens", DEFAULT_MIN_ECHO_TOKENS)
            or DEFAULT_MIN_ECHO_TOKENS
        )

    def accepts_transcript(self, transcript: str) -> EchoDecision:
        tokens = set(normalize_phrase(transcript).split())
        if not tokens:
            return EchoDecision(accepted=True, reason="ok")

        union: set[str] = set()
        best_row, best_overlap = None, 0.0
        for spoken in self._recently_spoken():
            spoken_tokens = set(normalize_phrase(spoken).split())
            if not spoken_tokens:
                continue
            union |= spoken_tokens
            row_overlap = len(tokens & spoken_tokens) / len(tokens)
            if row_overlap > best_overlap:
                best_row, best_overlap = spoken, row_overlap

        # Union-based echo detection (G4c): a PTT capture spans several consecutive
        # TTS sentences. Against any single row the overlap dilutes to ~1/n —
        # measured live 2026-07-02: pure echo 0.52 per row vs 1.00 union, a real
        # user interjection over playing TTS 0.31 union. Fail-closed for turn
        # creation: dropping a user sentence that duplicates Jarvis's own words is
        # acceptable; an echo that becomes a turn is a violation by construction.
        # Require BOTH: high union overlap AND minimum token count to reject.
        if union:
            union_overlap = len(tokens & union) / len(tokens)
            if union_overlap >= self._threshold and len(tokens) >= self._min_echo_tokens:
                return EchoDecision(accepted=False, reason="echo", matched_text=best_row)

        return EchoDecision(accepted=True, reason="ok")

    # -- internals -----------------------------------------------------------

    def _recently_spoken(self) -> list[str]:
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=self._window_seconds)
        ).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            # spoken_at is both the membership test (non-NULL = actually played)
            # and the recency clock (when it played), so cancelled/failed rows
            # that never reached the speaker are excluded by construction.
            rows = conn.execute(
                "SELECT text FROM voice_queue "
                "WHERE spoken_at IS NOT NULL AND spoken_at >= ?",
                (cutoff,),
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            close_quietly(conn)


__all__ = ["AntiEchoGate", "EchoDecision"]
