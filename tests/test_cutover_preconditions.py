"""Cutover preconditions: the engine refuses anything less than quiescence."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("state", ["queued", "synthesizing", "speaking"])
def test_cutover_refuses_non_quiescent_queue(state, cutover) -> None:
    from dan.migration.cutover import CutoverBlocked

    cutover.fixture_queue(state)
    with pytest.raises(CutoverBlocked, match=state):
        cutover.prepare()


def test_cutover_refuses_live_db_writer(cutover) -> None:
    from dan.migration.cutover import CutoverBlocked

    cutover.fixture_writer(pid=777, path="~/.jarvis/jarvis.db")
    with pytest.raises(CutoverBlocked, match="writer"):
        cutover.prepare()


def test_cutover_requires_every_producer_decision(cutover) -> None:
    from dan.migration.cutover import CutoverBlocked

    cutover.manifest.producers["old-feeder"].decision = None
    with pytest.raises(CutoverBlocked, match="old-feeder"):
        cutover.prepare()


def test_cutover_refuses_unrecognized_process_and_never_kills(cutover) -> None:
    from dan.migration.cutover import CutoverBlocked
    from dan.migration.runtime_probe import ProbedProcess

    cutover.probe.add_process(ProbedProcess(pid=4242, command="mystery-daemon"))
    with pytest.raises(CutoverBlocked, match="mystery-daemon"):
        cutover.prepare()
    assert cutover.launchctl.calls == []


def test_quiescent_fixture_passes_preconditions(cutover) -> None:
    cutover.prepare()
