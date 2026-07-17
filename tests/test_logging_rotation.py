"""FIX-11: the always-on daemon log (`dand.log`) must rotate.

`RunAtLoad` launchd means the process never restarts on its own, so a plain
`FileHandler` grows without bound. `configure_logging` must attach a size-capped
`RotatingFileHandler`, driven by config knobs. Rotation must also NOT regress the
0600 log perms FIX-10 established — a rotated backup created with umask perms
would be world-readable and leak the (unredacted-junk-transcript) log body.
"""

from __future__ import annotations

import logging
import stat
from collections.abc import Iterator
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from dan.config import DANConfig, load_config
from dan.logging import LOGGER_NAME, configure_logging
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


def _make_config(tmp_path: Path, **daemon_overrides: object) -> DANConfig:
    config = load_config(
        write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    )
    if daemon_overrides:
        config = replace(config, daemon=replace(config.daemon, **daemon_overrides))
    return config


def _rotating_handlers(logger: logging.Logger) -> list[RotatingFileHandler]:
    return [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]


def test_file_log_uses_size_capped_rotating_handler(
    tmp_path: Path, dan_logger: logging.Logger
) -> None:
    config = _make_config(tmp_path)
    paths = resolve_runtime_paths(config)

    configure_logging(config, paths)

    handlers = _rotating_handlers(dan_logger)
    assert len(handlers) == 1, "dand.log must be served by a RotatingFileHandler"
    handler = handlers[0]
    assert Path(handler.baseFilename) == paths.log_file
    assert handler.maxBytes > 0, "maxBytes==0 disables rotation entirely"
    assert handler.backupCount > 0, "backupCount==0 keeps no rotated history"


def test_rotation_knobs_are_honoured(
    tmp_path: Path, dan_logger: logging.Logger
) -> None:
    config = _make_config(tmp_path, log_max_bytes=4096, log_backup_count=3)
    paths = resolve_runtime_paths(config)

    configure_logging(config, paths)

    handler = _rotating_handlers(dan_logger)[0]
    assert handler.maxBytes == 4096
    assert handler.backupCount == 3


def test_rotated_backups_stay_owner_only(
    tmp_path: Path, dan_logger: logging.Logger
) -> None:
    # Tiny cap so a handful of records force a rollover, exercising the perms
    # of the rotated backup (the umask-perms regression FIX-10 guards against).
    config = _make_config(tmp_path, log_max_bytes=256, log_backup_count=2)
    paths = resolve_runtime_paths(config)

    logger = configure_logging(config, paths)
    for i in range(200):
        logger.info("rotate me %03d - padding to force a rollover past 256 bytes", i)

    rotated = paths.log_file.with_name(paths.log_file.name + ".1")
    assert rotated.exists(), "expected at least one rotated backup"
    assert stat.S_IMODE(rotated.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.log_file.stat().st_mode) == 0o600
