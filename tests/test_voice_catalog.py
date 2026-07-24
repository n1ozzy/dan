"""Structural checks on the real config/voice catalog.

This module checks the catalog mechanics. Owner decisions such as the exact
two-person cast, DAN's voice, neutral base speed and one calibrated gain per
active route are pinned centrally by ``tests/test_voice_policy.py``.

What stays testable: the catalog loads, it has no duplicate keys, every persona
carries a full field set, and the lookup contracts behave.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.voice.assets import load_voice_catalog
from dan.voice.assets import AssetVerificationError

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {"engine", "voice", "speed", "seed", "mastering", "dsp"}


def load() -> object:
    return load_voice_catalog(ROOT / "config" / "voice")


def test_catalog_loads_without_duplicate_keys() -> None:
    catalog = load()

    assert catalog.personas
    assert catalog.duplicate_keys == ()


def test_versioned_catalog_exposes_only_dan_and_danusia() -> None:
    catalog = load()

    assert set(catalog.personas) == {"dan", "danusia"}


@pytest.mark.parametrize(
    "sections",
    (
        ("dan",),
        ("danusia",),
        ("dan", "danusia", "intruder"),
    ),
)
def test_loader_rejects_any_catalog_other_than_exact_owner_cast(
    tmp_path: Path,
    sections: tuple[str, ...],
) -> None:
    blocks = []
    for name in sections:
        blocks.append(
            f"""[{name}]
engine = "supertonic"
voice = "M3"
mastering = "default"
speed = 1.0
seed = 1
dsp = "none"
"""
        )
    (tmp_path / "personas.toml").write_text("\n".join(blocks), encoding="utf-8")
    (tmp_path / "pronunciations.toml").write_text(
        'DAN = "Dan"\n',
        encoding="utf-8",
    )
    (tmp_path / "gains.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(AssetVerificationError, match="exactly"):
        load_voice_catalog(tmp_path)


def test_loader_rejects_retired_mastering_even_when_gains_match(
    tmp_path: Path,
) -> None:
    (tmp_path / "personas.toml").write_text(
        """[dan]
engine = "supertonic"
voice = "M3"
mastering = "default"
speed = 1.0
seed = 1
dsp = "none"

[danusia]
engine = "supertonic"
voice = "F4"
mastering = "default"
speed = 1.0
seed = 1
dsp = "none"
""",
        encoding="utf-8",
    )
    (tmp_path / "pronunciations.toml").write_text("", encoding="utf-8")
    (tmp_path / "gains.json").write_text(
        '{"F4|default": 0.0, "M3|default": 0.0}\n',
        encoding="utf-8",
    )

    with pytest.raises(AssetVerificationError, match="mastering"):
        load_voice_catalog(tmp_path)


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

    assert catalog.gain_for("definitely-not-a-voice", "default") is None
    assert catalog.gain_fallback
    for key, value in catalog.gains.items():
        assert "|" in key
        assert isinstance(value, float)
        assert catalog.gain_for(*key.split("|", 1)) == value


def test_gains_cover_exactly_the_two_active_routes() -> None:
    catalog = load()

    expected = {
        f"{spec['voice']}|{spec['mastering']}"
        for spec in catalog.personas.values()
    }
    assert set(catalog.gains) == expected


def test_shared_pronunciations_are_valid_strings() -> None:
    catalog = load()

    assert catalog.pronunciations
    assert all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in catalog.pronunciations.items()
    )
