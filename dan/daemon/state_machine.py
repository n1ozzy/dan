"""Runtime state machine for DAN daemon activity."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from dan.events.bus import EventBus
from dan.events.types import EventType
from dan.logging import redact_secrets
from dan.store.event_store import EventStore, EventStoreError


class StateTransitionError(Exception):
    """Raised when a runtime state transition is invalid or cannot be persisted."""


class RuntimeState(StrEnum):
    BOOTING = "BOOTING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    TOOLING = "TOOLING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"
    STOPPING = "STOPPING"


@dataclass(frozen=True)
class StateTransition:
    old_state: RuntimeState
    new_state: RuntimeState
    event_id: int
    reason: str | None = None
    correlation_id: str | None = None
    turn_id: str | None = None


_NORMAL_TRANSITIONS: dict[RuntimeState, set[RuntimeState]] = {
    RuntimeState.BOOTING: {RuntimeState.IDLE},
    RuntimeState.IDLE: {RuntimeState.LISTENING, RuntimeState.THINKING},
    RuntimeState.LISTENING: {RuntimeState.TRANSCRIBING},
    RuntimeState.TRANSCRIBING: {RuntimeState.THINKING},
    RuntimeState.THINKING: {
        RuntimeState.TOOLING,
        RuntimeState.SPEAKING,
        RuntimeState.IDLE,
    },
    RuntimeState.TOOLING: {RuntimeState.THINKING},
    RuntimeState.SPEAKING: {RuntimeState.IDLE, RuntimeState.INTERRUPTED},
    RuntimeState.INTERRUPTED: {RuntimeState.LISTENING, RuntimeState.THINKING},
    RuntimeState.ERROR: {RuntimeState.IDLE},
    RuntimeState.STOPPING: set(),
}


class RuntimeStateMachine:
    def __init__(
        self,
        event_store: EventStore,
        event_bus: EventBus | None = None,
        initial_state: RuntimeState | str = RuntimeState.BOOTING,
        source: str = "state_machine",
    ) -> None:
        self._event_store = event_store
        self._event_bus = event_bus
        self._state = _coerce_state(initial_state)
        self._source = source
        # Serializes validate+append+assign so concurrent turns/workers cannot
        # both pass the guard from the same old_state and double-append
        # state.changed events (FIX-05 case 5).
        self._lock = threading.Lock()

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def current(self) -> RuntimeState:
        """Compatibility alias for older scaffold code."""

        return self._state

    def can_transition(self, target: RuntimeState | str) -> bool:
        target_state = _coerce_state(target)
        if target_state is self._state:
            return False
        return target_state in self.allowed_targets(self._state)

    def transition(
        self,
        target: RuntimeState | str,
        *,
        reason: str | None = None,
        correlation_id: str | None = None,
        turn_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StateTransition:
        target_state = _coerce_state(target)
        with self._lock:
            old_state = self._state
            if target_state is old_state:
                raise StateTransitionError(
                    f"Cannot transition from {old_state.value} to same state."
                )
            if old_state is RuntimeState.STOPPING:
                raise StateTransitionError(
                    "STOPPING is terminal; no outgoing transitions are allowed."
                )
            if target_state not in self.allowed_targets(old_state):
                raise StateTransitionError(
                    f"Transition from {old_state.value} to {target_state.value} is not allowed."
                )

            event = self._append_state_changed_event(
                old_state,
                target_state,
                reason=reason,
                correlation_id=correlation_id,
                turn_id=turn_id,
                metadata=metadata,
            )

            self._state = target_state
            if self._event_bus is not None:
                try:
                    self._event_bus.publish(event)
                except Exception:
                    pass

            return StateTransition(
                old_state=old_state,
                new_state=target_state,
                event_id=event.id,
                reason=reason,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )

    def force_idle(
        self,
        *,
        reason: str | None = None,
        correlation_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Last-resort in-memory reset to IDLE for failure recovery (FIX-05).

        Used only when a normal transition to IDLE cannot be persisted (e.g. the
        state.changed event append fails). Best-effort: it tries to append the
        event but NEVER raises, and always leaves the runtime in IDLE so it is
        not stranded outside IDLE after a failed turn. STOPPING is terminal
        shutdown and is never resurrected; an already-IDLE runtime is a no-op.
        """

        with self._lock:
            old_state = self._state
            if old_state is RuntimeState.IDLE or old_state is RuntimeState.STOPPING:
                return
            event = None
            try:
                event = self._append_state_changed_event(
                    old_state,
                    RuntimeState.IDLE,
                    reason=reason,
                    correlation_id=correlation_id,
                    turn_id=turn_id,
                    metadata={"forced": True},
                )
            except Exception:
                event = None
            self._state = RuntimeState.IDLE
            if event is not None and self._event_bus is not None:
                try:
                    self._event_bus.publish(event)
                except Exception:
                    pass

    def transition_to(
        self, next_state: RuntimeState | str, reason: str | None = None
    ) -> StateTransition:
        """Compatibility alias for the Prompt 01 scaffold method name."""

        return self.transition(next_state, reason=reason)

    def allowed_targets(self, state: RuntimeState | str | None = None) -> set[RuntimeState]:
        source_state = self._state if state is None else _coerce_state(state)
        if source_state is RuntimeState.STOPPING:
            return set()

        targets = set(_NORMAL_TRANSITIONS[source_state])
        if source_state is not RuntimeState.ERROR:
            targets.add(RuntimeState.ERROR)
        targets.add(RuntimeState.STOPPING)
        targets.discard(source_state)
        return targets

    def _append_state_changed_event(
        self,
        old_state: RuntimeState,
        target_state: RuntimeState,
        *,
        reason: str | None,
        correlation_id: str | None,
        turn_id: str | None,
        metadata: Mapping[str, Any] | None,
    ):
        payload = {
            "old_state": old_state.value,
            "new_state": target_state.value,
            "reason": redact_secrets(reason) if reason is not None else None,
            "metadata": _redact_jsonable(metadata or {}),
        }
        try:
            return self._event_store.append(
                EventType.STATE_CHANGED,
                self._source,
                payload,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )
        except EventStoreError as exc:
            raise StateTransitionError(f"Could not persist state.changed event: {exc}") from exc


def _coerce_state(value: RuntimeState | str) -> RuntimeState:
    if isinstance(value, RuntimeState):
        return value
    if isinstance(value, str):
        try:
            return RuntimeState(value)
        except ValueError as exc:
            raise StateTransitionError(f"Unknown runtime state: {value}") from exc
    raise StateTransitionError(f"Unknown runtime state: {value!r}")


def _redact_jsonable(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, Mapping):
        return {key: _redact_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_jsonable(item) for item in value]
    return value


DaemonState = RuntimeState
StateMachine = RuntimeStateMachine


__all__ = [
    "DaemonState",
    "RuntimeState",
    "RuntimeStateMachine",
    "StateMachine",
    "StateTransition",
    "StateTransitionError",
]
