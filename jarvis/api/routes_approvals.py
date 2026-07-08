"""Approval gate route payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.daemon.app import DaemonApp


ROUTE_GROUP = "approvals"


class ApprovalRequestValidationError(ValueError):
    """Raised when an approval route payload is malformed."""


def get_approvals(app: DaemonApp, *, limit: int = 50) -> dict[str, object]:
    # Actionable = pending decisions PLUS approved-but-not-yet-executed. The
    # panel renders straight from this so an approved approval whose execution
    # failed stays visible (server truth) instead of living only in client
    # memory — nothing disappears silently.
    return {"approvals": app.list_actionable_approvals(limit=limit)}


def approve_approval(
    app: DaemonApp,
    approval_id: str,
    request_payload: Any | None = None,
) -> dict[str, object]:
    reason = _optional_reason(request_payload)
    return {"approval": app.approve(approval_id, reason=reason)}


def reject_approval(
    app: DaemonApp,
    approval_id: str,
    request_payload: Any | None = None,
) -> dict[str, object]:
    reason = _optional_reason(request_payload)
    return {"approval": app.reject(approval_id, reason=reason)}


def execute_approval(
    app: DaemonApp,
    approval_id: str,
    request_payload: Any | None = None,
) -> dict[str, object]:
    _reject_unexpected_payload(request_payload)
    return app.execute_approved_tool(approval_id)


def approve_and_execute_approval(
    app: DaemonApp,
    approval_id: str,
    request_payload: Any | None = None,
) -> dict[str, object]:
    reason = _optional_reason(request_payload)
    return app.approve_and_execute_tool(approval_id, reason=reason)


def _optional_reason(request_payload: Any | None) -> str | None:
    if request_payload is None:
        return None
    if not isinstance(request_payload, Mapping):
        raise ApprovalRequestValidationError("Request JSON must be an object.")
    raw_reason = request_payload.get("reason")
    if raw_reason is None:
        return None
    if not isinstance(raw_reason, str):
        raise ApprovalRequestValidationError("reason must be a string.")
    return raw_reason.strip() or None


def _reject_unexpected_payload(request_payload: Any | None) -> None:
    if request_payload is None:
        return
    if not isinstance(request_payload, Mapping):
        raise ApprovalRequestValidationError("Request JSON must be an object.")
    if request_payload:
        raise ApprovalRequestValidationError("execute request body must be empty.")


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ApprovalRequestValidationError",
    "ROUTE_GROUP",
    "approve_and_execute_approval",
    "approve_approval",
    "execute_approval",
    "get_approvals",
    "register_routes",
    "reject_approval",
]
