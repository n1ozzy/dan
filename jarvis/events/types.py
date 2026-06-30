"""Event name constants from the frozen pipeline."""

from __future__ import annotations

from enum import StrEnum


class FrozenEventType(StrEnum):
    STATE_CHANGED = "state.changed"
    INPUT_TEXT_RECEIVED = "input.text.received"
    INPUT_VOICE_TRANSCRIBED = "input.voice.transcribed"
    TURN_STARTED = "turn.started"
    TURN_FINISHED = "turn.finished"
    BRAIN_REQUESTED = "brain.requested"
    BRAIN_RESPONDED = "brain.responded"
    BRAIN_FAILED = "brain.failed"
    VOICE_SPEAK_CANCELLED = "voice.speak.cancelled"
    MEMORY_UPDATED = "memory.updated"
