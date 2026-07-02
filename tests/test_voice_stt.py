"""STT engine tests (G4b — MLX whisper by decree §7.4, mock for tests).

No real whisper ever runs here: the MLX engine's model call is patched and
only the machinery around it is tested — the §4a fact that MLX inference
must live on ONE dedicated thread, transient WAV hygiene, and fail-at-
startup construction. Real transcription is the G4 live gate's job.
"""

from __future__ import annotations

import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.voice.stt import (
    MlxWhisperEngine,
    MockSTTEngine,
    STTEngineError,
    build_stt_engine,
)


def mlx_config(tmp_path: Path, **voice_overrides) -> SimpleNamespace:
    voice = {
        "default_stt": "mlx_whisper",
        "stt_model": "mlx-community/whisper-large-v3-turbo",
        "stt_language": "pl",
    }
    voice.update(voice_overrides)
    return SimpleNamespace(
        voice=SimpleNamespace(**voice),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )


# --- build_stt_engine ---------------------------------------------------------


def test_build_mock_engine() -> None:
    engine = build_stt_engine("mock")
    assert isinstance(engine, MockSTTEngine)
    assert engine.name == "mock"


def test_build_unknown_engine_raises() -> None:
    with pytest.raises(STTEngineError, match="Unknown"):
        build_stt_engine("whispercpp")


def test_build_mlx_whisper_without_config_raises() -> None:
    with pytest.raises(STTEngineError, match="config"):
        build_stt_engine("mlx_whisper")


def test_build_mlx_whisper_accepts_hyphenated_name(tmp_path: Path) -> None:
    engine = build_stt_engine("mlx-whisper", config=mlx_config(tmp_path))
    try:
        assert isinstance(engine, MlxWhisperEngine)
        assert engine.name == "mlx_whisper"
    finally:
        engine.stop()


def test_mlx_whisper_missing_package_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.voice.stt._mlx_whisper_available", lambda: False)
    with pytest.raises(STTEngineError, match="mlx_whisper"):
        build_stt_engine("mlx_whisper", config=mlx_config(tmp_path))


# --- MockSTTEngine ------------------------------------------------------------


def test_mock_engine_returns_preset_transcript_and_logs_calls() -> None:
    engine = MockSTTEngine(transcript="Zrób mi kawę.")
    assert engine.transcribe(b"\x00" * 100) == "Zrób mi kawę."
    assert engine.calls == [100]


def test_mock_engine_default_transcript_carries_a_fake_secret() -> None:
    # The established fixture rule: every smoke run that persists a mock
    # transcript must prove redaction at rest, so the default carries sk-*.
    engine = MockSTTEngine()
    assert "sk-" in engine.transcribe(b"audio")


# --- MlxWhisperEngine ---------------------------------------------------------


def test_mlx_engine_runs_all_inference_on_one_dedicated_thread(tmp_path: Path) -> None:
    # §4a fact: MLX holds model+stream per thread — every transcribe call
    # must execute on the same dedicated thread, never the caller's.
    engine = build_stt_engine("mlx_whisper", config=mlx_config(tmp_path))
    seen: list[int] = []

    def fake_model(path: str) -> dict:
        seen.append(threading.get_ident())
        return {"text": " Rozpoznany tekst. "}

    engine._run_model = fake_model  # type: ignore[method-assign]
    try:
        assert engine.transcribe(b"a" * 2000) == "Rozpoznany tekst."
        assert engine.transcribe(b"b" * 2000) == "Rozpoznany tekst."
        assert len(set(seen)) == 1
        assert seen[0] != threading.get_ident()
    finally:
        engine.stop()


def test_mlx_engine_gives_the_model_a_private_wav_then_cleans(tmp_path: Path) -> None:
    engine = build_stt_engine("mlx_whisper", config=mlx_config(tmp_path))
    observed: list[tuple[bytes, int]] = []

    def fake_model(path: str) -> dict:
        p = Path(path)
        observed.append((p.read_bytes(), stat.S_IMODE(p.stat().st_mode)))
        assert p.parent == Path(engine.workdir)
        return {"text": "ok"}

    engine._run_model = fake_model  # type: ignore[method-assign]
    try:
        engine.transcribe(b"RIFFxxxxWAVEdata")
        assert observed == [(b"RIFFxxxxWAVEdata", 0o600)]
        assert list(Path(engine.workdir).glob("stt-*.wav")) == []
    finally:
        engine.stop()


def test_mlx_engine_model_failure_raises_engine_error_and_cleans(tmp_path: Path) -> None:
    engine = build_stt_engine("mlx_whisper", config=mlx_config(tmp_path))

    def fake_model(path: str) -> dict:
        raise RuntimeError("metal exploded")

    engine._run_model = fake_model  # type: ignore[method-assign]
    try:
        with pytest.raises(STTEngineError, match="metal exploded"):
            engine.transcribe(b"audio-bytes")
        assert list(Path(engine.workdir).glob("stt-*.wav")) == []
    finally:
        engine.stop()
