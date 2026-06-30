"""Worker job models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class WorkerJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WorkerJob:
    id: str
    kind: str
    spec: dict[str, Any] = field(default_factory=dict)
    status: WorkerJobStatus = WorkerJobStatus.QUEUED
    result_ref: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
