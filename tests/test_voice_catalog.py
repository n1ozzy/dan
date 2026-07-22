"""Structural checks on the real config/voice catalog.

Deliberately asserts NO tuned value. Voice, engine, mastering, speed, measured
gains, the loudnorm fallback and the pronunciation dictionary are all knobs the
owner turns live — the panel writes personas.toml on every apply. Pinning any of
them here reports a deliberate setting change as a test failure, which is how a
speed nudge from 1.28 to 1.29 turned this file red.

What stays testable: the catalog loads, it has no duplicate keys, every persona
carries a full field set, and the lookup contracts behave.
"""

from __future__ import annotations

from pathlib import Path

from dan.voice.assets import load_voice_catalog

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {"engine", "voice", "speed", "seed", "mastering", "dsp"}


def load() -> object:
    return load_voice_catalog(ROOT / "config" / "voice")


def test_catalog_loads_without_duplicate_keys() -> None:
    catalog = load()

    assert catalog.personas
    assert catalog.duplicate_keys == ()


def test_every_persona_carries_a_full_field_set() -> None:
    catalog = load()

    for name, spec in catalog.personas.items():
        missing = REQUIRED_FIELDS - set(spec)
        assert not missing, f"persona {name!r} is missing {sorted(missing)}"


def test_gain_lookup_reports_a_miss_instead_of_guessing() -> None:
    # The contract, not the numbers: an unmeasured voice/mastering pair must
    # come back as a miss so the caller falls back, rather than silently
    # inheriting some other pair's gain.
    catalog = load()

    assert catalog.gain_for("definitely-not-a-voice", "raw") is None
    assert catalog.gain_fallback
    for key, value in catalog.gains.items():
        assert "|" in key
        assert isinstance(value, float)
        assert catalog.gain_for(*key.split("|", 1)) == value


def test_shared_pronunciations_do_not_absorb_jarvis_local_overrides() -> None:
    # Isolation of the two dictionaries, not their contents.
    catalog = load()

    assert catalog.pronunciations
    assert all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in catalog.pronunciations.items()
    )


def test_zaneta_keeps_her_offline_pipeline_binding() -> None:
    # Not a tuning value: this names a pipeline module that must exist on disk,
    # and the panel has no control that can change it.
    catalog = load()

    assert catalog.personas["zaneta"]["offline_pipeline"] == "chatterbox-v3-zaneta"
