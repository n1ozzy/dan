"""FAZA E2 worker jobs tests: WorkerBroker + mock worker + memory candidates."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.daemon.app import DaemonApp, create_daemon_app
from jarvis.workers.broker import (
    UnknownWorkerKindError,
    WorkerBroker,
    WorkerBrokerError,
)
from jarvis.workers.jobs import WorkerJob, WorkerMemoryCandidate, WorkerResult
from jarvis.workers.mock_worker import MockWorker
from tests.git_guards import assert_schema_and_migrations_unchanged
from tests.test_api_smoke import ROOT, config_text, request_json, running_server


@pytest.fixture
def app(tmp_path: Path) -> Iterator[DaemonApp]:
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(config_text(tmp_path / "home" / "jarvis.db"), encoding="utf-8")
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        yield daemon_app
    finally:
        daemon_app.close()


class FailingWorker:
    kind = "failing"

    def run(self, job: WorkerJob) -> WorkerResult:
        raise RuntimeError("worker exploded on purpose")


class NoCandidateWorker:
    kind = "no_candidate"

    def run(self, job: WorkerJob) -> WorkerResult:
        return WorkerResult(summary="plain result without memory proposal")


def make_broker(app: DaemonApp, *, require_promotion: bool = True) -> WorkerBroker:
    return WorkerBroker(
        app.conn,
        event_store=app.event_store,
        memory_manager=app.memory_manager,
        workers=[MockWorker(), FailingWorker(), NoCandidateWorker()],
        require_candidate_promotion=require_promotion,
    )


def _event_rows(app: DaemonApp, event_type: str) -> list[dict]:
    rows = app.conn.execute(
        "SELECT payload_json FROM events WHERE type = ? ORDER BY id", (event_type,)
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _job_row(app: DaemonApp, job_id: str) -> dict:
    row = app.conn.execute(
        """
        SELECT status, requested_by, worker_kind, prompt, result_summary, error,
               started_at, finished_at, metadata_json
        FROM worker_jobs WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    assert row is not None
    keys = [
        "status",
        "requested_by",
        "worker_kind",
        "prompt",
        "result_summary",
        "error",
        "started_at",
        "finished_at",
        "metadata_json",
    ]
    return dict(zip(keys, row))


def test_enqueue_persists_queued_job_and_event(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(
        worker_kind="mock", prompt="Summarize the plan", requested_by="ozzy"
    )
    assert job.status == "queued"
    assert job.worker_kind == "mock"

    row = _job_row(app, job.id)
    assert row["status"] == "queued"
    assert row["started_at"] is None

    created = _event_rows(app, "worker.job.created")
    assert len(created) == 1
    assert created[0]["job_id"] == job.id
    assert created[0]["worker_kind"] == "mock"


def test_enqueue_unknown_worker_kind_fails_closed(app: DaemonApp) -> None:
    broker = make_broker(app)
    with pytest.raises(UnknownWorkerKindError):
        broker.enqueue(worker_kind="bogus", prompt="anything", requested_by="ozzy")
    count = app.conn.execute("SELECT COUNT(*) FROM worker_jobs").fetchone()[0]
    assert count == 0
    assert _event_rows(app, "worker.job.created") == []


def test_execute_success_creates_inactive_memory_candidate(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(
        worker_kind="mock", prompt="Research adapters", requested_by="ozzy"
    )
    finished = broker.execute(job.id)

    assert finished.status == "succeeded"
    assert finished.result_summary
    row = _job_row(app, job.id)
    assert row["status"] == "succeeded"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None

    # jarvisd (the broker) wrote the candidate, and only as INACTIVE memory:
    # it never enters brain context until a human promotes it (ADR-009).
    candidate_id = json.loads(row["metadata_json"]).get("memory_candidate_id")
    assert isinstance(candidate_id, str) and candidate_id
    block = app.memory_manager.get_block(candidate_id)
    assert block is not None
    assert block.active is False
    assert block.metadata.get("candidate") is True
    assert block.metadata.get("worker_job_id") == job.id
    assert app.memory_manager.active_blocks_for_context() == []

    created = _event_rows(app, "memory.candidate.created")
    assert len(created) == 1
    assert created[0]["block_id"] == candidate_id

    types = [
        str(row[0])
        for row in app.conn.execute(
            "SELECT type FROM events WHERE type LIKE 'worker.job.%' ORDER BY id"
        ).fetchall()
    ]
    assert types == ["worker.job.created", "worker.job.started", "worker.job.finished"]


def test_worker_never_touches_voice_or_turns(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(worker_kind="mock", prompt="quiet job", requested_by="ozzy")
    broker.execute(job.id)
    voice_count = app.conn.execute("SELECT COUNT(*) FROM voice_queue").fetchone()[0]
    turn_count = app.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert voice_count == 0
    assert turn_count == 0


def test_execute_failure_records_error_without_candidate(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(worker_kind="failing", prompt="explode", requested_by="ozzy")
    finished = broker.execute(job.id)

    assert finished.status == "failed"
    assert "worker exploded" in (finished.error or "")
    row = _job_row(app, job.id)
    assert row["status"] == "failed"

    failed_events = _event_rows(app, "worker.job.failed")
    assert len(failed_events) == 1
    assert failed_events[0]["job_id"] == job.id
    assert _event_rows(app, "memory.candidate.created") == []
    memory_count = app.conn.execute("SELECT COUNT(*) FROM memory_blocks").fetchone()[0]
    assert memory_count == 0


def test_execute_without_candidate_proposal_creates_no_memory(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(worker_kind="no_candidate", prompt="plain", requested_by="ozzy")
    finished = broker.execute(job.id)
    assert finished.status == "succeeded"
    memory_count = app.conn.execute("SELECT COUNT(*) FROM memory_blocks").fetchone()[0]
    assert memory_count == 0


def test_execute_is_single_shot(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(worker_kind="mock", prompt="once", requested_by="ozzy")
    broker.execute(job.id)
    with pytest.raises(WorkerBrokerError):
        broker.execute(job.id)
    started = _event_rows(app, "worker.job.started")
    assert len(started) == 1
    candidates = _event_rows(app, "memory.candidate.created")
    assert len(candidates) == 1


def test_concurrent_execute_runs_the_worker_at_most_once(app: DaemonApp) -> None:
    # FIX-07: read-check-then-update let two callers both see 'queued' and both
    # run the worker. The claim is now one conditional UPDATE, so exactly one
    # caller wins and the job runs at most once even under a race.
    import threading

    runs: list[str] = []
    runs_lock = threading.Lock()

    class CountingWorker:
        kind = "counting"

        def run(self, job: WorkerJob) -> WorkerResult:
            with runs_lock:
                runs.append(job.id)
            time.sleep(0.05)  # widen the claim window so the old race would fire
            return WorkerResult(summary="counted")

    broker = WorkerBroker(
        app.conn,
        event_store=app.event_store,
        memory_manager=app.memory_manager,
        workers=[CountingWorker()],
        require_candidate_promotion=True,
    )
    job = broker.enqueue(worker_kind="counting", prompt="race", requested_by="ozzy")

    n = 12
    barrier = threading.Barrier(n)
    conflicts: list[Exception] = []
    conflicts_lock = threading.Lock()

    def attempt() -> None:
        barrier.wait(timeout=5)  # all threads reach execute together
        try:
            broker.execute(job.id)
        except WorkerBrokerError as exc:  # conflict is a WorkerBrokerError subclass
            with conflicts_lock:
                conflicts.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(runs) == 1  # the worker ran exactly once
    assert len(conflicts) == n - 1  # every loser was rejected, none double-ran
    started = _event_rows(app, "worker.job.started")
    assert len(started) == 1


def test_execute_unknown_job_raises(app: DaemonApp) -> None:
    broker = make_broker(app)
    with pytest.raises(WorkerBrokerError):
        broker.execute("no-such-job")


def test_prompt_and_result_are_redacted_at_rest(app: DaemonApp) -> None:
    broker = make_broker(app)
    secret = "sk-test-1234567890abcdef1234567890abcdef"
    job = broker.enqueue(
        worker_kind="mock",
        prompt=f"Use the key {secret} to summarize",
        requested_by="ozzy",
    )
    broker.execute(job.id)
    row = _job_row(app, job.id)
    assert secret not in row["prompt"]
    assert secret not in (row["result_summary"] or "")
    event_dump = app.conn.execute(
        "SELECT GROUP_CONCAT(payload_json) FROM events"
    ).fetchone()[0]
    assert secret not in (event_dump or "")
    memory_dump = app.conn.execute(
        "SELECT GROUP_CONCAT(title || ' ' || body) FROM memory_blocks"
    ).fetchone()[0]
    assert secret not in (memory_dump or "")


def test_candidate_promotion_by_human_activates_block(app: DaemonApp) -> None:
    broker = make_broker(app)
    job = broker.enqueue(worker_kind="mock", prompt="promote me", requested_by="ozzy")
    broker.execute(job.id)
    candidate_id = json.loads(_job_row(app, job.id)["metadata_json"])["memory_candidate_id"]

    promoted = app.memory_manager.update_block(candidate_id, active=True)
    assert promoted.active is True
    assert promoted.metadata.get("candidate") is False
    assert promoted.metadata.get("promoted_by") == "human"

    events = _event_rows(app, "memory.candidate.promoted")
    assert len(events) == 1
    assert events[0]["block_id"] == candidate_id
    assert events[0]["promoted_by"] == "human"
    assert len(app.memory_manager.active_blocks_for_context()) == 1


def test_candidate_auto_promotion_when_policy_allows(app: DaemonApp) -> None:
    broker = make_broker(app, require_promotion=False)
    job = broker.enqueue(worker_kind="mock", prompt="auto", requested_by="ozzy")
    broker.execute(job.id)
    candidate_id = json.loads(_job_row(app, job.id)["metadata_json"])["memory_candidate_id"]

    block = app.memory_manager.get_block(candidate_id)
    assert block is not None
    assert block.active is True
    assert block.metadata.get("candidate") is False
    assert block.metadata.get("promoted_by") == "policy"
    events = _event_rows(app, "memory.candidate.promoted")
    assert len(events) == 1
    assert events[0]["promoted_by"] == "policy"


def test_mock_worker_is_deterministic() -> None:
    worker = MockWorker()
    job = WorkerJob(
        id="job-1",
        type="task",
        worker_kind="mock",
        prompt="same input",
        status="queued",
        requested_by="ozzy",
    )
    first = worker.run(job)
    second = worker.run(job)
    assert first == second
    assert isinstance(first.memory_candidate, WorkerMemoryCandidate)


# --- HTTP API ---


def _wait_for_job(base_url: str, job_id: str, *, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload = request_json("GET", f"{base_url}/workers/jobs/{job_id}")
        assert status == 200
        job = payload["job"]
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"worker job did not finish in time: {job_id}")


def test_worker_job_api_full_lifecycle(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/workers/jobs",
            {"worker_kind": "mock", "prompt": "API job", "requested_by": "ozzy"},
        )
        assert status == 201
        job = payload["job"]
        assert job["worker_kind"] == "mock"
        assert job["status"] in {"queued", "running", "succeeded"}

        finished = _wait_for_job(base_url, job["id"])
        assert finished["status"] == "succeeded"
        assert finished["result_summary"]
        assert finished["metadata"]["memory_candidate_id"]

        status, listing = request_json("GET", f"{base_url}/workers/jobs")
        assert status == 200
        assert [item["id"] for item in listing["jobs"]] == [job["id"]]

        status, filtered = request_json(
            "GET", f"{base_url}/workers/jobs?status=succeeded"
        )
        assert status == 200
        assert len(filtered["jobs"]) == 1


def test_worker_job_api_unknown_kind_is_404(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, payload = request_json(
            "POST",
            f"{base_url}/workers/jobs",
            {"worker_kind": "bogus", "prompt": "x", "requested_by": "ozzy"},
        )
    assert status == 404
    assert "bogus" in str(payload["error"])


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"worker_kind": "mock"},
        {"worker_kind": "mock", "prompt": ""},
        {"worker_kind": "mock", "prompt": "x", "requested_by": ""},
        {"worker_kind": 42, "prompt": "x", "requested_by": "ozzy"},
        ["not", "an", "object"],
    ],
)
def test_worker_job_api_rejects_invalid_payload(app: DaemonApp, body: object) -> None:
    with running_server(app) as base_url:
        status, payload = request_json("POST", f"{base_url}/workers/jobs", body)
    assert status == 400
    assert "error" in payload


def test_worker_job_api_unknown_job_is_404(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, _ = request_json("GET", f"{base_url}/workers/jobs/nope")
    assert status == 404


def test_worker_job_api_invalid_status_filter_is_400(app: DaemonApp) -> None:
    with running_server(app) as base_url:
        status, _ = request_json("GET", f"{base_url}/workers/jobs?status=weird")
    assert status == 400


def test_worker_job_api_requires_transport_token(tmp_path: Path) -> None:
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "jarvis.db").replace(
            "api_token_required = false", "api_token_required = true"
        ),
        encoding="utf-8",
    )
    daemon_app = create_daemon_app(config_path)
    daemon_app.start()
    try:
        with running_server(daemon_app) as base_url:
            status, payload = request_json(
                "POST",
                f"{base_url}/workers/jobs",
                {"worker_kind": "mock", "prompt": "x", "requested_by": "ozzy"},
            )
            assert status == 401
            assert payload == {"error": "Unauthorized", "status": 401}
    finally:
        daemon_app.close()


def test_schema_and_migrations_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)
