"""G4d streaming adapter tests (G0 §2: optional on_delta, canonical text).

The Claude CLI adapter gains a streaming path (`--output-format
stream-json`): deltas flow to on_delta best-effort and carry NO authority —
`BrainResponse.text` comes from the CLI's final result event only, tool
calls are parsed from that canonical text exactly like the blocking path,
and nothing about deltas is persisted here. Adapters that cannot stream
keep working unchanged (the manager only passes on_delta to adapters that
declare it). Every subprocess is fake; nothing real runs.
"""

from __future__ import annotations

import io
import json
import threading
import time
from typing import Any

import pytest

from jarvis.brain import BrainAdapterError, BrainRequest
from jarvis.brain.claude_cli_adapter import (
    DEFAULT_STREAM_ARGS,
    ClaudeCliAdapter,
)
from jarvis.brain.codex_cli_adapter import CodexCliAdapter
from jarvis.brain.manager import BrainManager
from jarvis.brain.mock_adapter import MockBrainAdapter
from jarvis.voice.cancellation import GenerationRegistry


def make_request(text: str = "Opowiedz o pogodzie.") -> BrainRequest:
    return BrainRequest(
        turn_id="turn-stream-1",
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


class FakeStreamProcess:
    """Duck-typed Popen double: scripted stdout lines, recorded signals."""

    def __init__(
        self,
        lines: list[str],
        *,
        returncode: int = 0,
        stderr: str = "",
        block_after: int | None = None,
    ) -> None:
        self._lines = lines
        self._final_returncode = returncode
        self.returncode: int | None = None
        self.stderr = io.StringIO(stderr)
        self.terminated = 0
        self.killed = 0
        self._unblock = threading.Event()
        self._block_after = block_after

    @property
    def stdout(self):
        for index, item in enumerate(self._lines):
            if self._block_after is not None and index == self._block_after:
                self._unblock.wait(timeout=30)
                if self.terminated or self.killed:
                    return
            yield item
        # block_after == len(lines): hang at end-of-stream until killed,
        # like a CLI that stops emitting without exiting.
        if self._block_after is not None and self._block_after >= len(self._lines):
            self._unblock.wait(timeout=30)

    def terminate(self) -> None:
        self.terminated += 1
        self._unblock.set()

    def kill(self) -> None:
        self.killed += 1
        self._unblock.set()

    def wait(self, timeout: float | None = None) -> int:
        if self.terminated or self.killed:
            self.returncode = -15
        else:
            self.returncode = self._final_returncode
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


class FakeFactory:
    def __init__(self, process: FakeStreamProcess) -> None:
        self.process = process
        self.calls: list[dict[str, Any]] = []

    def __call__(self, command: list[str], prompt: str) -> FakeStreamProcess:
        self.calls.append({"command": list(command), "prompt": prompt})
        return self.process


def streaming_adapter(process: FakeStreamProcess, **kwargs: Any) -> tuple[ClaudeCliAdapter, FakeFactory]:
    factory = FakeFactory(process)
    adapter = ClaudeCliAdapter(
        command="claude",
        args=["-p"],
        model="claude-test",
        timeout_seconds=5,
        process_factory=factory,
        **kwargs,
    )
    return adapter, factory


# --- the optional on_delta contract (G0 §2) -----------------------------------


def test_without_on_delta_the_blocking_path_is_unchanged() -> None:
    from tests.test_brain_cli_adapters import FakeRunner

    runner = FakeRunner(stdout="zwykła odpowiedź\n")
    adapter = ClaudeCliAdapter(runner=runner)

    response = adapter.generate(make_request())

    assert response.text == "zwykła odpowiedź"
    assert len(runner.calls) == 1
    command = runner.calls[0]["command"]
    assert "--output-format" not in command


def test_mock_adapter_accepts_and_ignores_on_delta() -> None:
    deltas: list[str] = []
    adapter = MockBrainAdapter()

    response = adapter.generate(make_request(), on_delta=deltas.append)

    assert response.text
    assert deltas == []  # mock cannot stream; degradation is silence


def test_codex_adapter_accepts_on_delta_and_stays_blocking() -> None:
    from tests.test_brain_cli_adapters import FakeRunner

    runner = FakeRunner(stdout="codex odpowiedź\n")
    adapter = CodexCliAdapter(runner=runner)
    deltas: list[str] = []

    response = adapter.generate(make_request(), on_delta=deltas.append)

    assert response.text == "codex odpowiedź"
    assert deltas == []


def test_manager_passes_on_delta_only_to_adapters_that_declare_it() -> None:
    class LegacyAdapter:
        name = "legacy"
        default_model = "legacy-model"

        def available_models(self) -> list[str]:
            return [self.default_model]

        def generate(self, request: BrainRequest):
            from jarvis.brain import BrainResponse

            return BrainResponse(text="legacy działa", model=self.default_model)

    manager = BrainManager([LegacyAdapter()], default_adapter="legacy")

    response = manager.generate(make_request(), on_delta=lambda _: None)

    assert response.text == "legacy działa"


# --- streaming path ------------------------------------------------------------


def test_streaming_emits_partial_deltas_and_result_stays_canonical() -> None:
    process = FakeStreamProcess(
        [
            line({"type": "system", "subtype": "init"}),
            partial("Pierwsze zdanie"),
            partial(" odpowiedzi. Drugie"),
            partial(" zdanie odpowiedzi."),
            assistant("Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi."),
            result("Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi."),
        ]
    )
    adapter, factory = streaming_adapter(process)
    deltas: list[str] = []

    response = adapter.generate(make_request(), on_delta=deltas.append)

    assert deltas == [
        "Pierwsze zdanie",
        " odpowiedzi. Drugie",
        " zdanie odpowiedzi.",
    ]  # the assistant repeat of streamed partials is NOT re-emitted
    assert response.text == "Pierwsze zdanie odpowiedzi. Drugie zdanie odpowiedzi."
    assert response.model == "claude-test"
    command = factory.calls[0]["command"]
    for flag in DEFAULT_STREAM_ARGS:
        assert flag in command


def test_streaming_without_partial_events_emits_assistant_messages() -> None:
    process = FakeStreamProcess(
        [
            assistant("Cała wiadomość naraz."),
            result("Cała wiadomość naraz."),
        ]
    )
    adapter, _ = streaming_adapter(process)
    deltas: list[str] = []

    response = adapter.generate(make_request(), on_delta=deltas.append)

    assert deltas == ["Cała wiadomość naraz."]
    assert response.text == "Cała wiadomość naraz."


def test_final_result_wins_over_deltas() -> None:
    # G0 §2: if deltas and the final text disagree, final text is canonical.
    process = FakeStreamProcess(
        [
            partial("Wersja z delt, która się urwała"),
            result("Wersja kanoniczna z result."),
        ]
    )
    adapter, _ = streaming_adapter(process)

    response = adapter.generate(make_request(), on_delta=lambda _: None)

    assert response.text == "Wersja kanoniczna z result."


def test_tool_call_blocks_in_result_are_parsed_not_spoken_text() -> None:
    canonical = (
        'Sprawdzę plik. <jarvis_tool_call>{"name":"file_read",'
        '"arguments":{"path":"/tmp/x"}}</jarvis_tool_call>'
    )
    process = FakeStreamProcess([result(canonical)])
    adapter, _ = streaming_adapter(process)

    response = adapter.generate(make_request(), on_delta=lambda _: None)

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "file_read"
    assert "jarvis_tool_call" not in response.text


def test_streaming_maps_result_usage_when_present() -> None:
    process = FakeStreamProcess(
        [result("Odpowiedź.", usage={"input_tokens": 11, "output_tokens": 7})]
    )
    adapter, _ = streaming_adapter(process)

    response = adapter.generate(make_request(), on_delta=lambda _: None)

    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7


def test_garbage_lines_are_ignored_best_effort() -> None:
    process = FakeStreamProcess(
        [
            "to nie jest json\n",
            partial("Prawdziwa treść."),
            result("Prawdziwa treść."),
        ]
    )
    adapter, _ = streaming_adapter(process)
    deltas: list[str] = []

    response = adapter.generate(make_request(), on_delta=deltas.append)

    assert deltas == ["Prawdziwa treść."]
    assert response.text == "Prawdziwa treść."


def test_on_delta_consumer_bug_does_not_kill_generation() -> None:
    process = FakeStreamProcess(
        [partial("Delta pierwsza."), result("Delta pierwsza.")]
    )
    adapter, _ = streaming_adapter(process)

    def explode(_: str) -> None:
        raise RuntimeError("consumer bug")

    response = adapter.generate(make_request(), on_delta=explode)

    assert response.text == "Delta pierwsza."


# --- streaming failures are loud ------------------------------------------------


def test_nonzero_exit_raises_with_stderr_preview() -> None:
    process = FakeStreamProcess(
        [partial("częściowo")], returncode=3, stderr="boom szczegóły"
    )
    adapter, _ = streaming_adapter(process)

    with pytest.raises(BrainAdapterError, match="boom"):
        adapter.generate(make_request(), on_delta=lambda _: None)


def test_missing_result_event_raises() -> None:
    process = FakeStreamProcess([partial("urwane w połowie")])
    adapter, _ = streaming_adapter(process)

    with pytest.raises(BrainAdapterError, match="result"):
        adapter.generate(make_request(), on_delta=lambda _: None)


def test_error_result_raises() -> None:
    process = FakeStreamProcess(
        [
            line(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "result": "provider error",
                }
            )
        ]
    )
    adapter, _ = streaming_adapter(process)

    with pytest.raises(BrainAdapterError):
        adapter.generate(make_request(), on_delta=lambda _: None)


def test_streaming_timeout_kills_the_subprocess() -> None:
    process = FakeStreamProcess(
        [partial("początek")], block_after=1  # then hangs until killed
    )
    factory = FakeFactory(process)
    adapter = ClaudeCliAdapter(
        command="claude",
        args=["-p"],
        model="claude-test",
        timeout_seconds=0.2,
        process_factory=factory,
    )

    started = time.monotonic()
    with pytest.raises(BrainAdapterError):
        adapter.generate(make_request(), on_delta=lambda _: None)

    assert time.monotonic() - started < 5
    assert process.killed or process.terminated


# --- barge-in leg 1: the generation registry ------------------------------------


def test_streaming_registers_a_kill_handle_and_unregisters_after() -> None:
    class SpyRegistry(GenerationRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.registered: list[str] = []
            self.unregistered: list[str] = []
            self.handles: dict[str, Any] = {}

        def register(self, turn_id: str, cancel) -> None:
            self.registered.append(turn_id)
            self.handles[turn_id] = cancel
            super().register(turn_id, cancel)

        def unregister(self, turn_id: str) -> None:
            self.unregistered.append(turn_id)
            super().unregister(turn_id)

    registry = SpyRegistry()
    process = FakeStreamProcess([result("Krótka odpowiedź.")])
    adapter, _ = streaming_adapter(process, generation_registry=registry)

    adapter.generate(make_request(), on_delta=lambda _: None)

    assert registry.registered == ["turn-stream-1"]
    assert registry.unregistered == ["turn-stream-1"]
    assert registry.active_count() == 0
    # The registered handle really kills the subprocess (leg 1 of §7).
    registry.handles["turn-stream-1"]()
    assert process.terminated or process.killed


def test_barge_in_cancel_mid_stream_fails_the_generation() -> None:
    registry = GenerationRegistry()
    process = FakeStreamProcess(
        [partial("początek zdania")], block_after=1  # hangs until cancelled
    )
    adapter, _ = streaming_adapter(process, generation_registry=registry)
    errors: list[Exception] = []
    done = threading.Event()

    def run() -> None:
        try:
            adapter.generate(make_request(), on_delta=lambda _: None)
        except BrainAdapterError as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and registry.active_count() == 0:
        time.sleep(0.01)
    assert registry.active_count() == 1

    assert len(registry.cancel_all()) == 1
    assert done.wait(timeout=5)
    # A killed generation raises (never a fake success) and the registry is
    # cleaned up; the distinct cancelled-vs-failed classification is asserted in
    # test_barge_in_cancel_raises_generation_cancelled_not_failure.
    assert len(errors) == 1
    assert registry.active_count() == 0


def test_streaming_writes_stdin_on_its_own_thread_without_deadlock() -> None:
    # FIX-07 HIGH: the full prompt was written to the child's stdin BEFORE the
    # watchdog was armed and stdout/stderr drained. A large prompt fills the
    # pipe → the parent blocks on write, the child blocks on unread stdout →
    # deadlock with no timeout. The write must run on its own thread, concurrent
    # with the stdout drain. This fake's stdin.write blocks until stdout is read.
    class BlockingStdinProcess:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines
            self.returncode: int | None = None
            self.stderr = io.StringIO("")
            self.pid = 0
            self.terminated = 0
            self.killed = 0
            self._stdout_reading = threading.Event()
            self.stdin_written: list[str] = []
            outer = self

            class _Stdin:
                def write(self, data: str) -> None:
                    # A real pipe blocks here until the child reads stdin, and a
                    # CLI only reads while it produces stdout. Writing before the
                    # stdout drain starts would therefore deadlock.
                    if not outer._stdout_reading.wait(timeout=5):
                        raise AssertionError("stdin write deadlocked: stdout never drained")
                    outer.stdin_written.append(data)

                def close(self) -> None:
                    pass

            self.stdin = _Stdin()

        @property
        def stdout(self):
            self._stdout_reading.set()
            yield from self._lines

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated += 1

        def kill(self) -> None:
            self.killed += 1

    proc = BlockingStdinProcess([result("Odpowiedź po zapisie stdin.")])
    adapter, _ = streaming_adapter(proc)

    response = adapter.generate(make_request(), on_delta=lambda _: None)

    assert response.text == "Odpowiedź po zapisie stdin."
    # The prompt was written to the child's stdin (concurrently — the blocking
    # fake would have raised otherwise), not swallowed by the factory.
    assert proc.stdin_written
    assert "Opowiedz o pogodzie" in proc.stdin_written[0]


def test_barge_in_cancel_raises_generation_cancelled_not_failure() -> None:
    # Operator-priority fix (FIX-09): a cancel (barge-in leg 1) that kills the
    # CLI must be DISTINGUISHABLE from a real failure. rc=143/-15 after our own
    # cancel handle fired is a cancellation, so the turn is CANCELLED, not FAILED.
    from jarvis.brain.base import BrainGenerationCancelled

    registry = GenerationRegistry()
    process = FakeStreamProcess(
        [partial("początek zdania")], block_after=1  # hangs until cancelled
    )
    adapter, _ = streaming_adapter(process, generation_registry=registry)
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
    # The distinct subclass is what lets the orchestrator mark CANCELLED, not FAILED.
    assert isinstance(captured[0], BrainGenerationCancelled)


def test_genuine_nonzero_exit_stays_a_failure_not_a_cancellation() -> None:
    # A real crash (nonzero exit we did NOT cause by cancelling) is still a
    # plain failure — never misreported as a cancellation.
    from jarvis.brain.base import BrainGenerationCancelled

    process = FakeStreamProcess([partial("częściowo")], returncode=3, stderr="boom")
    adapter, _ = streaming_adapter(process)

    with pytest.raises(BrainAdapterError) as exc_info:
        adapter.generate(make_request(), on_delta=lambda _: None)
    assert not isinstance(exc_info.value, BrainGenerationCancelled)


def test_blocking_path_never_touches_the_registry() -> None:
    from tests.test_brain_cli_adapters import FakeRunner

    registry = GenerationRegistry()
    adapter = ClaudeCliAdapter(
        runner=FakeRunner(stdout="ok\n"), generation_registry=registry
    )

    adapter.generate(make_request())

    assert registry.active_count() == 0
