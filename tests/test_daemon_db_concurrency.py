"""FIX-03: the daemon must not share one SQLite write connection across threads.

A single connection with check_same_thread=False means every `with conn:`
block joins the same transaction: one thread's rollback silently discards
another thread's uncommitted append-only event. These tests pin the fixed
contract: per-thread connections, worker threads drained on stop, and
memory mutation + audit event in one transaction.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.events.models import utc_now_iso
from jarvis.workers.jobs import WorkerJob, WorkerResult
from tests.test_api_smoke import write_config


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    daemon_app = create_daemon_app(config_path)
    try:
        yield daemon_app
    finally:
        daemon_app.close()


def _insert_event(conn, marker: str) -> None:
    conn.execute(
        """
        INSERT INTO events (created_at, type, source, correlation_id, turn_id, payload_json)
        VALUES (?, 'test.concurrency', ?, NULL, NULL, '{}')
        """,
        (utc_now_iso(), marker),
    )


def _event_count(conn, marker: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source = ?", (marker,)
    ).fetchone()
    return int(row[0])


def test_rollback_in_other_thread_does_not_discard_pending_write(app: DaemonApp) -> None:
    """Thread B rolling back its (empty) transaction must not wipe thread A's
    uncommitted insert — only possible when each thread has its own connection."""

    conn = app.conn
    inserted = threading.Event()
    rolled_back = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            _insert_event(conn, "thread-a")  # open transaction, not committed yet
            inserted.set()
            assert rolled_back.wait(timeout=5)
            conn.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced in main thread
            errors.append(exc)
            inserted.set()

    def roller() -> None:
        assert inserted.wait(timeout=5)
        try:
            with conn:
                raise RuntimeError("forced rollback")
        except RuntimeError:
            pass
        finally:
            rolled_back.set()

    threads = [threading.Thread(target=writer), threading.Thread(target=roller)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert _event_count(conn, "thread-a") == 1


def test_concurrent_event_appends_lose_nothing(app: DaemonApp) -> None:
    store = app.event_store
    assert store is not None
    thread_count, per_thread = 8, 10
    barrier = threading.Barrier(thread_count)
    errors: list[BaseException] = []

    def appender(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            for _ in range(per_thread):
                store.append("test.concurrency", f"appender-{index}", {})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=appender, args=(i,)) for i in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    row = app.conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'test.concurrency'"
    ).fetchone()
    assert int(row[0]) == thread_count * per_thread


class _GatedWorker:
    """Worker that blocks until released, to catch stop() racing workers."""

    kind = "gated"

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()

    def run(self, job: WorkerJob) -> WorkerResult:
        self.started.set()
        assert self.release.wait(timeout=10)
        self.finished.set()
        return WorkerResult(summary="gated done", memory_candidate=None)


def test_stop_waits_for_worker_threads_before_daemon_stopped(app: DaemonApp) -> None:
    from jarvis.workers import WorkerBroker

    worker = _GatedWorker()
    app.worker_broker = WorkerBroker(
        app.conn,
        event_store=app.event_store,
        memory_manager=app.memory_manager,
        workers=[worker],
    )
    app.start()
    app.create_worker_job(worker_kind="gated", prompt="hold", requested_by="test")
    assert worker.started.wait(timeout=5)

    stopper = threading.Thread(target=app.stop)
    stopper.start()
    time.sleep(0.1)  # let stop() reach the worker join
    worker.release.set()
    stopper.join(timeout=10)
    assert not stopper.is_alive()

    assert worker.finished.is_set()
    rows = app.conn.execute(
        "SELECT type FROM events WHERE type IN ('daemon.stopped', 'worker.job.finished') ORDER BY id"
    ).fetchall()
    types = [str(row[0]) for row in rows]
    assert "daemon.stopped" in types
    assert types.index("worker.job.finished") < types.index("daemon.stopped"), (
        "stop() must drain worker threads before appending daemon.stopped"
    )


def test_memory_update_rolls_back_when_event_append_fails(app: DaemonApp) -> None:
    manager = app.memory_manager
    assert manager is not None
    block = manager.create_block("fact", "Tytuł", "Treść")

    class _ExplodingStore:
        def append(self, *args, **kwargs):
            raise RuntimeError("event store down")

    manager._event_store = _ExplodingStore()
    try:
        with pytest.raises(Exception):
            manager.update_block(block.id, title="Nowy tytuł")
    finally:
        manager._event_store = app.event_store

    reread = manager.get_block(block.id)
    assert reread is not None
    assert reread.title == "Tytuł", "mutation must roll back when its audit event fails"
