"""Recorder backends for the listening pipeline (G2).

Only the mock exists until G4 brings the real sox-based recorder. The
recorder is a dumb sink: leases decide WHEN it runs (CONTRACTS §8), the
AudioDeviceManager decides WHICH input it uses (ADR-012).
"""

from __future__ import annotations


class RecorderBackendError(Exception):
    """Raised when the configured recorder backend is unknown."""


class MockRecorder:
    """Deterministic recorder double: counts starts/stops, captures nothing."""

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.recording = False

    def start(self) -> None:
        if not self.recording:
            self.started += 1
            self.recording = True

    def stop(self) -> None:
        if self.recording:
            self.stopped += 1
            self.recording = False


def build_recorder(backend: str) -> MockRecorder:
    if backend == "mock":
        return MockRecorder()
    raise RecorderBackendError(
        f"Unknown recorder backend {backend!r}; only 'mock' exists until G4."
    )


__all__ = ["MockRecorder", "RecorderBackendError", "build_recorder"]
