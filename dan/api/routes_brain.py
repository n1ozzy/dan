"""Brain switch API payloads (FAZA E1).

`POST /brain/switch` is a daemon-owned settings mutation: it rides the
central transport-token gate for mutating methods (C1, permission model §5)
and is reachable only through the local HTTP API. No tool exposes brain
switching, so a model-originated switch is structurally impossible — the
model never picks its own successor without a human in the loop.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dan.daemon.app import DaemonApp


ROUTE_GROUP = "brain"


class BrainRequestValidationError(ValueError):
    """Raised when a brain API request payload is invalid."""


def get_brain_adapters(app: DaemonApp) -> dict[str, Any]:
    return {
        "adapters": app.list_brain_adapters(),
        "current": app.current_brain_adapter(),
        "default": app.config.brain.default_adapter,
    }


def get_brain_current(app: DaemonApp) -> dict[str, Any]:
    return {
        "adapter": app.current_brain_adapter(),
        "default": app.config.brain.default_adapter,
    }


def post_brain_switch(app: DaemonApp, request_payload: Any) -> dict[str, Any]:
    return app.switch_brain(_adapter_from_request(request_payload))


def _adapter_from_request(request_payload: Any) -> str:
    if not isinstance(request_payload, Mapping):
        raise BrainRequestValidationError("Request JSON must be an object.")
    adapter = request_payload.get("adapter")
    if not isinstance(adapter, str) or not adapter.strip():
        raise BrainRequestValidationError("adapter must be a non-empty string.")
    return adapter.strip()


def register_routes(app: object) -> None:
    return None


__all__ = [
    "ROUTE_GROUP",
    "BrainRequestValidationError",
    "get_brain_adapters",
    "get_brain_current",
    "post_brain_switch",
    "register_routes",
]
