"""Deterministic read-only compiler for Memory OS items."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.memory.items import CompilerMemoryItem, MemoryItemRepository
from jarvis.security.redaction import redact_secret_text


ACTIVE_STATUS = "active"
PROCEDURAL_KIND = "procedural"


@dataclass(frozen=True, kw_only=True)
class MemoryCompilerConfig:
    max_items: int = 3
    max_chars: int = 1200
    include_procedural: bool = False
    scope_filter: str | None = None
    namespace_filter: str | None = None
    include_debug_metadata: bool = True


@dataclass(frozen=True, kw_only=True)
class MemoryCompilerRequest:
    conversation_id: str | None = None
    current_turn_id: str | None = None
    current_user_text: str | None = None
    config: MemoryCompilerConfig | None = None


@dataclass(frozen=True, kw_only=True)
class SelectedMemoryItem:
    memory_id: str
    canonical_key: str
    kind: str
    scope: str
    namespace: str
    title: str | None
    claim: str
    reason_selected: str
    evidence_count: int
    source_policy: str | None
    sensitivity: str
    budget_cost: int


@dataclass(frozen=True, kw_only=True)
class SkippedMemoryItem:
    memory_id: str
    reason_skipped: str


@dataclass(frozen=True, kw_only=True)
class CompiledMemoryContext:
    selected_items: list[SelectedMemoryItem] = field(default_factory=list)
    skipped_items: list[SkippedMemoryItem] = field(default_factory=list)
    budget_used: int = 0
    budget_limit: int = 0
    selection_reasons: dict[str, str] = field(default_factory=dict)
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    audit_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class _EligibleItem:
    item: CompilerMemoryItem
    title: str | None
    claim: str


class MemoryCompiler:
    """Build a safe compiled memory projection without database side effects."""

    def __init__(self, item_repository: MemoryItemRepository) -> None:
        self._item_repository = item_repository

    def compile(
        self,
        request: MemoryCompilerRequest | None = None,
    ) -> CompiledMemoryContext:
        normalized_request = request or MemoryCompilerRequest()
        config = normalized_request.config or MemoryCompilerConfig()
        source_items = self._item_repository.list_items_for_compiler()

        eligible_items: list[_EligibleItem] = []
        skipped_items: list[SkippedMemoryItem] = []
        skipped_reasons: dict[str, str] = {}

        for item in source_items:
            lifecycle_reason = _lifecycle_skip_reason(item)
            if lifecycle_reason is not None:
                _append_skip(
                    skipped_items,
                    skipped_reasons,
                    item.id,
                    lifecycle_reason,
                )
                continue

            if item.evidence_count < 1:
                _append_skip(
                    skipped_items,
                    skipped_reasons,
                    item.id,
                    "missing_provenance",
                )
                continue

            title = _redact_optional_text(item.title)
            claim = redact_secret_text(item.claim)
            # Content is intentionally inspected for redaction parity but never
            # returned by compiler output.
            if item.content is not None:
                redact_secret_text(item.content)

            if item.kind == PROCEDURAL_KIND and not config.include_procedural:
                _append_skip(
                    skipped_items,
                    skipped_reasons,
                    item.id,
                    "procedural_not_requested",
                )
                continue

            if _namespace_or_scope_mismatch(item, config):
                _append_skip(
                    skipped_items,
                    skipped_reasons,
                    item.id,
                    "namespace_mismatch",
                )
                continue

            eligible_items.append(_EligibleItem(item=item, title=title, claim=claim))

        _sort_eligible_items(eligible_items, config)

        selected_items: list[SelectedMemoryItem] = []
        selection_reasons: dict[str, str] = {}
        budget_used = 0
        budget_limit = int(config.max_chars)
        max_items = max(0, int(config.max_items))

        for eligible in eligible_items:
            budget_cost = _budget_cost(eligible.title, eligible.claim)
            if (
                len(selected_items) >= max_items
                or budget_used + budget_cost > budget_limit
            ):
                _append_skip(
                    skipped_items,
                    skipped_reasons,
                    eligible.item.id,
                    "over_budget",
                )
                continue

            projected_memory_id = _project_memory_id(eligible.item.id)
            selected = SelectedMemoryItem(
                memory_id=projected_memory_id,
                canonical_key=redact_secret_text(eligible.item.canonical_key),
                kind=eligible.item.kind,
                scope=redact_secret_text(eligible.item.scope),
                namespace=redact_secret_text(eligible.item.namespace),
                title=eligible.title,
                claim=eligible.claim,
                reason_selected="eligible",
                evidence_count=eligible.item.evidence_count,
                source_policy=_redact_optional_text(eligible.item.source_policy),
                sensitivity=redact_secret_text(eligible.item.sensitivity),
                budget_cost=budget_cost,
            )
            selected_items.append(selected)
            selection_reasons[selected.memory_id] = selected.reason_selected
            budget_used += budget_cost

        return CompiledMemoryContext(
            selected_items=selected_items,
            skipped_items=skipped_items,
            budget_used=budget_used,
            budget_limit=budget_limit,
            selection_reasons=selection_reasons,
            skipped_reasons=skipped_reasons,
            audit_metadata=_audit_metadata(
                normalized_request,
                config,
                source_count=len(source_items),
                selected_count=len(selected_items),
                skipped_count=len(skipped_items),
            ),
            warnings=[],
        )


def _append_skip(
    skipped_items: list[SkippedMemoryItem],
    skipped_reasons: dict[str, str],
    memory_id: str,
    reason_skipped: str,
) -> None:
    projected_memory_id = _project_memory_id(memory_id)
    skipped_items.append(
        SkippedMemoryItem(
            memory_id=projected_memory_id,
            reason_skipped=reason_skipped,
        )
    )
    skipped_reasons[projected_memory_id] = reason_skipped


def _lifecycle_skip_reason(item: CompilerMemoryItem) -> str | None:
    status = item.status.strip().lower()
    if status == ACTIVE_STATUS and item.superseded_by:
        return "superseded"
    if status == ACTIVE_STATUS:
        return None
    if status in {
        "candidate",
        "needs_review",
        "approved",
        "approved-but-not-activated",
        "approved_but_not_activated",
    }:
        return "candidate_only"
    if status == "rejected":
        return "rejected"
    if status == "disabled":
        return "disabled"
    if status == "superseded":
        return "superseded"
    if status == "forgotten":
        return "forgotten"
    if status in {"conflict", "merge_candidate"}:
        return "conflict"
    return "inactive"


def _namespace_or_scope_mismatch(
    item: CompilerMemoryItem,
    config: MemoryCompilerConfig,
) -> bool:
    if (
        config.scope_filter is not None
        and item.scope != config.scope_filter
        and not _is_broad_scope_fallback(item.scope)
    ):
        return True
    if (
        config.namespace_filter is not None
        and item.namespace != config.namespace_filter
        and not _is_broad_namespace_fallback(item.namespace)
    ):
        return True
    return False


def _sort_eligible_items(
    eligible_items: list[_EligibleItem],
    config: MemoryCompilerConfig,
) -> None:
    eligible_items.sort(key=lambda eligible: eligible.item.id)
    eligible_items.sort(
        key=lambda eligible: eligible.item.updated_at or "",
        reverse=True,
    )
    eligible_items.sort(
        key=lambda eligible: eligible.item.last_confirmed_at or "",
        reverse=True,
    )
    eligible_items.sort(
        key=lambda eligible: _confidence_rank(eligible.item.confidence),
        reverse=True,
    )
    eligible_items.sort(
        key=lambda eligible: _scope_namespace_rank(eligible.item, config)
    )


def _scope_namespace_rank(
    item: CompilerMemoryItem,
    config: MemoryCompilerConfig,
) -> tuple[int, int]:
    return (
        _scope_match_rank(item.scope, config.scope_filter),
        _namespace_match_rank(item.namespace, config.namespace_filter),
    )


def _scope_match_rank(value: str, filter_value: str | None) -> int:
    return _match_rank(value, filter_value, broad_match=_is_broad_scope_fallback)


def _namespace_match_rank(value: str, filter_value: str | None) -> int:
    return _match_rank(value, filter_value, broad_match=_is_broad_namespace_fallback)


def _match_rank(
    value: str,
    filter_value: str | None,
    *,
    broad_match: Callable[[str], bool],
) -> int:
    if filter_value is None:
        return 0
    if value == filter_value:
        return 0
    if broad_match(value):
        return 1
    return 2


def _is_broad_scope_fallback(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"global", "*", "all", "default"} or normalized.startswith(
        "global/"
    )


def _is_broad_namespace_fallback(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized == "global" or normalized.startswith("global/")


def _confidence_rank(confidence: str) -> int:
    return {
        "high": 3,
        "medium": 2,
        "low": 1,
        "unknown": 0,
    }.get(confidence.strip().lower(), 0)


def _redact_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_secret_text(value)


def _project_memory_id(memory_id: str) -> str:
    digest = hashlib.sha256(memory_id.encode("utf-8")).hexdigest()
    return f"mem_ref_{digest}"


def _budget_cost(title: str | None, claim: str) -> int:
    return len(title or "") + len(claim)


def _audit_metadata(
    request: MemoryCompilerRequest,
    config: MemoryCompilerConfig,
    *,
    source_count: int,
    selected_count: int,
    skipped_count: int,
) -> dict[str, Any]:
    if not config.include_debug_metadata:
        return {}
    return {
        "policy": "memory_compiler_v1",
        "conversation_id": _redact_optional_text(request.conversation_id),
        "current_turn_id": _redact_optional_text(request.current_turn_id),
        "source_count": source_count,
        "selected_count": selected_count,
        "skipped_count": skipped_count,
        "include_procedural": config.include_procedural,
        "scope_filter": _redact_optional_text(config.scope_filter),
        "namespace_filter": _redact_optional_text(config.namespace_filter),
    }


__all__ = [
    "CompiledMemoryContext",
    "MemoryCompiler",
    "MemoryCompilerConfig",
    "MemoryCompilerRequest",
    "SelectedMemoryItem",
    "SkippedMemoryItem",
]
