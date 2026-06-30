"""In-process event bus protocol placeholder."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from jarvis.events.models import Event


EventCallback = Callable[[Event], None]


class EventBus(Protocol):
    def publish(self, event: Event) -> None:
        """Publish an already-created event."""

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Subscribe to events and return an unsubscribe callback."""
