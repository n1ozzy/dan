"""Daemon-owned global PTT hotkey monitor: one CGEventTap per machine.

`MacOSHotkeyMonitor` is the ONLY global key observer in the DAN runtime
(Release 1, Task 9): the panel lost its NSEvent global monitor and only
displays state / posts manual PTT intent. Ownership is enforced with a file
lock in the dand runtime dir — a second monitor on the same lock path fails
loudly with `SingleOwnerError` instead of installing a second tap.

The real Quartz tap (`_QuartzEventTap`) imports PyObjC lazily — exactly like
`_AVFoundationBackend` in dan/voice/player.py — so hermetic tests inject a
fake tap and never touch Quartz, Accessibility, or a runloop thread. Missing
Accessibility permission is a *health* fact (`health().accessibility`), never
a crash: dand stays healthy, PTT is visibly unavailable.

Edges are delivered to an in-process callback (DaemonApp's PTT controller);
this module never sends HTTP back into the daemon that hosts it.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dan.input.hotkey import (
    DEVICE_MODIFIER_MASK,
    HotkeyEdgeDetector,
    accessibility_trust_state,
)
from dan.logging import get_logger

logger = get_logger(__name__)


class HotkeyMonitorError(Exception):
    """Raised when the global hotkey monitor cannot be started."""


class SingleOwnerError(HotkeyMonitorError):
    """Raised when another process/monitor already owns the hotkey lock."""


@dataclass(frozen=True)
class HotkeyHealth:
    """Observable hotkey state: is the tap alive, is the process trusted."""

    running: bool
    accessibility: str  # "trusted" | "untrusted" | "unknown"


class HotkeyOwnerLock:
    """Exclusive flock on a runtime file: exactly one hotkey owner at a time.

    flock is per open-file-description, so even a second monitor inside the
    same process is rejected. The lock dies with the fd — a crashed owner
    never leaves a stale lock behind.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise SingleOwnerError(
                f"another hotkey owner already holds {self._path}"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._fd = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class MacOSHotkeyMonitor:
    """One global flagsChanged tap feeding the in-process PTT controller."""

    def __init__(
        self,
        *,
        lock_path: Path | str,
        required_mask: int = 0,
        on_edge: Callable[[str], None] | None = None,
        tap_factory: Callable[[], Any] | None = None,
        trusted_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._owner_lock = HotkeyOwnerLock(lock_path)
        self._detector = HotkeyEdgeDetector(required_mask)
        self._on_edge = on_edge or (lambda edge: None)
        self._tap_factory = tap_factory or _QuartzEventTap
        self._trusted_checker = trusted_checker
        self._tap: Any = None

    def start(self) -> None:
        """Owner lock first, tap second: a loser never half-installs a tap."""

        if self._tap is not None:
            return
        self._owner_lock.acquire()
        try:
            tap = self._tap_factory()
            tap.start(self.handle_flags_changed)
        except BaseException:
            self._owner_lock.release()
            raise
        self._tap = tap

    def stop(self) -> None:
        tap, self._tap = self._tap, None
        if tap is None:
            return
        try:
            tap.stop()
        finally:
            self._owner_lock.release()

    @property
    def running(self) -> bool:
        return bool(self._tap is not None and getattr(self._tap, "running", False))

    def handle_flags_changed(self, flags: int) -> None:
        """Feed one flagsChanged snapshot through the edge detector.

        An edge callback that raises must never kill the tap thread — the
        error is logged and the tap keeps observing.
        """

        edge = self._detector.update(int(flags) & DEVICE_MODIFIER_MASK)
        if edge is None:
            return
        try:
            self._on_edge(edge)
        except Exception:  # noqa: BLE001 - the tap must outlive one bad edge
            logger.exception("Hotkey edge handler failed for %r.", edge)

    def health(self) -> HotkeyHealth:
        return HotkeyHealth(
            running=self.running,
            accessibility=accessibility_trust_state(checker=self._trusted_checker),
        )


class _QuartzEventTap:
    """Real CGEventTap + CFRunLoop source in a named daemon thread.

    PyObjC (Quartz) is imported lazily inside start() so importing this module
    — and every test that injects a fake tap — works without the framework.
    """

    def __init__(self) -> None:
        self.running = False
        self._thread: threading.Thread | None = None
        self._loop: Any = None
        self._tap: Any = None
        self._quartz: Any = None

    def start(self, on_flags_changed: Callable[[int], None]) -> None:
        try:
            import Quartz  # noqa: PLC0415 - deliberate lazy PyObjC import
        except ImportError as exc:
            raise HotkeyMonitorError(
                "global hotkey requires pyobjc-framework-Quartz"
            ) from exc
        self._quartz = Quartz

        def _callback(proxy: Any, event_type: Any, event: Any, refcon: Any) -> Any:
            try:
                on_flags_changed(int(Quartz.CGEventGetFlags(event)))
            except Exception:  # noqa: BLE001 - never raise across the C boundary
                logger.exception("flagsChanged tap callback failed.")
            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
            _callback,
            None,
        )
        if tap is None:
            # Typically missing Accessibility permission for this executable.
            raise HotkeyMonitorError(
                "could not create the CGEventTap (is the dand executable "
                "trusted for Accessibility in System Settings?)"
            )
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        started = threading.Event()

        def _run() -> None:
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._loop, source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(tap, True)
            self.running = True
            started.set()
            Quartz.CFRunLoopRun()
            self.running = False

        self._thread = threading.Thread(
            target=_run, name="dan-hotkey-tap", daemon=True
        )
        self._thread.start()
        if not started.wait(timeout=5.0):
            raise HotkeyMonitorError("hotkey tap runloop did not start in 5s")

    def stop(self) -> None:
        quartz, loop, thread = self._quartz, self._loop, self._thread
        if quartz is not None and self._tap is not None:
            try:
                quartz.CGEventTapEnable(self._tap, False)
            except Exception:  # noqa: BLE001 - best-effort disable
                logger.exception("CGEventTap disable failed.")
        if quartz is not None and loop is not None:
            quartz.CFRunLoopStop(loop)
        if thread is not None:
            thread.join(timeout=5.0)
        self.running = False
        self._thread = None
        self._loop = None
        self._tap = None


__all__ = [
    "HotkeyHealth",
    "HotkeyMonitorError",
    "HotkeyOwnerLock",
    "MacOSHotkeyMonitor",
    "SingleOwnerError",
]
