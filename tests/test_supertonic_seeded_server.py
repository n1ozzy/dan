from __future__ import annotations

import threading

import numpy as np
import pytest

from dan.voice.supertonic_seeded import SeededServerState, create_app

fastapi_testclient = pytest.importorskip("fastapi.testclient")


class RandomFakeTTS:
    sample_rate = 44_100
    voice_style_names = ["M3"]

    def get_voice_style(self, _name: str) -> object:
        return object()

    def synthesize(self, text: str, **_kwargs: object):
        assert text
        return np.random.randn(1, 4096).astype(np.float32), np.array([0.1])


def test_seeded_http_contract_is_reproducible_and_strict() -> None:
    state = SeededServerState(tts=RandomFakeTTS(), synth_lock=threading.Lock())
    body = {
        "text": "Identyczny render.",
        "voice": "M3",
        "lang": "pl",
        "speed": 1.25,
        "steps": 14,
        "max_chunk_length": 400,
        "silence_duration": 0.0,
        "seed": 17,
    }

    with fastapi_testclient.TestClient(create_app(state=state)) as client:
        health = client.get("/v1/health")
        first = client.post("/v1/tts", json=body)
        second = client.post("/v1/tts", json=body)
        other = client.post("/v1/tts", json={**body, "seed": 91})
        boolean = client.post("/v1/tts", json={**body, "seed": True})

    assert health.status_code == 200
    assert health.headers["X-DAN-Seed-Protocol"] == "1"
    assert first.status_code == second.status_code == other.status_code == 200
    assert first.headers["X-DAN-Synthesis-Seed"] == "17"
    assert first.content == second.content
    assert first.content != other.content
    assert boolean.status_code == 422
