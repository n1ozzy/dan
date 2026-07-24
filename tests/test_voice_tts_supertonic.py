from __future__ import annotations

import hashlib
import inspect
import json
import logging
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.voice import tts as voice_tts
from dan.voice.assets import load_asset_manifest
from dan.voice.models import RenderSnapshot
from dan.voice.resolver import VoiceCatalog
from dan.voice.tts import (
    BannedEngineError,
    SupertonicEngine,
    TTSEngineError,
    apply_pronunciations,
    build_tts_engine,
)

ROOT = Path(__file__).resolve().parents[1]


def write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def template_wav(tmp_path: Path, *, frames: int = 1000) -> Path:
    template = tmp_path / "template.wav"
    if not template.exists():
        with wave.open(str(template), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(44100)
            writer.writeframes(b"\x00" * (frames * 2))
    return template


def fake_supertonic(
    tmp_path: Path,
    *,
    rc: int = 0,
    version: str = "1.3.1",
) -> tuple[Path, Path]:
    args_file = tmp_path / "supertonic-args.txt"
    version_calls = tmp_path / "supertonic-version-calls.txt"
    template = template_wav(tmp_path)
    script = write_script(
        tmp_path / "fake-supertonic",
        f"""
if [ "$1" = "version" ]; then
  echo x >> {version_calls}
  echo "supertonic {version}"
  exit 0
fi
printf '%s\n' "$@" > {args_file}
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
if [ -n "$out" ]; then cat {template} > "$out"; fi
exit {rc}
""",
    )
    return script, args_file


def fake_ffmpeg(tmp_path: Path, *, rc: int = 0) -> tuple[Path, Path]:
    args_file = tmp_path / "ffmpeg-args.txt"
    script = write_script(
        tmp_path / "fake-ffmpeg",
        f"""
printf '%s\n' "$@" > {args_file}
for last; do true; done
if [ {rc} -eq 0 ]; then head -c 2000 /dev/zero > "$last"; fi
exit {rc}
""",
    )
    return script, args_file


def config(tmp_path: Path, binary: Path, **overrides) -> SimpleNamespace:
    values = {
        "supertonic_binary": str(binary),
        "supertonic_lang": "pl",
        "supertonic_steps": 14,
        "tts_timeout_seconds": 30,
        "mastering_binary": "definitely-missing-ffmpeg",
        "supertonic_serve_url": "",
        "supertonic_serve_model": "supertonic-3",
        "supertonic_serve_autostart": False,
        "supertonic_serve_max_chunk_length": 400,
        "supertonic_custom_styles_manifest": (
            "config/voice/custom_styles/manifest.json"
        ),
    }
    values.update(overrides)
    return SimpleNamespace(
        voice=SimpleNamespace(**values),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )


def snapshot(
    *,
    voice: str = "M3",
    speed: float = 1.25,
    # "none" is the only mastering value that skips ffmpeg entirely; these
    # tests exercise synthesis mechanics, not the loudnorm norm ("default").
    mastering: str = "none",
    dsp: str = "none",
    pronunciations: dict[str, str] | None = None,
    engine: str = "supertonic",
    seed: int = 17,
) -> RenderSnapshot:
    return RenderSnapshot(
        engine=engine,
        engine_version="1.3.1",
        voice_or_style=voice,
        speed=speed,
        mastering_profile=mastering,
        dsp=dsp,
        pronunciations=pronunciations or {},
        pronunciations_sha256="a" * 64,
        gain=1.0,
        asset_sha256={f"voice:{voice}": "b" * 64},
        config_revision="voice-catalog-v1",
        seed=seed,
    )


def build_engine(tmp_path: Path, **overrides) -> tuple[SupertonicEngine, Path]:
    binary, args_file = fake_supertonic(tmp_path)
    engine = build_tts_engine("supertonic", config=config(tmp_path, binary, **overrides))
    # Unit tests substitute the one-shot seeded renderer while production uses
    # ``python -m dan.voice.supertonic_seeded render``.
    engine._seeded_renderer_argv = (str(binary),)  # type: ignore[attr-defined]
    return engine, args_file


def test_supertonic_without_config_fails_loudly() -> None:
    with pytest.raises(TTSEngineError, match="config"):
        build_tts_engine("supertonic")


def test_supertonic_construction_has_no_resolver_or_player_dependency(tmp_path: Path) -> None:
    engine, _ = build_engine(tmp_path)

    assert isinstance(engine, SupertonicEngine)
    assert "resolver" not in inspect.signature(build_tts_engine).parameters
    assert "persona_provider" not in inspect.signature(build_tts_engine).parameters
    assert not hasattr(engine, "play")
    assert not hasattr(engine, "stop_playback")
    assert not hasattr(engine, "_render_snapshot")
    assert not hasattr(engine, "_voice_for")
    assert not hasattr(engine, "_mastering_filter_for")


def test_only_neutral_default_mastering_profile_is_active() -> None:
    assert voice_tts.MASTERING_PROFILES == frozenset({"default"})
    for retired in (
        "raw",
        "clean",
        "gritty",
        "raport",
        "whisper",
        "szept",
        "krzyk",
    ):
        assert voice_tts.mastering_filter(retired) == ""


def test_synthesis_uses_only_the_supplied_snapshot(tmp_path: Path) -> None:
    engine, args_file = build_engine(tmp_path)

    chunk = engine.synthesize(
        "Runtime zostaje w snapshotcie.",
        snapshot(voice="M2", speed=1.17, pronunciations={"runtime": "rantajm"}),
    )

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert chunk.text == "Runtime zostaje w snapshotcie."
    assert "rantajm zostaje w snapshotcie." in args
    assert args[args.index("--voice") + 1] == "M2"
    assert args[args.index("--speed") + 1] == "1.17"
    assert args[args.index("--seed") + 1] == "17"


def test_cli_fallback_preserves_full_snapshot_speed_precision(tmp_path: Path) -> None:
    engine, args_file = build_engine(tmp_path)

    engine.synthesize("Bez zaokrąglania.", snapshot(speed=1.23456789))

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--speed") + 1] == "1.23456789"


def test_raw_wav_hash_is_logged_before_mastering(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, _ = build_engine(tmp_path)
    caplog.set_level(logging.INFO, logger="dan.voice.tts")

    chunk = engine.synthesize("Hash surowego renderu.", snapshot(seed=42))

    assert "seed=42" in caplog.text
    assert hashlib.sha256(chunk.audio).hexdigest() in caplog.text


def test_warm_serve_sends_seed_and_requires_matching_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, _ = fake_supertonic(tmp_path)
    requests: list[dict[str, object]] = []

    class Response:
        status = 200
        headers = {
            "X-DAN-Seed-Protocol": "1",
            "X-DAN-Synthesis-Seed": "91",
        }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return b"w" * 2000

    def urlopen(request, timeout):
        assert not isinstance(request, str)
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("dan.voice.tts.urllib.request.urlopen", urlopen)
    engine = build_tts_engine("supertonic", config=config(tmp_path, binary))
    engine._serve = "http://127.0.0.1:9999"

    chunk = engine.synthesize("Ten sam seed.", snapshot(seed=91))

    assert chunk.audio == b"w" * 2000
    assert requests == [
        {
            "text": "Ten sam seed.",
            "voice": "M3",
            "lang": "pl",
            "speed": 1.25,
            "steps": 14,
            "max_chunk_length": 400,
            "silence_duration": 0.0,
            "seed": 91,
        }
    ]


def test_standard_unseeded_server_is_never_adopted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, args_file = fake_supertonic(tmp_path)
    calls: list[object] = []

    class StandardHealth:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        calls.append(request)
        return StandardHealth()

    monkeypatch.setattr("dan.voice.tts.urllib.request.urlopen", urlopen)
    engine = build_tts_engine(
        "supertonic",
        config=config(
            tmp_path,
            binary,
            supertonic_serve_url="http://127.0.0.1:9999",
        ),
    )
    engine._seeded_renderer_argv = (str(binary),)  # type: ignore[attr-defined]

    engine.synthesize("Stary serwer odpada.", snapshot(seed=42))

    assert calls and all(isinstance(call, str) for call in calls)
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--seed") + 1] == "42"


def test_pronunciations_match_tokens_without_corrupting_polish_words() -> None:
    rewritten = apply_pronunciations(
        "API jest zapisana w runtime'ie.",
        {"api": "epejaj", "runtime": "rantajm"},
    )

    assert rewritten == "epejaj jest zapisana w rantajm'ie."


def test_owner_name_uses_approved_spoken_form_from_catalog() -> None:
    catalog = VoiceCatalog.from_directory(ROOT / "config" / "voice")

    assert catalog.pronunciations["ozzy"] == "oz-i"
    assert apply_pronunciations(
        "Ozzy.", dict(catalog.pronunciations)
    ) == "oz-i."


def test_synthesis_refuses_a_snapshot_for_another_engine(tmp_path: Path) -> None:
    engine, _ = build_engine(tmp_path)

    with pytest.raises(TTSEngineError, match="snapshot engine"):
        engine.synthesize("Nie zmieniaj route.", snapshot(engine="chatterbox"))


def test_custom_style_uses_manifest_verified_repository_path(tmp_path: Path) -> None:
    engine, args_file = build_engine(tmp_path)
    manifest = load_asset_manifest(ROOT / "config/voice/custom_styles/manifest.json")
    asset = next(asset for asset in manifest.assets if asset.name == "M2M1")
    stored = snapshot(voice=str(asset.path))
    stored = RenderSnapshot(
        **{
            **stored.__dict__,
            "asset_sha256": {"engine.supertonic.voice:M2M1": asset.sha256},
        }
    )

    engine.synthesize("Styl z repo.", stored)

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--voice") + 1] == "M2M1"
    assert args[args.index("--custom-style-path") + 1] == str(asset.path)


def test_unverified_custom_style_fails_closed(tmp_path: Path) -> None:
    engine, _ = build_engine(tmp_path)

    with pytest.raises(TTSEngineError, match="unverified"):
        engine.synthesize(
            "Nie zgaduj stylu.",
            snapshot(voice=str(tmp_path / "CUSTOM-NOT-PINNED.json")),
        )


def test_mastering_and_dsp_are_taken_from_snapshot(tmp_path: Path) -> None:
    ffmpeg, args_file = fake_ffmpeg(tmp_path)
    engine, _ = build_engine(tmp_path, mastering_binary=str(ffmpeg))

    engine.synthesize(
        "Pelny render.",
        snapshot(mastering="default", dsp="highpass=f=80"),
    )

    args = args_file.read_text(encoding="utf-8").splitlines()
    chain = args[args.index("-af") + 1]
    assert chain.startswith("highpass=f=80,")
    assert "loudnorm=" in chain
    assert "acompressor=" not in chain


def test_default_mastering_applies_loudnorm_only(tmp_path: Path) -> None:
    ffmpeg, args_file = fake_ffmpeg(tmp_path)
    engine, _ = build_engine(tmp_path, mastering_binary=str(ffmpeg))

    engine.synthesize("Norma głośności.", snapshot(mastering="default"))

    args = args_file.read_text(encoding="utf-8").splitlines()
    chain = args[args.index("-af") + 1]
    assert "loudnorm=" in chain
    assert "equalizer=" not in chain
    assert "acompressor=" not in chain
    assert "asetrate=" not in chain


def test_required_mastering_failure_never_returns_raw_audio(tmp_path: Path) -> None:
    ffmpeg, _ = fake_ffmpeg(tmp_path, rc=7)
    engine, _ = build_engine(tmp_path, mastering_binary=str(ffmpeg))

    with pytest.raises(TTSEngineError, match="mastering failed"):
        engine.synthesize("Nie udawaj sukcesu.", snapshot(mastering="default"))


def test_warm_serve_failure_falls_back_to_cli_for_same_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, args_file = fake_supertonic(tmp_path)

    class Health:
        status = 200
        headers = {"X-DAN-Seed-Protocol": "1"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = 0

    def urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Health()
        raise OSError("serve zdechl")

    monkeypatch.setattr("dan.voice.tts.urllib.request.urlopen", urlopen)
    engine = build_tts_engine(
        "supertonic",
        config=config(tmp_path, binary, supertonic_serve_url="http://127.0.0.1:9999"),
    )
    engine._seeded_renderer_argv = (str(binary),)  # type: ignore[attr-defined]
    stored = snapshot(voice="M4", speed=1.09)

    engine.synthesize("Ten sam snapshot.", stored)

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--voice") + 1] == stored.voice_or_style
    assert args[args.index("--speed") + 1] == "1.09"


def test_reused_serve_is_re_adopted_after_supervised_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, _args_file = fake_supertonic(tmp_path)
    calls: list[str] = []

    class Response:
        status = 200
        headers = {
            "X-DAN-Seed-Protocol": "1",
            "X-DAN-Synthesis-Seed": "17",
        }

        def __init__(self, audio: bytes = b"") -> None:
            self._audio = audio

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return self._audio

    def urlopen(request, timeout):
        kind = "health" if isinstance(request, str) else "tts"
        calls.append(kind)
        if calls == ["health"]:
            return Response()
        if calls == ["health", "tts"]:
            raise OSError("supervised serve died")
        if calls == ["health", "tts", "health"]:
            raise OSError("replacement not healthy yet")
        if kind == "health":
            return Response()
        return Response(b"r" * 2000)

    monkeypatch.setattr("dan.voice.tts.urllib.request.urlopen", urlopen)
    engine = build_tts_engine(
        "supertonic",
        config=config(
            tmp_path,
            binary,
            supertonic_serve_url="http://127.0.0.1:9999",
        ),
        serve_autostart=False,
    )
    engine._seeded_renderer_argv = (str(binary),)  # type: ignore[attr-defined]

    first = engine.synthesize("Pierwsza proba.", snapshot())
    second = engine.synthesize("Po restarcie.", snapshot())

    assert first.audio == template_wav(tmp_path).read_bytes()
    assert second.audio == b"r" * 2000
    assert calls == ["health", "tts", "health", "health", "tts"]


def test_matching_engine_version_with_model_revision_passes(tmp_path: Path) -> None:
    # Resolver stamps "1.3.1+<model_revision>"; only the semver before "+"
    # must match the real binary.
    engine, _ = build_engine(tmp_path)
    stored = snapshot()
    stored = RenderSnapshot(**{**stored.__dict__, "engine_version": "1.3.1+model-rev-42"})

    chunk = engine.synthesize("Wersja sie zgadza.", stored)

    assert chunk.audio


def test_engine_version_mismatch_fails_closed_with_both_versions(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path, version="9.9.9")
    engine = build_tts_engine("supertonic", config=config(tmp_path, binary))

    with pytest.raises(TTSEngineError, match=r"1\.3\.1.*9\.9\.9"):
        engine.synthesize("Nie ta binarka.", snapshot())


def test_unparsable_engine_version_fails_closed(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path, version="banana")
    engine = build_tts_engine("supertonic", config=config(tmp_path, binary))

    with pytest.raises(TTSEngineError, match="version"):
        engine.synthesize("Bez wersji nie gramy.", snapshot())


def test_engine_version_probe_runs_once_and_is_cached(tmp_path: Path) -> None:
    engine, _ = build_engine(tmp_path)

    engine.synthesize("raz", snapshot())
    engine.synthesize("dwa", snapshot())

    calls = (tmp_path / "supertonic-version-calls.txt").read_text(encoding="utf-8")
    assert len(calls.splitlines()) == 1


def test_whole_utterance_reaches_supertonic_once_with_full_context(tmp_path: Path) -> None:
    engine, args_file = build_engine(tmp_path)
    text = "Pierwsze zdanie. Drugie pytanie? Trzeci cios! Cały kontekst zostaje razem."

    engine.synthesize(text, snapshot())

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args.count(text) == 1


def test_live_prosody_filter_uses_explicit_plan_not_punctuation() -> None:
    planned = snapshot().__class__(
        **{
            **snapshot().__dict__,
            "emotion": "anger",
            "tempo_start": 0.98,
            "tempo_end": 1.06,
            "tone": "hard",
            "pause_after": 0.24,
        }
    )

    first = voice_tts.live_prosody_filter(planned, duration_seconds=4.0)
    second = voice_tts.live_prosody_filter(planned, duration_seconds=4.0)

    assert first == second
    assert "asendcmd" in first and "atempo@live" in first
    assert "4.000 live tempo" in first
    assert "equalizer" in first and "volume=" in first
    assert "apad=pad_dur=0.240" in first


def test_static_neutral_plan_has_no_dynamic_tempo_or_tone_filter() -> None:
    chain = voice_tts.live_prosody_filter(snapshot(), duration_seconds=4.0)

    assert "asendcmd" not in chain
    assert "equalizer" not in chain


def test_extreme_legal_slowdown_uses_two_safe_tempo_stages() -> None:
    planned = snapshot().__class__(
        **{
            **snapshot().__dict__,
            "tempo_start": 1.4,
            "tempo_end": 0.6,
        }
    )

    chain = voice_tts.live_prosody_filter(planned, duration_seconds=4.0)

    assert "atempo@live_1" in chain
    assert "atempo@live_2" in chain


@pytest.mark.parametrize("name", ["edgetts", "piper", "xtts"])
def test_banned_engines_are_refused(name: str) -> None:
    with pytest.raises(BannedEngineError):
        build_tts_engine(name)


def test_unknown_engine_fails_closed() -> None:
    with pytest.raises(TTSEngineError):
        build_tts_engine("unknown")
