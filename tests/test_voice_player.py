from __future__ import annotations

import math
import threading
import wave
from collections.abc import Callable
from io import BytesIO

import pytest

from dan.voice import player as player_module
from dan.voice.tts import SynthesizedChunk


def wav_bytes(duration_seconds: float, *, sample_rate: int = 1_000) -> bytes:
    frame_count = max(1, round(duration_seconds * sample_rate))
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frame_count)
    return output.getvalue()


def wav_chunk(text: str = "chunk", *, duration_seconds: float = 0.25) -> SynthesizedChunk:
    return SynthesizedChunk(text=text, audio=wav_bytes(duration_seconds))


class ScriptedCompletionWaiter:
    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.events: list[threading.Event] = []
        self.timeouts: list[float] = []

    def __call__(self, event: threading.Event, timeout: float) -> bool:
        self.events.append(event)
        self.timeouts.append(timeout)
        return self._results.pop(0)


class RecoveringFakeBackend:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.recover_calls = 0
        self.completed_plays = 0
        self.auto_complete = False
        self.play_failures: list[BaseException] = []
        self.completions: list[Callable[[], None]] = []
        self.stop_error: BaseException | None = None
        self.recover_error: BaseException | None = None

    def start(self) -> None:
        self.start_calls += 1

    def make_buffer(self, audio: bytes) -> bytes:
        return audio

    def play(self, buffer: bytes, completion: Callable[[], None]) -> None:
        self.completions.append(completion)
        if self.play_failures:
            raise self.play_failures.pop(0)
        self.completed_plays += 1
        if self.auto_complete:
            completion()

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error

    def recover(self) -> None:
        self.recover_calls += 1
        if self.recover_error is not None:
            raise self.recover_error


def test_wav_deadline_clamps_slack_but_remains_longer_than_media() -> None:
    deadlines = [
        player_module.playback_deadline_seconds(wav_bytes(0.25)),
        player_module.playback_deadline_seconds(wav_bytes(10.0)),
        player_module.playback_deadline_seconds(wav_bytes(120.0)),
    ]

    assert deadlines == [3.0, 22.0, 122.0]
    assert deadlines[-1] > 120.0
    assert 300.0 not in deadlines


@pytest.mark.parametrize("deadline", [0.0, -1.0, math.inf, math.nan])
def test_injected_audio_deadline_must_be_finite_and_positive(deadline: float) -> None:
    backend = RecoveringFakeBackend()
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: deadline,
    )

    with pytest.raises(player_module.CoreAudioPlayerError, match="deadline"):
        player.play(wav_chunk(), should_play=lambda: True, on_started=lambda: None)

    assert backend.start_calls == 0


def test_player_accepts_valid_deadline_longer_than_soft_cap() -> None:
    backend = RecoveringFakeBackend()
    backend.auto_complete = True
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: 122.0,
    )

    player.play(
        wav_chunk(duration_seconds=120.0),
        should_play=lambda: True,
        on_started=lambda: None,
    )

    assert backend.completed_plays == 1


def test_native_timeout_uses_injected_audio_deadline_and_fully_resets() -> None:
    backend = RecoveringFakeBackend()
    waiter = ScriptedCompletionWaiter([False, True])
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: 0.025,
        completion_waiter=waiter,
    )

    with pytest.raises(player_module.CoreAudioPlayerError, match="completion timed out"):
        player.play(wav_chunk("first"), should_play=lambda: True, on_started=lambda: None)

    assert waiter.timeouts == [0.025]
    assert backend.stop_calls == 1
    assert backend.recover_calls == 1
    assert player._started is False
    assert player._current_completion is None
    assert player._current_cancelled is False
    assert player._active_buffers == 0
    assert player._last_completed_at is None

    backend.auto_complete = True
    player.play(wav_chunk("second"), should_play=lambda: True, on_started=lambda: None)

    assert backend.start_calls == 2
    assert waiter.timeouts == [0.025, 0.025]


def test_native_route_loss_recovers_before_the_next_request() -> None:
    backend = RecoveringFakeBackend()
    backend.play_failures.append(player_module.NativePlaybackRouteLost("route lost"))
    backend.auto_complete = True
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: 0.025,
    )

    with pytest.raises(player_module.CoreAudioPlayerError, match="route lost"):
        player.play(wav_chunk("first"), should_play=lambda: True, on_started=lambda: None)

    assert backend.recover_calls == 1
    player.play(wav_chunk("second"), should_play=lambda: True, on_started=lambda: None)
    assert backend.start_calls == 2
    assert backend.completed_plays == 1


def test_late_completion_from_dead_backend_cannot_finish_the_next_request() -> None:
    backend = RecoveringFakeBackend()
    first_waiter = ScriptedCompletionWaiter([False])
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: 0.025,
        completion_waiter=first_waiter,
    )
    with pytest.raises(player_module.CoreAudioPlayerError):
        player.play(wav_chunk("first"), should_play=lambda: True, on_started=lambda: None)

    old_completion = backend.completions[0]
    second_event_seen: list[bool] = []

    def wait_for_second(event: threading.Event, timeout: float) -> bool:
        old_completion()
        second_event_seen.append(event.is_set())
        backend.completions[-1]()
        return event.is_set()

    player._completion_waiter = wait_for_second
    player.play(wav_chunk("second"), should_play=lambda: True, on_started=lambda: None)

    assert second_event_seen == [False]


def test_recovery_failure_still_leaves_player_stopped_for_retry() -> None:
    backend = RecoveringFakeBackend()
    backend.recover_error = RuntimeError("fresh graph failed")
    waiter = ScriptedCompletionWaiter([False, True])
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: 0.025,
        completion_waiter=waiter,
    )

    with pytest.raises(player_module.CoreAudioPlayerError, match="fresh graph failed"):
        player.play(wav_chunk("first"), should_play=lambda: True, on_started=lambda: None)

    assert player._started is False
    assert player._current_completion is None
    assert player._active_buffers == 0

    backend.recover_error = None
    backend.auto_complete = True
    player.play(wav_chunk("second"), should_play=lambda: True, on_started=lambda: None)
    assert backend.start_calls == 2


def test_native_stop_failure_recovers_graph_but_still_reports_owner_failure() -> None:
    backend = RecoveringFakeBackend()
    backend.auto_complete = True
    player = player_module.CoreAudioPlayer(backend=backend)
    player.play(wav_chunk(), should_play=lambda: True, on_started=lambda: None)
    backend.stop_error = RuntimeError("route vanished during stop")

    with pytest.raises(player_module.CoreAudioPlayerError, match="route vanished"):
        player.stop()

    assert backend.stop_calls == 1
    assert backend.recover_calls == 1
    assert player._started is False


def test_on_started_failure_stops_and_recovers_already_scheduled_audio() -> None:
    backend = RecoveringFakeBackend()
    player = player_module.CoreAudioPlayer(
        backend=backend,
        deadline_for_audio=lambda _audio: 0.025,
    )

    def fail_started_transition() -> None:
        raise RuntimeError("speaking transition failed")

    with pytest.raises(
        player_module.CoreAudioPlayerError,
        match="speaking transition failed",
    ):
        player.play(
            wav_chunk("scheduled"),
            should_play=lambda: True,
            on_started=fail_started_transition,
        )

    assert backend.completed_plays == 1
    assert backend.stop_calls == 1
    assert backend.recover_calls == 1
    assert player._started is False
    assert player._current_completion is None
    assert player._active_buffers == 0


def test_unproven_native_stop_is_sticky_until_recovery_succeeds() -> None:
    backend = RecoveringFakeBackend()
    backend.auto_complete = True
    player = player_module.CoreAudioPlayer(backend=backend)
    player.play(
        wav_chunk("first"),
        should_play=lambda: True,
        on_started=lambda: None,
    )
    backend.stop_error = RuntimeError("native stop unavailable")
    backend.recover_error = RuntimeError("native recovery unavailable")

    with pytest.raises(player_module.CoreAudioPlayerError, match="stop unavailable"):
        player.stop()

    starts_before_blocked_play = backend.start_calls
    with pytest.raises(player_module.CoreAudioPlayerError, match="unproven"):
        player.play(
            wav_chunk("forbidden"),
            should_play=lambda: True,
            on_started=lambda: None,
        )
    assert backend.start_calls == starts_before_blocked_play

    with pytest.raises(player_module.CoreAudioPlayerError, match="recovery unavailable"):
        player.stop()
    assert backend.stop_calls == 2
    assert backend.recover_calls == 2

    backend.stop_error = None
    backend.recover_error = None
    player.stop()
    player.play(
        wav_chunk("after-proof"),
        should_play=lambda: True,
        on_started=lambda: None,
    )

    assert backend.stop_calls == 3
    assert backend.recover_calls == 3
    assert backend.start_calls == starts_before_blocked_play + 1


class _ToggleNativeOwner:
    def __init__(self) -> None:
        self.fail = True

    def stop(self) -> None:
        if self.fail:
            raise RuntimeError("native stop failed")

    def reset(self) -> None:
        if self.fail:
            raise RuntimeError("native reset failed")


def test_failed_avfoundation_teardown_cannot_build_a_second_graph() -> None:
    backend = object.__new__(player_module._AVFoundationBackend)
    node = _ToggleNativeOwner()
    engine = _ToggleNativeOwner()
    backend._node = node
    backend._engine = engine
    backend._connected_format = object()
    built_graphs: list[bool] = []
    backend._build_graph = lambda: built_graphs.append(True)
    player = player_module.CoreAudioPlayer(backend=backend)
    player._started = True

    with pytest.raises(player_module.CoreAudioPlayerError, match="native stop failed"):
        player.stop()

    assert built_graphs == []
    assert backend._node is node
    assert backend._engine is engine
    assert player._ownership_blocked is True

    node.fail = False
    engine.fail = False
    player.stop()

    assert built_graphs == [True]
    assert player._ownership_blocked is False
