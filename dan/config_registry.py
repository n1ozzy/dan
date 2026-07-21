"""Authoritative ownership, validation and persistence for DAN configuration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import tomllib
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Any


class ConfigRegistryError(RuntimeError):
    """The registry or a registered value is invalid."""


class ConfigWriteRejected(ConfigRegistryError):
    """A write targeted an unknown, dead, or non-writable key."""


class ConfigOwner(StrEnum):
    VERSIONED = "versioned"
    INSTALLATION = "installation"
    OWNER = "owner"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class ConfigKey:
    owner: ConfigOwner
    writable: bool
    parser: Callable[[Any], Any]
    consumers: tuple[str, ...] = ("DaemonApp",)


@dataclass(frozen=True)
class ConfigExplanation:
    key: str
    value: Any
    owner: ConfigOwner
    source_surface: str
    source_file: Path
    revision: str
    consumers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "owner": self.owner.value,
            "source_surface": self.source_surface,
            "source_file": str(self.source_file),
            "revision": self.revision,
            "consumers": list(self.consumers),
        }


# This explicit inventory is deliberately separate from dataclass discovery. A new
# config field therefore breaks the completeness test until its owner is decided.
_RUNTIME_CONFIG_KEYS = frozenset(
    """
daemon.name
daemon.host
daemon.port
daemon.log_level
daemon.log_max_bytes
daemon.log_backup_count
database.path
brain.default_adapter
brain.default_model
brain.timeout_seconds
brain.context_budget_chars
brain.context_window_tokens
brain.context_checkpoint_percent
brain.context_compact_percent
brain.context_recycle_percent
brain.provider_sessions_are_memory
brain.claude_cli.enabled
brain.claude_cli.command
brain.claude_cli.args
brain.claude_cli.model
brain.claude_cli.effort
brain.claude_cli.permission_mode
brain.claude_cli.output_format
brain.claude_cli.input_format
brain.claude_cli.tools
brain.claude_cli.allowed_tools
brain.claude_cli.disallowed_tools
brain.claude_cli.mcp_config_path
brain.claude_cli.strict_mcp_config
brain.claude_cli.timeout_seconds
brain.claude_cli.stream_args
brain.codex_cli.enabled
brain.codex_cli.command
brain.codex_cli.args
brain.codex_cli.model
brain.codex_cli.effort
brain.codex_cli.permission_mode
brain.codex_cli.output_format
brain.codex_cli.input_format
brain.codex_cli.tools
brain.codex_cli.allowed_tools
brain.codex_cli.disallowed_tools
brain.codex_cli.mcp_config_path
brain.codex_cli.strict_mcp_config
brain.codex_cli.timeout_seconds
brain.codex_cli.stream_args
brain.test.enabled
brain.test.command
brain.test.args
brain.test.model
brain.test.effort
brain.test.permission_mode
brain.test.output_format
brain.test.input_format
brain.test.tools
brain.test.allowed_tools
brain.test.disallowed_tools
brain.test.mcp_config_path
brain.test.strict_mcp_config
brain.test.timeout_seconds
brain.test.stream_args
memory.enabled
memory.max_active_blocks
memory.max_context_chars
memory.worker_candidates_require_promotion
memory.compiled_context_enabled
memory.compiled_context_max_items
memory.compiled_context_max_chars
memory.compiled_context_include_procedural
voice.enabled
voice.speak_responses
voice.broker_enabled
voice.hook_enabled
voice.output_gain
voice.default_tts
voice.default_stt
voice.ptt_mode
voice.ptt_hotkey
voice.queue_persisted
voice.recorder
voice.recorder_binary
voice.recorder_sample_rate
voice.recorder_highpass_hz
voice.recorder_gain_db
voice.recorder_segment_seconds
voice.stt_model
voice.stt_language
voice.stt_timeout_seconds
voice.stt_timeout_per_audio_second
voice.stt_min_rms
voice.stt_min_voiced_seconds
voice.stt_min_voiced_ratio
voice.stt_junk_phrases
voice.anti_echo_window_seconds
voice.anti_echo_overlap_threshold
voice.anti_echo_min_echo_tokens
voice.transcript_turn_retry_seconds
voice.ptt_hold_ttl_seconds
voice.listen_lock_ttl_seconds
voice.ptt_activation_grace_ms
voice.vad_engine
voice.vad_frame_ms
voice.vad_threshold
voice.vad_pre_activation_buffer_ms
voice.vad_silence_duration_ms
voice.lease_sweep_interval_seconds
voice.fillers
voice.filler_after_ms
voice.min_sentence_chars
voice.supertonic_binary
voice.supertonic_voice
voice.supertonic_lang
voice.supertonic_steps
voice.supertonic_speed
voice.supertonic_short_sentence_chars
voice.supertonic_short_sentence_speed
voice.playback_binary
voice.tts_timeout_seconds
voice.playback_pad_start_seconds
voice.playback_pad_end_seconds
voice.tts_pronunciations
voice.supertonic_serve_url
voice.supertonic_serve_model
voice.supertonic_serve_autostart
voice.supertonic_serve_max_chunk_length
voice.mastering_profile
voice.mastering_binary
voice.persona_voices
voice.persona_mastering
voice.persona_speeds
audio.enabled
audio.backend
audio.input_policy
audio.preferred_input
audio.output_policy
audio.allow_bluetooth_microphone
audio.always_listen_enabled
panel.enabled
panel.api_base_url
panel.width
panel.height
security.localhost_only
security.api_token_required
security.require_approval_for_shell
security.require_approval_for_file_write
security.require_approval_for_network
security.require_approval_for_ui
security.require_approval_for_terminal
security.require_approval_for_memory
security.destructive_tools_enabled
security.approved_roots
security.shell_read_whitelist
security.shell_read_unrestricted
security.ui_read_backend
security.ui_act_backend
security.screen_read_backend
security.terminal_backend
security.trusted_scopes
security.voice_auto_approve_tools
security.auto_approve_mode
runtime.home
runtime.logs_dir
runtime.runtime_dir
runtime.pid_file
runtime.legacy_detection
launchd.enabled
launchd.label
launchd.install_automatically
owner.display_name
brain.current_adapter
dan.conversation_id
voice.conversation_id
model
effort
""".split()
)

_OWNER_KEYS = frozenset({"owner.display_name"})
_LIVE_RUNTIME_KEYS = frozenset(
    {
        "brain.current_adapter",
        "dan.conversation_id",
        "voice.conversation_id",
        "model",
        "effort",
    }
)

_VERSIONED_KEYS = frozenset(
    {
        "brain.default_adapter",
        "brain.provider_sessions_are_memory",
        *{key for key in _RUNTIME_CONFIG_KEYS if key.startswith("brain.codex_cli.")},
        *{key for key in _RUNTIME_CONFIG_KEYS if key.startswith("brain.test.")},
        "voice.default_tts",
        "voice.supertonic_voice",
        "voice.supertonic_lang",
        "voice.supertonic_steps",
        "voice.supertonic_speed",
        "voice.supertonic_short_sentence_chars",
        "voice.supertonic_short_sentence_speed",
        "voice.tts_pronunciations",
        "voice.mastering_profile",
        "voice.persona_voices",
        "voice.persona_mastering",
        "voice.persona_speeds",
        "security.require_approval_for_shell",
        "security.require_approval_for_file_write",
        "security.require_approval_for_network",
        "security.require_approval_for_ui",
        "security.require_approval_for_terminal",
        "security.require_approval_for_memory",
    }
)

_VOICE_RESOLVER_CONSUMERS = {
    "voice.output_gain": ("VoiceResolver",),
    "voice.default_tts": ("VoiceResolver",),
    "voice.supertonic_voice": ("VoiceResolver",),
    "voice.supertonic_speed": ("VoiceResolver",),
    "voice.tts_pronunciations": ("VoiceResolver",),
    "voice.mastering_profile": ("VoiceResolver",),
    "voice.persona_voices": ("VoiceResolver",),
    "voice.persona_mastering": ("VoiceResolver",),
    "voice.persona_speeds": ("VoiceResolver",),
}

_SPECIAL_CONSUMERS = {
    "owner.display_name": ("PersonaRenderer",),
    "voice.hook_enabled": ("VoiceHook",),
    "brain.current_adapter": ("BrainManager",),
    "dan.conversation_id": ("TurnOrchestrator",),
    "voice.conversation_id": ("VoiceTurnGateway",),
    "model": ("BrainManager",),
    "effort": ("BrainManager",),
}

REJECTED_KEYS: Mapping[str, str] = {
    "jarvis_speed": "dead legacy setting",
    "database.migrations": "schema migrations are versioned code",
    "database.destroy_existing": "destructive legacy setting",
    "brain.stream_args": "use the sole brain.claude_cli route",
    "brain.chat_cli_model": "dead donor override",
    "brain.chat_model": "dead donor override",
    "brain.cli_timeout": "dead donor override",
    "brain.code_effort": "provider-specific donor override",
    "brain.code_model": "provider-specific donor override",
    "elevenlabs.model": "unsupported provider override",
    "groq.model": "removed provider override",
    "net.block_private": "dead donor override",
    "net.enabled": "dead donor override",
    "persona.frequency_penalty": "persona behavior is versioned canon",
    "persona.max_tokens": "persona behavior is versioned canon",
    "persona.model": "persona behavior is versioned canon",
    "persona.num_ctx": "persona behavior is versioned canon",
    "persona.presence_penalty": "persona behavior is versioned canon",
    "persona.temperature": "persona behavior is versioned canon",
    "persona.tools": "persona behavior is versioned canon",
    "persona.who": "owner data belongs in owner.toml",
    "persona.profile": "the product has one versioned DAN persona",
    "persona.dan.voice": "voice mappings are versioned assets",
    "projects.max_depth": "dead donor override",
    "voice.backend": "use registered voice.default_tts",
    "voice.merge_window": "dead runtime setting",
    "voice.profile": "voice mappings are versioned assets",
    "voice.rate": "voice mappings are versioned assets",
    "voice.speed": "voice mappings are versioned assets",
    "voice.voice_id": "voice mappings are versioned assets",
    "voice.voice_profile": "voice mappings are versioned assets",
    "voice.dan_drift": "dead donor override",
    "voice.dan_profile": "voice mappings are versioned assets",
    "voice.dan_speed": "voice mappings are versioned assets",
    "voice.dan_supertonic_voice": "voice mappings are versioned assets",
    "voice.dan_voice": "voice mappings are versioned assets",
    "voice.danusia_supertonic_voice": "voice mappings are versioned assets",
    "voice.jarvis_speed": "voice mappings are versioned assets",
    "voice.jarvis_supertonic_voice": "voice mappings are versioned assets",
    "voice.report_persona": "dead donor override",
    "voice.zaneta_supertonic_voice": "voice mappings are versioned assets",
    "voice_v2.input_device": "use audio.preferred_input",
    "voice_v2.max_speech_s": "dead donor override",
    "voice_v2.start_silence_s": "dead donor override",
    "voice_v2.stop_silence_s": "dead donor override",
}

# Snapshot of keys present in the donor state/overrides.json. Keep this
# inventory independent from REGISTRY and REJECTED_KEYS so dropping a decision
# from either mapping makes the completeness test fail.
IMPORTED_CONFIG_KEYS = frozenset(
    """
voice.enabled
voice.backend
voice.report_persona
voice.supertonic_voice
voice.dan_supertonic_voice
voice.danusia_supertonic_voice
voice.dan_drift
voice.dan_profile
voice.supertonic_speed
voice.dan_speed
voice.dan_voice
voice.jarvis_supertonic_voice
voice.jarvis_speed
voice.zaneta_supertonic_voice
persona.temperature
persona.max_tokens
persona.frequency_penalty
persona.presence_penalty
persona.tools
persona.num_ctx
persona.model
persona.who
brain.chat_model
brain.code_model
brain.chat_cli_model
brain.cli_timeout
brain.code_effort
net.enabled
net.block_private
voice_v2.max_speech_s
voice_v2.stop_silence_s
voice_v2.input_device
voice_v2.start_silence_s
projects.max_depth
groq.model
elevenlabs.model
""".split()
)


def parse_gain(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigWriteRejected("voice.output_gain must be a number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ConfigWriteRejected("voice.output_gain must be finite and greater than zero")
    return parsed


def parse_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigWriteRejected("value must be a boolean")
    return value


def parse_hotkey(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigWriteRejected("hotkey must be a string")
    from dan.input.hotkey import (
        HotkeySpecError,
    )
    from dan.input.hotkey import (
        parse_hotkey as parse_hotkey_spec,
    )

    try:
        parse_hotkey_spec(value)
    except HotkeySpecError as exc:
        raise ConfigWriteRejected(str(exc)) from exc
    return value.strip()


def _optional_base_annotation(annotation: Any) -> Any:
    """`T | None` → `T`; plain `T` → `T`; unions of several real types → None."""
    if annotation is None:
        return None
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return args[0] if len(args) == 1 else None
    return annotation


def _parse_by_annotation(key: str, value: Any, annotation: Any) -> Any:
    origin = typing.get_origin(annotation) or annotation
    if annotation is bool:
        if not isinstance(value, bool):
            raise ConfigWriteRejected(f"{key} must be a boolean or null")
        return value
    if annotation is int:
        if type(value) is not int:
            raise ConfigWriteRejected(f"{key} must be an integer or null")
        return value
    if annotation is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigWriteRejected(f"{key} must be a number or null")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ConfigWriteRejected(f"{key} must be finite")
        return parsed
    if annotation is str:
        if not isinstance(value, str):
            raise ConfigWriteRejected(f"{key} must be a string or null")
        return value
    if origin in (list, tuple):
        if not isinstance(value, (list, tuple)):
            raise ConfigWriteRejected(f"{key} must be an array or null")
        element_types = tuple(
            a for a in typing.get_args(annotation) if a is not Ellipsis
        )
        if element_types == (str,) and not all(
            isinstance(item, str) for item in value
        ):
            raise ConfigWriteRejected(f"{key} must be an array of strings")
        return list(value)
    if origin is dict or (isinstance(origin, type) and issubclass(origin, Mapping)):
        if not isinstance(value, Mapping):
            raise ConfigWriteRejected(f"{key} must be a table or null")
        return dict(value)
    return value


def _typed_parser(key: str) -> Callable[[Any], Any]:
    def parse(value: Any) -> Any:
        default = _runtime_defaults().get(key)
        if default is None:
            # A None default says nothing about the field's type — validate
            # against the declared dataclass annotation (e.g. `list[str] |
            # None` for stream_args). Only when no annotation is known does
            # the historical nullable-boolean contract apply.
            if value is None:
                return None
            annotation = _optional_base_annotation(_runtime_annotations().get(key))
            if annotation is not None:
                return _parse_by_annotation(key, value, annotation)
            if isinstance(value, bool):
                return value
            raise ConfigWriteRejected(f"{key} must be a boolean or null")
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigWriteRejected(f"{key} must be a boolean")
            return value
        if isinstance(default, int):
            if type(value) is not int:
                raise ConfigWriteRejected(f"{key} must be an integer")
            return value
        if isinstance(default, float):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigWriteRejected(f"{key} must be a number")
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ConfigWriteRejected(f"{key} must be finite")
            return parsed
        if isinstance(default, str):
            if not isinstance(value, str):
                raise ConfigWriteRejected(f"{key} must be a string")
            return value
        if isinstance(default, tuple):
            if not isinstance(value, (list, tuple)):
                raise ConfigWriteRejected(f"{key} must be an array")
            return list(value)
        if isinstance(default, list):
            if not isinstance(value, (list, tuple)):
                raise ConfigWriteRejected(f"{key} must be an array")
            return list(value)
        if isinstance(default, dict):
            if not isinstance(value, Mapping):
                raise ConfigWriteRejected(f"{key} must be a table")
            return dict(value)
        return value

    return parse


def _parse_text(key: str) -> Callable[[Any], str]:
    def parse(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ConfigWriteRejected(f"{key} must be a non-empty string")
        return value.strip()

    return parse


REGISTRY: Mapping[str, ConfigKey] = {
    key: ConfigKey(
        owner=(
            ConfigOwner.OWNER
            if key in _OWNER_KEYS
            else ConfigOwner.RUNTIME
            if key in _LIVE_RUNTIME_KEYS
            else ConfigOwner.VERSIONED
            if key in _VERSIONED_KEYS
            else ConfigOwner.INSTALLATION
        ),
        writable=key in _LIVE_RUNTIME_KEYS or key not in _VERSIONED_KEYS | _OWNER_KEYS,
        parser=(
            parse_gain
            if key == "voice.output_gain"
            else parse_hotkey
            if key == "voice.ptt_hotkey"
            else _parse_text(key)
            if key in _LIVE_RUNTIME_KEYS | _OWNER_KEYS
            else _typed_parser(key)
        ),
        consumers=_SPECIAL_CONSUMERS.get(
            key, _VOICE_RESOLVER_CONSUMERS.get(key, ("DaemonApp",))
        ),
    )
    for key in _RUNTIME_CONFIG_KEYS
}

class ConfigStore:
    """Typed installation config with one atomic TOML write path."""

    def __init__(
        self,
        path: str | Path,
        *,
        owner_path: str | Path | None = None,
        runtime_db_path: str | Path | None = None,
        versioned_root: str | Path | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.owner_path = (
            Path(owner_path).expanduser()
            if owner_path is not None
            else self.path.parent / "owner.toml"
        )
        self.runtime_db_path = (
            Path(runtime_db_path).expanduser()
            if runtime_db_path is not None
            else None
        )
        self.versioned_root = (
            Path(versioned_root).expanduser()
            if versioned_root is not None
            else Path(__file__).resolve().parents[1]
        )

    @property
    def revision(self) -> str:
        return hashlib.sha256(self._bytes()).hexdigest()

    def bytes(self) -> bytes:
        return self._bytes()

    def get(self, key: str, default: Any = None) -> Any:
        entry = _registered(key)
        data = self._read()
        found, value = _nested_value(data, key)
        if found:
            return entry.parser(value)
        registered_default = _runtime_defaults().get(key, default)
        return entry.parser(registered_default) if registered_default is not None else default

    def installation_snapshot(self) -> dict[str, Any]:
        return {
            key: self.get(key)
            for key, entry in REGISTRY.items()
            if entry.owner is ConfigOwner.INSTALLATION and entry.writable
        }

    def explain(self, key: str) -> ConfigExplanation:
        entry = _registered(key)
        if entry.owner is ConfigOwner.OWNER:
            value, source_file, revision, surface = self._explain_owner(key, entry)
        elif entry.owner is ConfigOwner.RUNTIME:
            value, source_file, revision, surface = self._explain_runtime(key, entry)
        elif entry.owner is ConfigOwner.VERSIONED:
            value, source_file, revision, surface = self._explain_versioned(key, entry)
        else:
            value = self.get(key)
            source_file = self.path
            revision = self.revision
            surface = "installation config"
        return ConfigExplanation(
            key=key,
            value=value,
            owner=entry.owner,
            source_surface=surface,
            source_file=source_file,
            revision=revision,
            consumers=entry.consumers,
        )

    def _explain_owner(
        self, key: str, entry: ConfigKey
    ) -> tuple[Any, Path, str, str]:
        if key != "owner.display_name":
            raise ConfigRegistryError(f"unsupported owner configuration key: {key}")
        data = _read_toml_file(self.owner_path)
        found, value = _nested_value(data, key)
        if not found:
            raise ConfigRegistryError(f"{key} is missing from owner profile {self.owner_path}")
        return (
            entry.parser(value),
            self.owner_path,
            _file_revision(self.owner_path),
            "owner profile",
        )

    def _explain_runtime(
        self, key: str, entry: ConfigKey
    ) -> tuple[Any, Path, str, str]:
        path = self.runtime_db_path
        if path is None or not path.is_file():
            raise ConfigRegistryError(f"runtime settings database is unavailable for {key}")
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT value_json, updated_at, source FROM settings WHERE key = ?",
                    (key,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise ConfigRegistryError(
                f"could not read runtime setting {key} from {path}: {exc}"
            ) from exc
        if row is None:
            raise ConfigRegistryError(f"runtime setting {key} does not exist in {path}")
        value_json, updated_at, source = map(str, row)
        try:
            value = entry.parser(json.loads(value_json))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ConfigRegistryError(f"invalid runtime setting {key} in {path}: {exc}") from exc
        revision = hashlib.sha256(
            _canonical_json([key, value_json, updated_at, source]).encode("utf-8")
        ).hexdigest()
        return value, path, revision, f"runtime settings database:{source}"

    def _explain_versioned(
        self, key: str, entry: ConfigKey
    ) -> tuple[Any, Path, str, str]:
        config_source = self.versioned_root / "config" / "dan.example.toml"
        data = _read_toml_file(config_source)
        found, value = _nested_value(data, key)
        if found:
            return (
                entry.parser(value),
                config_source,
                _file_revision(config_source),
                "versioned config",
            )
        code_source = self.versioned_root / "dan" / "config.py"
        default = _runtime_defaults().get(key)
        if default is None:
            raise ConfigRegistryError(f"versioned value for {key} has no source")
        return (
            entry.parser(default),
            code_source,
            _file_revision(code_source),
            "versioned code",
        )

    def set(self, key: str, value: Any) -> None:
        self.set_many({key: value})

    def set_many(self, updates: Mapping[str, Any]) -> None:
        if not isinstance(updates, Mapping) or not updates:
            raise ConfigWriteRejected("config update must be a non-empty mapping")
        parsed: dict[str, Any] = {}
        for key, value in updates.items():
            entry = _registered(key)
            if entry.owner is not ConfigOwner.INSTALLATION or not entry.writable:
                raise ConfigWriteRejected(
                    f"{key} is owned by {entry.owner.value} configuration and is read-only"
                )
            try:
                parsed[key] = entry.parser(value)
            except ConfigWriteRejected:
                raise
            except (TypeError, ValueError) as exc:
                raise ConfigWriteRejected(f"invalid value for {key}: {exc}") from exc

        data = self._read()
        for key, value in parsed.items():
            _set_nested(data, key, value)
        _write_toml_atomic(self.path, data)

    def _bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError as exc:
            raise ConfigRegistryError(f"could not read config {self.path}: {exc}") from exc

    def _read(self) -> dict[str, Any]:
        raw = self._bytes()
        if not raw:
            return {}
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigRegistryError(f"invalid TOML in {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigRegistryError(f"config root must be a TOML table: {self.path}")
        return data


def validate_setting_updates(updates: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a complete batch before any file or database write."""

    installation: dict[str, Any] = {}
    runtime: dict[str, Any] = {}
    for key, value in updates.items():
        entry = REGISTRY.get(key)
        if entry is None:
            reason = REJECTED_KEYS.get(key, "unknown configuration key")
            raise ConfigWriteRejected(f"{key}: {reason}")
        if not entry.writable:
            raise ConfigWriteRejected(
                f"{key} is owned by {entry.owner.value} configuration and is read-only"
            )
        if entry.owner is ConfigOwner.INSTALLATION:
            installation[key] = entry.parser(value)
        elif entry.owner is ConfigOwner.RUNTIME:
            runtime[key] = entry.parser(value)
        else:
            raise ConfigWriteRejected(
                f"{key} is owned by {entry.owner.value} configuration and is read-only"
            )
    return installation, runtime


def discovered_runtime_config_keys() -> set[str]:
    from dan.config import (
        AudioConfig,
        BrainCliAdapterConfig,
        BrainConfig,
        DaemonConfig,
        DatabaseConfig,
        LaunchdConfig,
        MemoryConfig,
        PanelConfig,
        RuntimeConfig,
        SecurityConfig,
        VoiceConfig,
    )

    sections = {
        "daemon": DaemonConfig,
        "database": DatabaseConfig,
        "brain": BrainConfig,
        "memory": MemoryConfig,
        "voice": VoiceConfig,
        "audio": AudioConfig,
        "panel": PanelConfig,
        "security": SecurityConfig,
        "runtime": RuntimeConfig,
        "launchd": LaunchdConfig,
    }
    nested = {
        BrainConfig: {
            "claude_cli": BrainCliAdapterConfig,
            "codex_cli": BrainCliAdapterConfig,
            "test": BrainCliAdapterConfig,
        }
    }

    def walk(prefix: str, section_type: type[Any]) -> set[str]:
        found: set[str] = set()
        for item in fields(section_type):
            key = f"{prefix}.{item.name}"
            child = nested.get(section_type, {}).get(item.name)
            found.update(walk(key, child) if child else {key})
        return found

    discovered = set().union(
        *(walk(section, section_type) for section, section_type in sections.items())
    )
    return discovered | _OWNER_KEYS | _LIVE_RUNTIME_KEYS


def validate_registered_config_tree(data: Mapping[str, Any], *, source: Path) -> None:
    """Fail startup for duplicate-parser-proof but unregistered TOML keys."""

    validate_registry_complete()

    def walk(prefix: str, value: Any) -> None:
        if prefix in REGISTRY or prefix in REJECTED_KEYS:
            return
        if not isinstance(value, Mapping):
            raise ConfigRegistryError(f"unregistered config key {prefix!r} in {source}")
        for name, child in value.items():
            key = f"{prefix}.{name}" if prefix else str(name)
            walk(key, child)

    walk("", data)


def validate_registry_complete() -> None:
    discovered = discovered_runtime_config_keys()
    registered = set(REGISTRY)
    missing = sorted(discovered - registered)
    extra = sorted(registered - discovered)
    overlap = sorted(registered & set(REJECTED_KEYS))
    unclassified_imports = sorted(IMPORTED_CONFIG_KEYS - registered - set(REJECTED_KEYS))
    if missing or extra or overlap or unclassified_imports:
        raise ConfigRegistryError(
            "invalid configuration registry: "
            f"missing={missing}, extra={extra}, duplicate_decisions={overlap}, "
            f"unclassified_imports={unclassified_imports}"
        )


def _registered(key: str) -> ConfigKey:
    entry = REGISTRY.get(key)
    if entry is None:
        reason = REJECTED_KEYS.get(key, "unknown configuration key")
        raise ConfigWriteRejected(f"{key}: {reason}")
    return entry


def _runtime_defaults() -> dict[str, Any]:
    from dan.config import (
        AudioConfig,
        BrainConfig,
        DaemonConfig,
        DatabaseConfig,
        LaunchdConfig,
        MemoryConfig,
        PanelConfig,
        RuntimeConfig,
        SecurityConfig,
        VoiceConfig,
    )

    instances = {
        "daemon": DaemonConfig(),
        "database": DatabaseConfig(),
        "brain": BrainConfig(),
        "memory": MemoryConfig(),
        "voice": VoiceConfig(),
        "audio": AudioConfig(),
        "panel": PanelConfig(),
        "security": SecurityConfig(),
        "runtime": RuntimeConfig(),
        "launchd": LaunchdConfig(),
    }
    defaults: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if prefix in REGISTRY:
            defaults[prefix] = value
            return
        if hasattr(value, "__dataclass_fields__"):
            for item in fields(value):
                walk(f"{prefix}.{item.name}" if prefix else item.name, getattr(value, item.name))

    for section, instance in instances.items():
        walk(section, instance)
    return defaults


_ANNOTATION_CACHE: dict[str, Any] = {}


def _runtime_annotations() -> dict[str, Any]:
    """Declared field annotations per registered key, resolved once.

    Mirrors the `_runtime_defaults()` walk, but records the dataclass type
    annotation instead of the default value — the only honest type source for
    fields whose default is None.
    """
    if _ANNOTATION_CACHE:
        return _ANNOTATION_CACHE
    from dan.config import (
        AudioConfig,
        BrainConfig,
        DaemonConfig,
        DatabaseConfig,
        LaunchdConfig,
        MemoryConfig,
        PanelConfig,
        RuntimeConfig,
        SecurityConfig,
        VoiceConfig,
    )

    instances = {
        "daemon": DaemonConfig(),
        "database": DatabaseConfig(),
        "brain": BrainConfig(),
        "memory": MemoryConfig(),
        "voice": VoiceConfig(),
        "audio": AudioConfig(),
        "panel": PanelConfig(),
        "security": SecurityConfig(),
        "runtime": RuntimeConfig(),
        "launchd": LaunchdConfig(),
    }
    annotations: dict[str, Any] = {}

    def walk(prefix: str, value: Any, annotation: Any) -> None:
        if prefix in REGISTRY:
            annotations[prefix] = annotation
            return
        if hasattr(value, "__dataclass_fields__"):
            try:
                hints = typing.get_type_hints(type(value))
            except Exception:
                hints = {}
            for item in fields(value):
                walk(
                    f"{prefix}.{item.name}" if prefix else item.name,
                    getattr(value, item.name),
                    hints.get(item.name),
                )

    for section, instance in instances.items():
        walk(section, instance, None)
    _ANNOTATION_CACHE.update(annotations)
    return _ANNOTATION_CACHE


def _nested_value(data: Mapping[str, Any], key: str) -> tuple[bool, Any]:
    current: Any = data
    for segment in key.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _read_toml_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigRegistryError(f"configuration source does not exist: {path}") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigRegistryError(f"could not read configuration source {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigRegistryError(f"configuration source must be a TOML table: {path}")
    return data


def _file_revision(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ConfigRegistryError(f"could not hash configuration source {path}: {exc}") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    current = data
    segments = key.split(".")
    for segment in segments[:-1]:
        nested = current.setdefault(segment, {})
        if not isinstance(nested, dict):
            raise ConfigWriteRejected(f"{'.'.join(segments[:-1])} is not a TOML table")
        current = nested
    current[segments[-1]] = value


_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(value: str) -> str:
    return value if _BARE_TOML_KEY.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigRegistryError("TOML config cannot persist non-finite numbers")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ConfigRegistryError(f"unsupported TOML value type: {type(value).__name__}")


def _dump_toml(data: Mapping[str, Any]) -> str:
    lines: list[str] = []

    def emit(table: tuple[str, ...], values: Mapping[str, Any], *, header: bool) -> None:
        if header:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("[" + ".".join(_toml_key(part) for part in table) + "]")
        scalars = [(key, value) for key, value in values.items() if not isinstance(value, dict)]
        mappings = [(key, value) for key, value in values.items() if isinstance(value, dict)]
        for key, value in scalars:
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                continue
            lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
        for key, value in mappings:
            emit((*table, str(key)), value, header=True)
        for key, value in scalars:
            is_array_of_tables = (
                isinstance(value, list)
                and value
                and all(isinstance(item, dict) for item in value)
            )
            if not is_array_of_tables:
                continue
            for item in value:
                if lines and lines[-1] != "":
                    lines.append("")
                table_name = ".".join(
                    _toml_key(part) for part in (*table, str(key))
                )
                lines.append(f"[[{table_name}]]")
                for item_key, item_value in item.items():
                    lines.append(f"{_toml_key(str(item_key))} = {_toml_value(item_value)}")

    emit((), data, header=False)
    return "\n".join(lines).rstrip() + "\n"


def _write_toml_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = _dump_toml(data).encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ConfigRegistryError(f"could not atomically write config {path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


__all__ = [
    "IMPORTED_CONFIG_KEYS",
    "REGISTRY",
    "REJECTED_KEYS",
    "ConfigExplanation",
    "ConfigKey",
    "ConfigOwner",
    "ConfigRegistryError",
    "ConfigStore",
    "ConfigWriteRejected",
    "discovered_runtime_config_keys",
    "parse_bool",
    "parse_gain",
    "parse_hotkey",
    "validate_registered_config_tree",
    "validate_registry_complete",
    "validate_setting_updates",
]
