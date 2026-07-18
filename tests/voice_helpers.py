"""Canonical Task 7 voice fixtures shared by focused tests."""

from __future__ import annotations

from dan.voice.models import RenderSnapshot, SpeechIntent, VoiceRequest
from dan.voice.queue import VoiceQueue


def render_snapshot(
    *,
    engine: str = "mock",
    voice_or_style: str = "tests/mock-voice",
) -> RenderSnapshot:
    return RenderSnapshot(
        engine=engine,
        engine_version="test-1",
        voice_or_style=voice_or_style,
        speed=1.0,
        mastering_profile="none",
        dsp="none",
        pronunciations={},
        pronunciations_sha256="1" * 64,
        gain=1.0,
        asset_sha256={"voice": "2" * 64},
        config_revision="test-config-v1",
    )


def speech_intent(
    text: str,
    *,
    session: str = "test-session",
    utterance_index: int = 0,
    lane: str = "normal",
    priority: int = 0,
    interrupt_policy: str = "finish_current",
) -> SpeechIntent:
    return SpeechIntent(
        text=text,
        persona="dan",
        source="pytest",
        session=session,
        participant="dan",
        priority=priority,
        lane=lane,
        interrupt_policy=interrupt_policy,
        utterance_index=utterance_index,
    )


def enqueue_voice(
    queue: VoiceQueue,
    text: str,
    *,
    session: str = "test-session",
    utterance_index: int = 0,
    lane: str = "normal",
    priority: int = 0,
    interrupt_policy: str = "finish_current",
    snapshot: RenderSnapshot | None = None,
) -> VoiceRequest:
    return queue.enqueue(
        speech_intent(
            text,
            session=session,
            utterance_index=utterance_index,
            lane=lane,
            priority=priority,
            interrupt_policy=interrupt_policy,
        ),
        snapshot or render_snapshot(),
    )
