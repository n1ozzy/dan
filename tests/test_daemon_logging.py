"""`dan daemon run` must configure logging before serving.

The G4 live-gate runbook (docs/runbooks/G4_LIVE_GATE.md) calibrates the
voice thresholds from `voice.*` logger diagnostics; a daemon whose
`dan` logger has no handlers silently drops all of them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from dan import cli as dan_cli
from dan.config import load_config
from dan.logging import LOGGER_NAME
from dan.paths import resolve_runtime_paths
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


def test_daemon_run_attaches_file_handler_for_runtime_log_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dan_logger: logging.Logger,
) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    monkeypatch.setattr(dan_cli, "serve_forever", lambda app, host, port: None)

    rc = dan_cli.main(["--config", str(config_path), "daemon", "run"])

    assert rc == 0
    paths = resolve_runtime_paths(load_config(config_path))
    file_handlers = [
        handler
        for handler in dan_logger.handlers
        if isinstance(handler, logging.FileHandler)
    ]
    assert [Path(handler.baseFilename) for handler in file_handlers] == [paths.log_file]


def test_daemon_run_applies_configured_log_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dan_logger: logging.Logger,
) -> None:
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    monkeypatch.setattr(dan_cli, "serve_forever", lambda app, host, port: None)

    rc = dan_cli.main(["--config", str(config_path), "daemon", "run"])

    assert rc == 0
    # config_text in test_api_smoke pins daemon.log_level = "INFO"
    assert dan_logger.level == logging.INFO
