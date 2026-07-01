"""Input route payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from jarvis.daemon.app import DaemonApp
from jarvis.turns.models import Turn


ROUTE_GROUP = "input"


class TextInputValidationError(ValueError):
    """Raised when an input route request is malformed."""


def post_text_input(app: DaemonApp, request_payload: Any) -> dict[str, object]:
    payload = _validate_request_payload(request_payload)
    result = app.handle_text_input(
        text=payload["text"],
        conversation_id=payload.get("conversation_id"),
        metadata=payload.get("metadata"),
    )
    return {
        "ok": True,
        "conversation_id": result.conversation_id,
        "turn_id": result.turn_id,
        "input_text": result.input_text,
        "final_text": result.final_text,
        "brain_adapter": result.brain_adapter,
        "brain_model": result.brain_model,
        "state": app.snapshot_state()["state"],
        "event_ids": list(result.event_ids),
        "turn": turn_to_dict(result.turn),
    }


def get_text_input_method_error() -> dict[str, object]:
    return {"error": "GET /input/text is not implemented.", "status": 405}


def text_input_not_implemented() -> dict[str, object]:
    return get_text_input_method_error()


def turn_to_dict(turn: Turn) -> dict[str, object]:
    return asdict(turn)


def _validate_request_payload(request_payload: Any) -> dict[str, Any]:
    if not isinstance(request_payload, Mapping):
        raise TextInputValidationError("Request JSON must be an object.")

    raw_text = request_payload.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise TextInputValidationError("text must be a non-empty string.")

    payload: dict[str, Any] = {"text": raw_text.strip()}

    if "conversation_id" in request_payload and request_payload["conversation_id"] is not None:
        conversation_id = request_payload["conversation_id"]
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise TextInputValidationError("conversation_id must be a non-empty string.")
        payload["conversation_id"] = conversation_id.strip()

    if "metadata" in request_payload and request_payload["metadata"] is not None:
        metadata = request_payload["metadata"]
        if not isinstance(metadata, Mapping):
            raise TextInputValidationError("metadata must be a JSON object.")
        payload["metadata"] = dict(metadata)
    else:
        payload["metadata"] = None

    return payload


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "TextInputValidationError",
    "get_text_input_method_error",
    "post_text_input",
    "register_routes",
    "text_input_not_implemented",
    "turn_to_dict",
]
