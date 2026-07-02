"""G0 voice sentence-streaming design contract tests (docs-only stage).

G0 ships a design document, not runtime code. These tests pin the load-bearing
decisions so later stages (G3/G4) cannot silently drift from them.
"""

from __future__ import annotations

from pathlib import Path

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "VOICE_STREAMING.md"

FORBIDDEN_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_streaming_design_doc_exists_and_is_substantial() -> None:
    body = text()
    assert len(body) > 4000
    assert "first-sound" in body.lower()


def test_design_keeps_final_text_canonical_and_optional_streaming() -> None:
    body = text()

    assert "on_delta" in body
    assert "canonical" in body.lower()
    # Adapters that cannot stream must keep working unchanged.
    assert "optional" in body.lower()


def test_design_pins_sentence_chunker_and_tool_call_safety() -> None:
    body = text()

    assert "SentenceChunker" in body
    assert "<jarvis_tool_call>" in body
    lowered = body.lower()
    assert "fail-closed" in lowered
    # Tool-call blocks must never be spoken.
    assert "never" in lowered and "spoken" in lowered


def test_design_defines_fillers_policy_limits() -> None:
    body = text().lower()

    assert "filler" in body
    assert "at most one" in body or "max 1" in body or "najwyżej jeden" in body


def test_design_covers_cancellation_and_barge_in() -> None:
    body = text().lower()

    assert "barge-in" in body
    assert "cancelled" in body


def test_design_declares_no_schema_change_and_no_delta_events() -> None:
    body = text().lower()

    assert "no schema change" in body or "zero schema" in body
    # Deltas are transient: the audit trail never records partial tokens.
    assert "delta" in body and "not persisted" in body


def test_design_respects_engine_decrees_and_single_speaker() -> None:
    body = text()

    for banned in ("edgeTTS", "piper", "XTTS"):
        assert banned in body  # named as banned, per decree §7.3
    lowered = body.lower()
    assert "banned" in lowered or "zakaz" in lowered
    assert "supertonic" in lowered and "chatterbox" in lowered
    assert "only the broker" in lowered or "broker is the only" in lowered


def test_design_avoids_forbidden_legacy_references() -> None:
    body = text()
    offenders = [snippet for snippet in FORBIDDEN_SNIPPETS if snippet in body]
    assert offenders == []


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
