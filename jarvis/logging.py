"""Logging configuration placeholder."""

from __future__ import annotations

import logging as stdlib_logging


def get_logger(name: str = "jarvis") -> stdlib_logging.Logger:
    return stdlib_logging.getLogger(name)
