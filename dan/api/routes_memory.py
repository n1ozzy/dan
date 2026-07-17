"""Memory block route payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import Any

from dan.daemon.app import DaemonApp
from dan.memory import (
    MemoryBlock,
    MemoryCandidate,
    MemoryCompilerConfig,
    MemoryCompilerRequest,
    MemoryEvidence,
    MemoryItem,
)
from dan.memory.archive import MemoryRecallValidationError, parse_memory_recall_request

ROUTE_GROUP = "memory"


class MemoryRequestValidationError(ValueError):
    """Raised when a memory route request is malformed."""


def get_memory(
    app: DaemonApp,
    *,
    active_only: bool = False,
    kinds: Iterable[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _validate_limit(limit)
    blocks = app.list_memory(active_only=active_only, kinds=kinds, limit=limit)
    return {
        "memory": [memory_to_dict(block) for block in blocks],
        "active_only": active_only,
        "limit": limit,
    }


def post_memory(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    payload = _create_payload(request_payload)
    block = app.create_memory(**payload)
    return {"memory": memory_to_dict(block)}


def post_memory_recall(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise MemoryRequestValidationError("Request JSON must be an object.")
    try:
        request = parse_memory_recall_request(request_payload)
        return app.recall_memory(request.query, limit=request.limit)
    except MemoryRecallValidationError as exc:
        raise MemoryRequestValidationError(str(exc)) from exc


def post_memory_compile_preview(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    payload = _compile_preview_payload(request_payload)
    context = app.compile_memory_preview(
        MemoryCompilerRequest(
            conversation_id=payload["conversation_id"],
            current_turn_id=payload["current_turn_id"],
            current_user_text=payload["current_user_text"],
            config=MemoryCompilerConfig(
                max_items=payload["max_items"],
                max_chars=payload["max_chars"],
                include_procedural=payload["include_procedural"],
                scope_filter=payload["scope_filter"],
                namespace_filter=payload["namespace_filter"],
            ),
        )
    )
    return asdict(context)


def get_memory_block(app: DaemonApp, memory_id: str) -> dict[str, Any]:
    return {"memory": memory_to_dict(app.get_memory(memory_id))}


def patch_memory(app: DaemonApp, memory_id: str, request_payload: Any) -> dict[str, Any]:
    payload = _update_payload(request_payload)
    block = app.update_memory(memory_id, **payload)
    return {"memory": memory_to_dict(block)}


def delete_memory(app: DaemonApp, memory_id: str) -> dict[str, Any]:
    return {"memory": memory_to_dict(app.disable_memory(memory_id))}


def post_memory_candidate(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise MemoryRequestValidationError("Request JSON must be an object.")
    candidate = app.create_memory_candidate(request_payload)
    return {"ok": True, "candidate": candidate_to_dict(candidate)}


def get_memory_candidates(app: DaemonApp, *, status: str | None = None) -> dict[str, Any]:
    candidates = app.list_memory_candidates(status=status)
    return {
        "ok": True,
        "candidates": [candidate_to_dict(candidate) for candidate in candidates],
    }


def get_memory_candidate(app: DaemonApp, candidate_id: str) -> dict[str, Any]:
    return {"ok": True, "candidate": candidate_to_dict(app.get_memory_candidate(candidate_id))}


def approve_memory_candidate(app: DaemonApp, candidate_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "candidate": candidate_to_dict(app.approve_memory_candidate(candidate_id)),
    }


def reject_memory_candidate(app: DaemonApp, candidate_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "candidate": candidate_to_dict(app.reject_memory_candidate(candidate_id)),
    }


def activate_memory_candidate(app: DaemonApp, candidate_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "item": item_to_dict(app.activate_memory_candidate(candidate_id)),
    }


def post_memory_candidate_evidence(
    app: DaemonApp,
    candidate_id: str,
    request_payload: Any,
) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise MemoryRequestValidationError("Request JSON must be an object.")
    evidence = app.add_memory_candidate_evidence(candidate_id, request_payload)
    return {"ok": True, "evidence": evidence_to_dict(evidence)}


def get_memory_candidate_evidence(
    app: DaemonApp,
    candidate_id: str,
) -> dict[str, Any]:
    evidence = app.list_memory_candidate_evidence(candidate_id)
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "evidence": [evidence_to_dict(item) for item in evidence],
    }


def get_memory_items(app: DaemonApp) -> dict[str, Any]:
    items = app.list_memory_items()
    return {"ok": True, "items": [item_to_dict(item) for item in items]}


def get_memory_item(app: DaemonApp, memory_id: str) -> dict[str, Any]:
    return {"ok": True, "item": item_to_dict(app.get_memory_item(memory_id))}


def memory_to_dict(block: MemoryBlock) -> dict[str, Any]:
    return asdict(block)


def candidate_to_dict(candidate: MemoryCandidate) -> dict[str, Any]:
    return asdict(candidate)


def evidence_to_dict(evidence: MemoryEvidence) -> dict[str, Any]:
    return asdict(evidence)


def item_to_dict(item: MemoryItem) -> dict[str, Any]:
    return asdict(item)


def register_routes(app: object) -> None:
    return None


def _create_payload(request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise MemoryRequestValidationError("Request JSON must be an object.")

    payload: dict[str, Any] = {
        "kind": request_payload.get("kind"),
        "title": request_payload.get("title"),
        "body": request_payload.get("body"),
    }
    if "priority" in request_payload:
        payload["priority"] = _integer(request_payload["priority"], "priority")
    if "active" in request_payload:
        payload["active"] = _boolean(request_payload["active"], "active")
    if "metadata" in request_payload:
        payload["metadata"] = _metadata(request_payload["metadata"])
    return payload


def _update_payload(request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise MemoryRequestValidationError("Request JSON must be an object.")

    payload: dict[str, Any] = {}
    if "title" in request_payload:
        payload["title"] = request_payload["title"]
    if "body" in request_payload:
        payload["body"] = request_payload["body"]
    if "priority" in request_payload:
        payload["priority"] = _integer(request_payload["priority"], "priority")
    if "active" in request_payload:
        payload["active"] = _boolean(request_payload["active"], "active")
    if "metadata" in request_payload:
        payload["metadata"] = _metadata(request_payload["metadata"])
    return payload


def _compile_preview_payload(request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise MemoryRequestValidationError("Request JSON must be an object.")

    return {
        "conversation_id": _optional_string(request_payload, "conversation_id"),
        "current_turn_id": _optional_string(request_payload, "current_turn_id"),
        "current_user_text": _optional_string(request_payload, "current_user_text"),
        "max_items": _optional_positive_integer(
            request_payload,
            "max_items",
            default=MemoryCompilerConfig.max_items,
        ),
        "max_chars": _optional_positive_integer(
            request_payload,
            "max_chars",
            default=MemoryCompilerConfig.max_chars,
        ),
        "include_procedural": _optional_boolean(
            request_payload,
            "include_procedural",
            default=MemoryCompilerConfig.include_procedural,
        ),
        "scope_filter": _optional_string(request_payload, "scope_filter"),
        "namespace_filter": _optional_string(request_payload, "namespace_filter"),
    }


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise MemoryRequestValidationError("metadata must be a JSON object.")
    return dict(value)


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise MemoryRequestValidationError(f"{label} must be an integer.")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise MemoryRequestValidationError(f"{label} must be true or false.")
    return value


def _optional_boolean(payload: Mapping[str, Any], label: str, *, default: bool) -> bool:
    if label not in payload:
        return default
    return _boolean(payload[label], label)


def _optional_positive_integer(
    payload: Mapping[str, Any],
    label: str,
    *,
    default: int,
) -> int:
    if label not in payload:
        return default
    value = _integer(payload[label], label)
    if value <= 0:
        raise MemoryRequestValidationError(f"{label} must be a positive integer.")
    return value


def _optional_string(payload: Mapping[str, Any], label: str) -> str | None:
    if label not in payload:
        return None
    value = payload[label]
    if type(value) is not str:
        raise MemoryRequestValidationError(f"{label} must be a string.")
    return value


def _validate_limit(limit: int) -> None:
    if type(limit) is not int or limit <= 0 or limit > 500:
        raise MemoryRequestValidationError("limit must be an integer between 1 and 500.")


__all__ = [
    "MemoryRequestValidationError",
    "ROUTE_GROUP",
    "activate_memory_candidate",
    "approve_memory_candidate",
    "candidate_to_dict",
    "delete_memory",
    "evidence_to_dict",
    "get_memory",
    "get_memory_block",
    "get_memory_candidate",
    "get_memory_candidate_evidence",
    "get_memory_candidates",
    "get_memory_item",
    "get_memory_items",
    "item_to_dict",
    "memory_to_dict",
    "patch_memory",
    "post_memory_compile_preview",
    "post_memory_candidate_evidence",
    "post_memory_candidate",
    "post_memory",
    "post_memory_recall",
    "reject_memory_candidate",
    "register_routes",
]
