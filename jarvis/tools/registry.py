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
from jarvis.store.event_store import EventStore
from jarvis.tools.permissions import ToolDecision, ToolPermissionPolicy


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
    description = "Approval-required demo tool that only runs after explicit approved execution."
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

    def request_tool(
        self,
        request: ToolRequest,
        *,
        permission_policy: ToolPermissionPolicy,
        approval_gate: ApprovalGate | None = None,
    ) -> ToolResult:
        tool = self.get(request.tool_name)
        permission = permission_policy.decide(
            tool.risk,
            tool_name=tool.name,
            payload=request.arguments,
        )

        if permission.decision == ToolDecision.BLOCKED:
            return ToolResult(
                id=request.id,
                tool_name=tool.name,
                status="blocked",
                error=permission.reason,
            )

        if permission.decision == ToolDecision.APPROVAL_REQUIRED:
            approval_id: str | None = None
            if approval_gate is not None:
                approval = approval_gate.create_approval(
                    risk=permission.risk,
                    requested_by=request.requested_by,
                    action_type=f"tool:{tool.name}",
                    payload={
                        "tool_name": tool.name,
                        "arguments": request.arguments,
                        "requested_by": request.requested_by,
                        "turn_id": request.turn_id,
                    },
                    metadata={
                        "tool_request_id": request.id,
                        **dict(request.metadata),
                    },
                )
                approval_id = str(approval["id"])
            return ToolResult(
                id=request.id,
                tool_name=tool.name,
                status="approval_required",
                error=permission.reason,
                approval_id=approval_id,
            )

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
    ) -> dict[str, Any]:
        approval_id = str(uuid.uuid4())
        created_at = self._now()
        normalized_risk = _required_text(risk, "risk")
        normalized_requested_by = _required_text(requested_by, "requested_by")
        normalized_action_type = _required_text(action_type, "action_type")
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
            self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, decided_at = ?, decision_reason = ?
                WHERE id = ? AND status = 'pending'
                """,
                (normalized_decision, decided_at, reason, approval_id),
            )

        event_type = (
            EventType.APPROVAL_APPROVED
            if normalized_decision == "approved"
            else EventType.APPROVAL_REJECTED
        )
        self._append_event(
            event_type,
            {
                "approval_id": approval_id,
                "risk": approval["risk"],
                "requested_by": approval["requested_by"],
                "action_type": approval["action_type"],
                "decision_reason": reason,
            },
        )
        decided = self.get_approval(approval_id)
        if decided is None:
            raise ToolRegistryError(f"Approval disappeared after decision: {approval_id}")
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

    def _append_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self._event_store is not None:
            self._event_store.append(event_type, "approval_gate", _redact(_json_safe(payload)))


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
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run was not persisted: {run_id}")
        return record

    def record_finished(self, run_id: str, output: Mapping[str, Any]) -> dict[str, Any]:
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
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run disappeared after finish: {run_id}")
        return record

    def record_started(self, run_id: str) -> dict[str, Any]:
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
        )
        record = self.get(run_id)
        if record is None:
            raise ToolRegistryError(f"Tool run disappeared after start: {run_id}")
        return record

    def record_failed(self, run_id: str, error: str) -> dict[str, Any]:
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
    ) -> None:
        if self._event_store is not None:
            self._event_store.append(
                event_type,
                "tool_run_recorder",
                _redact(_json_safe(payload)),
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


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ToolRegistryError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ToolRegistryError(f"{label} must be a non-empty string.")
    return normalized


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


SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in SECRET_KEY_PARTS):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


__all__ = [
    "ApprovalGate",
    "ApprovalProbeTool",
    "EchoTool",
    "Tool",
    "ToolExecutionError",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolRequest",
    "ToolResult",
    "ToolRunRecorder",
    "ToolSpec",
]
