"""Exactly one hotkey owner per machine (Task 9).

The MacOSHotkeyMonitor guards the global event tap with a file lock in the
dand runtime dir; a second monitor on the same lock path must fail loudly with
SingleOwnerError instead of installing a second tap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.input.macos_event_tap import MacOSHotkeyMonitor, SingleOwnerError
from tests.test_daemon_hotkey import DEFAULT_PTT_MASK, FakeEventTap


def make_monitor(lock_path: Path, edges: list[str] | None = None) -> MacOSHotkeyMonitor:
    return MacOSHotkeyMonitor(
        lock_path=lock_path,
        required_mask=DEFAULT_PTT_MASK,
        on_edge=(edges.append if edges is not None else lambda edge: None),
        tap_factory=FakeEventTap,
        trusted_checker=lambda: True,
    )


def test_second_hotkey_owner_is_rejected(tmp_path: Path) -> None:
    lock_path = tmp_path / "runtime" / "hotkey.lock"
    first = make_monitor(lock_path)
    first.start()
    try:
        with pytest.raises(SingleOwnerError):
            make_monitor(lock_path).start()
    finally:
        first.stop()


def test_rejected_owner_leaves_the_running_monitor_untouched(tmp_path: Path) -> None:
    lock_path = tmp_path / "hotkey.lock"
    edges: list[str] = []
    first = make_monitor(lock_path, edges)
    first.start()
    try:
        with pytest.raises(SingleOwnerError):
            make_monitor(lock_path).start()
        assert first.running is True
        first.handle_flags_changed(DEFAULT_PTT_MASK)
        first.handle_flags_changed(0x0)
        assert edges == ["down", "up"]
    finally:
        first.stop()


def test_stop_releases_the_lock_for_the_next_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "hotkey.lock"
    first = make_monitor(lock_path)
    first.start()
    first.stop()
    assert first.running is False

    second = make_monitor(lock_path)
    second.start()
    try:
        assert second.running is True
    finally:
        second.stop()


def test_failed_tap_start_releases_the_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "hotkey.lock"

    class ExplodingTap:
        running = False

        def start(self, on_flags_changed) -> None:
            raise RuntimeError("no event tap on this box")

        def stop(self) -> None:  # pragma: no cover - never started
            raise AssertionError("stop on a tap that never started")

    broken = MacOSHotkeyMonitor(
        lock_path=lock_path,
        required_mask=DEFAULT_PTT_MASK,
        on_edge=lambda edge: None,
        tap_factory=ExplodingTap,
        trusted_checker=lambda: True,
    )
    with pytest.raises(RuntimeError):
        broken.start()

    # The lock did not leak: a fresh owner can start.
    replacement = make_monitor(lock_path)
    replacement.start()
    try:
        assert replacement.running is True
    finally:
        replacement.stop()
