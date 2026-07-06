"""Client-safe event payload shaping shared by REST and WebSocket."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from jarvis.events.models import Event
from jarvis.security.redaction import redact_secrets


_UNSAFE_CLIENT_PAYLOAD_KEYS = {
    "args",
    "arguments",
    "authorization",
    "auth",
    "cookie",
    "cookies",
    "credentials",
    "credential",
    "env",
    "environment",
    "headers",
    "header",
    "input",
    "input_json",
    "output",
    "output_json",
    "raw_args",
    "raw_output",
    "raw_payload",
    "raw_result",
    "request",
    "result",
    "stderr",
    "stdout",
    "token",
    "tokens",
}

_UNSAFE_CLIENT_PAYLOAD_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "auth",
    "cookie",
    "credential",
    "header",
    "password",
    "private_key",
    "secret",
    "token",
)


def safe_event_payload_for_client(event: Event) -> dict[str, Any]:
    """Return an event payload suitable for panel/API clients.

    EventStore keeps the durable payload for audit/debugging; client surfaces
    get a narrower projection so raw tool output, args, tokens, headers,
    cookies, env values, and similar high-risk fields do not cross the UI/API
    boundary.
    """

    payload, omitted = _strip_unsafe_client_fields(event.payload)
    if "output" in omitted:
        payload["output_omitted"] = True
    return redact_secrets(payload)


def _strip_unsafe_client_fields(value: Any) -> tuple[Any, set[str]]:
    if isinstance(value, Mapping):
        stripped: dict[str, Any] = {}
        omitted: set[str] = set()
        for key, item in value.items():
            key_text = str(key)
            normalized_key = _normalize_key(key_text)
            if _unsafe_client_payload_key(normalized_key):
                omitted.add(normalized_key)
                continue
            safe_item, child_omitted = _strip_unsafe_client_fields(item)
            stripped[key_text] = safe_item
            omitted.update(child_omitted)
        return stripped, omitted
    if _sequence(value):
        stripped_items: list[Any] = []
        omitted: set[str] = set()
        for item in value:
            safe_item, child_omitted = _strip_unsafe_client_fields(item)
            stripped_items.append(safe_item)
            omitted.update(child_omitted)
        return stripped_items, omitted
    return value, set()


def _unsafe_client_payload_key(normalized_key: str) -> bool:
    return normalized_key in _UNSAFE_CLIENT_PAYLOAD_KEYS or any(
        fragment in normalized_key for fragment in _UNSAFE_CLIENT_PAYLOAD_KEY_FRAGMENTS
    )


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


__all__ = ["safe_event_payload_for_client"]
