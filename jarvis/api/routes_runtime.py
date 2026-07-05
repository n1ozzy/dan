"""Runtime supervision and runtime settings projection payloads."""

from __future__ import annotations

import json
import sqlite3
import os
import re
import shutil
import subprocess
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
KNOWN_STATUSES = frozenset({"ok", "missing", "invalid", "unknown"})
PERSONA_PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KNOWN_PROVIDER_EFFORT_LEVELS = ("low", "medium", "high")
KNOWN_PROVIDER_SUPPORT_UNKNOWN = "unknown"
KNOWN_PROVIDER_SUPPORT_YES = "yes"
KNOWN_PROVIDER_SUPPORT_NO = "no"

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
        "kind": "Provider",
        "supported_efforts": list(KNOWN_PROVIDER_EFFORT_LEVELS),
        "fast_support": KNOWN_PROVIDER_SUPPORT_NO,
        "streaming_support": KNOWN_PROVIDER_SUPPORT_YES,
        "tools_support": KNOWN_PROVIDER_SUPPORT_YES,
    },
    "claude_cli_warm": {
        "display_name": "Claude CLI",
        "kind": "Provider",
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


def _provider_credentials_projection(
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
    credentials_projection = _provider_credentials_projection(
        provider_name=adapter_name,
        adapter=adapter,
    )

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
    network_status, network_tool = _safe_probe_network_capability()

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
            if tts_binary_status == "ok":
                voice_status, _, voice_warning = _safe_probe_supertonic_voice(
                    tts_binary,
                    supertonic_voice,
                )
                if voice_status != "ok":
                    _append_compatibility_warning(
                        warnings,
                        voice_warning or "Configured supertonic voice is unavailable.",
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

    if getattr(app.config.security, "require_approval_for_network", False) and network_status == KNOWN_PROVIDER_SUPPORT_NO:
        tool_hint = network_tool or "curl/wget"
        _append_compatibility_warning(
            warnings,
            f"Internet policy is active but no network tool is available (missing: {tool_hint}).",
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
            "latest_barge_in": {
                "interrupted_previous_response": False,
                "cancelled_speech_id": None,
                "cancellation_reason": None,
                "previous_turn_id": None,
                "new_turn_source": None,
            },
            "status": "unknown",
        }

    conn = app._connect_existing()
    try:
        rows = conn.execute(
            """
            SELECT type, payload_json
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
        latest_barge_in = {
            "interrupted_previous_response": False,
            "cancelled_speech_id": None,
            "cancellation_reason": None,
            "previous_turn_id": None,
            "new_turn_source": None,
        }

        for event_type, payload_json in rows:
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
                raw_speech_id = payload.get("request_id")
                if isinstance(raw_speech_id, str) and raw_speech_id.strip():
                    latest_barge_in["cancelled_speech_id"] = raw_speech_id.strip()
                raw_turn_id = payload.get("turn_id")
                if isinstance(raw_turn_id, str) and raw_turn_id.strip():
                    latest_barge_in["previous_turn_id"] = raw_turn_id.strip()
                raw_reason = payload.get("reason")
                if isinstance(raw_reason, str) and raw_reason.strip():
                    latest_barge_in["cancellation_reason"] = raw_reason.strip()
                source = _normalize_new_turn_source(payload.get("interruption_source"))
                if source is not None:
                    latest_barge_in["new_turn_source"] = source
                latest_barge_in["interrupted_previous_response"] = True

            if latest_safe_error is not None and latest_cancel_reason is not None:
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
            "latest_barge_in": {
                "interrupted_previous_response": False,
                "cancelled_speech_id": None,
                "cancellation_reason": None,
                "previous_turn_id": None,
                "new_turn_source": None,
            },
            "status": "invalid",
            "warning": str(exc),
        }
    finally:
        close_quietly(conn)


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
                "new_turn_source": _projection(
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
        raw_event_snapshot = event_snapshot or {}
        barge_in_snapshot = raw_event_snapshot.get("latest_barge_in", {})
        if not isinstance(barge_in_snapshot, dict):
            barge_in_snapshot = {}
        interrupted_previous_response = bool(barge_in_snapshot.get("interrupted_previous_response"))
        cancelled_speech_id = (
            str(barge_in_snapshot["cancelled_speech_id"]).strip()
            if isinstance(barge_in_snapshot.get("cancelled_speech_id"), str)
            else None
        )
        barge_in_cancellation_reason = (
            str(barge_in_snapshot["cancellation_reason"]).strip()
            if isinstance(barge_in_snapshot.get("cancellation_reason"), str)
            else None
        )
        barge_previous_turn_id = (
            str(barge_in_snapshot["previous_turn_id"]).strip()
            if isinstance(barge_in_snapshot.get("previous_turn_id"), str)
            else None
        )
        barge_new_turn_source = (
            str(barge_in_snapshot["new_turn_source"]).strip()
            if isinstance(barge_in_snapshot.get("new_turn_source"), str)
            else None
        )

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
        if barge_previous_turn_id is None and interrupted_previous_response:
            barge_previous_turn_id = turn_id
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
            "new_turn_source": _projection(
                value=barge_new_turn_source,
                effective_value=barge_new_turn_source,
                source=source,
                status="ok" if barge_new_turn_source is not None else "missing",
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
    ptt_mode_ready = ptt_mode in {"hold", "toggle", "off"}
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
        supertonic_voice_status, _, supertonic_voice_warning = _safe_probe_supertonic_voice(
            tts_binary,
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


def _tools_projection(app: DaemonApp) -> dict[str, Any]:
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

    return {
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
    }


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


def get_runtime_settings(app: DaemonApp) -> dict[str, Any]:
    settings = _safe_read_settings(app)
    audio_projection = _audio_projection(app)
    queue_snapshot = _collect_voice_queue_snapshot(app)
    event_snapshot = _collect_voice_events_snapshot(app)
    brain_projection = _brain_projection(app, settings)
    tools_projection = _tools_projection(app)
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
    payload = {
        "runtime": _runtime_projection(app),
        "brain": brain_projection,
        "latest_turn_trace": _latest_turn_trace_projection(
            app,
            settings=settings,
            event_snapshot=event_snapshot,
        ),
        "voice": _voice_projection(
            app,
            queue_snapshot=queue_snapshot,
            event_snapshot=event_snapshot,
        ),
        "audio": audio_projection,
        "tools": tools_projection,
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
    "get_runtime_legacy",
    "get_runtime_processes",
    "get_runtime_settings",
    "get_runtime_startup",
    "register_routes",
]
