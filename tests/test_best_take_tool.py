from __future__ import annotations

import numpy as np

from tools.jarvis.best_take import TakeResult, choose_best, render_take


class RandomFakeTTS:
    sample_rate = 44_100

    def synthesize(self, text: str, **kwargs: object):
        assert text and kwargs["voice_style"] is not None
        return np.random.randn(1, 4096).astype(np.float32), np.array([0.1])


def test_offline_take_renderer_uses_the_canonical_seed_contract(tmp_path) -> None:
    tts = RandomFakeTTS()
    common = {
        "tts": tts,
        "text": "Ten sam kandydat.",
        "style": object(),
        "speed": 1.25,
        "steps": 18,
        "lang": "pl",
        "max_chunk_length": 400,
    }

    first = render_take(seed=17, output=tmp_path / "a.wav", **common)
    second = render_take(seed=17, output=tmp_path / "b.wav", **common)
    other = render_take(seed=91, output=tmp_path / "c.wav", **common)

    assert first.wav_sha256 == second.wav_sha256
    assert first.wav_sha256 != other.wav_sha256


def test_best_take_ranking_is_stable_on_score_ties(tmp_path) -> None:
    low_seed = TakeResult(8.5, 17, tmp_path / "17.wav", {}, "a" * 64)
    high_seed = TakeResult(8.5, 91, tmp_path / "91.wav", {}, "b" * 64)
    lower_score = TakeResult(8.4, 1, tmp_path / "1.wav", {}, "c" * 64)

    assert choose_best([high_seed, lower_score, low_seed]) is low_seed
