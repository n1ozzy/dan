"""Single input pipeline orchestrator."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jarvis.brain.base import BrainResponse
from jarvis.brain.context_builder import ContextBuilder
from jarvis.brain.manager import BrainManager
from jarvis.daemon.state_machine import RuntimeState, RuntimeStateMachine
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.store.event_store import EventStore
from jarvis.store.repositories import RepositoryError, ensure_mapping, ensure_non_empty_text
from jarvis.turns.models import Turn, TurnSource, TurnStatus
from jarvis.turns.repository import ConversationRepository, TurnRepository


class TurnOrchestratorError(Exception):
    """Raised when a text turn cannot be completed."""


class TurnOrchestratorBusyError(TurnOrchestratorError):
    """Raised when a text turn cannot start because the runtime is not IDLE.

    Subclasses ``TurnOrchestratorError`` so existing callers keep working while
    the HTTP layer can map this precondition to 409 instead of 500.
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
        conversation_repository: ConversationRepository | None = None,
        turn_repository: TurnRepository | None = None,
        source: str = "turn_orchestrator",
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._event_bus = event_bus
        self._state_machine = state_machine
        self._brain_manager = brain_manager
        self._context_builder = context_builder
        self._conversations = conversation_repository or ConversationRepository(conn)
        self._turns = turn_repository or TurnRepository(conn)
        self._source = _required_text(source, "source")

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
            request_model = _model_from_request(context_result.request, adapter.default_model)
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

            try:
                response = self._brain_manager.generate(context_result.request)
            except Exception as exc:
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
            turn = self._turns.finish(
                turn.id,
                final_text=response.text,
                brain_adapter=adapter_name,
                brain_model=response_model,
            )
            self._append_event(
                EventType.TURN_FINISHED,
                {
                    "turn_id": turn.id,
                    "conversation_id": conversation.id,
                    "final_text_length": len(response.text),
                    "brain_adapter": adapter_name,
                    "brain_model": response_model,
                },
                event_ids,
                correlation_id=correlation_id,
                turn_id=turn.id,
            )
            event_ids.append(
                self._state_machine.transition(
                    RuntimeState.IDLE,
                    reason="text turn finished",
                    correlation_id=correlation_id,
                    turn_id=turn.id,
                ).event_id
            )

            return TextTurnResult(
                conversation_id=conversation.id,
                turn_id=turn.id,
                input_text=normalized_text,
                final_text=response.text,
                brain_adapter=adapter_name,
                brain_model=response_model,
                event_ids=event_ids,
                turn=turn,
            )
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
        try:
            self._turns.fail(turn.id, error=error, metadata={"failure_kind": kind})
        except Exception:
            pass

    def _recover_runtime_after_failure(
        self,
        event_ids: list[int],
        *,
        correlation_id: str,
        turn_id: str,
    ) -> None:
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
                pass


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


def _error_message(error: Exception) -> str:
    message = str(error).strip()
    return message or error.__class__.__name__


__all__ = [
    "TextTurnResult",
    "TurnOrchestrator",
    "TurnOrchestratorBusyError",
    "TurnOrchestratorError",
]
