"""Cutover state machine: strict phase order, append-only journal, resume."""

from __future__ import annotations

import stat

import pytest


def _committed_phases(entries: list[dict]) -> list[str]:
    return [e["phase"] for e in entries if e["operation"] == "phase-committed"]


def test_apply_commits_every_phase_in_declared_order(cutover_fixture) -> None:
    from dan.migration.journal import CutoverPhase

    report = cutover_fixture.apply()
    entries = cutover_fixture.journal_entries(report.journal)
    assert _committed_phases(entries) == [phase.value for phase in CutoverPhase]


def test_journal_records_inverse_before_each_mutation(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    entries = cutover_fixture.journal_entries(report.journal)
    mutations = [
        e
        for e in entries
        if e["operation"] not in {"phase-committed", "note"}
    ]
    assert mutations, "apply must journal its mutations"
    assert all(e["rollback_operation"] for e in mutations)


def test_journal_dir_and_file_are_private_and_under_home(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    journal_dir = report.journal
    assert journal_dir.is_relative_to(cutover_fixture.home / ".dan" / "migration")
    assert stat.S_IMODE(journal_dir.stat().st_mode) == 0o700
    journal_file = journal_dir / "journal.jsonl"
    assert stat.S_IMODE(journal_file.stat().st_mode) == 0o600


def test_apply_requires_flag_and_exact_manifest_sha(cutover_fixture) -> None:
    from dan.migration.cutover import CutoverBlocked, CutoverEngine

    engine = CutoverEngine(
        manifest=cutover_fixture.manifest,
        home=cutover_fixture.home,
        probe=cutover_fixture.probe,
        launchctl=cutover_fixture.launchctl,
        runtime_starter=cutover_fixture.runtime.start,
    )
    with pytest.raises(CutoverBlocked, match="manifest"):
        engine.apply(manifest_sha256="0" * 64)


def test_resume_continues_from_last_committed_phase(cutover_fixture) -> None:
    from dan.migration.cutover import CutoverInterrupted
    from dan.migration.journal import CutoverPhase

    with pytest.raises(CutoverInterrupted):
        cutover_fixture.apply(interrupt_after=CutoverPhase.DATABASES_BACKED_UP)
    journal_dir = cutover_fixture.latest_journal_dir()
    before = cutover_fixture.journal_bytes(journal_dir)
    committed = _committed_phases(cutover_fixture.journal_entries(journal_dir))
    assert committed[-1] == CutoverPhase.DATABASES_BACKED_UP.value

    report = cutover_fixture.resume(journal_dir)

    after = cutover_fixture.journal_bytes(journal_dir)
    assert after.startswith(before), "journal must be append-only across resume"
    phases = _committed_phases(cutover_fixture.journal_entries(report.journal))
    assert phases == [phase.value for phase in CutoverPhase]
    assert len(phases) == len(set(phases)), "no phase may be committed twice"
