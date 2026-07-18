from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.daemon import app as app_module
from dan.daemon import supervisor as supervisor_module
from dan.voice.models import SpeechIntent
from dan.voice.broker import VoiceBrokerShutdownTimeout
from tests.test_daemon_hotkey import build_voice_app


class TimedOutBroker:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.fail = True

    def stop(self, *, join_timeout: float = 5.0) -> None:
        self.stop_calls += 1
        if self.fail:
            raise VoiceBrokerShutdownTimeout("synthesis still owns the engine")

    def start(self) -> None:
        return None

    def stop_playback(self) -> None:
        return None


class CloseRecordingEngine:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class CompleteContainmentSupervisor:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop_all(self, timeout: float = 5.0):
        self.stop_calls += 1
        return supervisor_module.ChildContainmentResult(
            watchdog_joined=True,
            children_reaped=True,
            process_groups_released=True,
            listeners_released=True,
            remaining_pids=(),
            errors=(),
        )

    def status(self):
        return {}

    def child_pids(self):
        return []


class RetryingContainmentSupervisor(CompleteContainmentSupervisor):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def stop_all(self, timeout: float = 5.0):
        self.stop_calls += 1
        if not self.fail:
            return supervisor_module.ChildContainmentResult(
                watchdog_joined=True,
                children_reaped=True,
                process_groups_released=True,
                listeners_released=True,
                remaining_pids=(),
                errors=(),
            )
        return supervisor_module.ChildContainmentResult(
            watchdog_joined=True,
            children_reaped=True,
            process_groups_released=False,
            listeners_released=False,
            remaining_pids=(6111,),
            errors=("supertonic descendant still owns the listener",),
        )

    def child_pids(self):
        return [6111] if self.fail else []


def test_daemon_does_not_drop_broker_or_close_engine_after_failed_quiesce(
    tmp_path,
) -> None:
    app, _tap = build_voice_app(tmp_path)
    original_broker = app.voice_broker
    assert original_broker is not None
    original_broker.stop()
    broker = TimedOutBroker()
    engine = CloseRecordingEngine()
    player = SimpleNamespace(name="single-player")
    supervisor = CompleteContainmentSupervisor()
    app.voice_broker = broker
    app.voice_engine = engine
    app.voice_player = player
    app.child_supervisor = supervisor

    try:
        with pytest.raises(VoiceBrokerShutdownTimeout):
            app.stop(reason="blocked synthesis")

        assert app.voice_broker is broker
        assert app.voice_engine is engine
        assert app.voice_player is player
        assert engine.close_calls == 0
        assert supervisor.stop_calls >= 1
        assert broker.stop_calls == 2

        with pytest.raises(app_module.DaemonLifecycleError, match="voice owner"):
            app.start()

        connection = app.conn
        with pytest.raises(VoiceBrokerShutdownTimeout):
            app.close()
        assert app.conn is connection
        assert app.conn is not None
        assert app.conn.execute("SELECT 1").fetchone() == (1,)
        assert app.voice_broker is broker
        assert app.voice_engine is engine
        assert app.voice_player is player
        assert engine.close_calls == 0
    finally:
        broker.fail = False
        app.stop(reason="test cleanup")
        app.close()


def test_startup_cleanup_retains_live_voice_owner_on_quiescence_timeout(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dan.daemon.app import create_daemon_app
    from dan.voice import broker as broker_module
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "dan.db").replace(
            "[voice]\nenabled = false",
            "[voice]\nenabled = true",
        ),
        encoding="utf-8",
    )
    broker = TimedOutBroker()
    monkeypatch.setattr(broker_module, "VoiceBroker", lambda *args, **kwargs: broker)
    app = create_daemon_app(config_path)

    def fail_late_startup() -> None:
        raise RuntimeError("late startup failure")

    app._start_hotkey_monitor = fail_late_startup
    try:
        with pytest.raises(VoiceBrokerShutdownTimeout):
            app.start()

        assert app.started is False
        assert app.voice_broker is broker
        assert app.voice_engine is not None
        assert app.voice_player is not None
        assert app._voice_owner_blocked is True
        with pytest.raises(app_module.DaemonLifecycleError, match="voice owner"):
            app.start()
    finally:
        broker.fail = False
        app.stop(reason="test cleanup")
        app.close()


def test_startup_cleanup_retains_owners_after_incomplete_child_containment(
    tmp_path,
) -> None:
    from dan.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "dan.db").replace(
            "[voice]\nenabled = false",
            "[voice]\nenabled = true",
        ),
        encoding="utf-8",
    )
    app = create_daemon_app(config_path)
    supervisor = RetryingContainmentSupervisor()
    app.child_supervisor = supervisor

    def fail_late_startup() -> None:
        raise RuntimeError("late startup failure")

    app._start_hotkey_monitor = fail_late_startup
    try:
        with pytest.raises(
            app_module.DaemonLifecycleError,
            match="containment",
        ):
            app.start()

        assert app.started is False
        assert app._voice_owner_blocked is True
        assert app.voice_broker is not None
        assert app.voice_engine is not None
        assert app.voice_player is not None
        assert supervisor.stop_calls >= 1
        with pytest.raises(app_module.DaemonLifecycleError, match="voice owner"):
            app.start()
    finally:
        supervisor.fail = False
        app.stop(reason="test cleanup")
        app.close()


def test_shutdown_drains_gateway_producer_before_broker_barrier(tmp_path) -> None:
    app, _tap = build_voice_app(tmp_path)
    original_gateway = app.voice_gateway
    service = app.voice_service
    assert original_gateway is not None
    assert service is not None
    original_gateway.stop()

    class EnqueueOnStopGateway:
        def __init__(self) -> None:
            self.stop_calls = 0

        def stop(self) -> None:
            self.stop_calls += 1
            service.submit(
                SpeechIntent(
                    text="final speech produced during gateway drain",
                    persona="dan",
                    source="voice",
                    session="shutdown-turn",
                    participant="dan",
                    priority=0,
                    lane="normal",
                    interrupt_policy="finish_current",
                    utterance_index=0,
                )
            )

    gateway = EnqueueOnStopGateway()
    app.voice_gateway = gateway
    try:
        app.stop(reason="producer ordering")

        active = app.conn.execute(
            """
            SELECT COUNT(*) FROM voice_queue
            WHERE status IN ('queued', 'synthesizing', 'speaking')
            """
        ).fetchone()[0]
        assert gateway.stop_calls == 1
        assert active == 0
    finally:
        app.close()
