"""Claude worker placeholder."""

from __future__ import annotations

from jarvis.workers.jobs import WorkerJob


class ClaudeWorker:
    def run(self, job: WorkerJob) -> str:
        raise NotImplementedError("Claude worker integration is not implemented yet")
