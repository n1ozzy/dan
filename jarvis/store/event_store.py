"""Append-only event store placeholder."""

from __future__ import annotations

from typing import Any

from jarvis.events.models import Event


class EventStore:
    def append(
        self,
        type: str,
        source: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        turn_id: str | None = None,
    ) -> Event:
        raise NotImplementedError("event persistence is not implemented yet")

    def list_after(self, after_id: int, limit: int = 100) -> list[Event]:
        raise NotImplementedError("event reads are not implemented yet")

    def latest(self, limit: int = 100) -> list[Event]:
        raise NotImplementedError("event reads are not implemented yet")
