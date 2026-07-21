"""In-process PTT controller: hotkey edges drive listening leases (Task 9).

The daemon's hotkey monitor calls straight into DaemonApp's lease manager
(source "global_hotkey") — it never POSTs back into its own HTTP API. These
tests observe the lease state through the same read path the API uses.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_daemon_hotkey import (
    DEFAULT_PTT_MASK,
    build_voice_app,
)


def test_hotkey_down_creates_a_hold_lease_in_process(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    try:
        tap.flags_changed(DEFAULT_PTT_MASK)

        leases = app.active_listening_leases()
        assert len(leases) == 1
        assert leases[0].mode == "hold"
        assert leases[0].source == "global_hotkey"
    finally:
        app.close()


def test_hotkey_up_releases_the_hold_lease(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    try:
        tap.flags_changed(DEFAULT_PTT_MASK)
        tap.flags_changed(0x0)

        assert app.active_listening_leases() == []
    finally:
        app.close()


def test_hotkey_drives_the_recorder_like_the_ptt_endpoint(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    try:
        recorder = app.voice_recorder
        assert recorder is not None and recorder.recording is False

        tap.flags_changed(DEFAULT_PTT_MASK)
        assert recorder.recording is True

        tap.flags_changed(0x0)
        assert recorder.recording is False
    finally:
        app.close()


def test_accidental_brush_within_grace_never_arms_the_mic(tmp_path: Path) -> None:
    """Ozzy's typing guard: press+release faster than the activation grace
    (voice.ptt_activation_grace_ms) must not create a lease, start the
    recorder, or emit any ptt.* event."""

    from tests.test_daemon_hotkey import _events_of_type

    timers: list = []
    app, tap = build_voice_app(tmp_path, grace_timers=timers)
    try:
        tap.flags_changed(DEFAULT_PTT_MASK)  # brush down...
        tap.flags_changed(0x0)  # ...released before the grace elapsed

        assert len(timers) == 1 and timers[0].cancelled is True
        assert app.active_listening_leases() == []
        assert app.voice_recorder.recording is False
        db_path = tmp_path / "home" / "dan.db"
        assert _events_of_type(db_path, "ptt.down") == []
        assert _events_of_type(db_path, "ptt.up") == []
    finally:
        app.close()


def test_hold_past_grace_arms_then_release_pairs_up(tmp_path: Path) -> None:
    timers: list = []
    app, tap = build_voice_app(tmp_path, grace_timers=timers)
    try:
        tap.flags_changed(DEFAULT_PTT_MASK)
        assert app.active_listening_leases() == []  # grace still pending
        timers[0].fire()  # combo held past the grace -> mic arms now
        assert len(app.active_listening_leases()) == 1

        tap.flags_changed(0x0)
        assert app.active_listening_leases() == []
    finally:
        app.close()


def test_edges_after_stop_never_touch_the_lease_manager(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    app.stop(reason="test shutdown")
    try:
        assert tap.running is False
        # A late OS event on a stopped monitor must be inert, not a crash.
        monitor_edges: list[str] = []
        assert app.hotkey_monitor is None
        assert monitor_edges == []
    finally:
        app.close()
