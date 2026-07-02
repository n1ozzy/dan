"""AntiEchoGate (G4c): echo of Jarvis's own TTS never becomes a turn.

Content anti-echo per AUDIO_RUNTIME §4: an incoming transcript is compared
against what the daemon recently sent to the speaker. The corpus is read
from the persisted voice_queue — daemon state, never a /tmp flag — and only
rows that at least reached playback count ('speaking', 'done', and
'cancelled' for chunks killed mid-play); a 'queued' row never made a sound,
so it cannot echo.

The comparison is deterministic token overlap on normalized text: if the
share of transcript tokens that also occur in one recently spoken sentence
reaches the threshold, the transcript is rejected as echo. Fail-closed for
turn creation: dropping a user sentence that duplicates Jarvis's own words
is acceptable; an echo that becomes a turn is a violation by construction.
Thresholds are config data, calibrated at the G4 live gate.
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

# Rows that produced (or were producing) actual sound. 'queued' is absent on
# purpose: text that never reached the speaker cannot be an echo source.
_SPOKEN_STATUSES = ("speaking", "done", "cancelled")


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

    def accepts_transcript(self, transcript: str) -> EchoDecision:
        tokens = set(normalize_phrase(transcript).split())
        if not tokens:
            # Nothing comparable; junk filtering is the pipeline's job.
            return EchoDecision(accepted=True, reason="ok")
        for spoken in self._recently_spoken():
            spoken_tokens = set(normalize_phrase(spoken).split())
            if not spoken_tokens:
                continue
            overlap = len(tokens & spoken_tokens) / len(tokens)
            if overlap >= self._threshold:
                return EchoDecision(accepted=False, reason="echo", matched_text=spoken)
        return EchoDecision(accepted=True, reason="ok")

    # -- internals -----------------------------------------------------------

    def _recently_spoken(self) -> list[str]:
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=self._window_seconds)
        ).isoformat(timespec="seconds")
        placeholders = ", ".join("?" for _ in _SPOKEN_STATUSES)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT text FROM voice_queue "
                f"WHERE status IN ({placeholders}) AND updated_at >= ?",
                (*_SPOKEN_STATUSES, cutoff),
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            close_quietly(conn)


__all__ = ["AntiEchoGate", "EchoDecision"]
