"""Memory retrieval placeholder."""

from __future__ import annotations


class MemoryRetriever:
    def retrieve(self, query: str, max_chars: int) -> tuple[str, ...]:
        raise NotImplementedError("memory retrieval is not implemented yet")
