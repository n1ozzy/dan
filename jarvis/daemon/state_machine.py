"""Frozen daemon state names from TURN_PIPELINE.md."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DaemonState(StrEnum):
    BOOTING = "BOOTING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    TOOLING = "TOOLING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WORKING = "WORKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"
    STOPPING = "STOPPING"


@dataclass
class StateTransition:
    previous: DaemonState
    next: DaemonState
    reason: str | None = None


class StateMachine:
    def __init__(self, initial: DaemonState = DaemonState.BOOTING) -> None:
        self.current = initial

    def transition_to(self, next_state: DaemonState, reason: str | None = None) -> StateTransition:
        raise NotImplementedError("state transitions are not implemented yet")
