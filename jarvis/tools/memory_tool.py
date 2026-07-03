"""memory_save: the model curates durable memory through the approval gate.

ADR-009 keeps memory promotion human-sanctioned and ADR-010 routes every
mutation through ApprovalGate. This tool composes both: the model proposes a
block during a turn, the human approves it in the existing approvals panel,
and only the approved execution (``run``) creates and promotes the block —
so ``run`` never fires without a human decision upstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.memory.manager import MemoryManager
from jarvis.memory.policies import MEMORY_KINDS, validate_memory_kind
from jarvis.tools.registry import Tool

# The durable prompt budget is 12000 chars across 50 blocks; a single save may
# not swallow it. Oversized "memories" are a symptom of dumping transcripts.
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 2000


class MemorySaveTool(Tool):
    name = "memory_save"
    description = (
        "Save one durable memory block about the user, their preferences or the "
        "environment (approval-gated). Use when you learn a lasting fact worth "
        "remembering across conversations; never for transient turn context."
    )
    risk = "memory_write"
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": sorted(MEMORY_KINDS)},
            "title": {"type": "string", "maxLength": MAX_TITLE_CHARS},
            "body": {"type": "string", "maxLength": MAX_BODY_CHARS},
            "priority": {"type": "integer", "minimum": 0, "maximum": 10},
        },
        "required": ["kind", "title", "body"],
    }

    def __init__(self, memory_manager: MemoryManager) -> None:
        self._memory_manager = memory_manager

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        kind = validate_memory_kind(_required_str(arguments, "kind"))
        title = _capped_str(arguments, "title", MAX_TITLE_CHARS)
        body = _capped_str(arguments, "body", MAX_BODY_CHARS)
        priority = _priority(arguments.get("priority", 0))

        candidate = self._memory_manager.create_candidate(
            kind,
            title,
            body,
            priority=priority,
            proposed_by="model",
        )
        block = self._memory_manager.promote_candidate(candidate.id, promoted_by="approval")
        return {
            "ok": True,
            "block_id": block.id,
            "kind": block.kind,
            "title": block.title,
        }


def _required_str(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"memory_save requires a non-empty string {key}.")
    return value.strip()


def _capped_str(arguments: Mapping[str, Any], key: str, max_chars: int) -> str:
    value = _required_str(arguments, key)
    if len(value) > max_chars:
        raise ValueError(f"memory_save {key} must be at most {max_chars} characters.")
    return value


def _priority(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 10:
        raise ValueError("memory_save priority must be an integer between 0 and 10.")
    return value


__all__ = ["MAX_BODY_CHARS", "MAX_TITLE_CHARS", "MemorySaveTool"]
