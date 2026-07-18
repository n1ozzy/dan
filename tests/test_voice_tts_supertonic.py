from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.voice.assets import load_asset_manifest
from dan.voice.models import RenderSnapshot
from dan.voice.tts import (
    BannedEngineError,
    SupertonicEngine,
    TTSEngineError,
    build_tts_engine,
)

ROOT = Path(__file__).resolve().parents[1]


def write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def fake_supertonic(
    tmp_path: Path,
    *,
    rc: int = 0,
    wav_bytes: int = 2000,
    version: str = "1.3.1",
) -> tuple[Path, Path]:
    args_file = tmp_path / "supertonic-args.txt"
    version_calls = tmp_path / "supertonic-version-calls.txt"
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
if [ -n "$out" ]; then head -c {wav_bytes} /dev/zero > "$out"; fi
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
    mastering: str = "raw",
    dsp: str = "none",
    pronunciations: dict[str, str] | None = None,
    engine: str = "supertonic",
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
    )


def build_engine(tmp_path: Path, **overrides) -> tuple[SupertonicEngine, Path]:
    binary, args_file = fake_supertonic(tmp_path)
    return build_tts_engine("supertonic", config=config(tmp_path, binary, **overrides)), args_file


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
        snapshot(mastering="clean", dsp="highpass=f=80"),
    )

    args = args_file.read_text(encoding="utf-8").splitlines()
    chain = args[args.index("-af") + 1]
    assert chain.startswith("highpass=f=80,")
    assert "acompressor=" in chain


def test_required_mastering_failure_never_returns_raw_audio(tmp_path: Path) -> None:
    ffmpeg, _ = fake_ffmpeg(tmp_path, rc=7)
    engine, _ = build_engine(tmp_path, mastering_binary=str(ffmpeg))

    with pytest.raises(TTSEngineError, match="mastering failed"):
        engine.synthesize("Nie udawaj sukcesu.", snapshot(mastering="clean"))


def test_warm_serve_failure_falls_back_to_cli_for_same_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, args_file = fake_supertonic(tmp_path)

    class Health:
        status = 200

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
    stored = snapshot(voice="M4", speed=1.09)

    engine.synthesize("Ten sam snapshot.", stored)

    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--voice") + 1] == stored.voice_or_style
    assert args[args.index("--speed") + 1] == "1.09"


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


@pytest.mark.parametrize("name", ["edgetts", "piper", "xtts"])
def test_banned_engines_are_refused(name: str) -> None:
    with pytest.raises(BannedEngineError):
        build_tts_engine(name)


def test_unknown_engine_fails_closed() -> None:
    with pytest.raises(TTSEngineError):
        build_tts_engine("unknown")
