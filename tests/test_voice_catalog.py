from __future__ import annotations

from pathlib import Path

from dan.voice.assets import load_voice_catalog


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PERSONAS = {
    "dan",
    "danusia",
    "zaneta",
    "zdzicho",
    "krysia",
    "komentator",
    "spiker",
    "ksiadz",
    "typ_z_telefonu",
    "blondyna",
    "zagadka",
    "radiowiec",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "F1",
    "F2",
    "F3",
    "F4",
    "F5",
}


def test_catalog_has_full_cast_and_one_owner() -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")

    assert EXPECTED_PERSONAS <= set(catalog.personas)
    assert catalog.duplicate_keys == ()
    for spec in catalog.personas.values():
        assert set(spec) >= {"engine", "voice", "speed", "mastering", "dsp"}


def test_audited_canonical_routes_are_preserved() -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")

    assert catalog.personas["dan"] == {
        "engine": "supertonic",
        "voice": "M3",
        "mastering": "raw",
        "speed": 1.28,
        "dsp": "none",
    }
    assert catalog.personas["danusia"] == {
        "engine": "supertonic",
        "voice": "F4",
        "mastering": "clean",
        "speed": 1.28,
        "dsp": "none",
    }
    assert catalog.personas["M3"]["voice"] == "M3"
    assert catalog.personas["M3"]["mastering"] == "raw"
    assert catalog.personas["M3"]["speed"] == 1.25
    assert catalog.personas["F4"]["voice"] == "F4"
    assert catalog.personas["F4"]["mastering"] == "clean"
    assert catalog.personas["F4"]["speed"] == 1.25
    assert catalog.personas["ksiadz"]["voice"] == "M1"
    assert catalog.personas["ksiadz"]["speed"] == 1.05


def test_zaneta_has_explicit_offline_pipeline_and_live_fallback() -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")
    zaneta = catalog.personas["zaneta"]

    assert zaneta["offline_pipeline"] == "chatterbox-v3-zaneta"
    assert zaneta["engine"] == "supertonic"
    assert (zaneta["voice"], zaneta["speed"], zaneta["mastering"]) == (
        "F2",
        1.15,
        "raw",
    )


def test_measured_gains_are_preserved_and_missing_pairs_use_loudnorm() -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")

    assert catalog.gains["M3|raw"] == 8.98
    assert catalog.gains["F4|clean"] == 7.97
    assert catalog.gain_for("M3", "raw") == 8.98
    assert catalog.gain_for("F2", "raw") is None
    assert catalog.gain_fallback == "loudnorm=I=-14:TP=-2.0:LRA=7,aresample=44100"


def test_shared_pronunciations_do_not_absorb_jarvis_local_overrides() -> None:
    catalog = load_voice_catalog(ROOT / "config" / "voice")

    assert catalog.pronunciations["bug"] == "bug"
    assert catalog.pronunciations["backend"] == "bakend"
    assert "broker" not in catalog.pronunciations
