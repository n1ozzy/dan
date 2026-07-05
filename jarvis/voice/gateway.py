"""VoiceTurnGateway (G4c): accepted transcript → turn, behind anti-echo.

The single wiring point between the STT pipeline and the TurnOrchestrator
(ADR-011: voice enters the same orchestrator as panel text). Order is the
contract:

1. Anti-echo gate first — an echo never becomes a turn AND never counts as
   barge-in (the system must not be cancelled by its own voice).
2. Mic-side barge-in — real user speech while Jarvis is speaking or
   generating cancels all three legs (jarvis/voice/cancellation.py) BEFORE
   the new turn starts.
3. The turn runs on the gateway's own worker thread: the STT worker hands
   off and returns, so a generating brain never blocks transcription of the
   next utterance (which may be the barge-in against this very turn).

A busy pipeline (previous turn still winding down after its generation was
killed) is retried within a bounded window, then dropped with a log — the
transcript event is already persisted, nothing is silently lost from the
audit trail.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jarvis.logging import get_logger


_LOGGER = get_logger("voice.gateway")

DEFAULT_RETRY_SECONDS = 10.0
DEFAULT_RETRY_INTERVAL = 0.2


class VoiceTurnGateway:
    def __init__(
        self,
        *,
        anti_echo: Any,
        cancellation: Any,
        turn_starter: Callable[[str], Any],
        speech_active: Callable[[], bool],
        busy_exceptions: tuple[type[BaseException], ...] = (),
        cancelled_exceptions: tuple[type[BaseException], ...] = (),
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        retry_interval: float = DEFAULT_RETRY_INTERVAL,
    ) -> None:
        self._anti_echo = anti_echo
        self._cancellation = cancellation
        self._turn_starter = turn_starter
        self._speech_active = speech_active
        self._busy_exceptions = tuple(busy_exceptions)
        self._cancelled_exceptions = tuple(cancelled_exceptions)
        self._retry_seconds = float(retry_seconds)
        self._retry_interval = float(retry_interval)
        self._stopped = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-voice-turn"
        )

    # -- public surface --------------------------------------------------------

    def handle_transcript(self, text: str) -> None:
        """STT consumer entry point; gate + barge-in inline, turn on the worker."""

        if self._stopped:
            _LOGGER.warning("gateway stopped; transcript dropped: %d chars", len(text))
            return
        try:
            decision = self._anti_echo.accepts_transcript(text)
        except Exception:  # noqa: BLE001 — fail closed: no decision, no turn
            _LOGGER.exception("anti-echo gate failed; transcript dropped.")
            return
        if not decision.accepted:
            _LOGGER.info(
                "transcript rejected as %s (matched %r); no turn started.",
                decision.reason,
                decision.matched_text,
            )
            return
        if self._is_speech_active():
            # Mic-side barge-in: the user spoke over Jarvis and it was NOT
            # an echo — kill generation, queue and playback before the new turn.
            try:
                self._cancellation.cancel_active_speech(
                    reason="barge_in", source="voice"
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("barge-in cancellation failed; continuing to the turn.")
        try:
            self._executor.submit(self._run_turn, text)
        except RuntimeError:  # racing stop(): executor already shut down
            _LOGGER.warning("gateway stopping; transcript dropped: %d chars", len(text))

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until every transcript accepted so far is processed (tests/stop)."""

        done = threading.Event()
        try:
            self._executor.submit(done.set)
        except RuntimeError:
            return True
        return done.wait(timeout)

    def stop(self) -> None:
        """Stop accepting transcripts and WAIT for the in-flight turn.

        The turn writes through the daemon's shared connection; returning
        while it still runs would race whoever stops the daemon next
        (busy-retry loops bail out early via the stopped flag)."""

        self._stopped = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    # -- worker thread -----------------------------------------------------------

    def _run_turn(self, text: str) -> None:
        deadline = time.monotonic() + self._retry_seconds
        while True:
            try:
                self._turn_starter(text)
                return
            except self._busy_exceptions:
                if self._stopped:
                    _LOGGER.warning("gateway stopping; busy voice turn dropped.")
                    return
                if time.monotonic() >= deadline:
                    _LOGGER.error(
                        "turn pipeline stayed busy for %.1fs; voice turn dropped "
                        "(transcript remains in the event log).",
                        self._retry_seconds,
                    )
                    return
                time.sleep(self._retry_interval)
            except self._cancelled_exceptions:
                # Barge-in cancelled the turn (FIX-09): a normal terminal
                # outcome, not a failure and not retryable. The turn is already
                # CANCELLED and the runtime back to IDLE — just log and move on.
                _LOGGER.info("voice turn cancelled by barge-in; gateway keeps running.")
                return
            except Exception:  # noqa: BLE001 — the worker must survive a failed turn
                _LOGGER.exception("voice turn failed; gateway keeps running.")
                return

    def _is_speech_active(self) -> bool:
        try:
            return bool(self._speech_active())
        except Exception:  # noqa: BLE001
            _LOGGER.exception("speech-activity probe failed; assuming idle.")
            return False


__all__ = ["VoiceTurnGateway"]
