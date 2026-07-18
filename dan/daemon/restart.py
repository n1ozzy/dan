"""Safe in-process daemon restart semantics (POST /runtime/restart, Task 9).

Contract:

1. Close intake and drain/cancel in-flight voice, stop supervised children,
   playback and the hotkey — all through DaemonApp.stop(), the one shutdown
   path that already owns that ordering.
2. Exit the process with the documented RESTART_EXIT_CODE. In production
   launchd (KeepAlive) resurrects dand; in tests the exit function is
   injected. The coordinator NEVER calls launchctl or pkill — process
   resurrection is the platform's job, not ours.
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
        # os._exit: the HTTP server thread pool must not block the exit and
        # app.stop() has already flushed durable state.
        self._exit = exit_fn or os._exit
        self._sleep = sleep
        self._flush_seconds = flush_seconds
        self._lock = threading.Lock()
        self._restarting = False

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
            self._restarting = True
        response = {
            "ok": True,
            "restarting": True,
            "already_restarting": already,
            "exit_code": RESTART_EXIT_CODE,
            "reason": reason,
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
            if containment is None or not containment.complete:
                errors = getattr(containment, "errors", ("containment unavailable",))
                logger.critical(
                    "Restart exit blocked because owner containment is incomplete: %s",
                    "; ".join(errors),
                )
                with self._lock:
                    self._restarting = False
                return
        logger.info("Exiting with restart code %s (%s).", RESTART_EXIT_CODE, reason)
        self._exit(RESTART_EXIT_CODE)

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
