"""Supervised child processes owned by dand (Release 1, Task 9).

Only dand starts `supertonic serve`; the ChildSupervisor is the one place that
spawns, health-probes and reaps those children. Contracts:

- ensure_running() is idempotent: a live child is returned as-is, never
  respawned (one pid, one start).
- Before any spawn the configured health URL is probed: an answering server
  that is NOT our child is a foreign owner — a loud error, never adopted and
  never killed.
- stop() terminates the whole process group (children are spawned with
  start_new_session=True, so pgid == pid) and escalates SIGTERM -> SIGKILL.

Every OS touchpoint (process factory, health probe, killpg, sleep) is
injectable so tests run without a real process or port.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from dan.logging import get_logger

logger = get_logger(__name__)


class ChildSupervisorError(Exception):
    """Raised when a supervised child cannot be started or stopped safely."""


class ForeignPortOwnerError(ChildSupervisorError):
    """The configured port answers but the server is not our child."""


class UnknownChildError(ChildSupervisorError):
    """Raised when a child name has no registered ChildSpec."""


@dataclass(frozen=True)
class ChildSpec:
    name: str
    argv: tuple[str, ...]
    health_url: str
    restart_limit: int = 3
    backoff_seconds: tuple[float, ...] = (0.5, 1.0, 2.0)


@dataclass
class ChildHandle:
    spec: ChildSpec
    process: Any

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def alive(self) -> bool:
        return self.process.poll() is None

    def status(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "pid": self.pid,
            "alive": self.alive(),
            "health_url": self.spec.health_url,
        }


def _default_process_factory(spec: ChildSpec) -> subprocess.Popen[bytes]:
    # start_new_session=True puts the child in its own process group so
    # stop() can killpg the whole tree (a serve that forks workers included).
    return subprocess.Popen(  # noqa: S603 - argv comes from daemon config
        list(spec.argv),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _default_health_probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - localhost
            return 200 <= int(response.status) < 300
    except Exception:  # noqa: BLE001 - any failure means "not healthy"
        return False


def _default_killpg(pgid: int, signum: int) -> None:
    os.killpg(pgid, signum)


class ChildSupervisor:
    def __init__(
        self,
        specs: Iterable[ChildSpec] = (),
        *,
        process_factory: Callable[[ChildSpec], Any] | None = None,
        health_probe: Callable[[str], bool] | None = None,
        killpg: Callable[[int, int], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._specs: dict[str, ChildSpec] = {spec.name: spec for spec in specs}
        self._children: dict[str, ChildHandle] = {}
        self._process_factory = process_factory or _default_process_factory
        self._health_probe = health_probe or _default_health_probe
        self._killpg = killpg or _default_killpg
        self._sleep = sleep

    def register(self, spec: ChildSpec) -> None:
        self._specs[spec.name] = spec

    def ensure_running(self, name: str) -> ChildHandle:
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownChildError(f"no ChildSpec registered for {name!r}")
        child = self._children.get(name)
        if child is not None and child.alive():
            return child
        if child is not None:
            logger.warning(
                "Supervised child %s (pid %s) died; respawning.", name, child.pid
            )
            self._children.pop(name, None)
        # Foreign-owner gate BEFORE spawn: an answering server that is not our
        # child means someone else runs it — refuse loudly, do not adopt, do
        # not kill.
        if spec.health_url and self._health_probe(spec.health_url):
            raise ForeignPortOwnerError(
                f"{name}: {spec.health_url} already answers but the server is "
                "not a dand child; refusing to adopt or kill it. Stop the "
                "foreign process (or change the configured port) first."
            )
        return self._spawn_and_probe(spec)

    def stop(self, name: str, timeout: float = 5.0) -> None:
        child = self._children.pop(name, None)
        if child is None:
            return
        self._terminate_process_group(child, timeout)

    def stop_all(self, timeout: float = 5.0) -> None:
        for name in list(self._children):
            self.stop(name, timeout)

    def child_pids(self) -> list[int]:
        return [child.pid for child in self._children.values() if child.alive()]

    def status(self) -> Mapping[str, dict[str, Any]]:
        return {name: child.status() for name, child in self._children.items()}

    # -- internals --------------------------------------------------------

    def _spawn_and_probe(self, spec: ChildSpec) -> ChildHandle:
        process = self._process_factory(spec)
        child = ChildHandle(spec=spec, process=process)
        self._children[spec.name] = child
        if not spec.health_url:
            return child
        delays = (0.0, *spec.backoff_seconds) or (0.0,)
        for delay in delays:
            if delay:
                self._sleep(delay)
            if not child.alive():
                self._children.pop(spec.name, None)
                raise ChildSupervisorError(
                    f"{spec.name} exited during startup "
                    f"(rc={child.process.poll()!r})"
                )
            if self._health_probe(spec.health_url):
                logger.info(
                    "Supervised child %s healthy (pid %s).", spec.name, child.pid
                )
                return child
        # Never healthy: reap the spawn instead of leaking a half-up child.
        self._children.pop(spec.name, None)
        self._terminate_process_group(child, timeout=5.0)
        raise ChildSupervisorError(
            f"{spec.name} did not become healthy at {spec.health_url} "
            f"after {len(delays)} probes"
        )

    def _terminate_process_group(self, child: ChildHandle, timeout: float) -> None:
        pgid = child.pid  # start_new_session=True -> pgid == pid
        try:
            self._killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if self._wait_for_exit(child, timeout):
            return
        logger.warning(
            "Supervised child %s ignored SIGTERM; escalating to SIGKILL.",
            child.spec.name,
        )
        try:
            self._killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        if not self._wait_for_exit(child, timeout):
            logger.error(
                "Supervised child %s (pid %s) survived SIGKILL wait.",
                child.spec.name,
                child.pid,
            )

    def _wait_for_exit(self, child: ChildHandle, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while child.alive():
            if time.monotonic() >= deadline:
                return False
            self._sleep(0.05)
        return True


__all__ = [
    "ChildHandle",
    "ChildSpec",
    "ChildSupervisor",
    "ChildSupervisorError",
    "ForeignPortOwnerError",
    "UnknownChildError",
]
