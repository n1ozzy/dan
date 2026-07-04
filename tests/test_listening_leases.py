"""G2 ListeningLease + PTT tests (CONTRACTS §8, ADR-006).

Leases live in the DB (never a /tmp flag), a hold release must not clear a
locked lease, stale leases expire instead of listening forever, and the
(mock) recorder starts/stops exactly with the first/last active lease.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.store.db import close_quietly, initialize_database
from jarvis.voice.listening import ListeningLeaseManager, ListeningLeaseError
from jarvis.voice.recorder import MockRecorder, RecorderBackendError, build_recorder
from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_database(tmp_path / "leases.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


def voice_config(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "recorder": "mock",
        "ptt_mode": "hold",
        "ptt_hold_ttl_seconds": 30,
        "listen_lock_ttl_seconds": 600,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Clock:
    def __init__(self, value: str = "2026-07-02T10:00:00+00:00") -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


class CaptureAwareRecorder(MockRecorder):
    """Recorder double that models whether stop() would hand audio to STT."""

    def __init__(self) -> None:
        super().__init__()
        self.discarded = 0
        self.capture_deliveries = 0
        self._discard_current = False

    def discard_current_capture(self) -> None:
        self.discarded += 1
        self._discard_current = True

    def stop(self) -> None:
        was_recording = self.recording
        super().stop()
        if not was_recording:
            return
        if self._discard_current:
            self._discard_current = False
            return
        self.capture_deliveries += 1


def manager(conn, recorder=None, clock=None, events=None, config=None):
    class FakeEventStore:
        def append(self, event_type, source, payload):
            if events is not None:
                events.append((getattr(event_type, "value", str(event_type)), payload))

    return ListeningLeaseManager(
        conn,
        config=config or voice_config(),
        recorder=recorder or MockRecorder(),
        event_store=FakeEventStore(),
        now=clock or Clock(),
    )


def test_ptt_down_creates_active_hold_lease_and_starts_recorder(conn) -> None:
    recorder = MockRecorder()
    events: list = []
    m = manager(conn, recorder=recorder, events=events)

    lease = m.acquire(mode="hold", source="ptt")

    assert lease.mode == "hold"
    assert lease.status == "active"
    assert recorder.started == 1
    row = conn.execute(
        "SELECT status, mode, source FROM listening_leases"
    ).fetchone()
    assert row == ("active", "hold", "ptt")
    assert any(name == "listening.lease.created" for name, _ in events)


def test_repeated_ptt_down_refreshes_instead_of_stacking(conn) -> None:
    recorder = MockRecorder()
    m = manager(conn, recorder=recorder)

    first = m.acquire(mode="hold", source="ptt")
    second = m.acquire(mode="hold", source="ptt")

    assert first.id == second.id
    count = conn.execute(
        "SELECT COUNT(*) FROM listening_leases WHERE status = 'active'"
    ).fetchone()[0]
    assert count == 1
    assert recorder.started == 1


def test_renewing_a_lease_restarts_a_dead_recorder(conn) -> None:
    # FIX-09: renewing an existing lease only bumped its TTL and returned — it
    # never re-synced the recorder, so a sox that crashed under a still-active
    # lease stayed dead. The renewal must restart it (recorder.start is
    # idempotent, so a live recorder is untouched).
    recorder = MockRecorder()
    m = manager(conn, recorder=recorder)
    m.acquire(mode="locked", source="lock")
    assert recorder.started == 1

    recorder.recording = False  # sox crashed on its own; the lease is still active
    m.acquire(mode="locked", source="lock")  # renewal

    assert recorder.started == 2


def test_ptt_up_releases_hold_and_stops_recorder(conn) -> None:
    recorder = MockRecorder()
    events: list = []
    m = manager(conn, recorder=recorder, events=events)

    m.acquire(mode="hold", source="ptt")
    released = m.release(mode="hold")

    assert len(released) == 1
    assert recorder.stopped == 1
    row = conn.execute("SELECT status, released_at FROM listening_leases").fetchone()
    assert row[0] == "released"
    assert row[1]
    assert any(name == "listening.lease.released" for name, _ in events)


def test_short_ptt_press_discards_capture_before_stt_handoff(conn) -> None:
    clock = Clock("2026-07-02T10:00:00+00:00")
    recorder = CaptureAwareRecorder()
    m = manager(conn, recorder=recorder, clock=clock)

    m.acquire(mode="hold", source="ptt")
    clock.value = "2026-07-02T10:00:00.200000+00:00"
    released = m.release(mode="hold")

    assert len(released) == 1
    assert recorder.stopped == 1
    assert recorder.discarded == 1
    assert recorder.capture_deliveries == 0


def test_long_ptt_press_keeps_capture_for_stt_handoff(conn) -> None:
    clock = Clock("2026-07-02T10:00:00+00:00")
    recorder = CaptureAwareRecorder()
    m = manager(conn, recorder=recorder, clock=clock)

    m.acquire(mode="hold", source="ptt")
    clock.value = "2026-07-02T10:00:00.900000+00:00"
    m.release(mode="hold")

    assert recorder.discarded == 0
    assert recorder.capture_deliveries == 1


def test_ptt_debounce_threshold_is_centralized_and_configurable(conn) -> None:
    clock = Clock("2026-07-02T10:00:00+00:00")
    recorder = CaptureAwareRecorder()
    m = manager(
        conn,
        recorder=recorder,
        clock=clock,
        config=voice_config(ptt_debounce_ms=100),
    )

    m.acquire(mode="hold", source="ptt")
    clock.value = "2026-07-02T10:00:00.200000+00:00"
    m.release(mode="hold")

    assert recorder.discarded == 0
    assert recorder.capture_deliveries == 1


def test_ptt_up_never_clears_a_locked_lease(conn) -> None:
    recorder = MockRecorder()
    m = manager(conn, recorder=recorder)

    m.acquire(mode="locked", source="lock")
    m.acquire(mode="hold", source="ptt")
    m.release(mode="hold")

    statuses = dict(
        conn.execute("SELECT mode, status FROM listening_leases").fetchall()
    )
    assert statuses["locked"] == "active"
    assert statuses["hold"] == "released"
    # The locked lease still listens: the recorder must NOT have stopped.
    assert recorder.stopped == 0
    assert m.is_listening() is True


def test_unlock_releases_locked_and_stops_recorder(conn) -> None:
    recorder = MockRecorder()
    m = manager(conn, recorder=recorder)

    m.acquire(mode="locked", source="lock")
    m.release(mode="locked")

    assert recorder.stopped == 1
    assert m.is_listening() is False


def test_stale_lease_expires_instead_of_listening_forever(conn) -> None:
    recorder = MockRecorder()
    clock = Clock("2026-07-02T10:00:00+00:00")
    events: list = []
    m = manager(conn, recorder=recorder, clock=clock, events=events)

    m.acquire(mode="hold", source="ptt")
    clock.value = "2026-07-02T10:05:00+00:00"  # past the 30s hold TTL

    assert m.is_listening() is False
    row = conn.execute("SELECT status FROM listening_leases").fetchone()
    assert row[0] == "expired"
    assert recorder.stopped == 1
    assert any(name == "listening.lease.expired" for name, _ in events)


def test_global_hotkey_source_is_accepted(conn) -> None:
    m = manager(conn)

    lease = m.acquire(mode="hold", source="global_hotkey")

    assert lease.source == "global_hotkey"


def test_unknown_mode_or_source_fails_closed(conn) -> None:
    m = manager(conn)

    with pytest.raises(ListeningLeaseError):
        m.acquire(mode="always_on", source="ptt")
    with pytest.raises(ListeningLeaseError):
        m.acquire(mode="hold", source="model")


def test_active_leases_survive_a_new_connection(conn, tmp_path: Path) -> None:
    m = manager(conn)
    m.acquire(mode="locked", source="lock")

    other = initialize_database(tmp_path / "leases.db")
    try:
        m2 = ListeningLeaseManager(
            other,
            config=voice_config(),
            recorder=MockRecorder(),
            event_store=None,
            now=Clock(),
        )
        assert m2.is_listening() is True
        assert [lease.mode for lease in m2.active()] == ["locked"]
    finally:
        close_quietly(other)


def test_recorder_factory_rejects_unknown_backend() -> None:
    with pytest.raises(RecorderBackendError):
        build_recorder("sox-not-yet")
    assert isinstance(build_recorder("mock"), MockRecorder)


# --- daemon API ---------------------------------------------------------------


def _daemon(tmp_path: Path, *, voice_enabled: bool):
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "jarvis.db").replace(
            "[voice]\nenabled = false",
            f"[voice]\nenabled = {'true' if voice_enabled else 'false'}",
        ),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    return daemon_app


def test_voice_disabled_rejects_ptt(tmp_path: Path) -> None:
    from tests.test_api_smoke import request_json, running_server

    daemon_app = _daemon(tmp_path, voice_enabled=False)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/voice/ptt/down",
                {"source": "ptt"},
            )
        assert status == 409
    finally:
        daemon_app.close()


def test_ptt_lifecycle_through_the_api(tmp_path: Path) -> None:
    from tests.test_api_smoke import request_json, running_server

    daemon_app = _daemon(tmp_path, voice_enabled=True)
    try:
        with running_server(daemon_app) as base_url:
            status, down = request_json(
                "POST", f"{base_url}/voice/ptt/down", {}
            )
            assert status == 200, down
            assert down["lease"]["mode"] == "hold"

            status, listening = request_json("GET", f"{base_url}/voice/listening")
            assert status == 200
            assert listening["listening"] is True

            status, up = request_json(
                "POST", f"{base_url}/voice/ptt/up", {}
            )
            assert status == 200
            assert up["released"] == 1

            status, lock = request_json(
                "POST", f"{base_url}/voice/listen/lock", {}
            )
            assert status == 200
            status, after_up = request_json(
                "POST", f"{base_url}/voice/ptt/up", {}
            )
            assert status == 200
            status, listening = request_json("GET", f"{base_url}/voice/listening")
            assert listening["listening"] is True  # locked survives ptt up

            status, unlock = request_json(
                "POST", f"{base_url}/voice/listen/unlock", {}
            )
            assert status == 200
            status, listening = request_json("GET", f"{base_url}/voice/listening")
            assert listening["listening"] is False
    finally:
        daemon_app.close()


def test_ptt_down_acquires_hold_lease_without_cancelling_pending_speech(tmp_path: Path) -> None:
    from jarvis.voice.queue import VoiceQueue
    from tests.test_api_smoke import request_json, running_server

    daemon_app = _daemon(tmp_path, voice_enabled=True)
    try:
        assert daemon_app.conn is not None
        queue = VoiceQueue(daemon_app.conn)
        request = queue.enqueue(
            text="Nie powinno doczekać się VAD ani STT.",
            turn_id="turn-before-ptt",
            seq=0,
        )

        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/voice/ptt/down",
                {"source": "ptt"},
            )

        assert status == 200, payload
        assert payload["lease"]["mode"] == "hold"
        assert payload["lease"]["source"] == "ptt"
        status_row = daemon_app.conn.execute(
            "SELECT status FROM voice_queue WHERE id = ?",
            (request.id,),
        ).fetchone()
        assert status_row == ("queued",)
        cancelled_events = daemon_app.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
        ).fetchone()[0]
        assert cancelled_events == 0
        event_types = [
            str(row[0])
            for row in daemon_app.conn.execute(
                "SELECT type FROM events ORDER BY id"
            ).fetchall()
        ]
        assert "voice.speak.cancelled" not in event_types
        assert "listening.lease.created" in event_types
    finally:
        daemon_app.close()


def test_ptt_unknown_source_is_bad_request(tmp_path: Path) -> None:
    # An unknown `source` is bad client input, not a server fault: the
    # ListeningLeaseError acquire() raises must map to 400, not 500 (FIX-17).
    from tests.test_api_smoke import request_json, running_server

    daemon_app = _daemon(tmp_path, voice_enabled=True)
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/voice/ptt/down",
                {"source": "nope"},
            )
            assert status == 400, payload
            assert "nope" in payload["error"]

            # A valid source still succeeds — no regression.
            status, ok = request_json(
                "POST", f"{base_url}/voice/ptt/down", {"source": "ptt"}
            )
            assert status == 200, ok
    finally:
        daemon_app.close()


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
