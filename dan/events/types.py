"""Canonical DAN event type names."""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    DAEMON_STARTED = "daemon.started"
    DAEMON_STOPPED = "daemon.stopped"
    DAEMON_FAILED = "daemon.failed"
    STATE_CHANGED = "state.changed"

    INPUT_TEXT_RECEIVED = "input.text.received"
    INPUT_VOICE_TRANSCRIBED = "input.voice.transcribed"
    INPUT_REJECTED = "input.rejected"

    TURN_STARTED = "turn.started"
    TURN_CONTEXT_BUILT = "turn.context.built"
    TURN_FINISHED = "turn.finished"
    TURN_FAILED = "turn.failed"
    TURN_CANCELLED = "turn.cancelled"

    BRAIN_REQUESTED = "brain.requested"
    BRAIN_RESPONDED = "brain.responded"
    BRAIN_FAILED = "brain.failed"
    BRAIN_CANCELLED = "brain.cancelled"
    BRAIN_SWITCHED = "brain.switched"

    VOICE_SPEAK_QUEUED = "voice.speak.queued"
    VOICE_SPEAK_STARTED = "voice.speak.started"
    VOICE_SPEAK_FINISHED = "voice.speak.finished"
    VOICE_SPEAK_CANCELLED = "voice.speak.cancelled"
    VOICE_SPEAK_FAILED = "voice.speak.failed"

    AUDIO_DEVICES_SNAPSHOT = "audio.devices.snapshot"
    LISTENING_LEASE_CREATED = "listening.lease.created"
    LISTENING_LEASE_RELEASED = "listening.lease.released"
    LISTENING_LEASE_EXPIRED = "listening.lease.expired"
    LISTENING_LEASE_CANCELLED = "listening.lease.cancelled"

    TOOL_REQUESTED = "tool.requested"
    TOOL_APPROVAL_REQUIRED = "tool.approval.required"
    TOOL_APPROVED = "tool.approved"
    TOOL_REJECTED = "tool.rejected"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    TOOL_FAILED = "tool.failed"

    APPROVAL_CREATED = "approval.created"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXPIRED = "approval.expired"

    MEMORY_UPDATED = "memory.updated"
    MEMORY_CANDIDATE_CREATED = "memory.candidate.created"
    MEMORY_CANDIDATE_PROMOTED = "memory.candidate.promoted"
    MEMORY_DISABLED = "memory.disabled"

    WORKER_JOB_CREATED = "worker.job.created"
    WORKER_JOB_STARTED = "worker.job.started"
    WORKER_JOB_PROGRESS = "worker.job.progress"
    WORKER_JOB_FINISHED = "worker.job.finished"
    WORKER_JOB_FAILED = "worker.job.failed"
    WORKER_JOB_CANCELLED = "worker.job.cancelled"

    RUNTIME_PROCESS_OBSERVED = "runtime.process.observed"
    RUNTIME_LEGACY_CONFLICT_DETECTED = "runtime.legacy.conflict.detected"

    ERROR_RAISED = "error.raised"


FrozenEventType = EventType


__all__ = ["EventType", "FrozenEventType"]
