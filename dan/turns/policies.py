"""Turn policy placeholders."""

from __future__ import annotations


class TurnPolicy:
    def accepts_input(self, text: str) -> bool:
        raise NotImplementedError("turn policies are not implemented yet")
