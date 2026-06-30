"""Worker broker placeholder."""

from __future__ import annotations

from jarvis.workers.jobs import WorkerJob


class WorkerBroker:
    def enqueue(self, job: WorkerJob) -> WorkerJob:
        raise NotImplementedError("worker broker is not implemented yet")
