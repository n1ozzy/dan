"""Tests for dan.voice.persona_editor — the panel's personas.toml editor.

The editor performs a TEXTUAL edit of a single ``[persona]`` section: comments
and layout everywhere else must survive byte-for-byte, validation is
fail-closed (file untouched on any error), and the write is atomic.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from dan.voice.persona_editor import (
    PersonaEdit,
    PersonaEditError,
    list_personas,
    set_persona_voice,
)

SAMPLE = """# Versioned voice routes. Header comment must survive edits.

[jarvis]
engine = "supertonic"
voice = "M3"  # legacy alias of dan
mastering = "raw"
speed = 1.28
dsp = "none"

[dan]
engine = "supertonic"
voice = "M3"
mastering = "raw"
speed = 1.28
dsp = "none"

[danusia]
engine = "supertonic"
voice = "F4"
mastering = "clean"
speed = 1.28
dsp = "none"
"""


@pytest.fixture()
def catalog_dir(tmp_path: Path) -> Path:
    (tmp_path / "personas.toml").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def read_personas(catalog_dir: Path) -> dict:
    text = (catalog_dir / "personas.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def read_raw(catalog_dir: Path) -> str:
    return (catalog_dir / "personas.toml").read_text(encoding="utf-8")


class TestListPersonas:
    def test_returns_every_section_with_fields(self, catalog_dir: Path) -> None:
        personas = list_personas(catalog_dir)
        assert set(personas) == {"jarvis", "dan", "danusia"}
        assert personas["danusia"]["voice"] == "F4"
        assert personas["danusia"]["mastering"] == "clean"
        assert personas["danusia"]["speed"] == pytest.approx(1.28)
        assert personas["jarvis"]["engine"] == "supertonic"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PersonaEditError):
            list_personas(tmp_path)


class TestSetPersonaVoice:
    def test_changes_voice_of_target_section_only(self, catalog_dir: Path) -> None:
        set_persona_voice(catalog_dir, "dan", voice="M1")
        data = read_personas(catalog_dir)
        assert data["dan"]["voice"] == "M1"
        assert data["jarvis"]["voice"] == "M3"
        assert data["danusia"]["voice"] == "F4"

    def test_preserves_comments_and_layout_outside_changed_line(
        self, catalog_dir: Path
    ) -> None:
        set_persona_voice(catalog_dir, "dan", voice="M1")
        text = read_raw(catalog_dir)
        assert text.startswith(
            "# Versioned voice routes. Header comment must survive edits.\n"
        )
        assert "# legacy alias of dan" in text

    def test_drops_inline_comment_of_changed_line(self, catalog_dir: Path) -> None:
        set_persona_voice(catalog_dir, "jarvis", voice="M1")
        text = read_raw(catalog_dir)
        assert "# legacy alias of dan" not in text
        assert read_personas(catalog_dir)["jarvis"]["voice"] == "M1"

    def test_sets_voice_and_speed_together(self, catalog_dir: Path) -> None:
        set_persona_voice(catalog_dir, "danusia", voice="F2", speed=1.1)
        data = read_personas(catalog_dir)
        assert data["danusia"]["voice"] == "F2"
        assert data["danusia"]["speed"] == pytest.approx(1.1)

    def test_sets_speed_alone_keeps_voice(self, catalog_dir: Path) -> None:
        set_persona_voice(catalog_dir, "dan", speed=1.5)
        data = read_personas(catalog_dir)
        assert data["dan"]["voice"] == "M3"
        assert data["dan"]["speed"] == pytest.approx(1.5)

    def test_sets_mastering_with_allowed_set(self, catalog_dir: Path) -> None:
        edit = set_persona_voice(
            catalog_dir,
            "danusia",
            mastering="raw",
            allowed_mastering={"raw", "clean"},
        )
        assert read_personas(catalog_dir)["danusia"]["mastering"] == "raw"
        assert edit.changes["mastering"] == ("clean", "raw")

    def test_result_reports_old_and_new_values(self, catalog_dir: Path) -> None:
        edit = set_persona_voice(catalog_dir, "dan", voice="M1", speed=1.1)
        assert isinstance(edit, PersonaEdit)
        assert edit.persona == "dan"
        assert edit.changes["voice"] == ("M3", "M1")
        old_speed, new_speed = edit.changes["speed"]
        assert old_speed == pytest.approx(1.28)
        assert new_speed == pytest.approx(1.1)

    def test_file_stays_parseable_toml_and_speed_stays_float(
        self, catalog_dir: Path
    ) -> None:
        set_persona_voice(catalog_dir, "dan", voice="M1", speed=1.0)
        data = read_personas(catalog_dir)
        assert data["dan"]["speed"] == pytest.approx(1.0)
        assert isinstance(data["dan"]["speed"], float)

    def test_no_leftover_temp_files(self, catalog_dir: Path) -> None:
        set_persona_voice(catalog_dir, "dan", voice="M1")
        assert [p.name for p in catalog_dir.iterdir()] == ["personas.toml"]

    @pytest.mark.parametrize("boundary", [0.5, 2.0])
    def test_speed_bounds_are_inclusive(
        self, catalog_dir: Path, boundary: float
    ) -> None:
        set_persona_voice(catalog_dir, "dan", speed=boundary)
        assert read_personas(catalog_dir)["dan"]["speed"] == pytest.approx(boundary)


class TestValidation:
    def test_unknown_persona_fails_and_leaves_file_untouched(
        self, catalog_dir: Path
    ) -> None:
        before = read_raw(catalog_dir)
        with pytest.raises(PersonaEditError, match="persona"):
            set_persona_voice(catalog_dir, "nikt_taki", voice="M1")
        assert read_raw(catalog_dir) == before

    def test_voice_outside_allowed_set_fails(self, catalog_dir: Path) -> None:
        before = read_raw(catalog_dir)
        with pytest.raises(PersonaEditError, match="voice"):
            set_persona_voice(
                catalog_dir, "dan", voice="X9", allowed_voices={"M1", "M3"}
            )
        assert read_raw(catalog_dir) == before

    def test_mastering_outside_allowed_set_fails(self, catalog_dir: Path) -> None:
        before = read_raw(catalog_dir)
        with pytest.raises(PersonaEditError, match="mastering"):
            set_persona_voice(
                catalog_dir, "dan", mastering="wet", allowed_mastering={"raw", "clean"}
            )
        assert read_raw(catalog_dir) == before

    @pytest.mark.parametrize("bad", [0.4, 2.5, 0.0, -1.0])
    def test_speed_out_of_range_fails(self, catalog_dir: Path, bad: float) -> None:
        before = read_raw(catalog_dir)
        with pytest.raises(PersonaEditError, match="speed"):
            set_persona_voice(catalog_dir, "dan", speed=bad)
        assert read_raw(catalog_dir) == before

    def test_nothing_to_change_fails(self, catalog_dir: Path) -> None:
        with pytest.raises(PersonaEditError):
            set_persona_voice(catalog_dir, "dan")


class TestFilePermissions:
    """The atomic write must not tighten the catalog's mode.

    ``tempfile.mkstemp`` creates 0600 and ``os.replace`` carries that mode onto
    the target, so a repo file tracked as 100644 silently became owner-only
    after every panel edit.
    """

    def test_edit_preserves_world_readable_mode(self, catalog_dir: Path) -> None:
        path = catalog_dir / "personas.toml"
        path.chmod(0o644)
        set_persona_voice(catalog_dir, "dan", voice="M1")
        assert path.stat().st_mode & 0o777 == 0o644

    def test_edit_preserves_restrictive_mode(self, catalog_dir: Path) -> None:
        path = catalog_dir / "personas.toml"
        path.chmod(0o600)
        set_persona_voice(catalog_dir, "dan", voice="M1")
        assert path.stat().st_mode & 0o777 == 0o600


class TestCollateralDamage:
    """The post-write check must prove nothing outside the request moved."""

    def test_decoy_line_in_multiline_string_survives_a_noop_edit(
        self, tmp_path: Path
    ) -> None:
        # A decoy `voice = ...` line inside a multi-line string is not a field.
        # Requesting the value the section already has used to rewrite the decoy
        # while the post-check (which only looked at the requested fields)
        # still passed.
        source = '''[dan]
engine = "supertonic"
notes = """
voice = "DECOY"
"""
voice = "M3"
mastering = "raw"
speed = 1.28
'''
        (tmp_path / "personas.toml").write_text(source, encoding="utf-8")
        before = read_raw(tmp_path)
        with pytest.raises(PersonaEditError):
            set_persona_voice(tmp_path, "dan", voice="M3")
        assert read_raw(tmp_path) == before

    def test_unrelated_persona_is_never_touched(self, catalog_dir: Path) -> None:
        set_persona_voice(catalog_dir, "dan", voice="M1")
        data = read_personas(catalog_dir)
        assert data["danusia"]["voice"] == "F4"
        assert data["jarvis"]["voice"] == "M3"


class TestValueEscaping:
    """A raw string concatenation must never emit unbalanced TOML."""

    @pytest.mark.parametrize("hostile", ['M1"', 'M1\\', 'M1"\nspeed = 9.9'])
    def test_hostile_voice_value_fails_closed(
        self, catalog_dir: Path, hostile: str
    ) -> None:
        before = read_raw(catalog_dir)
        with pytest.raises(PersonaEditError):
            set_persona_voice(catalog_dir, "dan", voice=hostile)
        assert read_raw(catalog_dir) == before
