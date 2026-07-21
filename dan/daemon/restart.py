"""Safe in-process daemon restart semantics (POST /runtime/restart, Task 9).

Contract:

1. Close intake and drain/cancel in-flight voice, stop supervised children,
   playback and the hotkey — all through DaemonApp.stop(), the one shutdown
   path that already owns that ordering.
2. Exit the process with the documented RESTART_EXIT_CODE. In production
   launchd (KeepAlive) resurrects dand; in tests the exit function is
   injected. The coordinator NEVER calls launchctl or pkill — process
   resurrection is the platform's job, not ours.
3. If the drain raises, containment decides: supervised children proven dead
   exits anyway; children left alive — or containment unavailable — keeps the
   process here, and then it reports itself failed so the outage is visible
   instead of green (ADR-001).

KNOWN DEFECT — do not read point 3 as sound reasoning. Containment proves only
that ChildSupervisor's children are dead; it says nothing about how far
DaemonApp.stop() actually got. Three of stop()'s four raise sites fire BEFORE
the voice teardown (app.py:482 close_intake, :483 wait_for_drain, :552
_quiesce_voice_broker), and a lease outliving the drain timeout is the likeliest
failure of all. On that path this code exits a fully live daemon mid-turn, and
os._exit skips the hotkey release, brain_manager.close() and the recorder —
orphaning the Claude stream-json subprocess and sox onto a live mic.
See docs/reviews/2026-07-21-restart-orphan-shell-review.md §6-§7.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

from dan.logging import get_logger

logger = get_logger(__name__)

# Documented restart exit code: distinguishable from a clean stop (0) and from
# crash codes, so launchd logs show "restart requested" rather than "died".
RESTART_EXIT_CODE = 86

# Small grace so the HTTP response for POST /runtime/restart flushes to the
# client before the listener goes down with the process.
DEFAULT_RESPONSE_FLUSH_SECONDS = 0.2


class RestartCoordinator:
    """Drains the daemon and exits with the documented restart code."""

    def __init__(
        self,
        app: Any,
        *,
        exit_fn: Callable[[int], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        flush_seconds: float = DEFAULT_RESPONSE_FLUSH_SECONDS,
    ) -> None:
        self._app = app
        # os._exit: the HTTP server thread pool must not block the exit. The
        # "app.stop() already flushed durable state" half of that holds only on
        # the success path — the drain-failure branch exits after stop() raised,
        # so daemon.stopped never lands and finally/atexit/logging.shutdown()
        # are all skipped.
        self._exit = exit_fn or os._exit
        self._sleep = sleep
        self._flush_seconds = flush_seconds
        self._lock = threading.Lock()
        self._restarting = False
        self._operation_id: str | None = None
        self._reason: str | None = None

    @property
    def restarting(self) -> bool:
        return self._restarting

    def request_restart(
        self,
        *,
        reason: str = "api restart",
        synchronous: bool = False,
    ) -> dict[str, Any]:
        """Start the drain-and-exit sequence exactly once.

        `synchronous=True` runs the sequence inline (tests); the default
        defers it to a named thread so the HTTP response can be written first.
        """

        with self._lock:
            already = self._restarting
            if already:
                operation_id = self._operation_id
                response_reason = self._reason
            else:
                operation_id = self._app.close_intake(reason=reason)
                self._operation_id = operation_id
                self._reason = reason
                self._restarting = True
                response_reason = reason
        response = {
            "ok": True,
            "restarting": True,
            "already_restarting": already,
            "exit_code": RESTART_EXIT_CODE,
            "operation_id": operation_id,
            "reason": response_reason,
        }
        if already:
            return response
        if synchronous:
            self._drain_and_exit(reason)
        else:
            thread = threading.Thread(
                target=self._drain_and_exit,
                args=(reason,),
                name="dan-restart",
                daemon=True,
            )
            thread.start()
        return response

    def _drain_and_exit(self, reason: str) -> None:
        self._sleep(self._flush_seconds)
        try:
            # stop() closes intake (started=False), waits out the in-flight
            # voice turn, stops broker/player/recorder/STT, reaps supervised
            # children and releases the hotkey owner lock.
            self._app.stop(reason=reason)
        except Exception:  # noqa: BLE001 - containment decides whether exit is safe
            logger.exception("Restart drain failed; proving emergency containment.")
            containment = self._emergency_containment()
            if containment is not None and containment.complete:
                # Every supervised child is proven dead, so launchd cannot end
                # up running a second daemon beside a live supertonic — the
                # danger blocking the exit was written against. Exiting is also
                # what frees the hotkey flock the next daemon needs for PTT.
                #
                # This is NOT proof that leaving is safe. "stop() already
                # dropped broker, engine and player, so this process can no
                # longer speak" holds only when stop() raised at app.py:568 or
                # :582; at :482/:483/:552 the voice layer is still up and this
                # exit kills a live daemon mid-turn. See the module docstring
                # and docs/reviews/2026-07-21-restart-orphan-shell-review.md §6.
                logger.critical(
                    "Restart drain failed but every supervised child is contained; "
                    "exiting %s so launchd can start a clean owner.",
                    RESTART_EXIT_CODE,
                )
                self._exit(RESTART_EXIT_CODE)
                return
            errors = getattr(containment, "errors", ("containment unavailable",))
            logger.critical(
                "Restart exit blocked because owner containment is incomplete: %s",
                "; ".join(errors),
            )
            self._report_failure(reason, errors)
            with self._lock:
                self._restarting = False
            return
        logger.info("Exiting with restart code %s (%s).", RESTART_EXIT_CODE, reason)
        self._exit(RESTART_EXIT_CODE)

    def _report_failure(self, reason: str, errors) -> None:
        """Make a blocked exit visible rather than let it read as healthy.

        The process outlives the restart with intake closed and voice gone, so
        without this it keeps answering ok and the outage looks like anything
        but the daemon.
        """

        mark = getattr(self._app, "mark_failed", None)
        if not callable(mark):
            return
        try:
            mark(reason=f"restart drain failed: {reason}", errors=tuple(errors))
        except Exception:
            logger.exception("Could not mark the daemon failed after a blocked restart.")

    def _emergency_containment(self):
        from dan.daemon.supervisor import ChildContainmentResult

        contain = getattr(
            self._app,
            "emergency_contain_supervised_children",
            None,
        )
        try:
            if callable(contain):
                result = contain()
            else:
                supervisor = getattr(self._app, "child_supervisor", None)
                if supervisor is None:
                    return None
                result = supervisor.stop_all()
        except Exception:
            logger.exception("Emergency supervised-child containment failed.")
            return None
        if not isinstance(result, ChildContainmentResult):
            logger.error(
                "Emergency containment returned no typed ownership proof: %r",
                result,
            )
            return None
        return result

__all__ = ["DEFAULT_RESPONSE_FLUSH_SECONDS", "RESTART_EXIT_CODE", "RestartCoordinator"]
