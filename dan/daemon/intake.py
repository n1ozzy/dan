"""Durable admission gate shared by restart and release cutover."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from dan.events.models import utc_now_iso


class IntakeGateError(RuntimeError):
    pass


class IntakeClosedError(IntakeGateError):
    def __init__(self, operation_id: str | None, reason: str | None) -> None:
        self.operation_id = operation_id
        self.reason = reason
        detail = f"operation_id={operation_id or 'unknown'}"
        super().__init__(f"Local intake is closed ({detail}, reason={reason or 'unknown'}).")


@dataclass(frozen=True)
class IntakeState:
    state: str
    operation_id: str | None
    reason: str | None
    reopen_policy: str
    active_leases: int


class IntakeGate:
    """SQLite gate that atomically excludes new work while admitted work drains."""

    def __init__(self, connection: Any) -> None:
        self._conn = connection
        self._local = threading.local()

    @contextmanager
    def admit(self, channel: str) -> Iterator[str]:
        channel = _text(channel, "channel")
        current = getattr(self._local, "token", None)
        if current is not None:
            self._local.depth += 1
            try:
                yield current
            finally:
                self._local.depth -= 1
            return

        token = str(uuid.uuid4())
        with self._write():
            state = self.snapshot()
            if state.state != "open":
                raise IntakeClosedError(state.operation_id, state.reason)
            self._conn.execute(
                "INSERT INTO intake_leases (token, channel, owner_pid, acquired_at) "
                "VALUES (?, ?, ?, ?)",
                (token, channel, os.getpid(), utc_now_iso()),
            )
        self._local.token = token
        self._local.depth = 1
        try:
            yield token
        finally:
            self._local.depth -= 1
            if self._local.depth == 0:
                try:
                    with self._write():
                        self._conn.execute(
                            "DELETE FROM intake_leases WHERE token = ?", (token,)
                        )
                finally:
                    # A failed durable cleanup must not leave this thread looking
                    # re-entrant: that would let its next request bypass a closed gate.
                    self._local.token = None
                    self._local.depth = 0

    def close(
        self,
        *,
        operation_id: str,
        reason: str,
        reopen_policy: str = "daemon",
        before_close: Callable[[IntakeState], None] | None = None,
    ) -> IntakeState:
        operation_id = _text(operation_id, "operation_id")
        reason = _text(reason, "reason")
        reopen_policy = _reopen_policy(reopen_policy)
        with self._write():
            current = self.snapshot()
            if current.state == "closed":
                if current.operation_id != operation_id:
                    raise IntakeGateError(
                        f"Intake is already closed by {current.operation_id or 'unknown'}."
                    )
                return current
            if before_close is not None:
                before_close(current)
            self._conn.execute(
                """
                UPDATE intake_gate
                SET state = 'closed', operation_id = ?, reason = ?, reopen_policy = ?,
                    closed_at = COALESCE(closed_at, ?), reopened_at = NULL
                WHERE singleton = 1
                """,
                (operation_id, reason, reopen_policy, utc_now_iso()),
            )
        return self.snapshot()

    def reopen(self, *, operation_id: str) -> IntakeState:
        operation_id = _text(operation_id, "operation_id")
        with self._write():
            current = self.snapshot()
            if current.operation_id != operation_id:
                raise IntakeGateError(
                    f"Cannot reopen operation {operation_id}; current is "
                    f"{current.operation_id or 'unknown'}."
                )
            if current.active_leases:
                raise IntakeGateError(
                    f"Cannot reopen with {current.active_leases} active lease(s)."
                )
            self._conn.execute(
                "UPDATE intake_gate "
                "SET state = 'open', reopen_policy = 'daemon', reopened_at = ? "
                "WHERE singleton = 1",
                (utc_now_iso(),),
            )
        return self.snapshot()

    def wait_for_drain(self, timeout_seconds: float = 30.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while self.snapshot().active_leases:
            if time.monotonic() >= deadline:
                raise IntakeGateError("Timed out waiting for active intake leases.")
            time.sleep(0.05)

    def snapshot(self) -> IntakeState:
        row = self._conn.execute(
            """
            SELECT gate.state, gate.operation_id, gate.reason, gate.reopen_policy,
                   (SELECT group_concat(owner_pid) FROM intake_leases)
            FROM intake_gate AS gate
            WHERE gate.singleton = 1
            """
        ).fetchone()
        if row is None:
            raise IntakeGateError("Durable intake gate row is missing.")
        return IntakeState(
            str(row[0]), row[1], row[2], str(row[3]), _live_lease_count(row[4])
        )

    @contextmanager
    def _write(self) -> Iterator[None]:
        if self._conn.in_transaction:
            raise IntakeGateError("Intake gate cannot join another transaction.")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise


def _live_lease_count(owner_pids: Any) -> int:
    """Count only leases whose owning process is still running.

    An unclean shutdown leaves rows behind. Counting those made reopen() fail
    forever, so the daemon could never start again without editing the
    database by hand — fail-closed turned into fail-stuck.
    """

    if not owner_pids:
        return 0
    return sum(1 for pid in str(owner_pids).split(",") if _process_alive(pid))


def _process_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        # Unreadable owner: treat as gone rather than wedging the gate.
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive but owned by another user.
        return True
    except OSError:
        return False
    return True


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntakeGateError(f"{label} must be a non-empty string.")
    return value.strip()


def _reopen_policy(value: str) -> str:
    normalized = _text(value, "reopen_policy")
    if normalized not in {"daemon", "external"}:
        raise IntakeGateError(f"Unsupported reopen_policy: {normalized}.")
    return normalized


__all__ = ["IntakeClosedError", "IntakeGate", "IntakeGateError", "IntakeState"]
