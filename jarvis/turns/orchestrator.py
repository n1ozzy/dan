"""Single input pipeline orchestrator."""

from __future__ import annotations

import sqlite3
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jarvis.brain.base import BrainGenerationCancelled, BrainResponse
from jarvis.brain.context_builder import ContextBuilder
from jarvis.brain.manager import BrainManager
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.security.redaction import redact_secrets
from jarvis.store.event_store import EventStore
from jarvis.store.repositories import RepositoryError, ensure_mapping, ensure_non_empty_text
from jarvis.tools.permissions import RequestSource, ToolDecision, ToolPermissionPolicy
from jarvis.tools.registry import (
    ApprovalGate,
    ToolRegistry,
    ToolRegistryError,
    ToolRequest,
    ToolResult,
)
from jarvis.turns.models import Turn, TurnSource, TurnStatus
from jarvis.turns.repository import ConversationRepository, TurnRepository
from jarvis.voice.speech_form_stream import SpeechFormStreamRouter


class TurnOrchestratorError(Exception):
    """Raised when a text turn cannot be completed."""


class TurnOrchestratorBusyError(TurnOrchestratorError):
    """Raised when a text turn cannot start because the runtime is not IDLE.

    Subclasses ``TurnOrchestratorError`` so existing callers keep working while
    the HTTP layer can map this precondition to 409 instead of 500.
    """


class TurnCancelledError(TurnOrchestratorError):
    """Raised when a turn's generation was cancelled by barge-in (FIX-09).

    Subclasses ``TurnOrchestratorError`` so existing callers keep working, but
    the voice gateway can tell a deliberate cancellation (info log, no retry,
    turn already CANCELLED and runtime back to IDLE) apart from a real failure.
    """


@dataclass(frozen=True)
class TextTurnResult:
    conversation_id: str
    turn_id: str
    input_text: str
    final_text: str
    brain_adapter: str
    brain_model: str
    event_ids: list[int]
    turn: Turn
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ToolResultContinuationResult:
    applied: bool
    status: str
    turn_id: str
    final_text: str | None
    error: str | None
    event_ids: list[int]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "applied": self.applied,
            "status": self.status,
            "turn_id": self.turn_id,
            "event_ids": list(self.event_ids),
            "metadata": dict(self.metadata),
        }
        if self.final_text is not None:
            payload["final_text"] = self.final_text
        if self.error is not None:
            payload["error"] = self.error
        return payload


# A turn in one of these statuses has reached a terminal outcome; a later
# post-completion error must never reclassify it (FIX-05).
_TERMINAL_TURN_STATUSES = frozenset(
    {TurnStatus.FINISHED.value, TurnStatus.FAILED.value, TurnStatus.CANCELLED.value}
)


class TurnOrchestrator:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        event_store: EventStore,
        event_bus: EventBus | None,
        state_machine: RuntimeStateMachine,
        brain_manager: BrainManager,
        context_builder: ContextBuilder,
        tool_registry: ToolRegistry | None = None,
        approval_gate: ApprovalGate | None = None,
        tool_permission_policy: ToolPermissionPolicy | None = None,
        conversation_repository: ConversationRepository | None = None,
        turn_repository: TurnRepository | None = None,
        speech_pipeline: Any | None = None,
        source: str = "turn_orchestrator",
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._event_bus = event_bus
        self._state_machine = state_machine
        self._brain_manager = brain_manager
        self._context_builder = context_builder
        self._tool_registry = tool_registry
        self._approval_gate = approval_gate
        self._tool_permission_policy = tool_permission_policy
        self._conversations = conversation_repository or ConversationRepository(conn)
        self._turns = turn_repository or TurnRepository(conn)
        self._speech = speech_pipeline
        self._source = _required_text(source, "source")

    def _speak(self, turn_id: str, text: str) -> None:
        """Queue the spoken form of a finished answer (G0/G3, best effort)."""

        if self._speech is None:
            return
        try:
            self._speech.speak_text(turn_id=turn_id, text=text)
        except Exception:  # speech must never fail a finished turn
            pass

    def _arm_filler(self, turn_id: str) -> Any:
        if self._speech is None:
            return None
        try:
            return self._speech.arm_filler(turn_id=turn_id)
        except Exception:
            return None

    def _start_speech_stream(self, turn_id: str, filler_timer: Any) -> Any:
        """Open the live delta consumer (G4d); None when speech is off."""

        if self._speech is None or not hasattr(self._speech, "start_stream"):
            return None
        try:
            return self._speech.start_stream(turn_id=turn_id, filler_timer=filler_timer)
        except Exception:  # speech must never fail generation
            return None

    def _speech_on_delta(self, speech_session: Any) -> Any:
        """This turn's delta consumer for live speech, or None when speech is off.

        When the context builder asked the model for the [[GŁOS]] voice form,
        the raw stream goes through a per-turn SpeechFormStreamRouter so only
        the block's inner text reaches TTS — the markers and the rich chat
        text must never be spoken. Without the instruction the deltas ARE the
        spoken text, so they feed the session directly (routing them would
        silence live speech until finalize)."""

        if speech_session is None:
            return None
        try:
            speech_form = bool(self._context_builder.speech_form_enabled())
        except Exception:  # speech must never fail generation
            speech_form = False
        if speech_form:
            return SpeechFormStreamRouter(speech_session.feed).feed
        return speech_session.feed

    def _finish_speech(self, session: Any, turn_id: str, display_text: str, speech_text: str | None = None) -> None:
        """Close the stream against the speech text (best effort).

        With a session, sentences already queued from deltas are NOT
        re-enqueued — finalize only flushes the tail (or chunks the whole
        speech text when no delta ever arrived)."""

        if session is None:
            self._speak(turn_id, speech_text or display_text)
            return
        try:
            session.finalize(speech_text or display_text)
        except Exception:  # speech must never fail a finished turn
            pass

    def _cancel_turn_speech(self, turn_id: str) -> None:
        """Turn failure is a §7 cancellation trigger: sentences already
        queued from deltas of a failed generation must not be spoken."""

        if self._speech is None:
            return
        try:
            from jarvis.voice.queue import VoiceQueue

            VoiceQueue(self._conn, event_store=self._event_store).cancel_turn(turn_id)
        except Exception:  # cancellation is best effort on the failure path
            pass

    def run_text_turn(self, text: str) -> Turn:
        """Compatibility wrapper for the Prompt 01 placeholder API."""

        return self.handle_text(text=text).turn

    def handle_text(
        self,
        *,
        text: str,
        conversation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source: str = "api",
    ) -> TextTurnResult:
        normalized_text = _required_text(text, "text")
        normalized_metadata = _metadata(metadata)
        normalized_source = _turn_source(source)
        is_live_voice = normalized_source == TurnSource.VOICE

        if self._state_machine.state is not RuntimeState.IDLE:
            raise TurnOrchestratorBusyError(
                f"Cannot start text turn while runtime state is {self._state_machine.state.value}."
            )

        event_ids: list[int] = []
        conversation = self._conversations.get_or_create(conversation_id)
        turn = self._turns.create(
            conversation.id,
            source=normalized_source,
            input_text=normalized_text,
            status=TurnStatus.RECEIVED.value,
            metadata=normalized_metadata,
        )
        correlation_id = turn.id

        try:
            self._append_event(
                EventType.INPUT_TEXT_RECEIVED,
                {
                    "text_length": len(normalized_text),
                    "conversation_id": conversation.id,
                    "turn_id": turn.id,
                    "source": normalized_source,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )
            self._append_event(
                EventType.TURN_STARTED,
                {
                    "conversation_id": conversation.id,
                    "turn_id": turn.id,
                    "source": normalized_source,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )
            event_ids.append(
                self._state_machine.transition(
                    RuntimeState.THINKING,
                    reason="text turn started",
                    correlation_id=correlation_id,
                    turn_id=turn.id,
                ).event_id
            )
            turn = self._turns.update_status(turn.id, TurnStatus.STARTED.value)

            try:
                context_result = self._context_builder.build_request(
                    turn_id=turn.id,
                    conversation_id=conversation.id,
                    input_text=normalized_text,
                    runtime_state=self._state_machine.state.value,
                )
            except Exception as exc:
                self._record_context_failure(
                    turn=turn,
                    conversation_id=conversation.id,
                    error=exc,
                    event_ids=event_ids,
                    correlation_id=correlation_id,
                )
                raise TurnOrchestratorError(f"context build failed: {exc}") from exc

            turn = self._turns.attach_context_snapshot(turn.id, context_result.context_snapshot)
            self._append_event(
                EventType.TURN_CONTEXT_BUILT,
                {
                    "turn_id": turn.id,
                    "conversation_id": conversation.id,
                    "context_snapshot": dict(context_result.context_snapshot),
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )

            adapter = self._brain_manager.get_adapter()
            adapter_name = str(adapter.name)
            request_model = _model_from_adapter(adapter, context_result.request)
            self._append_event(
                EventType.BRAIN_REQUESTED,
                {
                    "turn_id": turn.id,
                    "conversation_id": conversation.id,
                    "adapter": adapter_name,
                    "model": request_model,
                    "input_length": len(normalized_text),
                    "context_message_count": len(context_result.request.context_messages),
                    "memory_block_count": len(context_result.request.memory_blocks),
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )
            turn = self._turns.attach_brain_request(
                turn.id,
                adapter=adapter_name,
                model=request_model,
            )

            streaming_enabled = (
                self._speech is not None
                and self._brain_manager.supports_streaming()
            )
            # Voice turns get fillers + streaming; text/API turns get streaming
            # only (no filler — text turns must never enqueue spoken filler).
            filler_timer = (
                self._speech.arm_filler(turn_id=turn.id)
                if streaming_enabled and is_live_voice
                else None
            )
            speech_session = (
                self._start_speech_stream(turn.id, filler_timer)
                if streaming_enabled
                else None
            )
            try:
                response = self._brain_manager.generate(
                    context_result.request,
                    on_delta=self._speech_on_delta(speech_session),
                )
            except BrainGenerationCancelled as exc:
                # Barge-in killed the generation (FIX-09): this is a CANCELLED
                # turn, not a FAILED one. Same cleanup as a failure (disarm
                # filler, cancel queued speech) but the turn ends CANCELLED and
                # the runtime settles back to IDLE — never through ERROR.
                if filler_timer is not None:
                    filler_timer.disarm()
                self._cancel_turn_speech(turn.id)
                self._record_brain_cancellation(
                    turn=turn,
                    conversation_id=conversation.id,
                    adapter=adapter_name,
                    model=request_model,
                    reason=exc,
                    event_ids=event_ids,
                    correlation_id=correlation_id,
                )
                raise TurnCancelledError(f"brain generation cancelled: {exc}") from exc
            except Exception as exc:
                if filler_timer is not None:
                    filler_timer.disarm()
                # §7: a failed turn cancels its own queued speech — deltas
                # already sentence-cut into the queue were never truth.
                self._cancel_turn_speech(turn.id)
                self._record_brain_failure(
                    turn=turn,
                    conversation_id=conversation.id,
                    adapter=adapter_name,
                    model=request_model,
                    error=exc,
                    event_ids=event_ids,
                    correlation_id=correlation_id,
                )
                raise TurnOrchestratorError(f"brain generation failed: {exc}") from exc
            if filler_timer is not None:
                filler_timer.disarm()

            response_model = _response_model(response, request_model)
            self._append_event(
                EventType.BRAIN_RESPONDED,
                {
                    "turn_id": turn.id,
                    "conversation_id": conversation.id,
                    "adapter": adapter_name,
                    "model": response_model,
                    "text_length": len(response.text),
                    "tool_call_count": len(response.tool_calls),
                    "usage": _usage_payload(response),
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )
            capture = self._capture_model_tool_calls(
                response=response,
                turn_id=turn.id,
                conversation_id=conversation.id,
                event_ids=event_ids,
                correlation_id=correlation_id,
            )
            final_text = _final_text_with_tool_summary(response.text, capture.tool_calls)
            pending_approval_count = len(capture.approvals)
            finish_metadata = (
                {
                    "tool_call_capture": {
                        "origin": "model",
                        "total": len(capture.tool_calls),
                        "approval_count": pending_approval_count,
                        "error_count": len(
                            [
                                tool_call
                                for tool_call in capture.tool_calls
                                if tool_call["status"] != "approval_required"
                            ]
                        ),
                        "tool_calls": capture.tool_calls,
                        "approvals": capture.approvals,
                    }
                }
                if capture.tool_calls
                else None
            )
            if pending_approval_count:
                turn = self._turns.await_approval(
                    turn.id,
                    final_text=final_text,
                    brain_adapter=adapter_name,
                    brain_model=response_model,
                    metadata=finish_metadata,
                )
            else:
                turn = self._turns.finish(
                    turn.id,
                    final_text=final_text,
                    brain_adapter=adapter_name,
                    brain_model=response_model,
                    metadata=finish_metadata,
                )
            # The turn has reached its terminal/awaiting outcome. Everything
            # below is post-completion finalization and must NEVER reclassify
            # the turn as FAILED nor strand the runtime — even if the daemon is
            # shutting down (STOPPING) or an event append fails (FIX-05 1-2).
            result = TextTurnResult(
                conversation_id=conversation.id,
                turn_id=turn.id,
                input_text=normalized_text,
                final_text=final_text,
                brain_adapter=adapter_name,
                brain_model=response_model,
                event_ids=event_ids,
                turn=turn,
                tool_calls=capture.tool_calls,
                approvals=capture.approvals,
            )
            try:
                self._append_event(
                    EventType.TURN_FINISHED,
                    {
                        "turn_id": turn.id,
                        "conversation_id": conversation.id,
                        "final_text_length": len(final_text),
                        "brain_adapter": adapter_name,
                        "brain_model": response_model,
                        "turn_status": turn.status,
                        "pending_approval_count": pending_approval_count,
                    },
                    event_ids,
                    correlation_id=correlation_id,
                    turn_id=turn.id,
                )
            except Exception:
                pass  # audit event is best effort; the turn already finished
            # Speak the raw model text: the chunker strips tool-call blocks,
            # and an awaiting_approval turn speaks only its safe prefix (G0 §4).
            # A streaming session already queued its sentences live; this
            # only flushes the tail (or chunks everything when no delta came).
            speech_text = getattr(response, "speech_text", None) or response.text
            self._finish_speech(speech_session, turn.id, response.text, speech_text)
            self._settle_runtime_idle_after_completion(
                pending_approval=bool(pending_approval_count),
                correlation_id=correlation_id,
                turn_id=turn.id,
                event_ids=event_ids,
            )
            return result
        except TurnOrchestratorError:
            raise
        except Exception as exc:
            self._record_generic_failure(
                turn=turn,
                conversation_id=conversation.id,
                error=exc,
                event_ids=event_ids,
                correlation_id=correlation_id,
            )
            raise TurnOrchestratorError(f"text turn failed: {exc}") from exc

    def _settle_runtime_idle_after_completion(
        self,
        *,
        pending_approval: bool,
        correlation_id: str,
        turn_id: str,
        event_ids: list[int],
        reason: str | None = None,
    ) -> None:
        """Return the runtime to IDLE after a completed (or cancelled) turn.

        Post-completion and best effort: a shutdown race (STOPPING) or a persist
        failure must never fail the already-finished turn (FIX-05 cases 1-2).
        STOPPING is left terminal (the daemon is shutting down); any other
        non-IDLE state is reset in-memory so the runtime is not stranded.
        """

        if self._state_machine.state is RuntimeState.IDLE:
            return
        if reason is None:
            reason = "text turn awaiting approval" if pending_approval else "text turn finished"
        try:
            event_ids.append(
                self._state_machine.transition(
                    RuntimeState.IDLE,
                    reason=reason,
                    correlation_id=correlation_id,
                    turn_id=turn_id,
                ).event_id
            )
        except Exception:
            if self._state_machine.state is RuntimeState.STOPPING:
                return
            self._state_machine.force_idle(
                reason=reason,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )

    def continue_after_tool_result(
        self,
        *,
        approval_id: str,
        tool_request: ToolRequest,
        tool_result: ToolResult,
        tool_run: Mapping[str, Any],
    ) -> ToolResultContinuationResult | None:
        """Continue an awaiting turn after an explicitly executed one-shot tool."""

        if tool_request.turn_id is None:
            return None

        turn = self._turns.get(tool_request.turn_id)
        if turn is None or turn.status != TurnStatus.AWAITING_APPROVAL.value:
            return None

        eligibility = _continuation_eligibility(tool_result)
        if not eligibility["continuation_eligible"]:
            return None

        event_ids: list[int] = []
        correlation_id = turn.id
        base_metadata = _continuation_metadata(
            approval_id=approval_id,
            tool_request=tool_request,
            tool_result=tool_result,
            tool_run=tool_run,
            turn=turn,
            eligibility=eligibility,
        )
        continuation_input = _continuation_input_text(
            original_user_input=turn.input_text or "",
            tool_name=tool_request.tool_name,
            tool_arguments=base_metadata["tool_arguments"],
            tool_output=base_metadata["tool_result"],
        )

        adapter = self._brain_manager.get_adapter()
        adapter_name = str(adapter.name)
        request_model = _model_from_adapter(adapter, None)

        try:
            context_result = self._context_builder.build_request(
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                input_text=continuation_input,
                runtime_state=self._state_machine.state.value,
            )
            request = context_result.request
            request_model = _model_from_adapter(adapter, request)
            request.metadata = {
                **dict(request.metadata),
                "tool_result_continuation": dict(base_metadata),
            }
            self._append_event(
                EventType.BRAIN_REQUESTED,
                {
                    "turn_id": turn.id,
                    "conversation_id": turn.conversation_id,
                    "adapter": adapter_name,
                    "model": request_model,
                    "input_length": len(continuation_input),
                    "context_message_count": len(request.context_messages),
                    "memory_block_count": len(request.memory_blocks),
                    "continuation": base_metadata,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )
            streaming_enabled = (
                self._speech is not None
                and self._brain_manager.supports_streaming()
            )
            # Continuations of voice turns also stream (no filler needed).
            speech_session = (
                self._start_speech_stream(turn.id, None)
                if streaming_enabled and turn.source == TurnSource.VOICE.value
                else None
            )
            response = self._brain_manager.generate(
                request,
                on_delta=self._speech_on_delta(speech_session),
            )
            continuation_text = _continuation_answer_text(response)
        except BrainGenerationCancelled as exc:
            # Barge-in killed the continuation generation (FIX-09): CANCELLED,
            # not FAILED — same distinction as the main handle_text path.
            self._cancel_turn_speech(turn.id)
            return self._record_continuation_cancellation(
                turn=turn,
                adapter_name=adapter_name,
                request_model=request_model,
                reason=exc,
                event_ids=event_ids,
                correlation_id=correlation_id,
                metadata=base_metadata,
            )
        except Exception as exc:
            self._cancel_turn_speech(turn.id)
            return self._record_continuation_failure(
                turn=turn,
                adapter_name=adapter_name,
                request_model=request_model,
                error=exc,
                event_ids=event_ids,
                correlation_id=correlation_id,
                metadata=base_metadata,
            )

        response_model = _response_model(response, request_model)
        success_metadata = {
            **base_metadata,
            "status": "finished",
            "brain_adapter": adapter_name,
            "brain_model": response_model,
            "ignored_tool_call_count": len(response.tool_calls),
        }
        self._append_event(
            EventType.BRAIN_RESPONDED,
            {
                "turn_id": turn.id,
                "conversation_id": turn.conversation_id,
                "adapter": adapter_name,
                "model": response_model,
                "text_length": len(continuation_text),
                "tool_call_count": len(response.tool_calls),
                "tool_calls_ignored": True,
                "usage": _usage_payload(response),
                "continuation": success_metadata,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        finished = self._turns.finish(
            turn.id,
            final_text=continuation_text,
            brain_adapter=adapter_name,
            brain_model=response_model,
            metadata={"tool_result_continuation": success_metadata},
        )
        speech_text = getattr(response, "speech_text", None) or continuation_text
        self._finish_speech(speech_session, turn.id, continuation_text, speech_text)
        self._append_event(
            EventType.TURN_FINISHED,
            {
                "turn_id": finished.id,
                "conversation_id": finished.conversation_id,
                "final_text_length": len(continuation_text),
                "brain_adapter": adapter_name,
                "brain_model": response_model,
                "turn_status": finished.status,
                "previous_status": turn.status,
                "pending_approval_count": 0,
                "continuation": success_metadata,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        return ToolResultContinuationResult(
            applied=True,
            status="finished",
            turn_id=turn.id,
            final_text=continuation_text,
            error=None,
            event_ids=event_ids,
            metadata=success_metadata,
        )

    def _append_event(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        event_ids: list[int],
        *,
        correlation_id: str,
        turn_id: str,
    ) -> None:
        event = self._event_store.append(
            event_type,
            self._source,
            payload,
            correlation_id=correlation_id,
            turn_id=turn_id,
        )
        event_ids.append(event.id)
        if self._event_bus is not None:
            try:
                self._event_bus.publish(event)
            except Exception:
                pass

    def _capture_model_tool_calls(
        self,
        *,
        response: BrainResponse,
        turn_id: str,
        conversation_id: str,
        event_ids: list[int],
        correlation_id: str,
    ) -> "_ToolCaptureResult":
        if not response.tool_calls:
            return _ToolCaptureResult()

        result = _ToolCaptureResult()
        for index, tool_call in enumerate(response.tool_calls, start=1):
            call_id = _tool_call_id(tool_call, index)
            tool_name = _tool_call_name(tool_call)
            if self._tool_registry is None:
                result.tool_calls.append(
                    self._record_model_tool_call_failure(
                        call_id=call_id,
                        tool_name=tool_name,
                        status="unavailable",
                        error="tool registry is unavailable",
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue
            if self._tool_permission_policy is None:
                result.tool_calls.append(
                    self._record_model_tool_call_failure(
                        call_id=call_id,
                        tool_name=tool_name,
                        status="unavailable",
                        error="tool permission policy is unavailable",
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue

            try:
                arguments = _json_safe_arguments(tool_call)
            except TurnOrchestratorError as exc:
                result.tool_calls.append(
                    self._record_model_tool_call_failure(
                        call_id=call_id,
                        tool_name=tool_name,
                        status="failed",
                        error=str(exc),
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue

            request_id = str(uuid.uuid4())
            request = ToolRequest(
                id=request_id,
                tool_name=tool_name,
                arguments=arguments,
                requested_by="model",
                turn_id=turn_id,
                metadata={
                    "origin": "model",
                    "tool_call_id": call_id,
                },
            )
            try:
                permission = self._tool_registry.evaluate_permission(
                    request,
                    permission_policy=self._tool_permission_policy,
                    source=RequestSource.MODEL_ORIGINATED,
                )
            except ToolRegistryError as exc:
                result.tool_calls.append(
                    self._record_model_tool_call_failure(
                        call_id=call_id,
                        tool_name=tool_name,
                        status="unknown",
                        error=str(exc),
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue

            if permission.decision == ToolDecision.BLOCKED:
                result.tool_calls.append(
                    self._record_model_tool_call_blocked(
                        call_id=call_id,
                        tool_name=permission.tool_name,
                        risk=permission.risk,
                        reason=permission.reason,
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue

            if self._approval_gate is None:
                result.tool_calls.append(
                    self._record_model_tool_call_failure(
                        call_id=call_id,
                        tool_name=permission.tool_name,
                        status="unavailable",
                        error="approval gate is unavailable",
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        event_ids=event_ids,
                        correlation_id=correlation_id,
                    )
                )
                continue

            approval = self._approval_gate.create_approval(
                risk=permission.risk,
                requested_by="model",
                action_type=f"tool:{permission.tool_name}",
                payload={
                    "tool_name": permission.tool_name,
                    "arguments": arguments,
                    "requested_by": "model",
                    "source": str(RequestSource.MODEL_ORIGINATED),
                    "turn_id": turn_id,
                },
                metadata={
                    "origin": "model",
                    "tool_call_id": call_id,
                    "tool_request_id": request_id,
                },
                turn_id=turn_id,
                correlation_id=correlation_id,
            )
            approval_id = str(approval["id"])
            approval_reason = (
                permission.reason
                if permission.approval_required
                else "model-originated safe tool calls require explicit approval"
            )
            self._append_event(
                EventType.TOOL_REQUESTED,
                {
                    "run_id": request_id,
                    "tool_call_id": call_id,
                    "tool_name": permission.tool_name,
                    "risk": permission.risk,
                    "turn_id": turn_id,
                    "approval_id": approval_id,
                    "origin": "model",
                    "status": "approval_required",
                    "input": arguments,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )
            self._append_event(
                EventType.TOOL_APPROVAL_REQUIRED,
                {
                    "run_id": request_id,
                    "tool_call_id": call_id,
                    "tool_name": permission.tool_name,
                    "risk": permission.risk,
                    "turn_id": turn_id,
                    "approval_id": approval_id,
                    "origin": "model",
                    "reason": approval_reason,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )
            result.tool_calls.append(
                {
                    "id": call_id,
                    "tool_name": permission.tool_name,
                    "status": "approval_required",
                    "approval_required": True,
                    "approval_id": approval_id,
                    "error": None,
                }
            )
            result.approvals.append(
                {
                    "id": approval_id,
                    "tool_call_id": call_id,
                    "tool_name": permission.tool_name,
                    "status": str(approval["status"]),
                    "risk": str(approval["risk"]),
                }
            )

        return result

    def _record_model_tool_call_failure(
        self,
        *,
        call_id: str,
        tool_name: str,
        status: str,
        error: str,
        turn_id: str,
        conversation_id: str,
        event_ids: list[int],
        correlation_id: str,
    ) -> dict[str, Any]:
        summary = {
            "id": call_id,
            "tool_name": tool_name,
            "status": status,
            "approval_required": False,
            "approval_id": None,
            "error": error,
        }
        self._append_event(
            EventType.TOOL_FAILED,
            {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "origin": "model",
                "status": status,
                "error": error,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn_id,
        )
        self._append_event(
            EventType.ERROR_RAISED,
            {
                "turn_id": turn_id,
                "conversation_id": conversation_id,
                "kind": "tool",
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "error": error,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn_id,
        )
        return summary

    def _record_model_tool_call_blocked(
        self,
        *,
        call_id: str,
        tool_name: str,
        risk: str,
        reason: str,
        turn_id: str,
        conversation_id: str,
        event_ids: list[int],
        correlation_id: str,
    ) -> dict[str, Any]:
        summary = {
            "id": call_id,
            "tool_name": tool_name,
            "status": "blocked",
            "approval_required": False,
            "approval_id": None,
            "error": reason,
        }
        self._append_event(
            EventType.TOOL_REJECTED,
            {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "risk": risk,
                "origin": "model",
                "status": "blocked",
                "error": reason,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn_id,
        )
        self._append_event(
            EventType.ERROR_RAISED,
            {
                "turn_id": turn_id,
                "conversation_id": conversation_id,
                "kind": "tool",
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "error": reason,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn_id,
        )
        return summary

    def _record_context_failure(
        self,
        *,
        turn: Turn,
        conversation_id: str,
        error: Exception,
        event_ids: list[int],
        correlation_id: str,
    ) -> None:
        message = _error_message(error)
        self._fail_turn(turn, message, kind="context")
        self._append_failure_events(
            turn_id=turn.id,
            conversation_id=conversation_id,
            error=message,
            kind="context",
            event_ids=event_ids,
            correlation_id=correlation_id,
            include_brain_failed=False,
            brain_adapter=None,
            brain_model=None,
            error_first=True,
        )
        self._recover_runtime_after_failure(event_ids, correlation_id=correlation_id, turn_id=turn.id)

    def _record_brain_failure(
        self,
        *,
        turn: Turn,
        conversation_id: str,
        adapter: str,
        model: str,
        error: Exception,
        event_ids: list[int],
        correlation_id: str,
    ) -> None:
        message = _error_message(error)
        self._append_event(
            EventType.BRAIN_FAILED,
            {
                "turn_id": turn.id,
                "conversation_id": conversation_id,
                "adapter": adapter,
                "model": model,
                "error": message,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        self._fail_turn(turn, message, kind="brain")
        self._append_failure_events(
            turn_id=turn.id,
            conversation_id=conversation_id,
            error=message,
            kind="brain",
            event_ids=event_ids,
            correlation_id=correlation_id,
            include_brain_failed=False,
            brain_adapter=None,
            brain_model=None,
            error_first=False,
        )
        self._recover_runtime_after_failure(event_ids, correlation_id=correlation_id, turn_id=turn.id)

    def _record_brain_cancellation(
        self,
        *,
        turn: Turn,
        conversation_id: str,
        adapter: str,
        model: str,
        reason: Exception,
        event_ids: list[int],
        correlation_id: str,
    ) -> None:
        """Barge-in cancelled the generation: CANCELLED turn + IDLE runtime.

        Deliberately NOT a failure — no BRAIN_FAILED/TURN_FAILED/ERROR_RAISED and
        no transition through ERROR. Mirrors the failure recorder's shape so the
        audit trail is symmetric (brain.cancelled + turn.cancelled)."""

        message = _error_message(reason)
        self._append_event(
            EventType.BRAIN_CANCELLED,
            {
                "turn_id": turn.id,
                "conversation_id": conversation_id,
                "adapter": adapter,
                "model": model,
                "reason": message,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        self._cancel_turn_status(turn, message)
        self._append_event(
            EventType.TURN_CANCELLED,
            {
                "turn_id": turn.id,
                "conversation_id": conversation_id,
                "kind": "brain",
                "reason": message,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        self._settle_runtime_idle_after_completion(
            pending_approval=False,
            correlation_id=correlation_id,
            turn_id=turn.id,
            event_ids=event_ids,
            reason="text turn cancelled (barge-in)",
        )

    def _record_generic_failure(
        self,
        *,
        turn: Turn,
        conversation_id: str,
        error: Exception,
        event_ids: list[int],
        correlation_id: str,
    ) -> None:
        message = _error_message(error)
        self._fail_turn(turn, message, kind="orchestrator")
        self._append_failure_events(
            turn_id=turn.id,
            conversation_id=conversation_id,
            error=message,
            kind="orchestrator",
            event_ids=event_ids,
            correlation_id=correlation_id,
            include_brain_failed=False,
            brain_adapter=None,
            brain_model=None,
            error_first=True,
        )
        self._recover_runtime_after_failure(event_ids, correlation_id=correlation_id, turn_id=turn.id)

    def _record_continuation_failure(
        self,
        *,
        turn: Turn,
        adapter_name: str,
        request_model: str,
        error: Exception,
        event_ids: list[int],
        correlation_id: str,
        metadata: Mapping[str, Any],
    ) -> ToolResultContinuationResult:
        message = _error_message(error)
        failure_metadata = {
            **dict(metadata),
            "status": "failed",
            "error": message,
            "retry_policy": "no_automatic_retry",
        }
        self._append_event(
            EventType.BRAIN_FAILED,
            {
                "turn_id": turn.id,
                "conversation_id": turn.conversation_id,
                "adapter": adapter_name,
                "model": request_model,
                "error": message,
                "continuation": failure_metadata,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        self._append_event(
            EventType.ERROR_RAISED,
            {
                "turn_id": turn.id,
                "conversation_id": turn.conversation_id,
                "kind": "tool_result_continuation",
                "error": message,
                "continuation": failure_metadata,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        # A failed continuation must not leave the turn dangling in
        # AWAITING_APPROVAL forever — drive it to a terminal status (FIX-05
        # case 3). The approval was already executed; there is nothing to retry.
        self._fail_turn(turn, message, kind="continuation")
        self._turns.merge_metadata(
            turn.id,
            {"tool_result_continuation": failure_metadata},
        )
        return ToolResultContinuationResult(
            applied=False,
            status="failed",
            turn_id=turn.id,
            final_text=None,
            error=message,
            event_ids=event_ids,
            metadata=failure_metadata,
        )

    def _record_continuation_cancellation(
        self,
        *,
        turn: Turn,
        adapter_name: str,
        request_model: str,
        reason: Exception,
        event_ids: list[int],
        correlation_id: str,
        metadata: Mapping[str, Any],
    ) -> ToolResultContinuationResult:
        """Barge-in cancelled the continuation: CANCELLED turn, no failure.

        Mirrors _record_continuation_failure but emits brain.cancelled +
        turn.cancelled and drives the turn to CANCELLED (never FAILED/ERROR)."""

        message = _error_message(reason)
        cancel_metadata = {
            **dict(metadata),
            "status": "cancelled",
            "reason": message,
            "retry_policy": "no_automatic_retry",
        }
        self._append_event(
            EventType.BRAIN_CANCELLED,
            {
                "turn_id": turn.id,
                "conversation_id": turn.conversation_id,
                "adapter": adapter_name,
                "model": request_model,
                "reason": message,
                "continuation": cancel_metadata,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        # A cancelled continuation must not dangle in AWAITING_APPROVAL forever
        # either — drive it to CANCELLED (terminal). The approval already ran.
        self._cancel_turn_status(turn, message)
        self._append_event(
            EventType.TURN_CANCELLED,
            {
                "turn_id": turn.id,
                "conversation_id": turn.conversation_id,
                "kind": "tool_result_continuation",
                "reason": message,
                "continuation": cancel_metadata,
            },
            event_ids,
            correlation_id=correlation_id,
            turn_id=turn.id,
        )
        self._turns.merge_metadata(
            turn.id,
            {"tool_result_continuation": cancel_metadata},
        )
        return ToolResultContinuationResult(
            applied=False,
            status="cancelled",
            turn_id=turn.id,
            final_text=None,
            error=message,
            event_ids=event_ids,
            metadata=cancel_metadata,
        )

    def _append_failure_events(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        error: str,
        kind: str,
        event_ids: list[int],
        correlation_id: str,
        include_brain_failed: bool,
        brain_adapter: str | None,
        brain_model: str | None,
        error_first: bool,
    ) -> None:
        if include_brain_failed:
            self._append_event(
                EventType.BRAIN_FAILED,
                {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "adapter": brain_adapter,
                    "model": brain_model,
                    "error": error,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )

        def append_error() -> None:
            self._append_event(
                EventType.ERROR_RAISED,
                {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "kind": kind,
                    "error": error,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )

        def append_turn_failed() -> None:
            self._append_event(
                EventType.TURN_FAILED,
                {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "kind": kind,
                    "error": error,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn_id,
            )

        if error_first:
            append_error()
            append_turn_failed()
        else:
            append_turn_failed()
            append_error()

    def _fail_turn(self, turn: Turn, error: str, *, kind: str) -> None:
        # Never reclassify a turn that already reached a terminal outcome: a
        # post-completion error (a late transition, a failed audit append) must
        # not rewrite a FINISHED turn as FAILED (FIX-05 cases 1-2). AWAITING is
        # intentionally NOT terminal here — a failed continuation still fails it.
        try:
            current = self._turns.get(turn.id)
        except Exception:
            current = None
        status = current.status if current is not None else turn.status
        if status in _TERMINAL_TURN_STATUSES:
            return
        try:
            self._turns.fail(turn.id, error=error, metadata={"failure_kind": kind})
        except Exception:
            pass

    def _cancel_turn_status(self, turn: Turn, reason: str) -> None:
        # Same terminal guard as _fail_turn: never reclassify a turn that
        # already reached a terminal outcome (FIX-05).
        try:
            current = self._turns.get(turn.id)
        except Exception:
            current = None
        status = current.status if current is not None else turn.status
        if status in _TERMINAL_TURN_STATUSES:
            return
        try:
            self._turns.cancel(turn.id, reason=reason)
        except Exception:
            pass

    def _recover_runtime_after_failure(
        self,
        event_ids: list[int],
        *,
        correlation_id: str,
        turn_id: str,
    ) -> None:
        # Shutdown in progress: STOPPING is terminal and must not be disturbed.
        if self._state_machine.state is RuntimeState.STOPPING:
            return

        if self._state_machine.state is not RuntimeState.ERROR:
            try:
                event_ids.append(
                    self._state_machine.transition(
                        RuntimeState.ERROR,
                        reason="text turn failed",
                        correlation_id=correlation_id,
                        turn_id=turn_id,
                    ).event_id
                )
            except Exception:
                # Could not persist the ERROR transition: never strand the
                # runtime outside IDLE — reset in-memory as a last resort so the
                # next turn is not permanently rejected as busy (FIX-05 case 4).
                self._state_machine.force_idle(
                    reason="text turn failure recovered",
                    correlation_id=correlation_id,
                    turn_id=turn_id,
                )
                return

        if self._state_machine.state is RuntimeState.ERROR:
            try:
                event_ids.append(
                    self._state_machine.transition(
                        RuntimeState.IDLE,
                        reason="text turn failure recovered",
                        correlation_id=correlation_id,
                        turn_id=turn_id,
                    ).event_id
                )
            except Exception:
                self._state_machine.force_idle(
                    reason="text turn failure recovered",
                    correlation_id=correlation_id,
                    turn_id=turn_id,
                )


@dataclass
class _ToolCaptureResult:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)


def _required_text(value: str, label: str) -> str:
    try:
        return ensure_non_empty_text(value, label)
    except RepositoryError as exc:
        raise TurnOrchestratorError(str(exc)) from exc


def _metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        return ensure_mapping(value, "metadata")
    except RepositoryError as exc:
        raise TurnOrchestratorError(str(exc)) from exc


def _turn_source(value: str) -> str:
    try:
        return TurnSource(value).value
    except ValueError as exc:
        raise TurnOrchestratorError(f"Invalid turn source: {value}") from exc


def _model_from_adapter(adapter: Any, request: Any) -> str:
    value = getattr(adapter, "default_model", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _model_from_request(request, "unknown")


def _model_from_request(request: Any, default_model: str) -> str:
    settings = getattr(request, "settings", {})
    if isinstance(settings, Mapping):
        value = settings.get("model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default_model


def _response_model(response: BrainResponse, fallback: str) -> str:
    if isinstance(response.model, str) and response.model.strip():
        return response.model.strip()
    return fallback


def _usage_payload(response: BrainResponse) -> dict[str, int | None]:
    usage = response.usage
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def _tool_call_id(tool_call: Any, index: int) -> str:
    value = getattr(tool_call, "id", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"model-tool-call-{index}"


def _tool_call_name(tool_call: Any) -> str:
    value = getattr(tool_call, "name", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _json_safe_arguments(tool_call: Any) -> dict[str, Any]:
    arguments = getattr(tool_call, "arguments", {})
    if not isinstance(arguments, Mapping):
        raise TurnOrchestratorError("tool arguments must be a JSON object")
    try:
        json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TurnOrchestratorError("tool arguments must be JSON serializable") from exc
    return dict(arguments)


def _final_text_with_tool_summary(response_text: str, tool_calls: list[dict[str, Any]]) -> str:
    if not tool_calls:
        return response_text

    parts: list[str] = []
    for tool_call in tool_calls:
        tool_name = str(tool_call["tool_name"])
        status = str(tool_call["status"])
        if status == "approval_required":
            parts.append(f"{tool_name} requires approval")
        elif status == "blocked":
            parts.append(f"{tool_name} blocked")
        elif status == "unknown":
            parts.append(f"{tool_name} unknown")
        elif status == "unavailable":
            parts.append(f"{tool_name} unavailable")
        else:
            parts.append(f"{tool_name} failed")

    summary = "Tool requests captured: " + "; ".join(parts) + "."
    stripped_response = response_text.strip()
    if stripped_response:
        return f"{stripped_response}\n\n{summary}"
    return summary


def _error_message(error: Exception) -> str:
    message = str(error).strip()
    return message or error.__class__.__name__


CONTINUATION_ELIGIBLE_RESULT_CLASS = "continuation_eligible"
ONE_SHOT_RESULT_CLASSES = {CONTINUATION_ELIGIBLE_RESULT_CLASS, "one_shot"}
RESERVED_NON_ONE_SHOT_RESULT_CLASSES = {
    "requires_user_presence",
    "external_communication_pending",
    "operator_session_started",
    "live_visual_control_session",
    "worker_job_started",
}


def _continuation_eligibility(tool_result: ToolResult) -> dict[str, Any]:
    result_class = _tool_result_class(tool_result.output or {})
    return {
        "continuation_eligible": (
            tool_result.status == "finished" and result_class in ONE_SHOT_RESULT_CLASSES
        ),
        "result_class": result_class,
        "reserved_non_one_shot_result_classes": sorted(RESERVED_NON_ONE_SHOT_RESULT_CLASSES),
    }


def _tool_result_class(output: Mapping[str, Any]) -> str:
    raw_class = output.get("result_class")
    if isinstance(raw_class, str) and raw_class.strip():
        return raw_class.strip()
    return CONTINUATION_ELIGIBLE_RESULT_CLASS


def _continuation_metadata(
    *,
    approval_id: str,
    tool_request: ToolRequest,
    tool_result: ToolResult,
    tool_run: Mapping[str, Any],
    turn: Turn,
    eligibility: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "tool_name": tool_request.tool_name,
        "tool_run_id": _optional_mapping_text(tool_run, "id"),
        "previous_status": turn.status,
        "continuation_eligible": bool(eligibility["continuation_eligible"]),
        "result_class": str(eligibility["result_class"]),
        "user_approved_and_executed": True,
        "original_turn_id": turn.id,
        "original_user_input": redact_secrets(turn.input_text or ""),
        "original_context_snapshot": _redacted_jsonable(turn.context_snapshot or {}),
        "tool_arguments": _redacted_jsonable(tool_request.arguments),
        "tool_result": _redacted_jsonable(tool_result.output or {}),
    }


def _continuation_input_text(
    *,
    original_user_input: str,
    tool_name: str,
    tool_arguments: Mapping[str, Any],
    tool_output: Mapping[str, Any],
) -> str:
    arguments_json = json.dumps(tool_arguments, ensure_ascii=False, sort_keys=True)
    output_json = json.dumps(tool_output, ensure_ascii=False, sort_keys=True)
    return "\n".join(
        [
            "Continuation after approved tool execution.",
            "The user approved and explicitly executed the tool.",
            "Answer the original user request using the tool result.",
            "Do not request the same tool again unless necessary.",
            "Do not claim anything not supported by the tool result.",
            "",
            "Original user input:",
            original_user_input,
            "",
            f"Tool name: {tool_name}",
            "Approved tool arguments (JSON):",
            arguments_json,
            "Tool result/output (JSON):",
            output_json,
        ]
    )


def _continuation_answer_text(response: BrainResponse) -> str:
    return _required_text(response.text, "continuation final_text")


def _optional_mapping_text(value: Mapping[str, Any], key: str) -> str | None:
    raw_value = value.get(key)
    if raw_value is None:
        return None
    return str(raw_value)


def _redacted_jsonable(value: Any) -> Any:
    return redact_secrets(_jsonable(value))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            return str(value)
        return value
    return str(value)


__all__ = [
    "TextTurnResult",
    "ToolResultContinuationResult",
    "TurnCancelledError",
    "TurnOrchestrator",
    "TurnOrchestratorBusyError",
    "TurnOrchestratorError",
]
