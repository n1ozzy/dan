"""Logging setup and redaction helpers for Jarvis."""

from __future__ import annotations

import logging as stdlib_logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

from jarvis.config import JarvisConfig
from jarvis.paths import (
    RUNTIME_FILE_MODE,
    RuntimePaths,
    ensure_runtime_dirs,
    resolve_runtime_paths,
    secure_path,
)


LOGGER_NAME = "jarvis"
SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9._-]+"),
    re.compile(r"\bsk-[A-Za-z0-9._-]+"),
    re.compile(r"\bgsk_[A-Za-z0-9._-]+"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)([A-Z0-9_]*API_KEY\s*=\s*)[^\s,;]+"),
    re.compile(r"(?i)(xi-api-key\s*[:=]\s*)[^\s,;]+"),
)


class RedactingFormatter(stdlib_logging.Formatter):
    def format(self, record: stdlib_logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_secrets(rendered)


class SecureRotatingFileHandler(RotatingFileHandler):
    """`RotatingFileHandler` that keeps every log file owner-only (0600).

    FIX-10 secured `jarvisd.log` to 0600, but a plain rotation reopens the base
    file with umask perms (0644 on macOS) — leaking log bodies that carry
    unredacted junk transcripts (Gate G review §6). Rotated backups are renamed
    from the already-0600 active file so they inherit 0600; the only file that
    needs re-securing is the freshly opened base file, done here on every open.
    """

    def _open(self) -> IO[str]:
        stream = super()._open()
        secure_path(Path(self.baseFilename), RUNTIME_FILE_MODE)
        return stream


def configure_logging(config: JarvisConfig, paths: RuntimePaths | None = None) -> stdlib_logging.Logger:
    """Configure Jarvis console and file logging without starting runtime services."""

    runtime_paths = paths or resolve_runtime_paths(config)
    ensure_runtime_dirs(runtime_paths)

    logger = stdlib_logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(_log_level(config.daemon.log_level))
    logger.propagate = False

    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = stdlib_logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotate the always-on daemon log so it can't grow without bound (FIX-11).
    # Logs can carry redacted-but-sensitive context, so the handler keeps every
    # file owner-only (0600) across rotation — see SecureRotatingFileHandler.
    file_handler = SecureRotatingFileHandler(
        runtime_paths.log_file,
        maxBytes=config.daemon.log_max_bytes,
        backupCount=config.daemon.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> stdlib_logging.Logger:
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return stdlib_logging.getLogger(name)
    return stdlib_logging.getLogger(f"{LOGGER_NAME}.{name}")


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(_redaction_replacement, redacted)
    return redacted


def _redaction_replacement(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"


def _log_level(value: str) -> int:
    level = stdlib_logging.getLevelName(value.upper())
    if isinstance(level, int):
        return level
    return stdlib_logging.INFO
