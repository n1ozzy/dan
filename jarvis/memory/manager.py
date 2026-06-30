"""Memory manager placeholder."""

from __future__ import annotations


class MemoryManager:
    def active_blocks(self, max_chars: int) -> tuple[str, ...]:
        raise NotImplementedError("memory retrieval is not implemented yet")
