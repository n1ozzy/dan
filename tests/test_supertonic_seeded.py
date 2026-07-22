from __future__ import annotations

import threading

import numpy as np
import pytest

from dan.voice.supertonic_seeded import (
    SeedValidationError,
    synthesize_seeded,
)


class RandomFakeTTS:
    sample_rate = 44_100

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(self, text: str, **kwargs):
        self.calls.append({"text": text, **kwargs})
        return np.random.randn(1, 256).astype(np.float32), np.array([0.1])


def test_unseeded_supertonic_rng_changes_consecutive_identical_renders() -> None:
    tts = RandomFakeTTS()
    np.random.seed(20260722)

    first, _ = tts.synthesize("To samo wejście.", voice_style=object())
    second, _ = tts.synthesize("To samo wejście.", voice_style=object())

    assert first.tobytes() != second.tobytes()


def test_same_seed_is_bit_identical_and_different_seed_differs() -> None:
    tts = RandomFakeTTS()
    kwargs = {
        "text": "Identyczny render.",
        "voice_style": object(),
        "lang": "pl",
        "speed": 1.25,
        "total_steps": 14,
        "max_chunk_length": 400,
        "silence_duration": 0.0,
    }

    first, _ = synthesize_seeded(tts, seed=17, lock=threading.Lock(), **kwargs)
    second, _ = synthesize_seeded(tts, seed=17, lock=threading.Lock(), **kwargs)
    other, _ = synthesize_seeded(tts, seed=91, lock=threading.Lock(), **kwargs)

    assert first.tobytes() == second.tobytes()
    assert first.tobytes() != other.tobytes()
    expected = {
        "text": "Identyczny render.",
        **{key: value for key, value in kwargs.items() if key != "text"},
    }
    assert tts.calls == [expected] * 3


@pytest.mark.parametrize("seed", [True, -1, 2**32, 1.5, "17"])
def test_seed_validation_is_strict(seed: object) -> None:
    with pytest.raises(SeedValidationError, match="seed"):
        synthesize_seeded(
            RandomFakeTTS(),
            text="Nie losuj.",
            voice_style=object(),
            seed=seed,
            lock=threading.Lock(),
        )


def test_seed_is_applied_inside_lock_immediately_before_synthesize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Lock:
        def __enter__(self):
            events.append("lock")

        def __exit__(self, *args):
            events.append("unlock")

    class TTS(RandomFakeTTS):
        def synthesize(self, text: str, **kwargs):
            events.append("synthesize")
            return super().synthesize(text, **kwargs)

    real_seed = np.random.seed

    def tracked_seed(value: int) -> None:
        events.append(f"seed:{value}")
        real_seed(value)

    monkeypatch.setattr(np.random, "seed", tracked_seed)

    synthesize_seeded(
        TTS(),
        text="Kolejność ma znaczenie.",
        voice_style=object(),
        seed=42,
        lock=Lock(),
    )

    assert events == ["lock", "seed:42", "synthesize", "unlock"]
