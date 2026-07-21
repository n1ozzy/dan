"""Degenerate tool-loop detection for direct tool rounds.

Adapted from OpenJarvis ``agents/loop_guard.py`` (Apache-2.0,
https://github.com/open-jarvis/OpenJarvis): identical-call hashing and
A-B-A-B ping-pong detection with warn-before-block semantics. The context
compression, polling budgets and Rust backend of the original are out of
scope here — the turn orchestrator owns context and already has a hard
round cap; this guard exists to cut degenerate loops *before* that cap
burns full rounds on identical work.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoopVerdict:
    """Result of a single loop-guard check."""

    blocked: bool = False
    warned: bool = False
    reason: str = ""


class ToolLoopGuard:
    """Detect degenerate tool-calling loops inside one turn.

    ``check_call`` must be called once per model-originated tool call, in
    order. With ``warn_before_block`` enabled the first detection of a
    given cycle returns ``warned=True`` (the call may proceed, giving the
    model one chance to change course); the next detection of the same
    cycle blocks.
    """

    def __init__(
        self,
        *,
        max_identical_calls: int = 3,
        ping_pong_window: int = 6,
        warn_before_block: bool = True,
    ) -> None:
        self._max_identical_calls = max_identical_calls
        self._ping_pong_window = ping_pong_window
        self._warn_before_block = warn_before_block
        self._call_counts: dict[str, int] = {}
        # (tool name, call hash) pairs: the pattern match runs on hashes so a
        # repeating tool *order* with different targets each round reads as
        # progress, not as a loop.
        self._tool_sequence: deque[tuple[str, str]] = deque(maxlen=ping_pong_window * 2)
        self._warned_cycles: set[str] = set()

    def check_call(self, tool_name: str, arguments: Mapping[str, Any]) -> LoopVerdict:
        verdict, cycle_key = self._detect(tool_name, arguments)
        if verdict.blocked and self._warn_before_block:
            # The cycle key must stay stable across repeats (unlike the
            # human-readable reason, which embeds the running count) or the
            # guard would warn forever and never block.
            if cycle_key not in self._warned_cycles:
                self._warned_cycles.add(cycle_key)
                return LoopVerdict(blocked=False, warned=True, reason=verdict.reason)
        return verdict

    def reset(self) -> None:
        self._call_counts.clear()
        self._tool_sequence.clear()
        self._warned_cycles.clear()

    def _detect(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> tuple[LoopVerdict, str]:
        call_hash = self._call_hash(tool_name, arguments)
        count = self._call_counts.get(call_hash, 0) + 1
        self._call_counts[call_hash] = count
        # Record before any early return: a warned call still happened, and
        # leaving a hole in the history both hides real cycles and invents
        # ones that were never issued.
        self._tool_sequence.append((tool_name, call_hash))
        if count > self._max_identical_calls:
            verdict = LoopVerdict(
                blocked=True,
                reason=(
                    f"identical call to '{tool_name}' repeated {count} times "
                    f"(max {self._max_identical_calls})"
                ),
            )
            return verdict, f"identical:{call_hash}"

        pattern = (
            self._detect_ping_pong()
            if len(self._tool_sequence) >= self._ping_pong_window
            else None
        )
        if pattern is not None:
            names = " → ".join(name for name, _ in pattern)
            verdict = LoopVerdict(
                blocked=True,
                reason=(
                    "repetitive tool-calling pattern detected "
                    f"(ping-pong: {names})"
                ),
            )
            # Key per pattern, not a shared literal: a model that changes
            # course into a different cycle deserves its own warning first.
            return verdict, "ping-pong:" + "|".join(h for _, h in pattern)
        return LoopVerdict(), ""

    @staticmethod
    def _call_hash(tool_name: str, arguments: Mapping[str, Any]) -> str:
        try:
            encoded = json.dumps(
                arguments, sort_keys=True, ensure_ascii=False, default=repr
            )
        except (TypeError, ValueError):
            encoded = repr(sorted(arguments.items(), key=lambda item: item[0]))
        digest = hashlib.sha256(f"{tool_name}:{encoded}".encode()).hexdigest()
        return digest[:16]

    def _detect_ping_pong(self) -> tuple[tuple[str, str], ...] | None:
        """Return the repeated cycle, or None when the tail is not one.

        A cycle only counts when the *calls* repeat — same tool and same
        arguments — so alternating tools working through different targets
        are left alone.
        """

        sequence = list(self._tool_sequence)
        for period in (2, 3):
            if len(sequence) < period * 2:
                continue
            tail = sequence[-period * 2 :]
            pattern = tail[:period]
            if len({call_hash for _, call_hash in pattern}) < 2:
                continue
            if all(tail[i] == pattern[i % period] for i in range(len(tail))):
                return tuple(pattern)
        return None


__all__ = ["LoopVerdict", "ToolLoopGuard"]
