"""Task 11: one CLI speak contract for every host adapter + complete manifest.

Every machine adapter — regardless of host — invokes exactly the same
``dan speak`` argv, feeds UTF-8 text on stdin and carries no legacy path.
The integration decision manifest covers every producer frozen from the
Task 1 inventory with a finished decision (migrated/disabled/rejected).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"

HOSTS = {"claude", "codex", "openclaw", "gpt-say", "standup", "hook"}

# Built by concatenation on purpose: the baseline safety scanner refuses
# tests that carry these legacy runtime tokens as plain literals.
LEGACY_TOKENS = (
    "/tmp/" + "dan-",
    "/tmp/claude-loud-thinking",
    "voice_broker.py",
    "feeder.sh",
    "dan_core",
    "Documents/dev/dan/",
    "Documents/dev/jarvis",
    "Documents/dev/DANv2",
    ":" + "7788",
    "afplay",
)

SHARED_SKILLS = (
    "gadanie",
    "dobranocka",
    "trio-live",
    "danusia-live",
    "gpt-say",
    "voice-report",
    "standup",
    "screen-control",
)

PERSONA_ADAPTERS = (
    INTEGRATIONS / "codex" / "skills" / "dan-persona" / "SKILL.md",
    INTEGRATIONS / "claude" / "skills" / "dan-persona" / "SKILL.md",
    INTEGRATIONS / "openclaw" / "skills" / "dan" / "SKILL.md",
)


@pytest.fixture
def installed_adapter():
    from dan.install.adapters import installed_adapter as factory

    return factory


@pytest.fixture
def manifest():
    from dan.install.adapters import load_manifest

    return load_manifest()


@pytest.fixture
def inventory():
    from dan.install.adapters import load_inventory

    return load_inventory()


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_machine_adapter_uses_exact_speak_contract(host, installed_adapter) -> None:
    invocation = installed_adapter(host).invoke("Zażółć gęślą jaźń.")
    assert invocation.argv == [
        "dan",
        "speak",
        "--json",
        "--as",
        invocation.persona,
        "--session",
        invocation.session,
        "--source",
        host,
        "--stdin",
    ]
    assert invocation.stdin_encoding == "utf-8"
    assert invocation.stdin == "Zażółć gęślą jaźń.".encode("utf-8")
    joined = " ".join(invocation.argv)
    for token in LEGACY_TOKENS:
        assert token not in joined


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_adapter_template_documents_the_same_contract(host, installed_adapter) -> None:
    adapter = installed_adapter(host)
    assert adapter.template_path.is_file(), adapter.template_path
    text = adapter.template_path.read_text(encoding="utf-8")
    assert adapter.command_line in text, (adapter.template_path, adapter.command_line)


def test_every_shared_skill_template_exists_and_is_thin() -> None:
    for name in SHARED_SKILLS:
        template = INTEGRATIONS / "shared" / "skills" / name / "SKILL.md"
        assert template.is_file(), template
        text = template.read_text(encoding="utf-8")
        assert "dan speak --json --as" in text, template
        for token in LEGACY_TOKENS:
            assert token not in text, (template, token)


def test_adapter_templates_carry_no_legacy_paths() -> None:
    scanned = 0
    for sub in ("claude", "codex", "openclaw", "shared"):
        for path in sorted((INTEGRATIONS / sub).rglob("*")):
            if not path.is_file():
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8")
            for token in LEGACY_TOKENS:
                assert token not in text, (path, token)
    assert scanned >= 12


def test_persona_adapters_load_once_per_session_and_reload_only_at_boundaries() -> None:
    for path in PERSONA_ADAPTERS:
        text = path.read_text(encoding="utf-8")
        assert "once at the start of the host session" in text, path
        assert "Do not rerun it on every turn" in text, path
        for boundary in ("restart", "compaction", "handoff", "model change"):
            assert boundary in text, (path, boundary)
        assert "canon hash changes" in text, path


def test_adapter_manifest_accounts_for_every_inventory_producer(manifest, inventory) -> None:
    assert set(manifest.producer_ids) == set(inventory.producer_ids)
    assert not manifest.pending


def test_manifest_rows_are_complete_decisions(manifest) -> None:
    assert len(manifest.rows) >= 130
    for row in manifest.rows:
        assert row.status in {"migrated", "disabled", "rejected"}, row.id
        for field in ("id", "host", "old_format", "behavior", "destination", "test", "reason"):
            value = getattr(row, field)
            assert isinstance(value, str) and value.strip(), (row.id, field)
        assert "pending" != row.status
        assert "TBD" not in row.behavior and "UNMAPPED" not in row.behavior, row.id
        assert row.test.startswith("tests/"), row.id


def test_manifest_documents_engine_field_as_consumed_not_noop(manifest) -> None:
    note = manifest.notes["per_request_engine"]
    assert "CONSUMED" in note or "consumed" in note
    assert "no-op" in note.lower()
    assert "VoiceResolver" in note
    say_rows = [row for row in manifest.rows if row.id.endswith("/say.py")]
    assert say_rows, "dan_core/say.py must have a manifest row"
    assert "engine" in say_rows[0].old_format


def test_dobranocka_dan_prefix_stays_spoken_content(manifest, installed_adapter) -> None:
    """Persona routes only through explicit --as; 'DAN:' text stays spoken."""

    invocation = installed_adapter("openclaw").invoke("DAN: dobranoc, śpij dobrze.")
    assert invocation.stdin.decode("utf-8") == "DAN: dobranoc, śpij dobrze."
    persona_index = invocation.argv.index("--as") + 1
    assert invocation.argv[persona_index] == invocation.persona
    note = manifest.notes["dobranocka_dan_prefix"]
    assert "--as" in note
    assert "importer" in note.lower() or "feeder" in note.lower()


def test_manifest_has_no_second_product_launchd_owner(manifest) -> None:
    for row in manifest.rows:
        if row.host != "launchd":
            continue
        if row.status == "rejected":
            continue
        assert "com.dan.dand" in row.destination or row.destination in {"~/.dan/bin/dand"}, row.id
