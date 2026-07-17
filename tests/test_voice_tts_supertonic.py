"""SupertonicEngine tests (G3+, decree §7.3 — first real TTS engine).

The engine shells out to the `supertonic` CLI; tests replace it with a fake
script so no real synthesis runs and no sound is ever produced (ADR-005:
only the broker speaks, and tests never speak at all). Live Polish output
was verified in docs/reviews/2026-07-02-voice-tools-inventory.md.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.voice.resolver import AssetMetadata, EngineMetadata, VoiceCatalog, VoiceResolver
from dan.voice.tts import (
    BannedEngineError,
    SupertonicEngine,
    SynthesizedChunk,
    TTSEngineError,
    build_tts_engine,
)


def write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o700)
    return path


def fake_supertonic(tmp_path: Path, *, rc: int = 0, wav_bytes: int = 2000) -> tuple[Path, Path]:
    """Fake CLI: records argv, writes a WAV-sized file to the -o target."""

    args_file = tmp_path / "supertonic-args.txt"
    script = write_script(
        tmp_path / "fake-supertonic",
        f"""
printf '%s\\n' "$@" > {args_file}
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


def fake_player(tmp_path: Path, *, rc: int = 0) -> tuple[Path, Path]:
    """Fake player: records the path it was given and whether it existed."""

    played_file = tmp_path / "played.txt"
    script = write_script(
        tmp_path / "fake-player",
        f"""
if [ -f "$1" ]; then echo "exists $1" >> {played_file}; else echo "missing $1" >> {played_file}; fi
exit {rc}
""",
    )
    return script, played_file


def full_config(tmp_path: Path, binary: Path, player: Path, **voice_overrides) -> SimpleNamespace:
    voice = {
        "default_tts": "supertonic",
        "supertonic_binary": str(binary),
        "supertonic_voice": "M1",
        "supertonic_lang": "pl",
        "supertonic_steps": 14,
        "supertonic_speed": 1.35,
        "playback_binary": str(player),
        "tts_timeout_seconds": 30,
    }
    voice.update(voice_overrides)
    return SimpleNamespace(
        voice=SimpleNamespace(**voice),
        runtime=SimpleNamespace(runtime_dir=str(tmp_path / "runtime")),
    )


def strict_resolver(
    tmp_path: Path,
    config: SimpleNamespace,
    *,
    personas: dict[str, dict[str, object]] | None = None,
    engine_asset: Path | None = None,
) -> VoiceResolver:
    voice_cfg = config.voice
    voice_dir = tmp_path / f"voice-catalog-{len(list(tmp_path.glob('voice-catalog-*')))}"
    voice_dir.mkdir()
    configured_personas = personas or {
        "dan": {
            "voice": getattr(voice_cfg, "supertonic_voice", "M1"),
            "speed": getattr(voice_cfg, "supertonic_speed", 1.0),
            "mastering": getattr(voice_cfg, "mastering_profile", "") or "raw",
            "dsp": "none",
        }
    }
    persona_lines: list[str] = []
    for name, spec in configured_personas.items():
        persona_lines.extend(
            [
                f"[{name}]",
                'engine = "supertonic"',
                f"voice = {json.dumps(str(spec['voice']))}",
                f"speed = {float(spec['speed'])}",
                f"mastering = {json.dumps(str(spec['mastering']))}",
                f"dsp = {json.dumps(str(spec.get('dsp', 'none')))}",
            ]
        )
    (voice_dir / "personas.toml").write_text(
        "\n".join(persona_lines) + "\n", encoding="utf-8"
    )
    pronunciations = getattr(voice_cfg, "tts_pronunciations", {}) or {}
    (voice_dir / "pronunciations.toml").write_text(
        "".join(
            f"{json.dumps(str(key).lower())} = {json.dumps(str(value))}\n"
            for key, value in pronunciations.items()
        ),
        encoding="utf-8",
    )
    verified_asset = engine_asset or Path(voice_cfg.supertonic_binary)
    return VoiceResolver(
        VoiceCatalog.from_directory(voice_dir),
        {"voice": {"output_gain": getattr(voice_cfg, "output_gain", 1.0)}},
        {
            "supertonic": EngineMetadata(
                version="test-supertonic",
                assets={"binary": AssetMetadata.from_path(verified_asset)},
            )
        },
    )


def build_strict_engine(
    tmp_path: Path,
    config: SimpleNamespace,
    *,
    persona_provider=None,
    engine_asset: Path | None = None,
    personas: dict[str, dict[str, object]] | None = None,
) -> SupertonicEngine:
    return build_tts_engine(
        "supertonic",
        config=config,
        persona_provider=persona_provider,
        resolver=strict_resolver(
            tmp_path, config, personas=personas, engine_asset=engine_asset
        ),
    )


def build_engine(tmp_path: Path, **voice_overrides) -> tuple[SupertonicEngine, Path, Path]:
    binary, args_file = fake_supertonic(tmp_path)
    player, played_file = fake_player(tmp_path)
    config = full_config(tmp_path, binary, player, **voice_overrides)
    engine = build_strict_engine(tmp_path, config)
    return engine, args_file, played_file


# --- construction ----------------------------------------------------------


def test_supertonic_without_config_fails_loudly() -> None:
    with pytest.raises(TTSEngineError, match="config"):
        build_tts_engine("supertonic")


def test_supertonic_with_config_but_without_resolver_fails_loudly(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path)

    with pytest.raises(TTSEngineError, match="VoiceResolver"):
        build_tts_engine("supertonic", config=full_config(tmp_path, binary, player))


def test_supertonic_with_resolver_emits_migration_warning(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path)
    config = full_config(tmp_path, binary, player)

    with pytest.warns(DeprecationWarning, match="compatibility caller"):
        build_strict_engine(tmp_path, config)


def test_supertonic_builds_from_config(tmp_path: Path) -> None:
    engine, _, _ = build_engine(tmp_path)
    assert isinstance(engine, SupertonicEngine)
    assert engine.name == "supertonic"


def test_missing_binary_kills_construction(tmp_path: Path) -> None:
    verified_binary, _ = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path)
    config = full_config(tmp_path, tmp_path / "nope", player)
    with pytest.raises(TTSEngineError, match="binary"):
        build_strict_engine(tmp_path, config, engine_asset=verified_binary)


def test_missing_player_kills_construction(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path)
    config = full_config(tmp_path, binary, tmp_path / "no-player")
    with pytest.raises(TTSEngineError, match="player"):
        build_strict_engine(tmp_path, config)


def test_workdir_created_private(tmp_path: Path) -> None:
    engine, _, _ = build_engine(tmp_path)
    workdir = Path(engine.workdir)
    assert workdir.is_dir()
    assert stat.S_IMODE(workdir.stat().st_mode) == 0o700


def test_chatterbox_is_still_reserved() -> None:
    with pytest.raises(TTSEngineError, match="G5"):
        build_tts_engine("chatterbox")


def test_banned_engines_stay_banned_even_with_config(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path)
    config = full_config(tmp_path, binary, player)
    with pytest.raises(BannedEngineError):
        build_tts_engine("edgetts", config=config)


# --- synthesize --------------------------------------------------------------


def test_synthesize_invokes_cli_with_decreed_flags(tmp_path: Path) -> None:
    engine, args_file, _ = build_engine(tmp_path, supertonic_voice="M2")

    chunk = engine.synthesize("Pierwsze zdanie testowe.")

    args = args_file.read_text().splitlines()
    assert args[0] == "tts"
    assert args[1] == "Pierwsze zdanie testowe."
    assert args[args.index("--voice") + 1] == "M2"
    assert args[args.index("--lang") + 1] == "pl"
    assert args[args.index("--steps") + 1] == "14"
    assert args[args.index("--speed") + 1] == "1.35"
    assert isinstance(chunk, SynthesizedChunk)
    assert chunk.text == "Pierwsze zdanie testowe."
    assert len(chunk.audio) == 2000


def test_synthesize_strips_typographic_quotes(tmp_path: Path) -> None:
    # Empirical fact (inventory 2026-07-02): typographic quotes crash the
    # supertonic CLI, so the engine strips them before shelling out.
    engine, args_file, _ = build_engine(tmp_path)

    chunk = engine.synthesize("On powiedział „dość” i wyszedł.")

    args = args_file.read_text().splitlines()
    assert args[1] == "On powiedział dość i wyszedł."
    # The chunk keeps the original text: queue rows and logs stay truthful.
    assert chunk.text == "On powiedział „dość” i wyszedł."


def test_supertonic_resolves_binaries_from_path_and_synthesize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Constructor path policy must honor PATH fallback when config entries are
    # command names. This prevents "No binary" regressions after deployment
    # where absolute paths are not injected into config.
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    binary_src, _ = fake_supertonic(tmp_path)
    player_src, _ = fake_player(tmp_path)
    binary = system_bin / "supertonic"
    player = system_bin / "supertonic-player"
    binary.write_text(binary_src.read_text())
    binary.chmod(0o700)
    player.write_text(player_src.read_text())
    player.chmod(0o700)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{system_bin}:{original_path}")
    config = full_config(
        tmp_path,
        tmp_path / "ignored",
        tmp_path / "ignored",
        supertonic_binary="",
        playback_binary="supertonic-player",
    )
    engine = build_strict_engine(tmp_path, config, engine_asset=binary)

    chunk = engine.synthesize("Wypowiedź z PATH.")
    assert isinstance(chunk.text, str)
    assert len(chunk.audio) == 2000


def test_synthesize_applies_longer_pronunciation_key_first(tmp_path: Path) -> None:
    # Longest-to-shortest replacement order must win for overlapping keys.
    binary, args_file = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path)
    config = full_config(
        tmp_path,
        binary,
        player,
        tts_pronunciations={"run": "rwn", "runtime": "rantajm"},
    )
    engine = build_strict_engine(tmp_path, config)

    engine.synthesize("Runtime.")
    spoken = args_file.read_text().splitlines()[1]
    assert "rantajm" in spoken
    assert "rwn" not in spoken


def test_synthesize_nothing_speakable_raises(tmp_path: Path) -> None:
    engine, _, _ = build_engine(tmp_path)
    with pytest.raises(TTSEngineError, match="speakable"):
        engine.synthesize("„”")


def test_synthesize_nonzero_exit_raises_and_cleans(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path, rc=3)
    player, _ = fake_player(tmp_path)
    config = full_config(tmp_path, binary, player)
    engine = build_strict_engine(tmp_path, config)

    with pytest.raises(TTSEngineError):
        engine.synthesize("To zdanie nie wyjdzie.")
    assert list(Path(engine.workdir).iterdir()) == []


def test_synthesize_tiny_output_raises_and_cleans(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path, wav_bytes=10)
    player, _ = fake_player(tmp_path)
    config = full_config(tmp_path, binary, player)
    engine = build_strict_engine(tmp_path, config)

    with pytest.raises(TTSEngineError, match="audio"):
        engine.synthesize("Za mało bajtów wyszło.")
    assert list(Path(engine.workdir).iterdir()) == []


def test_synthesize_leaves_no_temp_files(tmp_path: Path) -> None:
    engine, _, _ = build_engine(tmp_path)
    engine.synthesize("Sprzątanie po syntezie działa.")
    assert list(Path(engine.workdir).iterdir()) == []


# --- play --------------------------------------------------------------------


def test_play_gives_player_a_live_file_then_cleans(tmp_path: Path) -> None:
    engine, _, played_file = build_engine(tmp_path)
    chunk = engine.synthesize("Odtwarzanie przez playera.")

    engine.play(chunk)

    entries = played_file.read_text().splitlines()
    assert len(entries) == 1
    assert entries[0].startswith("exists ")
    played_path = Path(entries[0].split(" ", 1)[1])
    assert not played_path.exists()
    assert list(Path(engine.workdir).iterdir()) == []


def test_play_failure_raises_and_cleans(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path, rc=1)
    config = full_config(tmp_path, binary, player)
    engine = build_strict_engine(tmp_path, config)
    chunk = engine.synthesize("Player padnie na tym zdaniu.")

    with pytest.raises(TTSEngineError, match="player"):
        engine.play(chunk)
    assert list(Path(engine.workdir).iterdir()) == []


def test_synthesize_applies_pronunciation_map(tmp_path: Path) -> None:
    # Ozzy's gate feedback (2026-07-02): anglicisms must be spoken Polish-
    # phonetically ("runtime" -> "rantajm"). The map is DATA in the TOML;
    # matching is case-insensitive and catches inflections ("runtime'ie").
    binary, args_file = fake_supertonic(tmp_path)
    player, _ = fake_player(tmp_path)
    config = full_config(
        tmp_path,
        binary,
        player,
        tts_pronunciations={"runtime": "rantajm", "stateless": "stejtles"},
    )
    engine = build_strict_engine(tmp_path, config)

    engine.synthesize("Stateless brain na żywym runtime'ie działa.")

    # argv: ["tts", <text>, "-o", <path>, ...] — the path itself contains
    # "runtime" (the runtime workdir), so assert on the text argument only.
    spoken = args_file.read_text().splitlines()[1]
    assert "rantajm'ie" in spoken
    assert "stejtles" in spoken
    assert "runtime" not in spoken.casefold()
    assert "stateless" not in spoken.casefold()


def test_synthesize_without_pronunciations_keeps_text(tmp_path: Path) -> None:
    engine, args_file, _ = build_engine(tmp_path)
    engine.synthesize("Runtime zostaje jak był.")
    assert "Runtime" in args_file.read_text()


# --- immutable resolver output -----------------------------------------------


def test_short_sentence_keeps_resolved_speed_unchanged(tmp_path: Path) -> None:
    engine, args_file, _ = build_engine(
        tmp_path,
        supertonic_short_sentence_chars=24,
        supertonic_short_sentence_speed=1.0,
    )

    engine.synthesize("Gotowe.")

    args = args_file.read_text().splitlines()
    assert args[args.index("--speed") + 1] == "1.35"


def test_nonempty_resolved_mastering_failure_is_visible(tmp_path: Path) -> None:
    mastering = write_script(tmp_path / "failing-mastering", "exit 7\n")
    engine, _, _ = build_engine(
        tmp_path,
        mastering_profile="clean",
        mastering_binary=str(mastering),
    )

    with pytest.raises(TTSEngineError, match="mastering"):
        engine.synthesize("Mastering ma nie udawać sukcesu.")


def test_explicit_raw_profile_preserves_unmastered_audio(tmp_path: Path) -> None:
    mastering = write_script(tmp_path / "unused-mastering", "exit 7\n")
    engine, _, _ = build_engine(
        tmp_path,
        mastering_profile="raw",
        mastering_binary=str(mastering),
    )

    chunk = engine.synthesize("Raw znaczy świadomie bez masteringu.")

    assert len(chunk.audio) == 2000


def fake_player_recording_argv(tmp_path: Path) -> tuple[Path, Path]:
    """Fake player: records its full argv, one argument per line."""

    argv_file = tmp_path / "player-argv.txt"
    script = write_script(
        tmp_path / "fake-player-argv",
        f"""
printf '%s\\n' "$@" > {argv_file}
exit 0
""",
    )
    return script, argv_file


def test_play_without_pads_passes_only_the_file(tmp_path: Path) -> None:
    binary, _ = fake_supertonic(tmp_path)
    player, argv_file = fake_player_recording_argv(tmp_path)
    config = full_config(tmp_path, binary, player)
    engine = build_strict_engine(tmp_path, config)
    chunk = engine.synthesize("Bez padów player dostaje sam plik.")

    engine.play(chunk)

    argv = argv_file.read_text().splitlines()
    assert len(argv) == 1
    assert argv[0].endswith(".wav")


def test_play_appends_pad_effect_when_configured(tmp_path: Path) -> None:
    # G4 live-gate fact: chunk boundaries click and swallow tails because
    # each chunk is its own player process; pads keep the stream open past
    # the audible audio (and give Bluetooth its codec-latency tail back).
    binary, _ = fake_supertonic(tmp_path)
    player, argv_file = fake_player_recording_argv(tmp_path)
    config = full_config(
        tmp_path,
        binary,
        player,
        playback_pad_start_seconds=0.2,
        playback_pad_end_seconds=0.4,
    )
    engine = build_strict_engine(tmp_path, config)
    chunk = engine.synthesize("Pady wchodzą do komendy playera.")

    engine.play(chunk)

    argv = argv_file.read_text().splitlines()
    assert argv[0].endswith(".wav")
    assert argv[1:] == ["pad", "0.2", "0.4"]


def test_temp_audio_files_are_owner_only(tmp_path: Path) -> None:
    # The played file is transient transport, but it still lives on disk for
    # a moment — keep it 0600 like every other runtime artifact.
    engine, _, played_file = build_engine(tmp_path)
    probe = tmp_path / "probe-mode.txt"
    engine._player = str(  # type: ignore[attr-defined]
        write_script(
            tmp_path / "mode-probe-player",
            f'stat -f "%Lp" "$1" > {probe}\nexit 0\n',
        )
    )
    engine.play(SynthesizedChunk(text="tryb pliku", audio=b"\x00" * 64))
    assert probe.read_text().strip() == "600"


# --- barge-in: playback cancellation (G4c, VOICE_STREAMING §7 leg 3) -----------


def test_stop_playback_kills_the_player_process(tmp_path: Path) -> None:
    """Cancel of playback = kill of the sox `play` subprocess (G3+ design).
    A long-running player dies promptly and play() raises instead of
    pretending the chunk finished."""

    import threading
    import time

    binary, _ = fake_supertonic(tmp_path)
    marker = tmp_path / "player-started.txt"
    # `exec` replaces the shell in place, so the player is ONE process — like the
    # real sox `play`. With a plain `sleep 30`, bash forks a child: under load,
    # stop_playback()'s killpg could fire between the marker write and that fork,
    # leaving the sleep orphaned outside the killed group, holding the stdout
    # pipe open so play()'s communicate() blocks ~30s and the thread outlives
    # join(timeout=5) — the flaky failure. One process closes that race.
    player = write_script(
        tmp_path / "slow-player",
        f'echo "started $$" > {marker}\nexec sleep 30\n',
    )
    config = full_config(tmp_path, binary, player)
    engine = build_strict_engine(tmp_path, config)
    chunk = engine.synthesize("Długi chunk przerwany barge-inem.")

    errors: list[Exception] = []

    def play() -> None:
        try:
            engine.play(chunk)
        except TTSEngineError as exc:
            errors.append(exc)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.01)
    assert marker.exists(), "player never started"

    started = time.monotonic()
    engine.stop_playback()
    thread.join(timeout=5)
    assert not thread.is_alive(), "playback survived stop_playback"
    assert time.monotonic() - started < 5
    assert len(errors) == 1
    assert list(Path(engine.workdir).iterdir()) == []


def test_stop_playback_when_idle_is_a_no_op(tmp_path: Path) -> None:
    engine, _, played_file = build_engine(tmp_path)

    engine.stop_playback()  # nothing playing — must not blow up

    chunk = engine.synthesize("Zdanie grane po bezczynnym stopie.")
    engine.play(chunk)  # and must not poison the NEXT playback
    assert played_file.read_text().splitlines()
