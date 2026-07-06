"""Runtime supervision and runtime settings projection payloads."""

from __future__ import annotations

import json
import sqlite3
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from datetime import datetime
import importlib.util
import sys

from jarvis.brain.context_builder import (
    DEFAULT_PERSONA_PATH,
    DEFAULT_PERSONA_PROFILE,
    PERSONA_PROFILE_SETTING_KEY,
)
from jarvis.brain.claude_cli_contract import (
    CLAUDE_CLI_COMMAND,
    CLAUDE_CLI_EFFORTS,
    CLAUDE_CLI_INPUT_FORMATS,
    CLAUDE_CLI_OUTPUT_FORMATS,
    CLAUDE_CLI_PERMISSION_MODES,
    ClaudeCliCommandSettings,
    build_claude_cli_command,
)
from jarvis.brain.manager import BrainManagerError
from jarvis.daemon.app import BRAIN_ADAPTER_SETTING_KEY, DaemonApp
from jarvis.runtime.models import RuntimeProcessObservation, RuntimeRisk
from jarvis.runtime.supervisor import OFFICIAL_LABEL
from jarvis.events.types import EventType
from jarvis.security.redaction import redact_secrets
from jarvis.store.db import close_quietly


ROUTE_GROUP = "runtime"

LEGACY_GUIDANCE = [
    "Legacy runtime items are detected only.",
    "no cleanup performed.",
    "Stop legacy components manually only after explicit human approval.",
]

KNOWN_SOURCES = frozenset({"config", "settings", "default", "runtime_detected", "unknown"})
KNOWN_STATUSES = frozenset({"ok", "missing", "invalid", "unsupported", "unknown"})
PERSONA_PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KNOWN_PROVIDER_EFFORT_LEVELS = tuple(CLAUDE_CLI_EFFORTS)
KNOWN_PROVIDER_SUPPORT_UNKNOWN = "unknown"
KNOWN_PROVIDER_SUPPORT_YES = "yes"
KNOWN_PROVIDER_SUPPORT_NO = "no"
CLAUDE_CLI_PROVIDER_IDS = frozenset({"claude_cli", "claude_cli_warm"})
SUPERTONIC_VOICE_MANUAL_DIAGNOSTIC_WARNING = "Supertonic voice list requires manual diagnostic."
VOICE_ENGINE_RELOAD_BLOCKER = "runtime engine reload not implemented in POC; requires restart"
VOICE_GATEWAY_RELOAD_BLOCKER = "runtime gateway reload not implemented in POC; requires restart"
TOOLS_POLICY_RESTART_BLOCKER = "runtime tool/network policy reload not implemented in POC; requires restart"
CANONICAL_PTT_MODES = ("hold", "toggle", "locked")
VOICE_ENGINE_RESTART_ONLY_KEYS = frozenset(
    {
        "voice.default_tts",
        "voice.default_stt",
        "voice.voice_id",
        "voice.voice_profile",
        "voice.profile",
        "voice.speed",
        "voice.rate",
    }
)
TOOLS_INTERNET_APPLY_KEYS = frozenset(
    {
        "tools.enabled",
        "tools.network_enabled",
        "security.network_enabled",
        "security.require_approval_for_network",
        "security.require_approval_for_shell",
        "security.require_approval_for_file_write",
        "security.destructive_tools_enabled",
        "destructive_tools_enabled",
        "provider_tools_enabled",
    }
)

RUNTIME_SETTINGS_APPLY_ALLOWED_KEYS = frozenset(
    {
        "brain.provider",
        "brain.adapter",
        "brain.model",
        "brain.effort",
        "brain.fast",
        "voice.default_tts",
        "voice.default_stt",
        "voice.voice_id",
        "voice.voice_profile",
        "voice.profile",
        "voice.speed",
        "voice.rate",
        "voice.speak_responses",
        "voice.broker_enabled",
        "voice.ptt_mode",
        "voice.merge_window",
        "persona.profile",
    }
    | TOOLS_INTERNET_APPLY_KEYS
)


class RuntimeSettingsApplyError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        apply_status: str = "blocked",
        rejected_keys: list[str] | None = None,
        unchanged_keys: list[str] | None = None,
        requires_restart_keys: list[str] | None = None,
        blockers: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.apply_status = apply_status
        self.rejected_keys = list(rejected_keys or [])
        self.unchanged_keys = list(unchanged_keys or [])
        self.requires_restart_keys = list(requires_restart_keys or [])
        self.blockers = list(blockers or [message])
        self.warnings = list(warnings or [])

PROVIDER_PRESET: dict[str, dict[str, Any]] = {
    "mock": {
        "display_name": "mock/dev",
        "kind": "Developer/Test",
        "supported_efforts": [],
        "fast_support": KNOWN_PROVIDER_SUPPORT_NO,
        "streaming_support": KNOWN_PROVIDER_SUPPORT_NO,
        "tools_support": KNOWN_PROVIDER_SUPPORT_NO,
        "credentials_status": KNOWN_PROVIDER_SUPPORT_YES,
    },
    "claude_cli": {
        "display_name": "Claude CLI",
        "kind": "cli",
        "supported_efforts": list(KNOWN_PROVIDER_EFFORT_LEVELS),
        "fast_support": KNOWN_PROVIDER_SUPPORT_NO,
        "streaming_support": KNOWN_PROVIDER_SUPPORT_YES,
        "tools_support": KNOWN_PROVIDER_SUPPORT_YES,
    },
    "claude_cli_warm": {
        "display_name": "Claude CLI",
        "kind": "cli",
        "supported_efforts": list(KNOWN_PROVIDER_EFFORT_LEVELS),
        "fast_support": KNOWN_PROVIDER_SUPPORT_NO,
        "streaming_support": KNOWN_PROVIDER_SUPPORT_YES,
        "tools_support": KNOWN_PROVIDER_SUPPORT_YES,
    },
    "codex_cli": {
        "display_name": "Codex CLI",
        "kind": "Provider",
        "supported_efforts": [],
        "fast_support": KNOWN_PROVIDER_SUPPORT_NO,
        "streaming_support": KNOWN_PROVIDER_SUPPORT_NO,
        "tools_support": KNOWN_PROVIDER_SUPPORT_YES,
    },
}

LOCAL_RUNTIME_PROBES: tuple[dict[str, Any], ...] = (
    {
        "id": "ollama",
        "label": "Ollama",
        "kind": "Local",
        "commands": ("ollama",),
        "model_env": ("JARVIS_OLLAMA_MODEL", "OLLAMA_MODEL"),
    },
    {
        "id": "mlx",
        "label": "MLX",
        "kind": "Local",
        "commands": ("mlx_lm.generate", "mlx_lm"),
        "python_modules": ("mlx_lm",),
        "base_python_modules": ("mlx",),
        "model_env": ("JARVIS_MLX_MODEL", "MLX_MODEL"),
        "planned": True,
    },
    {
        "id": "llama_cpp_metal",
        "label": "llama.cpp / Metal",
        "kind": "Local",
        "commands": ("llama-server", "llama-cli", "main"),
        "model_env": ("JARVIS_LLAMA_CPP_MODEL", "LLAMA_CPP_MODEL"),
        "planned": True,
    },
    {
        "id": "bielik",
        "label": "Bielik",
        "kind": "Local model",
        "commands": (),
        "model_env": ("JARVIS_BIELIK_MODEL", "BIELIK_MODEL"),
        "planned": True,
    },
    {
        "id": "mistral",
        "label": "Mistral local",
        "kind": "Local model",
        "commands": (),
        "model_env": ("JARVIS_MISTRAL_MODEL", "MISTRAL_MODEL"),
        "planned": True,
    },
)


def _normalize_source(value: str | None) -> str:
    source = str(value).strip() if value is not None else "unknown"
    return source if source in KNOWN_SOURCES else "unknown"


def _normalize_status(status: str | None) -> str:
    normalized = str(status).strip().lower() if status is not None else "unknown"
    return normalized if normalized in KNOWN_STATUSES else "unknown"


def _projection(
    *,
    value: Any,
    effective_value: Any,
    source: str,
    status: str,
    editable_later: bool,
    warning: str | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "effective_value": effective_value,
        "source": _normalize_source(source),
        "status": _normalize_status(status),
        "editable_later": bool(editable_later),
        "warning": warning,
    }


def _safe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str | int | float | bool | list | dict | tuple | None):
        return value
    return str(value)


def _safe_parse_json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _safe_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_turn_id_from_approval_payload(payload_json: Any, metadata_json: Any) -> str | None:
    payload = _safe_parse_json_dict(payload_json)
    metadata = _safe_parse_json_dict(metadata_json)
    for mapping in (payload, metadata):
        raw = mapping.get("turn_id")
        if isinstance(raw, str):
            normalized = raw.strip()
            if normalized:
                return normalized
    return None


def _safe_to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        return int(stripped) if stripped and stripped.isdigit() else None
    return None


def _normalize_turn_source(raw_source: Any) -> str:
    source = str(raw_source).strip().lower() if isinstance(raw_source, str) else ""
    if source == "api":
        return "text"
    if source in {"text", "voice", "panel"}:
        return source
    if source == "cli":
        return "text"
    return "unknown"


def _normalize_new_turn_source(raw_source: Any) -> str | None:
    source = str(raw_source).strip().lower() if isinstance(raw_source, str) else ""
    if not source:
        return None
    if source == "ptt":
        return "PTT"
    if source in {"voice", "barge_in"}:
        return "voice"
    return source


def _empty_barge_in_snapshot() -> dict[str, Any]:
    return {
        "interrupted_previous_response": False,
        "interruption_attributed_to_turn_id": None,
        "interrupted_turn_id": None,
        "cancelled_speech_id": None,
        "cancellation_reason": None,
        "interruption_reason": None,
        "previous_turn_id": None,
        "new_turn_source": None,
        "interruption_source": None,
    }


def _normalized_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _barge_in_snapshot_from_payload(
    payload: dict[str, Any],
    *,
    event_turn_id: Any = None,
) -> dict[str, Any]:
    attributed_turn_id = _normalized_optional_text(payload.get("turn_id")) or _normalized_optional_text(
        event_turn_id
    )
    if attributed_turn_id is None:
        return _empty_barge_in_snapshot()

    snapshot = _empty_barge_in_snapshot()
    snapshot["interrupted_previous_response"] = True
    snapshot["interruption_attributed_to_turn_id"] = attributed_turn_id
    snapshot["interrupted_turn_id"] = attributed_turn_id
    snapshot["previous_turn_id"] = attributed_turn_id

    cancelled_speech_id = _normalized_optional_text(payload.get("request_id"))
    if cancelled_speech_id is not None:
        snapshot["cancelled_speech_id"] = cancelled_speech_id

    reason = _normalized_optional_text(payload.get("reason"))
    if reason is not None:
        snapshot["cancellation_reason"] = reason
        snapshot["interruption_reason"] = reason

    interruption_source = _normalize_new_turn_source(payload.get("interruption_source"))
    if interruption_source is not None:
        snapshot["new_turn_source"] = interruption_source
        snapshot["interruption_source"] = interruption_source

    return snapshot


def _barge_in_attributed_turn_id(snapshot: dict[str, Any]) -> str | None:
    for key in (
        "interruption_attributed_to_turn_id",
        "interrupted_turn_id",
        "previous_turn_id",
    ):
        attributed = _normalized_optional_text(snapshot.get(key))
        if attributed is not None:
            return attributed
    return None


def _barge_in_matches_turn(snapshot: dict[str, Any], turn_id: Any) -> bool:
    normalized_turn_id = _normalized_optional_text(turn_id)
    if normalized_turn_id is None:
        return False
    return _barge_in_attributed_turn_id(snapshot) == normalized_turn_id


def _safe_has_credential(*vars_: str) -> str:
    for var_name in vars_:
        if str(os.environ.get(var_name, "")).strip():
            return KNOWN_PROVIDER_SUPPORT_YES
    return KNOWN_PROVIDER_SUPPORT_NO


def _safe_path_exists(path: str | None) -> bool:
    if not path:
        return False
    try:
        expanded = Path(os.path.expanduser(str(path))).expanduser()
        return expanded.exists()
    except Exception:
        return False


def _safe_is_executable(path: str | None) -> tuple[str, str | None, bool]:
    candidate = (path or "").strip()
    if not candidate:
        return ("missing", None, False)

    expanded = str(Path(os.path.expanduser(candidate)))
    expanded_path = Path(expanded)
    if expanded_path.is_file() and os.access(expanded, os.X_OK):
        return ("ok", str(expanded_path), True)

    which_hit = shutil.which(candidate)
    if which_hit:
        which_path = Path(which_hit)
        if which_path.is_file() and os.access(str(which_hit), os.X_OK):
            return ("ok", str(which_path), True)

    return ("missing", expanded, False)


def _safe_cli_probe(command: str, args: list[str], *, timeout: float = 2.0) -> tuple[int | None, str, str | None]:
    status, resolved, exists = _safe_is_executable(command)
    if status != "ok" or not exists:
        return None, "", "command missing"
    try:
        proc = subprocess.run(
            [resolved or command, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "", "probe timed out"
    except OSError as exc:
        return None, "", redact_secrets(str(exc))
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return proc.returncode, redact_secrets(output.strip()), None


def _safe_probe_cli_version(command: str) -> tuple[str | None, str, str | None]:
    returncode, output, error = _safe_cli_probe(command, ["--version"])
    if error:
        return None, "unknown", error
    if returncode == 0 and output:
        return output.splitlines()[0].strip(), "ok", None
    return None, "unknown", "version probe returned no safe version"


def _safe_probe_claude_auth_status(command: str) -> tuple[str, str | None]:
    returncode, output, error = _safe_cli_probe(command, ["auth", "status"])
    if error:
        return "unknown", error
    normalized = output.lower()
    if returncode == 0 and any(token in normalized for token in ("logged in", "authenticated", "valid")):
        return "logged_in", None
    if any(
        token in normalized
        for token in (
            "not logged in",
            "not authenticated",
            "login required",
            "no active account",
            "missing",
            "unauthorized",
        )
    ):
        return "missing", None
    if returncode != 0:
        return "unknown", "auth status probe returned a non-zero exit code"
    return "unknown", "auth status probe output was not recognized"


def _safe_probe_supertonic_binary(explicit: str) -> tuple[str, str | None, str | None]:
    candidate_paths = [explicit]
    try:
        candidate_paths.append(str(Path(sys.executable).parent / "supertonic"))
    except Exception:
        pass
    which_hit = shutil.which("supertonic")
    if which_hit:
        candidate_paths.append(which_hit)

    last_error: str | None = None
    for candidate in candidate_paths:
        if not candidate:
            continue
        status, resolved, exists = _safe_is_executable(candidate)
        if status == "ok":
            return "ok", resolved, None
        if resolved is not None:
            last_error = f"supertonic binary {resolved!r} is not executable."
    return (
        "missing",
        None,
        last_error or "supertonic binary not found (set voice.supertonic_binary or install it).",
    )


def _safe_probe_tts_binary(tts_engine: str, supertonic_binary: str) -> tuple[str, str | None, str | None]:
    normalized = str(tts_engine or "").strip().lower().replace("-", "_")
    if normalized in {"", "mock"}:
        return "ok", None, None
    if normalized == "supertonic":
        return _safe_probe_supertonic_binary(supertonic_binary)
    return KNOWN_PROVIDER_SUPPORT_UNKNOWN, None, None


def _parse_supertonic_voices(raw: str) -> set[str]:
    voices: set[str] = set()
    for token in re.findall(r"\b[FM]\d+\b", (raw or "").upper()):
        voices.add(token.upper())
    return voices


def _safe_probe_supertonic_voice(binary_path: str | None, voice: str | None) -> tuple[str, str | None, str | None]:
    normalized_voice = (voice or "").strip().upper()
    if not normalized_voice:
        return ("missing", None, "Supertonic profile is selected but supertonic_voice is missing.")
    if not binary_path:
        return ("missing", None, "Cannot validate supertonic_voice because TTS binary is unavailable.")
    try:
        proc = subprocess.run(
            [binary_path, "list-voices"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ("unknown", None, f"Timed out while validating supertonic voices ({exc})")
    except OSError as exc:
        return ("missing", None, f"Failed to run supertonic for voice validation: {exc}")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        return ("unknown", None, f"supertonic list-voices failed: {stderr or 'non-zero exit code'}")

    available = _parse_supertonic_voices(proc.stdout or "")
    if not available:
        return ("unknown", None, "Could not parse supertonic voice list output.")
    if normalized_voice in available:
        return ("ok", None, None)
    return (
        "missing",
        None,
        f"Configured supertonic voice {normalized_voice!r} is not available: {', '.join(sorted(available))}",
    )


def _supertonic_voice_status_without_probe(voice: str | None) -> tuple[str, str | None, str | None]:
    normalized_voice = (voice or "").strip()
    if not normalized_voice:
        return ("missing", None, "Supertonic profile is selected but supertonic_voice is missing.")
    return ("unknown", None, SUPERTONIC_VOICE_MANUAL_DIAGNOSTIC_WARNING)


def _safe_probe_playback_binary(explicit: str) -> tuple[str, str | None, str | None]:
    status, resolved, _ = _safe_is_executable(explicit)
    if status == "ok":
        return status, resolved, None
    if explicit:
        return (
            "missing",
            None,
            f"Configured playback binary {explicit!r} is not executable.",
        )
    status, resolved, _ = _safe_is_executable("play")
    if status == "ok":
        return "ok", resolved, None
    return (
        "missing",
        None,
        "Configured playback binary is missing or not executable.",
    )


def _safe_probe_recorder_binary(explicit: str | None, recorder_mode: str) -> tuple[str, str | None, str | None]:
    normalized = str(recorder_mode or "").strip().lower()
    if normalized != "sox":
        return "ok", None, None
    status, resolved, _ = _safe_is_executable(explicit)
    if status == "ok":
        return "ok", resolved, None
    if explicit:
        return (
            "missing",
            None,
            f"Configured recorder binary {explicit!r} is not executable.",
        )
    status, resolved, _ = _safe_is_executable("sox")
    if status == "ok":
        return "ok", resolved, None
    return ("missing", None, "sox recorder binary not found in configuration or PATH.")


def _safe_probe_stt_package(stt_engine: str) -> tuple[str, str | None]:
    normalized = str(stt_engine or "").strip().lower()
    if normalized in {"", "mock"}:
        return "ok", None
    if normalized in {"mlx_whisper", "mlx-whisper"}:
        try:
            return (
                KNOWN_PROVIDER_SUPPORT_YES if importlib.util.find_spec("mlx_whisper") else KNOWN_PROVIDER_SUPPORT_NO,
                "mlx_whisper",
            )
        except Exception:
            return KNOWN_PROVIDER_SUPPORT_NO, "mlx_whisper"
    return KNOWN_PROVIDER_SUPPORT_UNKNOWN, normalized


def _safe_probe_model_path(model: str | None) -> tuple[str, str | None, str | None]:
    raw = (model or "").strip()
    if not raw:
        return "missing", None, "STT model is not configured."
    if _safe_is_local_path(raw):
        if _safe_path_exists(raw):
            return "ok", str(Path(os.path.expanduser(raw))), None
        return "missing", None, f"Configured STT model path does not exist: {raw!r}."
    return "unknown", raw, None


def _safe_is_local_path(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    if raw.startswith("~"):
        return True
    if raw.startswith(".") or raw.startswith("/") or raw.startswith("\\"):
        return True
    return "/" in raw or "\\" in raw


def _safe_probe_runtime_voice_dir(runtime_dir: str | None) -> tuple[str, str | None]:
    if not runtime_dir:
        return "missing", None
    runtime_voice_dir = Path(os.path.expanduser(str(runtime_dir))).expanduser() / "voice"
    if runtime_voice_dir.is_dir():
        return "ok", str(runtime_voice_dir)
    return "missing", str(runtime_voice_dir)


def _safe_probe_network_capability() -> tuple[str, str | None]:
    return (
        KNOWN_PROVIDER_SUPPORT_YES if shutil.which("curl") or shutil.which("wget") else KNOWN_PROVIDER_SUPPORT_NO,
        "curl" if shutil.which("curl") else ("wget" if shutil.which("wget") else None),
    )


def _safe_read_settings(app: DaemonApp) -> dict[str, dict[str, Any]]:
    conn = app._connect_existing()
    try:
        rows = conn.execute("SELECT key, value_json, source FROM settings ORDER BY key").fetchall()
    except Exception:
        return {}
    finally:
        close_quietly(conn)

    settings: dict[str, dict[str, Any]] = {}
    for key, value_json, source in rows:
        setting_key = str(key)
        parsed = None
        status = "ok"
        try:
            parsed = json.loads(str(value_json))
        except (TypeError, json.JSONDecodeError):
            status = "invalid"
            parsed = None
        settings[setting_key] = {
            "source": _normalize_source("settings"),
            "raw_source": source,
            "raw_value": _safe_json(value_json),
            "value": parsed,
            "status": status,
        }
    return settings


def _read_setting(settings: dict[str, dict[str, Any]], key: str) -> tuple[Any, str]:
    entry = settings.get(key)
    if entry is None:
        return None, "missing"
    return entry.get("value"), _normalize_status(entry.get("status", "unknown"))


def _settings_value(settings: dict[str, dict[str, Any]], key: str) -> tuple[Any, str]:
    return _read_setting(settings, key)


def _evaluate_model_setting(
    *,
    requested: Any,
    requested_status: str,
    current: bool,
    default_model: str | None,
    supported_models: list[str],
) -> tuple[Any, str, str | None]:
    if not current:
        if requested is None:
            return default_model, "ok", None
        return requested, "unknown", "Model is evaluated only for current adapter."

    if requested_status == "missing":
        return default_model, "missing", "No model setting stored for current adapter."
    if requested_status != "ok":
        return default_model, requested_status, "Stored model value is invalid."
    if requested is None:
        return default_model, "missing", "No model setting stored for current adapter."
    if not isinstance(requested, str) or not requested.strip():
        return default_model, "invalid", "Model must be a non-empty string."
    if not supported_models:
        if default_model:
            return default_model, "unknown", "Current model support is unknown for this provider."
        return default_model, "invalid", "No supported model is known for this provider."
    if requested not in supported_models:
        return default_model, "invalid", "Current model is not supported by the selected provider."
    return requested, "ok", None


def _evaluate_effort_setting(
    *,
    requested: Any,
    requested_status: str,
    known_values: list[str],
    current: bool,
    provider_support: str,
) -> tuple[Any, str, str | None]:
    if not current:
        if requested is None:
            return None, "ok", None
        return requested, "unknown", "Effort is evaluated only for current adapter."

    if requested_status == "missing":
        return None, "missing", "No effort setting stored for current adapter."
    if requested_status != "ok":
        return requested, _normalize_status(requested_status), "Stored effort value is invalid."
    if not known_values:
        if provider_support == KNOWN_PROVIDER_SUPPORT_NO:
            if requested is None:
                return None, "ok", None
            return requested, "invalid", "Provider does not support effort."
        if provider_support == KNOWN_PROVIDER_SUPPORT_UNKNOWN:
            if requested is None:
                return None, "missing", "Effort support is unknown for this provider."
            if isinstance(requested, str) and requested.strip():
                return requested, "unknown", "Effort support is unknown for this provider."
            return requested, "invalid", "Effort must be a string."
        if requested is None:
            return None, "missing", "No effort setting stored for current adapter."
        return requested, "unknown", "Effort support is unknown for this provider."

    if not isinstance(requested, str):
        return requested, "invalid", "Effort must be a string."
    normalized = requested.strip()
    if not normalized:
        return requested, "invalid", "Effort cannot be empty."
    if normalized not in known_values:
        return requested, "invalid", f"Effort {normalized!r} is not supported."
    return normalized, "ok", None


def _evaluate_fast_setting(
    *,
    requested: Any,
    requested_status: str,
    provider_support: str,
    current: bool,
) -> tuple[Any, str, str | None]:
    if not current:
        if requested is None:
            return None, "ok", None
        return requested, "unknown", "Fast is evaluated only for current adapter."

    if requested_status == "missing":
        return None, "missing", "No fast setting stored for current adapter."
    if requested_status != "ok":
        return requested, _normalize_status(requested_status), "Stored fast value is invalid."

    if provider_support == KNOWN_PROVIDER_SUPPORT_NO:
        if isinstance(requested, bool):
            return requested, "invalid", "Provider does not support fast mode."
        return requested, "invalid", "Fast must be a boolean."

    if provider_support == KNOWN_PROVIDER_SUPPORT_UNKNOWN:
        if requested is None:
            return None, "missing", "Fast support is unknown for this provider."
        if isinstance(requested, bool):
            return requested, "unknown", "Fast support is unknown for this provider."
        return requested, "invalid", "Fast must be a boolean."

    if isinstance(requested, bool):
        return requested, "ok", None
    return requested, "invalid", "Fast must be a boolean."


def _latest_provider_error(app: DaemonApp, provider_name: str) -> tuple[str | None, str]:
    try:
        events = app._require_event_store().latest(limit=50)
    except Exception:
        return None, "unknown"

    for event in events:
        if event.type != EventType.BRAIN_FAILED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("adapter") != provider_name:
            continue
        raw_error = payload.get("error")
        if not isinstance(raw_error, str):
            return None, "ok"
        return raw_error, "ok"
    return None, "ok"


def _provider_command_projection(
    *,
    provider_name: str,
    adapter: Any,
) -> dict[str, Any]:
    if provider_name == "mock":
        return _projection(
            value=KNOWN_PROVIDER_SUPPORT_YES,
            effective_value=KNOWN_PROVIDER_SUPPORT_YES,
            source="runtime_detected",
            status="ok",
            editable_later=False,
        )

    command = getattr(adapter, "command", None)
    if not isinstance(command, str) or not command.strip():
        return _projection(
            value=KNOWN_PROVIDER_SUPPORT_UNKNOWN,
            effective_value=KNOWN_PROVIDER_SUPPORT_UNKNOWN,
            source="runtime_detected",
            status="unknown",
            editable_later=False,
            warning="Provider command is not configured.",
        )

    command = command.strip()
    if shutil.which(command) or Path(command).is_file():
        status = "ok"
        value = KNOWN_PROVIDER_SUPPORT_YES
        warning = None
    else:
        status = "invalid"
        value = KNOWN_PROVIDER_SUPPORT_NO
        warning = f"Provider command is not available: {command!r}."

    return _projection(
        value=value,
        effective_value=value,
        source="runtime_detected",
        status=status,
        editable_later=False,
        warning=warning,
    )


def _provider_credentials_projection(*, provider_name: str) -> dict[str, Any]:
    warning = (
        "Mock provider does not require external credentials."
        if provider_name == "mock"
        else "Credential status is not exposed by safe runtime checks."
    )
    return _projection(
        value=KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        effective_value=KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        source="runtime_detected",
        status="unknown",
        editable_later=False,
        warning=warning,
    )


def _claude_cli_command_settings(*, adapter: Any, config: Any) -> ClaudeCliCommandSettings:
    adapter_settings = getattr(adapter, "command_settings", None)
    if callable(adapter_settings):
        return adapter_settings()
    raw_stream_args = getattr(adapter, "stream_args", None)
    if raw_stream_args is None:
        raw_stream_args = getattr(config, "stream_args", None)
    return ClaudeCliCommandSettings(
        command=str(
            getattr(adapter, "command", None)
            or getattr(config, "command", None)
            or CLAUDE_CLI_COMMAND
        ),
        args=list(getattr(adapter, "args", None) or getattr(config, "args", None) or ["-p"]),
        model=str(getattr(adapter, "default_model", None) or getattr(config, "model", None) or ""),
        effort=str(getattr(adapter, "effort", None) or getattr(config, "effort", None) or ""),
        permission_mode=str(
            getattr(adapter, "permission_mode", None)
            or getattr(config, "permission_mode", None)
            or ""
        ),
        output_format=str(
            getattr(adapter, "output_format", None)
            or getattr(config, "output_format", None)
            or ""
        ),
        input_format=str(
            getattr(adapter, "input_format", None)
            or getattr(config, "input_format", None)
            or ""
        ),
        tools=list(
            getattr(adapter, "tools", None)
            or getattr(config, "tools", None)
            or []
        ),
        allowed_tools=list(
            getattr(adapter, "allowed_tools", None)
            or getattr(config, "allowed_tools", None)
            or []
        ),
        disallowed_tools=list(
            getattr(adapter, "disallowed_tools", None)
            or getattr(config, "disallowed_tools", None)
            or []
        ),
        mcp_config_path=str(
            getattr(adapter, "mcp_config_path", None)
            or getattr(config, "mcp_config_path", None)
            or ""
        ),
        strict_mcp_config=getattr(
            adapter,
            "strict_mcp_config",
            getattr(config, "strict_mcp_config", None),
        ),
        stream_args=list(raw_stream_args) if raw_stream_args is not None else None,
    )


def _claude_cli_contract(
    *,
    app: DaemonApp,
    adapter_name: str,
    adapter: Any,
    current: bool,
    supported_models: list[str],
    requested_model: Any,
    requested_model_status: str,
    requested_effort: Any,
    requested_effort_status: str,
    command_projection: dict[str, Any],
) -> dict[str, Any]:
    config = getattr(getattr(app.config, "brain", None), "claude_cli", None)
    command_settings = _claude_cli_command_settings(adapter=adapter, config=config)
    requested_model_text = str(requested_model).strip() if isinstance(requested_model, str) else ""
    requested_effort_text = str(requested_effort).strip() if isinstance(requested_effort, str) else ""
    command_contract = build_claude_cli_command(
        command_settings,
        runtime_model=requested_model_text if current and requested_model_status == "ok" else None,
        runtime_effort=requested_effort_text if current and requested_effort_status == "ok" else None,
        streaming=True,
    )
    command_found = _support_bool(command_projection) is True
    command_status = "found" if command_found else "missing"
    version = None
    version_status = "unknown"
    version_warning = None
    auth_status = "unknown"
    auth_warning = None
    if command_found:
        version, version_status, version_warning = _safe_probe_cli_version(command_contract.command)
        auth_status, auth_warning = _safe_probe_claude_auth_status(command_contract.command)

    apply_semantics = "next_turn"
    blocker = None
    if adapter_name == "claude_cli_warm":
        apply_semantics = "requires_new_session"
        blocker = "Claude warm CLI keeps a provider process; changes require a new provider session."
    elif not command_found:
        apply_semantics = "not_apply_capable"
        blocker = f"Claude CLI command is missing: {command_settings.command!r}."
    elif auth_status == "missing":
        apply_semantics = "not_apply_capable"
        blocker = "Claude CLI auth is missing; run claude auth login outside Jarvis."
    elif auth_status != "logged_in":
        apply_semantics = "not_apply_capable"
        blocker = "Claude CLI auth status is unknown; run claude auth status outside Jarvis."
    elif command_contract.permission_mode == "auto":
        apply_semantics = "not_apply_capable"
        blocker = "Claude CLI permission mode auto is not apply-capable until Jarvis can prove Claude Code auto-mode eligibility."
    elif command_contract.permission_mode == "bypassPermissions":
        apply_semantics = "not_apply_capable"
        blocker = "Claude CLI permission mode bypassPermissions is not apply-capable from the panel."

    warnings = [
        warning
        for warning in (version_warning, auth_warning)
        if warning
    ]
    return {
        "provider_id": "claude_cli",
        "label": "Claude CLI",
        "kind": "cli",
        "transport": "subprocess",
        "command": command_contract.command or CLAUDE_CLI_COMMAND,
        "command_status": command_status,
        "auth_status": auth_status,
        "version": version,
        "version_status": version_status,
        "selected_model": command_contract.selected_model,
        "effective_model": command_contract.effective_model,
        "model_source": command_contract.model_source if command_found else "unknown",
        "allowed_models": list(
            dict.fromkeys(
                model
                for model in [*supported_models, command_contract.selected_model]
                if model
            )
        ),
        "selected_effort": command_contract.selected_effort,
        "effective_effort": command_contract.effective_effort,
        "effort_source": command_contract.effort_source,
        "allowed_effort_values": list(KNOWN_PROVIDER_EFFORT_LEVELS),
        "fast_supported": KNOWN_PROVIDER_SUPPORT_NO,
        "fast_supported_state": KNOWN_PROVIDER_SUPPORT_NO,
        "permission_mode": command_contract.permission_mode,
        "tools": command_contract.tools,
        "allowed_tools": command_contract.allowed_tools,
        "disallowed_tools": command_contract.disallowed_tools,
        "mcp_config_path": command_contract.mcp_config_path,
        "mcp_config_status": command_contract.mcp_config_status,
        "strict_mcp_config": command_contract.strict_mcp_config,
        "output_format": command_contract.output_format,
        "input_format": command_contract.input_format,
        "streaming_supported": command_contract.streaming_supported,
        "partial_messages_supported": command_contract.partial_messages_supported,
        "hook_events_supported": KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        "apply_semantics": apply_semantics,
        "apply_capable": apply_semantics == "next_turn",
        "apply_semantics_reason": blocker,
        "apply_eligibility": {
            "mode": apply_semantics,
            "next_turn": apply_semantics == "next_turn",
            "reason": blocker,
        },
        "command_preview": command_contract.command_preview,
        "blocker": blocker,
        "contract_warnings": warnings,
    }


def _provider_capabilities_for_adapter(
    app: DaemonApp,
    adapter_name: str,
    manager: Any,
    resolved_adapter: str,
    requested_model: Any,
    requested_model_status: str,
    requested_effort: Any,
    requested_effort_status: str,
    requested_fast: Any,
    requested_fast_status: str,
) -> dict[str, Any]:
    preset = PROVIDER_PRESET.get(adapter_name, {})
    display_name = str(preset.get("display_name", adapter_name))
    kind = "Developer/Test" if adapter_name == "mock" else str(preset.get("kind", "Provider"))
    supported_efforts = list(preset.get("supported_efforts", []))
    fast_support = str(preset.get("fast_support", KNOWN_PROVIDER_SUPPORT_UNKNOWN))
    streaming_support = str(preset.get("streaming_support", KNOWN_PROVIDER_SUPPORT_UNKNOWN))
    tools_support = str(preset.get("tools_support", KNOWN_PROVIDER_SUPPORT_UNKNOWN))

    adapter = None
    adapter_warning: str | None = None
    status = "ok"
    supported_models: list[str] = []
    try:
        adapter = manager.get_adapter(adapter_name)
        available_models = getattr(adapter, "available_models", None)
        if callable(available_models):
            supported_models = list(available_models())
        if not supported_models:
            status = "missing"
    except Exception as exc:
        adapter = None
        status = "invalid"
        adapter_warning = str(exc)

    current = adapter_name == resolved_adapter
    provider_supported = adapter is not None
    configured = provider_supported
    available = provider_supported
    default_model = app.config.brain.default_model if not supported_models else supported_models[0]

    model_source = "runtime_detected"
    model_warning: str | None = None
    if provider_supported and current:
        model_source = "settings" if requested_model_status == "ok" else "default"
        model_value, model_status, model_warning = _evaluate_model_setting(
            requested=requested_model,
            requested_status=requested_model_status,
            current=True,
            default_model=default_model,
            supported_models=supported_models,
        )
    elif provider_supported:
        model_value = default_model
        if supported_models:
            model_status = "ok"
        else:
            model_status = "unknown"
            model_warning = "Supported models are unknown for this provider."
    else:
        model_value = None
        model_status = "missing"
        model_warning = adapter_warning

    effort_support = (
        KNOWN_PROVIDER_SUPPORT_YES
        if supported_efforts
        else KNOWN_PROVIDER_SUPPORT_NO
    )
    if provider_supported and current:
        effort_effective, effort_status, effort_warning = _evaluate_effort_setting(
            requested=requested_effort,
            requested_status=requested_effort_status,
            known_values=supported_efforts,
            current=True,
            provider_support=effort_support,
        )
        fast_value, fast_status, fast_warning = _evaluate_fast_setting(
            requested=requested_fast,
            requested_status=requested_fast_status,
            provider_support=fast_support,
            current=True,
        )
    elif provider_supported:
        effort_effective, effort_status, effort_warning = None, "unknown", None
        fast_value, fast_status, fast_warning = None, "unknown", None
    else:
        effort_effective, effort_status, effort_warning = None, "missing", None
        fast_value, fast_status, fast_warning = None, "missing", None

    context_budget = app.config.brain.context_budget_chars
    latest_error, latest_error_status = _latest_provider_error(app, adapter_name)
    command_projection = _provider_command_projection(
        provider_name=adapter_name,
        adapter=adapter,
    )
    credentials_projection = _provider_credentials_projection(provider_name=adapter_name)
    claude_cli_contract: dict[str, Any] = {}
    if adapter_name in CLAUDE_CLI_PROVIDER_IDS:
        claude_cli_contract = _claude_cli_contract(
            app=app,
            adapter_name=adapter_name,
            adapter=adapter,
            current=current,
            supported_models=supported_models,
            requested_model=requested_model,
            requested_model_status=requested_model_status,
            requested_effort=requested_effort,
            requested_effort_status=requested_effort_status,
            command_projection=command_projection,
        )
        if claude_cli_contract.get("command_status") == "missing":
            available = False
            status = "missing"
            adapter_warning = str(claude_cli_contract.get("blocker") or "Claude CLI command is missing.")
        elif claude_cli_contract.get("auth_status") == "missing":
            available = False
            status = "missing"
            adapter_warning = str(claude_cli_contract.get("blocker") or "Claude CLI auth is missing.")
        elif claude_cli_contract.get("auth_status") != "logged_in":
            available = False
            status = "unknown"
            adapter_warning = str(claude_cli_contract.get("blocker") or "Claude CLI auth status is unknown.")

    return {
        "name": adapter_name,
        "display_name": display_name,
        "kind": kind,
        "configured": configured,
        "available": available,
        "current": current,
        "supported_models": supported_models,
        "current_model": _projection(
            value=model_value,
            effective_value=model_value,
            source=model_source,
            status=_normalize_status(model_status),
            editable_later=False,
            warning=model_warning,
        ),
        "allowed_effort_values": _projection(
            value=supported_efforts if supported_efforts else None,
            effective_value=supported_efforts if supported_efforts else None,
            source="runtime_detected",
            status="ok" if supported_efforts else "unknown",
            editable_later=False,
            warning=None if supported_efforts else "Effort values are unknown for this provider.",
        ),
        "effort": _projection(
            value=effort_effective,
            effective_value=effort_effective,
            source="settings" if current else "runtime_detected",
            status=_normalize_status(effort_status),
            editable_later=False,
            warning=effort_warning,
        ),
        "fast_supported": _projection(
            value=fast_support,
            effective_value=fast_support,
            source="runtime_detected",
            status="ok" if fast_support in {KNOWN_PROVIDER_SUPPORT_YES, KNOWN_PROVIDER_SUPPORT_NO} else "unknown",
            editable_later=False,
            warning=adapter_warning,
        ),
        "fast": _projection(
            value=fast_value,
            effective_value=fast_value,
            source="settings" if current else "runtime_detected",
            status=_normalize_status(fast_status),
            editable_later=False,
            warning=fast_warning,
        ),
        "context_window_chars": _projection(
            value=context_budget,
            effective_value=context_budget,
            source="config",
            status="ok",
            editable_later=False,
            warning=None,
        ),
        "streaming_support": _projection(
            value=streaming_support,
            effective_value=streaming_support,
            source="runtime_detected",
            status="ok" if streaming_support in {KNOWN_PROVIDER_SUPPORT_YES, KNOWN_PROVIDER_SUPPORT_NO} else "unknown",
            editable_later=False,
            warning=adapter_warning,
        ),
        "tools_support": _projection(
            value=tools_support,
            effective_value=tools_support,
            source="runtime_detected",
            status="ok" if tools_support in {KNOWN_PROVIDER_SUPPORT_YES, KNOWN_PROVIDER_SUPPORT_NO} else "unknown",
            editable_later=False,
            warning=adapter_warning,
        ),
        "provider_command_status": _projection(
            value=command_projection["value"],
            effective_value=command_projection["effective_value"],
            source="runtime_detected",
            status=command_projection["status"],
            editable_later=False,
            warning=command_projection["warning"],
        ),
        "provider_credentials_status": _projection(
            value=credentials_projection["value"],
            effective_value=credentials_projection["effective_value"],
            source="runtime_detected",
            status=credentials_projection["status"],
            editable_later=False,
            warning=credentials_projection["warning"],
        ),
        "latest_error": _projection(
            value=latest_error,
            effective_value=latest_error,
            source="runtime_detected",
            status=_normalize_status(latest_error_status),
            editable_later=False,
            warning=None if latest_error_status == "ok" else "Could not determine latest provider error.",
        ),
        "claude_cli_contract": claude_cli_contract or None,
        "warning": adapter_warning,
        "status": status,
    }


def _build_provider_capabilities(
    app: DaemonApp,
    manager: Any,
    adapter_names: list[str],
    settings: dict[str, dict[str, Any]],
    resolved_adapter: str,
) -> list[dict[str, Any]]:
    requested_model, requested_model_status = _settings_value(settings, "model")
    requested_effort, requested_effort_status = _settings_value(settings, "effort")
    requested_fast, requested_fast_status = _settings_value(settings, "fast")

    return [
        _provider_capabilities_for_adapter(
            app,
            adapter_name=adapter_name,
            manager=manager,
            resolved_adapter=resolved_adapter,
            requested_model=requested_model,
            requested_model_status=requested_model_status,
            requested_effort=requested_effort,
            requested_effort_status=requested_effort_status,
            requested_fast=requested_fast,
            requested_fast_status=requested_fast_status,
        )
        for adapter_name in sorted(set(adapter_names + ["mock"]))
    ]


def _resolve_persona_profile(requested: Any) -> tuple[str, str]:
    if requested is None:
        return DEFAULT_PERSONA_PROFILE, "missing"

    if not isinstance(requested, str) or not requested.strip():
        return DEFAULT_PERSONA_PROFILE, "invalid"

    normalized = requested.strip()
    if not PERSONA_PROFILE_PATTERN.fullmatch(normalized):
        return DEFAULT_PERSONA_PROFILE, "invalid"

    profile_path = DEFAULT_PERSONA_PATH.parent / f"{normalized}.md"
    if not profile_path.is_file():
        return DEFAULT_PERSONA_PROFILE, "invalid"

    return normalized, "ok"


def _available_persona_profiles() -> list[str]:
    profiles = [DEFAULT_PERSONA_PROFILE]
    persona_dir = DEFAULT_PERSONA_PATH.parent
    try:
        for path in sorted(persona_dir.glob("*.md")):
            if path == DEFAULT_PERSONA_PATH:
                continue
            profile = path.stem
            if PERSONA_PROFILE_PATTERN.fullmatch(profile):
                profiles.append(profile)
    except Exception:
        return profiles
    return list(dict.fromkeys(profiles))


def _required_apply_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeSettingsApplyError(f"{label} must be a non-empty string.")
    return value.strip()


def _optional_apply_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeSettingsApplyError(f"{label} must be a boolean.")
    return value


def _optional_apply_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeSettingsApplyError(f"{label} must be a number.")
    return float(value)


def _runtime_settings_apply_request(request_payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = request_payload.get("settings")
    if not isinstance(settings, Mapping):
        raise RuntimeSettingsApplyError("Request must include settings JSON object.")
    unknown = sorted(str(key) for key in settings if str(key) not in RUNTIME_SETTINGS_APPLY_ALLOWED_KEYS)
    if unknown:
        raise RuntimeSettingsApplyError(
            f"Unknown runtime setting: {unknown[0]}",
            apply_status="blocked",
            rejected_keys=unknown,
            blockers=[f"Unknown runtime setting: {key}" for key in unknown],
        )
    return {str(key): value for key, value in settings.items()}


def _current_capability_payload(app: DaemonApp) -> dict[str, Any]:
    return get_runtime_settings(app)


def _apply_error(message: str, *, status_code: int = 422) -> RuntimeSettingsApplyError:
    return RuntimeSettingsApplyError(
        message,
        status_code=status_code,
        apply_status="blocked",
        blockers=[message],
    )


def _provider_apply_blocker(
    provider: dict[str, Any],
    *,
    current_provider_id: str | None,
    requested_provider_id: str,
) -> str | None:
    if provider.get("developer_only") and requested_provider_id != current_provider_id:
        return f"Provider {requested_provider_id!r} is Developer/Test only; not apply-capable in POC."
    if not provider.get("available", False):
        blocker = provider.get("blocker")
        if blocker:
            return f"{blocker} Provider {requested_provider_id!r} is not apply-capable in POC."
        return f"Provider {requested_provider_id!r} is unavailable; not apply-capable in POC."
    apply_semantics = provider.get("apply_semantics")
    if apply_semantics and apply_semantics != "next_turn":
        if apply_semantics == "requires_new_session":
            return f"Provider {requested_provider_id!r} requires a new provider session; not apply-capable in POC."
        if apply_semantics == "requires_daemon_restart":
            return f"Provider {requested_provider_id!r} requires daemon restart; not apply-capable in POC."
        return f"Provider {requested_provider_id!r} is not apply-capable in POC."
    if provider.get("auth_status") == "missing":
        return f"Provider {requested_provider_id!r} auth is missing; not apply-capable in POC."
    if _support_bool(provider.get("command_status")) is False:
        return f"Provider {requested_provider_id!r} command is missing; not apply-capable in POC."
    return None


def _brain_provider_for_apply(
    capability_graph: dict[str, Any],
    provider_id: Any,
) -> dict[str, Any]:
    provider_name = _required_apply_text(provider_id, "brain.provider")
    provider = _provider_by_id(capability_graph, provider_name)
    if provider is None:
        raise _apply_error(
            f"Provider {provider_name!r} is not present in capability_graph; not apply-capable in POC."
        )
    current_provider_id = capability_graph.get("brain_capabilities", {}).get("current_provider")
    blocker = _provider_apply_blocker(
        provider,
        current_provider_id=str(current_provider_id) if current_provider_id else None,
        requested_provider_id=provider_name,
    )
    if blocker:
        raise _apply_error(blocker)
    return provider


def _apply_brain_settings(
    app: DaemonApp,
    settings: dict[str, Any],
    capability_graph: dict[str, Any],
) -> list[str]:
    if not any(key.startswith("brain.") for key in settings):
        return []

    applied: list[str] = []
    provider_value = settings.get("brain.provider", settings.get("brain.adapter"))
    if "brain.provider" in settings and "brain.adapter" in settings and settings["brain.provider"] != settings["brain.adapter"]:
        raise RuntimeSettingsApplyError("brain.provider and brain.adapter must match when both are supplied.")

    brain_capabilities = capability_graph.get("brain_capabilities", {})
    target_provider_id = provider_value if provider_value is not None else brain_capabilities.get("current_provider")
    provider = _brain_provider_for_apply(capability_graph, target_provider_id)
    provider_id = str(provider["id"])
    manager = app.brain_manager
    if manager is None:
        raise _apply_error("Brain manager is not initialized; provider is not apply-capable in POC.", status_code=409)
    if provider_id not in set(manager.adapter_names()):
        raise _apply_error(
            f"Provider {provider_id!r} is not registered in BrainManager; not apply-capable in POC."
        )

    settings_updates: dict[str, Any] = {}
    adapter_model_value: str | None = None
    if "brain.model" in settings:
        model = _required_apply_text(settings["brain.model"], "brain.model")
        allowed_models = [
            str(model_node.get("id"))
            for model_node in provider.get("models", [])
            if model_node.get("id") and model_node.get("available", True)
        ]
        if not allowed_models:
            raise _apply_error(
                f"Provider {provider_id!r} has no apply-capable model in POC."
            )
        if model not in allowed_models:
            raise _apply_error(
                f"Model {model!r} is not supported for provider {provider_id!r}."
            )
        settings_updates["model"] = model
        adapter_model_value = model

    if "brain.effort" in settings:
        effort = _required_apply_text(settings["brain.effort"], "brain.effort")
        allowed_efforts = [str(value) for value in provider.get("allowed_effort_values") or []]
        if not allowed_efforts:
            raise _apply_error(
                f"Effort is not apply-capable in POC for provider {provider_id!r}."
            )
        if effort not in allowed_efforts:
            raise _apply_error(
                f"Effort {effort!r} is not supported for provider {provider_id!r}."
            )
        settings_updates["effort"] = effort

    if "brain.fast" in settings:
        fast = _optional_apply_bool(settings["brain.fast"], "brain.fast")
        if not provider.get("fast_supported"):
            raise _apply_error(
                f"Fast is not apply-capable in POC for provider {provider_id!r}."
            )
        settings_updates["fast"] = fast

    if provider_value is not None:
        previous = manager.current_adapter_name
        try:
            manager.switch_adapter(provider_id)
        except BrainManagerError as exc:
            raise _apply_error(
                f"Provider {provider_id!r} switch failed: {exc}; not apply-capable in POC.",
                status_code=409,
            ) from exc
        if app.started and previous != provider_id:
            app._require_event_store().append(
                EventType.BRAIN_SWITCHED,
                "api",
                {"from": previous, "to": provider_id, "persisted": True, "runtime_apply": True},
            )
        app.update_settings({BRAIN_ADAPTER_SETTING_KEY: provider_id})
        applied.append("brain.provider")

    if adapter_model_value is not None:
        try:
            adapter = manager.get_adapter(provider_id)
            if hasattr(adapter, "default_model"):
                setattr(adapter, "default_model", adapter_model_value)
        except BrainManagerError as exc:
            raise _apply_error(
                f"Provider {provider_id!r} model update failed: {exc}; not apply-capable in POC.",
                status_code=409,
            ) from exc

    if settings_updates:
        app.update_settings(settings_updates)
        applied.extend(f"brain.{key}" for key in settings_updates)

    return applied


def _voice_provider_for_apply(
    capability_graph: dict[str, Any],
    provider_id: str,
    provider_type: str,
) -> dict[str, Any]:
    voice_capabilities = capability_graph.get("voice_capabilities", {})
    providers_key = "stt_providers" if provider_type == "stt" else "tts_providers"
    providers = voice_capabilities.get(providers_key, [])
    provider = _voice_provider_by_id(providers if isinstance(providers, list) else [], provider_id)
    if provider is None:
        raise _apply_error(
            f"{provider_type.upper()} provider {provider_id!r} is not present in capability_graph."
        )
    if not provider.get("available", False):
        raise _apply_error(
            f"{provider_type.upper()} provider {provider_id!r} is unavailable; not apply-capable in POC."
        )
    return provider


def _validate_supertonic_voice_requirements(
    app: DaemonApp,
    settings: dict[str, Any],
    capability_graph: dict[str, Any],
    target_tts: str,
) -> None:
    if target_tts != "supertonic":
        return

    tts_provider = _voice_provider_for_apply(capability_graph, target_tts, "tts")
    voice_id = str(settings.get("voice.voice_id", app.config.voice.supertonic_voice) or "").strip()
    profile = str(
        settings.get(
            "voice.voice_profile",
            settings.get("voice.profile", app.config.voice.supertonic_lang),
        )
        or ""
    ).strip()
    if not voice_id:
        raise _apply_error(
            "voice.default_tts=supertonic requires voice_id; no Supertonic API call was made."
        )
    if not profile:
        raise _apply_error(
            "voice.default_tts=supertonic requires voice profile; no Supertonic API call was made."
        )

    allowed_voice_ids = [str(value) for value in tts_provider.get("voice_ids") or []]
    if allowed_voice_ids and voice_id not in allowed_voice_ids:
        raise _apply_error(f"voice.voice_id {voice_id!r} is not supported by TTS provider {target_tts!r}.")
    allowed_profiles = [str(value) for value in tts_provider.get("voice_profiles") or []]
    if allowed_profiles and profile not in allowed_profiles:
        raise _apply_error(f"voice.voice_profile {profile!r} is not supported by TTS provider {target_tts!r}.")


def _apply_voice_and_ptt_settings(
    app: DaemonApp,
    settings: dict[str, Any],
    capability_graph: dict[str, Any],
) -> list[str]:
    voice_keys = [key for key in settings if key.startswith("voice.")]
    if not voice_keys:
        return []

    voice = app.config.voice
    updates: dict[str, Any] = {}

    target_tts = str(settings.get("voice.default_tts", voice.default_tts) or "").strip()
    target_stt = str(settings.get("voice.default_stt", voice.default_stt) or "").strip()
    target_speak = (
        _optional_apply_bool(settings["voice.speak_responses"], "voice.speak_responses")
        if "voice.speak_responses" in settings
        else bool(voice.speak_responses)
    )

    if "voice.default_tts" in settings:
        target_tts = _required_apply_text(settings["voice.default_tts"], "voice.default_tts")
        _voice_provider_for_apply(capability_graph, target_tts, "tts")
        _validate_supertonic_voice_requirements(app, settings, capability_graph, target_tts)

    if "voice.default_stt" in settings:
        target_stt = _required_apply_text(settings["voice.default_stt"], "voice.default_stt")
        _voice_provider_for_apply(capability_graph, target_stt, "stt")

    engine_restart_keys = sorted(key for key in voice_keys if key in VOICE_ENGINE_RESTART_ONLY_KEYS)
    if engine_restart_keys:
        raise RuntimeSettingsApplyError(
            f"{', '.join(engine_restart_keys)}: {VOICE_ENGINE_RELOAD_BLOCKER}",
            status_code=409,
            apply_status="requires_restart",
            rejected_keys=engine_restart_keys,
            requires_restart_keys=engine_restart_keys,
        )

    if "voice.broker_enabled" in settings:
        raise RuntimeSettingsApplyError(
            "voice.broker_enabled is not apply-capable in POC; voice broker lifecycle rebuild is not wired.",
            status_code=409,
            apply_status="blocked",
            rejected_keys=["voice.broker_enabled"],
        )

    if "voice.merge_window" in settings:
        merge_window = _optional_apply_float(settings["voice.merge_window"], "voice.merge_window")
        if merge_window < 0:
            raise RuntimeSettingsApplyError("voice.merge_window must be non-negative.")
        raise RuntimeSettingsApplyError(
            f"voice.merge_window: {VOICE_GATEWAY_RELOAD_BLOCKER}",
            status_code=409,
            apply_status="requires_restart",
            rejected_keys=["voice.merge_window"],
            requires_restart_keys=["voice.merge_window"],
        )

    if target_speak:
        if not target_tts:
            raise _apply_error("voice.speak_responses cannot be enabled because TTS provider is missing.")
        _voice_provider_for_apply(capability_graph, target_tts, "tts")
        _validate_supertonic_voice_requirements(app, settings, capability_graph, target_tts)

    if "voice.speak_responses" in settings:
        updates["speak_responses"] = target_speak

    if "voice.ptt_mode" in settings:
        ptt_mode = _required_apply_text(settings["voice.ptt_mode"], "voice.ptt_mode")
        if ptt_mode not in CANONICAL_PTT_MODES:
            raise _apply_error(
                f"voice.ptt_mode must be one of {', '.join(CANONICAL_PTT_MODES)}."
            )
        updates["ptt_mode"] = ptt_mode

    if not updates:
        return []

    app.config = replace(app.config, voice=replace(voice, **updates))
    if app.context_builder is not None:
        try:
            app.context_builder._config = app.config
        except Exception:
            pass

    applied: list[str] = []
    for key in voice_keys:
        if key == "voice.broker_enabled":
            continue
        if key == "voice.voice_profile":
            applied.append("voice.voice_profile")
        elif key == "voice.profile":
            applied.append("voice.profile")
        elif key == "voice.rate":
            applied.append("voice.rate")
        else:
            applied.append(key)
    return applied


def _apply_persona_settings(app: DaemonApp, settings: dict[str, Any]) -> list[str]:
    if "persona.profile" not in settings:
        return []
    profile = _required_apply_text(settings["persona.profile"], "persona.profile")
    _, status = _resolve_persona_profile(profile)
    if status != "ok":
        raise _apply_error(f"persona.profile {profile!r} is not apply-capable in this POC.")
    app.update_settings({PERSONA_PROFILE_SETTING_KEY: profile})
    return ["persona.profile"]


def _apply_tools_internet_settings(
    app: DaemonApp,
    settings: dict[str, Any],
    capability_graph: dict[str, Any],
) -> list[str]:
    tool_keys = [key for key in settings if key in TOOLS_INTERNET_APPLY_KEYS]
    if not tool_keys:
        return []

    capabilities = capability_graph.get("tools_capabilities", {})
    apply_capabilities = capabilities.get("apply_capabilities")
    if not isinstance(apply_capabilities, dict):
        apply_capabilities = {}
    network_tools = capabilities.get("registered_network_tools")
    network_tool_names = network_tools if isinstance(network_tools, list) else []
    blockers: list[str] = []
    requires_restart_keys: list[str] = []
    for key in sorted(tool_keys):
        if not isinstance(settings[key], bool):
            raise RuntimeSettingsApplyError(
                f"{key} must be a boolean.",
                rejected_keys=[key],
            )
        if key in {"tools.network_enabled", "security.network_enabled"} and settings[key] and not network_tool_names:
            blockers.append("Internet unavailable: no network/search tool registered")
            continue
        capability = apply_capabilities.get(key) if isinstance(apply_capabilities.get(key), dict) else {}
        blocker = capability.get("blocker") or "not apply-capable in POC"
        if capability.get("requires_restart"):
            requires_restart_keys.append(key)
            blockers.append(f"{key}: {blocker}")
        else:
            blockers.append(f"{key}: {blocker}")

    raise RuntimeSettingsApplyError(
        blockers[0],
        status_code=409,
        apply_status="requires_restart" if requires_restart_keys else "blocked",
        rejected_keys=sorted(tool_keys),
        requires_restart_keys=sorted(requires_restart_keys),
        blockers=blockers,
    )


def _append_compatibility_warning(target: list[str], message: str) -> None:
    if not message:
        return
    if message in target:
        return
    target.append(message)


def _current_provider_capability(
    provider_capabilities: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for capability in provider_capabilities or []:
        if not isinstance(capability, dict):
            continue
        if capability.get("current", False):
            return capability
    return None


def _tool_spec_supports_network(spec: Mapping[str, Any]) -> bool:
    risk = str(spec.get("risk") or "").strip().lower()
    if risk == "network":
        return True
    text = f"{spec.get('name') or ''} {spec.get('description') or ''}".lower()
    return any(token in text for token in ("network", "internet", "web", "search", "http", "url", "fetch", "browser"))


def _network_tool_specs(specs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [spec for spec in specs or [] if _tool_spec_supports_network(spec)]


def _collect_voice_runtime_compatibility_warnings(
    *,
    app: DaemonApp,
    settings: dict[str, dict[str, Any]],
    provider_capabilities: list[dict[str, Any]] | None,
    tools_registered: list[dict[str, Any]] | None,
) -> list[str]:
    warnings: list[str] = []

    default_tts = str(app.config.voice.default_tts or "").strip()
    tts_engine = default_tts.lower().replace("-", "_")
    default_stt = str(app.config.voice.default_stt or "").strip()
    stt_engine = default_stt.lower().replace("-", "_")
    voice_enabled = bool(app.config.voice.enabled)

    tts_binary_status, tts_binary, tts_binary_warning = _safe_probe_tts_binary(
        default_tts,
        str(app.config.voice.supertonic_binary or ""),
    )
    stt_package_status, stt_package_name = _safe_probe_stt_package(default_stt)
    stt_model_status, _, stt_model_warning = _safe_probe_model_path(str(app.config.voice.stt_model))
    registered_network_tools = _network_tool_specs(tools_registered)

    if voice_enabled:
        if tts_engine and tts_engine != "mock" and tts_binary_status != "ok":
            _append_compatibility_warning(
                warnings,
                tts_binary_warning or f"TTS engine {default_tts!r} is not available.",
            )
        if stt_engine and stt_engine != "mock" and stt_package_status != KNOWN_PROVIDER_SUPPORT_YES:
            provider_name = stt_package_name or default_stt
            _append_compatibility_warning(
                warnings,
                f"Configured STT package {provider_name!r} is not available.",
            )

        if stt_engine in {"mlx_whisper", "mlxwhisper"} and stt_model_status != "ok":
            if stt_model_status == "missing":
                _append_compatibility_warning(
                    warnings,
                    stt_model_warning or "STT local model is configured but missing.",
                )
            elif stt_model_status == "unknown":
                _append_compatibility_warning(
                    warnings,
                    "STT model reference is treated as non-local for the selected provider.",
                )

        if tts_engine == "supertonic":
            supertonic_voice = str(app.config.voice.supertonic_voice or "").strip()
            supertonic_lang = str(app.config.voice.supertonic_lang or "").strip()
            if not supertonic_voice:
                _append_compatibility_warning(
                    warnings,
                    "Supertonic profile is selected but supertonic_voice is missing.",
                )
            if not supertonic_lang:
                _append_compatibility_warning(
                    warnings,
                    "Supertonic profile is selected but supertonic_lang is missing.",
                )
            if supertonic_voice:
                _append_compatibility_warning(
                    warnings,
                    SUPERTONIC_VOICE_MANUAL_DIAGNOSTIC_WARNING,
                )

        if not app.voice_stt and stt_engine:
            _append_compatibility_warning(warnings, "Voice is enabled but STT engine is unavailable.")
        if not app.voice_broker and tts_engine:
            _append_compatibility_warning(warnings, "Voice is enabled but TTS engine is unavailable.")

        current_provider = _current_provider_capability(provider_capabilities)
        if current_provider:
            current_model = current_provider.get("current_model")
            if isinstance(current_model, dict) and current_model.get("status") == "invalid":
                _append_compatibility_warning(
                    warnings,
                    "Current model configuration is not supported by the selected provider.",
                )
            effort = current_provider.get("effort")
            if isinstance(effort, dict) and effort.get("status") == "invalid":
                _append_compatibility_warning(
                    warnings,
                    "Configured effort value is unsupported by the selected provider.",
                )
            fast = current_provider.get("fast")
            if isinstance(fast, dict) and fast.get("status") == "invalid":
                _append_compatibility_warning(
                    warnings,
                    "Fast mode is unsupported for the selected provider.",
                )

            tools_support = current_provider.get("tools_support")
            if (
                isinstance(tools_support, dict)
                and tools_support.get("value") == KNOWN_PROVIDER_SUPPORT_NO
                and tools_registered is not None
                and tools_registered
            ):
                _append_compatibility_warning(
                    warnings,
                    "Tools are available to be shown, but selected provider does not support tools.",
                )

    elif tts_engine or stt_engine:
        if stt_engine and stt_engine in {"mlx_whisper", "mlxwhisper"} and stt_model_status != "ok":
            if stt_model_status == "missing":
                _append_compatibility_warning(
                    warnings,
                    "Configured STT local model is missing, but voice is disabled.",
                )
        if tts_engine and tts_binary_status != "ok":
            _append_compatibility_warning(
                warnings,
                tts_binary_warning or f"TTS engine {default_tts!r} is not available.",
            )
        if stt_engine and stt_engine != "mock" and stt_package_status != KNOWN_PROVIDER_SUPPORT_YES:
            provider_name = stt_package_name or default_stt
            _append_compatibility_warning(
                warnings,
                f"Configured STT package {provider_name!r} is not available.",
            )

    requested_persona = settings.get(PERSONA_PROFILE_SETTING_KEY, {})
    if requested_persona:
        _, persona_status = _resolve_persona_profile(requested_persona.get("value"))
        if persona_status != "ok":
            _append_compatibility_warning(
                warnings,
                "Requested persona profile is missing; using fallback profile.",
            )

    if getattr(app.config.security, "require_approval_for_network", False) and not registered_network_tools:
        _append_compatibility_warning(
            warnings,
            "network enabled but no network tool registered",
        )

    return warnings


def _runtime_projection(app: DaemonApp) -> dict[str, Any]:
    snapshot = {}
    try:
        snapshot = app.snapshot_state()
    except Exception:
        snapshot = {}
    return {
        "host": _projection(
            value=app.config.daemon.host,
            effective_value=app.config.daemon.host,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "port": _projection(
            value=app.config.daemon.port,
            effective_value=app.config.daemon.port,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "service": _projection(
            value=snapshot.get("service"),
            effective_value=snapshot.get("service"),
            source="runtime_detected",
            status="ok" if snapshot else "unknown",
            editable_later=False,
            warning=None if snapshot else "Runtime snapshot unavailable before app start.",
        ),
        "state": _projection(
            value=snapshot.get("state"),
            effective_value=snapshot.get("state"),
            source="runtime_detected",
            status="ok" if snapshot else "unknown",
            editable_later=False,
            warning=None if snapshot else "Runtime snapshot unavailable before app start.",
        ),
        "started": _projection(
            value=snapshot.get("started"),
            effective_value=snapshot.get("started"),
            source="runtime_detected",
            status="ok" if snapshot else "unknown",
            editable_later=False,
            warning=None if snapshot else "Runtime snapshot unavailable before app start.",
        ),
        "schema_version": _projection(
            value=snapshot.get("schema_version"),
            effective_value=snapshot.get("schema_version"),
            source="runtime_detected",
            status="ok" if snapshot else "unknown",
            editable_later=False,
            warning=None if snapshot else "Runtime snapshot unavailable before app start.",
        ),
        "latest_event_id": _projection(
            value=snapshot.get("latest_event_id"),
            effective_value=snapshot.get("latest_event_id"),
            source="runtime_detected",
            status="ok" if snapshot else "unknown",
            editable_later=False,
            warning=None if snapshot else "Runtime snapshot unavailable before app start.",
        ),
    }


def _brain_projection(app: DaemonApp, settings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    manager = app.brain_manager
    if manager is None:
        return {
            "default_adapter": _projection(
                value=app.config.brain.default_adapter,
                effective_value=app.config.brain.default_adapter,
                source="config",
                status="ok",
                editable_later=False,
            ),
            "current_adapter": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected",
                status="missing",
                editable_later=True,
                warning="Brain manager is not initialized.",
            ),
            "adapters": _projection(
                value=[],
                effective_value=[],
                source="runtime_detected",
                status="missing",
                editable_later=False,
            ),
        }

    persisted = settings.get(BRAIN_ADAPTER_SETTING_KEY)
    persisted_value = persisted["value"] if persisted is not None else None
    persisted_status = persisted.get("status", "missing") if persisted else "missing"
    resolved_adapter = app.config.brain.default_adapter
    adapter_warning = None

    if manager is not None:
        try:
            resolved_adapter = manager.current_adapter_name
        except Exception:
            resolved_adapter = app.config.brain.default_adapter

        try:
            adapter_names = manager.adapter_names()
        except Exception:
            adapter_names = []
    else:
        adapter_names = []

    adapter_status_final = "ok"
    if persisted is None:
        adapter_status_final = "missing"
    elif persisted_status != "ok":
        adapter_status_final = "invalid"
        adapter_warning = "Persisted value cannot be parsed; using runtime adapter."
    elif isinstance(persisted_value, str) and persisted_value in adapter_names:
        resolved_adapter = persisted_value
    elif persisted is not None:
        adapter_status_final = "invalid"
        adapter_warning = "Persisted adapter is not registered; using runtime adapter."

    adapters_payload = []
    adapter_projection_warning = None
    for name in adapter_names:
        adapter = None
        try:
            adapter = manager.get_adapter(name)
            available_models = list(adapter.available_models())
        except Exception as exc:
            available_models = []
            adapter_projection_warning = f"Unable to read adapter metadata for {name!r}: {exc}"
        try:
            streaming = bool(manager.supports_streaming(name))
        except Exception:
            streaming = False
        adapters_payload.append(
            {
                "name": name,
                "provider": type(adapter).__name__ if adapter is not None else "unknown",
                "models": available_models,
                "supports_streaming": streaming,
            }
        )

    provider_capabilities: list[dict[str, Any]]
    try:
        provider_capabilities = _build_provider_capabilities(
            app=app,
            manager=manager,
            adapter_names=adapter_names,
            settings=settings,
            resolved_adapter=resolved_adapter,
        )
    except Exception:
        provider_capabilities = []

    persona_requested = settings.get(PERSONA_PROFILE_SETTING_KEY, {}).get("value")
    resolved_persona, persona_status = _resolve_persona_profile(persona_requested)

    return {
        "default_adapter": _projection(
            value=app.config.brain.default_adapter,
            effective_value=app.config.brain.default_adapter,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "current_adapter": _projection(
            value=persisted_value,
            effective_value=resolved_adapter,
            source="settings" if persisted is not None else "runtime_detected",
            status=adapter_status_final if adapter_status_final != "ok" else "ok",
            editable_later=True,
            warning=adapter_warning,
        ),
        "adapters": _projection(
            value=adapters_payload,
            effective_value=adapters_payload,
            source="runtime_detected",
            status="ok",
            editable_later=False,
            warning=adapter_projection_warning,
        ),
        "providers": _projection(
            value=provider_capabilities,
            effective_value=provider_capabilities,
            source="runtime_detected",
            status="ok",
            editable_later=False,
            warning=None,
        ),
        "default_model": _projection(
            value=app.config.brain.default_model,
            effective_value=app.config.brain.default_model,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "provider_sessions_are_memory": _projection(
            value=app.config.brain.provider_sessions_are_memory,
            effective_value=app.config.brain.provider_sessions_are_memory,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "persona_profile": _projection(
            value=persona_requested,
            effective_value=resolved_persona,
            source="settings" if PERSONA_PROFILE_SETTING_KEY in settings else "default",
            status=persona_status,
            editable_later=True,
            warning=None
            if persona_status == "ok"
            else "Invalid persona profile; using fallback.",
        ),
    }


def _audio_projection(app: DaemonApp) -> dict[str, Any]:
    if not app.started:
        return {
            "enabled": _projection(
                value=app.config.audio.enabled,
                effective_value=app.config.audio.enabled,
                source="config",
                status="ok",
                editable_later=False,
            ),
            "backend": _projection(
                value=app.config.audio.backend,
                effective_value=app.config.audio.backend,
                source="config",
                status="ok",
                editable_later=False,
            ),
            "input_device": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected",
                status="unknown",
                editable_later=False,
                warning="Start required for live audio-device detection.",
            ),
            "output_device": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected",
                status="unknown",
                editable_later=False,
                warning="Start required for live audio-device detection.",
            ),
            "warnings": _projection(
                value=[],
                effective_value=[],
                source="runtime_detected",
                status="unknown",
                editable_later=False,
                warning="Start required for audio runtime probe.",
            ),
            "preferred_input": _projection(
                value=app.config.audio.preferred_input,
                effective_value=app.config.audio.preferred_input,
                source="config",
                status="ok",
                editable_later=False,
            ),
            "output_policy": _projection(
                value=app.config.audio.output_policy,
                effective_value=app.config.audio.output_policy,
                source="config",
                status="ok",
                editable_later=False,
            ),
            "allow_bluetooth_microphone": _projection(
                value=app.config.audio.allow_bluetooth_microphone,
                effective_value=app.config.audio.allow_bluetooth_microphone,
                source="config",
                status="ok",
                editable_later=False,
            ),
        }

    try:
        audio_state = app.get_audio_devices()
        status = "ok"
        warning = None
        audio_state_dict: dict[str, Any] = {
            "input_device": audio_state.input_device,
            "output_device": audio_state.output_device,
            "preferred_input": audio_state.preferred_input,
            "input_transport": audio_state.input_transport,
            "output_transport": audio_state.output_transport,
            "devices": [
                {
                    "name": device.name,
                    "transport": device.transport,
                    "is_input": device.is_input,
                    "is_output": device.is_output,
                    "default_input": device.default_input,
                    "default_output": device.default_output,
                }
                for device in audio_state.devices
            ],
            "warnings": list(audio_state.warnings),
        }
    except Exception as exc:
        status = "invalid"
        warning = str(exc)
        audio_state_dict = {"input_device": None, "output_device": None, "warnings": [warning], "devices": []}

    return {
        "enabled": _projection(
            value=app.config.audio.enabled,
            effective_value=app.config.audio.enabled,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "backend": _projection(
            value=app.config.audio.backend,
            effective_value=app.config.audio.backend,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "input_device": _projection(
            value=audio_state_dict["input_device"],
            effective_value=audio_state_dict["input_device"],
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "output_device": _projection(
            value=audio_state_dict["output_device"],
            effective_value=audio_state_dict["output_device"],
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "warnings": _projection(
            value=audio_state_dict["warnings"],
            effective_value=audio_state_dict["warnings"],
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "devices": _projection(
            value=audio_state_dict["devices"],
            effective_value=audio_state_dict["devices"],
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "preferred_input": _projection(
            value=audio_state_dict.get("preferred_input", app.config.audio.preferred_input),
            effective_value=audio_state_dict.get("preferred_input", app.config.audio.preferred_input),
            source="config",
            status=status if status == "ok" else "invalid",
            editable_later=False,
        ),
        "output_policy": _projection(
            value=app.config.audio.output_policy,
            effective_value=app.config.audio.output_policy,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "allow_bluetooth_microphone": _projection(
            value=app.config.audio.allow_bluetooth_microphone,
            effective_value=app.config.audio.allow_bluetooth_microphone,
            source="config",
            status="ok",
            editable_later=False,
        ),
    }


def _status_from_ready(*, started: bool, enabled: bool, ready: bool) -> str:
    if not started:
        return "unknown"
    if not enabled:
        return "missing"
    return "ok" if ready else "missing"


def _normalize_warning_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        return [value]
    return [str(value)]


def _collect_voice_queue_snapshot(app: DaemonApp) -> dict[str, Any]:
    if not app.started:
        return {
            "status": "unknown",
            "warning": "Start required for queue snapshot.",
            "counts": {"queued": 0, "speaking": 0, "done": 0, "cancelled": 0, "failed": 0},
            "active": 0,
            "queue_size": 0,
            "tail": [],
            "latest_voice_id": None,
            "latest_turn_id": None,
            "latest_error": None,
        }

    conn = app._connect_existing()
    try:
        counts_raw = conn.execute(
            "SELECT status, COUNT(*) FROM voice_queue GROUP BY status"
        ).fetchall()
        counts = {"queued": 0, "speaking": 0, "done": 0, "cancelled": 0, "failed": 0}
        for status, count in counts_raw:
            status_norm = str(status or "").strip().lower()
            if status_norm in counts:
                counts[status_norm] = int(count)

        tail = [
            str(status)
            for status, in conn.execute(
                "SELECT status FROM voice_queue ORDER BY rowid DESC LIMIT 5"
            ).fetchall()
        ]
        queue_size = int(sum(counts.values()))
        active = int(sum(counts.get(k, 0) for k in ("queued", "speaking")))

        latest_error = None
        row = conn.execute(
            "SELECT error, turn_id, voice_id FROM voice_queue WHERE status='failed' "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            raw_error = row[0]
            if isinstance(raw_error, str):
                latest_error = raw_error.strip() or None
        row = conn.execute(
            "SELECT voice_id, turn_id FROM voice_queue ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        latest_voice_id = str(row[0]) if row and row[0] is not None else None
        latest_turn_id = str(row[1]) if row and row[1] is not None else None

        return {
            "status": "ok",
            "warning": None,
            "counts": counts,
            "active": active,
            "queue_size": queue_size,
            "tail": tail,
            "latest_voice_id": latest_voice_id,
            "latest_turn_id": latest_turn_id,
            "latest_error": latest_error,
        }
    except Exception as exc:
        return {
            "status": "invalid",
            "warning": str(exc),
            "counts": {"queued": 0, "speaking": 0, "done": 0, "cancelled": 0, "failed": 0},
            "active": 0,
            "queue_size": 0,
            "tail": [],
            "latest_voice_id": None,
            "latest_turn_id": None,
            "latest_error": None,
        }
    finally:
        close_quietly(conn)


def _collect_voice_events_snapshot(app: DaemonApp) -> dict[str, Any]:
    if not app.started:
        return {
            "latest_safe_error": None,
            "latest_cancel_reason": None,
            "latest_barge_in": _empty_barge_in_snapshot(),
            "status": "unknown",
        }

    conn = app._connect_existing()
    try:
        rows = conn.execute(
            """
            SELECT type, payload_json, turn_id
            FROM events
            WHERE type IN (?, ?, ?, ?, ?)
            ORDER BY id DESC
            LIMIT 60
            """,
            (
                EventType.VOICE_SPEAK_FAILED,
                EventType.VOICE_SPEAK_CANCELLED,
                EventType.TURN_CANCELLED,
                EventType.BRAIN_CANCELLED,
                EventType.TURN_STARTED,
            ),
        ).fetchall()
        latest_safe_error = None
        latest_cancel_reason = None
        latest_barge_in = _empty_barge_in_snapshot()

        for event_type, payload_json, event_turn_id in rows:
            try:
                payload = json.loads(str(payload_json))
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            if latest_safe_error is None and str(event_type) == EventType.VOICE_SPEAK_FAILED:
                raw = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(raw, str) and raw.strip():
                    latest_safe_error = raw.strip()

            if latest_cancel_reason is None and str(event_type) in {
                EventType.TURN_CANCELLED,
                EventType.BRAIN_CANCELLED,
            }:
                raw = payload.get("reason") if isinstance(payload, dict) else None
                if isinstance(raw, str) and raw.strip():
                    latest_cancel_reason = raw.strip()

            if str(event_type) == EventType.VOICE_SPEAK_CANCELLED and not latest_barge_in["interrupted_previous_response"]:
                latest_barge_in = _barge_in_snapshot_from_payload(
                    payload,
                    event_turn_id=event_turn_id,
                )

            if (
                latest_safe_error is not None
                and latest_cancel_reason is not None
                and latest_barge_in["interrupted_previous_response"]
            ):
                break

        return {
            "latest_safe_error": latest_safe_error,
            "latest_cancel_reason": latest_cancel_reason,
            "latest_barge_in": latest_barge_in,
            "status": "ok",
        }
    except Exception as exc:
        return {
            "latest_safe_error": None,
            "latest_cancel_reason": None,
            "latest_barge_in": _empty_barge_in_snapshot(),
            "status": "invalid",
            "warning": str(exc),
        }
    finally:
        close_quietly(conn)


def _latest_barge_in_for_turn(conn: sqlite3.Connection, turn_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT turn_id, payload_json
        FROM events
        WHERE type = ?
        ORDER BY id DESC
        LIMIT 120
        """,
        (EventType.VOICE_SPEAK_CANCELLED,),
    ).fetchall()
    for row in rows:
        try:
            event_turn_id = row["turn_id"]
            payload_json = row["payload_json"]
        except (KeyError, TypeError):
            event_turn_id = row[0]
            payload_json = row[1]
        payload = _safe_parse_json_dict(payload_json)
        snapshot = _barge_in_snapshot_from_payload(payload, event_turn_id=event_turn_id)
        if _barge_in_matches_turn(snapshot, turn_id):
            return snapshot
    return _empty_barge_in_snapshot()


def _latest_turn_trace_projection(
    app: DaemonApp,
    *,
    settings: dict[str, dict[str, Any]],
    event_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = "runtime_detected" if app.started else "unknown"
    conn = app._connect_existing()
    try:
        conn.row_factory = sqlite3.Row
        latest_turn = conn.execute(
            """
            SELECT id, conversation_id, created_at, updated_at, source, status,
                   brain_adapter, brain_model, context_snapshot_json, error, metadata_json
            FROM turns
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_turn is None:
            return {
                "turn_id": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "conversation_id": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "source": _projection(
                    value="unknown",
                    effective_value="unknown",
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "provider_adapter": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "provider_model": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "effort": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "fast": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "context_budget_chars": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "context_window_chars": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "memory_included_count": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "memory_excluded_count": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "approvals_requested_count": _projection(
                    value=0,
                    effective_value=0,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "approvals_executed_count": _projection(
                    value=0,
                    effective_value=0,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "tools_attempted_count": _projection(
                    value=0,
                    effective_value=0,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "voice_rows_created": _projection(
                    value={"filler": 0, "final": 0, "error": 0},
                    effective_value={"filler": 0, "final": 0, "error": 0},
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "cancellation_reason": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "interruption_reason": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "interrupted_previous_response": _projection(
                    value=False,
                    effective_value=False,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "cancelled_speech_id": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "previous_turn_id": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "interrupted_turn_id": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "interruption_attributed_to_turn_id": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "new_turn_source": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "interruption_source": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "timestamps": _projection(
                    value={},
                    effective_value={},
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "latency_ms": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
                "latest_safe_error": _projection(
                    value=None,
                    effective_value=None,
                    source=source,
                    status="missing",
                    editable_later=False,
                    warning="No turns are available yet.",
                ),
            }

        turn_id = str(latest_turn["id"])
        conversation_id = latest_turn["conversation_id"]
        normalized_source = _normalize_turn_source(latest_turn["source"])

        context_snapshot = _safe_parse_json_dict(latest_turn["context_snapshot_json"])
        context_budget = _safe_to_int(context_snapshot.get("max_context_chars"))
        if context_budget is None:
            context_budget = _safe_to_int(app.config.brain.context_budget_chars)
        context_window = _safe_to_int(context_snapshot.get("estimated_context_chars"))
        memory_included = _safe_to_int(context_snapshot.get("memory_block_count"))

        effort_value, effort_status = _read_setting(settings, "effort")
        fast_value, fast_status = _read_setting(settings, "fast")
        barge_in_snapshot = _latest_barge_in_for_turn(conn, turn_id)
        interrupted_previous_response = bool(barge_in_snapshot.get("interrupted_previous_response"))
        cancelled_speech_id = _normalized_optional_text(barge_in_snapshot.get("cancelled_speech_id"))
        barge_in_cancellation_reason = _normalized_optional_text(barge_in_snapshot.get("cancellation_reason"))
        barge_previous_turn_id = _normalized_optional_text(barge_in_snapshot.get("previous_turn_id"))
        barge_interrupted_turn_id = _normalized_optional_text(barge_in_snapshot.get("interrupted_turn_id"))
        barge_attributed_turn_id = _barge_in_attributed_turn_id(barge_in_snapshot)
        barge_new_turn_source = _normalized_optional_text(barge_in_snapshot.get("new_turn_source"))
        barge_interruption_source = _normalized_optional_text(barge_in_snapshot.get("interruption_source"))

        events = conn.execute(
            """
            SELECT type, payload_json, created_at
            FROM events
            WHERE turn_id = ?
            ORDER BY id ASC
            """,
            (turn_id,),
        ).fetchall()

        started_at: datetime | None = None
        completion_at: datetime | None = None
        latest_safe_error: str | None = None
        cancellation_reason: str | None = None
        provider_adapter = latest_turn["brain_adapter"]
        provider_model = latest_turn["brain_model"]

        for row in events:
            event_type = str(row["type"]).strip()
            payload = _safe_parse_json_dict(row["payload_json"])
            created_at = _safe_iso_datetime(row["created_at"])
            if event_type == EventType.TURN_STARTED:
                started_at = created_at
            if event_type in {
                EventType.TURN_FINISHED,
                EventType.TURN_CANCELLED,
                EventType.TURN_FAILED,
            }:
                completion_at = created_at
            if event_type in {
                EventType.VOICE_SPEAK_FAILED,
                EventType.BRAIN_FAILED,
                EventType.TOOL_FAILED,
            } and latest_safe_error is None:
                raw_error = payload.get("error")
                if isinstance(raw_error, str) and raw_error.strip():
                    latest_safe_error = raw_error.strip()
            if event_type == EventType.TURN_CANCELLED and cancellation_reason is None:
                raw_reason = payload.get("reason")
                if isinstance(raw_reason, str) and raw_reason.strip():
                    cancellation_reason = raw_reason.strip()
            if (
                provider_adapter is None
                and event_type == EventType.BRAIN_REQUESTED
                and payload.get("adapter") is not None
            ):
                adapter = payload.get("adapter")
                if isinstance(adapter, str) and adapter.strip():
                    provider_adapter = adapter.strip()
            if (
                provider_model is None
                and event_type == EventType.BRAIN_REQUESTED
                and payload.get("model") is not None
            ):
                model = payload.get("model")
                if isinstance(model, str) and model.strip():
                    provider_model = model.strip()

        if started_at is None:
            started_at = _safe_iso_datetime(latest_turn["created_at"])
        if completion_at is None:
            completion_at = _safe_iso_datetime(latest_turn["updated_at"])

        latency_ms = None
        if started_at is not None and completion_at is not None:
            latency_ms = int((completion_at - started_at).total_seconds() * 1000)

        if latest_safe_error is None:
            raw_error = latest_turn["error"]
            if isinstance(raw_error, str) and raw_error.strip():
                latest_safe_error = raw_error.strip()

        if cancellation_reason is None and latest_turn["status"] == "cancelled":
            raw_error = latest_turn["error"]
            if isinstance(raw_error, str) and raw_error.strip():
                cancellation_reason = raw_error.strip()
        if cancellation_reason is None and barge_in_cancellation_reason is not None:
            cancellation_reason = barge_in_cancellation_reason

        approvals = conn.execute(
            "SELECT id, status, payload_json, metadata_json FROM approvals"
        ).fetchall()
        approvals_requested_count = 0
        approved_approval_ids: set[str] = set()
        for row in approvals:
            approval_id = str(row["id"])
            approval_status = str(row["status"]).strip().lower()
            approval_turn_id = _safe_turn_id_from_approval_payload(
                row["payload_json"],
                row["metadata_json"],
            )
            if approval_turn_id != turn_id:
                continue
            approvals_requested_count += 1
            if approval_status == "approved":
                approved_approval_ids.add(approval_id)

        tool_rows = conn.execute(
            "SELECT status, approval_id FROM tool_runs WHERE turn_id = ?",
            (turn_id,),
        ).fetchall()
        tools_attempted_count = len(tool_rows)
        approvals_executed_count = 0
        for row in tool_rows:
            if row["approval_id"] is None:
                continue
            if row["approval_id"] in approved_approval_ids and str(row["status"]) in {
                "finished",
                "failed",
            }:
                approvals_executed_count += 1

        voice_rows = conn.execute(
            "SELECT status, metadata_json FROM voice_queue WHERE turn_id = ?",
            (turn_id,),
        ).fetchall()
        voice_rows_created = {"filler": 0, "final": 0, "error": 0}
        for row in voice_rows:
            status = str(row["status"] or "").strip().lower()
            metadata = _safe_parse_json_dict(row["metadata_json"])
            kind = str(metadata.get("kind", "")).strip().lower()
            if kind == "filler":
                voice_rows_created["filler"] += 1
            else:
                voice_rows_created["final"] += 1
            if status in {"failed", "cancelled"}:
                voice_rows_created["error"] += 1

        return {
            "turn_id": _projection(
                value=turn_id,
                effective_value=turn_id,
                source=source,
                status="ok",
                editable_later=False,
            ),
            "conversation_id": _projection(
                value=conversation_id,
                effective_value=conversation_id,
                source=source,
                status="ok" if conversation_id is not None else "missing",
                editable_later=False,
            ),
            "source": _projection(
                value=normalized_source,
                effective_value=normalized_source,
                source=source,
                status="ok" if normalized_source != "unknown" else "missing",
                editable_later=False,
            ),
            "provider_adapter": _projection(
                value=provider_adapter,
                effective_value=provider_adapter,
                source=source,
                status="ok" if provider_adapter is not None else "missing",
                editable_later=False,
            ),
            "provider_model": _projection(
                value=provider_model,
                effective_value=provider_model,
                source=source,
                status="ok" if provider_model is not None else "missing",
                editable_later=False,
            ),
            "effort": _projection(
                value=effort_value,
                effective_value=effort_value,
                source="settings",
                status=_normalize_status(effort_status),
                editable_later=False,
            ),
            "fast": _projection(
                value=fast_value,
                effective_value=fast_value,
                source="settings",
                status=_normalize_status(fast_status),
                editable_later=False,
            ),
            "context_budget_chars": _projection(
                value=context_budget,
                effective_value=context_budget,
                source=source,
                status="ok" if context_budget is not None else "missing",
                editable_later=False,
            ),
            "context_window_chars": _projection(
                value=context_window,
                effective_value=context_window,
                source=source,
                status="ok" if context_window is not None else "missing",
                editable_later=False,
                warning="Context snapshot does not include estimated window."
                if context_window is None
                else None,
            ),
            "memory_included_count": _projection(
                value=memory_included,
                effective_value=memory_included,
                source=source,
                status="ok" if memory_included is not None else "missing",
                editable_later=False,
                warning="Context snapshot does not include memory count."
                if memory_included is None
                else None,
            ),
            "memory_excluded_count": _projection(
                value=None,
                effective_value=None,
                source=source,
                status="missing",
                editable_later=False,
                warning="Memory excluded count is not tracked in runtime state.",
            ),
            "approvals_requested_count": _projection(
                value=approvals_requested_count,
                effective_value=approvals_requested_count,
                source=source,
                status="ok",
                editable_later=False,
            ),
            "approvals_executed_count": _projection(
                value=approvals_executed_count,
                effective_value=approvals_executed_count,
                source=source,
                status="ok",
                editable_later=False,
            ),
            "tools_attempted_count": _projection(
                value=tools_attempted_count,
                effective_value=tools_attempted_count,
                source=source,
                status="ok",
                editable_later=False,
            ),
            "voice_rows_created": _projection(
                value=voice_rows_created,
                effective_value=voice_rows_created,
                source=source,
                status="ok",
                editable_later=False,
            ),
            "cancellation_reason": _projection(
                value=cancellation_reason,
                effective_value=cancellation_reason,
                source=source,
                status="ok" if cancellation_reason is None else "invalid",
                editable_later=False,
            ),
            "interruption_reason": _projection(
                value=barge_in_cancellation_reason,
                effective_value=barge_in_cancellation_reason,
                source=source,
                status="ok" if barge_in_cancellation_reason is not None else "missing",
                editable_later=False,
            ),
            "interrupted_previous_response": _projection(
                value=interrupted_previous_response,
                effective_value=interrupted_previous_response,
                source=source,
                status="ok",
                editable_later=False,
                warning="Previous response was not interrupted by cancellation."
                if not interrupted_previous_response
                else None,
            ),
            "cancelled_speech_id": _projection(
                value=cancelled_speech_id,
                effective_value=cancelled_speech_id,
                source=source,
                status="ok" if cancelled_speech_id is not None else "missing",
                editable_later=False,
            ),
            "previous_turn_id": _projection(
                value=barge_previous_turn_id,
                effective_value=barge_previous_turn_id,
                source=source,
                status="ok" if barge_previous_turn_id is not None else "missing",
                editable_later=False,
            ),
            "interrupted_turn_id": _projection(
                value=barge_interrupted_turn_id,
                effective_value=barge_interrupted_turn_id,
                source=source,
                status="ok" if barge_interrupted_turn_id is not None else "missing",
                editable_later=False,
            ),
            "interruption_attributed_to_turn_id": _projection(
                value=barge_attributed_turn_id,
                effective_value=barge_attributed_turn_id,
                source=source,
                status="ok" if barge_attributed_turn_id is not None else "missing",
                editable_later=False,
            ),
            "new_turn_source": _projection(
                value=barge_new_turn_source,
                effective_value=barge_new_turn_source,
                source=source,
                status="ok" if barge_new_turn_source is not None else "missing",
                editable_later=False,
            ),
            "interruption_source": _projection(
                value=barge_interruption_source,
                effective_value=barge_interruption_source,
                source=source,
                status="ok" if barge_interruption_source is not None else "missing",
                editable_later=False,
            ),
            "timestamps": _projection(
                value={
                    "created_at": latest_turn["created_at"],
                    "updated_at": latest_turn["updated_at"],
                    "started_at": None if started_at is None else started_at.isoformat(),
                    "completion_at": None if completion_at is None else completion_at.isoformat(),
                },
                effective_value={
                    "created_at": latest_turn["created_at"],
                    "updated_at": latest_turn["updated_at"],
                    "started_at": None if started_at is None else started_at.isoformat(),
                    "completion_at": None if completion_at is None else completion_at.isoformat(),
                },
                source=source,
                status="ok" if latest_turn["created_at"] else "missing",
                editable_later=False,
            ),
            "latency_ms": _projection(
                value=latency_ms,
                effective_value=latency_ms,
                source=source,
                status="ok" if latency_ms is not None else "missing",
                editable_later=False,
                warning="Need start and completion event timestamps to calculate latency."
                if latency_ms is None
                else None,
            ),
            "latest_safe_error": _projection(
                value=latest_safe_error,
                effective_value=latest_safe_error,
                source=source if latest_safe_error is not None else "runtime_detected",
                status="ok" if latest_safe_error is None else "invalid",
                editable_later=False,
            ),
        }
    except Exception as exc:
        error = str(exc)
        return {
            "turn_id": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "conversation_id": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "source": _projection(
                value="unknown",
                effective_value="unknown",
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "provider_adapter": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "provider_model": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "effort": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "fast": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "context_budget_chars": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "context_window_chars": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "memory_included_count": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "memory_excluded_count": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "approvals_requested_count": _projection(
                value=0,
                effective_value=0,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "approvals_executed_count": _projection(
                value=0,
                effective_value=0,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "tools_attempted_count": _projection(
                value=0,
                effective_value=0,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "voice_rows_created": _projection(
                value={"filler": 0, "final": 0, "error": 0},
                effective_value={"filler": 0, "final": 0, "error": 0},
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "cancellation_reason": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "timestamps": _projection(
                value={},
                effective_value={},
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "latency_ms": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "interrupted_previous_response": _projection(
                value=False,
                effective_value=False,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "cancelled_speech_id": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "previous_turn_id": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "new_turn_source": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
            "latest_safe_error": _projection(
                value=None,
                effective_value=None,
                source="runtime_detected" if app.started else "unknown",
                status="invalid",
                editable_later=False,
                warning=error,
            ),
        }
    finally:
        close_quietly(conn)


def _layer_projection(
    *,
    configured_value: Any,
    effective_value: Any,
    readiness: str,
    dependency_status: Any,
    latest_safe_error: Any,
    warnings: Any = None,
) -> dict[str, Any]:
    warning_value = _normalize_warning_list(warnings)
    return {
        "configured_value": _projection(
            value=configured_value,
            effective_value=configured_value,
            source="config",
            status=_normalize_status(readiness),
            editable_later=False,
        ),
        "effective_value": _projection(
            value=effective_value,
            effective_value=effective_value,
            source="runtime_detected",
            status=_normalize_status(readiness),
            editable_later=False,
        ),
        "readiness": _projection(
            value=readiness,
            effective_value=readiness,
            source="runtime_detected",
            status=_normalize_status(readiness),
            editable_later=False,
        ),
        "dependency_status": _projection(
            value=dependency_status,
            effective_value=dependency_status,
            source="runtime_detected",
            status=_normalize_status(readiness),
            editable_later=False,
            warning=None,
        ),
        "latest_safe_error": _projection(
            value=latest_safe_error,
            effective_value=latest_safe_error,
            source="runtime_detected",
            status="ok" if latest_safe_error is None else "invalid",
            editable_later=False,
            warning=None,
        ),
        "warnings": _projection(
            value=warning_value,
            effective_value=warning_value,
            source="runtime_detected",
            status="ok" if not warning_value else _normalize_status(readiness),
            editable_later=False,
            warning=None if not warning_value else "Warnings detected.",
            ),
        }


def _runtime_projection_value(projection: Any) -> Any:
    if isinstance(projection, dict):
        if "effective_value" in projection:
            return projection.get("effective_value")
        return projection.get("value")
    return None


def _normalize_current_turn_source(raw_source: Any) -> str:
    source = str(raw_source).strip().lower() if raw_source is not None else ""
    if source in {"ptt", "voice", "voice_ptt", "barge_in"}:
        return "voice_ptt"
    if source in {"text", "panel", "tool"}:
        return source
    if source in {"api", "cli"}:
        return "text"
    return "unknown"


def _current_turn_state_projection(
    app: DaemonApp,
    *,
    latest_turn_trace: dict[str, Any],
    queue_snapshot: dict[str, Any],
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    source = "runtime_detected" if app.started else "unknown"
    trace = latest_turn_trace if isinstance(latest_turn_trace, dict) else {}
    queue = queue_snapshot if isinstance(queue_snapshot, dict) else {}
    events = event_snapshot if isinstance(event_snapshot, dict) else {}
    barge_in = events.get("latest_barge_in")
    if not isinstance(barge_in, dict):
        barge_in = {}

    queue_counts = queue.get("counts")
    if not isinstance(queue_counts, dict):
        queue_counts = {}
    queued_count = _safe_to_int(queue_counts.get("queued")) or 0
    speaking_count = _safe_to_int(queue_counts.get("speaking")) or 0
    active_speech_count = _safe_to_int(queue.get("active")) or 0
    speech_pending = queued_count > 0 or active_speech_count > 0

    generation_active = 0
    try:
        generation_active = app.voice_generation_registry.active_count()
    except Exception:
        generation_active = 0

    active_leases = []
    if app.started:
        try:
            active_leases = app.active_listening_leases()
        except Exception:
            active_leases = []
    listening_active = len(active_leases) > 0
    lease_source = None
    for lease in active_leases:
        raw_source = getattr(lease, "source", None)
        if raw_source:
            lease_source = raw_source
            break

    trace_turn_id = _runtime_projection_value(trace.get("turn_id"))
    queue_turn_id = _normalized_optional_text(queue.get("latest_turn_id"))
    trace_conversation_id = _runtime_projection_value(trace.get("conversation_id"))
    trace_source = _runtime_projection_value(trace.get("source"))
    event_attributed_turn_id = _barge_in_attributed_turn_id(barge_in)
    event_relevant = False
    if event_attributed_turn_id is not None:
        if _barge_in_matches_turn(barge_in, trace_turn_id):
            event_relevant = True
        elif speech_pending and event_attributed_turn_id == queue_turn_id:
            event_relevant = True
        elif listening_active:
            event_relevant = True

    trace_attributed_turn_id = (
        _normalized_optional_text(_runtime_projection_value(trace.get("interruption_attributed_to_turn_id")))
        or _normalized_optional_text(_runtime_projection_value(trace.get("interrupted_turn_id")))
        or _normalized_optional_text(_runtime_projection_value(trace.get("previous_turn_id")))
    )
    trace_interrupted = (
        bool(_runtime_projection_value(trace.get("interrupted_previous_response")))
        and trace_attributed_turn_id is not None
        and trace_attributed_turn_id == _normalized_optional_text(trace_turn_id)
    )

    if event_relevant:
        interrupted = bool(barge_in.get("interrupted_previous_response"))
        interruption_attributed_to_turn_id = event_attributed_turn_id
        interrupted_turn_id = _normalized_optional_text(barge_in.get("interrupted_turn_id")) or event_attributed_turn_id
        interruption_reason = (
            _normalized_optional_text(barge_in.get("interruption_reason"))
            or _normalized_optional_text(barge_in.get("cancellation_reason"))
        )
        cancelled_speech_id = _normalized_optional_text(barge_in.get("cancelled_speech_id"))
        interruption_source = (
            _normalized_optional_text(barge_in.get("interruption_source"))
            or _normalized_optional_text(barge_in.get("new_turn_source"))
        )
    elif trace_interrupted:
        interrupted = True
        interruption_attributed_to_turn_id = trace_attributed_turn_id
        interrupted_turn_id = (
            _normalized_optional_text(_runtime_projection_value(trace.get("interrupted_turn_id")))
            or trace_attributed_turn_id
        )
        interruption_reason = (
            _normalized_optional_text(_runtime_projection_value(trace.get("interruption_reason")))
            or _normalized_optional_text(_runtime_projection_value(trace.get("cancellation_reason")))
        )
        cancelled_speech_id = _normalized_optional_text(_runtime_projection_value(trace.get("cancelled_speech_id")))
        interruption_source = (
            _normalized_optional_text(_runtime_projection_value(trace.get("interruption_source")))
            or _normalized_optional_text(_runtime_projection_value(trace.get("new_turn_source")))
        )
    else:
        interrupted = False
        interruption_attributed_to_turn_id = None
        interrupted_turn_id = None
        interruption_reason = None
        cancelled_speech_id = None
        interruption_source = None

    if listening_active:
        current_turn_source = _normalize_current_turn_source(interruption_source or lease_source or "voice_ptt")
    else:
        current_turn_source = _normalize_current_turn_source(interruption_source or trace_source)

    if speech_pending:
        current_turn_id = queue_turn_id or trace_turn_id
    elif generation_active > 0:
        current_turn_id = trace_turn_id
    elif listening_active and current_turn_source == "voice_ptt":
        current_turn_id = None
    else:
        current_turn_id = trace_turn_id

    current_conversation_id = trace_conversation_id if current_turn_id is not None else None
    current_speech_id = queue.get("latest_voice_id") if speaking_count > 0 else None
    latest_safe_error = _runtime_projection_value(trace.get("latest_safe_error"))

    if not app.started:
        generation_state = "unknown"
    elif latest_safe_error:
        generation_state = "error"
    elif listening_active:
        generation_state = "listening"
    elif speaking_count > 0 or queued_count > 0:
        generation_state = "speaking"
    elif generation_active > 0:
        generation_state = "generating"
    elif interrupted and interruption_reason:
        generation_state = "cancelled"
    else:
        generation_state = "idle"

    turn_missing_warning = (
        "Current voice PTT turn has no persisted turn id yet."
        if listening_active and current_turn_id is None
        else None
    )

    return {
        "current_turn_id": _projection(
            value=current_turn_id,
            effective_value=current_turn_id,
            source=source,
            status="ok" if current_turn_id is not None else "missing",
            editable_later=False,
            warning=turn_missing_warning,
        ),
        "current_conversation_id": _projection(
            value=current_conversation_id,
            effective_value=current_conversation_id,
            source=source,
            status="ok" if current_conversation_id is not None else "missing",
            editable_later=False,
            warning=turn_missing_warning,
        ),
        "current_turn_source": _projection(
            value=current_turn_source,
            effective_value=current_turn_source,
            source=source,
            status="ok" if current_turn_source != "unknown" else "unknown",
            editable_later=False,
        ),
        "generation_state": _projection(
            value=generation_state,
            effective_value=generation_state,
            source=source,
            status="ok" if generation_state != "unknown" else "unknown",
            editable_later=False,
        ),
        "current_speech_id": _projection(
            value=current_speech_id,
            effective_value=current_speech_id,
            source=source,
            status="ok" if current_speech_id is not None else "missing",
            editable_later=False,
        ),
        "interrupted_previous_response": _projection(
            value=interrupted if app.started else "unknown",
            effective_value=interrupted if app.started else "unknown",
            source=source,
            status="ok" if app.started else "unknown",
            editable_later=False,
        ),
        "interrupted_turn_id": _projection(
            value=interrupted_turn_id,
            effective_value=interrupted_turn_id,
            source=source,
            status="ok" if interrupted_turn_id is not None else "missing",
            editable_later=False,
        ),
        "interruption_attributed_to_turn_id": _projection(
            value=interruption_attributed_to_turn_id,
            effective_value=interruption_attributed_to_turn_id,
            source=source,
            status="ok" if interruption_attributed_to_turn_id is not None else "missing",
            editable_later=False,
        ),
        "interruption_reason": _projection(
            value=interruption_reason,
            effective_value=interruption_reason,
            source=source,
            status="ok" if interruption_reason is not None else "missing",
            editable_later=False,
        ),
        "interruption_source": _projection(
            value=interruption_source,
            effective_value=interruption_source,
            source=source,
            status="ok" if interruption_source is not None else "missing",
            editable_later=False,
        ),
        "cancelled_speech_id": _projection(
            value=cancelled_speech_id,
            effective_value=cancelled_speech_id,
            source=source,
            status="ok" if cancelled_speech_id is not None else "missing",
            editable_later=False,
        ),
    }


def _voice_layer_projection_capture_input(
    app: DaemonApp,
    *,
    audio_projection: dict[str, Any],
    queue_snapshot: dict[str, Any],
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(app.config.voice.enabled)
    readiness = _status_from_ready(started=app.started, enabled=enabled, ready=app.started and enabled)
    recorder_status, recorder_binary, recorder_warning = _safe_probe_recorder_binary(
        str(app.config.voice.recorder_binary or ""),
        str(app.config.voice.recorder or "mock"),
    )
    audio_warnings = audio_projection.get("warnings", {})
    audio_warning_values = _normalize_warning_list(audio_warnings.get("value"))
    recorder_warning = recorder_warning or audio_warning_values[0] if audio_warning_values else None
    warnings: list[str] = []
    if not app.started:
        warnings.append("Start required for capture/input runtime state.")
    if queue_snapshot.get("status") != "ok":
        warnings.append(f"Queue snapshot state: {queue_snapshot.get('status')}")
    if recorder_status != "ok":
        warnings.append(recorder_warning or "Recorder binary check failed.")
    if audio_warning_values:
        warnings.append(f"Audio probe warning: {audio_warning_values[0]}")

    audio_input = audio_projection.get("input_device", {})
    audio_output = audio_projection.get("output_device", {})
    audio_input_status = str(audio_input.get("status", "unknown"))
    audio_output_status = str(audio_output.get("status", "unknown"))
    audio_permission_ready = (
        "ok" if (
            app.started
            and audio_input_status == "ok"
            and audio_output_status == "ok"
        )
        else "unknown" if not app.started else "invalid"
    )

    dependency_status = {
        "audio_backend": "ok" if app.started and app.config.audio.enabled else "missing",
        "audio_input_device_probe": audio_input_status if audio_input_status in KNOWN_STATUSES else "invalid",
        "audio_output_device_probe": audio_output_status if audio_output_status in KNOWN_STATUSES else "invalid",
        "audio_permission_device_status": audio_permission_ready,
        "recorder_binary": recorder_status,
        "voice_recorder": "ok" if app.voice_recorder is not None else "missing",
    }

    configured_value = {
        "voice_enabled": enabled,
        "recorder": app.config.voice.recorder,
        "recorder_binary": app.config.voice.recorder_binary,
        "recorder_binary_detected": recorder_binary,
        "recorder_sample_rate": app.config.voice.recorder_sample_rate,
        "recorder_highpass_hz": app.config.voice.recorder_highpass_hz,
        "recorder_gain_db": app.config.voice.recorder_gain_db,
        "audio_input_policy": app.config.audio.input_policy,
        "audio_output_policy": app.config.audio.output_policy,
        "preferred_input": app.config.audio.preferred_input,
        "always_listen_enabled": app.config.audio.always_listen_enabled,
    }
    effective_value = {
        "audio_device_input": audio_input.get("effective_value"),
        "audio_device_output": audio_output.get("effective_value"),
        "recorder_ready": app.voice_recorder is not None,
        "input_probe_status": audio_input_status,
        "output_probe_status": audio_output_status,
    }

    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error"),
        warnings=warnings,
    )


def _voice_layer_projection_stt(app: DaemonApp, *, queue_snapshot: dict[str, Any], event_snapshot: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(app.config.voice.enabled)
    stt_ready = app.started and enabled and app.voice_stt is not None
    readiness = _status_from_ready(started=app.started, enabled=enabled, ready=stt_ready)
    stt_engine = str(app.config.voice.default_stt or "").strip()
    stt_package_status, stt_package_name = _safe_probe_stt_package(stt_engine)
    stt_model_status, stt_model_path, stt_model_warning = _safe_probe_model_path(
        str(app.config.voice.stt_model or "")
    )
    network_tool_status, network_tool = _safe_probe_network_capability()
    runtime_voice_dir_status, runtime_voice_dir = _safe_probe_runtime_voice_dir(
        str(getattr(app.config.runtime, "runtime_dir", ""))
    )
    stt_runtime_model: str | None = None
    stt_runtime_language: str | None = None
    stt_runtime_timeout_seconds: float | None = None
    stt_runtime_timeout_per_audio_second: float | None = None
    stt_runtime_min_voiced_seconds: float | None = None
    stt_runtime_min_voiced_ratio: float | None = None
    stt_runtime_min_rms: int | None = None
    if app.voice_stt is not None:
        stt_runtime_model = str(getattr(app.voice_stt, "_model", "")).strip() or None
        stt_runtime_language = str(getattr(app.voice_stt, "_language", "")).strip() or None
        if getattr(app.voice_stt, "_base_timeout", None) is not None:
            stt_runtime_timeout_seconds = float(getattr(app.voice_stt, "_base_timeout", 0.0))
        if getattr(app.voice_stt, "_timeout_per_second", None) is not None:
            stt_runtime_timeout_per_audio_second = float(
                getattr(app.voice_stt, "_timeout_per_second", 0.0)
            )
        if getattr(app.voice_stt, "_min_voiced_seconds", None) is not None:
            stt_runtime_min_voiced_seconds = float(
                getattr(app.voice_stt, "_min_voiced_seconds", 0.0)
            )
        if getattr(app.voice_stt, "_min_voiced_ratio", None) is not None:
            stt_runtime_min_voiced_ratio = float(
                getattr(app.voice_stt, "_min_voiced_ratio", 0.0)
            )
        if getattr(app.voice_stt, "_min_rms", None) is not None:
            stt_runtime_min_rms = int(getattr(app.voice_stt, "_min_rms", 0))
    warnings: list[str] = []
    if not app.config.voice.default_stt:
        warnings.append("default_stt is empty.")
    if stt_package_status != "ok" and stt_model_status == "ok":
        warnings.append("STT package is not available in runtime.")
    if stt_model_status == "missing":
        warnings.append(stt_model_warning or "Configured STT model path missing.")
    if _safe_is_local_path(str(app.config.voice.stt_model)) and stt_model_status != "ok":
        warnings.append(stt_model_warning or "STT model path is configured as local path but is missing or inaccessible.")
    elif not _safe_is_local_path(str(app.config.voice.stt_model)):
        if stt_engine not in {"mock", "mlx_whisper", "mlx-whisper"} and stt_model_status == "unknown":
            warnings.append("STT model format is non-path and non-default; runtime may require network.")
        elif stt_engine in {"mock", "mlx_whisper", "mlx-whisper"} and stt_model_status == "unknown":
            warnings.append("STT model path not local; network/tool availability determines readiness.")
            if network_tool_status == KNOWN_PROVIDER_SUPPORT_NO and app.config.voice.stt_model:
                warnings.append("No local network tool available for STT model acquisition.")

    stt_language = str(app.config.voice.stt_language or "").strip()
    stt_language_status = "ok" if stt_language else "missing"
    dependency_status = {
        "capture_input": "ok" if app.voice_recorder is not None else "missing",
        "transcription_pipeline": "ok" if app.voice_stt is not None else "missing",
        "anti_echo_gate": "ok" if app.voice_gateway is not None else "missing",
        "stt_package": stt_package_status,
        "stt_model_path": stt_model_status if _safe_is_local_path(str(app.config.voice.stt_model)) else KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        "stt_network_tool": network_tool_status,
        "stt_runtime_voice_dir": runtime_voice_dir_status,
        "stt_language": stt_language_status,
        "stt_model_type": stt_model_status if _safe_is_local_path(str(app.config.voice.stt_model)) else KNOWN_PROVIDER_SUPPORT_UNKNOWN,
        "stt_runtime_model": "ok" if stt_runtime_model else "missing",
        "stt_runtime_language": "ok" if stt_runtime_language else "missing",
        "stt_runtime_timeouts": "ok"
        if stt_runtime_timeout_seconds is not None and stt_runtime_timeout_per_audio_second is not None
        else "missing",
    }
    engine_name = None
    if app.voice_stt is not None:
        engine_name = getattr(app.voice_stt, "name", None)
        if engine_name is None:
            engine_obj = getattr(app.voice_stt, "_engine", None)
            engine_name = getattr(engine_obj, "name", None)

    configured_value = {
        "default_stt": app.config.voice.default_stt,
        "stt_model": app.config.voice.stt_model,
        "stt_language": app.config.voice.stt_language,
        "stt_timeout_seconds": app.config.voice.stt_timeout_seconds,
        "stt_timeout_per_audio_second": app.config.voice.stt_timeout_per_audio_second,
        "stt_min_voiced_seconds": app.config.voice.stt_min_voiced_seconds,
        "stt_min_voiced_ratio": app.config.voice.stt_min_voiced_ratio,
        "stt_min_rms": app.config.voice.stt_min_rms,
    }
    effective_value = {
        "engine": engine_name,
        "pipeline_ready": app.voice_stt is not None,
        "queue_pending": queue_snapshot.get("active"),
        "last_error": queue_snapshot.get("latest_error"),
        "stt_package_available": stt_package_status,
        "stt_model_path_detected": stt_model_path,
        "stt_network_tool": network_tool,
        "stt_runtime_voice_dir": runtime_voice_dir,
        "stt_engine": stt_package_name,
        "stt_runtime_model": stt_runtime_model,
        "stt_runtime_language": stt_runtime_language,
        "stt_runtime_timeout_seconds": stt_runtime_timeout_seconds,
        "stt_runtime_timeout_per_audio_second": stt_runtime_timeout_per_audio_second,
        "stt_runtime_min_voiced_seconds": stt_runtime_min_voiced_seconds,
        "stt_runtime_min_voiced_ratio": stt_runtime_min_voiced_ratio,
        "stt_runtime_min_rms": stt_runtime_min_rms,
    }
    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error") or queue_snapshot.get("latest_error"),
        warnings=warnings,
    )


def _voice_layer_projection_endpointing(app: DaemonApp, *, event_snapshot: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(app.config.voice.enabled)
    gateway_ready = app.voice_gateway is not None
    ptt_mode = str(app.config.voice.ptt_mode or "").strip()
    ptt_mode_ready = ptt_mode in CANONICAL_PTT_MODES
    anti_echo_window_status = (
        "ok" if app.config.voice.anti_echo_window_seconds >= 0 else "invalid"
    )
    anti_echo_overlap_status = (
        "ok" if 0 <= app.config.voice.anti_echo_overlap_threshold <= 1 else "invalid"
    )
    retry_status = (
        "ok" if app.config.voice.transcript_turn_retry_seconds >= 0 else "invalid"
    )
    readiness = "ok" if app.started and enabled and gateway_ready and ptt_mode_ready else (
        "invalid" if app.started and enabled and not ptt_mode_ready else _status_from_ready(started=app.started, enabled=enabled, ready=gateway_ready)
    )
    warnings = []
    if ptt_mode and not ptt_mode_ready:
        warnings.append(f"Unsupported ptt_mode {ptt_mode!r}.")
    if not app.config.voice.transcript_turn_retry_seconds > 0:
        warnings.append("transcript_turn_retry_seconds is not positive.")

    configured_value = {
        "ptt_mode": app.config.voice.ptt_mode,
        "ptt_hotkey": app.config.voice.ptt_hotkey,
        "ptt_hold_ttl_seconds": app.config.voice.ptt_hold_ttl_seconds,
        "transcript_turn_retry_seconds": app.config.voice.transcript_turn_retry_seconds,
        "anti_echo_window_seconds": app.config.voice.anti_echo_window_seconds,
        "anti_echo_overlap_threshold": app.config.voice.anti_echo_overlap_threshold,
    }
    dependency_status = {
        "voice_gateway": "ok" if app.voice_gateway is not None else "missing",
        "stt_input": "ok" if app.voice_stt is not None else "missing",
        "turn_speech_active_probe": "ok" if app.started else "unknown",
        "ptt_mode": "ok" if ptt_mode_ready else "invalid",
        "anti_echo_window": anti_echo_window_status,
        "anti_echo_overlap_threshold": anti_echo_overlap_status,
        "turn_cancel_retry_seconds": retry_status,
    }
    return _layer_projection(
        configured_value=configured_value,
        effective_value={
            "ptt_mode_runtime": app.config.voice.ptt_mode,
            "ptt_hotkey_runtime": app.config.voice.ptt_hotkey,
            "endpointing_ready": gateway_ready,
        },
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error"),
        warnings=warnings,
    )


def _voice_layer_projection_turn_manager(
    app: DaemonApp,
    *,
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(app.config.voice.enabled)
    gateway_ready = app.voice_gateway is not None
    cancellation_ready = app.voice_cancellation is not None
    registry_active = 0
    try:
        registry_active = app.voice_generation_registry.active_count()
    except Exception:
        registry_active = 0

    readiness = _status_from_ready(
        started=app.started,
        enabled=enabled,
        ready=gateway_ready and cancellation_ready,
    )
    dependency_status = {
        "voice_gateway": "ok" if gateway_ready else "missing",
        "cancellation_coordinator": "ok" if cancellation_ready else "missing",
        "generation_registry": "ok" if app.voice_generation_registry is not None else "missing",
        "start_orchestrator": "ok" if app.started else "unknown",
    }
    configured_value = {
        "speak_responses": app.config.voice.speak_responses,
        "broker_enabled": app.config.voice.broker_enabled,
        "queue_persisted": app.config.voice.queue_persisted,
    }
    effective_value = {
        "gateway_ready": gateway_ready,
        "broker_ready": app.voice_broker is not None,
        "active_generation_count": registry_active,
        "speak_responses": app.config.voice.speak_responses,
    }
    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error"),
        warnings=None if app.config.voice.speak_responses or not enabled else ["speak_responses is disabled."],
    )


def _voice_layer_projection_tts(
    app: DaemonApp,
    *,
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(app.config.voice.enabled)
    tts_ready = app.started and enabled and app.voice_broker is not None
    readiness = _status_from_ready(started=app.started, enabled=enabled, ready=tts_ready)
    configured_tts = str(app.config.voice.default_tts or "").strip()
    tts_binary_status, tts_binary, tts_binary_warning = _safe_probe_tts_binary(
        configured_tts,
        str(app.config.voice.supertonic_binary or ""),
    )
    playback_status, playback_binary, playback_warning = _safe_probe_playback_binary(
        str(app.config.voice.playback_binary or "")
    )
    runtime_voice_dir_status, runtime_voice_dir = _safe_probe_runtime_voice_dir(
        str(getattr(app.config.runtime, "runtime_dir", ""))
    )
    engine = None
    player = None
    tts_runtime_voice: str | None = None
    tts_runtime_language: str | None = None
    tts_runtime_steps: int | None = None
    tts_runtime_speed: float | None = None
    tts_runtime_short_sentence_chars: int | None = None
    tts_runtime_short_sentence_speed: float | None = None
    tts_runtime_timeout_seconds: int | None = None
    tts_runtime_pad_start_seconds: float | None = None
    tts_runtime_pad_end_seconds: float | None = None
    tts_runtime_binary: str | None = None
    tts_runtime_pronunciations_count: int | None = None
    supertonic_voice = str(app.config.voice.supertonic_voice or "").strip()
    supertonic_lang = str(app.config.voice.supertonic_lang or "").strip()
    supertonic_steps = int(getattr(app.config.voice, "supertonic_steps", 0) or 0)
    supertonic_speed = float(getattr(app.config.voice, "supertonic_speed", 0.0) or 0.0)
    supertonic_short_sentence_chars = int(getattr(app.config.voice, "supertonic_short_sentence_chars", 0) or 0)
    supertonic_short_sentence_speed = float(
        getattr(app.config.voice, "supertonic_short_sentence_speed", 1.0) or 1.0
    )
    if app.voice_broker is not None:
        engine = getattr(app.voice_broker, "_engine", None)
        player = getattr(engine, "_player", None)
        tts_runtime_binary = str(getattr(engine, "_binary", "")).strip() or None
        tts_runtime_voice = str(getattr(engine, "_voice", "")).strip() or None
        tts_runtime_language = str(getattr(engine, "_lang", "")).strip() or None
        if getattr(engine, "_steps", None) is not None:
            tts_runtime_steps = int(getattr(engine, "_steps", 0))
        if getattr(engine, "_speed", None) is not None:
            tts_runtime_speed = float(getattr(engine, "_speed", 0.0))
        if getattr(engine, "_short_chars", None) is not None:
            tts_runtime_short_sentence_chars = int(getattr(engine, "_short_chars", 0))
        if getattr(engine, "_short_speed", None) is not None:
            tts_runtime_short_sentence_speed = float(getattr(engine, "_short_speed", 1.0))
        if getattr(engine, "_timeout", None) is not None:
            tts_runtime_timeout_seconds = int(getattr(engine, "_timeout", 120))
        if getattr(engine, "_pad_start", None) is not None:
            tts_runtime_pad_start_seconds = float(getattr(engine, "_pad_start", 0.0))
        if getattr(engine, "_pad_end", None) is not None:
            tts_runtime_pad_end_seconds = float(getattr(engine, "_pad_end", 0.0))
        tts_runtime_pronunciations_count = len(getattr(engine, "_pronunciations", {}))
    engine_name = getattr(engine, "name", None) if engine is not None else None
    warnings = []
    if configured_tts and configured_tts.lower() not in {"mock", "supertonic"}:
        warnings.append(f"Configured TTS engine {configured_tts!r} is unknown in runtime projection.")
    if configured_tts == "supertonic" and not supertonic_voice:
        warnings.append("Configured supertonic voice id is empty.")
    if configured_tts == "supertonic" and not supertonic_lang:
        warnings.append("Configured supertonic language is empty.")
    supertonic_voice_status = "ok"
    if configured_tts == "supertonic":
        supertonic_voice_status, _, supertonic_voice_warning = _supertonic_voice_status_without_probe(
            supertonic_voice,
        )
        if supertonic_voice_warning:
            warnings.append(supertonic_voice_warning)
    if tts_binary_status != "ok":
        warnings.append(tts_binary_warning or "TTS binary probe failed.")
    if playback_status != "ok":
        warnings.append(playback_warning or "Playback binary probe failed.")

    dependency_status = {
        "tts_engine": "ok" if app.voice_broker is not None else "missing",
        "tts_binary": tts_binary_status,
        "tts_player": "ok" if player is not None else "missing",
        "playback_binary": playback_status,
        "tts_runtime_voice_dir": runtime_voice_dir_status,
        "supertonic_voice": (
            supertonic_voice_status
            if configured_tts == "supertonic"
            else ("ok" if configured_tts != "supertonic" else "missing")
        ),
        "supertonic_lang": "ok" if configured_tts != "supertonic" else ("ok" if supertonic_lang else "missing"),
        "tts_runtime_voice": "ok" if tts_runtime_voice else ("ok" if configured_tts != "supertonic" else "missing"),
        "tts_runtime_profile": "ok" if tts_runtime_language else ("ok" if configured_tts != "supertonic" else "missing"),
        "tts_runtime_binary": "ok" if tts_runtime_binary else "missing",
        "tts_runtime_synthesis_timeout": "ok" if tts_runtime_timeout_seconds is not None else "missing",
    }
    configured_value = {
        "default_tts": app.config.voice.default_tts,
        "voice_id": supertonic_voice if configured_tts == "supertonic" else None,
        "voice_model": supertonic_voice if configured_tts == "supertonic" else None,
        "voice_profile": supertonic_lang if configured_tts == "supertonic" else None,
        "voice_engine": configured_tts,
        "tts_binary": tts_binary,
        "playback_binary": playback_binary,
        "supertonic_steps": supertonic_steps if configured_tts == "supertonic" else None,
        "supertonic_speed": supertonic_speed if configured_tts == "supertonic" else None,
        "supertonic_short_sentence_chars": supertonic_short_sentence_chars
        if configured_tts == "supertonic"
        else None,
        "supertonic_short_sentence_speed": supertonic_short_sentence_speed
        if configured_tts == "supertonic"
        else None,
        "voice_id_model": engine_name,
        "tts_pronunciations_count": (
            tts_runtime_pronunciations_count
            if tts_runtime_pronunciations_count is not None
            else len(getattr(app.config.voice, "tts_pronunciations", {}))
        ),
        "broker_enabled": app.config.voice.broker_enabled,
        "speak_responses": app.config.voice.speak_responses,
        "tts_runtime_steps": tts_runtime_steps,
        "tts_runtime_speed": tts_runtime_speed,
        "tts_runtime_short_sentence_chars": tts_runtime_short_sentence_chars,
        "tts_runtime_short_sentence_speed": tts_runtime_short_sentence_speed,
        "tts_runtime_timeout_seconds": tts_runtime_timeout_seconds,
        "tts_runtime_pad_start_seconds": tts_runtime_pad_start_seconds,
        "tts_runtime_pad_end_seconds": tts_runtime_pad_end_seconds,
        "tts_runtime_binary": tts_runtime_binary,
        "voice_id_runtime": tts_runtime_voice or supertonic_voice if configured_tts == "supertonic" else None,
        "voice_model_runtime": tts_runtime_voice or supertonic_voice if configured_tts == "supertonic" else None,
        "voice_profile_runtime": tts_runtime_language or supertonic_lang if configured_tts == "supertonic" else None,
    }
    effective_value = {
        "engine_name": engine_name,
        "tts_binary_detected": tts_binary,
        "playback_binary_detected": playback_binary,
        "playback_engine_present": player is not None,
        "playback_engine": type(player).__name__ if player is not None else None,
        "broker_ready": app.voice_broker is not None,
        "playback_binary_runtime": playback_binary or str(app.config.voice.playback_binary),
        "tts_runtime_voice_dir": runtime_voice_dir,
        "voice_profile_runtime": tts_runtime_language or supertonic_lang,
        "voice_id_runtime": tts_runtime_voice,
        "voice_model_runtime": tts_runtime_voice,
        "tts_runtime_steps": tts_runtime_steps,
        "tts_runtime_speed": tts_runtime_speed,
        "tts_runtime_short_sentence_chars": tts_runtime_short_sentence_chars,
        "tts_runtime_short_sentence_speed": tts_runtime_short_sentence_speed,
        "tts_runtime_timeout_seconds": tts_runtime_timeout_seconds,
        "tts_runtime_pad_start_seconds": tts_runtime_pad_start_seconds,
        "tts_runtime_pad_end_seconds": tts_runtime_pad_end_seconds,
        "tts_runtime_binary": tts_runtime_binary,
        "engine_configured": configured_tts,
    }
    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error"),
        warnings=warnings,
    )


def _voice_layer_projection_playback(
    app: DaemonApp,
    *,
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(app.config.voice.enabled)
    broker_ready = app.voice_broker is not None
    playback_status, playback_binary, playback_warning = _safe_probe_playback_binary(
        str(app.config.voice.playback_binary or "")
    )
    queue_counts = {
        "queued": 0,
        "speaking": 0,
        "done": 0,
        "cancelled": 0,
        "failed": 0,
    }
    readiness = _status_from_ready(started=app.started, enabled=enabled and app.config.voice.speak_responses, ready=broker_ready)
    warnings = []
    if playback_status != "ok":
        warnings.append(playback_warning or "Playback command probe failed.")
    if not app.config.voice.speak_responses:
        warnings.append("speak_responses is disabled.")
    if not app.config.voice.broker_enabled:
        warnings.append("broker_enabled is disabled.")
    player = None
    engine = None
    if app.voice_broker is not None:
        engine = getattr(app.voice_broker, "_engine", None)
        player = getattr(engine, "_player", None)
    if queue_projection := _collect_voice_queue_snapshot(app):
        snapshot_status = queue_projection.get("status", "unknown")
        counts = queue_projection.get("counts")
        if isinstance(counts, dict):
            queue_counts = {
                "queued": int(counts.get("queued", 0)),
                "speaking": int(counts.get("speaking", 0)),
                "done": int(counts.get("done", 0)),
                "cancelled": int(counts.get("cancelled", 0)),
                "failed": int(counts.get("failed", 0)),
            }
        if snapshot_status != "ok":
            warnings.append(f"Queue snapshot is not ready: {snapshot_status}")
    dependency_status = {
        "playback_binary": playback_status,
        "tts_broker": "ok" if broker_ready else "missing",
        "tts_player": "ok" if player is not None else "missing",
        "playback_queue_table": str(
            queue_projection.get("status", "unknown") if "queue_projection" in locals() else "unknown"
        ),
    }
    configured_value = {
        "playback_binary": app.config.voice.playback_binary,
        "tts_timeout_seconds": app.config.voice.tts_timeout_seconds,
        "playback_pad_start_seconds": app.config.voice.playback_pad_start_seconds,
        "playback_pad_end_seconds": app.config.voice.playback_pad_end_seconds,
        "playback_binary_detected": playback_binary,
        "speak_responses": app.config.voice.speak_responses,
        "broker_enabled": app.config.voice.broker_enabled,
        "voice_engine": str(app.config.voice.default_tts or ""),
    }
    effective_value = {
        "speaker_available": broker_ready,
        "speak_responses": app.config.voice.speak_responses,
        "playback_binary_runtime": app.config.voice.playback_binary,
        "playback_binary_runtime_detected": playback_binary or str(app.config.voice.playback_binary),
        "playback_player": type(player).__name__ if player is not None else None,
        "playback_status": playback_status,
        "queue_counts": queue_counts,
    }
    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error"),
        warnings=warnings,
    )


def _voice_layer_projection_queue_barge_in(
    app: DaemonApp,
    *,
    queue_snapshot: dict[str, Any],
    event_snapshot: dict[str, Any],
) -> dict[str, Any]:
    readiness = queue_snapshot.get("status", "unknown")
    dependency_status = {
        "voice_queue_table": str(queue_snapshot.get("status", "unknown")),
        "broker_for_drain": "ok" if app.voice_broker is not None else "missing",
        "barge_in_capability": "ok" if app.voice_gateway is not None else "missing",
    }
    configured_value = {
        "speak_responses": app.config.voice.speak_responses,
        "queue_persisted": app.config.voice.queue_persisted,
    }
    effective_value = {
        "queue_counts": queue_snapshot.get("counts"),
        "queue_size": queue_snapshot.get("queue_size"),
        "active": queue_snapshot.get("active"),
        "tail": queue_snapshot.get("tail"),
        "latest_turn_id": queue_snapshot.get("latest_turn_id"),
        "latest_voice_id": queue_snapshot.get("latest_voice_id"),
    }
    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=queue_snapshot.get("latest_error") or event_snapshot.get("latest_safe_error"),
        warnings=queue_snapshot.get("warning"),
    )


def _voice_layer_projection_errors(
    app: DaemonApp,
    *,
    queue_snapshot: dict[str, Any],
    event_snapshot: dict[str, Any],
    compatibility_warnings: list[str] | None = None,
) -> dict[str, Any]:
    readiness = "ok" if app.started else "unknown"
    configured_value = {
        "voice_enabled": app.config.voice.enabled,
        "speak_responses": app.config.voice.speak_responses,
        "broker_enabled": app.config.voice.broker_enabled,
        "ptt_mode": app.config.voice.ptt_mode,
    }
    effective_value = {
        "latest_safe_error": event_snapshot.get("latest_safe_error") or queue_snapshot.get("latest_error"),
        "cancellation_reason": event_snapshot.get("latest_cancel_reason"),
        "event_snapshot_status": event_snapshot.get("status"),
    }
    dependency_status = {
        "event_store": "ok" if app.started else "unknown",
        "voice_queue": "ok" if queue_snapshot.get("status") == "ok" else queue_snapshot.get("status", "unknown"),
    }
    warnings = []
    if compatibility_warnings:
        warnings.extend(compatibility_warnings)
    if event_snapshot.get("status") == "invalid":
        warnings.append("Event snapshot is invalid.")
    if queue_snapshot.get("status") == "invalid":
        warnings.append("Queue snapshot is invalid.")
    return _layer_projection(
        configured_value=configured_value,
        effective_value=effective_value,
        readiness=readiness,
        dependency_status=dependency_status,
        latest_safe_error=event_snapshot.get("latest_safe_error") or queue_snapshot.get("latest_error"),
        warnings=warnings,
    )


def _voice_projection(
    app: DaemonApp,
    *,
    queue_snapshot: dict[str, Any] | None = None,
    event_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if queue_snapshot is None:
        queue_snapshot = _collect_voice_queue_snapshot(app)
    if event_snapshot is None:
        event_snapshot = _collect_voice_events_snapshot(app)

    queue_status = queue_snapshot.get("status", "invalid")
    queue_warning = queue_snapshot.get("warning")
    queue_size = int(queue_snapshot.get("queue_size", 0))
    queue_tail = list(queue_snapshot.get("tail") or [])
    speaking = queue_snapshot.get("active", 0) > 0
    stt_ready = _status_from_ready(
        started=app.started,
        enabled=app.config.voice.enabled,
        ready=app.voice_stt is not None,
    )
    tts_ready = _status_from_ready(
        started=app.started,
        enabled=app.config.voice.enabled and app.config.voice.speak_responses,
        ready=app.voice_broker is not None,
    )

    warnings = []
    if queue_warning is not None:
        warnings.append(queue_warning)
    if not app.config.voice.enabled:
        warnings.append("Voice is disabled.")

    return {
        "enabled": _projection(
            value=app.config.voice.enabled,
            effective_value=app.config.voice.enabled,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "speak_responses": _projection(
            value=app.config.voice.speak_responses,
            effective_value=app.config.voice.speak_responses,
            source="config",
            status="ok",
            editable_later=True,
        ),
        "broker_enabled": _projection(
            value=app.config.voice.broker_enabled,
            effective_value=app.config.voice.broker_enabled,
            source="config",
            status="ok",
            editable_later=True,
        ),
        "default_tts": _projection(
            value=app.config.voice.default_tts,
            effective_value=app.config.voice.default_tts,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "default_stt": _projection(
            value=app.config.voice.default_stt,
            effective_value=app.config.voice.default_stt,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "ptt_mode": _projection(
            value=app.config.voice.ptt_mode,
            effective_value=app.config.voice.ptt_mode,
            source="config",
            status="ok" if app.config.voice.ptt_mode in CANONICAL_PTT_MODES else "invalid",
            editable_later=True,
        ),
        "queue_size": _projection(
            value=queue_size,
            effective_value=queue_size,
            source="runtime_detected" if app.started else "unknown",
            status=queue_status,
            editable_later=False,
            warning=queue_warning,
        ),
        "speaking": _projection(
            value=speaking,
            effective_value=speaking,
            source="runtime_detected" if app.started else "unknown",
            status=queue_status,
            editable_later=False,
            warning=queue_warning,
        ),
        "active_tail": _projection(
            value=queue_tail,
            effective_value=queue_tail,
            source="runtime_detected" if app.started else "unknown",
            status=queue_status,
            editable_later=False,
            warning=queue_warning,
        ),
        "tts_ready": _projection(
            value=tts_ready == "ok",
            effective_value=tts_ready == "ok",
            source="runtime_detected",
            status=tts_ready,
            editable_later=False,
            warning=None if app.started else "Start required to build speech runtime.",
        ),
        "stt_ready": _projection(
            value=stt_ready == "ok",
            effective_value=stt_ready == "ok",
            source="runtime_detected",
            status=stt_ready,
            editable_later=False,
            warning=None if app.started else "Start required to build speech runtime.",
        ),
        "recorder_ready": _projection(
            value=app.voice_recorder is not None,
            effective_value=app.voice_recorder is not None,
            source="runtime_detected",
            status=_status_from_ready(started=app.started, enabled=True, ready=app.voice_recorder is not None),
            editable_later=False,
            warning=None if app.started else "Start required to build voice runtime.",
        ),
        "barge_in_trace": _projection(
            value=queue_tail,
            effective_value=queue_tail,
            source="runtime_detected" if app.started else "unknown",
            status=queue_status,
            editable_later=False,
            warning=queue_warning,
        ),
        "latest_safe_error": _projection(
            value=event_snapshot.get("latest_safe_error") or queue_snapshot.get("latest_error"),
            effective_value=event_snapshot.get("latest_safe_error") or queue_snapshot.get("latest_error"),
            source="runtime_detected" if app.started else "unknown",
            status="ok" if not (event_snapshot.get("latest_safe_error") or queue_snapshot.get("latest_error")) else "invalid",
            editable_later=False,
            warning=None,
        ),
        "cancellation_reason": _projection(
            value=event_snapshot.get("latest_cancel_reason"),
            effective_value=event_snapshot.get("latest_cancel_reason"),
            source="runtime_detected" if app.started else "unknown",
            status="ok" if not event_snapshot.get("latest_cancel_reason") else "invalid",
            editable_later=False,
            warning=None,
        ),
        "warnings": _projection(
            value=warnings,
            effective_value=warnings,
            source="runtime_detected",
            status="ok" if not warnings else "invalid",
            editable_later=False,
            warning="Warnings detected." if warnings else None,
        ),
    }


def _tools_projection(
    app: DaemonApp,
    settings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    specs = []
    try:
        specs = [
            {
                "name": spec.name,
                "risk": spec.risk,
                "description": spec.description,
            }
            for spec in app.tool_registry.list_specs()
        ]
        status = "ok"
        warning = None
    except Exception as exc:
        status = "invalid"
        warning = str(exc)

    network_specs = _network_tool_specs(specs)
    has_network_tool = bool(network_specs)
    tool_registry_value = "unknown" if status == "invalid" else ("registered" if specs else "missing")
    configured_enabled, configured_status = _settings_value(settings, "tools.enabled")
    configured_tools_enabled: bool | None
    tools_master_source = "settings"
    tools_master_warning = None
    if configured_status == "ok":
        configured_tools_enabled = bool(configured_enabled)
        tools_master_status = "ok"
    elif configured_status == "invalid":
        configured_tools_enabled = None
        tools_master_status = "invalid"
        tools_master_warning = "Configured tools.enabled is invalid."
    else:
        configured_tools_enabled = bool(specs) if status != "invalid" else None
        tools_master_source = "runtime_detected" if status != "invalid" else "unknown"
        tools_master_status = status if status == "invalid" else ("ok" if specs else "unknown")
    if configured_tools_enabled is None:
        tools_master_flag = "unknown"
    else:
        tools_master_flag = "enabled" if configured_tools_enabled else "disabled"
    approval_required = []
    if app.config.security.require_approval_for_shell:
        approval_required.append("shell")
    if app.config.security.require_approval_for_file_write:
        approval_required.append("file_write")
    if app.config.security.require_approval_for_network:
        approval_required.append("network")
    internet_warning = None
    if status == "invalid":
        internet_state = "unknown"
        internet_status = "invalid"
        internet_warning = warning
    elif has_network_tool:
        internet_state = "available"
        internet_status = "ok"
    else:
        internet_state = "unavailable"
        internet_status = "missing"
        internet_warning = "Internet unavailable: no network/search tool registered"
    network_search_tool_status = "ok" if has_network_tool else ("invalid" if status == "invalid" else "missing")
    network_search_tool_value = "unknown" if status == "invalid" else ("registered" if has_network_tool else "missing")
    apply_capability_warning = TOOLS_POLICY_RESTART_BLOCKER
    requires_restart_value = True
    blocker_value = internet_warning or apply_capability_warning

    return {
        "tools_enabled": _projection(
            value=bool(specs),
            effective_value=bool(specs),
            source="runtime_detected",
            status=status if specs or status == "invalid" else "missing",
            editable_later=False,
            warning=warning,
        ),
        "tools_master_flag": _projection(
            value=tools_master_flag,
            effective_value=tools_master_flag,
            source=tools_master_source,
            status=tools_master_status,
            editable_later=True,
            warning=tools_master_warning,
        ),
        "registered": _projection(
            value=specs,
            effective_value=specs,
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "risk_classes": _projection(
            value=sorted({spec.get("risk") for spec in specs}),
            effective_value=sorted({spec.get("risk") for spec in specs}),
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "internet_capability": _projection(
            value={
                "state": internet_state,
                "registered_network_tools": [spec["name"] for spec in network_specs],
            },
            effective_value={
                "state": internet_state,
                "registered_network_tools": [spec["name"] for spec in network_specs],
            },
            source="runtime_detected",
            status=internet_status,
            editable_later=False,
            warning=internet_warning,
        ),
        "network_search_tool": _projection(
            value=network_search_tool_value,
            effective_value=network_search_tool_value,
            source="runtime_detected" if status != "invalid" else "unknown",
            status=network_search_tool_status,
            editable_later=False,
            warning=None if has_network_tool else "no network/search tool registered",
        ),
        "network_policy": _projection(
            value="approval_required" if app.config.security.require_approval_for_network else "allowed",
            effective_value="approval_required" if app.config.security.require_approval_for_network else "allowed",
            source="config",
            status="ok",
            editable_later=True,
            warning=None if has_network_tool else "network enabled but no network tool registered",
        ),
        "approval_required_tools": _projection(
            value=approval_required,
            effective_value=approval_required,
            source="config",
            status="ok" if approval_required else "missing",
            editable_later=True,
            warning=None,
        ),
        "tool_registry_status": _projection(
            value=tool_registry_value,
            effective_value=tool_registry_value,
            source="runtime_detected",
            status=status if specs or status == "invalid" else "missing",
            editable_later=False,
            warning=warning,
        ),
        "apply_capability": _projection(
            value="no",
            effective_value="no",
            source="runtime_detected",
            status="unsupported",
            editable_later=True,
            warning=apply_capability_warning,
        ),
        "requires_restart": _projection(
            value=requires_restart_value,
            effective_value=requires_restart_value,
            source="runtime_detected",
            status="ok",
            editable_later=True,
            warning=apply_capability_warning,
        ),
        "blocker": _projection(
            value=blocker_value,
            effective_value=blocker_value,
            source="runtime_detected",
            status="invalid" if blocker_value else "ok",
            editable_later=False,
            warning=blocker_value,
        ),
        "latest_safe_error": _projection(
            value=warning,
            effective_value=warning,
            source="runtime_detected",
            status="ok" if warning is None else "invalid",
            editable_later=False,
            warning=warning,
        ),
    }


def _preview_disabled(value: Any, reason: str) -> dict[str, Any]:
    return {"value": value, "reason": reason}


def _preview_field(
    *,
    section: str,
    field_id: str,
    label: str,
    current: Any,
    effective: Any | None = None,
    status: str = "ok",
    source: str = "runtime_detected",
    allowed_values: list[Any] | tuple[Any, ...] | None = None,
    disabled_values: list[dict[str, Any]] | None = None,
    warning: str | None = None,
    blocker: str | None = None,
    dependencies: list[str] | tuple[str, ...] | None = None,
    invalidates: list[str] | tuple[str, ...] | None = None,
    requires_restart: bool = False,
    requires_reload: bool = False,
    editable_now: bool = False,
    editable_later: bool = False,
    developer_only: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"{section}.{field_id}",
        "label": label,
        "current": current,
        "effective": current if effective is None else effective,
        "status": _normalize_status(status),
        "source": _normalize_source(source),
        "allowed_values": list(allowed_values or []),
        "disabled_values": list(disabled_values or []),
        "warning": warning,
        "blocker": blocker,
        "dependencies": list(dependencies or []),
        "invalidates": list(invalidates or []),
        "requires_restart": bool(requires_restart),
        "requires_reload": bool(requires_reload),
        "editable_now": bool(editable_now),
        "editable_later": bool(editable_later),
        "developer_only": bool(developer_only),
    }


def _preview_section(section_id: str, label: str, fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": section_id,
        "label": label,
        "fields": fields,
    }


def _support_bool(value: Any) -> bool | None:
    if isinstance(value, dict):
        value = _runtime_projection_value(value)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower() if value is not None else ""
    if normalized in {"yes", "true", "supported", "available", "ok", "enabled", "found", "logged_in"}:
        return True
    if normalized in {"no", "false", "unsupported", "missing", "disabled", "not_found"}:
        return False
    return None


def _support_word(value: Any) -> str:
    supported = _support_bool(value)
    if supported is True:
        return KNOWN_PROVIDER_SUPPORT_YES
    if supported is False:
        return KNOWN_PROVIDER_SUPPORT_NO
    return KNOWN_PROVIDER_SUPPORT_UNKNOWN


def _projection_status(projection: Any) -> str:
    if isinstance(projection, dict):
        return _normalize_status(str(projection.get("status", "unknown")))
    return "unknown"


def _projection_warning(projection: Any) -> str | None:
    if isinstance(projection, dict):
        warning = projection.get("warning")
        return str(warning) if warning else None
    return None


def _safe_command_probe(commands: tuple[str, ...] | list[str]) -> tuple[str, str | None, str | None]:
    last_candidate = None
    for command in commands:
        status, resolved, exists = _safe_is_executable(command)
        if resolved:
            last_candidate = resolved
        if exists:
            return "ok", command, resolved
    return "missing", None, last_candidate


def _safe_python_module_probe(modules: tuple[str, ...] | list[str]) -> tuple[str, str | None, str | None]:
    for module in modules:
        module_name = str(module or "").strip()
        if not module_name:
            continue
        try:
            spec = importlib.util.find_spec(module_name)
        except Exception:
            spec = None
        if spec is not None:
            return "ok", f"python-module:{module_name}", None
    return "missing", None, None


def _safe_model_env_hints(env_names: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "")).strip()
        if not raw:
            continue
        models.append(
            {
                "id": raw,
                "label": raw,
                "source": f"env:{env_name}",
                "available": True,
            }
        )
    return models


def _build_local_capabilities(app: DaemonApp) -> dict[str, Any]:
    runtimes: list[dict[str, Any]] = []
    all_models: list[dict[str, Any]] = []
    default_model = str(getattr(app.config.brain, "default_model", "") or "").strip()
    for probe in LOCAL_RUNTIME_PROBES:
        runtime_id = str(probe["id"])
        commands = tuple(probe.get("commands", ()))
        command_status, command_name, command_path = _safe_command_probe(commands)
        base_module_status = "missing"
        if probe.get("base_python_modules"):
            base_module_status, _, _ = _safe_python_module_probe(
                tuple(probe.get("base_python_modules", ()))
            )
        if command_status != "ok":
            module_status, module_name, module_path = _safe_python_module_probe(
                tuple(probe.get("python_modules", ()))
            )
            if module_status == "ok":
                command_status, command_name, command_path = module_status, module_name, module_path
        models = _safe_model_env_hints(tuple(probe.get("model_env", ())))
        if default_model and runtime_id in default_model.lower() and not models:
            models.append(
                {
                    "id": default_model,
                    "label": default_model,
                    "source": "config:brain.default_model",
                    "available": False,
                }
            )
        available = command_status == "ok"
        configured = available or bool(models)
        model_ready = any(bool(model.get("available")) for model in models)
        status = "ok" if available and (model_ready or runtime_id not in {"ollama", "bielik", "mistral"}) else "missing"
        warning = None
        blocker = None
        if not available:
            if runtime_id == "mlx" and base_module_status == "ok":
                warning = "Base MLX package detected but MLX-LM generation package missing."
                blocker = warning
            elif models:
                warning = f"{probe['label']} model configured but {probe['label']} runtime not detected."
                blocker = warning
            else:
                warning = "Local runtime is not detected from safe command/env probes."
        if runtime_id in {"ollama", "bielik", "mistral"} and not models:
            blocker = "Local provider has no safely detected local model."
        runtimes.append(
            {
                "id": runtime_id,
                "label": str(probe["label"]),
                "kind": str(probe.get("kind", "Local")),
                "planned": bool(probe.get("planned", False)),
                "configured": configured,
                "available": available,
                "status": _normalize_status(status),
                "command": command_name,
                "command_path": command_path,
                "models": models,
                "local_models": models,
                "warning": warning,
                "blocker": blocker,
            }
        )
        all_models.extend(models)

    return {
        "runtimes": runtimes,
        "local_runtime_status": "ok" if any(runtime["available"] for runtime in runtimes) else "missing",
        "local_models": all_models,
    }


def _normalize_brain_provider_capability(raw: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(raw.get("name") or raw.get("id") or "").strip()
    claude_contract = raw.get("claude_cli_contract")
    if not isinstance(claude_contract, dict):
        claude_contract = {}
    supported_models = [str(model) for model in raw.get("supported_models", []) if str(model)]
    current_model_projection = raw.get("current_model")
    allowed_efforts = _runtime_projection_value(raw.get("allowed_effort_values")) or []
    if not isinstance(allowed_efforts, list):
        allowed_efforts = []
    command = raw.get("provider_command_status")
    command_value = _support_word(command)
    configured = bool(raw.get("configured", False))
    available = bool(raw.get("available", False)) and command_value != KNOWN_PROVIDER_SUPPORT_NO
    status = _normalize_status(str(raw.get("status") or "ok"))
    warnings = [
        warning
        for warning in (
            raw.get("warning"),
            _projection_warning(raw.get("current_model")),
            _projection_warning(raw.get("effort")),
            _projection_warning(raw.get("fast")),
            _projection_warning(raw.get("latest_error")),
            _projection_warning(command),
            *(claude_contract.get("contract_warnings") or []),
        )
        if warning
    ]
    blocker = raw.get("warning") or claude_contract.get("blocker")
    if not available and configured:
        status = "missing"
        warnings.append(str(blocker or "Provider is configured but not available from safe runtime probes."))
    models = [
        {
            "id": model,
            "label": model,
            "available": True,
            "configured": model == _runtime_projection_value(current_model_projection),
        }
        for model in supported_models
    ]
    return {
        "id": provider_id,
        "label": str(raw.get("display_name") or provider_id),
        "kind": str(raw.get("kind") or "Provider"),
        "configured": configured,
        "available": available,
        "models": models,
        "current_model": _runtime_projection_value(current_model_projection),
        "allowed_effort_values": allowed_efforts,
        "fast_supported": _support_bool(raw.get("fast_supported")) is True,
        "context_info": {
            "budget_chars": _runtime_projection_value(raw.get("context_window_chars")),
            "source": "config",
        },
        "tools_supported": _support_bool(raw.get("tools_support")) is True,
        "streaming_supported": _support_bool(raw.get("streaming_support")) is True,
        "streaming_supported_state": claude_contract.get("streaming_supported"),
        "provider_command_status": command_value,
        "command_status": claude_contract.get("command_status") or command_value,
        "latest_provider_error": _runtime_projection_value(raw.get("latest_error")),
        "status": status,
        "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
        "developer_only": provider_id == "mock" or str(raw.get("kind")) == "Developer/Test",
        "blocker": blocker,
        "raw": raw,
        **(
            {
                key: claude_contract.get(key)
                for key in (
                    "provider_id",
                    "transport",
                    "command",
                    "auth_status",
                    "version",
                    "version_status",
                    "selected_model",
                    "effective_model",
                    "model_source",
                    "allowed_models",
                    "selected_effort",
                    "effective_effort",
                    "effort_source",
                    "fast_supported_state",
                    "permission_mode",
                    "tools",
                    "allowed_tools",
                    "disallowed_tools",
                    "mcp_config_path",
                    "mcp_config_status",
                    "strict_mcp_config",
                    "output_format",
                    "input_format",
                    "partial_messages_supported",
                    "hook_events_supported",
                    "apply_semantics",
                    "apply_capable",
                    "apply_semantics_reason",
                    "apply_eligibility",
                    "command_preview",
                )
                if key in claude_contract
            }
            if claude_contract
            else {}
        ),
    }


def _local_provider_capability_nodes(local_capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for runtime in local_capabilities.get("runtimes", []):
        runtime_id = str(runtime.get("id") or "")
        if not runtime_id:
            continue
        models = [
            {
                "id": str(model.get("id")),
                "label": str(model.get("label") or model.get("id")),
                "available": bool(model.get("available")),
                "configured": False,
            }
            for model in runtime.get("models", [])
            if model.get("id")
        ]
        available = bool(runtime.get("available")) and bool(models)
        blocker = runtime.get("blocker")
        if not available and not blocker:
            blocker = "Local runtime or local model is missing."
        providers.append(
            {
                "id": runtime_id,
                "label": str(runtime.get("label") or runtime_id),
                "kind": str(runtime.get("kind") or "Local"),
                "configured": bool(runtime.get("configured")),
                "available": available,
                "models": models,
                "current_model": models[0]["id"] if models else None,
                "allowed_effort_values": [],
                "fast_supported": False,
                "context_info": {"budget_chars": None, "source": "unknown"},
                "tools_supported": False,
                "streaming_supported": bool(runtime.get("available")),
                "command_status": KNOWN_PROVIDER_SUPPORT_YES
                if runtime.get("available")
                else KNOWN_PROVIDER_SUPPORT_NO,
                "latest_provider_error": None,
                "status": "ok" if available else "missing",
                "warnings": [str(runtime.get("warning"))] if runtime.get("warning") else [],
                "developer_only": False,
                "blocker": blocker,
                "local_runtime": True,
            }
        )
    return providers


def _build_brain_capabilities(
    *,
    brain_projection: dict[str, Any],
    local_capabilities: dict[str, Any],
) -> dict[str, Any]:
    raw_providers = _runtime_projection_value(brain_projection.get("providers")) or []
    providers = [
        _normalize_brain_provider_capability(provider)
        for provider in raw_providers
        if isinstance(provider, dict)
    ]
    known_ids = {provider["id"] for provider in providers}
    for provider in _local_provider_capability_nodes(local_capabilities):
        if provider["id"] not in known_ids:
            providers.append(provider)
            known_ids.add(provider["id"])

    current_provider = _runtime_projection_value(brain_projection.get("current_adapter"))
    current_model = None
    for provider in providers:
        if provider["id"] == current_provider:
            current_model = provider.get("current_model")
            break
    return {
        "providers": providers,
        "current_provider": current_provider,
        "current_model": current_model,
        "provider_sessions_are_memory": _runtime_projection_value(
            brain_projection.get("provider_sessions_are_memory")
        ),
    }


def _build_voice_capabilities(
    app: DaemonApp,
    *,
    tts_projection: dict[str, Any],
    stt_projection: dict[str, Any],
    queue_snapshot: dict[str, Any],
) -> dict[str, Any]:
    configured_tts = str(app.config.voice.default_tts or "").strip()
    configured_stt = str(app.config.voice.default_stt or "").strip()
    tts_binary_status, tts_binary, _ = _safe_probe_tts_binary(
        configured_tts,
        str(app.config.voice.supertonic_binary or ""),
    )
    stt_package_status, stt_package_name = _safe_probe_stt_package(configured_stt)
    playback_status, playback_binary, _ = _safe_probe_playback_binary(
        str(app.config.voice.playback_binary or "")
    )

    tts_providers = [
        {
            "id": "mock",
            "label": "Mock TTS",
            "configured": configured_tts == "mock",
            "available": True,
            "models": [{"id": "mock", "label": "mock", "available": True}],
            "voice_ids": [],
            "voice_profiles": [],
            "controls": {
                "speed": False,
                "style": False,
                "stability": False,
                "similarity": False,
                "streaming": False,
                "continuity": False,
            },
            "developer_only": True,
            "status": "ok",
        },
        {
            "id": "supertonic",
            "label": "Supertonic",
            "configured": configured_tts == "supertonic",
            "available": tts_binary_status == "ok",
            "models": [{"id": "supertonic", "label": "supertonic", "available": tts_binary_status == "ok"}],
            "voice_ids": [app.config.voice.supertonic_voice] if app.config.voice.supertonic_voice else [],
            "voice_profiles": [app.config.voice.supertonic_lang] if app.config.voice.supertonic_lang else [],
            "controls": {
                "speed": True,
                "style": False,
                "stability": False,
                "similarity": False,
                "streaming": False,
                "continuity": bool(app.config.voice.broker_enabled),
            },
            "developer_only": False,
            "status": "ok" if tts_binary_status == "ok" else "missing",
            "binary": tts_binary,
        },
    ]
    if configured_tts and configured_tts not in {"mock", "supertonic"}:
        tts_providers.append(
            {
                "id": configured_tts,
                "label": configured_tts,
                "configured": True,
                "available": False,
                "models": [],
                "voice_ids": [],
                "voice_profiles": [],
                "controls": {
                    "speed": False,
                    "style": False,
                    "stability": False,
                    "similarity": False,
                    "streaming": False,
                    "continuity": False,
                },
                "developer_only": False,
                "status": "missing",
            }
        )

    stt_providers = [
        {
            "id": "mock",
            "label": "Mock STT",
            "configured": configured_stt == "mock",
            "available": True,
            "models": [{"id": "mock", "label": "mock", "available": True}],
            "endpointing_support": False,
            "developer_only": True,
            "status": "ok",
        },
        {
            "id": "mlx_whisper",
            "label": "MLX Whisper",
            "configured": configured_stt in {"mlx_whisper", "mlx-whisper"},
            "available": stt_package_status == KNOWN_PROVIDER_SUPPORT_YES,
            "models": [
                {
                    "id": app.config.voice.stt_model,
                    "label": app.config.voice.stt_model,
                    "available": bool(app.config.voice.stt_model),
                }
            ]
            if app.config.voice.stt_model
            else [],
            "endpointing_support": True,
            "developer_only": False,
            "status": "ok" if stt_package_status == KNOWN_PROVIDER_SUPPORT_YES else "missing",
            "package": stt_package_name,
        },
    ]
    if configured_stt and configured_stt not in {"mock", "mlx_whisper", "mlx-whisper"}:
        stt_providers.append(
            {
                "id": configured_stt,
                "label": configured_stt,
                "configured": True,
                "available": False,
                "models": [],
                "endpointing_support": False,
                "developer_only": False,
                "status": "missing",
            }
        )

    return {
        "tts_providers": tts_providers,
        "tts_models": [
            model
            for provider in tts_providers
            for model in provider.get("models", [])
        ],
        "voice_ids": [
            voice_id
            for provider in tts_providers
            for voice_id in provider.get("voice_ids", [])
        ],
        "voice_profiles": [
            profile
            for provider in tts_providers
            for profile in provider.get("voice_profiles", [])
        ],
        "supported_voice_controls": {
            "speed": configured_tts == "supertonic",
            "style": False,
            "stability": False,
            "similarity": False,
            "streaming": False,
            "continuity": bool(app.config.voice.broker_enabled),
        },
        "stt_providers": stt_providers,
        "stt_models": [
            model
            for provider in stt_providers
            for model in provider.get("models", [])
        ],
        "endpointing_support": app.config.voice.ptt_mode in CANONICAL_PTT_MODES,
        "ptt_support": True,
        "playback_support": playback_status == "ok",
        "playback_binary": playback_binary,
        "cancellation_support": app.voice_cancellation is not None,
        "queue_status": queue_snapshot.get("status", "unknown"),
        "tts_readiness": _projection_status(tts_projection.get("readiness")),
        "stt_readiness": _projection_status(stt_projection.get("readiness")),
    }


def _build_tools_capabilities(
    app: DaemonApp,
    *,
    tools_projection: dict[str, Any],
) -> dict[str, Any]:
    registered = _runtime_projection_value(tools_projection.get("registered")) or []
    network_tools = _network_tool_specs(registered if isinstance(registered, list) else [])
    internet_capability = _runtime_projection_value(tools_projection.get("internet_capability")) or {}
    approval_required = _runtime_projection_value(tools_projection.get("approval_required_tools")) or []
    live_toggle_blocker = TOOLS_POLICY_RESTART_BLOCKER
    return {
        "tools_enabled": bool(registered),
        "tools_master_flag": _runtime_projection_value(tools_projection.get("tools_master_flag")) or "unknown",
        "internet_capability": internet_capability,
        "network_policy": _runtime_projection_value(tools_projection.get("network_policy")),
        "network_search_tool": _runtime_projection_value(tools_projection.get("network_search_tool")) or "unknown",
        "registered_network_tools": [spec["name"] for spec in network_tools],
        "approval_required_tools": approval_required if isinstance(approval_required, list) else [],
        "tool_registry_status": _runtime_projection_value(tools_projection.get("tool_registry_status")),
        "apply_capability": _runtime_projection_value(tools_projection.get("apply_capability")) or "no",
        "requires_restart": bool(_runtime_projection_value(tools_projection.get("requires_restart"))),
        "blocker": _runtime_projection_value(tools_projection.get("blocker")),
        "apply_capabilities": {
            "tools.enabled": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": "tool registry enable/disable is not apply-capable in POC; requires restart",
            },
            "tools.network_enabled": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": "network capability is registry-backed; no live network tool toggle is wired in POC",
            },
            "security.network_enabled": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": "network security toggle is not backed by a live runtime setting in POC",
            },
            "security.require_approval_for_network": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": live_toggle_blocker,
            },
            "security.require_approval_for_shell": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": live_toggle_blocker,
            },
            "security.require_approval_for_file_write": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": live_toggle_blocker,
            },
            "security.destructive_tools_enabled": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": "destructive tools remain read-only/high-risk in this POC",
            },
            "destructive_tools_enabled": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": "destructive tools remain read-only/high-risk in this POC",
            },
            "provider_tools_enabled": {
                "apply_capable": False,
                "requires_restart": True,
                "blocker": "provider tool support is provider capability, not a live toggle in this POC",
            },
        },
    }


def _build_capability_graph(
    app: DaemonApp,
    *,
    brain_projection: dict[str, Any],
    tools_projection: dict[str, Any],
    tts_projection: dict[str, Any],
    stt_projection: dict[str, Any],
    queue_snapshot: dict[str, Any],
) -> dict[str, Any]:
    local_capabilities = _build_local_capabilities(app)
    return {
        "brain_capabilities": _build_brain_capabilities(
            brain_projection=brain_projection,
            local_capabilities=local_capabilities,
        ),
        "voice_capabilities": _build_voice_capabilities(
            app,
            tts_projection=tts_projection,
            stt_projection=stt_projection,
            queue_snapshot=queue_snapshot,
        ),
        "tools_capabilities": _build_tools_capabilities(
            app,
            tools_projection=tools_projection,
        ),
        "local_capabilities": local_capabilities,
    }


def _provider_by_id(capability_graph: dict[str, Any], provider_id: Any) -> dict[str, Any] | None:
    for provider in capability_graph.get("brain_capabilities", {}).get("providers", []):
        if str(provider.get("id")) == str(provider_id):
            return provider
    return None


def _voice_provider_by_id(providers: list[dict[str, Any]], provider_id: Any) -> dict[str, Any] | None:
    for provider in providers:
        if str(provider.get("id")) == str(provider_id):
            return provider
    return None


def _credentials_or_command_status_value(provider: dict[str, Any]) -> str:
    command_status = provider.get("command_status")
    if _support_bool(command_status) is True:
        return "ok"
    if _support_bool(command_status) is False:
        return "missing"
    if provider and provider.get("available") is False:
        return "unavailable"
    return "unknown"


def _build_settings_preview(
    app: DaemonApp,
    *,
    brain_projection: dict[str, Any],
    tools_projection: dict[str, Any],
    queue_snapshot: dict[str, Any],
    event_snapshot: dict[str, Any],
    tts_projection: dict[str, Any],
    stt_projection: dict[str, Any],
    capability_graph: dict[str, Any],
) -> dict[str, Any]:
    section = "brain_provider"
    brain_capabilities = capability_graph["brain_capabilities"]
    providers = brain_capabilities["providers"]
    current_provider_id = brain_capabilities.get("current_provider")
    current_provider = _provider_by_id(capability_graph, current_provider_id) or {}
    raw_provider = current_provider.get("raw", {}) if isinstance(current_provider.get("raw"), dict) else {}
    provider_disabled = []
    for provider in providers:
        reason = None
        if provider.get("developer_only"):
            reason = "Developer/Test only."
        elif not provider.get("available"):
            reason = provider.get("blocker") or "Provider is unavailable."
        if reason:
            provider_disabled.append(_preview_disabled(provider.get("id"), str(reason)))
    model_values = [model["id"] for model in current_provider.get("models", []) if model.get("id")]
    current_model = current_provider.get("current_model")
    model_status = _projection_status(raw_provider.get("current_model")) if raw_provider else "ok"
    model_blocker = None
    model_warning = _projection_warning(raw_provider.get("current_model")) if raw_provider else None
    if not model_values:
        model_status = "missing"
        model_blocker = current_provider.get("blocker") or "No model is available for the selected provider."
    elif current_model not in model_values:
        model_status = "invalid"
        model_blocker = "Selected model is not allowed for the selected provider; reset required."
    effort_allowed = list(current_provider.get("allowed_effort_values") or [])
    effort_value = _runtime_projection_value(raw_provider.get("effort")) if raw_provider else None
    effort_status = _projection_status(raw_provider.get("effort")) if raw_provider else "unsupported"
    effort_warning = _projection_warning(raw_provider.get("effort")) if raw_provider else None
    fast_value = _runtime_projection_value(raw_provider.get("fast")) if raw_provider else None
    fast_supported = bool(current_provider.get("fast_supported"))
    fast_status = _projection_status(raw_provider.get("fast")) if raw_provider else ("ok" if fast_supported else "unsupported")
    fast_disabled = []
    if not fast_supported:
        fast_disabled.append(_preview_disabled(True, "Selected provider/model does not support fast mode."))
        if fast_value is True:
            fast_status = "unsupported"
    credentials_or_command_status = _credentials_or_command_status_value(current_provider)
    credentials_or_command_field_status = {
        "ok": "ok",
        "missing": "missing",
        "invalid": "invalid",
        "unavailable": "missing",
        "unknown": "unknown",
    }.get(credentials_or_command_status, "unknown")
    provider_command_status = current_provider.get("command_status")
    provider_command_ready = _support_bool(provider_command_status)
    auth_status = str(current_provider.get("auth_status") or "unknown")
    auth_field_status = "ok" if auth_status == "logged_in" else ("missing" if auth_status == "missing" else "unknown")
    apply_semantics = str(current_provider.get("apply_semantics") or "not_apply_capable")
    apply_semantics_status = "ok" if apply_semantics == "next_turn" else (
        "invalid" if apply_semantics == "not_apply_capable" else "unsupported"
    )
    brain_next_turn_apply = apply_semantics == "next_turn"
    selected_model = current_provider.get("selected_model")
    effective_model = current_provider.get("effective_model")
    selected_effort = current_provider.get("selected_effort")
    effective_effort = current_provider.get("effective_effort")
    permission_mode = current_provider.get("permission_mode") or "unknown"
    command_preview = current_provider.get("command_preview")
    brain_fields = {
        "provider": _preview_field(
            section=section,
            field_id="provider",
            label="Provider",
            current=current_provider_id,
            status=current_provider.get("status", "unknown"),
            source="settings",
            allowed_values=[provider["id"] for provider in providers],
            disabled_values=provider_disabled,
            warning="Developer/Test provider is active." if current_provider.get("developer_only") else None,
            blocker=current_provider.get("blocker") if not current_provider.get("available", True) else None,
            invalidates=["brain_provider.model", "brain_provider.effort", "brain_provider.fast", "brain_provider.tools_support", "brain_provider.streaming_support", "brain_provider.context_budget", "brain_provider.command_status", "brain_provider.credentials_or_command_status", "brain_provider.auth_status", "brain_provider.command_preview", "brain_provider.apply_semantics"],
            requires_reload=not brain_next_turn_apply,
            editable_now=brain_next_turn_apply,
            editable_later=True,
            developer_only=bool(current_provider.get("developer_only")),
        ),
        "provider_id": _preview_field(
            section=section,
            field_id="provider_id",
            label="Provider id",
            current=current_provider.get("provider_id") or current_provider_id,
            status="ok" if current_provider_id else "unknown",
            source="runtime_detected",
        ),
        "transport": _preview_field(
            section=section,
            field_id="transport",
            label="Transport",
            current=current_provider.get("transport") or "unknown",
            status="ok" if current_provider.get("transport") else "unknown",
            source="runtime_detected",
            dependencies=["brain_provider.provider"],
        ),
        "model": _preview_field(
            section=section,
            field_id="model",
            label="Model",
            current=current_model,
            status=model_status,
            source="settings" if raw_provider else "runtime_detected",
            allowed_values=model_values,
            warning=model_warning,
            blocker=model_blocker,
            dependencies=["brain_provider.provider"],
            invalidates=["brain_provider.effort", "brain_provider.fast", "brain_provider.context_budget"],
            requires_reload=not brain_next_turn_apply,
            editable_now=brain_next_turn_apply,
            editable_later=True,
        ),
        "selected_model": _preview_field(
            section=section,
            field_id="selected_model",
            label="Selected model",
            current=selected_model,
            status="ok" if selected_model else "missing",
            source="settings",
            dependencies=["brain_provider.provider"],
        ),
        "effective_model": _preview_field(
            section=section,
            field_id="effective_model",
            label="Effective model",
            current=effective_model,
            status="ok" if effective_model else "unknown",
            source="runtime_detected",
            dependencies=["brain_provider.provider"],
        ),
        "model_source": _preview_field(
            section=section,
            field_id="model_source",
            label="Model source",
            current=current_provider.get("model_source") or "unknown",
            status="ok" if current_provider.get("model_source") in {"jarvis_explicit", "claude_default"} else "unknown",
            source="runtime_detected",
            dependencies=["brain_provider.provider"],
        ),
        "effort": _preview_field(
            section=section,
            field_id="effort",
            label="Effort",
            current=effort_value,
            status=effort_status,
            source="settings",
            allowed_values=effort_allowed,
            warning=effort_warning,
            blocker="Effort is unsupported by selected provider/model; reset required."
            if effort_status in {"invalid", "unsupported"} else None,
            dependencies=["brain_provider.provider", "brain_provider.model"],
            requires_reload=not brain_next_turn_apply,
            editable_now=brain_next_turn_apply and bool(effort_allowed),
            editable_later=True,
        ),
        "selected_effort": _preview_field(
            section=section,
            field_id="selected_effort",
            label="Selected effort",
            current=selected_effort,
            status="ok" if selected_effort in effort_allowed else ("missing" if selected_effort in {None, ""} else "invalid"),
            source="settings",
            dependencies=["brain_provider.provider", "brain_provider.model"],
        ),
        "effective_effort": _preview_field(
            section=section,
            field_id="effective_effort",
            label="Effective effort",
            current=effective_effort,
            status="ok" if effective_effort in effort_allowed else "unknown",
            source="runtime_detected",
            dependencies=["brain_provider.provider", "brain_provider.model"],
        ),
        "effort_source": _preview_field(
            section=section,
            field_id="effort_source",
            label="Effort source",
            current=current_provider.get("effort_source") or "unknown",
            status="ok" if current_provider.get("effort_source") in {"jarvis_explicit", "model_default", "unsupported"} else "unknown",
            source="runtime_detected",
            dependencies=["brain_provider.provider", "brain_provider.model"],
        ),
        "fast": _preview_field(
            section=section,
            field_id="fast",
            label="Fast",
            current=fast_value,
            status=fast_status,
            source="settings",
            allowed_values=[True, False],
            disabled_values=fast_disabled,
            warning=_projection_warning(raw_provider.get("fast")) if raw_provider else None,
            blocker="Fast is unsupported by selected provider/model." if fast_status in {"invalid", "unsupported"} else None,
            dependencies=["brain_provider.provider", "brain_provider.model"],
            requires_reload=not brain_next_turn_apply,
            editable_now=brain_next_turn_apply,
            editable_later=True,
        ),
        "context_budget": _preview_field(
            section=section,
            field_id="context_budget",
            label="Context budget",
            current=current_provider.get("context_info", {}).get("budget_chars"),
            source="config",
            status="ok" if current_provider.get("context_info", {}).get("budget_chars") is not None else "unknown",
            dependencies=["brain_provider.provider", "brain_provider.model"],
            requires_reload=True,
            editable_later=True,
        ),
        "provider_sessions_are_memory": _preview_field(
            section=section,
            field_id="provider_sessions_are_memory",
            label="Provider sessions are memory",
            current=_runtime_projection_value(brain_projection.get("provider_sessions_are_memory")),
            source="config",
            status="ok",
            requires_restart=True,
            editable_later=True,
        ),
        "tools_support": _preview_field(
            section=section,
            field_id="tools_support",
            label="Tools support",
            current=_support_word(current_provider.get("tools_supported")),
            source="runtime_detected",
            status="ok" if current_provider else "unknown",
            dependencies=["brain_provider.provider", "brain_provider.model"],
        ),
        "streaming_support": _preview_field(
            section=section,
            field_id="streaming_support",
            label="Streaming support",
            current=_support_word(current_provider.get("streaming_supported")),
            source="runtime_detected",
            status="ok" if current_provider else "unknown",
            dependencies=["brain_provider.provider", "brain_provider.model"],
        ),
        "auth_status": _preview_field(
            section=section,
            field_id="auth_status",
            label="Auth status",
            current=auth_status,
            source="runtime_detected",
            status=auth_field_status,
            blocker="Claude CLI auth is missing." if auth_status == "missing" else None,
            dependencies=["brain_provider.provider"],
        ),
        "version": _preview_field(
            section=section,
            field_id="version",
            label="Version",
            current=current_provider.get("version"),
            source="runtime_detected",
            status="ok" if current_provider.get("version") else "unknown",
            dependencies=["brain_provider.provider"],
        ),
        "permission_mode": _preview_field(
            section=section,
            field_id="permission_mode",
            label="Permission mode",
            current=permission_mode,
            source="config",
            status="ok" if permission_mode in CLAUDE_CLI_PERMISSION_MODES else "unknown",
            allowed_values=list(CLAUDE_CLI_PERMISSION_MODES),
            dependencies=["brain_provider.provider"],
        ),
        "tools": _preview_field(
            section=section,
            field_id="tools",
            label="Tools",
            current=current_provider.get("tools") or [],
            source="config",
            status="ok" if current_provider.get("tools") else "missing",
            dependencies=["brain_provider.provider"],
        ),
        "allowed_tools": _preview_field(
            section=section,
            field_id="allowed_tools",
            label="Allowed tools",
            current=current_provider.get("allowed_tools") or [],
            source="config",
            status="ok" if current_provider.get("allowed_tools") else "missing",
            dependencies=["brain_provider.provider"],
        ),
        "disallowed_tools": _preview_field(
            section=section,
            field_id="disallowed_tools",
            label="Disallowed tools",
            current=current_provider.get("disallowed_tools") or [],
            source="config",
            status="ok" if current_provider.get("disallowed_tools") else "missing",
            dependencies=["brain_provider.provider"],
        ),
        "mcp_config_status": _preview_field(
            section=section,
            field_id="mcp_config_status",
            label="MCP config",
            current=current_provider.get("mcp_config_status") or "unknown",
            source="config",
            status="ok" if current_provider.get("mcp_config_status") == "configured" else ("missing" if current_provider.get("mcp_config_status") == "missing" else "unknown"),
            dependencies=["brain_provider.provider"],
        ),
        "strict_mcp_config": _preview_field(
            section=section,
            field_id="strict_mcp_config",
            label="Strict MCP config",
            current=current_provider.get("strict_mcp_config"),
            source="config",
            status="unknown" if current_provider.get("strict_mcp_config") in {None, "unknown"} else "ok",
            dependencies=["brain_provider.provider"],
        ),
        "output_format": _preview_field(
            section=section,
            field_id="output_format",
            label="Output format",
            current=current_provider.get("output_format") or "unknown",
            source="config",
            status="ok" if current_provider.get("output_format") in CLAUDE_CLI_OUTPUT_FORMATS else "unknown",
            allowed_values=list(CLAUDE_CLI_OUTPUT_FORMATS),
            dependencies=["brain_provider.provider"],
        ),
        "input_format": _preview_field(
            section=section,
            field_id="input_format",
            label="Input format",
            current=current_provider.get("input_format") or "unknown",
            source="config",
            status="ok" if current_provider.get("input_format") in CLAUDE_CLI_INPUT_FORMATS else "unknown",
            allowed_values=list(CLAUDE_CLI_INPUT_FORMATS),
            dependencies=["brain_provider.provider"],
        ),
        "partial_messages_supported": _preview_field(
            section=section,
            field_id="partial_messages_supported",
            label="Partial messages",
            current=current_provider.get("partial_messages_supported") or "unknown",
            source="runtime_detected",
            status="ok" if _support_bool(current_provider.get("partial_messages_supported")) is not None else "unknown",
            dependencies=["brain_provider.provider"],
        ),
        "hook_events_supported": _preview_field(
            section=section,
            field_id="hook_events_supported",
            label="Hook events",
            current=current_provider.get("hook_events_supported") or "unknown",
            source="runtime_detected",
            status="unknown",
            dependencies=["brain_provider.provider"],
        ),
        "apply_semantics": _preview_field(
            section=section,
            field_id="apply_semantics",
            label="Apply semantics",
            current=apply_semantics,
            source="runtime_detected",
            status=apply_semantics_status,
            blocker=current_provider.get("blocker") if apply_semantics != "next_turn" else None,
            dependencies=["brain_provider.provider"],
        ),
        "command_preview": _preview_field(
            section=section,
            field_id="command_preview",
            label="Next-turn command preview",
            current=command_preview,
            source="runtime_detected",
            status="ok" if command_preview else "missing",
            dependencies=["brain_provider.provider", "brain_provider.model", "brain_provider.effort", "brain_provider.permission_mode"],
        ),
        "command_status": _preview_field(
            section=section,
            field_id="command_status",
            label="Command status",
            current=provider_command_status,
            source="runtime_detected",
            status="ok" if provider_command_ready is True else ("missing" if provider_command_ready is False else "unknown"),
            blocker="Provider command is missing."
            if provider_command_ready is False else None,
        ),
        "credentials_or_command_status": _preview_field(
            section=section,
            field_id="credentials_or_command_status",
            label="Credentials or command status",
            current=credentials_or_command_status,
            source="runtime_detected",
            status=credentials_or_command_field_status,
            blocker="Provider command or credential readiness is missing."
            if credentials_or_command_status in {"missing", "unavailable"} else None,
        ),
        "latest_provider_error": _preview_field(
            section=section,
            field_id="latest_provider_error",
            label="Latest provider error",
            current=current_provider.get("latest_provider_error"),
            source="runtime_detected",
            status="ok" if not current_provider.get("latest_provider_error") else "invalid",
        ),
    }

    voice_capabilities = capability_graph["voice_capabilities"]
    tts_section = "voice_tts"
    configured_tts = str(app.config.voice.default_tts or "").strip()
    tts_provider = _voice_provider_by_id(voice_capabilities["tts_providers"], configured_tts)
    tts_disabled = [
        _preview_disabled(provider["id"], "TTS provider is unavailable.")
        for provider in voice_capabilities["tts_providers"]
        if not provider.get("available")
    ]
    tts_blocker = None
    tts_status = "ok"
    if app.config.voice.enabled and not configured_tts:
        tts_status = "missing"
        tts_blocker = "Voice enabled but TTS provider is missing."
    elif tts_provider is not None and not tts_provider.get("available"):
        tts_status = "missing"
        tts_blocker = "Selected TTS provider is unavailable."
    tts_model_values = [model["id"] for model in (tts_provider or {}).get("models", [])]
    voice_id_values = list((tts_provider or {}).get("voice_ids", []))
    voice_profile_values = list((tts_provider or {}).get("voice_profiles", []))
    voice_id_status = "ok"
    voice_id_blocker = None
    if configured_tts == "supertonic" and not app.config.voice.supertonic_voice:
        voice_id_status = "missing"
        voice_id_blocker = "TTS provider requires voice_id."
    speed_supported = bool((tts_provider or {}).get("controls", {}).get("speed"))
    tts_fields = {
        "tts_provider": _preview_field(
            section=tts_section,
            field_id="tts_provider",
            label="TTS provider",
            current=configured_tts,
            status=tts_status,
            source="config",
            allowed_values=[provider["id"] for provider in voice_capabilities["tts_providers"]],
            disabled_values=tts_disabled,
            blocker=tts_blocker or VOICE_ENGINE_RELOAD_BLOCKER,
            invalidates=["voice_tts.tts_model", "voice_tts.voice_id", "voice_tts.speed_or_rate"],
            requires_restart=True,
            editable_now=False,
            editable_later=True,
            developer_only=bool((tts_provider or {}).get("developer_only")),
        ),
        "tts_model": _preview_field(
            section=tts_section,
            field_id="tts_model",
            label="TTS model",
            current=tts_model_values[0] if tts_model_values else None,
            status="ok" if tts_model_values else ("missing" if configured_tts and configured_tts != "mock" else "unknown"),
            source="runtime_detected",
            allowed_values=tts_model_values,
            dependencies=["voice_tts.tts_provider"],
            requires_restart=True,
            editable_now=bool(tts_model_values),
            editable_later=True,
        ),
        "voice_id": _preview_field(
            section=tts_section,
            field_id="voice_id",
            label="Voice id",
            current=app.config.voice.supertonic_voice if configured_tts == "supertonic" else None,
            status=voice_id_status if configured_tts == "supertonic" else "unknown",
            source="config",
            allowed_values=voice_id_values,
            blocker=voice_id_blocker,
            dependencies=["voice_tts.tts_provider"],
            requires_restart=True,
            editable_now=False,
            editable_later=True,
        ),
        "voice_profile": _preview_field(
            section=tts_section,
            field_id="voice_profile",
            label="Voice profile",
            current=app.config.voice.supertonic_lang if configured_tts == "supertonic" else None,
            status="ok" if configured_tts == "supertonic" and app.config.voice.supertonic_lang else "unknown",
            source="config",
            allowed_values=voice_profile_values,
            dependencies=["voice_tts.tts_provider"],
            blocker=VOICE_ENGINE_RELOAD_BLOCKER if configured_tts == "supertonic" else None,
            requires_restart=True,
            editable_now=False,
            editable_later=True,
        ),
        "speed_or_rate": _preview_field(
            section=tts_section,
            field_id="speed_or_rate",
            label="Speed / rate",
            current=app.config.voice.supertonic_speed if configured_tts == "supertonic" else None,
            status="ok" if speed_supported else "unsupported",
            source="config",
            allowed_values=[0.8, 1.0, 1.15, 1.35] if speed_supported else [],
            disabled_values=[] if speed_supported else [_preview_disabled("speed", "Selected TTS provider does not support speed/rate.")],
            blocker=VOICE_ENGINE_RELOAD_BLOCKER if speed_supported else None,
            dependencies=["voice_tts.tts_provider"],
            requires_restart=True,
            editable_now=False,
            editable_later=True,
        ),
        "style": _preview_field(section=tts_section, field_id="style", label="Style", current=None, status="unsupported", source="unknown"),
        "stability": _preview_field(section=tts_section, field_id="stability", label="Stability", current=None, status="unsupported", source="unknown"),
        "similarity": _preview_field(section=tts_section, field_id="similarity", label="Similarity", current=None, status="unsupported", source="unknown"),
        "streaming_support": _preview_field(section=tts_section, field_id="streaming_support", label="Streaming", current=voice_capabilities["supported_voice_controls"]["streaming"], status="ok", source="runtime_detected"),
        "continuity_support": _preview_field(section=tts_section, field_id="continuity_support", label="Continuity", current=voice_capabilities["supported_voice_controls"]["continuity"], status="ok", source="runtime_detected"),
        "latest_tts_error": _preview_field(section=tts_section, field_id="latest_tts_error", label="Latest TTS error", current=_runtime_projection_value(tts_projection.get("latest_safe_error")), status="ok" if not _runtime_projection_value(tts_projection.get("latest_safe_error")) else "invalid", source="runtime_detected"),
    }

    stt_section = "voice_stt"
    configured_stt = str(app.config.voice.default_stt or "").strip()
    stt_provider = _voice_provider_by_id(voice_capabilities["stt_providers"], configured_stt)
    stt_disabled = [
        _preview_disabled(provider["id"], "STT provider is unavailable.")
        for provider in voice_capabilities["stt_providers"]
        if not provider.get("available")
    ]
    stt_status = "ok"
    stt_blocker = None
    if app.config.voice.enabled and not configured_stt:
        stt_status = "missing"
        stt_blocker = "Voice enabled but STT provider is missing."
    elif stt_provider is not None and not stt_provider.get("available"):
        stt_status = "missing"
        stt_blocker = "Selected STT provider/runtime is unavailable."
    stt_model_values = [model["id"] for model in (stt_provider or {}).get("models", []) if model.get("id")]
    stt_fields = {
        "stt_provider": _preview_field(
            section=stt_section,
            field_id="stt_provider",
            label="STT provider",
            current=configured_stt,
            status=stt_status,
            source="config",
            allowed_values=[provider["id"] for provider in voice_capabilities["stt_providers"]],
            disabled_values=stt_disabled,
            blocker=stt_blocker or VOICE_ENGINE_RELOAD_BLOCKER,
            invalidates=["voice_stt.stt_model", "voice_stt.endpointing_support"],
            requires_restart=True,
            editable_now=False,
            editable_later=True,
            developer_only=bool((stt_provider or {}).get("developer_only")),
        ),
        "stt_model": _preview_field(
            section=stt_section,
            field_id="stt_model",
            label="STT model",
            current=app.config.voice.stt_model,
            status="ok" if app.config.voice.stt_model else "missing",
            source="config",
            allowed_values=stt_model_values,
            dependencies=["voice_stt.stt_provider"],
            requires_restart=True,
            editable_now=bool(stt_model_values),
            editable_later=True,
        ),
        "language": _preview_field(section=stt_section, field_id="language", label="Language", current=app.config.voice.stt_language, status="ok" if app.config.voice.stt_language else "missing", source="config", requires_restart=True, editable_later=True),
        "transcription_ready": _preview_field(section=stt_section, field_id="transcription_ready", label="Transcription ready", current=voice_capabilities["stt_readiness"] == "ok", status=voice_capabilities["stt_readiness"], source="runtime_detected", dependencies=["voice_stt.stt_provider", "voice_stt.stt_model"]),
        "endpointing_support": _preview_field(section=stt_section, field_id="endpointing_support", label="Endpointing support", current=bool((stt_provider or {}).get("endpointing_support")), status="ok", source="runtime_detected", dependencies=["voice_stt.stt_provider"]),
        "latest_stt_error": _preview_field(section=stt_section, field_id="latest_stt_error", label="Latest STT error", current=_runtime_projection_value(stt_projection.get("latest_safe_error")), status="ok" if not _runtime_projection_value(stt_projection.get("latest_safe_error")) else "invalid", source="runtime_detected"),
    }

    endpoint_section = "endpointing_ptt"
    endpoint_fields = {
        "ptt_mode": _preview_field(section=endpoint_section, field_id="ptt_mode", label="PTT mode", current=app.config.voice.ptt_mode, status="ok" if app.config.voice.ptt_mode in CANONICAL_PTT_MODES else "invalid", source="config", allowed_values=list(CANONICAL_PTT_MODES), editable_now=True, editable_later=True),
        "ptt_hotkey": _preview_field(section=endpoint_section, field_id="ptt_hotkey", label="PTT hotkey", current=app.config.voice.ptt_hotkey, status="ok" if app.config.voice.ptt_hotkey else "missing", source="config", warning="PTT hotkey is missing." if app.config.voice.ptt_mode in CANONICAL_PTT_MODES and not app.config.voice.ptt_hotkey else None, requires_restart=True, editable_later=True),
        "merge_window": _preview_field(section=endpoint_section, field_id="merge_window", label="Merge window", current=app.config.voice.transcript_turn_retry_seconds, status="ok", source="config", blocker=VOICE_GATEWAY_RELOAD_BLOCKER, requires_restart=True, editable_now=False, editable_later=True),
        "silence_threshold": _preview_field(section=endpoint_section, field_id="silence_threshold", label="Silence threshold", current=app.config.voice.stt_min_rms, status="ok", source="config", requires_restart=True, editable_later=True),
        "silence_duration": _preview_field(section=endpoint_section, field_id="silence_duration", label="Silence duration", current=app.config.voice.stt_min_voiced_seconds, status="ok", source="config", requires_restart=True, editable_later=True),
        "interrupt_policy": _preview_field(section=endpoint_section, field_id="interrupt_policy", label="Interrupt policy", current="barge-in cancels active speech" if voice_capabilities["cancellation_support"] else "not available", status="ok" if voice_capabilities["cancellation_support"] else "unsupported", source="runtime_detected"),
        "listening_lease_state": _preview_field(section=endpoint_section, field_id="listening_lease_state", label="Listening lease state", current=event_snapshot.get("active_leases"), status="ok" if app.started else "unknown", source="runtime_detected"),
    }

    queue_section = "queue_barge_in"
    queue_counts = queue_snapshot.get("counts") if isinstance(queue_snapshot.get("counts"), dict) else {}
    queue_fields = {
        "queue_status": _preview_field(section=queue_section, field_id="queue_status", label="Queue status", current={"status": queue_snapshot.get("status"), "counts": queue_counts}, status=queue_snapshot.get("status", "unknown"), source="runtime_detected"),
        "cancel_support": _preview_field(section=queue_section, field_id="cancel_support", label="Cancel support", current=voice_capabilities["cancellation_support"], status="ok" if voice_capabilities["cancellation_support"] else "unsupported", source="runtime_detected"),
        "active_speech_id": _preview_field(section=queue_section, field_id="active_speech_id", label="Active speech id", current=queue_snapshot.get("latest_voice_id"), status="ok" if queue_snapshot.get("latest_voice_id") else "missing", source="runtime_detected"),
        "current_spoken_kind": _preview_field(section=queue_section, field_id="current_spoken_kind", label="Current spoken kind", current=(queue_snapshot.get("tail") or [None])[0] if queue_snapshot.get("tail") else None, status="ok" if queue_snapshot.get("tail") else "unknown", source="runtime_detected"),
        "interrupted_previous_response": _preview_field(section=queue_section, field_id="interrupted_previous_response", label="Interrupted previous response", current=event_snapshot.get("latest_barge_in", {}).get("interrupted_previous_response") if isinstance(event_snapshot.get("latest_barge_in"), dict) else False, status="ok", source="runtime_detected"),
        "last_cancellation_reason": _preview_field(section=queue_section, field_id="last_cancellation_reason", label="Last cancellation reason", current=event_snapshot.get("latest_cancel_reason"), status="ok" if event_snapshot.get("latest_cancel_reason") else "missing", source="runtime_detected"),
        "manual_cancel_available": _preview_field(section=queue_section, field_id="manual_cancel_available", label="Manual cancel available", current=voice_capabilities["cancellation_support"], status="ok" if voice_capabilities["cancellation_support"] else "unsupported", source="runtime_detected", editable_now=voice_capabilities["cancellation_support"], editable_later=True),
    }

    tools_section = "tools_internet"
    tools_enabled_projection = tools_projection.get("tools_enabled")
    tools_registered = _runtime_projection_value(tools_projection.get("registered")) or []
    internet_projection = tools_projection.get("internet_capability")
    internet_value = _runtime_projection_value(internet_projection)
    internet_status = _projection_status(internet_projection)
    internet_warning = _projection_warning(internet_projection)
    network_policy_projection = tools_projection.get("network_policy")
    approval_required = _runtime_projection_value(tools_projection.get("approval_required_tools")) or []
    if not isinstance(approval_required, list):
        approval_required = []
    tools_apply_capabilities = capability_graph.get("tools_capabilities", {}).get("apply_capabilities", {})
    tools_enabled_capability = (
        tools_apply_capabilities.get("tools.enabled")
        if isinstance(tools_apply_capabilities, dict)
        else {}
    )
    network_policy_capability = (
        tools_apply_capabilities.get("security.require_approval_for_network")
        if isinstance(tools_apply_capabilities, dict)
        else {}
    )
    tools_fields = {
        "tools_enabled": _preview_field(
            section=tools_section,
            field_id="tools_enabled",
            label="Tools enabled",
            current=_runtime_projection_value(tools_enabled_projection),
            status=_projection_status(tools_enabled_projection),
            source="runtime_detected",
            warning=_projection_warning(tools_enabled_projection),
            blocker=tools_enabled_capability.get("blocker") if isinstance(tools_enabled_capability, dict) else None,
            requires_restart=True,
            editable_now=False,
            editable_later=True,
        ),
        "tools_support": _preview_field(section=tools_section, field_id="tools_support", label="Provider tools support", current=_support_word(current_provider.get("tools_supported")), status="ok" if current_provider.get("tools_supported") else "unsupported", source="runtime_detected", dependencies=["brain_provider.provider"]),
        "internet_capability": _preview_field(
            section=tools_section,
            field_id="internet_capability",
            label="Internet capability",
            current=internet_value,
            status=internet_status,
            source="runtime_detected",
            warning=internet_warning,
            blocker=internet_warning if internet_status == "missing" else None,
        ),
        "network_policy": _preview_field(
            section=tools_section,
            field_id="network_policy",
            label="Network policy",
            current=_runtime_projection_value(network_policy_projection),
            status=_projection_status(network_policy_projection),
            source="config",
            warning=_projection_warning(network_policy_projection),
            blocker=network_policy_capability.get("blocker") if isinstance(network_policy_capability, dict) else None,
            requires_restart=True,
            editable_now=False,
            editable_later=True,
        ),
        "approval_required_tools": _preview_field(section=tools_section, field_id="approval_required_tools", label="Approval required tools", current=approval_required, status="ok" if approval_required else "missing", source="config"),
        "latest_tool_error": _preview_field(section=tools_section, field_id="latest_tool_error", label="Latest tool error", current=event_snapshot.get("latest_tool_error") or _runtime_projection_value(tools_projection.get("latest_safe_error")), status="ok" if not event_snapshot.get("latest_tool_error") and not _runtime_projection_value(tools_projection.get("latest_safe_error")) else "invalid", source="runtime_detected"),
    }

    personality_section = "personality"
    personality_fields = {
        "active_persona": _preview_field(section=personality_section, field_id="active_persona", label="Active persona", current=_runtime_projection_value(brain_projection.get("persona_profile")), status=_projection_status(brain_projection.get("persona_profile")), source="settings", allowed_values=_available_persona_profiles(), requires_reload=True, editable_now=True, editable_later=True),
        "active_style": _preview_field(section=personality_section, field_id="active_style", label="Active style", current=None, status="unknown", source="unknown", editable_later=True),
        "personality_source": _preview_field(section=personality_section, field_id="personality_source", label="Personality source", current="persona.profile setting + default persona file", status="ok", source="runtime_detected"),
        "editable_later": _preview_field(section=personality_section, field_id="editable_later", label="Editable later", current=True, status="ok", source="runtime_detected", editable_later=True),
    }

    developer_section = "developer_test"
    developer_fields = {
        "mock_provider": _preview_field(section=developer_section, field_id="mock_provider", label="Mock provider", current=current_provider_id == "mock", status="ok", source="runtime_detected", warning="Mock provider is Developer/Test only." if current_provider_id == "mock" else None, developer_only=True),
        "fake_tts": _preview_field(section=developer_section, field_id="fake_tts", label="Fake TTS", current=configured_tts == "mock", status="ok", source="config", developer_only=True),
        "fake_stt": _preview_field(section=developer_section, field_id="fake_stt", label="Fake STT", current=configured_stt == "mock", status="ok", source="config", developer_only=True),
        "debug_mode": _preview_field(section=developer_section, field_id="debug_mode", label="Debug mode", current=str(app.config.daemon.log_level).upper() == "DEBUG", status="ok", source="config", developer_only=True),
        "test_harness_status": _preview_field(section=developer_section, field_id="test_harness_status", label="Test harness", current="not active", status="unknown", source="runtime_detected", developer_only=True),
    }

    return {
        "preview_only": True,
        "save_implemented": False,
        "save_disabled_reason": "Save not implemented in POC",
        "sections": {
            "brain_provider": _preview_section("brain_provider", "Brain / Provider", brain_fields),
            "voice_tts": _preview_section("voice_tts", "Voice / TTS", tts_fields),
            "voice_stt": _preview_section("voice_stt", "Voice / STT", stt_fields),
            "endpointing_ptt": _preview_section("endpointing_ptt", "Endpointing / PTT", endpoint_fields),
            "queue_barge_in": _preview_section("queue_barge_in", "Queue / Barge-in", queue_fields),
            "tools_internet": _preview_section("tools_internet", "Tools / Internet", tools_fields),
            "personality": _preview_section("personality", "Personality", personality_fields),
            "developer_test": _preview_section("developer_test", "Developer / Test", developer_fields),
        },
    }


def _compatibility_warning(
    *,
    warning_id: str,
    severity: str,
    group: str,
    field_ids: list[str],
    message: str,
    reason: str,
    suggested_action: str,
) -> dict[str, Any]:
    return {
        "id": warning_id,
        "severity": severity if severity in {"info", "warning", "invalid", "blocker"} else "warning",
        "group": group,
        "field_ids": field_ids,
        "message": message,
        "reason": reason,
        "suggested_action": suggested_action,
    }


def _build_structured_compatibility_warnings(
    app: DaemonApp,
    *,
    settings_preview: dict[str, Any],
    capability_graph: dict[str, Any],
    tools_projection: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        if any(existing["id"] == kwargs["warning_id"] for existing in warnings):
            return
        warnings.append(_compatibility_warning(**kwargs))

    brain_fields = settings_preview["sections"]["brain_provider"]["fields"]
    current_provider = _provider_by_id(
        capability_graph,
        brain_fields["provider"]["effective"],
    )
    if current_provider is None or not current_provider.get("available", False):
        add(
            warning_id="brain_provider_unavailable",
            severity="blocker",
            group="brain",
            field_ids=["brain_provider.provider"],
            message="Selected brain provider is unavailable.",
            reason="The configured provider is not available from safe runtime/capability probes.",
            suggested_action="Choose an available provider or install/configure the selected provider.",
        )
    if brain_fields["model"]["status"] in {"missing", "invalid"}:
        add(
            warning_id="brain_model_missing",
            severity="blocker" if brain_fields["model"]["status"] == "missing" else "invalid",
            group="brain",
            field_ids=["brain_provider.model", "brain_provider.provider"],
            message="Selected brain model is missing or invalid.",
            reason=brain_fields["model"]["blocker"] or brain_fields["model"]["warning"] or "Model is not usable.",
            suggested_action="Pick an allowed model for the selected provider.",
        )
    if brain_fields["effort"]["status"] in {"invalid", "unsupported"}:
        add(
            warning_id="brain_effort_unsupported",
            severity="invalid",
            group="brain",
            field_ids=["brain_provider.effort", "brain_provider.provider", "brain_provider.model"],
            message="Configured effort is unsupported by the selected provider/model.",
            reason=brain_fields["effort"]["blocker"] or "Provider capability graph does not allow this effort value.",
            suggested_action="Reset effort or choose a provider/model that supports it.",
        )
    if brain_fields["fast"]["status"] in {"invalid", "unsupported"}:
        add(
            warning_id="brain_fast_unsupported",
            severity="invalid",
            group="brain",
            field_ids=["brain_provider.fast", "brain_provider.provider", "brain_provider.model"],
            message="Fast mode is unsupported by the selected provider/model.",
            reason=brain_fields["fast"]["blocker"] or "Provider capability graph marks fast unsupported.",
            suggested_action="Turn fast off or choose a provider/model with fast support.",
        )
    if current_provider is not None and current_provider.get("developer_only"):
        add(
            warning_id="mock_provider_developer_only",
            severity="info",
            group="developer_test",
            field_ids=["developer_test.mock_provider", "brain_provider.provider"],
            message="Mock provider is Developer/Test only.",
            reason="Mock is useful for tests and offline scaffolding but is not a normal brain provider.",
            suggested_action="Use a real provider for normal operation.",
        )
    if current_provider is not None and current_provider.get("local_runtime") and not current_provider.get("models"):
        add(
            warning_id="local_provider_missing_model",
            severity="blocker",
            group="brain",
            field_ids=["brain_provider.provider", "brain_provider.model"],
            message="Local provider has no detected local model.",
            reason="Safe local detection found no usable model for the selected local provider.",
            suggested_action="Install/configure a local model before using this provider.",
        )
    local_models = capability_graph.get("local_capabilities", {}).get("local_models", [])
    local_runtime_status = capability_graph.get("local_capabilities", {}).get("local_runtime_status")
    if local_models and local_runtime_status != "ok":
        add(
            warning_id="local_model_runtime_missing",
            severity="blocker",
            group="brain",
            field_ids=["brain_provider.provider", "brain_provider.model"],
            message="A local model is configured but local runtime is missing.",
            reason="A model hint exists, but no local runtime command was safely detected.",
            suggested_action="Install/start the matching local runtime or choose another provider.",
        )

    tts_fields = settings_preview["sections"]["voice_tts"]["fields"]
    stt_fields = settings_preview["sections"]["voice_stt"]["fields"]
    if app.config.voice.enabled and tts_fields["tts_provider"]["status"] == "missing":
        add(
            warning_id="voice_enabled_tts_missing",
            severity="blocker",
            group="voice",
            field_ids=["voice_tts.tts_provider"],
            message="Voice is enabled but TTS provider is missing.",
            reason=tts_fields["tts_provider"]["blocker"] or "Voice output needs a TTS provider.",
            suggested_action="Configure TTS or disable voice output.",
        )
    if app.config.voice.speak_responses and tts_fields["tts_provider"]["status"] == "missing":
        add(
            warning_id="speak_responses_tts_missing",
            severity="blocker",
            group="voice",
            field_ids=["voice_tts.tts_provider"],
            message="speak_responses is enabled but TTS is missing.",
            reason="Jarvis is configured to speak responses, but no usable TTS provider exists.",
            suggested_action="Configure TTS or turn speak_responses off.",
        )
    if app.config.voice.enabled and stt_fields["stt_provider"]["status"] == "missing":
        add(
            warning_id="voice_enabled_stt_missing",
            severity="blocker",
            group="voice",
            field_ids=["voice_stt.stt_provider"],
            message="Voice is enabled but STT provider is missing.",
            reason=stt_fields["stt_provider"]["blocker"] or "Voice input needs an STT provider.",
            suggested_action="Configure STT or disable voice input.",
        )
    if tts_fields["tts_model"]["status"] == "missing" and tts_fields["tts_provider"]["current"] not in {"", "mock", None}:
        add(
            warning_id="tts_model_missing",
            severity="blocker",
            group="voice",
            field_ids=["voice_tts.tts_provider", "voice_tts.tts_model"],
            message="TTS model is required but missing.",
            reason="The selected TTS provider has no model in capability data.",
            suggested_action="Choose an allowed TTS model or configure provider metadata.",
        )
    if tts_fields["voice_id"]["status"] in {"missing", "invalid"}:
        add(
            warning_id="tts_voice_id_missing",
            severity="blocker",
            group="voice",
            field_ids=["voice_tts.tts_provider", "voice_tts.voice_id"],
            message="TTS voice_id is required but missing.",
            reason=tts_fields["voice_id"]["blocker"] or "The selected TTS provider requires a voice id.",
            suggested_action="Choose a valid voice_id for the selected TTS provider.",
        )
    if stt_fields["stt_model"]["status"] in {"missing", "invalid"} and stt_fields["stt_provider"]["current"] not in {"", "mock", None}:
        add(
            warning_id="stt_model_or_runtime_missing",
            severity="blocker",
            group="voice",
            field_ids=["voice_stt.stt_provider", "voice_stt.stt_model"],
            message="STT model or runtime is missing.",
            reason="The selected STT provider needs a model/runtime before transcription can work.",
            suggested_action="Configure a valid STT model/runtime.",
        )
    if tts_fields["speed_or_rate"]["current"] is not None and tts_fields["speed_or_rate"]["status"] == "unsupported":
        add(
            warning_id="tts_speed_unsupported",
            severity="warning",
            group="voice",
            field_ids=["voice_tts.tts_provider", "voice_tts.speed_or_rate"],
            message="Speed/rate is configured but unsupported by selected TTS provider.",
            reason="Capability graph marks speed/rate unsupported.",
            suggested_action="Remove the speed setting or choose a TTS provider with speed support.",
        )
    queue_fields = settings_preview["sections"]["queue_barge_in"]["fields"]
    if app.config.voice.enabled and app.config.voice.speak_responses and queue_fields["cancel_support"]["status"] == "unsupported":
        add(
            warning_id="barge_in_cancel_unavailable",
            severity="warning",
            group="voice",
            field_ids=["queue_barge_in.cancel_support", "queue_barge_in.manual_cancel_available"],
            message="Barge-in is enabled by voice flow but cancel support is unavailable.",
            reason="Runtime cancellation coordinator is not available from safe probes.",
            suggested_action="Start/configure the voice runtime cancellation path before relying on barge-in.",
        )
    ptt_fields = settings_preview["sections"]["endpointing_ptt"]["fields"]
    if ptt_fields["ptt_mode"]["current"] in CANONICAL_PTT_MODES and ptt_fields["ptt_hotkey"]["status"] == "missing":
        add(
            warning_id="ptt_hotkey_missing",
            severity="warning",
            group="voice",
            field_ids=["endpointing_ptt.ptt_mode", "endpointing_ptt.ptt_hotkey"],
            message="PTT is enabled but hotkey is missing.",
            reason="PTT mode has no configured global hotkey.",
            suggested_action="Configure ptt_hotkey or use a non-hotkey control surface.",
        )

    tools_registered = _runtime_projection_value(tools_projection.get("registered")) or []
    tools_fields = settings_preview["sections"]["tools_internet"]["fields"]
    if tools_registered and tools_fields["tools_support"]["status"] == "unsupported":
        add(
            warning_id="tools_enabled_provider_unsupported",
            severity="warning",
            group="tools",
            field_ids=["tools_internet.tools_enabled", "tools_internet.tools_support", "brain_provider.provider"],
            message="Tools are enabled but selected provider does not support tools.",
            reason="Tool registry is populated while provider capability says tools unsupported.",
            suggested_action="Choose a tools-capable provider or hide/disable tools for this provider.",
        )
    if app.config.security.require_approval_for_network and tools_fields["internet_capability"]["status"] == "missing":
        add(
            warning_id="internet_policy_without_capability",
            severity="warning",
            group="tools",
            field_ids=["tools_internet.network_policy", "tools_internet.internet_capability"],
            message="network enabled but no network tool registered",
            reason="Network approval policy is present, but the Jarvis tool registry has no network/search tool.",
            suggested_action="Install a network tool or remove the network policy from this profile.",
        )
    if tools_fields["approval_required_tools"]["current"] and not hasattr(app, "approval_gate"):
        add(
            warning_id="approval_required_surface_unavailable",
            severity="blocker",
            group="tools",
            field_ids=["tools_internet.approval_required_tools"],
            message="Approval-required tools exist but approval surface is unavailable.",
            reason="Runtime does not expose approval handling.",
            suggested_action="Wire approval surface before enabling approval-required tools.",
        )

    return warnings


def _readiness_projection(
    *,
    value: Any,
    status: str,
    warning: str | None = None,
    source: str = "runtime_detected",
) -> dict[str, Any]:
    return _projection(
        value=value,
        effective_value=value,
        source=source,
        status=status,
        editable_later=False,
        warning=warning,
    )


def _runtime_readiness_projection(
    app: DaemonApp,
    *,
    brain_projection: dict[str, Any],
    tools_projection: dict[str, Any],
    stt_projection: dict[str, Any],
    tts_projection: dict[str, Any],
    compatibility_warnings: list[str],
) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    warnings: list[str] = list(compatibility_warnings)

    host = str(app.config.daemon.host or "").strip()
    port = getattr(app.config.daemon, "port", None)
    daemon_config_status = "ok"
    daemon_config_warning = None
    if not host:
        daemon_config_status = "missing"
        daemon_config_warning = "Daemon host is missing."
    elif not isinstance(port, int) or port <= 0 or port > 65535:
        daemon_config_status = "invalid"
        daemon_config_warning = "Daemon port is invalid."
    fields["daemon_config"] = _readiness_projection(
        value={
            "host_configured": bool(host),
            "port_configured": isinstance(port, int),
            "localhost_only": bool(app.config.security.localhost_only),
        },
        status=daemon_config_status,
        warning=daemon_config_warning,
        source="config",
    )

    try:
        db_path = app.paths.db_path
        db_parent = db_path.parent
        if db_path.is_file():
            database_status = "ok"
            database_warning = None
        elif db_parent.exists():
            database_status = "missing"
            database_warning = "Database file does not exist yet."
        else:
            database_status = "missing"
            database_warning = "Database parent directory does not exist."
        database_value = {
            "configured": bool(str(getattr(app.config.database, "path", "")).strip()),
            "parent_exists": db_parent.exists(),
            "file_exists": db_path.exists(),
        }
    except Exception as exc:
        database_status = "invalid"
        database_warning = f"Database path probe failed: {exc}"
        database_value = {"configured": bool(str(getattr(app.config.database, "path", "")).strip())}
    fields["database_path"] = _readiness_projection(
        value=database_value,
        status=database_status,
        warning=database_warning,
    )

    fields["panel_backend_connected"] = _readiness_projection(
        value="yes",
        status="ok",
        source="runtime_detected",
    )

    providers = _runtime_projection_value(brain_projection.get("providers"))
    current_provider = _current_provider_capability(providers if isinstance(providers, list) else [])
    if current_provider is None:
        brain_command_value = "unknown"
        brain_command_status = "unknown"
        brain_command_warning = "Current brain provider capability is not exposed."
    else:
        provider_name = str(current_provider.get("name") or "").strip()
        command = current_provider.get("provider_command_status")
        command_value = _runtime_projection_value(command)
        command_status = _normalize_status(
            command.get("status") if isinstance(command, dict) else "unknown"
        )
        if provider_name == "mock":
            brain_command_value = "built-in"
            brain_command_status = "ok"
            brain_command_warning = None
        elif command_value == KNOWN_PROVIDER_SUPPORT_YES:
            brain_command_value = "exists"
            brain_command_status = "ok"
            brain_command_warning = None
        elif command_value == KNOWN_PROVIDER_SUPPORT_NO:
            brain_command_value = "missing"
            brain_command_status = "missing"
            brain_command_warning = "Selected brain provider command is missing."
        else:
            brain_command_value = "unknown"
            brain_command_status = command_status
            brain_command_warning = "Selected brain provider command readiness is unknown."
    fields["brain_provider_command"] = _readiness_projection(
        value=brain_command_value,
        status=brain_command_status,
        warning=brain_command_warning,
    )

    default_tts = str(app.config.voice.default_tts or "").strip()
    tts_status = "ok" if default_tts else "missing"
    fields["tts_provider"] = _readiness_projection(
        value="configured" if default_tts else "missing",
        status=tts_status,
        warning=None if default_tts else "TTS provider is not configured.",
        source="config",
    )

    default_stt = str(app.config.voice.default_stt or "").strip()
    stt_status = "ok" if default_stt else "missing"
    fields["stt_provider"] = _readiness_projection(
        value="configured" if default_stt else "missing",
        status=stt_status,
        warning=None if default_stt else "STT provider is not configured.",
        source="config",
    )

    recorder_status, recorder_binary, recorder_warning = _safe_probe_recorder_binary(
        str(app.config.voice.recorder_binary or ""),
        str(app.config.voice.recorder or "mock"),
    )
    fields["recorder_command"] = _readiness_projection(
        value={
            "backend": app.config.voice.recorder,
            "exists": recorder_status == "ok",
            "detected": recorder_binary is not None,
        },
        status=recorder_status,
        warning=recorder_warning,
    )

    playback_status, playback_binary, playback_warning = _safe_probe_playback_binary(
        str(app.config.voice.playback_binary or "")
    )
    fields["playback_command"] = _readiness_projection(
        value={
            "exists": playback_status == "ok",
            "detected": playback_binary is not None,
        },
        status=playback_status,
        warning=playback_warning,
    )

    internet_capability = _runtime_projection_value(tools_projection.get("internet_capability")) or {}
    if isinstance(internet_capability, dict):
        network_value = internet_capability.get("state", "unknown")
        network_tools = internet_capability.get("registered_network_tools", [])
    else:
        network_value = "unknown"
        network_tools = []
    network_projection_status = _projection_status(tools_projection.get("internet_capability"))
    fields["network_tools_capability"] = _readiness_projection(
        value={"capability": network_value, "registered_network_tools": network_tools},
        status=network_projection_status,
        warning=_projection_warning(tools_projection.get("internet_capability")),
    )

    if app.config.voice.enabled and not app.config.voice.broker_enabled:
        warnings.append("voice enabled but broker disabled")
    if app.config.voice.speak_responses and not default_tts:
        warnings.append("speak_responses enabled but TTS missing")
    if current_provider is not None:
        provider_name = str(current_provider.get("name") or "").strip().lower()
        current_model = current_provider.get("current_model")
        current_model_status = (
            current_model.get("status") if isinstance(current_model, dict) else "unknown"
        )
        if provider_name in {"local", "ollama"} and current_model_status in {"missing", "invalid"}:
            warnings.append("local provider selected but local model/runtime missing")

    for key, field in fields.items():
        status = field["status"]
        warning = field.get("warning")
        label = key.replace("_", " ")
        if status in {"missing", "invalid"}:
            blockers.append(f"{label}: {warning or status}")
        if warning:
            warnings.append(str(warning))

    deduped_warnings = list(dict.fromkeys(item for item in warnings if item))
    deduped_blockers = list(dict.fromkeys(item for item in blockers if item))
    counts = {
        "OK": 0,
        "Missing": 0,
        "Invalid": 0,
        "Unknown": 0,
        "Warning": len(deduped_warnings),
    }
    for field in fields.values():
        status = field["status"]
        if status == "ok":
            counts["OK"] += 1
        elif status == "missing":
            counts["Missing"] += 1
        elif status == "invalid":
            counts["Invalid"] += 1
        else:
            counts["Unknown"] += 1

    fields["summary"] = _readiness_projection(
        value=counts,
        status="ok" if not deduped_blockers else "invalid",
        warning="Startup blockers detected." if deduped_blockers else None,
    )
    fields["top_blockers"] = _readiness_projection(
        value=deduped_blockers,
        status="ok" if not deduped_blockers else "invalid",
        warning="Startup blockers detected." if deduped_blockers else None,
    )
    fields["warnings"] = _readiness_projection(
        value=deduped_warnings,
        status="ok" if not deduped_warnings else "invalid",
        warning="Runtime warnings detected." if deduped_warnings else None,
    )
    return fields


def _approvals_projection(app: DaemonApp) -> dict[str, Any]:
    pending_approvals = 0
    try:
        pending_approvals = app._pending_approval_count()
        status = "ok"
    except Exception as exc:
        status = "invalid"
        pending_approvals = 0
        warning = str(exc)
    else:
        warning = None

    return {
        "pending_count": _projection(
            value=pending_approvals,
            effective_value=pending_approvals,
            source="runtime_detected",
            status=status,
            editable_later=False,
            warning=warning,
        ),
        "require_approval_for_shell": _projection(
            value=app.config.security.require_approval_for_shell,
            effective_value=app.config.security.require_approval_for_shell,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "require_approval_for_file_write": _projection(
            value=app.config.security.require_approval_for_file_write,
            effective_value=app.config.security.require_approval_for_file_write,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "require_approval_for_network": _projection(
            value=app.config.security.require_approval_for_network,
            effective_value=app.config.security.require_approval_for_network,
            source="config",
            status="ok",
            editable_later=False,
        ),
    }


def _memory_projection(app: DaemonApp) -> dict[str, Any]:
    return {
        "enabled": _projection(
            value=app.config.memory.enabled,
            effective_value=app.config.memory.enabled,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "max_active_blocks": _projection(
            value=app.config.memory.max_active_blocks,
            effective_value=app.config.memory.max_active_blocks,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "worker_candidates_require_promotion": _projection(
            value=app.config.memory.worker_candidates_require_promotion,
            effective_value=app.config.memory.worker_candidates_require_promotion,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "compiled_context_enabled": _projection(
            value=app.config.memory.compiled_context_enabled,
            effective_value=app.config.memory.compiled_context_enabled,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "compiled_context_max_items": _projection(
            value=app.config.memory.compiled_context_max_items,
            effective_value=app.config.memory.compiled_context_max_items,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "compiled_context_max_chars": _projection(
            value=app.config.memory.compiled_context_max_chars,
            effective_value=app.config.memory.compiled_context_max_chars,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "compiled_context_include_procedural": _projection(
            value=app.config.memory.compiled_context_include_procedural,
            effective_value=app.config.memory.compiled_context_include_procedural,
            source="config",
            status="ok",
            editable_later=False,
        ),
    }


def _panel_projection(app: DaemonApp) -> dict[str, Any]:
    return {
        "enabled": _projection(
            value=app.config.panel.enabled,
            effective_value=app.config.panel.enabled,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "api_base_url": _projection(
            value=app.config.panel.api_base_url,
            effective_value=app.config.panel.api_base_url,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "width": _projection(
            value=app.config.panel.width,
            effective_value=app.config.panel.width,
            source="config",
            status="ok",
            editable_later=False,
        ),
        "height": _projection(
            value=app.config.panel.height,
            effective_value=app.config.panel.height,
            source="config",
            status="ok",
            editable_later=False,
        ),
    }


def post_runtime_settings_apply(
    app: DaemonApp,
    request_payload: Mapping[str, Any],
) -> dict[str, Any]:
    settings = _runtime_settings_apply_request(request_payload)
    if not settings:
        return redact_secrets(
            _runtime_settings_apply_response(
                status="unchanged",
                requested_keys=[],
                applied_keys=[],
                warnings=["No runtime settings were submitted."],
                runtime_settings=get_runtime_settings(app),
            )
        )

    runtime_payload = _current_capability_payload(app)
    capability_graph = runtime_payload.get("capability_graph", {})
    applied: list[str] = []
    applied.extend(_apply_tools_internet_settings(app, settings, capability_graph))
    applied.extend(_apply_brain_settings(app, settings, capability_graph))
    applied.extend(_apply_voice_and_ptt_settings(app, settings, capability_graph))
    applied.extend(_apply_persona_settings(app, settings))
    applied = list(dict.fromkeys(applied))

    requested_keys = sorted(settings)
    return redact_secrets(
        _runtime_settings_apply_response(
            status="applied" if applied else "unchanged",
            requested_keys=requested_keys,
            applied_keys=applied,
            unchanged_keys=sorted(set(requested_keys) - set(applied)),
            runtime_settings=get_runtime_settings(app),
        )
    )


def _runtime_settings_apply_response(
    *,
    status: str,
    requested_keys: list[str],
    applied_keys: list[str],
    rejected_keys: list[str] | None = None,
    unchanged_keys: list[str] | None = None,
    requires_restart_keys: list[str] | None = None,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    runtime_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rejected = list(rejected_keys or [])
    unchanged = list(unchanged_keys or [])
    restart = list(requires_restart_keys or [])
    return {
        "ok": status == "applied" or status == "unchanged",
        "status": status,
        "requested_keys": list(requested_keys),
        "applied": list(applied_keys),
        "applied_keys": list(applied_keys),
        "rejected_keys": rejected,
        "unchanged_keys": unchanged,
        "requires_restart_keys": restart,
        "blockers": list(blockers or []),
        "warnings": list(warnings or []),
        "runtime_settings": runtime_settings or {},
    }


def get_runtime_settings(app: DaemonApp) -> dict[str, Any]:
    settings = _safe_read_settings(app)
    audio_projection = _audio_projection(app)
    queue_snapshot = _collect_voice_queue_snapshot(app)
    event_snapshot = _collect_voice_events_snapshot(app)
    brain_projection = _brain_projection(app, settings)
    tools_projection = _tools_projection(app, settings)
    stt_projection = _voice_layer_projection_stt(
        app,
        queue_snapshot=queue_snapshot,
        event_snapshot=event_snapshot,
    )
    tts_projection = _voice_layer_projection_tts(
        app,
        event_snapshot=event_snapshot,
    )
    compatibility_warnings = _collect_voice_runtime_compatibility_warnings(
        app=app,
        settings=settings,
        provider_capabilities=(
            brain_projection.get("providers", {}).get("value")
            if isinstance(brain_projection.get("providers"), dict)
            else []
        ),
        tools_registered=tools_projection.get("registered", {}).get("value")
        if isinstance(tools_projection.get("registered"), dict)
        else [],
    )
    capability_graph = _build_capability_graph(
        app,
        brain_projection=brain_projection,
        tools_projection=tools_projection,
        tts_projection=tts_projection,
        stt_projection=stt_projection,
        queue_snapshot=queue_snapshot,
    )
    settings_preview = _build_settings_preview(
        app,
        brain_projection=brain_projection,
        tools_projection=tools_projection,
        queue_snapshot=queue_snapshot,
        event_snapshot=event_snapshot,
        tts_projection=tts_projection,
        stt_projection=stt_projection,
        capability_graph=capability_graph,
    )
    structured_compatibility_warnings = _build_structured_compatibility_warnings(
        app,
        settings_preview=settings_preview,
        capability_graph=capability_graph,
        tools_projection=tools_projection,
    )
    latest_turn_trace = _latest_turn_trace_projection(
        app,
        settings=settings,
        event_snapshot=event_snapshot,
    )
    payload = {
        "runtime": _runtime_projection(app),
        "brain": brain_projection,
        "current_turn_state": _current_turn_state_projection(
            app,
            latest_turn_trace=latest_turn_trace,
            queue_snapshot=queue_snapshot,
            event_snapshot=event_snapshot,
        ),
        "latest_turn_trace": latest_turn_trace,
        "voice": _voice_projection(
            app,
            queue_snapshot=queue_snapshot,
            event_snapshot=event_snapshot,
        ),
        "audio": audio_projection,
        "tools": tools_projection,
        "runtime_readiness": _runtime_readiness_projection(
            app,
            brain_projection=brain_projection,
            tools_projection=tools_projection,
            stt_projection=stt_projection,
            tts_projection=tts_projection,
            compatibility_warnings=compatibility_warnings,
        ),
        "settings_preview": settings_preview,
        "capability_graph": capability_graph,
        "compatibility_warnings": structured_compatibility_warnings,
        "memory": _memory_projection(app),
        "approvals": _approvals_projection(app),
        "panel": _panel_projection(app),
        "voice_capture_input": _voice_layer_projection_capture_input(
            app,
            audio_projection=audio_projection,
            queue_snapshot=queue_snapshot,
            event_snapshot=event_snapshot,
        ),
        "voice_stt_transcription": stt_projection,
        "voice_endpointing_vad_ptt": _voice_layer_projection_endpointing(
            app,
            event_snapshot=event_snapshot,
        ),
        "voice_turn_manager": _voice_layer_projection_turn_manager(
            app,
            event_snapshot=event_snapshot,
        ),
        "voice_tts_voice_model": tts_projection,
        "voice_playback": _voice_layer_projection_playback(
            app,
            event_snapshot=event_snapshot,
        ),
        "voice_queue_barge_in": _voice_layer_projection_queue_barge_in(
            app,
            queue_snapshot=queue_snapshot,
            event_snapshot=event_snapshot,
        ),
        "voice_errors": _voice_layer_projection_errors(
            app,
            queue_snapshot=queue_snapshot,
            event_snapshot=event_snapshot,
            compatibility_warnings=compatibility_warnings,
        ),
    }
    return redact_secrets(payload)


def get_runtime_processes(app: DaemonApp) -> dict[str, Any]:
    observations = app.runtime_supervisor.observe_all()
    conflicts = _conflicts(observations)
    return {
        "observations": [observation.to_dict() for observation in observations],
        "conflicts": [observation.to_dict() for observation in conflicts],
        "conflict_count": len(conflicts),
        "report_only": True,
        "cleanup_automated": False,
    }


def get_runtime_startup(app: DaemonApp) -> dict[str, Any]:
    return {
        "startup": app.runtime_supervisor.startup_snapshot().to_dict(),
        "report_only": True,
        "official_label": OFFICIAL_LABEL,
    }


def get_runtime_legacy(app: DaemonApp) -> dict[str, Any]:
    conflicts = app.runtime_supervisor.legacy_conflicts()
    return {
        "legacy_conflicts": [observation.to_dict() for observation in conflicts],
        "legacy_conflict_count": len(conflicts),
        "guidance": list(LEGACY_GUIDANCE),
    }


def _conflicts(observations: list[RuntimeProcessObservation]) -> list[RuntimeProcessObservation]:
    return [
        observation
        for observation in observations
        if observation.risk in {RuntimeRisk.HIGH, RuntimeRisk.CRITICAL}
    ]


def register_routes(app: object) -> None:
    return None


__all__ = [
    "LEGACY_GUIDANCE",
    "ROUTE_GROUP",
    "RuntimeSettingsApplyError",
    "get_runtime_legacy",
    "get_runtime_processes",
    "get_runtime_settings",
    "get_runtime_startup",
    "post_runtime_settings_apply",
    "register_routes",
]
