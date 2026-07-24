from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dan.voice.prosody.audio import (
    PCMBuffer,
    concatenate_wavs,
    read_wav_bytes,
    write_wav_bytes,
)
from dan.voice.prosody.command import build_standalone_parser
from dan.voice.prosody.models import TakeCandidate
from dan.voice.prosody.parser import SceneParseError, parse_scene_text
from dan.voice.prosody.planning import DirectorSettings, ProsodyDirector
from dan.voice.prosody.quality import analyze_take, select_best_take
from dan.voice.tts import mastering_filter


PERSONAS = {
    "dan": {
        "engine": "supertonic",
        "voice": "M3",
        "mastering": "default",
        "speed": 1.0,
        "seed": 1,
        "dsp": "none",
    },
    "danusia": {
        "engine": "supertonic",
        "voice": "F4",
        "mastering": "default",
        "speed": 1.0,
        "seed": 1,
        "dsp": "none",
    },
}


class SceneParserTests(unittest.TestCase):
    def test_parses_complete_direction_without_touching_spoken_text(self) -> None:
        lines = parse_scene_text(
            "dan;tempo=0.96;tempo_end=0.90;emotion=contempt;tone=dark;"
            "pause=0.26;takes=3;seeds=1,17,42|No... naprawdę?"
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].persona, "dan")
        self.assertEqual(lines[0].text, "No... naprawdę?")
        self.assertEqual(lines[0].tempo_start, 0.96)
        self.assertEqual(lines[0].tempo_end, 0.90)
        self.assertEqual(lines[0].emotion, "contempt")
        self.assertEqual(lines[0].tone, "dark")
        self.assertEqual(lines[0].pause_after, 0.26)
        self.assertEqual(lines[0].seeds, (1, 17, 42))
        self.assertEqual(lines[0].take_count, 3)

    def test_rejects_unknown_controls(self) -> None:
        with self.assertRaises(SceneParseError):
            parse_scene_text("dan;speed=1.29|Nie.")


class DirectorTests(unittest.TestCase):
    def test_cli_does_not_expose_an_artificial_split_limit(self) -> None:
        with self.assertRaises(SystemExit):
            build_standalone_parser().parse_args(
                [
                    "render",
                    "scene.txt",
                    "--plan-only",
                    "--hard-max-chars",
                    "120",
                ]
            )

    def test_keeps_complete_thought_under_hard_limit(self) -> None:
        line = parse_scene_text("dan|To jest pełna myśl. I nadal należy do jednego kontekstu.")
        plan = ProsodyDirector(PERSONAS).plan(line, source_name="test")
        self.assertEqual(len(plan.utterances[0].segments), 1)
        self.assertEqual(plan.utterances[0].segments[0].split_reason, "whole_thought")

    def test_rejects_every_retired_persona_route(self) -> None:
        director = ProsodyDirector(PERSONAS)
        for persona in ("jarvis", "gpt", "zaneta"):
            with self.subTest(persona=persona):
                with self.assertRaisesRegex(ValueError, "unknown voice persona"):
                    director.plan(
                        parse_scene_text(f"{persona}|Nie wolno mnie wyrenderować."),
                        source_name="test",
                    )

    def test_short_utterance_keeps_authored_contextual_tempo(self) -> None:
        line = parse_scene_text("dan;tempo=1.18;tempo_end=0.94|Serio?")
        plan = ProsodyDirector(PERSONAS).plan(line, source_name="test")
        utterance = plan.utterances[0]
        self.assertEqual(utterance.effective_speed, 1.18)
        self.assertEqual(utterance.tempo_start, 1.18)
        self.assertEqual(utterance.tempo_end, 0.94)

    def test_long_text_splits_only_when_engine_limit_requires_it(self) -> None:
        text = (
            "Pierwsza część zachowuje pełny sens i prowadzi do następnej myśli. "
            "Druga część nadal należy do tej samej wypowiedzi, ale jest wystarczająco długa, "
            "żeby silnik potrzebował technicznego oddechu. "
        ) * 3
        line = parse_scene_text(f"dan|{text}")
        settings = DirectorSettings(hard_max_chars=250)
        plan = ProsodyDirector(PERSONAS, settings=settings).plan(line, source_name="test")
        segments = plan.utterances[0].segments
        self.assertGreater(len(segments), 1)
        self.assertTrue(all(len(segment.text) <= 250 for segment in segments))
        self.assertEqual(segments[-1].internal_gap_after, 0.0)
        self.assertTrue(all(segment.internal_gap_after == 0.0 for segment in segments))

    def test_does_not_infer_pause_or_tone_from_punctuation(self) -> None:
        lines = parse_scene_text(
            "dan|To jest pytanie?\n"
            "danusia|A to jest urwana myśl..."
        )
        plan = ProsodyDirector(PERSONAS).plan(lines, source_name="test")
        for utterance in plan.utterances:
            self.assertEqual(utterance.pause_after, 0.0)
            self.assertEqual(utterance.tone, "neutral")
            self.assertEqual(utterance.emotion, "neutral")

    def test_plan_records_neighboring_context_without_speaking_it(self) -> None:
        lines = parse_scene_text(
            "dan|Pierwsza kwestia.\n"
            "danusia;emotion=anger;tone=hard|Odpowiedź.\n"
            "dan|Reakcja."
        )
        middle = ProsodyDirector(PERSONAS).plan(lines, source_name="test").utterances[1]
        self.assertEqual(middle.previous_context, "dan|Pierwsza kwestia.")
        self.assertEqual(middle.next_context, "dan|Reakcja.")
        self.assertEqual(middle.spoken_text, "Odpowiedź.")

    def test_dynamic_tempo_is_distributed_once_across_technical_segments(self) -> None:
        text = (
            "Pierwsza pełna część prowadzi napięcie i kończy się wyraźną granicą. "
            "Druga pełna część kontynuuje odpowiedź oraz domyka sens bez mechanicznego cięcia. "
            "Trzecia pełna część zostawia końcowy ciężar wypowiedzi."
        )
        line = parse_scene_text(
            f"dan;tempo=1.10;tempo_end=0.90|{text}"
        )
        plan = ProsodyDirector(
            PERSONAS,
            settings=DirectorSettings(hard_max_chars=110),
        ).plan(line, source_name="test")
        segments = plan.utterances[0].segments
        self.assertGreater(len(segments), 1)
        self.assertAlmostEqual(segments[0].tempo_start, 1.10)
        self.assertAlmostEqual(segments[-1].tempo_end, 0.90)
        for left, right in zip(segments, segments[1:]):
            self.assertAlmostEqual(left.tempo_end, right.tempo_start)


class QualityAndAudioTests(unittest.TestCase):
    def test_good_candidate_beats_obvious_near_silence(self) -> None:
        good = _voice_like_wav(2.15, amplitude=0.20)
        silent = _voice_like_wav(2.15, amplitude=0.0002)
        good_metrics = analyze_take(good, text="To jest test naturalnego głosu.")
        silent_metrics = analyze_take(silent, text="To jest test naturalnego głosu.")
        candidates = (
            TakeCandidate(
                17, Path("good.wav"), "a" * 64, Path("good.preview.wav"), "c" * 64, good_metrics
            ),
            TakeCandidate(
                42,
                Path("silent.wav"),
                "b" * 64,
                Path("silent.preview.wav"),
                "d" * 64,
                silent_metrics,
            ),
        )
        selected, _ = select_best_take(candidates)
        self.assertEqual(selected.seed, 17)
        self.assertFalse(good_metrics.hard_failures)
        self.assertIn("near_silence", silent_metrics.hard_failures)

    def test_concatenation_adds_exact_deterministic_gap(self) -> None:
        first = _voice_like_wav(0.20, amplitude=0.1, sample_rate=44_100)
        second = _voice_like_wav(0.30, amplitude=0.1, sample_rate=44_100)
        joined = concatenate_wavs(((first, 0.10), (second, 0.0)))
        duration = read_wav_bytes(joined).duration_seconds
        self.assertAlmostEqual(duration, 0.60, places=3)

    def test_quality_score_does_not_infer_a_target_duration_from_text(self) -> None:
        wav = _voice_like_wav(1.2, amplitude=0.2)
        plain = analyze_take(wav, text="To jest test")
        longer = analyze_take(wav, text="To jest trochę dłuższy test")

        self.assertFalse(plain.hard_failures)
        self.assertFalse(longer.hard_failures)
        self.assertEqual(plain.score, longer.score)

    def test_offline_mastering_can_skip_loudnorm_without_changing_live_default(self) -> None:
        self.assertIn("loudnorm", mastering_filter("default"))
        self.assertEqual(mastering_filter("default", include_loudnorm=False), "")


def _voice_like_wav(
    duration: float,
    *,
    amplitude: float,
    sample_rate: int = 44_100,
) -> bytes:
    frames = int(round(duration * sample_rate))
    time = np.arange(frames, dtype=np.float32) / sample_rate
    carrier = np.sin(2.0 * math.pi * 145.0 * time)
    harmonic = 0.28 * np.sin(2.0 * math.pi * 290.0 * time)
    envelope = 0.78 + (0.22 * np.sin(2.0 * math.pi * 2.2 * time))
    samples = amplitude * envelope * (carrier + harmonic)
    edge = min(frames // 4, int(0.04 * sample_rate))
    if edge > 1:
        ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)
        samples[:edge] *= ramp
        samples[-edge:] *= ramp[::-1]
    return write_wav_bytes(PCMBuffer(sample_rate=sample_rate, samples=samples.astype(np.float32)))


class RendererIntegrationTests(unittest.TestCase):
    def test_render_and_manifest_replay_are_bit_reproducible(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from dan.voice.models import RenderSnapshot
        from dan.voice.prosody.renderer import OfflineProsodyRenderer
        import dan.voice.prosody.renderer as renderer_module

        class FakeVoiceCatalog:
            revision = "catalog-revision"

        class FakeCatalog:
            voice_catalog = FakeVoiceCatalog()

            @staticmethod
            def gain_for(voice: str, mastering: str) -> float | None:
                return {("M3", "default"): 2.0, ("F4", "default"): 1.0}.get(
                    (voice, mastering)
                )

        class FakeResolver:
            def resolve(self, intent):
                base = 1.0
                voice = "M3" if intent.persona == "dan" else "F4"
                profile = "default"
                return RenderSnapshot(
                    engine="supertonic",
                    engine_version="1.3.1+test",
                    voice_or_style=voice,
                    speed=base * intent.tempo,
                    mastering_profile=profile,
                    dsp="none",
                    pronunciations={},
                    pronunciations_sha256="p" * 64,
                    gain=1.0,
                    asset_sha256={"engine": "a" * 64},
                    config_revision="c" * 64,
                    seed=1,
                    emotion=intent.emotion,
                    tempo_start=intent.tempo,
                    tempo_end=intent.tempo_end or intent.tempo,
                    tone=intent.tone,
                    pause_after=intent.pause_after,
                )

        class FakeEngine:
            def synthesize(self, text, snapshot):
                from dan.voice.tts import SynthesizedChunk

                duration = max(0.35, sum(char.isalnum() for char in text) / (12.3 * snapshot.speed))
                frequency = 135.0 + (snapshot.seed % 7)
                frames = int(round(duration * 44_100))
                time = np.arange(frames, dtype=np.float32) / 44_100
                envelope = 0.78 + 0.18 * np.sin(2 * math.pi * 2.0 * time)
                samples = 0.13 * envelope * np.sin(2 * math.pi * frequency * time)
                edge = min(frames // 4, int(0.03 * 44_100))
                if edge > 1:
                    ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)
                    samples[:edge] *= ramp
                    samples[-edge:] *= ramp[::-1]
                return SynthesizedChunk(
                    text=text,
                    audio=write_wav_bytes(PCMBuffer(44_100, samples.astype(np.float32))),
                )

            def close(self):
                return None

        config = SimpleNamespace(
            voice=SimpleNamespace(
                mastering_binary="ffmpeg",
                supertonic_serve_model="supertonic-3",
                supertonic_lang="pl",
                supertonic_steps=14,
                supertonic_serve_max_chunk_length=400,
            ),
            runtime=SimpleNamespace(runtime_dir="/tmp"),
        )
        renderer = object.__new__(OfflineProsodyRenderer)
        renderer.config = config
        renderer.repo_root = Path("/tmp")
        renderer.voice_root = Path("/tmp/voice")
        renderer.catalog = FakeCatalog()
        renderer.ffmpeg_binary = "ffmpeg"
        director = ProsodyDirector(PERSONAS, settings=DirectorSettings(default_take_count=3))
        lines = parse_scene_text(
            "dan;tempo=1.02;tempo_end=0.94;emotion=contempt;tone=dark;"
            "pause=0.20|Dobra... teraz sprawdzamy pełną myśl.\n"
            "danusia;tempo=0.98;tempo_end=1.03;emotion=mockery;tone=bright;"
            "pause=0.28|Naprawdę myślisz, że tym razem zadziała?"
        )
        plan = director.plan(lines, source_name="scene.txt")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            def fake_postprocess(
                raw_wav: bytes,
                **_: object,
            ) -> bytes:
                # The repository's baseline pytest guard must never spawn
                # ffmpeg or another audio executable. This deterministic
                # in-process stand-in keeps the integration test focused on
                # planning, take selection, manifests and exact replay.
                return raw_wav

            with patch(
                "dan.voice.prosody.integration.build_offline_voice_resolver",
                return_value=FakeResolver(),
            ), patch.object(
                renderer_module, "build_tts_engine", return_value=FakeEngine()
            ), patch.object(
                renderer_module, "postprocess_selected_wav", side_effect=fake_postprocess
            ):
                result = renderer.render_plan(
                    plan,
                    source_bytes=b"fake scene source\n",
                    output_dir=first,
                )
                replayed = renderer.replay_manifest(
                    result.manifest_path,
                    output_dir=second,
                )
            self.assertEqual(result.final_wav_sha256, replayed.final_wav_sha256)
            self.assertTrue(result.final_wav_path.is_file())
            self.assertTrue(result.manifest_path.is_file())
            self.assertGreaterEqual(len(result.selected_segments), 2)
            first_snapshot = result.selected_segments[0].snapshot_json
            self.assertIn('"emotion":"contempt"', first_snapshot)
            self.assertIn('"tone":"dark"', first_snapshot)
            self.assertIn('"tempo_start":1.02', first_snapshot)
            self.assertIn('"tempo_end":0.94', first_snapshot)
            self.assertIn(
                '"pause_after":0.2',
                result.selected_segments[0].directed_snapshot_json,
            )
            self.assertIn('"pause_after":0.0', first_snapshot)

if __name__ == "__main__":
    unittest.main()
