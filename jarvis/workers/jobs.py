"""Worker job models (CONTRACTS.md §13).

Workers advise; they do not act on the world. A worker returns a
``WorkerResult`` — plain data. Writing anything durable (the job row, events,
the memory *candidate*) is the broker's job, i.e. jarvisd's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WorkerJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WorkerMemoryCandidate:
    """A memory proposal from a worker — never a committed fact (ADR-009)."""

    kind: str
    title: str
    body: str
    priority: int = 0


@dataclass(frozen=True)
class WorkerResult:
    summary: str
    artifact_refs: tuple[str, ...] = ()
    memory_candidate: WorkerMemoryCandidate | None = None


@dataclass(frozen=True)
class WorkerJob:
    id: str
    type: str
    worker_kind: str
    prompt: str
    status: str
    requested_by: str
    result_summary: str | None = None
    artifact_refs: tuple[str, ...] = ()
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "worker_kind": self.worker_kind,
            "prompt": self.prompt,
            "status": self.status,
            "requested_by": self.requested_by,
            "result_summary": self.result_summary,
            "artifact_refs": list(self.artifact_refs),
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata),
        }


__all__ = [
    "WorkerJob",
    "WorkerJobStatus",
    "WorkerMemoryCandidate",
    "WorkerResult",
]
