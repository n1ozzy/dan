"""ChildSupervisor: dand is the only owner of `supertonic serve` (Task 9).

Everything runs against injected fakes: no real process is ever spawned, no
port is probed, no signal reaches a live pid.
"""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from dan.daemon.supervisor import (
    ChildSpec,
    ChildSupervisor,
    ChildSupervisorError,
    ForeignPortOwnerError,
)

SUPERTONIC_SPEC = ChildSpec(
    name="supertonic",
    argv=("supertonic", "serve", "--model", "supertonic-3", "--port", "7797"),
    health_url="http://127.0.0.1:7797/v1/health",
    restart_limit=3,
    backoff_seconds=(0.0, 0.0, 0.0),
)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
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
        return healthy_when_spawned and factory.starts > 0

    supervisor = ChildSupervisor(
        [SUPERTONIC_SPEC],
        process_factory=factory,
        health_probe=probe,
        killpg=killpg,
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
    assert supervisor.child_pids() == []


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
