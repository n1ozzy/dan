"""Deterministic memory selection policies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dan.brain.base import BrainMemoryBlock
    from dan.memory.manager import MemoryBlock


MEMORY_KINDS = {
    "identity",
    "user_preference",
    "project",
    "fact",
    "summary",
    "temporary",
}


def validate_memory_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise ValueError("Memory kind must be a string.")
    normalized = kind.strip()
    if normalized not in MEMORY_KINDS:
        allowed = ", ".join(sorted(MEMORY_KINDS))
        raise ValueError(f"Invalid memory kind: {kind}. Expected one of: {allowed}.")
    return normalized


def estimate_memory_chars(block: "MemoryBlock | BrainMemoryBlock | Mapping[str, Any]") -> int:
    if isinstance(block, Mapping):
        title = str(block.get("title", ""))
        body = str(block.get("body", ""))
    else:
        title = str(getattr(block, "title", ""))
        body = str(getattr(block, "body", ""))
    return len(title) + len(body)


def select_memory_for_budget(
    blocks: Iterable["MemoryBlock"],
    *,
    max_blocks: int | None,
    max_chars: int | None,
) -> list["MemoryBlock"]:
    sorted_blocks = [block for block in blocks if getattr(block, "active", True)]
    sorted_blocks.sort(key=lambda block: str(block.id))
    sorted_blocks.sort(key=lambda block: str(block.updated_at), reverse=True)
    sorted_blocks.sort(key=lambda block: int(block.priority), reverse=True)

    if max_blocks is not None and max_blocks <= 0:
        return []
    if max_chars is not None and max_chars <= 0:
        return []

    selected: list[MemoryBlock] = []
    used_chars = 0
    for block in sorted_blocks:
        if max_blocks is not None and len(selected) >= max_blocks:
            break

        block_chars = estimate_memory_chars(block)
        if max_chars is not None and used_chars + block_chars > max_chars:
            continue

        selected.append(block)
        used_chars += block_chars

    return selected


__all__ = [
    "MEMORY_KINDS",
    "estimate_memory_chars",
    "select_memory_for_budget",
    "validate_memory_kind",
]
