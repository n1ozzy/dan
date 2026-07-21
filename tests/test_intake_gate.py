from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from dan.daemon.intake import IntakeClosedError, IntakeGate, IntakeGateError
from dan.store.db import close_quietly, initialize_database


class RecordingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.statements: list[str] = []
        self.fail_lease_delete = False

    @property
    def in_transaction(self) -> bool:
        return self.connection.in_transaction

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        self.statements.append(sql)
        if self.fail_lease_delete and sql.lstrip().startswith("DELETE FROM intake_leases"):
            raise sqlite3.OperationalError("simulated lease cleanup failure")
        return self.connection.execute(sql, parameters)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()


def test_close_blocks_new_admission_but_drains_existing_lease(tmp_path: Path) -> None:
    first = initialize_database(tmp_path / "dan.db")
    second = initialize_database(tmp_path / "dan.db")
    first_gate = IntakeGate(first)
    second_gate = IntakeGate(second)
    with first_gate.admit("text:api"):
        snapshot = second_gate.close(operation_id="restart-1", reason="restart")

        assert snapshot.state == "closed"
        assert snapshot.operation_id == "restart-1"
        assert snapshot.active_leases == 1
        with pytest.raises(IntakeClosedError) as blocked:
            with second_gate.admit("voice_speak"):
                pass
        assert blocked.value.operation_id == "restart-1"
        with pytest.raises(IntakeGateError, match="active intake lease"):
            second_gate.wait_for_drain(0)

    second_gate.wait_for_drain(0)
    reopened = second_gate.reopen(operation_id="restart-1")
    assert reopened.state == "open"
    assert reopened.operation_id == "restart-1"
    close_quietly(first)
    close_quietly(second)


def test_reentrant_admission_finishes_after_close_without_new_lease(
    tmp_path: Path,
) -> None:
    connection = initialize_database(tmp_path / "dan.db")
    gate = IntakeGate(connection)

    with gate.admit("text:api") as outer:
        gate.close(operation_id="cutover-1", reason="cutover")
        with gate.admit("voice_speak") as inner:
            assert inner == outer
            assert gate.snapshot().active_leases == 1
        assert gate.snapshot().active_leases == 1

    assert gate.snapshot().active_leases == 0
    close_quietly(connection)


def test_close_runs_before_close_hook_before_durable_state_change(
    tmp_path: Path,
) -> None:
    connection = initialize_database(tmp_path / "dan.db")
    gate = IntakeGate(connection)
    observed = []

    closed = gate.close(
        operation_id="cutover-2",
        reason="cutover",
        reopen_policy="external",
        before_close=lambda prior: observed.append(prior),
    )

    assert len(observed) == 1
    assert observed[0].state == "open"
    assert observed[0].reopen_policy == "daemon"
    assert closed.state == "closed"
    assert closed.reopen_policy == "external"
    close_quietly(connection)


def test_repeated_close_for_same_operation_is_idempotent(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "dan.db")
    gate = IntakeGate(connection)
    observed: list[str] = []

    first = gate.close(
        operation_id="cutover-2",
        reason="cutover",
        before_close=lambda prior: observed.append(prior.state),
    )
    second = gate.close(
        operation_id="cutover-2",
        reason="replacement reason must be ignored",
        reopen_policy="external",
        before_close=lambda prior: observed.append(f"duplicate:{prior.state}"),
    )

    assert second == first
    assert observed == ["open"]
    assert second.reason == "cutover"
    assert second.reopen_policy == "daemon"
    close_quietly(connection)


def test_close_rolls_back_when_before_close_hook_fails(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "dan.db")
    gate = IntakeGate(connection)

    def fail_journal(_prior) -> None:
        raise OSError("journal fsync failed")

    with pytest.raises(OSError, match="journal fsync failed"):
        gate.close(
            operation_id="cutover-3",
            reason="cutover",
            reopen_policy="external",
            before_close=fail_journal,
        )

    assert gate.snapshot().state == "open"
    close_quietly(connection)


def test_failed_lease_cleanup_does_not_leave_reentrant_bypass(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "dan.db")
    proxy = RecordingConnection(connection)
    gate = IntakeGate(proxy)

    with pytest.raises(sqlite3.OperationalError, match="lease cleanup failure"):
        with gate.admit("text:api"):
            proxy.fail_lease_delete = True

    proxy.fail_lease_delete = False
    gate.close(operation_id="restart-after-cleanup-error", reason="restart")

    with pytest.raises(IntakeClosedError):
        with gate.admit("text:api"):
            pass

    assert gate.snapshot().active_leases == 1
    connection.execute("DELETE FROM intake_leases")
    connection.commit()
    close_quietly(connection)


def test_snapshot_reads_gate_and_lease_count_in_one_statement(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "dan.db")
    proxy = RecordingConnection(connection)
    gate = IntakeGate(proxy)

    snapshot = gate.snapshot()

    selects = [
        statement
        for statement in proxy.statements
        if statement.lstrip().startswith("SELECT")
    ]
    assert snapshot.state == "open"
    assert snapshot.active_leases == 0
    assert len(selects) == 1
    close_quietly(connection)


def test_lease_from_a_dead_process_does_not_wedge_the_gate(tmp_path: Path) -> None:
    """A crash-orphaned lease must not make the daemon unstartable.

    An unclean shutdown leaves the row behind, and reopen() refused forever
    because the count included leases whose owning process is long gone —
    only manual DB surgery got the daemon back.
    """

    conn = initialize_database(tmp_path / "dan.db")
    try:
        gate = IntakeGate(conn)
        gate.close(operation_id="op-restart", reason="SIGTERM")
        with conn:
            conn.execute(
                "INSERT INTO intake_leases (token, channel, owner_pid, acquired_at) "
                "VALUES (?, ?, ?, ?)",
                ("orphan", "text:voice", _dead_pid(), "2026-07-21T13:29:44Z"),
            )

        assert gate.snapshot().active_leases == 0
        assert gate.reopen(operation_id="op-restart").state == "open"
    finally:
        close_quietly(conn)


def test_lease_from_a_live_process_still_blocks_reopen(tmp_path: Path) -> None:
    import os

    conn = initialize_database(tmp_path / "dan.db")
    try:
        gate = IntakeGate(conn)
        gate.close(operation_id="op-restart", reason="SIGTERM")
        with conn:
            conn.execute(
                "INSERT INTO intake_leases (token, channel, owner_pid, acquired_at) "
                "VALUES (?, ?, ?, ?)",
                ("live", "text:voice", os.getpid(), "2026-07-21T13:29:44Z"),
            )

        assert gate.snapshot().active_leases == 1
        with pytest.raises(IntakeGateError, match="active lease"):
            gate.reopen(operation_id="op-restart")
    finally:
        close_quietly(conn)


def _dead_pid() -> int:
    """A pid that is certainly not running: run a child and reap it."""

    import subprocess

    child = subprocess.Popen(["/usr/bin/true"])
    child.wait()
    return child.pid
