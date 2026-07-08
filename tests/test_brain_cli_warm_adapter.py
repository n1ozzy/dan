"""Warm (persistent-process) Claude CLI adapter — barge-in cancel wiring.

The warm adapter keeps ONE long-lived `claude` process and feeds it turns
through stdin. Unlike the streaming adapter (one process per turn), a barge-in
must interrupt only the CURRENT generation without corrupting the shared
process — so cancel = terminate + recycle the session, and the NEXT turn spawns
a fresh process.

The adapter joins the same barge-in contract as the streaming path (FIX-09): a
cancel registered in the GenerationRegistry fires, kills the read in flight, and
surfaces as ``BrainGenerationCancelled`` (the distinct subclass) — so the
orchestrator marks the turn CANCELLED, never a misreported FAILED. That
orchestrator mapping is proven adapter-agnostically elsewhere
(tests/test_text_turn_pipeline.py, CancellingBrainAdapter); here we prove the
warm adapter raises exactly that subclass and recycles cleanly.

Every process is a deterministic fake; nothing real runs.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.base import (
    BrainAdapterError,
    BrainGenerationCancelled,
    BrainRequest,
)
from jarvis.brain.claude_cli_warm_adapter import ClaudeCliWarmAdapter
from jarvis.brain.manager import BrainManager
from jarvis.voice.cancellation import GenerationRegistry


def make_request(text: str = "Opowiedz o pogodzie.") -> BrainRequest:
    return BrainRequest(
        turn_id="turn-warm-1",
        conversation_id="conversation-1",
        input_text=text,
    )


def line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def partial(text: str) -> str:
    return line(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def assistant(text: str) -> str:
    return line(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def result(text: str, **extra: Any) -> str:
    payload = {"type": "result", "subtype": "success", "is_error": False, "result": text}
    payload.update(extra)
    return line(payload)


class _RecordingStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flushes = 0
        self.closed = False

    def write(self, data: str) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closed = True


class _ScriptedStdout:
    def __init__(self, proc: "FakeWarmProcess") -> None:
        self._proc = proc

    def readline(self) -> str:
        return self._proc._readline()


class FakeWarmProcess:
    """Duck-typed persistent Popen double: scripted stdout, recorded signals.

    ``block=True`` hangs at end-of-script (a generation still in flight) until a
    terminate/kill lands, exactly the window a barge-in interrupts.
    """

    def __init__(self, lines: list[str], *, block: bool = False) -> None:
        self._lines = list(lines)
        self._index = 0
        self._block = block
        self._unblock = threading.Event()
        self.returncode: int | None = None
        self.pid = 0  # non-positive → _signal_process falls back to terminate()
        self.terminated = 0
        self.killed = 0
        self.stdin = _RecordingStdin()
        self.stdout = _ScriptedStdout(self)

    def _readline(self) -> str:
        if self.terminated or self.killed:
            return ""  # EOF once the process is signalled dead
        if self._index < len(self._lines):
            item = self._lines[self._index]
            self._index += 1
            return item
        if self._block:
            self._unblock.wait(timeout=30)
        return ""  # EOF: death between turns, or unblocked after cancel

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        if self.returncode is None:
            self.returncode = -15
        self._unblock.set()

    def kill(self) -> None:
        self.killed += 1
        if self.returncode is None:
            self.returncode = -9
        self._unblock.set()

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


class FakeWarmFactory:
    """Hands out pre-scripted processes in spawn order; asserts on over-spawn."""

    def __init__(self, *processes: FakeWarmProcess) -> None:
        self._queue = list(processes)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> FakeWarmProcess:
        self.commands.append(list(command))
        if not self._queue:
            raise AssertionError("warm factory exhausted: unexpected extra spawn")
        return self._queue.pop(0)


def warm_adapter(
    *processes: FakeWarmProcess,
    generation_registry: Any | None = None,
) -> tuple[ClaudeCliWarmAdapter, FakeWarmFactory]:
    factory = FakeWarmFactory(*processes)
    adapter = ClaudeCliWarmAdapter(
        command="claude",
        args=["-p"],
        model="claude-warm-test",
        timeout_seconds=5,
        process_factory=factory,
        generation_registry=generation_registry,
    )
    return adapter, factory


# --- happy path (proves the factory injection + parser still work) ------------


def test_warm_generate_returns_canonical_result_and_writes_prompt() -> None:
    proc = FakeWarmProcess(
        [
            line({"type": "system", "subtype": "init"}),
            partial("Część "),
            partial("odpowiedzi."),
            assistant("Część odpowiedzi."),
            result("Część odpowiedzi."),
        ]
    )
    adapter, factory = warm_adapter(proc)
    deltas: list[str] = []

    response = adapter.generate(make_request(), on_delta=deltas.append)

    assert response.text == "Część odpowiedzi."
    assert deltas == ["Część ", "odpowiedzi."]
    assert factory.commands == [
        [
            "claude",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
    ]
    # The turn's prompt was written to the persistent stdin as a stream-json
    # user message — one shared process fed a fresh turn, not a per-turn spawn.
    assert proc.stdin.writes
    assert '"role": "user"' in proc.stdin.writes[0]
    assert "Opowiedz o pogodzie" in proc.stdin.writes[0]


def test_warm_reuses_the_persistent_process_across_turns() -> None:
    proc = FakeWarmProcess([result("Pierwsza."), result("Druga.")])
    adapter, factory = warm_adapter(proc)

    first = adapter.generate(make_request("pierwsza"))
    second = adapter.generate(make_request("druga"))

    assert first.text == "Pierwsza."
    assert second.text == "Druga."
    assert len(factory.commands) == 1  # spawned once, fed twice
    assert len(proc.stdin.writes) == 2


# --- barge-in leg 1: the generation registry ----------------------------------


def test_warm_generate_registers_and_unregisters_kill_handle() -> None:
    class SpyRegistry(GenerationRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.registered: list[str] = []
            self.unregistered: list[str] = []
            self.handles: dict[str, Any] = {}

        def register(self, turn_id: str, cancel: Any) -> None:
            self.registered.append(turn_id)
            self.handles[turn_id] = cancel
            super().register(turn_id, cancel)

        def unregister(self, turn_id: str) -> None:
            self.unregistered.append(turn_id)
            super().unregister(turn_id)

    registry = SpyRegistry()
    proc = FakeWarmProcess([result("Krótka odpowiedź.")])
    adapter, _ = warm_adapter(proc, generation_registry=registry)

    adapter.generate(make_request(), on_delta=lambda _: None)

    assert registry.registered == ["turn-warm-1"]
    assert registry.unregistered == ["turn-warm-1"]
    assert registry.active_count() == 0
    # The registered handle really terminates the persistent process (leg 1).
    registry.handles["turn-warm-1"]()
    assert proc.terminated or proc.killed


def test_warm_generate_without_registry_still_works() -> None:
    proc = FakeWarmProcess([result("Bez rejestru.")])
    adapter, _ = warm_adapter(proc)  # generation_registry=None

    response = adapter.generate(make_request())

    assert response.text == "Bez rejestru."


def test_barge_in_during_warm_generation_raises_generation_cancelled() -> None:
    # The reproducer: a barge-in during warm generation must surface as the
    # distinct BrainGenerationCancelled subclass (→ orchestrator marks the turn
    # CANCELLED, never FAILED), not as a generic adapter error.
    registry = GenerationRegistry()
    proc = FakeWarmProcess(
        [line({"type": "system", "subtype": "init"}), partial("początek")],
        block=True,  # hangs mid-generation until the barge-in kills it
    )
    adapter, _ = warm_adapter(proc, generation_registry=registry)
    captured: list[Exception] = []
    done = threading.Event()

    def run() -> None:
        try:
            adapter.generate(make_request(), on_delta=lambda _: None)
        except BrainAdapterError as exc:
            captured.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and registry.active_count() == 0:
        time.sleep(0.01)
    assert registry.active_count() == 1

    registry.cancel_all()

    assert done.wait(timeout=5)
    assert len(captured) == 1
    assert isinstance(captured[0], BrainGenerationCancelled)
    assert proc.terminated or proc.killed
    assert registry.active_count() == 0  # unregistered on the cancel path


def test_warm_recycles_process_after_cancel_so_next_turn_works() -> None:
    # The warm-specific concern: cancel must NOT leave the persistent process in
    # a broken state. After a barge-in, the session is recycled and the NEXT
    # turn spawns a fresh process and answers normally.
    registry = GenerationRegistry()
    dying = FakeWarmProcess([partial("urwane")], block=True)
    fresh = FakeWarmProcess([result("Świeża odpowiedź.")])
    adapter, factory = warm_adapter(dying, fresh, generation_registry=registry)

    captured: list[Exception] = []
    done = threading.Event()

    def run() -> None:
        try:
            adapter.generate(make_request("pierwsza"), on_delta=lambda _: None)
        except BrainAdapterError as exc:
            captured.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and registry.active_count() == 0:
        time.sleep(0.01)
    assert registry.active_count() == 1
    registry.cancel_all()
    assert done.wait(timeout=5)
    assert isinstance(captured[0], BrainGenerationCancelled)
    assert dying.terminated or dying.killed

    # Second turn: a FRESH process is spawned and the turn succeeds.
    response = adapter.generate(make_request("druga"))

    assert response.text == "Świeża odpowiedź."
    assert len(factory.commands) == 2  # recycled: dying killed, fresh spawned
    assert fresh.stdin.writes  # the fresh process was actually fed the turn


def test_warm_process_death_without_cancel_stays_a_failure() -> None:
    # A genuine EOF (process died between turns) with no barge-in is a plain
    # failure — never misreported as a cancellation.
    proc = FakeWarmProcess([])  # immediate EOF, no result, no cancel
    adapter, _ = warm_adapter(proc)

    with pytest.raises(BrainAdapterError) as exc_info:
        adapter.generate(make_request())

    assert not isinstance(exc_info.value, BrainGenerationCancelled)


# --- from_config wiring (requirement 4) ---------------------------------------


def test_from_config_passes_generation_registry_to_warm_adapter() -> None:
    registry = GenerationRegistry()
    config = SimpleNamespace(
        brain=SimpleNamespace(
            default_adapter="claude_cli_warm",
            default_model="mock-local",
            claude_cli=SimpleNamespace(
                command="claude", args=["-p"], model="", timeout_seconds=120
            ),
            claude_cli_warm=SimpleNamespace(
                command="claude", args=["-p"], model="", timeout_seconds=120, enabled=True
            ),
        )
    )

    manager = BrainManager.from_config(config, generation_registry=registry)
    adapter = manager.get_adapter("claude_cli_warm")

    assert isinstance(adapter, ClaudeCliWarmAdapter)
    assert adapter._generation_registry is registry
    assert manager.current_adapter_name == "claude_cli_warm"
