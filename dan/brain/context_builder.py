"""Build stateless BrainRequest objects from DAN-owned state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dan.brain.base import BrainMessage, BrainRequest, BrainToolSpec
from dan.logging import get_logger
from dan.persona import DEFAULT_CANON_PATH, PersonaError, render_persona
from dan.security.redaction import redact_secret_text

from ..memory.compiler import MemoryCompiler, MemoryCompilerConfig, MemoryCompilerRequest
from ..memory.manager import MemoryManager

if TYPE_CHECKING:
    from ..memory.compiler import CompiledMemoryContext


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONA_PATH = DEFAULT_CANON_PATH
DEFAULT_CONTEXT_BUDGET_CHARS = 24000
JOB_PROMPT_PREVIEW_CHARS = 120

PERSONA_PROFILE_SETTING_KEY = "persona.profile"

# Added only when responses are voiced: the model returns an explicit spoken
# rendition plus the full chat form, without changing the owner-defined persona.
_VOICE_FORM_INSTRUCTION = (
    "Twoja odpowiedź jest czytana na głos przez syntezator. Zacznij odpowiedź "
    "od bloku:\n"
    "[[GŁOS]]\n"
    "tu naturalna forma do odsłuchu, dokładnie tą samą personą co pełna odpowiedź\n"
    "[[/GŁOS]]\n"
    "a dopiero po nim napisz pełną odpowiedź na czat. Blok musi być pierwszy, "
    "żeby mowa ruszyła od razu. Długość dobierz naturalnie do treści i rytmu persony; "
    "nie tnij wypowiedzi do ustalonej liczby zdań ani znaków. Jeśli w tej odpowiedzi "
    "żądasz narzędzia, blok jest jawnym komentarzem dla użytkownika przed jego "
    "wykonaniem, a nie finalną odpowiedzią. Nie ujawniaj wewnętrznego toku rozumowania. "
    "Po wyniku narzędzia przygotuj nowy blok dla kolejnej reakcji albo finalnej wypowiedzi. "
    "Nie wygładzaj stylu, nie usuwaj wulgaryzmów i nie zmieniaj tonu persony. "
    "Nie wkładaj do bloku surowych logów, argumentów ani wyników narzędzi, kodu, "
    "ścieżek, nazw plików, adresów ani identyfikatorów; szczegóły techniczne zostaw "
    "w pełnej odpowiedzi na czacie. Użyj dokładnie jednego bloku i nie odwołuj się do niego "
    "w treści przeznaczonej na czat."
)
DEFAULT_PERSONA_PROFILE = "dan"
_SAFE_COMPILED_MEMORY_SKIPPED_CATEGORIES = frozenset(
    {
        "candidate_only",
        "conflict",
        "disabled",
        "forgotten",
        "inactive",
        "missing_provenance",
        "namespace_mismatch",
        "over_budget",
        "procedural_not_requested",
        "rejected",
        "superseded",
    }
)

_LOGGER = get_logger("brain.context_builder")


class ContextBuilderError(Exception):
    """Raised when DAN-owned context cannot be assembled."""


def _persona_version(content: str) -> str | None:
    match = re.search(r"(?m)^DAN_CANON_VERSION:\s*([^\s]+)\s*$", content)
    return match.group(1) if match else None


@dataclass(frozen=True)
class CompiledMemoryDiagnostics:
    compiled_memory_enabled: bool
    compiler_available: bool
    compiled_memory_attempted: bool
    compiled_memory_section_present: bool
    selected_count: int
    skipped_count: int
    fail_closed: bool
    failure_category: str | None
    skipped_categories: dict[str, int]


@dataclass(frozen=True)
class ContextBuildResult:
    request: BrainRequest
    context_snapshot: dict[str, Any]
    compiled_memory_diagnostics: CompiledMemoryDiagnostics


@dataclass(frozen=True)
class _CompiledMemoryBuild:
    message: BrainMessage | None
    diagnostics: CompiledMemoryDiagnostics


class ContextBuilder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        config: Any | None = None,
        persona_path: Path | None = None,
        memory_manager: MemoryManager | None = None,
        memory_compiler: Any | None = None,
        compiled_memory_enabled: bool = False,
        compiled_memory_scope_gate_enabled: bool | None = None,
        compiled_memory_enabled_session_profiles: Iterable[tuple[str, str]] | None = None,
        compiled_memory_force_disabled: bool = False,
        compiled_memory_config: MemoryCompilerConfig | None = None,
        event_store: Any | None = None,
        now: Callable[[], str] | None = None,
        tool_specs: Callable[[], Any] | None = None,
    ) -> None:
        self._conn = conn
        self._config = config
        self._persona_path = persona_path or DEFAULT_PERSONA_PATH
        self._memory_manager = memory_manager or MemoryManager(conn)
        self._memory_compiler = memory_compiler
        self._compiled_memory_enabled = bool(compiled_memory_enabled)
        self._compiled_memory_scope_gate_enabled = (
            bool(compiled_memory_scope_gate_enabled)
            if compiled_memory_scope_gate_enabled is not None
            else self._compiled_memory_enabled
        )
        self._compiled_memory_enabled_session_profiles = (
            _normalize_compiled_memory_session_profiles(
                compiled_memory_enabled_session_profiles
            )
        )
        self._compiled_memory_force_disabled = bool(compiled_memory_force_disabled)
        self._compiled_memory_config = (
            compiled_memory_config or _default_memory_compiler_config()
        )
        self._event_store = event_store
        self._now = now or utc_now_iso
        # Callable returning the registry's ToolSpecs (name/description/
        # input_schema/risk). None or empty => the prompt lists no tools.
        self._tool_specs = tool_specs

    @property
    def context_budget_chars(self) -> int:
        """Return the effective input budget used by ``build_request``."""

        return self._resolve_context_budget(None)

    def build_request(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        input_text: str,
        runtime_state: str | None = None,
        settings: Mapping[str, Any] | None = None,
        max_context_chars: int | None = None,
        recent_turn_limit: int = 12,
        compiled_memory_enabled_override: bool | None = None,
    ) -> ContextBuildResult:
        normalized_turn_id = _required_text(turn_id, "turn_id")
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        if not isinstance(input_text, str):
            raise ContextBuilderError("input_text must be a string.")

        budget = self._resolve_context_budget(max_context_chars)
        # Cap the user input to the budget (FIX-07): _fit_budget only trims
        # messages/memory, so an oversized input_text would otherwise flow to the
        # prompt/stdin unbounded — the very thing that made the stdin deadlock
        # reachable. Truncate with a visible marker rather than silently.
        input_text = _cap_input_text(input_text, budget)
        request_settings = self._build_settings(settings)
        persona_profile = DEFAULT_PERSONA_PROFILE
        core_messages = self._build_core_messages(runtime_state)
        recent_messages = self._build_recent_turn_messages(
            normalized_conversation_id,
            recent_turn_limit,
            exclude_turn_id=normalized_turn_id,
        )
        active_jobs = self._active_worker_jobs()
        job_message = self._build_job_message(active_jobs)
        if job_message is not None:
            core_messages.append(job_message)
        compiled_memory_enabled = self._resolve_compiled_memory_enabled(
            conversation_id=normalized_conversation_id,
            persona_profile=persona_profile,
            override=compiled_memory_enabled_override,
        )
        compiled_memory = self._build_compiled_memory(
            conversation_id=normalized_conversation_id,
            turn_id=normalized_turn_id,
            input_text=input_text,
            compiled_memory_enabled=compiled_memory_enabled,
            cache_created_compiler=compiled_memory_enabled_override is None,
        )
        if compiled_memory.message is not None:
            core_messages.append(compiled_memory.message)

        memory_max_chars = _config_int(self._config, ("memory", "max_context_chars"), budget)
        if memory_max_chars is None:
            memory_max_chars = budget
        memory_blocks = self._memory_manager.active_blocks_for_context(
            max_blocks=_config_int(self._config, ("memory", "max_active_blocks"), None),
            max_chars=min(memory_max_chars, budget),
        )
        brain_memory_blocks = self._memory_manager.to_brain_memory_blocks(memory_blocks)

        messages = core_messages + recent_messages
        messages, brain_memory_blocks = _fit_budget(
            messages=messages,
            core_message_count=len(core_messages),
            memory_blocks=brain_memory_blocks,
            input_text=input_text,
            max_context_chars=budget,
        )
        compiled_memory_diagnostics = _finalize_compiled_memory_diagnostics(
            compiled_memory.diagnostics,
            messages,
        )

        estimated_context_chars = _estimate_context_chars(
            messages,
            brain_memory_blocks,
            input_text,
        )
        recent_turn_count = len(
            {
                str(message.metadata.get("turn_id"))
                for message in messages[len(core_messages) :]
                if message.metadata.get("turn_id")
            }
        )
        persona_metadata = next(
            (message.metadata for message in messages if message.metadata.get("kind") == "persona"),
            {},
        )
        snapshot = {
            "turn_id": normalized_turn_id,
            "conversation_id": normalized_conversation_id,
            "context_message_count": len(messages),
            "memory_block_count": len(brain_memory_blocks),
            "recent_turn_count": recent_turn_count,
            "active_job_count": len(active_jobs),
            "max_context_chars": budget,
            "estimated_context_chars": estimated_context_chars,
            "includes_persona": bool(messages and messages[0].metadata.get("kind") == "persona"),
            "persona_profile": persona_profile,
            "persona_source": persona_metadata.get("source"),
            "persona_version": persona_metadata.get("version"),
            "persona_sha256": persona_metadata.get("sha256"),
            "provider_sessions_are_memory": False,
            "created_at": self._now(),
        }

        request = BrainRequest(
            turn_id=normalized_turn_id,
            conversation_id=normalized_conversation_id,
            input_text=input_text,
            context_messages=messages,
            memory_blocks=brain_memory_blocks,
            available_tools=self._collect_available_tools(),
            settings=request_settings,
            metadata={"context_snapshot": _stable_snapshot_for_request_metadata(snapshot)},
        )
        return ContextBuildResult(
            request=request,
            context_snapshot=snapshot,
            compiled_memory_diagnostics=compiled_memory_diagnostics,
        )

    def _build_compiled_memory(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        input_text: str,
        compiled_memory_enabled: bool,
        cache_created_compiler: bool,
    ) -> _CompiledMemoryBuild:
        if not compiled_memory_enabled:
            return _CompiledMemoryBuild(
                message=None,
                diagnostics=_compiled_memory_diagnostics(
                    enabled=False,
                    compiler_available=self._memory_compiler is not None,
                    attempted=False,
                    section_present=False,
                    selected_count=0,
                    skipped_count=0,
                    fail_closed=False,
                    failure_category=None,
                    skipped_categories={},
                ),
            )

        compiler = self._memory_compiler
        if compiler is None:
            from ..memory.items import MemoryItemRepository

            compiler = MemoryCompiler(MemoryItemRepository(self._conn))
            if cache_created_compiler:
                self._memory_compiler = compiler

        try:
            compiled = compiler.compile(
                MemoryCompilerRequest(
                    conversation_id=conversation_id,
                    current_turn_id=turn_id,
                    current_user_text=input_text,
                    config=self._compiled_memory_config,
                )
            )
            content = _format_compiled_memory_context(compiled)
        except Exception:
            _LOGGER.warning("omitting compiled memory context after compiler failure")
            return _CompiledMemoryBuild(
                message=None,
                diagnostics=_compiled_memory_diagnostics(
                    enabled=True,
                    compiler_available=compiler is not None,
                    attempted=True,
                    section_present=False,
                    selected_count=0,
                    skipped_count=0,
                    fail_closed=True,
                    failure_category="compiler_error",
                    skipped_categories={},
                ),
            )

        if content is None:
            return _CompiledMemoryBuild(
                message=None,
                diagnostics=_compiled_memory_diagnostics(
                    enabled=True,
                    compiler_available=True,
                    attempted=True,
                    section_present=False,
                    selected_count=len(compiled.selected_items),
                    skipped_count=len(compiled.skipped_items),
                    fail_closed=False,
                    failure_category=None,
                    skipped_categories=_compiled_memory_skipped_categories(compiled),
                ),
            )
        return _CompiledMemoryBuild(
            message=BrainMessage(
                role="user",
                content=content,
                metadata={"kind": "compiled_memory", "untrusted": True},
            ),
            diagnostics=_compiled_memory_diagnostics(
                enabled=True,
                compiler_available=True,
                attempted=True,
                section_present=True,
                selected_count=len(compiled.selected_items),
                skipped_count=len(compiled.skipped_items),
                fail_closed=False,
                failure_category=None,
                skipped_categories=_compiled_memory_skipped_categories(compiled),
            ),
        )

    def _resolve_compiled_memory_enabled(
        self,
        *,
        conversation_id: str,
        persona_profile: str,
        override: bool | None,
    ) -> bool:
        if not _config_bool(self._config, ("memory", "enabled"), True):
            return False
        if self._compiled_memory_force_disabled:
            return False
        if override is not None:
            return bool(override)
        if self._compiled_memory_enabled:
            return True
        if not self._compiled_memory_scope_gate_enabled:
            return False
        return (
            conversation_id,
            persona_profile,
        ) in self._compiled_memory_enabled_session_profiles

    def _collect_available_tools(self) -> list[BrainToolSpec]:
        """Expose the registry's tools so the prompt can list them (else the
        model is told "Available tools: - none" and denies having any). Risk
        rides along; execution is handled by the observable DAN tool loop."""

        if self._tool_specs is None:
            return []
        return [
            BrainToolSpec(
                name=spec.name,
                description=spec.description,
                input_schema=dict(getattr(spec, "input_schema", {}) or {}),
                risk=spec.risk,
            )
            for spec in self._tool_specs()
        ]

    def _resolve_context_budget(self, max_context_chars: int | None) -> int:
        if max_context_chars is not None:
            if max_context_chars <= 0:
                raise ContextBuilderError("max_context_chars must be positive.")
            return int(max_context_chars)
        configured = _config_int(
            self._config,
            ("brain", "context_budget_chars"),
            DEFAULT_CONTEXT_BUDGET_CHARS,
        )
        if configured is None or configured <= 0:
            return DEFAULT_CONTEXT_BUDGET_CHARS
        return configured

    def _build_settings(self, explicit_settings: Mapping[str, Any] | None) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "provider_sessions_are_memory": False,
        }
        brain_config = getattr(self._config, "brain", None)
        memory_config = getattr(self._config, "memory", None)

        if brain_config is not None:
            settings["brain_adapter"] = getattr(brain_config, "default_adapter", "mock")
            settings["model"] = getattr(brain_config, "default_model", "mock-local")
            settings["model_source"] = "config"
            settings["context_budget_chars"] = getattr(
                brain_config,
                "context_budget_chars",
                DEFAULT_CONTEXT_BUDGET_CHARS,
            )

        if memory_config is not None:
            settings["memory_enabled"] = getattr(memory_config, "enabled", True)
            settings["max_active_memory_blocks"] = getattr(
                memory_config,
                "max_active_blocks",
                None,
            )
            settings["max_memory_context_chars"] = getattr(
                memory_config,
                "max_context_chars",
                None,
            )

        stored_settings = self._read_settings_table()
        stored_settings.pop(PERSONA_PROFILE_SETTING_KEY, None)
        settings.update(stored_settings)
        if "model" in stored_settings:
            settings["model_source"] = "settings"
        if explicit_settings is not None:
            if not isinstance(explicit_settings, Mapping):
                raise ContextBuilderError("settings must be a mapping.")
            explicit = dict(explicit_settings)
            explicit.pop(PERSONA_PROFILE_SETTING_KEY, None)
            settings.update(explicit)
            if "model" in explicit and "model_source" not in explicit:
                settings["model_source"] = "runtime"

        settings["provider_sessions_are_memory"] = False
        settings[PERSONA_PROFILE_SETTING_KEY] = DEFAULT_PERSONA_PROFILE
        settings.pop("persona.vulgarity_level", None)
        return settings

    def _read_settings_table(self) -> dict[str, Any]:
        try:
            rows = self._conn.execute(
                "SELECT key, value_json FROM settings ORDER BY key ASC"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ContextBuilderError(f"Could not read settings: {exc}") from exc

        values: dict[str, Any] = {}
        for key, value_json in rows:
            try:
                values[str(key)] = json.loads(str(value_json))
            except json.JSONDecodeError as exc:
                # One corrupt settings row must not abort every turn build
                # (FIX-07 — a DoS otherwise): skip it and fall back to defaults.
                _LOGGER.warning("skipping settings row %r with invalid JSON: %s", key, exc)
        return values

    def _build_core_messages(
        self,
        runtime_state: str | None,
    ) -> list[BrainMessage]:
        messages: list[BrainMessage] = []
        persona = self._load_persona()
        messages.append(
            BrainMessage(
                role="system",
                content=persona,
                metadata={
                    "kind": "persona",
                    "profile": DEFAULT_PERSONA_PROFILE,
                    "source": str(self._persona_path.resolve()),
                    "version": _persona_version(persona),
                    "sha256": hashlib.sha256(persona.encode("utf-8")).hexdigest(),
                },
            )
        )
        if runtime_state:
            messages.append(
                BrainMessage(
                    role="system",
                    content=f"Runtime state: {runtime_state}",
                    metadata={"kind": "runtime_state"},
                )
            )
        if self.speech_form_enabled():
            messages.append(
                BrainMessage(
                    role="system",
                    content=_VOICE_FORM_INSTRUCTION,
                    metadata={"kind": "voice_form"},
                )
            )
        return messages

    def speech_form_enabled(self) -> bool:
        """Ask for a model-authored spoken form only when responses are voiced.

        Public because the orchestrator uses the same gate to decide whether
        streamed deltas must pass through the [[GŁOS]] stream router — the
        instruction and the router are only safe together (instruction without
        router = TTS reads markers aloud; router without instruction = live
        speech goes silent until finalize)."""

        return _config_bool(self._config, ("voice", "enabled"), False) and _config_bool(
            self._config, ("voice", "speak_responses"), False
        )


    def _load_persona(self) -> str:
        path = self._persona_path
        try:
            return render_persona(path)
        except PersonaError as exc:
            raise ContextBuilderError(str(exc)) from exc

    def _build_recent_turn_messages(
        self,
        conversation_id: str,
        recent_turn_limit: int,
        *,
        exclude_turn_id: str | None = None,
    ) -> list[BrainMessage]:
        if recent_turn_limit <= 0:
            return []

        try:
            rows = self._conn.execute(
                """
                SELECT id, created_at, status, input_text, final_text
                FROM turns
                WHERE conversation_id = ?
                  AND (? IS NULL OR id != ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (conversation_id, exclude_turn_id, exclude_turn_id, int(recent_turn_limit)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ContextBuilderError(f"Could not read recent turns: {exc}") from exc

        messages: list[BrainMessage] = []
        for turn_id, created_at, status, stored_input, final_text in reversed(rows):
            if str(status).lower() in {"failed", "error"} and not final_text:
                continue
            metadata = {"kind": "turn", "turn_id": str(turn_id), "created_at": str(created_at)}
            if stored_input and str(status).lower() not in {"failed", "error"}:
                messages.append(
                    BrainMessage(
                        role="user",
                        content=str(stored_input),
                        metadata={**metadata, "field": "input_text"},
                    )
                )
            if final_text:
                messages.append(
                    BrainMessage(
                        role="assistant",
                        content=str(final_text),
                        metadata={**metadata, "field": "final_text"},
                    )
                )
        return messages

    def _active_worker_jobs(self) -> list[dict[str, Any]]:
        # Workers are intentionally disabled in the runtime-lab path for now.
        # DAN is the single active brain; background job context was another
        # source of prompt confusion.
        return []
        try:
            rows = self._conn.execute(
                """
                SELECT id, type, status, worker_kind, prompt
                FROM worker_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise ContextBuilderError(f"Could not read worker jobs: {exc}") from exc

        return [
            {
                "id": str(row[0]),
                "type": str(row[1]),
                "status": str(row[2]),
                "worker_kind": str(row[3]),
                "prompt": str(row[4]),
            }
            for row in rows
        ]

    def _build_job_message(self, active_jobs: list[dict[str, Any]]) -> BrainMessage | None:
        if not active_jobs:
            return None

        # Worker jobs are operator/runtime queued work items, not a second system
        # prompt. They are carried as user context so DAN can use them without
        # letting them override the owner persona.
        lines = [
            f"Active worker jobs: {len(active_jobs)} (operator/runtime queued work; "
            "use when relevant, while keeping the DAN persona and current user "
            "turn authoritative):",
        ]
        for job in active_jobs:
            preview = _truncate(job["prompt"], JOB_PROMPT_PREVIEW_CHARS)
            lines.append(
                "- {id} [{status}] {worker_kind}/{type}: prompt={prompt!r}".format(
                    id=job["id"],
                    status=job["status"],
                    worker_kind=job["worker_kind"],
                    type=job["type"],
                    prompt=preview,
                )
            )
        return BrainMessage(
            role="user",
            content="\n".join(lines),
            metadata={"kind": "worker_jobs", "untrusted": True},
        )


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fit_budget(
    *,
    messages: list[BrainMessage],
    core_message_count: int,
    memory_blocks: list[Any],
    input_text: str,
    max_context_chars: int,
) -> tuple[list[BrainMessage], list[Any]]:
    fitted_messages = list(messages)
    fitted_memory_blocks = list(memory_blocks)

    while (
        _estimate_context_chars(fitted_messages, fitted_memory_blocks, input_text)
        > max_context_chars
        and len(fitted_messages) > core_message_count
    ):
        del fitted_messages[core_message_count]

    while (
        _estimate_context_chars(fitted_messages, fitted_memory_blocks, input_text)
        > max_context_chars
        and fitted_memory_blocks
    ):
        fitted_memory_blocks.pop()

    while (
        _estimate_context_chars(fitted_messages, fitted_memory_blocks, input_text)
        > max_context_chars
        and len(fitted_messages) > 1
    ):
        fitted_messages.pop()

    return fitted_messages, fitted_memory_blocks


_INPUT_TRUNCATION_MARKER = "\n…[input truncated to fit the context budget]"


def _cap_input_text(input_text: str, max_chars: int) -> str:
    """Bound the user input to the context budget with a visible marker (FIX-07).

    A non-positive budget or an already-small input is returned unchanged; a
    budget smaller than the marker still yields a bounded (marker-only) string."""

    if max_chars <= 0 or len(input_text) <= max_chars:
        return input_text
    keep = max(0, max_chars - len(_INPUT_TRUNCATION_MARKER))
    return (input_text[:keep] + _INPUT_TRUNCATION_MARKER)[:max_chars]


def _format_compiled_memory_context(compiled: CompiledMemoryContext) -> str | None:
    if not compiled.selected_items:
        return None

    lines = ["Compiled memory:"]
    for item in compiled.selected_items:
        title = _compiled_prompt_field(item.title) if item.title else "(untitled)"
        claim = _compiled_prompt_field(item.claim)
        evidence_count = _compiled_evidence_count(item.evidence_count)
        lines.extend(
            [
                f"- title: {title}",
                f"  claim: {claim}",
                f"  evidence_count: {evidence_count}",
            ]
        )
    return "\n".join(lines)


def _compiled_memory_diagnostics(
    *,
    enabled: bool,
    compiler_available: bool,
    attempted: bool,
    section_present: bool,
    selected_count: int,
    skipped_count: int,
    fail_closed: bool,
    failure_category: str | None,
    skipped_categories: dict[str, int],
) -> CompiledMemoryDiagnostics:
    return CompiledMemoryDiagnostics(
        compiled_memory_enabled=enabled,
        compiler_available=compiler_available,
        compiled_memory_attempted=attempted,
        compiled_memory_section_present=section_present,
        selected_count=selected_count,
        skipped_count=skipped_count,
        fail_closed=fail_closed,
        failure_category=failure_category,
        skipped_categories=dict(sorted(skipped_categories.items())),
    )


def _compiled_memory_skipped_categories(
    compiled: CompiledMemoryContext,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in compiled.skipped_items:
        reason = str(getattr(item, "reason_skipped", "")).strip()
        category = (
            reason
            if reason in _SAFE_COMPILED_MEMORY_SKIPPED_CATEGORIES
            else "other"
        )
        counts[category] = counts.get(category, 0) + 1
    return counts


def _finalize_compiled_memory_diagnostics(
    diagnostics: CompiledMemoryDiagnostics,
    final_context_messages: list[BrainMessage],
) -> CompiledMemoryDiagnostics:
    section_present = any(
        message.metadata.get("kind") == "compiled_memory"
        for message in final_context_messages
    )
    return _compiled_memory_diagnostics(
        enabled=diagnostics.compiled_memory_enabled,
        compiler_available=diagnostics.compiler_available,
        attempted=diagnostics.compiled_memory_attempted,
        section_present=section_present,
        selected_count=diagnostics.selected_count if section_present else 0,
        skipped_count=diagnostics.skipped_count,
        fail_closed=diagnostics.fail_closed,
        failure_category=diagnostics.failure_category,
        skipped_categories=diagnostics.skipped_categories,
    )


def _compiled_prompt_field(value: Any) -> str:
    redacted = redact_secret_text(str(value))
    normalized = " ".join(redacted.split())
    return normalized or "(empty)"


def _default_memory_compiler_config() -> MemoryCompilerConfig:
    return MemoryCompilerConfig()


def _compiled_evidence_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _estimate_context_chars(
    messages: list[BrainMessage],
    memory_blocks: list[Any],
    input_text: str,
) -> int:
    memory_chars = sum(len(str(block.title)) + len(str(block.body)) for block in memory_blocks)
    message_chars = sum(len(message.content) for message in messages)
    return len(input_text) + memory_chars + message_chars


def _stable_snapshot_for_request_metadata(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "created_at"}


def _config_int(config: Any | None, path: tuple[str, str], default: int | None) -> int | None:
    section_name, attr_name = path
    section = getattr(config, section_name, None)
    value = getattr(section, attr_name, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ContextBuilderError(f"Config value {section_name}.{attr_name} must be an integer.") from exc


def _config_bool(config: Any | None, path: tuple[str, str], default: bool) -> bool:
    section_name, attr_name = path
    section = getattr(config, section_name, None)
    value = getattr(section, attr_name, default)
    return bool(default if value is None else value)


def _normalize_compiled_memory_session_profiles(
    scopes: Iterable[tuple[str, str]] | None,
) -> frozenset[tuple[str, str]]:
    if scopes is None:
        return frozenset()

    normalized: set[tuple[str, str]] = set()
    for scope in scopes:
        if (
            not isinstance(scope, (list, tuple))
            or len(scope) != 2
        ):
            raise ContextBuilderError(
                "compiled_memory_enabled_session_profiles entries must be "
                "(session_id, persona_profile) pairs."
            )
        session_id, persona_profile = scope
        normalized.add(
            (
                _required_text(session_id, "compiled memory session_id"),
                _required_text(persona_profile, "compiled memory persona_profile"),
            )
        )
    return frozenset(normalized)


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ContextBuilderError(f"{label} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ContextBuilderError(f"{label} must be a non-empty string.")
    return normalized


def _truncate(value: str, max_chars: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


__all__ = [
    "CompiledMemoryDiagnostics",
    "ContextBuildResult",
    "ContextBuilder",
    "ContextBuilderError",
    "MemoryCompiler",
]
