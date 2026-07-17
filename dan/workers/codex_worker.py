"""Codex worker placeholder."""

from __future__ import annotations

from dan.workers.jobs import WorkerJob


class CodexWorker:
    def run(self, job: WorkerJob) -> str:
        raise NotImplementedError("Codex worker integration is not implemented yet")
