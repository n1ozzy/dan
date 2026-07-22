"""Persona routing is resolver-owned and frozen before TTS sees a request."""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.store.db import close_quietly, initialize_database
from dan.voice.models import SnapshotValidationError, SpeechIntent
from dan.voice.queue import VoiceQueue
from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceCatalog, VoiceResolver
from dan.voice.service import VoiceService
from tests.test_voice_tts_supertonic import build_engine, fake_ffmpeg


def resolver(tmp_path: Path) -> VoiceResolver:
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "personas.toml").write_text(
        '[dan]\nengine = "supertonic"\nvoice = "M3"\nmastering = "raw"\n'
        'speed = 1.25\nseed = 17\ndsp = "none"\n\n'
        '[mentor]\nengine = "supertonic"\nvoice = "M4"\nmastering = "clean"\n'
        'speed = 1.1\nseed = 91\ndsp = "highpass=f=80"\n',
        encoding="utf-8",
    )
    (voice_dir / "pronunciations.toml").write_text("", encoding="utf-8")
    model = tmp_path / "engine.asset"
    model.write_bytes(b"test-engine")
    return VoiceResolver(
        VoiceCatalog.from_directory(voice_dir),
        {"voice": {"output_gain": 1.0}},
        {
            "supertonic": EngineMetadata(
                version="1.3.1",
                assets={"model": AssetMetadata.from_path(model)},
            )
        },
    )


def intent(persona: str) -> SpeechIntent:
    return SpeechIntent(
        text="Persona ma zostac zamrozona.",
        persona=persona,
        source="pytest",
        session="persona-test",
        participant=persona,
        priority=0,
        lane="normal",
        interrupt_policy="finish_current",
        utterance_index=0,
    )


def test_resolver_freezes_persona_voice_mastering_and_dsp(tmp_path: Path) -> None:
    snapshot = resolver(tmp_path).resolve(intent("mentor"))

    assert snapshot.voice_or_style == "M4"
    assert snapshot.speed == 1.1
    assert snapshot.seed == 91
    assert snapshot.mastering_profile == "clean"
    assert snapshot.dsp == "highpass=f=80"


def test_tts_executes_the_resolved_voice_without_persona_dependency(tmp_path: Path) -> None:
    snapshot = resolver(tmp_path).resolve(intent("mentor"))
    ffmpeg, _ = fake_ffmpeg(tmp_path)
    engine, args_file = build_engine(tmp_path, mastering_binary=str(ffmpeg))

    engine.synthesize("Dokladnie ten snapshot.", snapshot)

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--voice") + 1] == "M4"
    assert not hasattr(engine, "_persona_provider")
    assert not hasattr(engine, "_mastering_filter_for")


def test_unknown_persona_never_reaches_queue_or_tts(tmp_path: Path) -> None:
    conn = initialize_database(tmp_path / "voice.db")
    service = VoiceService(VoiceQueue(conn), resolver(tmp_path))
    try:
        with pytest.raises(SnapshotValidationError, match="unknown voice persona"):
            service.submit(intent("missing"))
        assert service.queue.list() == []
    finally:
        close_quietly(conn)


def test_tts_constructor_has_no_persona_or_resolver_switch(tmp_path: Path) -> None:
    engine, _ = build_engine(tmp_path)

    assert not hasattr(engine, "_resolver")
    assert not hasattr(engine, "_voice_for")
