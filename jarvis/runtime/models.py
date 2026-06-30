"""Runtime observation models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class RuntimeLaunchMode(StrEnum):
    CLI = "cli"
    LAUNCHD = "launchd"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeProcessObservation:
    id: str
    launch_mode: RuntimeLaunchMode = RuntimeLaunchMode.UNKNOWN
    official_label_present: bool = False
    legacy_labels: tuple[str, ...] = field(default_factory=tuple)
    legacy_processes: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    ts: datetime | None = None
