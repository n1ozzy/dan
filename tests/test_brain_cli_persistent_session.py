"""Hermetic tests for the single persistent Claude CLI execution session."""

from __future__ import annotations

import json
import os
import queue
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from dan.brain.base import BrainMessage, BrainRequest
from dan.brain.base import BrainAdapterError, BrainGenerationCancelled
from dan.brain.claude_cli_adapter import ClaudeCliAdapter
from dan.brain.manager import BrainManager
from dan.voice.cancellation import GenerationRegistry


def stream_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def result_line(
    text: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 2,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> str:
    return stream_line(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "output_tokens": output_tokens,
            },
        }
    )


def error_result_line(text: str = "resume state corrupt") -> str:
    return stream_line(
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": text,
        }
    )


class QueueReader:
    def __init__(self) -> None:
        self.items: queue.Queue[str] = queue.Queue()

    def readline(self) -> str:
        return self.items.get(timeout=5)

    def read(self) -> str:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.items.get_nowait())
            except queue.Empty:
                return "".join(chunks)

    def push(self, value: str) -> None:
        self.items.put(value)


class ScriptedStdin:
    def __init__(self, process: "FakePersistentProcess") -> None:
        self.process = process
        self.writes: list[str] = []
        self.closed = False
        self.write_event = threading.Event()

    def write(self, value: str) -> int:
        self.writes.append(value)
        self.write_event.set()
        self.process.accept_message(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakePersistentProcess:
    def __init__(self, generations: list[list[str]]) -> None:
        self.generations = list(generations)
        self.stdout = QueueReader()
        self.stderr = QueueReader()
        self.stdin = ScriptedStdin(self)
        self.pid = 0
        self.returncode: int | None = None
        self.terminated = 0
        self.killed = 0

    def accept_message(self, _value: str) -> None:
        if not self.generations:
            raise AssertionError("unexpected persistent-session message")
        for line in self.generations.pop(0):
            self.stdout.push(line)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = -15
        self.stdout.push("")
        self.stderr.push("")

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9
        self.stdout.push("")
        self.stderr.push("")

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class RecordingFactory:
    def __init__(self, *processes: FakePersistentProcess) -> None:
        self.processes = list(processes)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> FakePersistentProcess:
        self.commands.append(list(command))
        if not self.processes:
            raise AssertionError("unexpected persistent Claude spawn")
        return self.processes.pop(0)


def request(
    text: str,
    *,
    turn_id: str,
    persona: str = "DAN_CANON_VERSION: 1\nKANONICZNY DAN — dokładnie i świeżo.",
) -> BrainRequest:
    return BrainRequest(
        turn_id=turn_id,
        conversation_id="dan-conversation",
        input_text=text,
        context_messages=[
            BrainMessage(
                role="system",
                content=persona,
                metadata={"kind": "persona"},
            ),
            BrainMessage(role="user", content="poprzedni kontekst"),
        ],
    )


def test_initial_and_restored_hash_change_system_prompts_start_with_fresh_canon(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "fresh-canon.json"
    old_canon = "DAN OLD — byte for byte\nbez sanitizera"
    new_canon = "DAN NEW — byte for byte\nzero ugrzeczniania"
    initial_process = FakePersistentProcess([[result_line("first")]])
    initial_factory = RecordingFactory(initial_process)
    initial_adapter = ClaudeCliAdapter(
        process_factory=initial_factory,
        state_path=state_path,
    )
    initial_adapter.generate(request("first", turn_id="turn-first", persona=old_canon))
    initial_adapter.close()

    resumed_process = FakePersistentProcess(
        [
            [result_line("checkpoint przyjęty")],
            [result_line("second")],
        ]
    )
    resumed_factory = RecordingFactory(resumed_process)
    resumed_adapter = ClaudeCliAdapter(
        process_factory=resumed_factory,
        state_path=state_path,
    )
    try:
        resumed_adapter.generate(
            request("second", turn_id="turn-second", persona=new_canon)
        )
    finally:
        resumed_adapter.close()

    initial_command = initial_factory.commands[0]
    initial_prompt = initial_command[initial_command.index("--system-prompt") + 1]
    resumed_command = resumed_factory.commands[0]
    resumed_prompt = resumed_command[resumed_command.index("--system-prompt") + 1]
    assert initial_prompt.startswith(old_canon)
    assert resumed_prompt.startswith(new_canon)
    assert old_canon not in resumed_prompt
    assert resumed_prompt.index(new_canon) == 0
    assert resumed_prompt.index("Runtime rules:") > len(new_canon)
    assert "--resume" not in resumed_command
    assert "--append-system-prompt" not in resumed_command
    old_session_id = initial_command[initial_command.index("--session-id") + 1]
    new_session_id = resumed_command[resumed_command.index("--session-id") + 1]
    assert new_session_id != old_session_id
    assert len(resumed_process.stdin.writes) == 2
    checkpoint = json.loads(resumed_process.stdin.writes[0])
    assert old_canon not in checkpoint["message"]["content"][0]["text"]
    current = json.loads(resumed_process.stdin.writes[1])
    assert current["message"]["content"][0]["text"] == "second"


def test_persistent_adapter_spawns_once_and_sends_only_incremental_second_turn(
    tmp_path: Path,
) -> None:
    process = FakePersistentProcess(
        [
            [result_line("pierwsza")],
            [result_line("druga")],
        ]
    )
    factory = RecordingFactory(process)
    adapter = ClaudeCliAdapter(
        command="claude",
        process_factory=factory,
        state_path=tmp_path / "brain-session.json",
        context_window_tokens=1000,
    )

    first = adapter.generate(request("pierwsza wiadomość", turn_id="turn-1"))
    second = adapter.generate(request("druga wiadomość", turn_id="turn-2"))

    assert first.text == "pierwsza"
    assert second.text == "druga"
    assert len(factory.commands) == 1
    command = factory.commands[0]
    assert command.count("--session-id") == 1
    assert "--input-format" in command
    assert command[command.index("--input-format") + 1] == "stream-json"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert len(process.stdin.writes) == 2
    bootstrap = json.loads(process.stdin.writes[0])
    incremental = json.loads(process.stdin.writes[1])
    bootstrap_text = bootstrap["message"]["content"][0]["text"]
    incremental_text = incremental["message"]["content"][0]["text"]
    assert "poprzedni kontekst" in bootstrap_text
    assert "pierwsza wiadomość" in bootstrap_text
    assert incremental_text == "druga wiadomość"
    assert "KANONICZNY DAN" not in incremental_text


def test_live_persistent_session_rebuilds_when_persona_hash_changes(
    tmp_path: Path,
) -> None:
    old_canon = "DAN_CANON_VERSION: 1\nSTARY KANON"
    new_canon = "DAN_CANON_VERSION: 1\nNOWY KANON OZZY'EGO"
    old_process = FakePersistentProcess(
        [
            [result_line("pierwsza")],
            [result_line("stara sesja nie może odpowiedzieć")],
        ]
    )
    rebuilt_process = FakePersistentProcess(
        [
            [result_line("checkpoint przyjęty")],
            [result_line("odpowiedź z nowego kanonu")],
        ]
    )
    factory = RecordingFactory(old_process, rebuilt_process)
    adapter = ClaudeCliAdapter(
        command="claude",
        process_factory=factory,
        state_path=tmp_path / "brain-session.json",
        context_window_tokens=1000,
    )

    first = adapter.generate(
        request("pierwsza wiadomość", turn_id="turn-1", persona=old_canon)
    )
    second = adapter.generate(
        request("druga wiadomość", turn_id="turn-2", persona=new_canon)
    )

    assert first.text == "pierwsza"
    assert second.text == "odpowiedź z nowego kanonu"
    assert len(factory.commands) == 2
    first_command, rebuilt_command = factory.commands
    old_session_id = first_command[first_command.index("--session-id") + 1]
    new_session_id = rebuilt_command[rebuilt_command.index("--session-id") + 1]
    assert new_session_id != old_session_id
    assert "--resume" not in rebuilt_command
    assert "--append-system-prompt" not in rebuilt_command
    rebuilt_system = rebuilt_command[rebuilt_command.index("--system-prompt") + 1]
    assert rebuilt_system.startswith(new_canon)
    assert old_canon not in rebuilt_system
    assert old_process.terminated == 1
    assert len(old_process.stdin.writes) == 1
    assert len(rebuilt_process.stdin.writes) == 2
    checkpoint = json.loads(rebuilt_process.stdin.writes[0])
    checkpoint_text = checkpoint["message"]["content"][0]["text"]
    assert old_canon not in checkpoint_text
    current = json.loads(rebuilt_process.stdin.writes[1])
    assert current["message"]["content"][0]["text"] == "druga wiadomość"

    adapter.close()


def test_process_eof_resumes_same_session_and_retries_current_message_once(
    tmp_path: Path,
) -> None:
    crashed = FakePersistentProcess([[""]])
    resumed = FakePersistentProcess([[result_line("odbudowana")]])
    factory = RecordingFactory(crashed, resumed)
    adapter = ClaudeCliAdapter(
        command="claude",
        process_factory=factory,
        state_path=tmp_path / "brain-session.json",
        context_window_tokens=1000,
    )

    try:
        response = adapter.generate(request("wiadomość po awarii", turn_id="turn-crash"))
    finally:
        adapter.close()

    assert response.text == "odbudowana"
    assert len(factory.commands) == 2
    first, second = factory.commands
    session_id = first[first.index("--session-id") + 1]
    assert second[second.index("--resume") + 1] == session_id
    assert "--append-system-prompt" in second
    resumed_system = second[second.index("--append-system-prompt") + 1]
    assert "DAN_CANON_VERSION: 1" in resumed_system
    assert "KANONICZNY DAN — dokładnie i świeżo." in resumed_system
    assert len(crashed.stdin.writes) == 1
    assert len(resumed.stdin.writes) == 1


def test_corrupt_resume_mints_new_session_and_rebuilds_from_full_request(
    tmp_path: Path,
) -> None:
    crashed = FakePersistentProcess([[""]])
    corrupt_resume = FakePersistentProcess([[error_result_line()]])
    rebuilt = FakePersistentProcess([[result_line("świeża sesja")]])
    factory = RecordingFactory(crashed, corrupt_resume, rebuilt)
    adapter = ClaudeCliAdapter(
        command="claude",
        process_factory=factory,
        state_path=tmp_path / "brain-session.json",
        context_window_tokens=1000,
    )

    try:
        response = adapter.generate(request("nie zgub tej tury", turn_id="turn-rebuild"))
        snapshot = adapter.session_snapshot()
    finally:
        adapter.close()

    assert response.text == "świeża sesja"
    assert len(factory.commands) == 3
    first, resumed_command, rebuilt_command = factory.commands
    old_session_id = first[first.index("--session-id") + 1]
    assert resumed_command[resumed_command.index("--resume") + 1] == old_session_id
    new_session_id = rebuilt_command[rebuilt_command.index("--session-id") + 1]
    assert new_session_id != old_session_id
    assert len(rebuilt.stdin.writes) == 1
    rebuilt_payload = json.loads(rebuilt.stdin.writes[0])
    rebuilt_text = rebuilt_payload["message"]["content"][0]["text"]
    assert "poprzedni kontekst" in rebuilt_text
    assert "nie zgub tej tury" in rebuilt_text
    assert snapshot["session_id"] == new_session_id
    assert snapshot["last_action"] == "rebuilt"


def test_checkpoint_is_mode_0600_and_new_adapter_resumes_durable_session(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "brain-session.json"
    first_process = FakePersistentProcess([[result_line("pierwsza", input_tokens=100)]])
    first_factory = RecordingFactory(first_process)
    first_adapter = ClaudeCliAdapter(
        command="claude",
        process_factory=first_factory,
        state_path=state_path,
        context_window_tokens=1000,
    )
    first_adapter.generate(request("pierwsza", turn_id="turn-one"))
    first_snapshot = first_adapter.session_snapshot()
    first_adapter.close()

    resumed_process = FakePersistentProcess([[result_line("druga", input_tokens=200)]])
    resumed_factory = RecordingFactory(resumed_process)
    second_adapter = ClaudeCliAdapter(
        command="claude",
        process_factory=resumed_factory,
        state_path=state_path,
        context_window_tokens=1000,
    )
    try:
        response = second_adapter.generate(request("druga", turn_id="turn-two"))
    finally:
        second_adapter.close()

    assert response.text == "druga"
    assert state_path.stat().st_mode & 0o777 == 0o600
    command = resumed_factory.commands[0]
    assert command[command.index("--resume") + 1] == first_snapshot["session_id"]
    payload = json.loads(resumed_process.stdin.writes[0])
    assert payload["message"]["content"][0]["text"] == "druga"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["generation"] == 2
    assert persisted["conversation_id"] == "dan-conversation"
    assert persisted["checkpoint_prompt"]


def test_state_persistence_fsyncs_file_before_replace_and_parent_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracking_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        events.append("fsync_dir" if stat.S_ISDIR(mode) else "fsync_file")
        real_fsync(fd)

    def tracking_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    monkeypatch.setattr(os, "replace", tracking_replace)
    state_path = tmp_path / "durable" / "session.json"
    process = FakePersistentProcess([[result_line("saved")]])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(process),
        state_path=state_path,
    )
    try:
        adapter.generate(request("save", turn_id="turn-save"))
    finally:
        adapter.close()

    assert events == ["fsync_file", "replace", "fsync_dir"]
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_rejected_durable_resume_mints_new_session_and_rebuilds_checkpoint(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "rejected-durable-resume.json"
    initial = FakePersistentProcess([[result_line("old assistant answer")]])
    initial_adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(initial),
        state_path=state_path,
    )
    initial_adapter.generate(request("old user input", turn_id="turn-initial"))
    old_session_id = initial_adapter.session_snapshot()["session_id"]
    initial_adapter.close()

    rejected = FakePersistentProcess([[error_result_line()]])
    rebuilt = FakePersistentProcess(
        [
            [result_line("bootstrap acknowledgement")],
            [result_line("current response")],
        ]
    )
    factory = RecordingFactory(rejected, rebuilt)
    adapter = ClaudeCliAdapter(process_factory=factory, state_path=state_path)
    try:
        response = adapter.generate(request("after restart", turn_id="turn-restart"))
        new_session_id = adapter.session_snapshot()["session_id"]
    finally:
        adapter.close()

    assert response.text == "current response"
    assert factory.commands[0][factory.commands[0].index("--resume") + 1] == old_session_id
    assert factory.commands[1][factory.commands[1].index("--session-id") + 1] == new_session_id
    assert new_session_id != old_session_id
    assert len(rebuilt.stdin.writes) == 2
    checkpoint_payload = json.loads(rebuilt.stdin.writes[0])
    current_payload = json.loads(rebuilt.stdin.writes[1])
    checkpoint_text = checkpoint_payload["message"]["content"][0]["text"]
    current_text = current_payload["message"]["content"][0]["text"]
    assert "old user input" in checkpoint_text
    assert "old assistant answer" in checkpoint_text
    assert "after restart" not in checkpoint_text
    assert current_text == "after restart"


def test_context_policy_checkpoints_at_exact_70_percent_not_below(
    tmp_path: Path,
) -> None:
    actions: dict[int, str] = {}
    for tokens in (699, 700):
        process = FakePersistentProcess([[result_line("ok", input_tokens=tokens)]])
        adapter = ClaudeCliAdapter(
            command="claude",
            process_factory=RecordingFactory(process),
            state_path=tmp_path / f"session-{tokens}.json",
            context_window_tokens=1000,
        )
        try:
            adapter.generate(request(f"tokens {tokens}", turn_id=f"turn-{tokens}"))
            actions[tokens] = adapter.session_snapshot()["last_action"]
        finally:
            adapter.close()

    assert actions[699] != "checkpoint"
    assert actions[700] == "checkpoint"


def test_cache_tokens_count_toward_effective_usage_and_exact_threshold_actions(
    tmp_path: Path,
) -> None:
    cases = {
        700: ("checkpoint", 1),
        800: ("compact", 2),
        900: ("recycle_pending", 2),
    }
    for effective_tokens, (expected_action, expected_writes) in cases.items():
        generations = [
            [
                result_line(
                    "normal",
                    input_tokens=100,
                    cache_read_input_tokens=effective_tokens - 200,
                    cache_creation_input_tokens=100,
                    output_tokens=7,
                )
            ]
        ]
        if effective_tokens >= 800:
            generations.append([result_line("compacted", input_tokens=600)])
        process = FakePersistentProcess(generations)
        adapter = ClaudeCliAdapter(
            process_factory=RecordingFactory(process),
            state_path=tmp_path / f"cache-{effective_tokens}.json",
            context_window_tokens=1000,
        )
        try:
            response = adapter.generate(
                request(f"cache {effective_tokens}", turn_id=f"turn-cache-{effective_tokens}")
            )
            snapshot = adapter.session_snapshot()
        finally:
            adapter.close()

        assert response.usage.input_tokens == effective_tokens
        assert response.usage.output_tokens == 7
        assert response.usage.total_tokens == effective_tokens + 7
        assert response.usage.cache_read_input_tokens == effective_tokens - 200
        assert response.usage.cache_creation_input_tokens == 100
        assert snapshot["last_action"] == expected_action
        assert len(process.stdin.writes) == expected_writes


def test_context_policy_compacts_once_at_exact_80_percent_not_below(
    tmp_path: Path,
) -> None:
    below = FakePersistentProcess([[result_line("below", input_tokens=799)]])
    at = FakePersistentProcess(
        [
            [result_line("at", input_tokens=800)],
            [result_line("compacted", input_tokens=600)],
        ]
    )
    below_adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(below),
        state_path=tmp_path / "below-80.json",
        context_window_tokens=1000,
    )
    at_adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(at),
        state_path=tmp_path / "at-80.json",
        context_window_tokens=1000,
    )
    try:
        below_adapter.generate(request("below", turn_id="turn-below-80"))
        at_adapter.generate(request("at", turn_id="turn-at-80"))
        at_snapshot = at_adapter.session_snapshot()
    finally:
        below_adapter.close()
        at_adapter.close()

    assert len(below.stdin.writes) == 1
    assert len(at.stdin.writes) == 2
    compact_message = json.loads(at.stdin.writes[1])
    assert compact_message["message"]["content"][0]["text"] == "/compact"
    assert at_snapshot["last_action"] == "compact"


def test_context_policy_recycles_before_next_normal_message_at_exact_90_percent(
    tmp_path: Path,
) -> None:
    saturated = FakePersistentProcess(
        [
            [result_line("first", input_tokens=900)],
            [result_line("compacted", input_tokens=700)],
        ]
    )
    recycled = FakePersistentProcess([[result_line("second", input_tokens=100)]])
    factory = RecordingFactory(saturated, recycled)
    adapter = ClaudeCliAdapter(
        process_factory=factory,
        state_path=tmp_path / "at-90.json",
        context_window_tokens=1000,
    )
    try:
        adapter.generate(request("first", turn_id="turn-first-90"))
        pending = adapter.session_snapshot()
        second = adapter.generate(request("second", turn_id="turn-second-90"))
    finally:
        adapter.close()

    assert pending["last_action"] == "recycle_pending"
    assert second.text == "second"
    assert len(factory.commands) == 2
    first_command, second_command = factory.commands
    session_id = first_command[first_command.index("--session-id") + 1]
    assert second_command[second_command.index("--resume") + 1] == session_id
    assert len(saturated.stdin.writes) == 2
    assert len(recycled.stdin.writes) == 1
    recycled_payload = json.loads(recycled.stdin.writes[0])
    assert recycled_payload["message"]["content"][0]["text"] == "second"


def test_compact_threshold_is_idempotent_across_adapter_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "compact-once.json"
    first_process = FakePersistentProcess(
        [
            [result_line("first", input_tokens=800)],
            [result_line("compacted", input_tokens=850)],
        ]
    )
    first_adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(first_process),
        state_path=state_path,
        context_window_tokens=1000,
    )
    first_adapter.generate(request("first", turn_id="turn-first"))
    first_adapter.close()

    resumed_process = FakePersistentProcess([[result_line("second", input_tokens=850)]])
    resumed_adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(resumed_process),
        state_path=state_path,
        context_window_tokens=1000,
    )
    try:
        response = resumed_adapter.generate(request("second", turn_id="turn-second"))
    finally:
        resumed_adapter.close()

    assert response.text == "second"
    assert len(resumed_process.stdin.writes) == 1


def test_brain_manager_close_terminates_persistent_adapter_process(tmp_path: Path) -> None:
    process = FakePersistentProcess([[result_line("ok")]])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(process),
        state_path=tmp_path / "manager-close.json",
    )
    manager = BrainManager([adapter], default_adapter="claude_cli")
    manager.generate(request("start", turn_id="turn-close"))

    manager.close()

    assert process.stdin.closed is True
    assert process.terminated == 1


def test_persistent_generation_unregisters_the_exact_registration_token(
    tmp_path: Path,
) -> None:
    class SpyRegistry(GenerationRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.registrations: list[Any] = []
            self.unregistered: list[Any] = []

        def register(self, turn_id: str, cancel) -> Any:
            registration = super().register(turn_id, cancel)
            self.registrations.append(registration)
            return registration

        def unregister(self, registration: Any) -> None:
            self.unregistered.append(registration)
            super().unregister(registration)

    registry = SpyRegistry()
    process = FakePersistentProcess([[result_line("ok")]])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(process),
        generation_registry=registry,
        state_path=tmp_path / "exact-registration-token.json",
    )
    try:
        adapter.generate(request("start", turn_id="turn-token"))
    finally:
        adapter.close()

    assert len(registry.registrations) == 1
    assert registry.unregistered == registry.registrations
    assert registry.unregistered[0] is registry.registrations[0]


def test_close_signals_active_generation_before_waiting_for_generation_lock(
    tmp_path: Path,
) -> None:
    registry = GenerationRegistry()
    process = FakePersistentProcess([[]])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(process),
        generation_registry=registry,
        state_path=tmp_path / "active-close.json",
        timeout_seconds=5,
    )
    generation_errors: list[BaseException] = []
    close_done = threading.Event()

    def generate() -> None:
        try:
            adapter.generate(request("blocks", turn_id="turn-active-close"))
        except BaseException as exc:  # noqa: BLE001 - asserted below
            generation_errors.append(exc)

    worker = threading.Thread(target=generate, daemon=True)
    worker.start()
    assert process.stdin.write_event.wait(timeout=1)

    closer = threading.Thread(
        target=lambda: (adapter.close(), close_done.set()),
        daemon=True,
    )
    started = time.monotonic()
    closer.start()
    closed_immediately = close_done.wait(timeout=0.2)
    elapsed = time.monotonic() - started
    if not closed_immediately:
        registry.cancel_all()
    closer.join(timeout=1)
    worker.join(timeout=1)

    assert closed_immediately is True
    assert elapsed < 0.2
    assert process.terminated >= 1
    assert len(generation_errors) == 1
    assert isinstance(generation_errors[0], BrainGenerationCancelled)
    assert adapter._stdout_thread is None or not adapter._stdout_thread.is_alive()
    assert adapter._stderr_thread is None or not adapter._stderr_thread.is_alive()


def test_close_signals_process_spawned_before_generation_cancel_registration(
    tmp_path: Path,
) -> None:
    process = FakePersistentProcess([[]])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(process),
        state_path=tmp_path / "spawn-close-race.json",
        timeout_seconds=5,
    )
    phase_reached = threading.Event()
    release_start = threading.Event()

    def block_before_send(_process: FakePersistentProcess):
        phase_reached.set()
        assert release_start.wait(timeout=1)
        return None

    adapter._start_persistent_stderr_reader = block_before_send  # type: ignore[method-assign]
    worker = threading.Thread(
        target=lambda: _capture_generation_error(
            adapter,
            request("race", turn_id="turn-spawn-race"),
            [],
        ),
        daemon=True,
    )
    worker.start()
    assert phase_reached.wait(timeout=1)
    closer = threading.Thread(target=adapter.close, daemon=True)
    closer.start()
    signalled_before_generation_unlock = _wait_until(lambda: process.terminated > 0)
    release_start.set()
    closer.join(timeout=1)
    worker.join(timeout=1)

    assert signalled_before_generation_unlock is True
    assert not closer.is_alive()


def test_close_returns_while_process_factory_is_blocked_then_reaps_returned_process(
    tmp_path: Path,
) -> None:
    entered_factory = threading.Event()
    release_factory = threading.Event()
    close_done = threading.Event()
    process = FakePersistentProcess([[""]])

    class BlockingFactory:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _command: list[str]) -> FakePersistentProcess:
            self.calls += 1
            entered_factory.set()
            assert release_factory.wait(timeout=1)
            if self.calls > 1:
                raise AssertionError("closed generation attempted provider restart")
            return process

    factory = BlockingFactory()
    adapter = ClaudeCliAdapter(
        process_factory=factory,
        state_path=tmp_path / "factory-close-race.json",
        timeout_seconds=5,
    )
    errors: list[BaseException] = []
    worker = threading.Thread(
        target=lambda: _capture_generation_error(
            adapter,
            request("factory race", turn_id="turn-factory-race"),
            errors,
        ),
        daemon=True,
    )
    worker.start()
    assert entered_factory.wait(timeout=1)
    closer = threading.Thread(
        target=lambda: (adapter.close(), close_done.set()),
        daemon=True,
    )
    closer.start()
    close_returned_before_factory = close_done.wait(timeout=0.2)
    release_factory.set()
    worker.join(timeout=1)
    closer.join(timeout=1)

    assert close_returned_before_factory is True
    assert not worker.is_alive()
    assert factory.calls == 1
    assert process.terminated >= 1
    assert len(errors) == 1
    assert isinstance(errors[0], BrainGenerationCancelled)


def test_close_kills_and_reaps_process_that_ignores_terminate(tmp_path: Path) -> None:
    class StubbornProcess(FakePersistentProcess):
        def __init__(self) -> None:
            super().__init__([[result_line("done")]])
            self.wait_calls = 0
            self.reaped = False

        def terminate(self) -> None:
            self.terminated += 1

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.killed == 0:
                raise TimeoutError("ignores terminate")
            self.reaped = True
            self.returncode = -9
            return self.returncode

    process = StubbornProcess()
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(process),
        state_path=tmp_path / "stubborn-reap.json",
    )
    adapter.generate(request("start", turn_id="turn-stubborn"))

    adapter.close()

    assert process.terminated >= 1
    assert process.killed == 1
    assert process.wait_calls == 2
    assert process.reaped is True


def test_generation_registry_cancel_stops_active_persistent_generation_without_resume(
    tmp_path: Path,
) -> None:
    registry = GenerationRegistry()
    process = FakePersistentProcess([[]])
    factory = RecordingFactory(process)
    adapter = ClaudeCliAdapter(
        process_factory=factory,
        generation_registry=registry,
        state_path=tmp_path / "registry-cancel.json",
        timeout_seconds=5,
    )
    errors: list[BaseException] = []

    worker = threading.Thread(
        target=lambda: _capture_generation_error(
            adapter,
            request("cancel me", turn_id="turn-registry-cancel"),
            errors,
        ),
        daemon=True,
    )
    worker.start()
    assert process.stdin.write_event.wait(timeout=1)
    assert registry.cancel_all() == ["turn-registry-cancel"]
    worker.join(timeout=1)
    adapter.close()

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], BrainGenerationCancelled)
    assert len(factory.commands) == 1


def test_persistent_timeout_kills_hung_process_then_resumes_once(tmp_path: Path) -> None:
    hung = FakePersistentProcess([[]])
    resumed = FakePersistentProcess([[result_line("after timeout")]])
    factory = RecordingFactory(hung, resumed)
    adapter = ClaudeCliAdapter(
        process_factory=factory,
        state_path=tmp_path / "timeout-resume.json",
        timeout_seconds=0.02,
    )
    try:
        response = adapter.generate(request("timeout", turn_id="turn-timeout"))
    finally:
        adapter.close()

    assert response.text == "after timeout"
    assert hung.killed == 1
    assert len(factory.commands) == 2


def test_terminal_transport_error_waits_for_stderr_drain_before_reporting(
    tmp_path: Path,
) -> None:
    first = FakePersistentProcess([[""]])
    rejected_resume = FakePersistentProcess([[error_result_line()]])
    terminal = FakePersistentProcess([[""]])
    release_stderr = threading.Event()

    class ReleasingStdout:
        def readline(self) -> str:
            release_stderr.set()
            return ""

        def push(self, _value: str) -> None:
            return None

    class DelayedStderr:
        def __init__(self) -> None:
            self.calls = 0

        def readline(self) -> str:
            assert release_stderr.wait(timeout=1)
            if self.calls == 0:
                self.calls += 1
                time.sleep(0.05)
                return "fatal resume stderr detail\n"
            return ""

    terminal.stdout = ReleasingStdout()
    terminal.stderr = DelayedStderr()
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(first, rejected_resume, terminal),
        state_path=tmp_path / "stderr-drain.json",
        timeout_seconds=1,
    )
    with pytest.raises(BrainAdapterError, match="fatal resume stderr detail"):
        adapter.generate(request("fails", turn_id="turn-stderr"))
    adapter.close()


def _capture_generation_error(
    adapter: ClaudeCliAdapter,
    brain_request: BrainRequest,
    errors: list[BaseException],
) -> None:
    try:
        adapter.generate(brain_request)
    except BaseException as exc:  # noqa: BLE001 - test captures exact type
        errors.append(exc)


def _wait_until(predicate, timeout: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def test_compact_transport_failure_preserves_completed_response_and_recycles_next_turn(
    tmp_path: Path,
) -> None:
    first = FakePersistentProcess(
        [
            [result_line("completed user response", input_tokens=800)],
            [""],
        ]
    )
    recycled = FakePersistentProcess([[result_line("next response", input_tokens=100)]])
    factory = RecordingFactory(first, recycled)
    adapter = ClaudeCliAdapter(
        process_factory=factory,
        state_path=tmp_path / "compact-failure.json",
        context_window_tokens=1000,
    )
    try:
        completed = adapter.generate(request("first input", turn_id="turn-first"))
        pending = adapter.session_snapshot()
        next_response = adapter.generate(request("second input", turn_id="turn-second"))
    finally:
        adapter.close()

    assert completed.text == "completed user response"
    assert pending["last_action"] == "compact_failed_recycle_pending"
    assert next_response.text == "next response"
    assert len(first.stdin.writes) == 2
    assert json.loads(first.stdin.writes[0])["message"]["content"][0]["text"].endswith(
        "Respond now as DAN.\n"
    )
    assert json.loads(first.stdin.writes[1])["message"]["content"][0]["text"] == "/compact"
    assert len(recycled.stdin.writes) == 1
    assert json.loads(recycled.stdin.writes[0])["message"]["content"][0]["text"] == "second input"


def test_compact_error_result_preserves_completed_response_and_recycles_next_turn(
    tmp_path: Path,
) -> None:
    first = FakePersistentProcess(
        [
            [result_line("completed before compact error", input_tokens=800)],
            [error_result_line("compact rejected")],
        ]
    )
    recycled = FakePersistentProcess([[result_line("next after compact error")]])
    factory = RecordingFactory(first, recycled)
    adapter = ClaudeCliAdapter(
        process_factory=factory,
        state_path=tmp_path / "compact-error-result.json",
        context_window_tokens=1000,
    )
    try:
        completed = adapter.generate(request("first input", turn_id="turn-first"))
        pending = adapter.session_snapshot()
        next_response = adapter.generate(request("second input", turn_id="turn-second"))
    finally:
        adapter.close()

    assert completed.text == "completed before compact error"
    assert pending["last_action"] == "compact_failed_recycle_pending"
    assert next_response.text == "next after compact error"
    assert len(first.stdin.writes) == 2
    assert json.loads(first.stdin.writes[1])["message"]["content"][0]["text"] == "/compact"
    assert len(recycled.stdin.writes) == 1
    assert json.loads(recycled.stdin.writes[0])["message"]["content"][0]["text"] == "second input"


def test_brain_manager_start_reopens_adapter_for_lazy_session_restart(
    tmp_path: Path,
) -> None:
    first = FakePersistentProcess([[result_line("first")]])
    resumed = FakePersistentProcess([[result_line("second")]])
    adapter = ClaudeCliAdapter(
        process_factory=RecordingFactory(first, resumed),
        state_path=tmp_path / "manager-restart.json",
    )
    manager = BrainManager([adapter], default_adapter="claude_cli")
    manager.generate(request("first", turn_id="turn-first"))
    manager.close()

    manager.start()
    response = manager.generate(request("second", turn_id="turn-second"))
    manager.close()

    assert response.text == "second"
