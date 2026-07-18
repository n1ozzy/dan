from __future__ import annotations

from pathlib import Path

import pytest

from dan.daemon.intake import IntakeClosedError, IntakeGate, IntakeGateError
from dan.store.db import close_quietly, initialize_database


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
