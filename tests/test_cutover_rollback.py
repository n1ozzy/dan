"""Rollback restores non-database bytes and complete logical database state."""

from __future__ import annotations

import sqlite3

import pytest


def test_rollback_restores_non_intake_files_byte_for_byte(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    cutover_fixture.rollback(report.journal)
    assert (
        cutover_fixture.before_non_intake_hash
        == cutover_fixture.tree_hash_without_intake_database()
    )


def test_rollback_restores_legacy_intake_state_from_before_cutover(
    cutover_fixture,
) -> None:
    report = cutover_fixture.apply()
    cutover_fixture.rollback(report.journal)

    connection = sqlite3.connect(cutover_fixture.home / ".jarvis" / "jarvis.db")
    try:
        row = connection.execute(
            """
            SELECT state, operation_id, reason, reopen_policy, closed_at, reopened_at
            FROM intake_gate WHERE singleton = 1
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == ("open", None, None, "daemon", None, None)
    assert cutover_fixture.intake_database_dump() == cutover_fixture.before_intake_dump


def test_restore_intake_refuses_foreign_operation_without_mutating_gate(
    cutover_fixture,
) -> None:
    from dan.migration.journal import Journal
    from dan.migration.rollback import RollbackBlocked, RollbackReport, _undo

    cutover = cutover_fixture.apply()
    journal = Journal.open(cutover.journal)
    entry = next(
        candidate
        for candidate in journal.entries()
        if candidate.rollback_operation.startswith("restore-intake:")
    )
    database = cutover_fixture.home / ".jarvis" / "jarvis.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            UPDATE intake_gate
            SET state = 'closed', operation_id = 'foreign-operation',
                reason = 'new owner', reopen_policy = 'external'
            WHERE singleton = 1
            """
        )
        connection.commit()
        before = connection.execute(
            """
            SELECT state, operation_id, reason, reopen_policy, closed_at, reopened_at
            FROM intake_gate WHERE singleton = 1
            """
        ).fetchone()
    finally:
        connection.close()

    with pytest.raises(RollbackBlocked, match="foreign-operation"):
        _undo(
            entry,
            journal=journal,
            home=cutover_fixture.home,
            launchctl=cutover_fixture.launchctl,
            report=RollbackReport(journal=journal.directory),
        )

    connection = sqlite3.connect(database)
    try:
        after = connection.execute(
            """
            SELECT state, operation_id, reason, reopen_policy, closed_at, reopened_at
            FROM intake_gate WHERE singleton = 1
            """
        ).fetchone()
    finally:
        connection.close()
    assert after == before


def test_apply_moves_trees_and_installs_new_root(cutover_fixture) -> None:
    dev = cutover_fixture.home / "Documents" / "dev"
    report = cutover_fixture.apply()

    # Old dan tree parked under the stamped migration backup root.
    backups = cutover_fixture.home / "Documents" / "DAN-migration-backups"
    parked = sorted(backups.glob("*/dev-dan"))
    assert parked and (parked[-1] / "config" / "persona" / "DAN.md").is_file()

    # Accepted integration tree now lives at dev/DAN (case-safe rename).
    assert (dev / "DAN" / "pyproject.toml").is_file()
    assert not any(entry.name == "jarvis" for entry in dev.iterdir())

    # Donors untouched.
    for donor in ("DANv2", "menubar-controller"):
        assert (dev / donor / "donor.txt").is_file()

    # New runtime cold-started from the new root, exactly once.
    assert cutover_fixture.runtime.started == [dev / "DAN"]
    assert report.journal.is_dir()


def test_rollback_reverses_case_sensitive_moves_and_donors_survive(cutover_fixture) -> None:
    dev = cutover_fixture.home / "Documents" / "dev"
    report = cutover_fixture.apply()
    cutover_fixture.rollback(report.journal)

    assert (dev / "jarvis" / "pyproject.toml").is_file()
    assert (dev / "dan" / "config" / "persona" / "DAN.md").is_file()
    assert not (dev / "DAN" / "pyproject.toml").exists() or (
        (dev / "dan").resolve() == (dev / "DAN").resolve()
    )
    for donor in ("DANv2", "menubar-controller"):
        assert (dev / donor / "donor.txt").is_file()


def test_rollback_stops_new_runtime_before_restoring(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    rollback_report = cutover_fixture.rollback(report.journal)
    assert cutover_fixture.runtime.stopped == 1
    assert rollback_report.old_runtime_start_allowed is True
