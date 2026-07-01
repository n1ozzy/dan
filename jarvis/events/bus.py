"""Simple in-process event fanout."""

from __future__ import annotations

from collections.abc import Callable

from jarvis.logging import get_logger
from jarvis.events.models import Event


EventCallback = Callable[[Event], None]


class EventBusError(Exception):
    """Raised for event bus setup errors."""


class EventBus:
    """Non-persistent, synchronous event bus for in-process subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[EventCallback] = []
        self._last_errors: list[Exception] = []
        self._logger = get_logger(__name__)

    def publish(self, event: Event) -> None:
        """Publish an already-created event without persisting it."""

        errors: list[Exception] = []
        for callback in tuple(self._subscribers):
            try:
                callback(event)
            except Exception as exc:  # pragma: no cover - exact logging path is incidental.
                errors.append(exc)
                self._logger.exception("Event subscriber failed for event %s", event.type)
        self._last_errors = errors

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Subscribe to events and return an unsubscribe callback."""

        if not callable(callback):
            raise EventBusError("Event subscriber callback must be callable.")

        self._subscribers.append(callback)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if not active:
                return
            active = False
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def last_errors(self) -> tuple[Exception, ...]:
        return tuple(self._last_errors)


__all__ = ["EventBus", "EventBusError", "EventCallback"]
