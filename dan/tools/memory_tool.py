"""memory_save: explicit durable memory with candidate/evidence provenance.

**A model-originated memory_save writes active memory immediately.** `run()`
creates the candidate, approves it and activates it into ``memory_items`` in
one step. There is no human promotion in this path, whatever MEMORY_CONTRACT.md
says: `ToolRegistry.request_tool` ignores its approval-gate argument.

`propose()` is live — `run()` calls it to build the candidate. What that ignored
argument stranded is the wrapper that used to call `propose()` separately:
`_MemorySaveProposalApprovalGate` in `dan/daemon/app.py`, whose only constructor
(`DaemonApp._approval_gate_for_tool_requests`) now has no callers at all.

It deliberately does not create ``memory_blocks``; ContextBuilder still reads
legacy blocks alongside compiled memory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dan.memory.inbox import (
    APPROVED,
    NEEDS_REVIEW,
    MemoryCandidateRepository,
)
from dan.memory.evidence import MemoryEvidenceRepository
from dan.memory.items import MemoryItemRepository
from dan.memory.policies import MEMORY_KINDS, validate_memory_kind
from dan.security.redaction import redact_secret_text
from dan.tools.registry import Tool

# The durable prompt budget is 12000 chars across 50 blocks; a single save may
# not swallow it. Oversized "memories" are a symptom of dumping transcripts.
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 2000


class MemorySaveTool(Tool):
    name = "memory_save"
    description = (
        "Save one durable fact about the user, their preferences, identity, or a "
        "stable project/environment convention. Never save transient state such "
        "as the current screen, active app, running process, temporary file state, "
        "or one-turn status; inspect those with live tools when needed."
    )
    risk = "memory_write"
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": sorted(MEMORY_KINDS)},
            "title": {"type": "string", "maxLength": MAX_TITLE_CHARS},
            "body": {"type": "string", "maxLength": MAX_BODY_CHARS},
        },
        "required": ["kind", "title", "body"],
    }

    def __init__(
        self,
        *,
        candidate_repository: MemoryCandidateRepository,
        evidence_repository: MemoryEvidenceRepository,
        item_repository: MemoryItemRepository,
    ) -> None:
        self._candidate_repository = candidate_repository
        self._evidence_repository = evidence_repository
        self._item_repository = item_repository

    def propose(
        self,
        arguments: Mapping[str, Any],
        *,
        source_type: str = "explicit_memory_save",
        source_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        event_id: int | None = None,
    ) -> Mapping[str, Any]:
        payload = _memory_save_payload(arguments)
        candidate = self._candidate_repository.create_candidate(
            candidate_kind=payload.kind,
            scope=_scope_for_kind(payload.kind),
            namespace=_namespace_for_kind(payload.kind),
            claim=payload.body,
            title=payload.title,
            reason="explicit memory_save request",
            confidence="unknown",
            sensitivity="unknown",
            recommended_action="approve",
        )
        evidence = self._evidence_repository.add_evidence(
            candidate.id,
            source_type=source_type,
            source_id=source_id or candidate.id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            event_id=event_id,
            quote=payload.body,
            weight=1.0,
        )
        return {
            "ok": True,
            "candidate_id": candidate.id,
            "evidence_id": evidence.id,
            "kind": candidate.candidate_kind,
            "title": candidate.title,
        }

    def validate_proposal_arguments(self, arguments: Mapping[str, Any]) -> None:
        _memory_save_payload(arguments)

    def run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = _memory_save_payload(arguments)
        raw_candidate_id = arguments.get("candidate_id")
        if not isinstance(raw_candidate_id, str) or not raw_candidate_id.strip():
            proposal = self.propose(arguments)
            raw_candidate_id = str(proposal["candidate_id"])
        candidate_id = raw_candidate_id.strip()
        candidate = self._candidate_repository.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"memory_save candidate does not exist: {candidate_id}")
        # create_candidate stored claim/title through redact_secret_text, so the
        # requested payload must be compared through the SAME redaction. Comparing
        # the redacted stored value against the raw payload made any secret-shaped
        # content (API keys, connection strings, PEM) fail this guard and strand
        # the candidate — memory_save could then NEVER persist it. Payload text is
        # already stripped by _capped_str, matching inbox's _required_text.
        if (
            candidate.candidate_kind != payload.kind
            or candidate.title != redact_secret_text(payload.title)
            or candidate.claim != redact_secret_text(payload.body)
        ):
            raise ValueError("memory_save candidate_id does not match the requested payload.")
        if candidate.status == NEEDS_REVIEW:
            candidate = self._candidate_repository.approve_candidate(candidate.id)
        elif candidate.status != APPROVED:
            raise ValueError(f"memory_save candidate is not approvable: {candidate.id}")

        item = self._item_repository.activate_candidate(candidate.id)
        return {
            "ok": True,
            "candidate_id": candidate.id,
            "memory_id": item.id,
            "kind": item.kind,
            "title": item.title,
        }


@dataclass(frozen=True)
class _MemorySavePayload:
    kind: str
    title: str
    body: str


def _memory_save_payload(arguments: Mapping[str, Any]) -> _MemorySavePayload:
    kind = validate_memory_kind(_required_str(arguments, "kind"))
    title = _capped_str(arguments, "title", MAX_TITLE_CHARS)
    body = _capped_str(arguments, "body", MAX_BODY_CHARS)
    return _MemorySavePayload(kind=kind, title=title, body=body)


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


def _scope_for_kind(kind: str) -> str:
    if kind in {"identity", "user_preference"}:
        return "user"
    if kind == "project":
        return "project"
    return "global"


def _namespace_for_kind(kind: str) -> str:
    if kind in {"identity", "user_preference"}:
        return "user/default"
    if kind == "project":
        return "project/default"
    return f"global/{kind}"


__all__ = ["MAX_BODY_CHARS", "MAX_TITLE_CHARS", "MemorySaveTool"]
