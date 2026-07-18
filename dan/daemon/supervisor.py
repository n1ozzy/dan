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
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

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


@dataclass(frozen=True)
class ChildStatus:
    name: str
    state: Literal["stopped", "starting", "running", "degraded"]
    pid: int | None
    alive: bool
    restart_count: int
    restart_limit: int
    last_exit_code: int | None
    last_error: str | None
    degraded: bool
    health_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "pid": self.pid,
            "restart_count": self.restart_count,
            "restart_limit": self.restart_limit,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "degraded": self.degraded,
            "health_url": self.health_url,
            "alive": self.alive,
        }


@dataclass(frozen=True)
class ChildContainmentResult:
    watchdog_joined: bool
    children_reaped: bool
    process_groups_released: bool
    listeners_released: bool
    remaining_pids: tuple[int, ...]
    errors: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return (
            self.watchdog_joined
            and self.children_reaped
            and self.process_groups_released
            and self.listeners_released
            and not self.remaining_pids
        )


@dataclass(frozen=True)
class _ProcessContainmentResult:
    parent_reaped: bool
    process_group_released: bool

    @property
    def complete(self) -> bool:
        return self.parent_reaped and self.process_group_released


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


def _default_process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _default_listener_released(url: str) -> bool:
    """Prove release by binding every address represented by the health URL."""

    parsed = urlsplit(url)
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return False
    if not host or port is None:
        return False
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not addresses:
        return False
    for family, socktype, protocol, _canonname, sockaddr in addresses:
        probe = socket.socket(family, socktype, protocol)
        try:
            # macOS keeps the server-side tuple in TIME_WAIT after health probes;
            # reuse distinguishes that kernel residue from an active listener.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(sockaddr)
        except OSError:
            return False
        finally:
            probe.close()
    return True


class ChildSupervisor:
    def __init__(
        self,
        specs: Iterable[ChildSpec] = (),
        *,
        process_factory: Callable[[ChildSpec], Any] | None = None,
        health_probe: Callable[[str], bool] | None = None,
        killpg: Callable[[int, int], None] | None = None,
        process_group_alive: Callable[[int], bool] | None = None,
        listener_released_probe: Callable[[str], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        watchdog_poll_interval: float = 0.5,
        watchdog_waiter: Callable[[threading.Event, float], bool] | None = None,
    ) -> None:
        self._specs: dict[str, ChildSpec] = {spec.name: spec for spec in specs}
        self._children: dict[str, ChildHandle] = {}
        self._unresolved_listeners: set[str] = set()
        self._process_factory = process_factory or _default_process_factory
        self._health_probe = health_probe or _default_health_probe
        self._killpg = killpg or _default_killpg
        self._process_group_alive = (
            process_group_alive or _default_process_group_alive
        )
        self._listener_released_probe = (
            listener_released_probe or _default_listener_released
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._watchdog_poll_interval = max(0.01, float(watchdog_poll_interval))
        self._watchdog_waiter = watchdog_waiter or (
            lambda stop, timeout: stop.wait(timeout=timeout)
        )
        self._lock = threading.RLock()
        self._state: Literal["running", "stopping", "stopped"] = "running"
        self._restart_counts: dict[str, int] = {
            spec.name: 0 for spec in specs
        }
        self._last_exit_codes: dict[str, int | None] = {
            spec.name: None for spec in specs
        }
        self._last_errors: dict[str, str | None] = {
            spec.name: None for spec in specs
        }
        self._degraded: set[str] = set()
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    def register(self, spec: ChildSpec) -> None:
        with self._lock:
            self._specs[spec.name] = spec
            self._restart_counts.setdefault(spec.name, 0)
            self._last_exit_codes.setdefault(spec.name, None)
            self._last_errors.setdefault(spec.name, None)

    def ensure_running(self, name: str) -> ChildHandle:
        with self._lock:
            if self._state == "stopping":
                raise ChildSupervisorError(
                    "child supervisor is stopping; refusing to spawn"
                )
            if self._state == "stopped":
                self._state = "running"
                self._restart_counts[name] = 0
                self._degraded.discard(name)
            spec = self._specs.get(name)
            if spec is None:
                raise UnknownChildError(f"no ChildSpec registered for {name!r}")
            child = self._children.get(name)
            if child is not None and child.alive():
                return child
            if child is not None:
                return self._restart_dead_child_locked(spec, child)
            if name in self._degraded:
                raise ChildSupervisorError(
                    f"{name} exhausted its restart budget and is degraded"
                )
            self._reject_foreign_owner_locked(spec)
            return self._spawn_and_probe_locked(spec)

    def start_watchdog(self) -> None:
        with self._lock:
            if self._state == "stopping":
                raise ChildSupervisorError(
                    "child supervisor is stopping; watchdog cannot start"
                )
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                return
            if self._state == "stopped":
                self._state = "running"
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_run,
                name="dan-child-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def stop_watchdog(self, timeout: float = 5.0) -> bool:
        # Event.set and the thread reference are safe without the lifecycle
        # lock. This must happen before attempting that lock: a watchdog may
        # be inside an injected health probe while holding it.
        self._watchdog_stop.set()
        thread = self._watchdog_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        joined = thread is None or not thread.is_alive()
        if joined:
            with self._lock:
                if self._watchdog_thread is thread:
                    self._watchdog_thread = None
        return joined

    @property
    def watchdog_alive(self) -> bool:
        with self._lock:
            return (
                self._watchdog_thread is not None
                and self._watchdog_thread.is_alive()
            )

    def stop(self, name: str, timeout: float = 5.0) -> ChildContainmentResult:
        with self._lock:
            child = self._children.get(name)
            if child is None:
                if name in self._unresolved_listeners:
                    spec = self._specs.get(name)
                    if spec is None:
                        raise UnknownChildError(f"no ChildSpec registered for {name!r}")
                    listener_released = self._listener_released(spec)
                    if listener_released:
                        self._unresolved_listeners.discard(name)
                    errors = (
                        ()
                        if listener_released
                        else (f"{name} listener still answers at {spec.health_url}",)
                    )
                    return ChildContainmentResult(
                        True,
                        True,
                        True,
                        listener_released,
                        (),
                        errors,
                    )
                return ChildContainmentResult(True, True, True, True, (), ())
            process_result = self._terminate_process_group(child, timeout)
            listener_released = self._listener_released(child.spec)
            self._retire_released_process(
                name,
                child,
                process_result=process_result,
                listener_released=listener_released,
            )
            remaining = (child.pid,) if name in self._children else ()
            errors = self._containment_errors(
                child,
                parent_reaped=process_result.parent_reaped,
                process_group_released=process_result.process_group_released,
                listener_released=listener_released,
            )
            return ChildContainmentResult(
                watchdog_joined=True,
                children_reaped=process_result.parent_reaped,
                process_groups_released=process_result.process_group_released,
                listeners_released=listener_released,
                remaining_pids=remaining,
                errors=errors,
            )

    def stop_all(self, timeout: float = 5.0) -> ChildContainmentResult:
        timeout = max(0.0, float(timeout))
        deadline = self._monotonic() + timeout
        self._watchdog_stop.set()
        if not self._lock.acquire(
            timeout=max(0.0, deadline - self._monotonic())
        ):
            return self._incomplete_stop_result(
                "child lifecycle lock remained owned by the watchdog"
            )
        try:
            self._state = "stopping"
        finally:
            self._lock.release()
        watchdog_joined = self.stop_watchdog(
            timeout=max(0.0, deadline - self._monotonic())
        )
        if not watchdog_joined:
            return self._incomplete_stop_result("child watchdog did not join")
        children_reaped = True
        process_groups_released = True
        listeners_released = True
        errors: list[str] = []
        with self._lock:
            pending_listeners = tuple(self._unresolved_listeners)
            for name, child in tuple(self._children.items()):
                process_result = self._terminate_process_group(child, timeout)
                listener_released = self._listener_released(child.spec)
                children_reaped = (
                    children_reaped and process_result.parent_reaped
                )
                process_groups_released = (
                    process_groups_released
                    and process_result.process_group_released
                )
                listeners_released = listeners_released and listener_released
                errors.extend(
                    self._containment_errors(
                        child,
                        parent_reaped=process_result.parent_reaped,
                        process_group_released=(
                            process_result.process_group_released
                        ),
                        listener_released=listener_released,
                    )
                )
                self._retire_released_process(
                    name,
                    child,
                    process_result=process_result,
                    listener_released=listener_released,
                )
            for name in pending_listeners:
                spec = self._specs.get(name)
                listener_released = spec is not None and self._listener_released(spec)
                listeners_released = listeners_released and listener_released
                if listener_released:
                    self._unresolved_listeners.discard(name)
                elif spec is not None:
                    errors.append(
                        f"{name} listener still answers at {spec.health_url}"
                    )
            remaining_pids = tuple(
                sorted(child.pid for child in self._children.values())
            )
            self._state = "stopped"
        if not watchdog_joined:
            errors.append("child watchdog did not join")
        return ChildContainmentResult(
            watchdog_joined=watchdog_joined,
            children_reaped=children_reaped,
            process_groups_released=process_groups_released,
            listeners_released=listeners_released,
            remaining_pids=remaining_pids,
            errors=tuple(errors),
        )

    def _incomplete_stop_result(self, error: str) -> ChildContainmentResult:
        try:
            remaining_pids = tuple(
                sorted(child.pid for child in tuple(self._children.values()))
            )
        except RuntimeError:
            remaining_pids = ()
        return ChildContainmentResult(
            watchdog_joined=False,
            children_reaped=False,
            process_groups_released=False,
            listeners_released=False,
            remaining_pids=remaining_pids,
            errors=(error,),
        )

    def child_pids(self) -> list[int]:
        with self._lock:
            return [
                child.pid
                for child in self._children.values()
                if child.alive() or not self._process_group_released(child.pid)
            ]

    def status(
        self,
        name: str | None = None,
    ) -> ChildStatus | Mapping[str, dict[str, Any]]:
        with self._lock:
            if name is not None:
                if name not in self._specs:
                    raise UnknownChildError(f"no ChildSpec registered for {name!r}")
                return self._status_locked(name)
            return {
                child_name: self._status_locked(child_name).to_dict()
                for child_name in self._specs
                if child_name in self._children
                or child_name in self._unresolved_listeners
                or child_name in self._degraded
                or self._restart_counts.get(child_name, 0)
            }

    # -- internals --------------------------------------------------------

    def _retire_released_process(
        self,
        name: str,
        child: ChildHandle,
        *,
        process_result: _ProcessContainmentResult,
        listener_released: bool,
    ) -> None:
        if not process_result.complete:
            return
        # A released process group must never remain addressable by numeric PID;
        # the kernel may reuse it before a listener-only containment retry.
        if self._children.get(name) is child:
            self._children.pop(name, None)
        if child.spec.health_url and not listener_released:
            self._unresolved_listeners.add(name)
        else:
            self._unresolved_listeners.discard(name)

    def _watchdog_run(self) -> None:
        while not self._watchdog_waiter(
            self._watchdog_stop,
            self._watchdog_poll_interval,
        ):
            try:
                self._watchdog_tick()
            except Exception:
                logger.exception("Child supervisor watchdog tick failed.")

    def _watchdog_tick(self) -> None:
        with self._lock:
            if self._state != "running":
                return
            for name, child in tuple(self._children.items()):
                if child.alive():
                    continue
                spec = self._specs[name]
                try:
                    self._restart_dead_child_locked(spec, child)
                except ChildSupervisorError as exc:
                    self._last_errors[name] = str(exc)
                    self._degraded.add(name)
                    logger.error("Supervised child %s degraded: %s", name, exc)

    def _restart_dead_child_locked(
        self,
        spec: ChildSpec,
        child: ChildHandle,
    ) -> ChildHandle:
        exit_code = child.process.poll()
        self._last_exit_codes[spec.name] = (
            int(exit_code) if exit_code is not None else None
        )
        parent_reaped = self._reap_process(child, timeout=0.0)
        process_group_released = self._process_group_released(child.pid)
        listener_released = self._listener_released(spec)
        process_result = _ProcessContainmentResult(
            parent_reaped=parent_reaped,
            process_group_released=process_group_released,
        )
        self._retire_released_process(
            spec.name,
            child,
            process_result=process_result,
            listener_released=listener_released,
        )
        if (
            not parent_reaped
            or not process_group_released
            or not listener_released
        ):
            details = self._containment_errors(
                child,
                parent_reaped=parent_reaped,
                process_group_released=process_group_released,
                listener_released=listener_released,
            )
            error = "; ".join(details)
            self._last_errors[spec.name] = error
            self._degraded.add(spec.name)
            raise ChildSupervisorError(error)
        restart_count = self._restart_counts.get(spec.name, 0)
        if restart_count >= spec.restart_limit:
            self._degraded.add(spec.name)
            raise ChildSupervisorError(
                f"{spec.name} exhausted restart limit {spec.restart_limit}"
            )
        self._restart_counts[spec.name] = restart_count + 1
        logger.warning(
            "Supervised child %s (pid %s) died; restart %s/%s.",
            spec.name,
            child.pid,
            restart_count + 1,
            spec.restart_limit,
        )
        self._reject_foreign_owner_locked(spec)
        return self._spawn_and_probe_locked(spec)

    def _reject_foreign_owner_locked(self, spec: ChildSpec) -> None:
        if not spec.health_url:
            self._unresolved_listeners.discard(spec.name)
            return
        if self._health_probe(spec.health_url) or not self._listener_released(spec):
            raise ForeignPortOwnerError(
                f"{spec.name}: {spec.health_url} already answers but the server is "
                "not a dand child; refusing to adopt or kill it. Stop the "
                "foreign process (or change the configured port) first."
            )
        self._unresolved_listeners.discard(spec.name)

    def _spawn_and_probe_locked(self, spec: ChildSpec) -> ChildHandle:
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
                exit_code = child.process.poll()
                self._last_exit_codes[spec.name] = (
                    int(exit_code) if exit_code is not None else None
                )
                parent_reaped = self._reap_process(child, timeout=0.0)
                process_group_released = self._process_group_released(child.pid)
                listener_released = self._listener_released(spec)
                process_result = _ProcessContainmentResult(
                    parent_reaped=parent_reaped,
                    process_group_released=process_group_released,
                )
                self._retire_released_process(
                    spec.name,
                    child,
                    process_result=process_result,
                    listener_released=listener_released,
                )
                if not process_result.complete or not listener_released:
                    details = self._containment_errors(
                        child,
                        parent_reaped=parent_reaped,
                        process_group_released=process_group_released,
                        listener_released=listener_released,
                    )
                    self._last_errors[spec.name] = "; ".join(details)
                    self._degraded.add(spec.name)
                raise ChildSupervisorError(
                    f"{spec.name} exited during startup "
                    f"(rc={child.process.poll()!r}); "
                    f"containment={self._last_errors.get(spec.name)!r}"
                )
            if self._health_probe(spec.health_url):
                logger.info(
                    "Supervised child %s healthy (pid %s).", spec.name, child.pid
                )
                return child
        # Never healthy: reap the spawn instead of leaking a half-up child.
        process_result = self._terminate_process_group(child, timeout=5.0)
        listener_released = self._listener_released(spec)
        self._retire_released_process(
            spec.name,
            child,
            process_result=process_result,
            listener_released=listener_released,
        )
        if not process_result.complete or not listener_released:
            details = self._containment_errors(
                child,
                parent_reaped=process_result.parent_reaped,
                process_group_released=process_result.process_group_released,
                listener_released=listener_released,
            )
            self._last_errors[spec.name] = "; ".join(details)
            self._degraded.add(spec.name)
        raise ChildSupervisorError(
            f"{spec.name} did not become healthy at {spec.health_url} "
            f"after {len(delays)} probes"
        )

    def _terminate_process_group(
        self,
        child: ChildHandle,
        timeout: float,
    ) -> _ProcessContainmentResult:
        pgid = child.pid  # start_new_session=True -> pgid == pid
        try:
            self._killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        term_deadline = self._monotonic() + max(0.0, float(timeout))
        parent_reaped = self._wait_for_exit(
            child,
            max(0.0, term_deadline - self._monotonic()),
        )
        process_group_released = (
            self._wait_for_process_group_release(
                pgid,
                max(0.0, term_deadline - self._monotonic()),
            )
            if parent_reaped
            else self._process_group_released(pgid)
        )
        if parent_reaped and process_group_released:
            return _ProcessContainmentResult(True, True)
        logger.warning(
            "Supervised child group %s survived SIGTERM; escalating to SIGKILL.",
            child.spec.name,
        )
        try:
            self._killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kill_deadline = self._monotonic() + max(0.0, float(timeout))
        if not parent_reaped:
            parent_reaped = self._wait_for_exit(
                child,
                max(0.0, kill_deadline - self._monotonic()),
            )
        process_group_released = (
            self._wait_for_process_group_release(
                pgid,
                max(0.0, kill_deadline - self._monotonic()),
            )
            if parent_reaped
            else self._process_group_released(pgid)
        )
        if not parent_reaped or not process_group_released:
            logger.error(
                "Supervised child %s (pgid %s) survived SIGKILL containment.",
                child.spec.name,
                child.pid,
            )
        return _ProcessContainmentResult(
            parent_reaped=parent_reaped,
            process_group_released=process_group_released,
        )

    def _wait_for_exit(self, child: ChildHandle, timeout: float) -> bool:
        deadline = self._monotonic() + max(0.0, float(timeout))
        while True:
            returncode = child.process.poll()
            if returncode is not None:
                return self._reap_process(child, timeout=0.0)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            wait = getattr(child.process, "wait", None)
            if callable(wait):
                try:
                    waited = wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    return False
                except ChildProcessError:
                    return True
                if waited is not None or child.process.poll() is not None:
                    return True
            else:
                self._sleep(min(0.05, remaining))

    @staticmethod
    def _reap_process(child: ChildHandle, timeout: float) -> bool:
        wait = getattr(child.process, "wait", None)
        if not callable(wait):
            return child.process.poll() is not None
        try:
            waited = wait(timeout=max(0.0, float(timeout)))
        except subprocess.TimeoutExpired:
            return False
        except ChildProcessError:
            return True
        return waited is not None or child.process.poll() is not None

    def _wait_for_process_group_release(self, pgid: int, timeout: float) -> bool:
        deadline = self._monotonic() + max(0.0, float(timeout))
        while True:
            if self._process_group_released(pgid):
                return True
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(0.05, remaining))

    def _process_group_released(self, pgid: int) -> bool:
        try:
            return not self._process_group_alive(pgid)
        except Exception as exc:
            logger.error(
                "Process-group liveness probe failed for pgid %s: %s",
                pgid,
                exc,
            )
            return False

    def _listener_released(self, spec: ChildSpec) -> bool:
        if not spec.health_url:
            return True
        try:
            return bool(self._listener_released_probe(spec.health_url))
        except Exception as exc:
            self._last_errors[spec.name] = f"listener probe failed: {exc}"
            return False

    @staticmethod
    def _containment_errors(
        child: ChildHandle,
        *,
        parent_reaped: bool,
        process_group_released: bool,
        listener_released: bool,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if not parent_reaped:
            errors.append(f"{child.spec.name} pid {child.pid} was not reaped")
        if not process_group_released:
            errors.append(
                f"{child.spec.name} process group {child.pid} is still alive"
            )
        if not listener_released:
            errors.append(
                f"{child.spec.name} listener still answers at {child.spec.health_url}"
            )
        return tuple(errors)

    def _status_locked(self, name: str) -> ChildStatus:
        spec = self._specs[name]
        child = self._children.get(name)
        degraded = name in self._degraded or name in self._unresolved_listeners
        alive = child is not None and child.alive()
        state: Literal["stopped", "starting", "running", "degraded"]
        if degraded:
            state = "degraded"
        elif alive:
            state = "running"
        else:
            state = "stopped"
        return ChildStatus(
            name=name,
            state=state,
            pid=child.pid if alive and child is not None else None,
            alive=alive,
            restart_count=self._restart_counts.get(name, 0),
            restart_limit=spec.restart_limit,
            last_exit_code=self._last_exit_codes.get(name),
            last_error=self._last_errors.get(name),
            degraded=degraded,
            health_url=spec.health_url,
        )


__all__ = [
    "ChildContainmentResult",
    "ChildHandle",
    "ChildSpec",
    "ChildStatus",
    "ChildSupervisor",
    "ChildSupervisorError",
    "ForeignPortOwnerError",
    "UnknownChildError",
]
