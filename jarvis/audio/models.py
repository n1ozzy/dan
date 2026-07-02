"""Audio device state models (CONTRACTS §9)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AudioDevice:
    """One observed audio device (read-only view, never a selection)."""

    name: str
    transport: str = "unknown"  # builtin | bluetooth | usb | unknown
    is_input: bool = False
    is_output: bool = False
    default_input: bool = False
    default_output: bool = False
    uid: str | None = None


@dataclass(frozen=True)
class AudioDeviceState:
    """Policy-applied device view; "current" is the latest snapshot."""

    input_device: str | None = None
    output_device: str | None = None
    preferred_input: str = "Mikrofon (MacBook Air)"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    ts: str | None = None
    input_transport: str | None = None
    output_transport: str | None = None
    devices: tuple[AudioDevice, ...] = field(default_factory=tuple)
