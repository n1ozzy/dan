"""Audio device manager placeholder."""

from __future__ import annotations

from jarvis.audio.models import AudioDeviceState


class AudioDeviceManager:
    def current(self) -> AudioDeviceState:
        raise NotImplementedError("audio device inspection is not implemented yet")

    def select(self, input_device: str | None = None, output_device: str | None = None) -> None:
        raise NotImplementedError("audio device selection is not implemented yet")
