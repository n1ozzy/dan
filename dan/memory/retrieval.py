"""Small retrieval wrapper around DAN-owned memory selection."""

from __future__ import annotations

from dan.memory.manager import MemoryBlock, MemoryManager


class MemoryRetriever:
    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    def retrieve(self, max_chars: int, max_blocks: int | None = None) -> list[MemoryBlock]:
        return self._manager.active_blocks_for_context(
            max_blocks=max_blocks,
            max_chars=max_chars,
        )


__all__ = ["MemoryRetriever"]
