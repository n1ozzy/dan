"""Safe tool registry, approval gate and tool run recorder."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from jarvis.events.models import utc_now_iso
from jarvis.events.types import EventType
from jarvis.security.redaction import redact_secrets
from jarvis.store.event_store import EventStore
from jarvis.tools.permissions import (
    RequestSource,
    ToolDecision,
    ToolPermissionPolicy,
    ToolPermissionResult,
)


class ToolRegistryError(Exception):
    """Raised when tool registration, lookup or durable records fail."""


class ToolExecutionError(Exception):
    """Raised when a tool handler cannot return a valid result."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: str


@dataclass(frozen=True)
class ToolRequest:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    requested_by: str
    turn_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    id: str
    tool_name: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    approval_id: str | None = None


@dataclass(frozen=True)
class ToolPermissionEvaluation:
    tool_name: str
    risk: str
    decision: str
    reason: str
    approval_required: bool = False
    blocked: bool = False


class Tool:
    name: str
    description: str
    risk: str
    input_schema: dict[str, Any]

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError


class EchoTool(Tool):
    name = "echo"
    description = "Return the provided arguments without side effects."
    risk = "safe_read"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"arguments": _json_safe(dict(arguments))}


class ApprovalProbeTool(Tool):
    name = "approval_probe"
    description = "Demo tool that executes immediately and returns a recorded result."
    risk = "shell_read"
    input_schema = {"type": "object"}

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "ok": True,
            "message": "approval_probe executed safely",
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        spec = _spec_from_tool(tool)
        if spec.name in self._tools:
            raise ToolRegistryError(f"Tool is already registered: {spec.name}")
        self._tools[spec.name] = tool

    def get(self, name: str) -> Tool:
        tool_name = _required_text(name, "tool name")
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ToolRegistryError(f"Unknown tool: {tool_name}") from exc

    def list_specs(self) -> list[ToolSpec]:
        return [_spec_from_tool(self._tools[name]) for name in sorted(self._tools)]

    def evaluate_permission(
        self,
        request: ToolRequest,
        *,
        permission_policy: ToolPermissionPolicy,
        source: RequestSource | str,
    ) -> ToolPermissionEvaluation:
        tool = self.get(request.tool_name)
        permission = permission_policy.decide(
            tool.risk,
            source=source,
            tool_name=tool.name,
            payload=request.arguments,
        )
        return _permission_evaluation(tool.name, permission)

    def request_tool(
        self,
        request: ToolRequest,
        *,
        permission_policy: ToolPermissionPolicy,
        source: RequestSource | str,
        approval_gate: ApprovalGate | None = None,
    ) -> ToolResult:
        # Runtime-lab branch: request means execute. ApprovalGate is ignored so
        # model/voice/panel tool calls are not stranded as pending approvals.
        self.get(request.tool_name)
        return self.execute_tool(request)

    def execute_tool(self, request: ToolRequest, *, approval_id: str | None = None) -> ToolResult:
        tool = self.get(request.tool_name)
        try:
            output = tool.run(dict(request.arguments))
            if not isinstance(output, Mapping):
                raise ToolExecutionError("Tool output must be a mapping.")
            return ToolResult(
                id=request.id,
                tool_name=tool.name,
                status="finished",
                output=dict(output),
                approval_id=approval_id,
            )
        except Exception as exc:
            return ToolResult(
                id=request.id,
                tool_name=tool.name,
                status="failed",
                error=str(exc),
                approval_id=approval_id,
            )


class ApprovalGate:
    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: EventStore | None = None,
        now: Callable[[], str] | None = None,
    ):
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso

    def create_approval(
        self,
        risk: str,
        requested_by: str,
        action_type: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        approval_id = str(uuid.uuid4())
        created_at = self._now()
        normalized_risk = _required_text(risk, "risk")
        normalized_requested_by = _required_text(requested_by, "requested_by")
        normalized_action_type = _required_text(action_type, "action_type")
        event_turn_id = _optional_text(turn_id, "turn_id")
        event_correlation_id = _optional_text(correlation_id, "correlation_id")
        safe_payload = _redact(_json_safe(payload))
        safe_metadata = _redact(_json_safe(metadata or {}))

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO approvals (
                  id, created_at, decided_at, status, risk, requested_by,
                  action_type, payload_json, decision_reason, metadata_json
                )
                VALUES (?, ?, NULL, 'pending', ?, ?, ?, ?, NULL, ?)
                """,
                (
                    approval_id,
                    created_at,
                    normalized_risk,
                    normalized_requested_by,
                    normalized_action_type,
                    _json_dumps(safe_payload),
                    _json_dumps(safe_metadata),
                ),
            )

        self._append_event(
            EventType.APPROVAL_CREATED,
            {
                "approval_id": approval_id,
                "risk": normalized_risk,
                "requested_by": normalized_requested_by,
                "action_type": normalized_action_type,
                "payload": safe_payload,
                "metadata": safe_metadata,
            },
            turn_id=event_turn_id,
            correlation_id=event_correlation_id,
        )
        approval = self.get_approval(approval_id)
        if approval is None:
            raise ToolRegistryError(f"Approval was not persisted: {approval_id}")
        return approval

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT id, created_at, decided_at, status, risk, requested_by,
                   action_type, payload_json, decision_reason, metadata_json
            FROM approvals
            WHERE id = ?
            """,
            (_required_text(approval_id, "approval_id"),),
        ).fetchone()
        if row is None:
            return None
        return _approval_from_row(row)

    def decide(
        self,
        approval_id: str,
        decision: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_decision = _required_text(decision, "decision")
        if normalized_decision not in {"approved", "rejected"}:
            raise ToolRegistryError("Approval decision must be approved or rejected.")

        approval = self.get_approval(approval_id)
        if approval is None:
            raise ToolRegistryError(f"Unknown approval: {approval_id}")
        if approval["status"] != "pending":
            raise ToolRegistryError(f"Approval is not pending: {approval_id}")

        decided_at = self._now()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, decided_at = ?, decision_reason = ?
                WHERE id = ? AND status = 'pending'
                """,
                (normalized_decision, decided_at, reason, approval_id),
            )
            if cursor.rowcount != 1:
                raise ToolRegistryError(f"Approval is not pending: {approval_id}")

        decided = self.get_approval(approval_id)
        if decided is None:
            raise ToolRegistryError(f"Approval disappeared after decision: {approval_id}")
        event_type = (
            EventType.APPROVAL_APPROVED
            if normalized_decision == "approved"
            else EventType.APPROVAL_REJECTED
        )
        event_turn_id, event_correlation_id = _approval_event_context(decided)
        self._append_event(
            event_type,
            _approval_decision_event_payload(
                decided,
                normalized_decision,
                turn_id=event_turn_id,
                correlation_id=event_correlation_id,
            ),
            turn_id=event_turn_id,
            correlation_id=event_correlation_id,
        )
        return decided

    def list_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded_limit = _bounded_limit(limit)
        rows = self._conn.execute(
            """
            SELECT id, created_at, decided_at, status, risk, requested_by,
                   action_type, payload_json, decision_reason, metadata_json
            FROM approvals
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        return [_approval_from_row(row) for row in rows]

    def list_pending_and_approved(self, limit: int = 50) -> list[dict[str, Any]]:
        """Both still-open decisions AND already-approved-but-not-yet-executed
        approvals. The daemon drops the ones that already ran; keeping approved
        rows here is what stops an approved-but-unexecuted approval from
        vanishing (server truth, not client memory)."""

        bounded_limit = _bounded_limit(limit)
        rows = self._conn.execute(
            """
            SELECT id, created_at, decided_at, status, risk, requested_by,
                   action_type, payload_json, decision_reason, metadata_json
            FROM approvals
            WHERE status IN ('pending', 'approved')
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        return [_approval_from_row(row) for row in rows]

    def _append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        if self._event_store is not None:
            self._event_store.append(
                event_type,
                "approval_gate",
                _redact(_json_safe(payload)),
                correlation_id=correlation_id,
                turn_id=turn_id,
            )


class ToolRunRecorder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: EventStore | None = None,
        now: Callable[[], str] | None = None,
    ):
        self._conn = conn
        self._event_store = event_store
        self._now = now or utc_now_iso

    def record_requested(
        self,
        *,
        run_id: str,
        tool_name: str,
        risk: str,
        input: Mapping[str, Any],
        turn_id: str | None = None,
        approval_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        safe_input = _redact(_json_safe(input))
        created_at = self._now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO tool_runs (
                  id, created_at, finished_at, turn_id, tool_name, status, risk,
                  input_json, output_json, error, approval_id
                )
                VALUES (?, ?, NULL, ?, ?, 'requested', ?, ?, NULL, NULL, ?)
                """,
                (
                    _required_text(run_id, "run_id"),
                    created_at,
                    turn_id,
                    _required_text(tool_name, "tool_name"),
                    _required_text(risk, "risk"),
                    _json_dumps(safe_input),
                    approval_id,
                ),
            )

        self._append_event(
            EventType.TOOL_REQUESTED,
            {
                "run_id": run_id,
                "tool_name": tool_name,
                "risk": risk,
                "turn_id": turn_id,
                "approval_id": approval_id,
                "input": safe_input,
            },
            turn_id=turn_id,
            correlation_id=correlation_id,
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run was not persisted: {run_id}")
        return record

    def record_finished(
        self,
        run_id: str,
        output: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.get(run_id)
        if run is None:
            raise ToolRegistryError(f"Unknown tool run: {run_id}")
        safe_output = _redact(_json_safe(output))
        finished_at = self._now()
        with self._conn:
            self._conn.execute(
                """
                UPDATE tool_runs
                SET status = 'finished', finished_at = ?, output_json = ?, error = NULL
                WHERE id = ?
                """,
                (finished_at, _json_dumps(safe_output), run_id),
            )

        self._append_event(
            EventType.TOOL_FINISHED,
            {
                "run_id": run_id,
                "tool_run_id": run_id,
                "tool_name": run["tool_name"],
                "risk": run["risk"],
                "approval_id": run["approval_id"],
                "status": "finished",
                "output": safe_output,
            },
            turn_id=run["turn_id"],
            correlation_id=correlation_id,
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run disappeared after finish: {run_id}")
        return record

    def record_started(
        self,
        run_id: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.get(run_id)
        if run is None:
            raise ToolRegistryError(f"Unknown tool run: {run_id}")
        with self._conn:
            self._conn.execute(
                """
                UPDATE tool_runs
                SET status = 'started'
                WHERE id = ?
                """,
                (run_id,),
            )

        self._append_event(
            EventType.TOOL_STARTED,
            {
                "run_id": run_id,
                "tool_run_id": run_id,
                "tool_name": run["tool_name"],
                "risk": run["risk"],
                "turn_id": run["turn_id"],
                "approval_id": run["approval_id"],
                "status": "started",
            },
            turn_id=run["turn_id"],
            correlation_id=correlation_id,
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run disappeared after start: {run_id}")
        return record

    def record_failed(
        self,
        run_id: str,
        error: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.get(run_id)
        if run is None:
            raise ToolRegistryError(f"Unknown tool run: {run_id}")
        finished_at = self._now()
        normalized_error = _required_text(error, "error")
        with self._conn:
            self._conn.execute(
                """
                UPDATE tool_runs
                SET status = 'failed', finished_at = ?, error = ?
                WHERE id = ?
                """,
                (finished_at, normalized_error, run_id),
            )

        self._append_event(
            EventType.TOOL_FAILED,
            {
                "run_id": run_id,
                "tool_run_id": run_id,
                "tool_name": run["tool_name"],
                "risk": run["risk"],
                "approval_id": run["approval_id"],
                "status": "failed",
                "error": normalized_error,
            },
            turn_id=run["turn_id"],
            correlation_id=correlation_id,
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run disappeared after failure: {run_id}")
        return record

    def get_by_approval_id(self, approval_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT id, created_at, finished_at, turn_id, tool_name, status, risk,
                   input_json, output_json, error, approval_id
            FROM tool_runs
            WHERE approval_id = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT 1
            """,
            (_required_text(approval_id, "approval_id"),),
        ).fetchone()
        if row is None:
            return None
        return _tool_run_from_row(row)

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT id, created_at, finished_at, turn_id, tool_name, status, risk,
                   input_json, output_json, error, approval_id
            FROM tool_runs
            WHERE id = ?
            """,
            (_required_text(run_id, "run_id"),),
        ).fetchone()
        if row is None:
            return None
        return _tool_run_from_row(row)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded_limit = _bounded_limit(limit)
        rows = self._conn.execute(
            """
            SELECT id, created_at, finished_at, turn_id, tool_name, status, risk,
                   input_json, output_json, error, approval_id
            FROM tool_runs
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        return [_tool_run_from_row(row) for row in rows]

    def _append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        if self._event_store is not None:
            self._event_store.append(
                event_type,
                "tool_run_recorder",
                _redact(_json_safe(payload)),
                correlation_id=correlation_id,
                turn_id=turn_id,
            )


def _spec_from_tool(tool: Tool) -> ToolSpec:
    name = _required_text(getattr(tool, "name", None), "tool name")
    description = _required_text(getattr(tool, "description", None), f"{name} description")
    risk = _required_text(getattr(tool, "risk", None), f"{name} risk")
    input_schema = getattr(tool, "input_schema", None)
    if not isinstance(input_schema, dict):
        raise ToolRegistryError(f"{name} input_schema must be a dict.")
    return ToolSpec(name=name, description=description, input_schema=dict(input_schema), risk=risk)


def _permission_evaluation(
    tool_name: str,
    permission: ToolPermissionResult,
) -> ToolPermissionEvaluation:
    return ToolPermissionEvaluation(
        tool_name=tool_name,
        risk=permission.risk,
        decision=permission.decision,
        reason=permission.reason,
        approval_required=permission.approval_required,
        blocked=permission.blocked,
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ToolRegistryError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ToolRegistryError(f"{label} must be a non-empty string.")
    return normalized


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _bounded_limit(limit: int) -> int:
    if type(limit) is not int or limit <= 0:
        raise ToolRegistryError("limit must be a positive integer.")
    if limit > 500:
        raise ToolRegistryError("limit must be at most 500.")
    return limit


def _approval_from_row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    data = _row_dict(
        row,
        [
            "id",
            "created_at",
            "decided_at",
            "status",
            "risk",
            "requested_by",
            "action_type",
            "payload_json",
            "decision_reason",
            "metadata_json",
        ],
    )
    return {
        "id": data["id"],
        "created_at": data["created_at"],
        "decided_at": data["decided_at"],
        "status": data["status"],
        "risk": data["risk"],
        "requested_by": data["requested_by"],
        "action_type": data["action_type"],
        "payload": _json_loads_object(data["payload_json"], "approval payload_json"),
        "decision_reason": data["decision_reason"],
        "metadata": _json_loads_object(data["metadata_json"], "approval metadata_json"),
    }


def _tool_run_from_row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    data = _row_dict(
        row,
        [
            "id",
            "created_at",
            "finished_at",
            "turn_id",
            "tool_name",
            "status",
            "risk",
            "input_json",
            "output_json",
            "error",
            "approval_id",
        ],
    )
    output_json = data["output_json"]
    return {
        "id": data["id"],
        "created_at": data["created_at"],
        "finished_at": data["finished_at"],
        "turn_id": data["turn_id"],
        "tool_name": data["tool_name"],
        "status": data["status"],
        "risk": data["risk"],
        "input": _json_loads_object(data["input_json"], "tool run input_json"),
        "output": None
        if output_json is None
        else _json_loads_object(output_json, "tool run output_json"),
        "error": data["error"],
        "approval_id": data["approval_id"],
    }


def _approval_decision_event_payload(
    approval: Mapping[str, Any],
    decision: str,
    *,
    turn_id: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "approval_id": approval["id"],
        "tool_name": _approval_tool_name(approval),
        "requested_risk": approval["risk"],
        "status": approval["status"],
        "decision": decision,
    }
    decided_at = approval.get("decided_at")
    if decided_at is not None:
        payload["decided_at"] = decided_at
    if decision == "rejected" and approval.get("decision_reason") is not None:
        payload["reason"] = approval["decision_reason"]
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    return payload


def _approval_tool_name(approval: Mapping[str, Any]) -> str:
    approval_payload = approval.get("payload")
    if isinstance(approval_payload, Mapping):
        raw_tool_name = approval_payload.get("tool_name")
        if isinstance(raw_tool_name, str) and raw_tool_name.strip():
            return raw_tool_name.strip()

    action_type = str(approval.get("action_type", "")).strip()
    if action_type.startswith("tool:") and action_type.removeprefix("tool:").strip():
        return action_type.removeprefix("tool:").strip()
    return action_type


def _approval_event_context(approval: Mapping[str, Any]) -> tuple[str | None, str | None]:
    approval_payload = approval.get("payload")
    payload = approval_payload if isinstance(approval_payload, Mapping) else {}
    approval_metadata = approval.get("metadata")
    metadata = approval_metadata if isinstance(approval_metadata, Mapping) else {}

    turn_id = _first_optional_text(payload.get("turn_id"), metadata.get("turn_id"))
    correlation_id = _first_optional_text(
        payload.get("correlation_id"),
        metadata.get("correlation_id"),
        turn_id,
    )
    return turn_id, correlation_id


def _first_optional_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _row_dict(row: sqlite3.Row | tuple[Any, ...], columns: list[str]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {column: row[column] for column in columns}
    return dict(zip(columns, row, strict=True))


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ToolRegistryError(f"value must be JSON serializable: {exc}") from exc


def _json_loads_object(value: str | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ToolRegistryError(f"{label} must contain valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ToolRegistryError(f"{label} must decode to a JSON object.")
    return decoded


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# The durable store keeps a bounded preview of each string, not whole payloads:
# a 256 KB file_read body must not persist in full in tool_runs/events. The
# brain continuation reads the transient ToolResult.output through the shared
# redact_secrets (no cap), so the model still gets the full redacted content.
PERSIST_MAX_STRING_CHARS = 4096


def _redact(value: Any) -> Any:
    """Redact secrets in persisted tool payloads, then size-cap long strings.

    Key masking and secret-value redaction are delegated to the central
    ``security.redaction`` module (single source of truth: it normalizes key
    separators, so ``api-key``/``API.KEY`` mask just like ``api_key`` — the old
    local substring rule missed those). On top of that, the DURABLE store caps
    long strings (``PERSIST_MAX_STRING_CHARS``) so a large tool payload never
    lands whole in tool_runs/events even if a novel secret shape slips redaction.
    """

    return _cap_persisted_strings(redact_secrets(value))


def _cap_persisted_strings(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _cap_persisted_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cap_persisted_strings(item) for item in value]
    if isinstance(value, str) and len(value) > PERSIST_MAX_STRING_CHARS:
        dropped = len(value) - PERSIST_MAX_STRING_CHARS
        return f"{value[:PERSIST_MAX_STRING_CHARS]}…[+{dropped} chars TRUNCATED]"
    return value


__all__ = [
    "ApprovalGate",
    "ApprovalProbeTool",
    "EchoTool",
    "Tool",
    "ToolExecutionError",
    "ToolPermissionEvaluation",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolRequest",
    "ToolResult",
    "ToolRunRecorder",
    "ToolSpec",
]
