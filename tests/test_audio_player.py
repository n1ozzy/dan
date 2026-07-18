from __future__ import annotations

import threading
import time

import pytest

from dan.voice.player import CoreAudioPlayer
from dan.voice.tts import PlaybackCancelled, SynthesizedChunk


class FakeCoreAudioBackend:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.started = 0
        self.active_buffers = 0
        self.max_active_buffers = 0
        self.stop_calls = 0
        self.audio: list[bytes] = []
        self.playing = threading.Event()
        self.release = threading.Event()

    def start(self) -> None:
        self.started += 1

    def make_buffer(self, audio: bytes):
        return bytes(audio)

    def play(self, buffer: bytes, completion) -> None:
        self.active_buffers += 1
        self.max_active_buffers = max(self.max_active_buffers, self.active_buffers)
        self.audio.append(buffer)
        self.playing.set()
        if self.block:
            def complete_later() -> None:
                self.release.wait(timeout=5)
                self.active_buffers = max(0, self.active_buffers - 1)
                completion()

            threading.Thread(target=complete_later, daemon=True).start()
            return
        self.active_buffers = max(0, self.active_buffers - 1)
        completion()

    def stop(self) -> None:
        self.stop_calls += 1
        self.active_buffers = 0
        self.release.set()


def chunks() -> list[SynthesizedChunk]:
    return [
        SynthesizedChunk(text=f"chunk-{index}", audio=b"RIFF" + bytes([index]) * 128)
        for index in range(3)
    ]


def test_multiple_chunks_reuse_one_coreaudio_engine() -> None:
    backend = FakeCoreAudioBackend()
    player = CoreAudioPlayer(backend=backend)

    for chunk in chunks():
        player.play(chunk, should_play=lambda: True, on_started=lambda: None)

    assert player.engine_start_count == 1
    assert backend.started == 1
    assert player.max_parallel_buffers == 1
    assert backend.max_active_buffers == 1
    assert player.measured_inter_chunk_gap_ms < 80


def test_should_play_gate_prevents_any_native_schedule() -> None:
    backend = FakeCoreAudioBackend()
    player = CoreAudioPlayer(backend=backend)

    with pytest.raises(PlaybackCancelled):
        player.play(chunks()[0], should_play=lambda: False, on_started=lambda: None)

    assert backend.started == 0
    assert backend.audio == []


def test_stop_interrupts_current_buffer_and_leaves_no_audio_tail() -> None:
    backend = FakeCoreAudioBackend(block=True)
    player = CoreAudioPlayer(backend=backend)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            player.play(chunks()[0], should_play=lambda: True, on_started=lambda: None)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert backend.playing.wait(timeout=2)

    player.stop()
    thread.join(timeout=2)
    time.sleep(0.01)

    assert not thread.is_alive()
    assert errors and isinstance(errors[0], PlaybackCancelled)
    assert backend.stop_calls == 1
    assert backend.active_buffers == 0


def test_stop_between_db_start_and_native_schedule_never_starts_audio() -> None:
    backend = FakeCoreAudioBackend()
    player = CoreAudioPlayer(backend=backend)

    with pytest.raises(PlaybackCancelled):
        player.play(
            chunks()[0],
            should_play=lambda: True,
            on_started=player.stop,
        )

    assert backend.audio == []
    assert backend.active_buffers == 0


def test_only_native_completion_returns_success() -> None:
    backend = FakeCoreAudioBackend()
    player = CoreAudioPlayer(backend=backend)
    started: list[str] = []

    player.play(chunks()[0], should_play=lambda: True, on_started=lambda: started.append("yes"))

    assert started == ["yes"]
    assert backend.audio == [chunks()[0].audio]
