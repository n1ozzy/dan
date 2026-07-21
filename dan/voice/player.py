"""The single long-lived CoreAudio playback owner."""

from __future__ import annotations

import math
import threading
import time
import wave
from array import array
from collections.abc import Callable
from io import BytesIO
from typing import Any, Protocol

from dan.audio.execution import assert_audio_execution_allowed
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


class NativePlaybackRouteLost(CoreAudioPlayerError):
    """Raised when the native output graph or device route is unusable."""


class _PlaybackStartCallbackFailed(CoreAudioPlayerError):
    """The buffer was scheduled but its durable started transition failed."""


class NativeAudioBackend(Protocol):
    def start(self) -> None: ...

    def is_running(self) -> bool: ...

    def make_buffer(self, audio: bytes) -> Any: ...

    def play(self, buffer: Any, completion: Callable[[], None]) -> None: ...

    def stop(self) -> None: ...

    def recover(self) -> None: ...


PLAYBACK_TIMEOUT_MULTIPLIER = 2.0
PLAYBACK_TIMEOUT_GRACE_SECONDS = 2.0
PLAYBACK_TIMEOUT_MIN_SECONDS = 3.0
PLAYBACK_TIMEOUT_SOFT_MAX_SECONDS = 60.0


def wav_duration_seconds(audio: bytes) -> float:
    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            frame_count = wav.getnframes()
            sample_rate = wav.getframerate()
    except (EOFError, TypeError, wave.Error) as exc:
        raise CoreAudioPlayerError(f"invalid WAV audio: {exc}") from exc
    if frame_count <= 0 or sample_rate <= 0:
        raise CoreAudioPlayerError("WAV audio must contain positive frames and sample rate")
    duration = frame_count / sample_rate
    if not math.isfinite(duration) or duration <= 0:
        raise CoreAudioPlayerError("WAV audio duration must be finite and positive")
    return duration


def playback_deadline_seconds(audio: bytes) -> float:
    duration = wav_duration_seconds(audio)
    return max(
        duration + PLAYBACK_TIMEOUT_GRACE_SECONDS,
        min(
            PLAYBACK_TIMEOUT_SOFT_MAX_SECONDS,
            max(
                PLAYBACK_TIMEOUT_MIN_SECONDS,
                duration * PLAYBACK_TIMEOUT_MULTIPLIER
                + PLAYBACK_TIMEOUT_GRACE_SECONDS,
            ),
        ),
    )


def wait_for_event(event: threading.Event, timeout_seconds: float) -> bool:
    return event.wait(timeout=timeout_seconds)


class CoreAudioPlayer:
    """Serialize WAV buffers through one daemon-lifetime native audio engine."""

    def __init__(
        self,
        *,
        backend: NativeAudioBackend | None = None,
        deadline_for_audio: Callable[[bytes], float] = playback_deadline_seconds,
        completion_waiter: Callable[[threading.Event, float], bool] = wait_for_event,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if backend is None:
            assert_audio_execution_allowed(operation="native backend construction")
            backend = _AVFoundationBackend()
        self._backend = backend
        self._deadline_for_audio = deadline_for_audio
        self._completion_waiter = completion_waiter
        self._monotonic = monotonic
        self._play_lock = threading.Lock()
        self._schedule_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._started = False
        self._current_completion: threading.Event | None = None
        self._current_generation: int | None = None
        self._generation = 0
        self._current_cancelled = False
        self._ownership_blocked = False
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
        assert_audio_execution_allowed(operation="coreaudio playback")
        try:
            deadline = float(self._deadline_for_audio(chunk.audio))
        except CoreAudioPlayerError:
            raise
        except Exception as exc:
            raise CoreAudioPlayerError(f"invalid playback deadline: {exc}") from exc
        if (
            not math.isfinite(deadline)
            or deadline <= 0
        ):
            raise CoreAudioPlayerError(
                "playback deadline must be finite and positive"
            )

        with self._play_lock:
            completion = threading.Event()
            generation: int | None = None
            recovered = False
            try:
                try:
                    with self._schedule_lock:
                        with self._state_lock:
                            if self._ownership_blocked:
                                raise CoreAudioPlayerError(
                                    "native audio ownership is unproven; "
                                    "stop recovery must succeed before playback"
                                )
                            started = self._started
                        # should_play() reaches SQLite (busy_timeout 5 s) in
                        # production. The CoreAudio completion thread waits on
                        # _state_lock while holding the engine's own mutex, so a
                        # database roundtrip under that lock stalls the realtime
                        # thread; _schedule_lock already serializes this.
                        if not should_play():
                            raise PlaybackCancelled(
                                f"playback skipped for {chunk.text!r}"
                            )
                        # macOS stops the engine on its own after an output-device
                        # change or sleep/wake, and a buffer scheduled on a stopped
                        # engine never fires its completion handler, so the backend
                        # outranks the _started flag. Every native call here stays
                        # outside _state_lock and inside _schedule_lock: the
                        # CoreAudio completion thread takes _state_lock while
                        # holding the engine's own mutex, so a native call under it
                        # deadlocks the playback owner. _schedule_lock still
                        # serializes this whole section against stop().
                        engine_alive = started and self._backend.is_running()
                        if not engine_alive:
                            self._backend.start()
                            # Commit the flag before anything else can raise.
                            # make_buffer rejects a malformed WAV with a plain
                            # CoreAudioPlayerError, which the except below does
                            # not catch; leaving _started False after a
                            # successful start makes stop() take its early
                            # return and never tear the engine down, so a
                            # running engine would hold the output route for the
                            # rest of the daemon's life.
                            with self._state_lock:
                                self._started = True
                                self.engine_start_count += 1
                        buffer = self._backend.make_buffer(chunk.audio)
                        with self._state_lock:
                            self._generation += 1
                            generation = self._generation
                            self._current_generation = generation
                            self._current_completion = completion
                            self._current_cancelled = False
                            self._active_buffers += 1
                            self.max_parallel_buffers = max(
                                self.max_parallel_buffers,
                                self._active_buffers,
                            )
                            started_at = self._monotonic()
                            if self._last_completed_at is not None:
                                self.measured_inter_chunk_gap_ms = max(
                                    0.0,
                                    (started_at - self._last_completed_at) * 1000,
                                )

                        def completed() -> None:
                            with self._state_lock:
                                active_generation = (
                                    self._current_generation == generation
                                    and self._current_completion is completion
                                )
                            if active_generation:
                                completion.set()

                        with self._state_lock:
                            cancelled_before_schedule = (
                                self._current_completion is not completion
                                or self._current_cancelled
                            )
                        if not cancelled_before_schedule:
                            cancelled_before_schedule = not should_play()
                        if cancelled_before_schedule:
                            raise PlaybackCancelled(
                                "playback interrupted before schedule for "
                                f"{chunk.text!r}"
                            )
                        self._backend.play(buffer, completed)
                        try:
                            on_started()
                        except Exception as exc:
                            raise _PlaybackStartCallbackFailed(
                                f"playback started transition failed: {exc}"
                            ) from exc
                except (NativePlaybackRouteLost, _PlaybackStartCallbackFailed) as exc:
                    recovered = True
                    raise self._recover_native_failure(exc) from exc

                if not self._completion_waiter(completion, deadline):
                    recovered = True
                    failure = CoreAudioPlayerError(
                        f"native playback completion timed out after {deadline:g} seconds"
                    )
                    raise self._recover_native_failure(failure) from failure

                with self._state_lock:
                    cancelled = self._current_cancelled
                if cancelled:
                    raise PlaybackCancelled(f"playback interrupted for {chunk.text!r}")
            finally:
                with self._state_lock:
                    if self._current_generation == generation:
                        self._active_buffers = max(0, self._active_buffers - 1)
                        self._current_completion = None
                        self._current_generation = None
                        self._current_cancelled = False
                        self._last_completed_at = (
                            None if recovered else self._monotonic()
                        )

    def _recover_native_failure(
        self,
        failure: BaseException,
    ) -> CoreAudioPlayerError:
        stop_error: BaseException | None = None
        recovery_error: BaseException | None = None
        with self._schedule_lock:
            with self._state_lock:
                self._generation += 1
                self._started = False
                self._current_completion = None
                self._current_generation = None
                self._current_cancelled = False
                self._active_buffers = 0
                self._last_completed_at = None
            try:
                self._backend.stop()
            except Exception as exc:
                stop_error = exc
            try:
                self._backend.recover()
            except Exception as exc:
                recovery_error = exc
            with self._state_lock:
                self._ownership_blocked = (
                    stop_error is not None and recovery_error is not None
                )

        details = [str(failure)]
        if stop_error is not None:
            details.append(f"native stop failed: {stop_error}")
        if recovery_error is not None:
            details.append(f"native recovery failed: {recovery_error}")
        return CoreAudioPlayerError("; ".join(details))

    def stop(self) -> None:
        with self._schedule_lock:
            with self._state_lock:
                completion = self._current_completion
                recovery_required = self._ownership_blocked
                if completion is None and not self._started and not recovery_required:
                    return
                if completion is not None:
                    self._current_cancelled = True
                self._started = False
            stop_error: Exception | None = None
            recovery_error: Exception | None = None
            try:
                self._backend.stop()
            except Exception as exc:
                stop_error = exc
            if recovery_required or stop_error is not None:
                try:
                    self._backend.recover()
                except Exception as recovery_exc:
                    recovery_error = recovery_exc
            with self._state_lock:
                if recovery_required:
                    self._ownership_blocked = recovery_error is not None
                else:
                    self._ownership_blocked = (
                        stop_error is not None and recovery_error is not None
                    )
            try:
                if stop_error is not None or recovery_error is not None:
                    details = []
                    if stop_error is not None:
                        details.append(f"native stop failed: {stop_error}")
                    if recovery_error is not None:
                        details.append(f"native recovery failed: {recovery_error}")
                    cause = stop_error or recovery_error
                    raise CoreAudioPlayerError("; ".join(details)) from cause
            finally:
                if completion is not None:
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
        assert_audio_execution_allowed(operation="native backend initialization")
        try:
            import AVFoundation
        except ImportError as exc:
            raise CoreAudioPlayerError(
                "CoreAudio playback requires pyobjc-framework-AVFoundation==12.2.1"
            ) from exc
        self._av = AVFoundation
        self._engine: Any | None = None
        self._node: Any | None = None
        self._connected_format: Any | None = None
        self._build_graph()

    def _build_graph(self) -> None:
        assert_audio_execution_allowed(operation="native graph initialization")
        engine = self._av.AVAudioEngine.alloc().init()
        node = self._av.AVAudioPlayerNode.alloc().init()
        engine.attachNode_(node)
        self._engine = engine
        self._node = node
        # The node is connected lazily, per buffer format: CoreAudio aborts a
        # schedule whose channel count differs from the node's output
        # connection ("_outputFormat.channelCount == buffer.format.channelCount"),
        # and the engine-default connection is the stereo device format while
        # synthesized WAVs are mono. The mixer upmixes/resamples to the device.
        self._connected_format = None

    def is_running(self) -> bool:
        # Guarded like every other native boundary here, and deliberately
        # outside the try: the kill switch must surface as
        # AudioExecutionDisabled, not get rewritten into a recoverable route
        # loss by the except below.
        assert_audio_execution_allowed(operation="native route liveness probe")
        try:
            engine = self._engine
            if engine is None:
                return False
            return bool(engine.isRunning())
        except Exception as exc:
            # A failing bridge call is a lost route, not a stopped engine:
            # reporting False would silently restart on every chunk forever
            # and bury the real fault.
            raise NativePlaybackRouteLost(
                f"native audio route liveness check failed: {exc}"
            ) from exc

    def start(self) -> None:
        assert_audio_execution_allowed(operation="native route start")
        try:
            if self._engine is None:
                raise RuntimeError("native audio graph is unavailable")
            # Apple documents that a configuration change leaves nodes
            # "attached and connected with previously set formats", so the
            # connection usually survives — but the format cache is the only
            # record of it, and a restart is when that record is least
            # trustworthy: every synthesized WAV carries the same format, so a
            # stale cache makes _ensure_connected short-circuit and schedule
            # onto whatever the node is actually wired to. Rebuild explicitly,
            # disconnecting first. stop() never disconnects, so after an idle
            # stop the node is still wired and _ensure_connected disconnects
            # only when it holds a cached format; clearing alone would hand
            # connect_to_format_ an already-connected node, which lands it on
            # the mixer's nextAvailableInputBus instead of the bus it just
            # vacated.
            if self._connected_format is not None and self._node is not None:
                self._engine.disconnectNodeOutput_(self._node)
            self._connected_format = None
            # Touching mainMixerNode materializes the mixer -> output path so the
            # engine can start before the first player-node connection exists.
            self._engine.mainMixerNode()
            result = self._engine.startAndReturnError_(None)
            success = result[0] if isinstance(result, tuple) else result
            if not success:
                error = (
                    result[1]
                    if isinstance(result, tuple) and len(result) > 1
                    else None
                )
                raise RuntimeError(f"AVAudioEngine failed to start: {error}")
            if self._node is not None:
                # Stopping the engine is not documented to reset the player
                # node, so isPlaying() can still report YES for a route macOS
                # tore down. play() only calls node.play() when isPlaying() is
                # false, so a stale YES means the node is never re-armed and
                # the buffer never renders — the same silent timeout this
                # restart exists to end. Re-arm before the caller schedules.
                self._node.stop()
        except NativePlaybackRouteLost:
            raise
        except Exception as exc:
            raise NativePlaybackRouteLost(f"native audio route start failed: {exc}") from exc

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

        try:
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
                mono = array(
                    "f",
                    (value * scale for value in samples[channel_index::channels]),
                )
                channel = channel_data[channel_index]
                try:
                    view = memoryview(channel.as_buffer(len(mono) * 4)).cast("B")
                    view[: len(mono) * 4] = memoryview(mono.tobytes())
                except (AttributeError, TypeError, ValueError):
                    for frame_index, value in enumerate(mono):
                        channel[frame_index] = value
        except Exception as exc:
            raise NativePlaybackRouteLost(
                f"native audio buffer construction failed: {exc}"
            ) from exc
        return buffer

    def _ensure_connected(self, audio_format: Any) -> None:
        if self._engine is None or self._node is None:
            raise NativePlaybackRouteLost("native audio graph is unavailable")
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
        assert_audio_execution_allowed(operation="native buffer scheduling")
        try:
            if self._node is None:
                raise RuntimeError("native audio node is unavailable")
            self._ensure_connected(buffer.format())
            self._node.scheduleBuffer_completionHandler_(buffer, completion)
            if not self._node.isPlaying():
                self._node.play()
        except NativePlaybackRouteLost:
            raise
        except Exception as exc:
            raise NativePlaybackRouteLost(
                f"native audio route scheduling failed: {exc}"
            ) from exc

    def stop(self) -> None:
        failures: list[str] = []
        if self._node is not None:
            for method_name in ("stop", "reset"):
                try:
                    getattr(self._node, method_name)()
                except Exception as exc:
                    failures.append(f"node.{method_name}: {exc}")
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception as exc:
                failures.append(f"engine.stop: {exc}")
        if failures:
            raise NativePlaybackRouteLost(
                "native audio graph stop failed: " + "; ".join(failures)
            )

    def recover(self) -> None:
        old_node, old_engine = self._node, self._engine
        failures: list[str] = []
        for owner_name, owner, methods in (
            ("node", old_node, ("stop", "reset")),
            ("engine", old_engine, ("stop", "reset")),
        ):
            if owner is None:
                continue
            for method_name in methods:
                method = getattr(owner, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception as exc:
                        failures.append(f"{owner_name}.{method_name}: {exc}")
        if failures:
            raise NativePlaybackRouteLost(
                "native audio graph teardown failed: " + "; ".join(failures)
            )
        self._node = None
        self._engine = None
        self._connected_format = None
        try:
            self._build_graph()
        except Exception as exc:
            self._node = None
            self._engine = None
            self._connected_format = None
            raise NativePlaybackRouteLost(
                f"native audio graph recovery failed: {exc}"
            ) from exc


__all__ = [
    "AudioPlayer",
    "CoreAudioPlayer",
    "CoreAudioPlayerError",
    "MockAudioPlayer",
    "NativeAudioBackend",
    "NativePlaybackRouteLost",
    "playback_deadline_seconds",
    "wav_duration_seconds",
]
