"""Repository placeholders for future persisted contracts."""

from __future__ import annotations


class RepositoryRegistry:
    def resolve(self, name: str) -> object:
        raise NotImplementedError(f"repository is not implemented yet: {name}")
