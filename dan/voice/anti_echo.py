"""AntiEchoGate (G4c): echo of DAN's own TTS never becomes a turn.

Content anti-echo per AUDIO_RUNTIME §4: an incoming transcript is compared
against what actually reached a speaker. Legacy DAN playback is read from
persisted ``voice_queue.spoken_at``. When the shared broker owns audio, its
bounded ``spoken-recent.txt`` ring is the playback truth and is merged read-only
with the DB corpus.

The comparison is deterministic token overlap on normalized text. A PTT capture
spans several consecutive TTS sentences, so the primary signal is UNION overlap
(the incoming tokens against the union of every sentence DAN spoke in the
window) — measured live 2026-07-02: pure echo ~1.0 union vs a real interjection
0.31. Rejection additionally requires that some SINGLE spoken row overlaps too
(best-row >= min_row_overlap): the union of a long TTS history covers most common
words, so union alone would falsely reject an original user sentence that merely
reuses scattered words; a genuine echo, being a copy, always lands high overlap
on the specific rows it echoes. A minimum token count is also required so short
follow-ups referencing DAN's key terms ("file_read tool") are not dropped.
Threshold, min_echo_tokens and min_row_overlap are config data, calibrated at
the G4 live gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import time
from typing import Any

from dan.store.db import close_quietly
from dan.voice.transcription import normalize_phrase
from dan.voice.shared_broker import DEFAULT_SPOKEN_RECENT_PATH


DEFAULT_WINDOW_SECONDS = 30
DEFAULT_OVERLAP_THRESHOLD = 0.75
DEFAULT_MIN_ECHO_TOKENS = 5
# A single spoken row must clear this to confirm echo, guarding against a bloated
# union (long TTS history) falsely rejecting an original user turn. Kept below the
# measured per-row echo overlap (~0.52) so real echoes still trip it.
DEFAULT_MIN_ROW_OVERLAP = 0.4
MAX_SHARED_RING_BYTES = 64 * 1024
MAX_SHARED_RING_ROWS = 256
MAX_SHARED_RING_TEXT_CHARS = 2000

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
        shared_spoken_path: str | Path = DEFAULT_SPOKEN_RECENT_PATH,
        clock: Callable[[], float] = time.time,
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
        self._min_row_overlap = float(
            getattr(config, "anti_echo_min_row_overlap", DEFAULT_MIN_ROW_OVERLAP)
            or DEFAULT_MIN_ROW_OVERLAP
        )
        self._shared_broker_enabled = bool(getattr(config, "broker_enabled", False))
        self._shared_spoken_path = Path(shared_spoken_path)
        self._clock = clock

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
        # creation: dropping a user sentence that duplicates DAN's own words is
        # acceptable; an echo that becomes a turn is a violation by construction.
        # Reject only when ALL hold: high union overlap, minimum token count, AND
        # some single spoken row clears min_row_overlap. The last guard stops a
        # bloated union (long TTS history covering most common words) from falsely
        # rejecting an original user sentence — a real echo, being a copy, always
        # lands high on the specific rows it repeats.
        if union:
            union_overlap = len(tokens & union) / len(tokens)
            if (
                union_overlap >= self._threshold
                and len(tokens) >= self._min_echo_tokens
                and best_overlap >= self._min_row_overlap
            ):
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
            spoken = [str(row[0]) for row in rows]
        finally:
            close_quietly(conn)
        if not self._shared_broker_enabled:
            return spoken
        seen = set(spoken)
        for text in self._recent_shared_spoken():
            if text not in seen:
                spoken.append(text)
                seen.add(text)
        return spoken

    def _recent_shared_spoken(self) -> list[str]:
        """Read the broker TSV ring without ever loading an unbounded file."""

        try:
            with self._shared_spoken_path.open("rb") as handle:
                size = self._shared_spoken_path.stat().st_size
                start = max(0, size - MAX_SHARED_RING_BYTES)
                handle.seek(start)
                raw = handle.read(MAX_SHARED_RING_BYTES)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            return []
        if start:
            _partial, separator, raw = raw.partition(b"\n")
            if not separator:
                return []

        now = self._clock()
        spoken: list[str] = []
        lines = raw.decode("utf-8", errors="replace").splitlines()[-MAX_SHARED_RING_ROWS:]
        for line in lines:
            timestamp, separator, text = line.partition("\t")
            if not separator or not text or len(text) > MAX_SHARED_RING_TEXT_CHARS:
                continue
            try:
                age = now - float(timestamp)
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 <= age <= self._window_seconds:
                spoken.append(text)
        return spoken


__all__ = ["AntiEchoGate", "EchoDecision", "MAX_SHARED_RING_BYTES"]
