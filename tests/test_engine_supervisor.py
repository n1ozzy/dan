"""ChildSupervisor: dand is the only owner of `supertonic serve` (Task 9).

Process and signal interactions use injected fakes. Loopback sockets exercise
listener ownership without spawning a real child or signalling a live pid.
"""

from __future__ import annotations

import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from dan.daemon.supervisor import (
    ChildHandle,
    ChildSpec,
    ChildSupervisor,
    ChildSupervisorError,
    ForeignPortOwnerError,
    _default_listener_released,
)

SUPERTONIC_SPEC = ChildSpec(
    name="supertonic",
    argv=("supertonic", "serve", "--model", "supertonic-3", "--port", "7797"),
    health_url="http://127.0.0.1:7797/v1/health",
    restart_limit=3,
    backoff_seconds=(0.0, 0.0, 0.0),
)


def test_listener_release_probe_ignores_time_wait_after_server_close() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _address = listener.accept()
    accepted.shutdown(socket.SHUT_WR)
    accepted.close()
    client.recv(1)
    client.close()
    listener.close()

    assert _default_listener_released(f"http://127.0.0.1:{port}/health") is True


def test_listener_release_probe_rejects_live_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert (
            _default_listener_released(f"http://127.0.0.1:{port}/health")
            is False
        )
    finally:
        listener.close()


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        self.wait_calls += 1
        return self.returncode


class FakeProcessFactory:
    def __init__(self) -> None:
        self.starts = 0
        self.processes: list[FakeProcess] = []

    def __call__(self, spec: ChildSpec) -> FakeProcess:
        self.starts += 1
        process = FakeProcess(pid=4000 + self.starts)
        self.processes.append(process)
        return process


class KillpgRecorder:
    """Records process-group kills and marks the matching fake pid dead."""

    def __init__(self, factory: FakeProcessFactory) -> None:
        self._factory = factory
        self.calls: list[tuple[int, int]] = []

    def __call__(self, pgid: int, signum: int) -> None:
        self.calls.append((pgid, signum))
        for process in self._factory.processes:
            if process.pid == pgid:
                process.returncode = -signum


def build_supervisor(
    *,
    factory: FakeProcessFactory | None = None,
    healthy_when_spawned: bool = True,
    foreign_owner: bool = False,
):
    factory = factory or FakeProcessFactory()
    killpg = KillpgRecorder(factory)

    def probe(url: str) -> bool:
        if foreign_owner:
            return True
        return healthy_when_spawned and any(
            process.returncode is None for process in factory.processes
        )

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=factory,
        health_probe=probe,
        killpg=killpg,
        process_group_alive=lambda pgid: any(
            process.pid == pgid and process.returncode is None
            for process in factory.processes
        ),
        listener_released_probe=lambda _url: (
            not foreign_owner
            and not any(
                process.returncode is None for process in factory.processes
            )
        ),
        sleep=lambda seconds: None,
    )
    return supervisor, factory, killpg


def test_supertonic_is_one_supervised_child() -> None:
    supervisor, factory, _killpg = build_supervisor()

    first = supervisor.ensure_running("supertonic")
    second = supervisor.ensure_running("supertonic")

    assert first.pid == second.pid
    assert factory.starts == 1


def test_foreign_port_owner_is_rejected_not_adopted_or_killed() -> None:
    supervisor, factory, killpg = build_supervisor(foreign_owner=True)

    with pytest.raises(ForeignPortOwnerError):
        supervisor.ensure_running("supertonic")

    assert factory.starts == 0  # never spawned over the stranger
    assert killpg.calls == []  # and never killed it either


def test_stop_terminates_the_whole_process_group() -> None:
    supervisor, factory, killpg = build_supervisor()
    child = supervisor.ensure_running("supertonic")

    supervisor.stop("supertonic")

    assert killpg.calls[0] == (child.pid, signal.SIGTERM)
    assert factory.processes[0].wait_calls >= 1
    assert supervisor.child_pids() == []


def test_stop_all_proves_watchdog_join_reap_and_listener_release() -> None:
    supervisor, factory, killpg = build_supervisor()
    child = supervisor.ensure_running("supertonic")
    supervisor.start_watchdog()

    result = supervisor.stop_all(timeout=0.1)

    assert result.complete is True
    assert result.watchdog_joined is True
    assert result.children_reaped is True
    assert result.listeners_released is True
    assert result.remaining_pids == ()
    assert killpg.calls[0] == (child.pid, signal.SIGTERM)
    assert factory.processes[0].wait_calls >= 1
    assert supervisor.watchdog_alive is False


def test_watchdog_restarts_dead_child_without_ensure_running() -> None:
    supervisor, factory, _killpg = build_supervisor()
    supervisor._watchdog_poll_interval = 0.01
    first = supervisor.ensure_running("supertonic")
    supervisor.start_watchdog()
    first.process.returncode = 1

    deadline = time.monotonic() + 2
    while factory.starts < 2 and time.monotonic() < deadline:
        time.sleep(0.005)

    try:
        assert factory.starts == 2
        status = supervisor.status("supertonic")
        assert status.restart_count == 1
        assert status.state == "running"
    finally:
        supervisor.stop_all(timeout=0.1)


def test_stop_watchdog_joins_before_child_killpg() -> None:
    factory = FakeProcessFactory()
    order: list[str] = []
    supervisor = None

    def killpg(pgid: int, signum: int) -> None:
        assert supervisor is not None
        assert supervisor.watchdog_alive is False
        order.append("children.killpg")
        factory.processes[0].returncode = -signum

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=factory,
        health_probe=lambda _url: any(
            process.returncode is None for process in factory.processes
        ),
        killpg=killpg,
        process_group_alive=lambda pgid: any(
            process.pid == pgid and process.returncode is None
            for process in factory.processes
        ),
        listener_released_probe=lambda _url: not any(
            process.returncode is None for process in factory.processes
        ),
        sleep=lambda _seconds: None,
    )
    supervisor.ensure_running("supertonic")
    supervisor.start_watchdog()

    result = supervisor.stop_all(timeout=0.1)

    assert result.watchdog_joined is True
    assert order == ["children.killpg"]


def test_stop_all_returns_incomplete_when_watchdog_holds_lifecycle_lock() -> None:
    dead_parent = FakeProcess(pid=4666)
    dead_parent.returncode = 1
    replacement = FakeProcess(pid=4667)
    probe_entered = threading.Event()
    release_probe = threading.Event()

    def blocking_health_probe(_url: str) -> bool:
        probe_entered.set()
        release_probe.wait(timeout=2)
        return False

    def killpg(pgid: int, signum: int) -> None:
        if pgid == replacement.pid:
            replacement.returncode = -signum

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: replacement,
        health_probe=blocking_health_probe,
        killpg=killpg,
        process_group_alive=lambda pgid: (
            pgid == replacement.pid and replacement.returncode is None
        ),
        listener_released_probe=lambda _url: True,
        sleep=lambda _seconds: None,
        watchdog_poll_interval=0.01,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=dead_parent,
    )
    supervisor.start_watchdog()
    assert probe_entered.wait(timeout=1)
    results = []
    finished = threading.Event()

    def stop_all() -> None:
        results.append(supervisor.stop_all(timeout=0.05))
        finished.set()

    stopper = threading.Thread(target=stop_all)
    stopper.start()
    returned_within_bound = finished.wait(timeout=0.2)
    release_probe.set()
    stopper.join(timeout=1)
    supervisor.stop_all(timeout=0.1)

    assert returned_within_bound is True
    assert results[0].complete is False
    assert results[0].watchdog_joined is False


class StubbornProcess(FakeProcess):
    def wait(self, timeout: float | None = None) -> int | None:
        self.wait_calls += 1
        raise subprocess.TimeoutExpired("stubborn", timeout)


def test_dead_but_unreaped_child_is_retained_and_never_respawned() -> None:
    unreaped = StubbornProcess(pid=4777)
    unreaped.returncode = 1

    def forbidden_spawn(_spec: ChildSpec):
        raise AssertionError("must not spawn beside an unreaped child")

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=forbidden_spawn,
        health_probe=lambda _url: False,
        killpg=lambda _pgid, _signum: None,
        process_group_alive=lambda _pgid: False,
        listener_released_probe=lambda _url: True,
        sleep=lambda _seconds: None,
    )
    handle = ChildHandle(spec=SUPERTONIC_SPEC, process=unreaped)
    supervisor._children["supertonic"] = handle

    with pytest.raises(ChildSupervisorError, match="reap"):
        supervisor.ensure_running("supertonic")

    assert supervisor._children["supertonic"] is handle
    assert supervisor.status("supertonic").degraded is True


def test_startup_death_retains_handle_when_reap_cannot_be_proven() -> None:
    unreaped = StubbornProcess(pid=4888)
    unreaped.returncode = 1
    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: unreaped,
        health_probe=lambda _url: False,
        killpg=lambda _pgid, _signum: None,
        process_group_alive=lambda _pgid: False,
        listener_released_probe=lambda _url: True,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ChildSupervisorError):
        supervisor.ensure_running("supertonic")

    assert supervisor._children["supertonic"].process is unreaped


def test_deliberate_stop_never_respawns_from_watchdog() -> None:
    supervisor, factory, _killpg = build_supervisor()
    supervisor._watchdog_poll_interval = 0.01
    supervisor.ensure_running("supertonic")
    supervisor.start_watchdog()

    supervisor.stop("supertonic", timeout=0.1)
    time.sleep(0.04)

    try:
        assert factory.starts == 1
        assert supervisor.status("supertonic").restart_count == 0
    finally:
        supervisor.stop_all(timeout=0.1)


def test_failed_child_reap_or_live_listener_blocks_complete_containment() -> None:
    stubborn = StubbornProcess(pid=4999)
    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: stubborn,
        health_probe=lambda _url: True,
        killpg=lambda _pgid, _signum: None,
        process_group_alive=lambda _pgid: True,
        listener_released_probe=lambda _url: False,
        sleep=lambda _seconds: None,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=stubborn,
    )

    result = supervisor.stop_all(timeout=0.01)

    assert result.complete is False
    assert result.children_reaped is False
    assert result.listeners_released is False
    assert result.remaining_pids == (stubborn.pid,)


def test_degraded_status_still_reports_retained_live_process() -> None:
    live_process = FakeProcess(pid=5050)
    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: live_process,
        health_probe=lambda _url: False,
        killpg=lambda _pgid, _signum: None,
        process_group_alive=lambda _pgid: True,
        listener_released_probe=lambda _url: False,
        sleep=lambda _seconds: None,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=live_process,
    )
    supervisor._degraded.add("supertonic")

    status = supervisor.status("supertonic")

    assert status.degraded is True
    assert status.pid == live_process.pid
    assert status.to_dict()["alive"] is True


def test_parent_exit_after_term_does_not_prove_process_group_is_gone() -> None:
    parent = FakeProcess(pid=5111)
    descendant_alive = True
    signals: list[int] = []

    def killpg(_pgid: int, signum: int) -> None:
        nonlocal descendant_alive
        signals.append(signum)
        if signum == signal.SIGTERM:
            parent.returncode = -signal.SIGTERM
        if signum == signal.SIGKILL:
            descendant_alive = False

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: parent,
        health_probe=lambda _url: False,
        killpg=killpg,
        process_group_alive=lambda _pgid: descendant_alive,
        listener_released_probe=lambda _url: True,
        sleep=lambda _seconds: None,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=parent,
    )

    result = supervisor.stop_all(timeout=0.01)

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert parent.wait_calls >= 1
    assert result.process_groups_released is True
    assert result.complete is True


def test_unhealthy_http_does_not_prove_still_bound_listener_was_released() -> None:
    parent = FakeProcess(pid=5222)

    def killpg(_pgid: int, signum: int) -> None:
        parent.returncode = -signum

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: parent,
        # A bound server returning HTTP 503 is unhealthy, but still owns the port.
        health_probe=lambda _url: False,
        killpg=killpg,
        process_group_alive=lambda _pgid: False,
        listener_released_probe=lambda _url: False,
        sleep=lambda _seconds: None,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=parent,
    )

    result = supervisor.stop_all(timeout=0.01)

    assert result.complete is False
    assert result.children_reaped is True
    assert result.process_groups_released is True
    assert result.listeners_released is False
    assert result.remaining_pids == ()


def test_released_process_group_is_never_signalled_again_for_listener_only_retry() -> None:
    parent = FakeProcess(pid=5333)
    signals: list[tuple[int, int]] = []
    listener_released = False

    def killpg(pgid: int, signum: int) -> None:
        signals.append((pgid, signum))
        parent.returncode = -signum

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: parent,
        health_probe=lambda _url: False,
        killpg=killpg,
        process_group_alive=lambda _pgid: False,
        listener_released_probe=lambda _url: listener_released,
        sleep=lambda _seconds: None,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=parent,
    )

    first = supervisor.stop_all(timeout=0.01)
    unresolved_status = supervisor.status("supertonic")
    listener_released = True
    second = supervisor.stop_all(timeout=0.01)

    assert first.complete is False
    assert first.children_reaped is True
    assert first.process_groups_released is True
    assert first.listeners_released is False
    assert first.remaining_pids == ()
    assert unresolved_status.degraded is True
    assert unresolved_status.pid is None
    assert unresolved_status.alive is False
    assert second.complete is True
    assert signals == [(parent.pid, signal.SIGTERM)]


def test_child_that_never_gets_healthy_is_a_loud_error() -> None:
    supervisor, factory, killpg = build_supervisor(healthy_when_spawned=False)

    with pytest.raises(ChildSupervisorError):
        supervisor.ensure_running("supertonic")

    # The failed spawn is reaped, not leaked.
    assert supervisor.child_pids() == []
    assert killpg.calls != []


def test_dead_child_is_respawned_on_ensure_running() -> None:
    supervisor, factory, _killpg = build_supervisor()
    first = supervisor.ensure_running("supertonic")

    factory.processes[0].returncode = 1  # child died behind our back

    def probe(url: str) -> bool:
        return factory.starts > 1

    supervisor._health_probe = probe  # dead child means the port is free again
    second = supervisor.ensure_running("supertonic")

    assert second.pid != first.pid
    assert factory.starts == 2


def test_restart_coordinator_drains_then_exits_with_restart_code(tmp_path: Path) -> None:
    """POST /runtime/restart semantics: drain in-process, exit, launchd revives.

    The coordinator must stop children/hotkey/playback through app.stop() and
    then exit with the documented code — never call launchctl or pkill.
    """

    from dan.daemon.restart import RESTART_EXIT_CODE, RestartCoordinator
    from tests.test_daemon_hotkey import build_voice_app

    app, _tap = build_voice_app(tmp_path)
    supervisor, _factory, _killpg = build_supervisor()
    app.child_supervisor = supervisor
    supervisor.ensure_running("supertonic")
    exits: list[int] = []
    coordinator = RestartCoordinator(
        app,
        exit_fn=exits.append,
        sleep=lambda seconds: None,
    )
    try:
        response = coordinator.request_restart(reason="test restart", synchronous=True)

        assert response["ok"] is True
        assert response["restarting"] is True
        assert response["exit_code"] == RESTART_EXIT_CODE
        assert exits == [RESTART_EXIT_CODE]
        assert app.started is False
        assert app.child_pids() == []
        assert app.hotkey_monitor is None
    finally:
        app.close()


class FailingDrainApp:
    def __init__(self, supervisor: ChildSupervisor) -> None:
        self.child_supervisor = supervisor
        self.stop_calls = 0
        self.lifecycle_calls: list[str] = []

    def close_intake(self, *, reason: str) -> str:
        self.lifecycle_calls.append(f"close:{reason}")
        return "restart-operation"

    def stop(self, reason: str | None = None) -> None:
        self.stop_calls += 1
        self.lifecycle_calls.append(f"stop:{reason}")
        raise RuntimeError("drain failed")


class BlockingCloseRestartApp:
    def __init__(self) -> None:
        self.close_entered = threading.Event()
        self.release_close = threading.Event()
        self.close_calls = 0
        self.stop_calls = 0

    def close_intake(self, *, reason: str) -> str:
        self.close_calls += 1
        self.close_entered.set()
        assert self.release_close.wait(timeout=2)
        return "restart-operation"

    def stop(self, reason: str | None = None) -> None:
        self.stop_calls += 1


def test_duplicate_restart_waits_for_close_and_reuses_operation_id() -> None:
    from dan.daemon.restart import RestartCoordinator

    app = BlockingCloseRestartApp()
    coordinator = RestartCoordinator(
        app,
        exit_fn=lambda _code: None,
        sleep=lambda _seconds: None,
    )
    responses: list[dict[str, object]] = []
    first = threading.Thread(
        target=lambda: responses.append(
            coordinator.request_restart(reason="first", synchronous=True)
        )
    )
    second = threading.Thread(
        target=lambda: responses.append(
            coordinator.request_restart(reason="second", synchronous=True)
        )
    )

    first.start()
    assert app.close_entered.wait(timeout=2)
    second.start()
    second.join(timeout=0.05)
    duplicate_waited = second.is_alive()

    app.release_close.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert duplicate_waited, "duplicate must wait until durable close is proven"
    assert not first.is_alive()
    assert not second.is_alive()
    assert app.close_calls == 1
    assert app.stop_calls == 1
    assert {response["operation_id"] for response in responses} == {
        "restart-operation"
    }
    assert sorted(response["already_restarting"] for response in responses) == [
        False,
        True,
    ]
    assert {response["reason"] for response in responses} == {"first"}


def test_failed_drain_contains_children_and_blocks_exit_86() -> None:
    from dan.daemon.restart import RestartCoordinator

    supervisor, factory, killpg = build_supervisor()
    child = supervisor.ensure_running("supertonic")
    supervisor.start_watchdog()
    app = FailingDrainApp(supervisor)
    exits: list[int] = []
    coordinator = RestartCoordinator(
        app,
        exit_fn=exits.append,
        sleep=lambda _seconds: None,
    )

    coordinator.request_restart(reason="failed drain", synchronous=True)

    assert app.stop_calls == 1
    assert app.lifecycle_calls == ["close:failed drain", "stop:failed drain"]
    assert killpg.calls[0] == (child.pid, signal.SIGTERM)
    assert factory.processes[0].wait_calls >= 1
    assert supervisor.watchdog_alive is False
    assert supervisor.child_pids() == []
    assert exits == []
    assert coordinator.restarting is False


def test_failed_containment_blocks_exit_86() -> None:
    from dan.daemon.restart import RestartCoordinator

    stubborn = StubbornProcess(pid=5888)
    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=lambda _spec: stubborn,
        health_probe=lambda _url: True,
        killpg=lambda _pgid, _signum: None,
        process_group_alive=lambda _pgid: True,
        listener_released_probe=lambda _url: False,
        sleep=lambda _seconds: None,
    )
    supervisor._children["supertonic"] = ChildHandle(
        spec=SUPERTONIC_SPEC,
        process=stubborn,
    )
    app = FailingDrainApp(supervisor)
    exits: list[int] = []
    coordinator = RestartCoordinator(
        app,
        exit_fn=exits.append,
        sleep=lambda _seconds: None,
    )

    coordinator.request_restart(reason="failed containment", synchronous=True)

    assert exits == []
    assert supervisor.child_pids() == [stubborn.pid]
    assert coordinator.restarting is False


def test_daemon_shutdown_reaps_player_engine_and_hotkey(tmp_path: Path) -> None:
    """runtime.stop() leaves no child pid, no hotkey, no live playback owner."""

    from tests.test_daemon_hotkey import build_voice_app

    app, tap = build_voice_app(tmp_path)
    supervisor, factory, _killpg = build_supervisor()
    app.child_supervisor = supervisor
    supervisor.ensure_running("supertonic")
    monitor = app.hotkey_monitor
    try:
        assert app.child_pids() != []
        assert monitor is not None and monitor.running is True

        app.stop(reason="test shutdown")

        assert app.child_pids() == []
        assert monitor.running is False
        assert app.voice_broker is None
        assert app.voice_player is None
        assert app.voice_engine is None
    finally:
        app.close()
