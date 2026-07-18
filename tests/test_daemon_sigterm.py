"""`dan daemon run` must write daemon.stopped on SIGTERM.

launchd stops the daemon with SIGTERM; without a handler Python dies
mid-loop, `app.stop()` never runs and events keep a dangling
`daemon.started` with no matching `daemon.stopped` (G4 punch list).

Since Task 9 the same stop() path also reaps supervised children and releases
the daemon-owned hotkey monitor (the panel no longer owns any global monitor),
so a SIGTERM exit must still complete cleanly through that longer teardown.
"""

from __future__ import annotations

import json
import logging
import signal
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan import cli as dan_cli
from dan.logging import LOGGER_NAME
from tests.test_api_smoke import write_config


@pytest.fixture
def dan_logger() -> Iterator[logging.Logger]:
    """Snapshot and restore the global `dan` logger around the test."""

    logger = logging.getLogger(LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers = []
    try:
        yield logger
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


@pytest.fixture
def restore_sigterm() -> Iterator[None]:
    previous = signal.getsignal(signal.SIGTERM)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _read_events(db_path: Path) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT type, payload_json FROM events ORDER BY id").fetchall()
    finally:
        conn.close()
    return [(row[0], json.loads(row[1])) for row in rows]


def test_daemon_run_writes_daemon_stopped_on_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dan_logger: logging.Logger,
    restore_sigterm: None,
) -> None:
    db_path = tmp_path / "home" / "dan.db"
    config_path = write_config(tmp_path / "dan.toml", db_path)

    def fake_serve_forever(app: object, host: str, port: int) -> None:
        # Deliver SIGTERM to ourselves exactly where launchd would: while
        # the daemon is blocked serving. raise_signal runs the installed
        # Python handler synchronously in this (main) thread. With no
        # Python handler installed (SIG_DFL) raising would kill the whole
        # pytest process — the unhandled-SIGTERM bug itself — so return
        # instead and let the missing daemon.stopped event fail the test.
        if callable(signal.getsignal(signal.SIGTERM)):
            signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(dan_cli, "serve_forever", fake_serve_forever)

    rc = dan_cli.main(["--config", str(config_path), "daemon", "run"])

    assert rc == 0
    events = _read_events(db_path)
    started = [payload for etype, payload in events if etype == "daemon.started"]
    stopped = [payload for etype, payload in events if etype == "daemon.stopped"]
    assert len(started) == 1
    assert len(stopped) == 1
    assert stopped[0]["reason"] == "SIGTERM"
    # Task 9 ownership: under DAN_TEST_MODE the real Quartz monitor never
    # starts, so the SIGTERM teardown must not leave a hotkey owner lock.
    assert not [
        payload for etype, payload in events if etype in {"ptt.down", "ptt.up"}
    ]
