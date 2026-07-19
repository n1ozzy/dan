"""Fail-closed boundaries for starting audio and microphone execution."""

from __future__ import annotations

import os

AUDIO_EXECUTABLE_NAMES = frozenset(
    {
        "afplay",
        "aplay",
        "arecord",
        "ffmpeg",
        "ffplay",
        "parec",
        "play",
        "pw-record",
        "rec",
        "say",
        "sox",
        "supertonic",
    }
)


class AudioExecutionDisabled(RuntimeError):
    """Raised when a new audio execution edge is disabled."""


class MicrophoneExecutionDisabled(RuntimeError):
    """Raised when a new microphone execution edge is disabled."""


def assert_audio_execution_allowed(*, operation: str) -> None:
    """Reject a new audio operation only for the exact kill-switch value."""

    if os.environ.get("DAN_DISABLE_AUDIO") == "1":
        raise AudioExecutionDisabled(f"audio execution disabled: {operation}")


def assert_microphone_execution_allowed(*, operation: str) -> None:
    """Reject a new microphone operation only for the exact kill-switch value."""

    if os.environ.get("DAN_DISABLE_MIC") == "1":
        raise MicrophoneExecutionDisabled(f"microphone execution disabled: {operation}")


__all__ = [
    "AUDIO_EXECUTABLE_NAMES",
    "AudioExecutionDisabled",
    "MicrophoneExecutionDisabled",
    "assert_audio_execution_allowed",
    "assert_microphone_execution_allowed",
]
