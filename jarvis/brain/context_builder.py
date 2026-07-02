"""Build stateless BrainRequest objects from Jarvis-owned state."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.brain.base import BrainMessage, BrainRequest
from jarvis.logging import get_logger

from ..memory.manager import MemoryManager


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONA_PATH = REPO_ROOT / "config" / "persona" / "jarvis.md"
DEFAULT_CONTEXT_BUDGET_CHARS = 24000
JOB_PROMPT_PREVIEW_CHARS = 120

PERSONA_PROFILE_SETTING_KEY = "persona.profile"
DEFAULT_PERSONA_PROFILE = "default"
# Conservative file names only: the profile is a settings-supplied value, so
# anything that could escape the persona directory is rejected outright.
_PERSONA_PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")

_LOGGER = get_logger("brain.context_builder")


class ContextBuilderError(Exception):
    """Raised when Jarvis-owned context cannot be assembled."""


@dataclass(frozen=True)
class ContextBuildResult:
    request: BrainRequest
    context_snapshot: dict[str, Any]


class ContextBuilder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        config: Any | None = None,
        persona_path: Path | None = None,
        memory_manager: MemoryManager | None = None,
        event_store: Any | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._config = config
        self._persona_path = persona_path or DEFAULT_PERSONA_PATH
        self._memory_manager = memory_manager or MemoryManager(conn)
        self._event_store = event_store
        self._now = now or utc_now_iso

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
    ) -> ContextBuildResult:
        normalized_turn_id = _required_text(turn_id, "turn_id")
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        if not isinstance(input_text, str):
            raise ContextBuilderError("input_text must be a string.")

        budget = self._resolve_context_budget(max_context_chars)
        request_settings = self._build_settings(settings)
        persona_profile = self._resolve_persona_profile(request_settings)
        core_messages = self._build_core_messages(runtime_state, persona_profile)
        recent_messages = self._build_recent_turn_messages(
            normalized_conversation_id,
            recent_turn_limit,
            exclude_turn_id=normalized_turn_id,
        )
        active_jobs = self._active_worker_jobs()
        job_message = self._build_job_message(active_jobs)
        if job_message is not None:
            core_messages.append(job_message)

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
            "provider_sessions_are_memory": False,
            "created_at": self._now(),
        }

        request = BrainRequest(
            turn_id=normalized_turn_id,
            conversation_id=normalized_conversation_id,
            input_text=input_text,
            context_messages=messages,
            memory_blocks=brain_memory_blocks,
            settings=request_settings,
            metadata={"context_snapshot": _stable_snapshot_for_request_metadata(snapshot)},
        )
        return ContextBuildResult(request=request, context_snapshot=snapshot)

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

        settings.update(self._read_settings_table())
        if explicit_settings is not None:
            if not isinstance(explicit_settings, Mapping):
                raise ContextBuilderError("settings must be a mapping.")
            settings.update(dict(explicit_settings))

        settings["provider_sessions_are_memory"] = False
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
                raise ContextBuilderError(f"Invalid settings JSON for {key}: {exc}") from exc
        return values

    def _build_core_messages(
        self,
        runtime_state: str | None,
        persona_profile: str = DEFAULT_PERSONA_PROFILE,
    ) -> list[BrainMessage]:
        messages: list[BrainMessage] = []
        persona = self._load_persona(persona_profile)
        if persona:
            messages.append(
                BrainMessage(
                    role="system",
                    content=persona,
                    metadata={"kind": "persona", "profile": persona_profile},
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
        return messages

    def _resolve_persona_profile(self, request_settings: Mapping[str, Any]) -> str:
        """Validate the settings-selected persona profile, fail-closed.

        Returns the profile name only when it is a conservative file name AND
        the profile file exists next to the base persona; anything else falls
        back to the base persona so a bad setting can never break a turn.
        """

        requested = request_settings.get(PERSONA_PROFILE_SETTING_KEY)
        if requested is None:
            return DEFAULT_PERSONA_PROFILE
        if not isinstance(requested, str) or not _PERSONA_PROFILE_NAME.fullmatch(requested):
            _LOGGER.warning(
                "Ignoring invalid persona profile setting %r; using the base persona.",
                requested,
            )
            return DEFAULT_PERSONA_PROFILE
        if not (self._persona_path.parent / f"{requested}.md").is_file():
            _LOGGER.warning(
                "Persona profile %r has no file in %s; using the base persona.",
                requested,
                self._persona_path.parent,
            )
            return DEFAULT_PERSONA_PROFILE
        return requested

    def _load_persona(self, persona_profile: str = DEFAULT_PERSONA_PROFILE) -> str | None:
        path = self._persona_path
        if persona_profile != DEFAULT_PERSONA_PROFILE:
            path = self._persona_path.parent / f"{persona_profile}.md"
        try:
            if not path.is_file():
                return None
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ContextBuilderError(f"Could not read persona file {path}: {exc}") from exc
        return content or None

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
                ORDER BY created_at DESC, id DESC
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

        lines = [f"Active worker jobs: {len(active_jobs)}"]
        for job in active_jobs:
            lines.append(
                "- {id} [{status}] {worker_kind}/{type}: {prompt}".format(
                    id=job["id"],
                    status=job["status"],
                    worker_kind=job["worker_kind"],
                    type=job["type"],
                    prompt=_truncate(job["prompt"], JOB_PROMPT_PREVIEW_CHARS),
                )
            )
        return BrainMessage(
            role="system",
            content="\n".join(lines),
            metadata={"kind": "worker_jobs"},
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


__all__ = ["ContextBuildResult", "ContextBuilder", "ContextBuilderError"]
