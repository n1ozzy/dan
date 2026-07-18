"""Daemon-owned global PTT hotkey (Release 1, Task 9).

The one CGEventTap lives in dand, not in the panel. These tests drive a fake
event tap through the real MacOSHotkeyMonitor + DaemonApp wiring: a physical
press produces exactly one ptt.down/ptt.up pair and the edges drive the
in-process listening-lease manager directly (never HTTP back into dand).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dan.input.macos_event_tap import MacOSHotkeyMonitor

# right_cmd (0x10) + right_shift (0x4): the default voice.ptt_hotkey combo.
RIGHT_CMD_RIGHT_SHIFT = 0x10 | 0x4
RIGHT_OPTION_DOWN = RIGHT_CMD_RIGHT_SHIFT
RIGHT_OPTION_UP = 0x0


class FakeEventTap:
    """Hermetic CGEventTap double: no Quartz, no Accessibility, no threads."""

    def __init__(self) -> None:
        self.running = False
        self._callback = None

    def start(self, on_flags_changed) -> None:
        self._callback = on_flags_changed
        self.running = True

    def stop(self) -> None:
        self.running = False
        self._callback = None

    def flags_changed(self, flags: int) -> None:
        assert self._callback is not None, "tap not started"
        self._callback(flags)


class FakeGraceTimer:
    """Test double for the PTT activation-grace timer.

    "immediate" mode fires the callback synchronously on start() (the combo is
    held past the grace); "manual" mode fires only when the test says so, which
    models an accidental brush released inside the grace window.
    """

    def __init__(self, interval: float, function, *, immediate: bool = False) -> None:
        self.interval = interval
        self.function = function
        self.immediate = immediate
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True
        if self.immediate and not self.cancelled:
            self.function()

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        assert self.started
        if not self.cancelled:
            self.function()


def build_voice_app(tmp_path: Path, *, grace_timers: list | None = None):
    """A started DaemonApp with voice enabled (all-mock) and a fake event tap.

    Returns (app, tap): the daemon believes it owns a live hotkey monitor while
    every OS-facing piece (Quartz tap, Accessibility probe, grace timer) is a
    test double. With `grace_timers=None` the activation grace elapses
    instantly (held-past-grace behavior); passing a list captures manual
    timers so a test can decide whether the grace ever fires.
    """

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
    tap = FakeEventTap()

    def timer_factory(interval: float, function) -> FakeGraceTimer:
        timer = FakeGraceTimer(interval, function, immediate=grace_timers is None)
        if grace_timers is not None:
            grace_timers.append(timer)
        return timer

    app.ptt_timer_factory = timer_factory

    def factory(*, lock_path: Path, required_mask: int, on_edge):
        return MacOSHotkeyMonitor(
            lock_path=lock_path,
            required_mask=required_mask,
            on_edge=on_edge,
            tap_factory=lambda: tap,
            trusted_checker=lambda: True,
        )

    app.hotkey_monitor_factory = factory
    app.start()
    return app, tap


def _events_of_type(db_path: Path, event_type: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type = ? ORDER BY id",
            (event_type,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def test_one_physical_press_creates_one_ptt_pair(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    db_path = tmp_path / "home" / "dan.db"
    try:
        # macOS delivers repeated flagsChanged events while a combo is held;
        # only the edge may create a PTT pair, never the level.
        tap.flags_changed(RIGHT_OPTION_DOWN)
        tap.flags_changed(RIGHT_OPTION_DOWN)
        tap.flags_changed(RIGHT_OPTION_UP)

        assert len(_events_of_type(db_path, "ptt.down")) == 1
        assert len(_events_of_type(db_path, "ptt.up")) == 1
    finally:
        app.close()


def test_hotkey_monitor_uses_runtime_lock_and_reports_health(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    try:
        monitor = app.hotkey_monitor
        assert monitor is not None
        assert monitor.running is True
        health = monitor.health()
        assert health.running is True
        assert health.accessibility == "trusted"
        # The owner lock is a dand runtime artifact resolved via dan.paths.
        assert (app.paths.runtime_dir / "hotkey.lock").exists()
    finally:
        app.close()


def test_daemon_stop_stops_the_hotkey_monitor(tmp_path: Path) -> None:
    app, tap = build_voice_app(tmp_path)
    monitor = app.hotkey_monitor
    try:
        assert monitor is not None and monitor.running is True
        app.stop(reason="test shutdown")
        assert monitor.running is False
        assert app.hotkey_monitor is None
        assert tap.running is False
    finally:
        app.close()


def test_hotkey_not_started_when_voice_disabled(tmp_path: Path) -> None:
    from dan.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "dan.db"),
        encoding="utf-8",
    )
    app = create_daemon_app(config_path)
    calls = {"factory": 0}

    def factory(**_kwargs):
        calls["factory"] += 1
        raise AssertionError("hotkey factory must not run with voice disabled")

    app.hotkey_monitor_factory = factory
    app.start()
    try:
        assert calls["factory"] == 0
        assert app.hotkey_monitor is None
    finally:
        app.close()
