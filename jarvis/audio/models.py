"""Audio device state model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class AudioDeviceState:
    input_device: str | None = None
    output_device: str | None = None
    preferred_input: str = "Mikrofon (MacBook Air)"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    ts: datetime | None = None
