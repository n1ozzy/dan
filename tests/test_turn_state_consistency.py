"""FIX-05: turn/orchestrator state consistency.

Every case here defends the same invariant: a state transition must never
reclassify a finished turn as FAILED, never strand a turn in a trap state, and
never leave the runtime stranded outside IDLE after a failed turn.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.brain import (
    BrainAdapterError,
    BrainManager,
    BrainRequest,
    BrainResponse,
    MockBrainAdapter,
)
from jarvis.brain.context_builder import ContextBuilder
from jarvis.daemon.state_machine import (
    RuntimeState,
    RuntimeStateMachine,
    StateTransitionError,
    _coerce_state,
)
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import create_event_store
from jarvis.tools.registry import ToolRequest, ToolResult
from jarvis.turns.models import TurnStatus
from jarvis.turns.orchestrator import TurnOrchestrator, TurnOrchestratorError
from jarvis.turns.repository import ConversationRepository, TurnRepository


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = initialize_database(tmp_path / "turn-state.db")
    try:
        yield connection
    finally:
        close_quietly(connection)


class FailingBrainAdapter:
    name = "failing"
    default_model = "failing-model"

    def available_models(self) -> list[str]:
        return [self.default_model]

    def generate(self, request: BrainRequest) -> BrainResponse:
        raise BrainAdapterError("mock brain failure")


def build_orchestrator(
    conn: sqlite3.Connection,
    *,
    state_machine: RuntimeStateMachine,
    brain_manager: BrainManager | None = None,
    speech_pipeline: object | None = None,
) -> TurnOrchestrator:
    event_store = create_event_store(conn)
    return TurnOrchestrator(
        conn=conn,
        event_store=event_store,
        event_bus=None,
        state_machine=state_machine,
        brain_manager=brain_manager or BrainManager([MockBrainAdapter()], default_adapter="mock"),
        context_builder=ContextBuilder(conn),
        speech_pipeline=speech_pipeline,
    )


def turn_event_types(conn: sqlite3.Connection, turn_id: str) -> list[str]:
    return [
        event.type
        for event in create_event_store(conn).list_by_turn_id(turn_id, limit=200)
    ]


class _StopOnFinalizeSpeech:
    """Speech pipeline whose finalize() flips the runtime to STOPPING — exactly
    reproducing stop() winning the race between finish() and transition(IDLE)."""

    def __init__(self, state_machine: RuntimeStateMachine) -> None:
        self._sm = state_machine

    def arm_filler(self, *, turn_id: str) -> object | None:
        return None

    def start_stream(self, *, turn_id: str, filler_timer: object | None) -> object:
        sm = self._sm

        class _Session:
            def feed(self, *args: object, **kwargs: object) -> None:
                return None

            def finalize(self, text: str) -> None:
                sm.transition(RuntimeState.STOPPING, reason="stop() race")

        return _Session()


class _TargetPersistFailsStateMachine(RuntimeStateMachine):
    """A state machine whose transition() to a chosen target cannot be
    persisted, simulating an event-store failure at that transition."""

    def __init__(self, *args: object, fail_target: RuntimeState, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._fail_target = fail_target

    def transition(self, target: RuntimeState | str, **kwargs: object):  # type: ignore[override]
        if _coerce_state(target) is self._fail_target:
            raise StateTransitionError(f"cannot persist {self._fail_target.value}")
        return super().transition(target, **kwargs)


def test_shutdown_race_after_finish_keeps_turn_finished(conn: sqlite3.Connection) -> None:
    # Case 1 (HIGH): stop() flips STOPPING between finish() and transition(IDLE);
    # the spoken, finished turn must stay FINISHED and never become FAILED.
    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(event_store, initial_state=RuntimeState.IDLE)
    orchestrator = build_orchestrator(
        conn,
        state_machine=state_machine,
        speech_pipeline=_StopOnFinalizeSpeech(state_machine),
    )

    result = orchestrator.handle_text(text="Race against shutdown")

    turn = TurnRepository(conn).get(result.turn_id)
    assert turn is not None
    assert turn.status == TurnStatus.FINISHED.value
    types = turn_event_types(conn, result.turn_id)
    assert "turn.finished" in types
    assert "turn.failed" not in types
    # The runtime legitimately reached STOPPING; it must NOT be resurrected.
    assert state_machine.state is RuntimeState.STOPPING


def test_post_finish_persist_failure_does_not_reclassify_turn(
    conn: sqlite3.Connection,
) -> None:
    # Case 2: a persist failure on the post-finish transition(IDLE) must not
    # reclassify the finished turn, and the runtime must settle back to IDLE.
    event_store = create_event_store(conn)
    state_machine = _TargetPersistFailsStateMachine(
        event_store, initial_state=RuntimeState.IDLE, fail_target=RuntimeState.IDLE
    )
    orchestrator = build_orchestrator(conn, state_machine=state_machine)

    result = orchestrator.handle_text(text="Persist fails after finish")

    turn = TurnRepository(conn).get(result.turn_id)
    assert turn is not None
    assert turn.status == TurnStatus.FINISHED.value
    types = turn_event_types(conn, result.turn_id)
    assert "turn.finished" in types
    assert "turn.failed" not in types
    assert state_machine.state is RuntimeState.IDLE


def test_recover_runtime_resets_to_idle_when_error_persist_fails(
    conn: sqlite3.Connection,
) -> None:
    # Case 4: when recovery cannot persist the ERROR transition, the runtime
    # must not be stranded in THINKING — it must settle to IDLE in-memory.
    event_store = create_event_store(conn)
    state_machine = _TargetPersistFailsStateMachine(
        event_store, initial_state=RuntimeState.IDLE, fail_target=RuntimeState.ERROR
    )
    orchestrator = build_orchestrator(
        conn,
        state_machine=state_machine,
        brain_manager=BrainManager([FailingBrainAdapter()], default_adapter="failing"),
    )

    with pytest.raises(TurnOrchestratorError, match="brain"):
        orchestrator.handle_text(text="Fail then recover")

    turn = conn.execute("SELECT status FROM turns").fetchone()
    assert turn[0] == TurnStatus.FAILED.value
    assert state_machine.state is RuntimeState.IDLE


def test_failed_tool_result_continuation_marks_turn_failed(
    conn: sqlite3.Connection,
) -> None:
    # Case 3: a continuation whose generation fails must not leave the turn
    # dangling in AWAITING_APPROVAL — it must reach a terminal status.
    conversations = ConversationRepository(conn)
    turns = TurnRepository(conn)
    conversation = conversations.create(conversation_id="conv-continuation")
    turn = turns.create(
        conversation.id,
        source="api",
        input_text="Please run the tool",
        status=TurnStatus.AWAITING_APPROVAL.value,
    )

    event_store = create_event_store(conn)
    state_machine = RuntimeStateMachine(event_store, initial_state=RuntimeState.IDLE)
    orchestrator = build_orchestrator(
        conn,
        state_machine=state_machine,
        brain_manager=BrainManager([FailingBrainAdapter()], default_adapter="failing"),
    )

    tool_request = ToolRequest(
        id="tool-req-1",
        tool_name="echo",
        arguments={"text": "hi"},
        requested_by="model",
        turn_id=turn.id,
    )
    tool_result = ToolResult(
        id="tool-res-1",
        tool_name="echo",
        status="finished",
        output={"result_class": "continuation_eligible", "text": "hi"},
    )

    outcome = orchestrator.continue_after_tool_result(
        approval_id="approval-1",
        tool_request=tool_request,
        tool_result=tool_result,
        tool_run={"id": "run-1"},
    )

    assert outcome is not None
    assert outcome.applied is False
    assert outcome.status == "failed"
    persisted = turns.get(turn.id)
    assert persisted is not None
    assert persisted.status == TurnStatus.FAILED.value
