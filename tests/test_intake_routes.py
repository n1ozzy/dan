from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.api.routes_intake import (
    IntakeRequestValidationError,
    get_runtime_intake,
    post_runtime_intake_close,
    post_runtime_intake_open,
)
from dan.daemon.intake import IntakeGateError, IntakeState


class FakeGate:
    def __init__(self) -> None:
        self.state = IntakeState("open", None, None, "daemon", 0)
        self.close_calls: list[dict[str, object]] = []
        self.drain_calls: list[float] = []
        self.open_calls: list[str] = []

    def snapshot(self) -> IntakeState:
        return self.state

    def close(self, **kwargs: object) -> IntakeState:
        self.close_calls.append(kwargs)
        self.state = IntakeState(
            "closed",
            str(kwargs["operation_id"]),
            str(kwargs["reason"]),
            str(kwargs["reopen_policy"]),
            0,
        )
        return self.state

    def wait_for_drain(self, timeout_seconds: float) -> None:
        self.drain_calls.append(timeout_seconds)

    def reopen(self, *, operation_id: str) -> IntakeState:
        self.open_calls.append(operation_id)
        if self.state.operation_id != operation_id:
            raise IntakeGateError("operation mismatch")
        self.state = IntakeState("open", operation_id, self.state.reason, "daemon", 0)
        return self.state


def test_get_runtime_intake_returns_stable_envelope() -> None:
    gate = FakeGate()
    app = SimpleNamespace(intake_gate=gate)

    assert get_runtime_intake(app) == {
        "intake": {
            "state": "open",
            "operation_id": None,
            "reason": None,
            "reopen_policy": "daemon",
            "active_leases": 0,
        }
    }


def test_close_validates_payload_and_waits_for_drain() -> None:
    gate = FakeGate()
    app = SimpleNamespace(intake_gate=gate)

    result = post_runtime_intake_close(
        app,
        {
            "operation_id": "cutover-1",
            "reason": "release cutover",
            "reopen_policy": "external",
            "timeout_seconds": 4.5,
        },
    )

    assert gate.close_calls == [
        {
            "operation_id": "cutover-1",
            "reason": "release cutover",
            "reopen_policy": "external",
        }
    ]
    assert gate.drain_calls == [4.5]
    assert result["intake"]["state"] == "closed"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"operation_id": "", "reason": "cutover"},
        {"operation_id": "op", "reason": ""},
        {"operation_id": "op", "reason": "cutover", "timeout_seconds": True},
        {"operation_id": "op", "reason": "cutover", "timeout_seconds": -1},
    ],
)
def test_close_rejects_invalid_payloads(payload: object) -> None:
    with pytest.raises(IntakeRequestValidationError):
        post_runtime_intake_close(SimpleNamespace(intake_gate=FakeGate()), payload)


def test_open_requires_matching_operation() -> None:
    gate = FakeGate()
    app = SimpleNamespace(intake_gate=gate)
    post_runtime_intake_close(
        app,
        {"operation_id": "cutover-2", "reason": "release cutover"},
    )

    result = post_runtime_intake_open(app, {"operation_id": "cutover-2"})

    assert gate.open_calls == ["cutover-2"]
    assert result["intake"]["state"] == "open"
