"""TranscriptionPipeline tests (G4b — capture → filters → STT → event).

The pipeline is the mandatory hallucination firewall in front of whisper:
the energy/VAD gate drops silence BEFORE any model runs, and the junk-
phrase filter drops whisper's silence-hallucinations („Dziękuję." — fact
confirmed live) AFTER it. Only a surviving transcript becomes the reserved
`input.voice.transcribed` event. Engines are mocks; nothing real runs.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from jarvis.store.db import close_quietly, connect_db, initialize_database
from jarvis.voice.stt import MockSTTEngine
from jarvis.voice.transcription import TranscriptionPipeline
from tests.test_voice_capture_gate import as_wav, pcm_silence, pcm_tone


def pipeline_config(**overrides) -> SimpleNamespace:
    values = {
        "recorder_sample_rate": 16000,
        "stt_min_rms": 300,
        "stt_min_voiced_seconds": 0.3,
        "stt_min_voiced_ratio": 0.05,
        "stt_junk_phrases": ("dziękuję", "dziękuję za oglądanie", "thanks for watching"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RecordingEngine(MockSTTEngine):
    """Mock engine that also records the transcribing thread."""

    def __init__(self, transcript: str = "Prawdziwe zdanie użytkownika.") -> None:
        super().__init__(transcript=transcript)
        self.threads: list[int] = []

    def transcribe(self, audio: bytes) -> str:
        self.threads.append(threading.get_ident())
        return super().transcribe(audio)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "stt.db"
    close_quietly(initialize_database(path))
    return path


def factory_for(db_path: Path) -> Callable[[], sqlite3.Connection]:
    return lambda: connect_db(db_path)


def transcribed_events(db_path: Path) -> list[dict]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type = 'input.voice.transcribed' ORDER BY id"
        ).fetchall()
        return [json.loads(str(row[0])) for row in rows]
    finally:
        close_quietly(conn)


def run_pipeline(
    db_path: Path,
    engine,
    *,
    on_transcript=None,
    **config_overrides,
) -> TranscriptionPipeline:
    return TranscriptionPipeline(
        factory_for(db_path),
        config=pipeline_config(**config_overrides),
        engine=engine,
        on_transcript=on_transcript,
    )


SPEECH = as_wav(pcm_tone(1.0))
SILENCE = as_wav(pcm_silence(3.0))


# --- gate before the model ------------------------------------------------------


def test_silence_never_reaches_the_engine(db_path: Path) -> None:
    engine = RecordingEngine()
    got: list[str] = []
    pipeline = run_pipeline(db_path, engine, on_transcript=got.append)
    try:
        pipeline.accept_capture(SILENCE)
        assert pipeline.flush()
        assert engine.calls == []
        assert got == []
        assert transcribed_events(db_path) == []
    finally:
        pipeline.stop()


def test_speech_is_transcribed_and_becomes_the_reserved_event(db_path: Path) -> None:
    engine = RecordingEngine()
    got: list[str] = []
    pipeline = run_pipeline(db_path, engine, on_transcript=got.append)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert len(engine.calls) == 1
        assert got == ["Prawdziwe zdanie użytkownika."]
        events = transcribed_events(db_path)
        assert len(events) == 1
        assert events[0]["text"] == "Prawdziwe zdanie użytkownika."
        assert events[0]["engine"] == "mock"
        assert events[0]["duration_seconds"] > 0.9
    finally:
        pipeline.stop()


def test_processing_happens_off_the_caller_thread(db_path: Path) -> None:
    engine = RecordingEngine()
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert engine.threads and engine.threads[0] != threading.get_ident()
    finally:
        pipeline.stop()


# --- junk filter after the model -------------------------------------------------


def test_junk_phrase_is_dropped(db_path: Path) -> None:
    # THE confirmed hallucination: whisper says „Dziękuję." to silence that
    # slipped past the gate. It must never become an input event.
    engine = RecordingEngine(transcript="Dziękuję.")
    got: list[str] = []
    pipeline = run_pipeline(db_path, engine, on_transcript=got.append)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert len(engine.calls) == 1
        assert got == []
        assert transcribed_events(db_path) == []
    finally:
        pipeline.stop()


def test_junk_matching_ignores_case_punctuation_and_whitespace(db_path: Path) -> None:
    engine = RecordingEngine(transcript="  DZIĘKUJĘ!!!  ")
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert transcribed_events(db_path) == []
    finally:
        pipeline.stop()


def test_junk_list_comes_from_config(db_path: Path) -> None:
    engine = RecordingEngine(transcript="Dziękuję.")
    pipeline = run_pipeline(
        db_path, engine, stt_junk_phrases=("zupełnie inna fraza",)
    )
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        # Not on the configured list any more -> it is a real transcript.
        assert [event["text"] for event in transcribed_events(db_path)] == ["Dziękuję."]
    finally:
        pipeline.stop()


def test_a_sentence_containing_a_junk_prefix_is_not_junk(db_path: Path) -> None:
    engine = RecordingEngine(transcript="Dziękuję, a teraz otwórz terminal.")
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert len(transcribed_events(db_path)) == 1
    finally:
        pipeline.stop()


def test_degenerate_repeated_char_transcript_is_dropped(db_path: Path) -> None:
    # Live-confirmed at the G4 gate (2026-07-02): ambient noise slipped past
    # the gate and whisper answered with 446 x "m". A junk-phrase list can
    # never enumerate variable-length babble — this needs a shape rule.
    engine = RecordingEngine(transcript="m" * 446)
    got: list[str] = []
    pipeline = run_pipeline(db_path, engine, on_transcript=got.append)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert got == []
        assert transcribed_events(db_path) == []
    finally:
        pipeline.stop()


def test_degenerate_two_letter_babble_is_dropped(db_path: Path) -> None:
    engine = RecordingEngine(transcript="No no no no no no no.")
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert transcribed_events(db_path) == []
    finally:
        pipeline.stop()


def test_short_low_variety_speech_is_not_degenerate(db_path: Path) -> None:
    # Short real utterances ("Tak tak") stay: the rule needs BOTH length
    # and near-zero character variety before it may drop anything.
    engine = RecordingEngine(transcript="Tak tak")
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert [event["text"] for event in transcribed_events(db_path)] == ["Tak tak"]
    finally:
        pipeline.stop()


def test_empty_transcript_is_dropped(db_path: Path) -> None:
    engine = RecordingEngine(transcript="   ")
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert transcribed_events(db_path) == []
    finally:
        pipeline.stop()


# --- resilience -----------------------------------------------------------------


def test_engine_failure_does_not_kill_the_pipeline(db_path: Path) -> None:
    class ExplodingOnce(RecordingEngine):
        def transcribe(self, audio: bytes) -> str:
            if not self.calls:
                self.calls.append(-1)
                raise RuntimeError("model crashed")
            return super().transcribe(audio)

    engine = ExplodingOnce()
    pipeline = run_pipeline(db_path, engine)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert transcribed_events(db_path) == []

        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert len(transcribed_events(db_path)) == 1
    finally:
        pipeline.stop()


def test_consumer_exception_does_not_lose_the_event(db_path: Path) -> None:
    def explode(text: str) -> None:
        raise RuntimeError("consumer bug")

    pipeline = run_pipeline(db_path, RecordingEngine(), on_transcript=explode)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        assert len(transcribed_events(db_path)) == 1
    finally:
        pipeline.stop()


def test_transcript_with_a_secret_is_redacted_at_rest(db_path: Path) -> None:
    engine = RecordingEngine(transcript="Mój klucz to sk-supersecret1234567890 zapisz go.")
    got: list[str] = []
    pipeline = run_pipeline(db_path, engine, on_transcript=got.append)
    try:
        pipeline.accept_capture(SPEECH)
        assert pipeline.flush()
        events = transcribed_events(db_path)
        assert len(events) == 1
        assert "sk-supersecret1234567890" not in events[0]["text"]
        assert "[REDACTED]" in events[0]["text"]
        # The live consumer gets the raw transcript (it is the user's input);
        # only what is written down is redacted.
        assert got == ["Mój klucz to sk-supersecret1234567890 zapisz go."]
    finally:
        pipeline.stop()


# --- daemon wiring ----------------------------------------------------------------


def _daemon_with_sox_and_stt(tmp_path: Path):
    from jarvis.daemon.app import create_daemon_app
    from tests.test_api_smoke import config_text
    from tests.test_voice_recorder import write_script

    # Fake sox that captures NOISE (urandom): loud enough to pass the gate.
    argv_file = tmp_path / "sox-argv.txt"
    binary = write_script(
        tmp_path / "fake-sox-noise",
        f"""
out=""
for arg in "$@"; do
  case "$arg" in *.wav) out="$arg";; esac
done
if [ -n "$out" ]; then head -c 32000 /dev/urandom > "$out"; fi
printf '%s\\t' "$@" >> {argv_file}
printf '\\n' >> {argv_file}
trap 'exit 0' INT TERM
sleep 30 &
wait $!
""",
    )
    text = config_text(tmp_path / "home" / "jarvis.db")
    text = text.replace("[voice]\nenabled = false", "[voice]\nenabled = true")
    text = text.replace(
        "queue_persisted = true",
        f'queue_persisted = true\nrecorder = "sox"\nrecorder_binary = "{binary}"',
    )
    text = text.replace(
        "[audio]\nenabled = false",
        '[audio]\nenabled = true\nbackend = "fake"',
    ).replace(
        "allow_bluetooth_microphone = false",
        "allow_bluetooth_microphone = true",
    )
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(text, encoding="utf-8")
    return create_daemon_app(config_path), argv_file


def test_daemon_wires_capture_to_stt_and_persists_the_event(tmp_path: Path) -> None:
    daemon_app, argv_file = _daemon_with_sox_and_stt(tmp_path)
    daemon_app.start()
    try:
        assert daemon_app.voice_stt is not None
        recorder = daemon_app.voice_recorder
        recorder.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not argv_file.exists():
            time.sleep(0.02)
        recorder.stop()
        assert daemon_app.voice_stt.flush()

        db = tmp_path / "home" / "jarvis.db"
        events = transcribed_events(db)
        assert len(events) == 1
        # Mock default transcript carries sk-*: the event at rest proves
        # append-time redaction for voice transcripts too.
        assert "sk-" not in events[0]["text"]
        assert "[REDACTED]" in events[0]["text"]
    finally:
        daemon_app.stop()


def test_daemon_stop_stops_the_stt_pipeline(tmp_path: Path) -> None:
    daemon_app, _ = _daemon_with_sox_and_stt(tmp_path)
    daemon_app.start()
    assert daemon_app.voice_stt is not None
    daemon_app.stop()
    assert daemon_app.voice_stt is None
