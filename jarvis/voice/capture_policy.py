"""Centralized voice capture timing thresholds."""

from __future__ import annotations

from datetime import datetime
from typing import Any


DEFAULT_PTT_DEBOUNCE_MS = 350
DEFAULT_MIN_CAPTURE_MS = 800


def ptt_debounce_ms(config: Any) -> int:
    return _non_negative_ms(config, "ptt_debounce_ms", DEFAULT_PTT_DEBOUNCE_MS)


def min_capture_ms(config: Any) -> int:
    return _non_negative_ms(config, "min_capture_ms", DEFAULT_MIN_CAPTURE_MS)


def elapsed_ms(start_iso: str, end_iso: str) -> float:
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    return max(0.0, (end - start).total_seconds() * 1000.0)


def _non_negative_ms(config: Any, name: str, default: int) -> int:
    value = getattr(config, name, default)
    return max(0, int(value))


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


__all__ = [
    "DEFAULT_MIN_CAPTURE_MS",
    "DEFAULT_PTT_DEBOUNCE_MS",
    "elapsed_ms",
    "min_capture_ms",
    "ptt_debounce_ms",
]
