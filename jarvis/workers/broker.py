"""WorkerBroker: persistence, lifecycle events, and candidate handoff (FAZA E2).

The broker is the only writer for worker jobs. Workers get a ``WorkerJob``
and return a ``WorkerResult`` — they hold no database handle, no event store,
no memory manager, no voice queue. Everything durable happens here, in
jarvisd (CONTRACTS.md §13, ADR-009, ADR-015: history lives in ``events`` as
``worker.job.*``; there is no parallel job-history table).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from jarvis.events.models import utc_now_iso
from jarvis.events.types import EventType
from jarvis.memory import MemoryManager
from jarvis.security.redaction import redact_secret_text
from jarvis.store.event_store import EventStore
from jarvis.workers.jobs import WorkerJob, WorkerJobStatus, WorkerResult


class WorkerBrokerError(Exception):
    """Raised when a worker job cannot be created or executed safely."""


class UnknownWorkerKindError(WorkerBrokerError):
    """Raised for a worker kind that is not registered (fail-closed)."""


class WorkerJobNotFoundError(WorkerBrokerError):
    """Raised when a worker job does not exist."""


class WorkerJobConflictError(WorkerBrokerError):
    """Raised when a job is not in the right state for the operation."""


class Worker(Protocol):
    kind: str

    def run(self, job: WorkerJob) -> WorkerResult:
        """Produce an advisory result. Never touches the world."""


class WorkerBroker:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        event_store: EventStore,
        memory_manager: MemoryManager,
        workers: Iterable[Worker],
        require_candidate_promotion: bool = True,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._memory_manager = memory_manager
        self._require_candidate_promotion = require_candidate_promotion
        self._workers: dict[str, Worker] = {}
        for worker in workers:
            kind = getattr(worker, "kind", None)
            if not isinstance(kind, str) or not kind:
                raise WorkerBrokerError("Worker must expose a non-empty kind")
            if kind in self._workers:
                raise WorkerBrokerError(f"Duplicate worker registered: {kind}")
            self._workers[kind] = worker

    def worker_kinds(self) -> list[str]:
        return sorted(self._workers)

    def enqueue(
        self,
        *,
        worker_kind: str,
        prompt: str,
        requested_by: str,
        job_type: str = "task",
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkerJob:
        if worker_kind not in self._workers:
            raise UnknownWorkerKindError(f"Unknown worker kind: {worker_kind}")
        normalized_prompt = _required_text(prompt, "prompt")
        normalized_requested_by = _required_text(requested_by, "requested_by")
        normalized_type = _required_text(job_type, "job_type")

        job = WorkerJob(
            id=uuid.uuid4().hex,
            type=normalized_type,
            worker_kind=worker_kind,
            # Prompts are user text and may carry secrets; redact at rest,
            # same stance as tool inputs.
            prompt=redact_secret_text(normalized_prompt),
            status=WorkerJobStatus.QUEUED.value,
            requested_by=normalized_requested_by,
            created_at=utc_now_iso(),
            metadata=dict(metadata or {}),
        )
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO worker_jobs (
                      id, type, status, requested_by, worker_kind, prompt,
                      created_at, artifact_refs_json, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.id,
                        job.type,
                        job.status,
                        job.requested_by,
                        job.worker_kind,
                        job.prompt,
                        job.created_at,
                        json.dumps(list(job.artifact_refs)),
                        json.dumps(job.metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
        except sqlite3.Error as exc:
            raise WorkerBrokerError(f"Could not persist worker job: {exc}") from exc

        self._append_event(
            EventType.WORKER_JOB_CREATED,
            job,
            {"requested_by": job.requested_by},
        )
        return job

    def get_job(self, job_id: str) -> WorkerJob | None:
        row = self._conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM worker_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return None if row is None else _job_from_row(row)

    def list_jobs(self, *, limit: int = 50, status: str | None = None) -> list[WorkerJob]:
        if limit <= 0:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            try:
                normalized = WorkerJobStatus(status).value
            except ValueError as exc:
                allowed = ", ".join(item.value for item in WorkerJobStatus)
                raise ValueError(
                    f"Invalid worker job status: {status}. Expected one of: {allowed}."
                ) from exc
            clauses.append("status = ?")
            params.append(normalized)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(min(limit, 500))
        rows = self._conn.execute(
            f"""
            SELECT {_JOB_COLUMNS} FROM worker_jobs
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [_job_from_row(row) for row in rows]

    def execute(self, job_id: str) -> WorkerJob:
        """Run a queued job to completion, synchronously (single-shot)."""

        job = self.get_job(job_id)
        if job is None:
            raise WorkerJobNotFoundError(f"Unknown worker job: {job_id}")
        if job.status != WorkerJobStatus.QUEUED.value:
            raise WorkerJobConflictError(
                f"Worker job is not queued: {job_id} (status={job.status})"
            )
        worker = self._workers.get(job.worker_kind)
        if worker is None:
            raise UnknownWorkerKindError(f"Unknown worker kind: {job.worker_kind}")

        started_at = utc_now_iso()
        if not self._claim_job(job.id, started_at):
            # Lost the race: another caller moved this job out of 'queued'
            # between the read above and now. The atomic claim is what stops the
            # same job from running twice (FIX-07) — the early check is only a
            # fast fail for an obviously-finished job.
            raise WorkerJobConflictError(
                f"Worker job was already claimed: {job_id}"
            )
        self._append_event(EventType.WORKER_JOB_STARTED, job, {})

        try:
            result = worker.run(job)
        except Exception as exc:
            error = redact_secret_text(f"{type(exc).__name__}: {exc}")
            self._update_job(
                job.id,
                status=WorkerJobStatus.FAILED.value,
                finished_at=utc_now_iso(),
                error=error,
            )
            self._append_event(EventType.WORKER_JOB_FAILED, job, {"error": error})
            return self.get_job(job.id)  # type: ignore[return-value]

        # The worker only PROPOSED memory; jarvisd writes it, and only as an
        # inactive candidate (ADR-009). Policy may auto-promote when the
        # config explicitly says candidates need no human promotion.
        candidate_id: str | None = None
        if result.memory_candidate is not None:
            candidate = result.memory_candidate
            block = self._memory_manager.create_candidate(
                candidate.kind,
                redact_secret_text(candidate.title),
                redact_secret_text(candidate.body),
                priority=candidate.priority,
                proposed_by=f"worker:{job.worker_kind}",
                metadata={"worker_job_id": job.id},
            )
            candidate_id = block.id
            if not self._require_candidate_promotion:
                self._memory_manager.promote_candidate(block.id, promoted_by="policy")

        metadata = dict(job.metadata)
        if candidate_id is not None:
            metadata["memory_candidate_id"] = candidate_id
        self._update_job(
            job.id,
            status=WorkerJobStatus.SUCCEEDED.value,
            finished_at=utc_now_iso(),
            result_summary=redact_secret_text(result.summary),
            artifact_refs_json=json.dumps(list(result.artifact_refs)),
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
        self._append_event(
            EventType.WORKER_JOB_FINISHED,
            job,
            {"memory_candidate_id": candidate_id},
        )
        return self.get_job(job.id)  # type: ignore[return-value]

    def _claim_job(self, job_id: str, started_at: str) -> bool:
        """Atomically move a job from queued to running (FIX-07).

        Returns True only for the caller that won the claim; a concurrent caller
        gets False because the UPDATE's ``status='queued'`` guard no longer
        matches once the job is running."""

        try:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE worker_jobs SET status = ?, started_at = ? "
                    "WHERE id = ? AND status = ?",
                    (
                        WorkerJobStatus.RUNNING.value,
                        started_at,
                        job_id,
                        WorkerJobStatus.QUEUED.value,
                    ),
                )
            return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise WorkerBrokerError(f"Could not claim worker job {job_id}: {exc}") from exc

    def _update_job(self, job_id: str, **fields: Any) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        try:
            with self._conn:
                self._conn.execute(
                    f"UPDATE worker_jobs SET {assignments} WHERE id = ?",
                    (*fields.values(), job_id),
                )
        except sqlite3.Error as exc:
            raise WorkerBrokerError(f"Could not update worker job {job_id}: {exc}") from exc

    def _append_event(
        self, event_type: EventType, job: WorkerJob, extra: Mapping[str, Any]
    ) -> None:
        payload = {
            "job_id": job.id,
            "type": job.type,
            "worker_kind": job.worker_kind,
            **dict(extra),
        }
        self._event_store.append(
            event_type, "worker_broker", payload, correlation_id=job.id
        )


_JOB_COLUMNS = (
    "id, type, worker_kind, prompt, status, requested_by, result_summary, "
    "artifact_refs_json, error, created_at, started_at, finished_at, metadata_json"
)


def _job_from_row(row: sqlite3.Row | tuple[Any, ...]) -> WorkerJob:
    (
        job_id,
        job_type,
        worker_kind,
        prompt,
        status,
        requested_by,
        result_summary,
        artifact_refs_json,
        error,
        created_at,
        started_at,
        finished_at,
        metadata_json,
    ) = row
    return WorkerJob(
        id=str(job_id),
        type=str(job_type),
        worker_kind=str(worker_kind),
        prompt=str(prompt),
        status=str(status),
        requested_by=str(requested_by),
        result_summary=None if result_summary is None else str(result_summary),
        artifact_refs=tuple(json.loads(str(artifact_refs_json or "[]"))),
        error=None if error is None else str(error),
        created_at=None if created_at is None else str(created_at),
        started_at=None if started_at is None else str(started_at),
        finished_at=None if finished_at is None else str(finished_at),
        metadata=json.loads(str(metadata_json or "{}")),
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


__all__ = [
    "UnknownWorkerKindError",
    "Worker",
    "WorkerBroker",
    "WorkerBrokerError",
    "WorkerJobConflictError",
    "WorkerJobNotFoundError",
]
