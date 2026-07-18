"""The single long-lived CoreAudio playback owner."""

from __future__ import annotations

import threading
import time
import wave
from array import array
from collections.abc import Callable
from io import BytesIO
from typing import Any, Protocol

from dan.voice.tts import PlaybackCancelled, SynthesizedChunk


class AudioPlayer(Protocol):
    def play(
        self,
        chunk: SynthesizedChunk,
        *,
        should_play: Callable[[], bool],
        on_started: Callable[[], None],
    ) -> None: ...

    def stop(self) -> None: ...


class CoreAudioPlayerError(RuntimeError):
    """Raised when CoreAudio cannot decode, schedule, or complete a buffer."""


class CoreAudioPlayer:
    """Serialize WAV buffers through one daemon-lifetime native audio engine."""

    def __init__(self, *, backend: Any | None = None) -> None:
        self._backend = backend or _AVFoundationBackend()
        self._play_lock = threading.Lock()
        self._schedule_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._started = False
        self._current_completion: threading.Event | None = None
        self._current_cancelled = False
        self._active_buffers = 0
        self._last_completed_at: float | None = None
        self.engine_start_count = 0
        self.max_parallel_buffers = 0
        self.measured_inter_chunk_gap_ms = 0.0

    def play(
        self,
        chunk: SynthesizedChunk,
        *,
        should_play: Callable[[], bool],
        on_started: Callable[[], None],
    ) -> None:
        with self._play_lock:
            with self._state_lock:
                if not should_play():
                    raise PlaybackCancelled(f"playback skipped for {chunk.text!r}")
                if not self._started:
                    self._backend.start()
                    self._started = True
                    self.engine_start_count += 1
                buffer = self._backend.make_buffer(chunk.audio)
                completion = threading.Event()
                self._current_completion = completion
                self._current_cancelled = False
                self._active_buffers += 1
                self.max_parallel_buffers = max(
                    self.max_parallel_buffers,
                    self._active_buffers,
                )
                started_at = time.monotonic()
                if self._last_completed_at is not None:
                    self.measured_inter_chunk_gap_ms = max(
                        0.0,
                        (started_at - self._last_completed_at) * 1000,
                    )

            def completed() -> None:
                completion.set()

            try:
                with self._schedule_lock:
                    with self._state_lock:
                        cancelled_before_schedule = (
                            self._current_completion is not completion
                            or self._current_cancelled
                            or not should_play()
                        )
                    if cancelled_before_schedule:
                        raise PlaybackCancelled(
                            f"playback interrupted before schedule for {chunk.text!r}"
                        )
                    self._backend.play(buffer, completed)
                    # Telemetry truth: playback "started" only once the buffer
                    # was actually handed to the backend. A failed schedule
                    # must never leave a phantom 'speaking' row behind.
                    on_started()
                if not completion.wait(timeout=300):
                    self.stop()
                    raise CoreAudioPlayerError("native playback completion timed out")
                with self._state_lock:
                    cancelled = self._current_cancelled
                if cancelled:
                    raise PlaybackCancelled(f"playback interrupted for {chunk.text!r}")
            finally:
                with self._state_lock:
                    self._active_buffers = max(0, self._active_buffers - 1)
                    self._current_completion = None
                    self._last_completed_at = time.monotonic()

    def stop(self) -> None:
        with self._schedule_lock:
            with self._state_lock:
                completion = self._current_completion
                if completion is None:
                    return
                self._current_cancelled = True
                self._backend.stop()
                completion.set()


class MockAudioPlayer:
    """Deterministic no-audio player used at the external audio edge."""

    def __init__(self, *, play_gate: threading.Event | None = None) -> None:
        self.log: list[tuple[str, str]] = []
        self.max_parallel_buffers = 0
        self.started = threading.Event()
        self._play_gate = play_gate
        self._lock = threading.Lock()
        self._interrupt: threading.Event | None = None
        self._active = 0

    def play(
        self,
        chunk: SynthesizedChunk,
        *,
        should_play: Callable[[], bool],
        on_started: Callable[[], None],
    ) -> None:
        interrupt = threading.Event()
        with self._lock:
            if not should_play():
                raise PlaybackCancelled(f"playback skipped for {chunk.text!r}")
            self._interrupt = interrupt
            self._active += 1
            self.max_parallel_buffers = max(self.max_parallel_buffers, self._active)
        try:
            # Mirror CoreAudioPlayer's schedule contract: the predicate is
            # re-checked and on_started fires only AFTER the pre-schedule
            # gate passed, so the mock may never be laxer than production.
            if not should_play():
                self.log.append(("play_interrupted", chunk.text))
                raise PlaybackCancelled(
                    f"playback interrupted before schedule for {chunk.text!r}"
                )
            on_started()
            self.started.set()
            if self._play_gate is not None:
                while not self._play_gate.wait(timeout=0.005):
                    if interrupt.is_set():
                        self.log.append(("play_interrupted", chunk.text))
                        raise PlaybackCancelled(f"playback interrupted for {chunk.text!r}")
            if interrupt.is_set():
                self.log.append(("play_interrupted", chunk.text))
                raise PlaybackCancelled(f"playback interrupted for {chunk.text!r}")
            self.log.append(("play", chunk.text))
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)
                self._interrupt = None

    def stop(self) -> None:
        with self._lock:
            interrupt = self._interrupt
        if interrupt is not None:
            interrupt.set()


class _AVFoundationBackend:
    """Thin PyObjC boundary; imported lazily so non-audio tests stay hermetic."""

    def __init__(self) -> None:
        try:
            import AVFoundation
        except ImportError as exc:
            raise CoreAudioPlayerError(
                "CoreAudio playback requires pyobjc-framework-AVFoundation==12.2.1"
            ) from exc
        self._av = AVFoundation
        self._engine = AVFoundation.AVAudioEngine.alloc().init()
        self._node = AVFoundation.AVAudioPlayerNode.alloc().init()
        self._engine.attachNode_(self._node)
        # The node is connected lazily, per buffer format: CoreAudio aborts a
        # schedule whose channel count differs from the node's output
        # connection ("_outputFormat.channelCount == buffer.format.channelCount"),
        # and the engine-default connection is the stereo device format while
        # synthesized WAVs are mono. The mixer upmixes/resamples to the device.
        self._connected_format = None

    def start(self) -> None:
        # Touching mainMixerNode materializes the mixer -> output path so the
        # engine can start before the first player-node connection exists.
        self._engine.mainMixerNode()
        result = self._engine.startAndReturnError_(None)
        success = result[0] if isinstance(result, tuple) else result
        if not success:
            error = result[1] if isinstance(result, tuple) and len(result) > 1 else None
            raise CoreAudioPlayerError(f"AVAudioEngine failed to start: {error}")

    def make_buffer(self, audio: bytes):
        try:
            with wave.open(BytesIO(audio), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                sample_rate = wav.getframerate()
                frame_count = wav.getnframes()
                pcm = wav.readframes(frame_count)
        except (EOFError, wave.Error) as exc:
            raise CoreAudioPlayerError(f"invalid WAV audio: {exc}") from exc
        if sample_width != 2 or channels <= 0 or frame_count <= 0:
            raise CoreAudioPlayerError("CoreAudio player requires non-empty PCM16 WAV")

        audio_format = (
            self._av.AVAudioFormat.alloc()
            .initWithCommonFormat_sampleRate_channels_interleaved_(
                self._av.AVAudioPCMFormatFloat32,
                float(sample_rate),
                channels,
                False,
            )
        )
        buffer = self._av.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(
            audio_format,
            frame_count,
        )
        buffer.setFrameLength_(frame_count)
        samples = array("h")
        samples.frombytes(pcm)
        scale = 1.0 / 32768.0
        channel_data = buffer.floatChannelData()
        for channel_index in range(channels):
            mono = array("f", (value * scale for value in samples[channel_index::channels]))
            channel = channel_data[channel_index]
            try:
                view = memoryview(channel.as_buffer(len(mono) * 4)).cast("B")
                view[: len(mono) * 4] = memoryview(mono.tobytes())
            except (AttributeError, TypeError, ValueError):
                for frame_index, value in enumerate(mono):
                    channel[frame_index] = value
        return buffer

    def _ensure_connected(self, audio_format: Any) -> None:
        current = self._connected_format
        if current is not None and current.isEqual_(audio_format):
            return
        if current is not None:
            self._engine.disconnectNodeOutput_(self._node)
        self._engine.connect_to_format_(
            self._node,
            self._engine.mainMixerNode(),
            audio_format,
        )
        self._connected_format = audio_format

    def play(self, buffer: Any, completion: Callable[[], None]) -> None:
        self._ensure_connected(buffer.format())
        self._node.scheduleBuffer_completionHandler_(buffer, completion)
        if not self._node.isPlaying():
            self._node.play()

    def stop(self) -> None:
        self._node.stop()
        self._node.reset()


__all__ = [
    "AudioPlayer",
    "CoreAudioPlayer",
    "CoreAudioPlayerError",
    "MockAudioPlayer",
]
