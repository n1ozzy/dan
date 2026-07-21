"""Degenerate tool-loop detection for the direct tool rounds (ToolLoopGuard).

Adapted from OpenJarvis (Apache 2.0) agents/loop_guard.py: identical-call
hashing and ping-pong pattern detection, with warn-before-block semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.turns.loop_guard import LoopVerdict, ToolLoopGuard
from dan.turns.models import TurnStatus
from dan.turns.orchestrator import TurnOrchestratorError
from tests.test_model_tool_permission_policy import table_count
from tests.test_tool_result_continuation import (
    RecordingContinuationTool,
    SequenceBrainAdapter,
    make_app,
    model_tool_response,
    turn_row,
)


def make_guard(**kwargs: object) -> ToolLoopGuard:
    return ToolLoopGuard(**kwargs)


def test_distinct_calls_pass_without_warnings() -> None:
    guard = make_guard()
    for index in range(6):
        verdict = guard.check_call("file_tool", {"path": f"/tmp/{index}.txt"})
        assert verdict == LoopVerdict()


def test_identical_call_warns_then_blocks() -> None:
    guard = make_guard(max_identical_calls=3)
    for _ in range(3):
        assert guard.check_call("shell_tool", {"cmd": "ls"}) == LoopVerdict()

    fourth = guard.check_call("shell_tool", {"cmd": "ls"})
    assert fourth.warned is True
    assert fourth.blocked is False
    assert "shell_tool" in fourth.reason

    fifth = guard.check_call("shell_tool", {"cmd": "ls"})
    assert fifth.blocked is True
    assert "shell_tool" in fifth.reason


def test_same_tool_with_different_arguments_is_not_identical() -> None:
    guard = make_guard(max_identical_calls=3)
    for index in range(8):
        verdict = guard.check_call("shell_tool", {"cmd": f"echo {index}"})
        assert verdict.blocked is False


def test_argument_order_does_not_change_identity() -> None:
    guard = make_guard(max_identical_calls=3)
    for _ in range(3):
        assert guard.check_call("web_tool", {"a": 1, "b": 2}) == LoopVerdict()
    verdict = guard.check_call("web_tool", {"b": 2, "a": 1})
    assert verdict.warned is True


def test_ping_pong_pattern_warns_then_blocks() -> None:
    # max_identical_calls is raised so only the A-B-A-B path can fire. The
    # arguments repeat: a cycle is degenerate when the same *calls* come back,
    # not merely when the same tools alternate over different targets.
    guard = make_guard(max_identical_calls=99, ping_pong_window=6)
    verdicts = []
    for index in range(8):
        tool = "screen_tool" if index % 2 == 0 else "ui_tool"
        verdicts.append(guard.check_call(tool, {"same": "target"}))

    warned = [v for v in verdicts if v.warned]
    blocked = [v for v in verdicts if v.blocked]
    assert warned, "expected a ping-pong warning before any block"
    assert blocked, "expected the repeated ping-pong cycle to block"
    first_flagged = next(i for i, v in enumerate(verdicts) if v.warned or v.blocked)
    assert first_flagged >= 5, "window must fill before pattern detection"


def test_reset_clears_all_tracking() -> None:
    guard = make_guard(max_identical_calls=3)
    for _ in range(4):
        guard.check_call("shell_tool", {"cmd": "ls"})
    guard.reset()
    assert guard.check_call("shell_tool", {"cmd": "ls"}) == LoopVerdict()


def test_warn_before_block_disabled_blocks_immediately() -> None:
    guard = make_guard(max_identical_calls=2, warn_before_block=False)
    guard.check_call("shell_tool", {"cmd": "ls"})
    guard.check_call("shell_tool", {"cmd": "ls"})
    third = guard.check_call("shell_tool", {"cmd": "ls"})
    assert third.blocked is True
    assert third.warned is False


def test_direct_tool_loop_is_cut_by_guard_before_round_cap(tmp_path: Path) -> None:
    """A brain repeating the exact same tool call burns 3 clean rounds and
    one warned round, then the guard fails the turn — well before the hard
    8-round cap and with a reason naming the degenerate call."""
    tool = RecordingContinuationTool()
    same_call = lambda: model_tool_response(tool.name, {"question": "status"})  # noqa: E731
    adapter = SequenceBrainAdapter(*[same_call() for _ in range(10)])
    app = make_app(tmp_path, adapter)
    app.tool_registry.register(tool)
    try:
        app.start()
        with pytest.raises(TurnOrchestratorError, match="identical call"):
            app.handle_text_input(text="Loop forever")

        # Rounds 1-3 clean, round 4 warned (still executed), round 5 blocked
        # before execution.
        assert len(tool.calls) == 4
        assert table_count(app, "tool_runs") == 4

        turns = app.conn.execute("SELECT id FROM turns").fetchall()
        assert len(turns) == 1
        stored = turn_row(app, turns[0][0])
        assert stored["status"] == TurnStatus.FAILED
    finally:
        app.close()


class TestProgressIsNotALoop:
    """A repeating tool *order* is normal work; a repeating tool *call* is not.

    Matching on tool names alone flagged legitimate read→act cycles — each
    iteration touching a different target — and blocked the turn on the
    seventh call, before the orchestrator's own round cap could apply.
    """

    def test_alternating_calls_with_distinct_arguments_never_trip(self) -> None:
        guard = ToolLoopGuard()
        for step in range(12):
            name = "ui_read_window" if step % 2 == 0 else "ui_click"
            verdict = guard.check_call(name, {"step": step})
            assert not verdict.blocked, f"blocked at step {step}: {verdict.reason}"
            assert not verdict.warned, f"warned at step {step}: {verdict.reason}"

    def test_three_phase_cycle_with_distinct_arguments_never_trips(self) -> None:
        guard = ToolLoopGuard()
        names = ("read", "click", "verify")
        for step in range(12):
            verdict = guard.check_call(names[step % 3], {"target": step})
            assert not verdict.blocked
            assert not verdict.warned

    def test_identical_ping_pong_still_warns_then_blocks(self) -> None:
        guard = ToolLoopGuard()
        verdicts = [
            guard.check_call("read" if step % 2 == 0 else "click", {"same": True})
            for step in range(10)
        ]
        assert any(v.warned for v in verdicts)
        assert any(v.blocked for v in verdicts)


class TestSequenceHasNoHoles:
    """Every checked call must enter the history, including a warned one."""

    def test_warned_identical_call_is_still_recorded(self) -> None:
        guard = ToolLoopGuard(max_identical_calls=1)
        guard.check_call("alpha", {"n": 1})
        warned = guard.check_call("alpha", {"n": 1})
        assert warned.warned and not warned.blocked
        assert len(guard._tool_sequence) == 2


class TestWarningBudgetIsPerPattern:
    """A newly detected cycle gets its own warning, not somebody else's block."""

    def test_second_distinct_pattern_warns_before_it_blocks(self) -> None:
        guard = ToolLoopGuard()
        first = [
            guard.check_call("a" if step % 2 == 0 else "b", {"same": True})
            for step in range(6)
        ]
        assert any(v.warned for v in first)
        guard_verdicts = [
            guard.check_call(("c", "d", "e")[step % 3], {"same": True})
            for step in range(6)
        ]
        assert not guard_verdicts[-1].blocked or any(
            v.warned for v in guard_verdicts[:-1]
        ), "a new pattern must be warned about before it is blocked"
