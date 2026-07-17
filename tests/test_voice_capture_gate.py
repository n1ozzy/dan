"""CaptureGate tests (G4b — mandatory pre-whisper energy/VAD filters).

Empirical fact (live inventory 2026-07-02): whisper hallucinates on silence
(3 s of digital silence → „Dziękuję." despite no_speech_threshold=0.6), so
captures MUST be filtered by our own energy/VAD gate BEFORE any model sees
them. The gate is pure and deterministic: synthetic PCM in, decision out.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from types import SimpleNamespace

from dan.voice.stt import MockSTTEngine
from dan.voice.vad import CaptureGate, analyze_capture, pcm_from_wav


SAMPLE_RATE = 16000


def pcm_silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def pcm_tone(seconds: float, *, amplitude: int = 8000, freq: float = 440.0) -> bytes:
    count = int(SAMPLE_RATE * seconds)
    return struct.pack(
        f"<{count}h",
        *(
            int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            for i in range(count)
        ),
    )


def as_wav(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()


def gate(**overrides) -> CaptureGate:
    values = {
        "recorder_sample_rate": SAMPLE_RATE,
        "stt_min_rms": 300,
        "stt_min_voiced_seconds": 0.3,
        "stt_min_voiced_ratio": 0.05,
    }
    values.update(overrides)
    return CaptureGate(config=SimpleNamespace(**values))


# --- WAV parsing --------------------------------------------------------------


def test_pcm_from_wav_strips_the_header() -> None:
    pcm = pcm_tone(0.1)
    assert pcm_from_wav(as_wav(pcm)) == pcm


def test_pcm_from_wav_passes_raw_pcm_through() -> None:
    pcm = pcm_tone(0.1)
    assert pcm_from_wav(pcm) == pcm


def test_pcm_from_wav_tolerates_a_killed_sox_header() -> None:
    # SIGKILL leaves sox no chance to fix up RIFF sizes; the payload after
    # the data chunk marker must still come out.
    pcm = pcm_tone(0.1)
    wav = bytearray(as_wav(pcm))
    wav[4:8] = b"\xff\xff\xff\xff"
    data_at = bytes(wav).find(b"data")
    wav[data_at + 4 : data_at + 8] = b"\xff\xff\xff\xff"
    assert pcm_from_wav(bytes(wav)) == pcm


def test_pcm_from_wav_header_only_is_empty() -> None:
    assert pcm_from_wav(as_wav(b"")) == b""


# --- analysis -----------------------------------------------------------------


def test_analyze_capture_measures_silence_as_unvoiced() -> None:
    stats = analyze_capture(as_wav(pcm_silence(1.0)), sample_rate=SAMPLE_RATE)
    assert stats.duration_seconds == 1.0
    assert stats.rms == 0
    assert stats.voiced_seconds == 0.0
    assert stats.voiced_ratio == 0.0


def test_analyze_capture_measures_a_tone_as_voiced() -> None:
    stats = analyze_capture(as_wav(pcm_tone(1.0)), sample_rate=SAMPLE_RATE)
    assert stats.duration_seconds == 1.0
    assert stats.rms > 5000
    assert stats.voiced_ratio > 0.9
    assert stats.voiced_seconds > 0.9


# --- gate ---------------------------------------------------------------------


def test_gate_rejects_empty_capture() -> None:
    decision = gate().evaluate(b"")
    assert not decision.accepted
    assert decision.reason == "empty"


def test_gate_rejects_pure_silence() -> None:
    # THE hallucination case: 3 s of digital silence must never reach whisper.
    decision = gate().evaluate(as_wav(pcm_silence(3.0)))
    assert not decision.accepted
    assert decision.reason == "too_quiet"


def test_gate_rejects_quiet_hum_below_threshold() -> None:
    decision = gate().evaluate(as_wav(pcm_tone(2.0, amplitude=100)))
    assert not decision.accepted
    assert decision.reason == "too_quiet"


def test_gate_rejects_a_blip_shorter_than_min_voiced_seconds() -> None:
    pcm = pcm_tone(0.1) + pcm_silence(0.9)
    decision = gate().evaluate(as_wav(pcm))
    assert not decision.accepted
    assert decision.reason == "too_quiet"


def test_gate_rejects_capture_shorter_than_min_capture_ms() -> None:
    decision = gate().evaluate(as_wav(pcm_tone(0.6)))

    assert not decision.accepted
    assert decision.reason == "too_short"


def test_min_capture_ms_comes_from_config() -> None:
    strict = gate(min_capture_ms=1200)
    assert strict.evaluate(as_wav(pcm_tone(1.0))).reason == "too_short"

    lenient = gate(min_capture_ms=500)
    assert lenient.evaluate(as_wav(pcm_tone(1.0))).accepted


def test_gate_rejects_sparse_voice_below_ratio() -> None:
    # Half a second of speech lost in a minute of silence: enough voiced
    # seconds, but so sparse it is noise pickup, not an utterance.
    pcm = pcm_tone(0.5) + pcm_silence(59.5)
    decision = gate(stt_min_voiced_ratio=0.05).evaluate(as_wav(pcm))
    assert not decision.accepted
    assert decision.reason == "sparse_voice"


def test_gate_accepts_speech_like_audio() -> None:
    decision = gate().evaluate(as_wav(pcm_tone(1.0)))
    assert decision.accepted
    assert decision.reason == "ok"
    assert decision.stats.voiced_seconds > 0.9


def test_gate_accepts_speech_with_leading_and_trailing_silence() -> None:
    pcm = pcm_silence(0.5) + pcm_tone(1.0) + pcm_silence(0.5)
    decision = gate().evaluate(as_wav(pcm))
    assert decision.accepted


def test_gate_thresholds_come_from_config() -> None:
    strict = gate(stt_min_voiced_seconds=2.0)
    assert not strict.evaluate(as_wav(pcm_tone(1.0))).accepted
    lenient = gate(stt_min_voiced_seconds=0.5)
    assert lenient.evaluate(as_wav(pcm_tone(1.0))).accepted


def test_too_short_capture_skips_stt_turn_and_barge_in(tmp_path) -> None:
    from dan.daemon.app import create_daemon_app
    from dan.voice.queue import VoiceQueue
    from tests.test_api_smoke import config_text

    config_path = tmp_path / "dan.toml"
    text = config_text(tmp_path / "home" / "dan.db").replace(
        "[voice]\nenabled = false", "[voice]\nenabled = true"
    )
    config_path.write_text(text, encoding="utf-8")
    app = create_daemon_app(config_path)
    app.start()
    try:
        assert app.conn is not None
        assert app.voice_stt is not None
        assert isinstance(app.voice_stt._engine, MockSTTEngine)
        queue = VoiceQueue(app.conn)
        pending = queue.enqueue(
            text="To ma zostać, bo za krótki capture nie jest barge-in.",
            turn_id="turn-pending",
            seq=0,
        )

        app.voice_stt.accept_capture(as_wav(pcm_tone(0.6)))
        assert app.voice_stt.flush(timeout=15)
        assert app.voice_gateway.flush(timeout=15)

        assert app.voice_stt._engine.calls == []
        assert app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
        assert app.conn.execute(
            "SELECT status FROM voice_queue WHERE id = ?",
            (pending.id,),
        ).fetchone() == ("queued",)
        assert app.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'voice.speak.cancelled'"
        ).fetchone()[0] == 0
    finally:
        app.close()
