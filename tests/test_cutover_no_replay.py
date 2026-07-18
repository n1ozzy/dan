"""An interrupted request is cancelled forever — never replayed after rollback."""

from __future__ import annotations

import pytest


def test_interrupted_request_is_not_replayed_after_rollback(cutover_fixture) -> None:
    request_id = cutover_fixture.speaking_request()
    report = cutover_fixture.apply(cancel_in_flight=True)
    cutover_fixture.rollback(report.journal)
    assert cutover_fixture.request(request_id).status == "cancelled"
    assert cutover_fixture.play_count(request_id) == 0
    assert cutover_fixture.runtime_state().speaking is None


def test_apply_without_cancel_flag_refuses_in_flight_request(cutover_fixture) -> None:
    from dan.migration.cutover import CutoverBlocked

    cutover_fixture.speaking_request()
    with pytest.raises(CutoverBlocked, match="speaking"):
        cutover_fixture.apply()


def test_cancellation_is_journaled_as_never_replay(cutover_fixture) -> None:
    request_id = cutover_fixture.speaking_request()
    report = cutover_fixture.apply(cancel_in_flight=True)
    entries = cutover_fixture.journal_entries(report.journal)
    cancellations = [e for e in entries if e["operation"] == "cancel-request"]
    assert [e["source"] for e in cancellations] == [request_id]
    assert all(e["rollback_operation"] == "never-replay" for e in cancellations)

    # The cancellation itself survives rollback: still cancelled, never played.
    cutover_fixture.rollback(report.journal)
    assert cutover_fixture.request(request_id).status == "cancelled"
    assert cutover_fixture.play_count(request_id) == 0
