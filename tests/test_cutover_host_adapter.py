"""Focused production-host cutover adapter boundaries with synthetic state."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dan.daemon.intake import IntakeGate
from dan.migration.cutover import CutoverBlocked, CutoverEngine
from dan.migration.host_adapter import SystemCutoverHostAdapter
from tests.cutover_helpers import tree_hash


class LaunchctlRunner:
    def __init__(self, *, loaded: bool) -> None:
        self.loaded = loaded
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command, **_kwargs) -> subprocess.CompletedProcess[str]:
        arguments = tuple(str(value) for value in command[1:])
        self.calls.append(arguments)
        verb = arguments[0]
        if verb == "print":
            return self._result(0 if self.loaded else 113)
        if verb == "bootout":
            if not self.loaded:
                return self._result(113)
            self.loaded = False
            return self._result(0)
        if verb == "bootstrap":
            if self.loaded:
                return self._result(5)
            self.loaded = True
            return self._result(0)
        raise AssertionError(arguments)

    @staticmethod
    def _result(returncode: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, "", "synthetic launchctl")


def test_validation_rejects_foreign_closed_gate_before_journal_write(
    cutover_fixture,
) -> None:
    database = cutover_fixture.home / ".jarvis" / "jarvis.db"
    connection = sqlite3.connect(database)
    try:
        IntakeGate(connection).close(
            operation_id="foreign-operation",
            reason="another lifecycle operation",
        )
    finally:
        connection.close()
    before = tree_hash(cutover_fixture.home)
    engine = CutoverEngine(
        manifest=cutover_fixture.manifest,
        home=cutover_fixture.home,
        probe=cutover_fixture.probe,
        host_adapter=SystemCutoverHostAdapter(launchctl=Path(sys.executable)),
    )

    with pytest.raises(CutoverBlocked, match="foreign-operation"):
        engine.apply(manifest_sha256=cutover_fixture.manifest.sha256)

    assert tree_hash(cutover_fixture.home) == before
    assert not (cutover_fixture.home / ".dan" / "migration").exists()


def test_wait_for_intake_drain_uses_keyword_timeout(cutover_fixture) -> None:
    adapter = SystemCutoverHostAdapter(launchctl=Path(sys.executable))
    adapter.validate(
        cutover_fixture.manifest,
        cutover_fixture.home,
        operation_id="cutover-operation",
    )

    adapter.wait_for_intake_drain()


def test_close_intake_translates_foreign_gate_race_to_cutover_blocked(
    cutover_fixture,
) -> None:
    adapter = SystemCutoverHostAdapter(launchctl=Path(sys.executable))
    adapter.validate(
        cutover_fixture.manifest,
        cutover_fixture.home,
        operation_id="cutover-operation",
    )
    connection = sqlite3.connect(adapter.intake_database)
    try:
        IntakeGate(connection).close(
            operation_id="foreign-operation",
            reason="won the validation-to-close race",
        )
    finally:
        connection.close()

    with pytest.raises(CutoverBlocked, match="foreign-operation"):
        adapter.close_intake(
            operation_id="cutover-operation",
            reason="release cutover",
            before_close=lambda _path, _state: None,
        )


def test_close_intake_maps_callback_error_and_keeps_gate_open(
    cutover_fixture,
) -> None:
    adapter = SystemCutoverHostAdapter(launchctl=Path(sys.executable))
    adapter.validate(
        cutover_fixture.manifest,
        cutover_fixture.home,
        operation_id="cutover-operation",
    )

    def fail_callback(_path, _state) -> None:
        raise RuntimeError("journal fsync failed")

    with pytest.raises(CutoverBlocked, match="journal fsync failed"):
        adapter.close_intake(
            operation_id="cutover-operation",
            reason="release cutover",
            before_close=fail_callback,
        )

    connection = sqlite3.connect(adapter.intake_database)
    try:
        state = IntakeGate(connection).snapshot()
    finally:
        connection.close()
    assert state.state == "open"
    assert state.operation_id is None


def test_stop_launch_agent_is_retry_safe_when_service_is_already_absent(
    cutover_fixture,
) -> None:
    runner = LaunchctlRunner(loaded=False)
    adapter = SystemCutoverHostAdapter(
        launchctl=Path(sys.executable),
        command_runner=runner,
    )
    agent = cutover_fixture.manifest.launch_agents[0]

    adapter.stop_launch_agent(agent)

    target = f"gui/{os.getuid()}/{agent.label}"
    assert runner.calls == [("print", target)]


def test_bootstrap_launch_agent_is_retry_safe_when_service_is_already_loaded(
    cutover_fixture,
) -> None:
    runner = LaunchctlRunner(loaded=True)
    adapter = SystemCutoverHostAdapter(
        launchctl=Path(sys.executable),
        command_runner=runner,
    )
    plist = cutover_fixture.home / "Library" / "LaunchAgents" / "com.dan.dand.plist"

    adapter.bootstrap_launch_agent(label="com.dan.dand", plist=plist)

    target = f"gui/{os.getuid()}/com.dan.dand"
    assert runner.calls == [("print", target)]


def test_launch_agent_transitions_use_mac_service_and_domain_targets(
    cutover_fixture,
) -> None:
    runner = LaunchctlRunner(loaded=True)
    adapter = SystemCutoverHostAdapter(
        launchctl=Path(sys.executable),
        command_runner=runner,
    )
    agent = cutover_fixture.manifest.launch_agents[0]
    service_target = f"gui/{os.getuid()}/{agent.label}"

    adapter.stop_launch_agent(agent)
    adapter.bootstrap_launch_agent(label=agent.label, plist=agent.plist)

    assert runner.calls == [
        ("print", service_target),
        ("bootout", service_target),
        ("print", service_target),
        ("bootstrap", f"gui/{os.getuid()}", str(agent.plist)),
    ]
