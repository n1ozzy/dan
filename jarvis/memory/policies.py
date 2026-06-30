"""Memory policy placeholders."""

from __future__ import annotations


class MemoryPolicy:
    def may_promote_candidate(self, source: str) -> bool:
        raise NotImplementedError("memory promotion policy is not implemented yet")
