"""Prompt 05 runtime state machine tests."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.git_guards import assert_schema_and_migrations_unchanged
from jarvis.daemon.state_machine import (
    RuntimeState,
    RuntimeStateMachine,
    StateTransitionError,
)
from jarvis.events.bus import EventBus
from jarvis.events.models import Event
from jarvis.store.db import close_quietly, initialize_database
from jarvis.store.event_store import EventStore, EventStoreError, create_event_store


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_STATES = [
    "BOOTING",
    "IDLE",
    "LISTENING",
    "TRANSCRIBING",
    "THINKING",
    "TOOLING",
    "SPEAKING",
    "INTERRUPTED",
    "ERROR",
    "STOPPING",
]

NORMAL_TRANSITIONS = [
    (RuntimeState.BOOTING, RuntimeState.IDLE),
    (RuntimeState.IDLE, RuntimeState.LISTENING),
    (RuntimeState.LISTENING, RuntimeState.TRANSCRIBING),
    (RuntimeState.TRANSCRIBING, RuntimeState.THINKING),
    (RuntimeState.IDLE, RuntimeState.THINKING),
    (RuntimeState.THINKING, RuntimeState.TOOLING),
    (RuntimeState.TOOLING, RuntimeState.THINKING),
    (RuntimeState.THINKING, RuntimeState.SPEAKING),
    (RuntimeState.THINKING, RuntimeState.IDLE),
    (RuntimeState.SPEAKING, RuntimeState.IDLE),
    (RuntimeState.SPEAKING, RuntimeState.INTERRUPTED),
    (RuntimeState.INTERRUPTED, RuntimeState.LISTENING),
    (RuntimeState.INTERRUPTED, RuntimeState.THINKING),
    (RuntimeState.ERROR, RuntimeState.IDLE),
]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_conn = initialize_database(tmp_path / "jarvis.db")
    try:
        yield db_conn
    finally:
        close_quietly(db_conn)


@pytest.fixture
def store(conn: sqlite3.Connection) -> EventStore:
    return create_event_store(conn)


def machine(
    store: EventStore,
    *,
    initial_state: RuntimeState | str = RuntimeState.BOOTING,
    event_bus: EventBus | None = None,
) -> RuntimeStateMachine:
    return RuntimeStateMachine(store, event_bus=event_bus, initial_state=initial_state)


def state_events(store: EventStore) -> list[Event]:
    return [event for event in store.latest(limit=100) if event.type == "state.changed"]


def test_runtime_state_values_are_exactly_the_prompt_05_set() -> None:
    assert [state.value for state in RuntimeState] == EXPECTED_STATES


def test_initial_state_is_booting(store: EventStore) -> None:
    state_machine = machine(store)

    assert state_machine.state is RuntimeState.BOOTING
    assert state_events(store) == []


def test_booting_to_idle_succeeds(store: EventStore) -> None:
    state_machine = machine(store)

    transition = state_machine.transition(RuntimeState.IDLE, reason="ready")

    assert state_machine.state is RuntimeState.IDLE
    assert transition.old_state is RuntimeState.BOOTING
    assert transition.new_state is RuntimeState.IDLE
    assert transition.event_id > 0
    assert transition.reason == "ready"


@pytest.mark.parametrize(("old_state", "new_state"), NORMAL_TRANSITIONS)
def test_every_allowed_normal_transition_succeeds(
    store: EventStore,
    old_state: RuntimeState,
    new_state: RuntimeState,
) -> None:
    state_machine = machine(store, initial_state=old_state)

    transition = state_machine.transition(new_state)

    assert transition.old_state is old_state
    assert transition.new_state is new_state
    assert state_machine.state is new_state
    assert len(state_events(store)) == 1


def test_invalid_transition_raises_and_does_not_append_event(store: EventStore) -> None:
    state_machine = machine(store, initial_state=RuntimeState.IDLE)

    with pytest.raises(StateTransitionError, match="not allowed"):
        state_machine.transition(RuntimeState.SPEAKING)

    assert state_machine.state is RuntimeState.IDLE
    assert state_events(store) == []


def test_invalid_target_value_raises_clearly(store: EventStore) -> None:
    state_machine = machine(store)

    with pytest.raises(StateTransitionError, match="Unknown runtime state"):
        state_machine.transition("WAITING_APPROVAL")

    assert state_machine.state is RuntimeState.BOOTING
    assert state_events(store) == []


def test_same_state_transition_is_invalid_and_does_not_append_event(store: EventStore) -> None:
    state_machine = machine(store, initial_state=RuntimeState.IDLE)

    with pytest.raises(StateTransitionError, match="same state"):
        state_machine.transition(RuntimeState.IDLE)

    assert state_machine.state is RuntimeState.IDLE
    assert state_events(store) == []


@pytest.mark.parametrize("old_state", [state for state in RuntimeState if state is not RuntimeState.STOPPING])
def test_any_non_stopping_state_can_transition_to_error(
    store: EventStore,
    old_state: RuntimeState,
) -> None:
    if old_state is RuntimeState.ERROR:
        return
    state_machine = machine(store, initial_state=old_state)

    transition = state_machine.transition(RuntimeState.ERROR)

    assert transition.old_state is old_state
    assert transition.new_state is RuntimeState.ERROR
    assert state_machine.state is RuntimeState.ERROR


@pytest.mark.parametrize("old_state", [state for state in RuntimeState if state is not RuntimeState.STOPPING])
def test_any_non_stopping_state_can_transition_to_stopping(
    store: EventStore,
    old_state: RuntimeState,
) -> None:
    state_machine = machine(store, initial_state=old_state)

    transition = state_machine.transition(RuntimeState.STOPPING)

    assert transition.old_state is old_state
    assert transition.new_state is RuntimeState.STOPPING
    assert state_machine.state is RuntimeState.STOPPING


def test_stopping_is_terminal(store: EventStore) -> None:
    state_machine = machine(store, initial_state=RuntimeState.STOPPING)

    with pytest.raises(StateTransitionError, match="terminal"):
        state_machine.transition(RuntimeState.IDLE)

    assert state_machine.state is RuntimeState.STOPPING
    assert state_events(store) == []


def test_error_to_idle_succeeds(store: EventStore) -> None:
    state_machine = machine(store, initial_state=RuntimeState.ERROR)

    transition = state_machine.transition(RuntimeState.IDLE)

    assert transition.old_state is RuntimeState.ERROR
    assert transition.new_state is RuntimeState.IDLE
    assert state_machine.state is RuntimeState.IDLE


def test_successful_transition_appends_one_state_changed_event(store: EventStore) -> None:
    state_machine = machine(store)

    transition = state_machine.transition(RuntimeState.IDLE)

    events = state_events(store)
    assert len(events) == 1
    assert events[0].id == transition.event_id
    assert events[0].type == "state.changed"
    assert events[0].source == "state_machine"


def test_successful_transition_payload_contains_state_reason_and_metadata(
    store: EventStore,
) -> None:
    state_machine = machine(store)

    state_machine.transition(
        RuntimeState.IDLE,
        reason="startup complete",
        metadata={"component": "bootstrap", "attempt": 1},
    )

    payload = state_events(store)[0].payload
    assert payload == {
        "old_state": "BOOTING",
        "new_state": "IDLE",
        "reason": "startup complete",
        "metadata": {"component": "bootstrap", "attempt": 1},
    }


def test_transition_payload_redacts_secret_like_metadata(store: EventStore) -> None:
    state_machine = machine(store)

    state_machine.transition(
        RuntimeState.IDLE,
        reason="OPENAI_API_KEY=sk-proj-secret",
        metadata={"authorization": "Authorization: Bearer token-value"},
    )

    payload = state_events(store)[0].payload
    assert "sk-proj-secret" not in str(payload)
    assert "token-value" not in str(payload)
    assert "[REDACTED]" in str(payload)


def test_correlation_id_and_turn_id_are_persisted_on_event(store: EventStore) -> None:
    state_machine = machine(store)

    state_machine.transition(RuntimeState.IDLE, correlation_id="corr-1", turn_id="turn-1")

    event = state_events(store)[0]
    assert event.correlation_id == "corr-1"
    assert event.turn_id == "turn-1"


def test_event_bus_receives_the_persisted_state_changed_event(store: EventStore) -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(received.append)
    state_machine = machine(store, event_bus=bus)

    transition = state_machine.transition(RuntimeState.IDLE)

    assert len(received) == 1
    assert received[0].id == transition.event_id
    assert received[0] == state_events(store)[0]


def test_event_bus_failing_subscriber_does_not_prevent_transition(
    store: EventStore,
) -> None:
    bus = EventBus()
    received: list[Event] = []

    def fail(_: Event) -> None:
        raise RuntimeError("subscriber failed")

    bus.subscribe(fail)
    bus.subscribe(received.append)
    state_machine = machine(store, event_bus=bus)

    transition = state_machine.transition(RuntimeState.IDLE)

    assert state_machine.state is RuntimeState.IDLE
    assert received[0].id == transition.event_id
    assert len(bus.last_errors) == 1


def test_state_changes_only_after_event_append_succeeds(store: EventStore) -> None:
    state_machine = machine(store, initial_state=RuntimeState.BOOTING)

    with pytest.raises(StateTransitionError, match="JSON"):
        state_machine.transition(RuntimeState.IDLE, metadata={"bad": object()})

    assert state_machine.state is RuntimeState.BOOTING
    assert state_events(store) == []


def test_allowed_targets_returns_expected_runtime_state_values(store: EventStore) -> None:
    state_machine = machine(store, initial_state=RuntimeState.THINKING)

    assert state_machine.allowed_targets() == {
        RuntimeState.TOOLING,
        RuntimeState.SPEAKING,
        RuntimeState.IDLE,
        RuntimeState.ERROR,
        RuntimeState.STOPPING,
    }
    assert state_machine.allowed_targets(RuntimeState.STOPPING) == set()


def test_can_transition_uses_the_same_policy_as_transition(store: EventStore) -> None:
    state_machine = machine(store, initial_state="IDLE")

    assert state_machine.can_transition("LISTENING") is True
    assert state_machine.can_transition("SPEAKING") is False


class _GatedFakeEventStore:
    """Thread-safe in-memory stand-in for EventStore (real SQLite is
    single-thread). The FIRST append blocks inside the critical section until
    released, letting a second thread try to enter transition() concurrently.
    Deterministic: the second appender signals arrival, so the test never
    depends on sleeps, and no cross-thread SQLite handle is touched."""

    class _Event:
        def __init__(self, event_id: int, payload: dict[str, Any]) -> None:
            self.id = event_id
            self.type = "state.changed"
            self.payload = payload

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._append_calls = 0
        self.appended: list[_GatedFakeEventStore._Event] = []
        self.first_entered = threading.Event()
        self.second_entered = threading.Event()
        self.release = threading.Event()

    def append(
        self,
        event_type: Any,
        source: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
        turn_id: str | None = None,
    ) -> "_GatedFakeEventStore._Event":
        with self._lock:
            self._append_calls += 1
            is_first = self._append_calls == 1
        if is_first:
            self.first_entered.set()
            # Safety fallback so a wrongly-locked run cannot hang forever.
            self.release.wait(timeout=5.0)
        else:
            self.second_entered.set()
        with self._lock:
            event = _GatedFakeEventStore._Event(len(self.appended) + 1, dict(payload))
            self.appended.append(event)
        return event


def test_concurrent_transition_from_same_state_appends_single_event() -> None:
    # FIX-05 (case 5): transition() must validate+append+assign atomically.
    # Two threads racing from IDLE must yield exactly ONE IDLE-origin
    # transition, not two — otherwise the check-then-act is unsynchronized.
    gated = _GatedFakeEventStore()
    state_machine = RuntimeStateMachine(gated, initial_state=RuntimeState.IDLE)
    errors: list[StateTransitionError] = []

    def attempt(target: RuntimeState) -> None:
        try:
            state_machine.transition(target)
        except StateTransitionError as exc:
            errors.append(exc)

    first = threading.Thread(target=attempt, args=(RuntimeState.THINKING,))
    second = threading.Thread(target=attempt, args=(RuntimeState.LISTENING,))

    first.start()
    assert gated.first_entered.wait(2.0), "first transition never reached append"
    second.start()
    # With a lock, the second thread blocks on acquire and never reaches append
    # before release; without it, it slips into the critical section.
    gated.second_entered.wait(1.0)
    gated.release.set()
    first.join(3.0)
    second.join(3.0)
    assert not first.is_alive()
    assert not second.is_alive()

    idle_origin = [
        event for event in gated.appended if event.payload["old_state"] == "IDLE"
    ]
    assert len(idle_origin) == 1
    assert len(errors) == 1


def test_force_idle_resets_in_memory_even_when_append_fails(store: EventStore) -> None:
    # FIX-05 (case 4): recovery must not strand the runtime outside IDLE when the
    # state.changed event cannot be persisted.
    class _FailingAppendStore:
        def __init__(self, real: EventStore) -> None:
            self._real = real

        def append(self, *args: Any, **kwargs: Any) -> Any:
            raise EventStoreError("cannot persist")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    state_machine = RuntimeStateMachine(
        _FailingAppendStore(store), initial_state=RuntimeState.THINKING
    )

    state_machine.force_idle(reason="recovered")

    assert state_machine.state is RuntimeState.IDLE


def test_force_idle_is_noop_when_stopping(store: EventStore) -> None:
    # STOPPING is terminal shutdown; force_idle must never resurrect it.
    state_machine = machine(store, initial_state=RuntimeState.THINKING)
    state_machine.transition(RuntimeState.STOPPING)

    state_machine.force_idle(reason="should be ignored")

    assert state_machine.state is RuntimeState.STOPPING


def test_sqlite_schema_and_migrations_are_not_modified() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "jarvis",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css"}
    offenders: list[tuple[str, str]] = []

    for root in roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
