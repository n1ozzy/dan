"""Durable intake control routes used by restart and release cutover."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from dan.daemon.app import DaemonApp, DaemonAppNotStartedError
from dan.daemon.intake import IntakeGate, IntakeState


ROUTE_GROUP = "runtime"
DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0


class IntakeRequestValidationError(ValueError):
    """Raised when an intake control request has an invalid payload."""


def get_runtime_intake(app: DaemonApp) -> dict[str, Any]:
    return _payload(_gate(app).snapshot())


def post_runtime_intake_close(app: DaemonApp, payload: Any) -> dict[str, Any]:
    body = _object(payload)
    operation_id = _required_text(body, "operation_id")
    reason = _required_text(body, "reason")
    reopen_policy = _optional_text(body, "reopen_policy", default="daemon")
    timeout = _optional_timeout(body, "timeout_seconds", default=DEFAULT_DRAIN_TIMEOUT_SECONDS)

    gate = _gate(app)
    gate.close(
        operation_id=operation_id,
        reason=reason,
        reopen_policy=reopen_policy,
    )
    gate.wait_for_drain(timeout)
    return _payload(gate.snapshot())


def post_runtime_intake_open(app: DaemonApp, payload: Any) -> dict[str, Any]:
    body = _object(payload)
    operation_id = _required_text(body, "operation_id")
    return _payload(_gate(app).reopen(operation_id=operation_id))


def _gate(app: DaemonApp) -> IntakeGate:
    gate = getattr(app, "intake_gate", None)
    if gate is None:
        raise DaemonAppNotStartedError("Durable intake gate is not initialized.")
    return gate


def _payload(state: IntakeState) -> dict[str, Any]:
    return {"intake": asdict(state)}


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IntakeRequestValidationError("Request JSON must be an object.")
    return payload


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IntakeRequestValidationError(f"{key} must be a non-empty string.")
    return value.strip()


def _optional_text(payload: dict[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise IntakeRequestValidationError(f"{key} must be a non-empty string.")
    return value.strip()


def _optional_timeout(payload: dict[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntakeRequestValidationError(f"{key} must be a non-negative number.")
    timeout = float(value)
    if timeout < 0:
        raise IntakeRequestValidationError(f"{key} must be a non-negative number.")
    return timeout


__all__ = [
    "DEFAULT_DRAIN_TIMEOUT_SECONDS",
    "IntakeRequestValidationError",
    "get_runtime_intake",
    "post_runtime_intake_close",
    "post_runtime_intake_open",
]
