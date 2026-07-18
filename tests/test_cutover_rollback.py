"""Rollback restores paths, plists, databases and adapters byte-for-byte."""

from __future__ import annotations


def test_rollback_restores_paths_plists_databases_and_adapters(cutover_fixture) -> None:
    report = cutover_fixture.apply()
    cutover_fixture.rollback(report.journal)
    assert cutover_fixture.tree_hash() == cutover_fixture.before_hash


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
