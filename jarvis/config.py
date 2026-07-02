"""Configuration loading for Jarvis v4.1."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "jarvis.toml"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "jarvis.example.toml"
REQUIRED_SECTIONS = (
    "daemon",
    "database",
    "brain",
    "memory",
    "voice",
    "audio",
    "panel",
    "security",
    "runtime",
    "launchd",
)


class ConfigError(RuntimeError):
    """Raised when Jarvis configuration cannot be loaded."""


@dataclass(frozen=True)
class DaemonConfig:
    name: str = "jarvisd"
    host: str = "127.0.0.1"
    port: int = 41741
    log_level: str = "INFO"


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = "~/.jarvis/jarvis.db"
    migrations: str = "manual"
    destroy_existing: bool = False


@dataclass(frozen=True)
class BrainCliAdapterConfig:
    enabled: bool = False
    command: str = ""
    args: list[str] = field(default_factory=list)
    model: str = ""
    timeout_seconds: int = 120


@dataclass(frozen=True)
class BrainConfig:
    default_adapter: str = "mock"
    default_model: str = "mock-local"
    timeout_seconds: int = 60
    context_budget_chars: int = 24000
    provider_sessions_are_memory: bool = False
    claude_cli: BrainCliAdapterConfig = field(
        default_factory=lambda: BrainCliAdapterConfig(command="claude", args=["-p"])
    )
    codex_cli: BrainCliAdapterConfig = field(
        default_factory=lambda: BrainCliAdapterConfig(command="codex", args=[])
    )


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = True
    max_active_blocks: int = 50
    max_context_chars: int = 12000
    worker_candidates_require_promotion: bool = True


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    speak_responses: bool = False
    broker_enabled: bool = False
    default_tts: str = "mock"
    default_stt: str = "mock"
    ptt_mode: str = "hold"
    queue_persisted: bool = True
    recorder: str = "mock"
    ptt_hold_ttl_seconds: int = 30
    listen_lock_ttl_seconds: int = 600
    fillers: tuple[str, ...] = ("Już sprawdzam.", "Chwila.")
    filler_after_ms: int = 1200
    min_sentence_chars: int = 12


@dataclass(frozen=True)
class AudioConfig:
    enabled: bool = False
    backend: str = "native"
    input_policy: str = "pin_builtin_mic"
    preferred_input: str = "Mikrofon (MacBook Air)"
    output_policy: str = "follow_system_default"
    allow_bluetooth_microphone: bool = False
    always_listen_enabled: bool = False


@dataclass(frozen=True)
class PanelConfig:
    enabled: bool = False
    api_base_url: str = "http://127.0.0.1:41741"
    width: int = 420
    height: int = 620


@dataclass(frozen=True)
class SecurityConfig:
    localhost_only: bool = True
    api_token_required: bool = True
    require_approval_for_shell: bool = True
    require_approval_for_file_write: bool = True
    require_approval_for_network: bool = True
    destructive_tools_enabled: bool = False
    # File-tool containment roots. Empty means "no roots configured": the
    # daemon then falls back to its runtime home only — never to the whole
    # filesystem (fail-closed, docs/SECURITY_MODEL.md).
    approved_roots: tuple[str, ...] = ()
    # Exact-match whitelist for the read-only shell tool. Empty means the
    # conservative built-in default set (jarvis/tools/shell_tool.py).
    shell_read_whitelist: tuple[str, ...] = ()
    # ui_read backend: "ax" (real AXUIElement, needs the Accessibility TCC
    # grant) or "fake" (deterministic fixture for tests/smoke). Unknown
    # names fail the daemon at startup — never a silent fallback.
    ui_read_backend: str = "ax"
    # ui_act backend for UI actions; empty inherits ui_read_backend so the
    # common case stays a single knob. Same names, same fail-closed rule.
    ui_act_backend: str = ""
    # screen_read backend: "native" (screencapture + Vision OCR, needs the
    # Screen Recording TCC grant) or "fake" (deterministic fixture for
    # tests/smoke). Unknown names fail the daemon at startup.
    screen_read_backend: str = "native"
    # terminal bridge backend: "osascript" (fixed AppleScript to
    # Terminal/iTerm2, needs the Automation TCC grant per target app) or
    # "fake" (deterministic fixture for tests/smoke). Unknown names fail
    # the daemon at startup.
    terminal_backend: str = "osascript"


@dataclass(frozen=True)
class RuntimeConfig:
    home: str = "~/.jarvis"
    logs_dir: str = "~/.jarvis/logs"
    runtime_dir: str = "~/.jarvis/runtime"
    pid_file: str = "~/.jarvis/runtime/jarvisd.pid"
    legacy_detection: str = "report_only"


@dataclass(frozen=True)
class LaunchdConfig:
    enabled: bool = False
    label: str = "com.ozzy.jarvisd"
    install_automatically: bool = False


@dataclass(frozen=True)
class JarvisConfig:
    source_path: Path
    daemon: DaemonConfig
    database: DatabaseConfig
    brain: BrainConfig
    memory: MemoryConfig
    voice: VoiceConfig
    audio: AudioConfig
    panel: PanelConfig
    security: SecurityConfig
    runtime: RuntimeConfig
    launchd: LaunchdConfig

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload["source_path"] = str(self.source_path)
        return payload


T = TypeVar("T")


def load_config(path: str | Path | None = None) -> JarvisConfig:
    """Load Jarvis configuration from explicit path, env, repo config, or example."""

    config_path = _select_config_path(path)
    raw = _read_toml(config_path)
    _require_sections(raw)

    return JarvisConfig(
        source_path=config_path,
        daemon=_build_section(DaemonConfig, raw["daemon"]),
        database=_build_section(DatabaseConfig, raw["database"]),
        brain=_build_brain_config(raw["brain"]),
        memory=_build_section(MemoryConfig, raw["memory"]),
        voice=_build_section(VoiceConfig, raw["voice"]),
        audio=_build_section(AudioConfig, raw["audio"]),
        panel=_build_section(PanelConfig, raw["panel"]),
        security=_build_section(SecurityConfig, raw["security"]),
        runtime=_build_section(RuntimeConfig, raw["runtime"]),
        launchd=_build_section(LaunchdConfig, raw["launchd"]),
    )


def _select_config_path(path: str | Path | None) -> Path:
    if path is not None:
        return _normalize_path(path)

    env_path = os.environ.get("JARVIS_CONFIG")
    if env_path:
        return _normalize_path(env_path)

    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH

    return EXAMPLE_CONFIG_PATH


def _normalize_path(path: str | Path) -> Path:
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    return config_path


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a TOML table: {path}")
    return data


def _require_sections(raw: dict[str, Any]) -> None:
    for section in REQUIRED_SECTIONS:
        value = raw.get(section)
        if value is None:
            raise ConfigError(f"Missing required config section: {section}")
        if not isinstance(value, dict):
            raise ConfigError(f"Config section must be a table: {section}")


def _build_section(section_type: type[T], raw: dict[str, Any]) -> T:
    allowed = {field.name for field in fields(section_type)}
    selected = {key: value for key, value in raw.items() if key in allowed}
    try:
        return section_type(**selected)
    except TypeError as exc:
        raise ConfigError(f"Invalid config section {section_type.__name__}: {exc}") from exc


def _build_brain_config(raw: dict[str, Any]) -> BrainConfig:
    selected = {
        key: value
        for key, value in raw.items()
        if key
        in {
            "default_adapter",
            "default_model",
            "timeout_seconds",
            "context_budget_chars",
            "provider_sessions_are_memory",
        }
    }
    try:
        return BrainConfig(
            **selected,
            claude_cli=_build_brain_cli_config(
                "brain.claude_cli",
                raw.get("claude_cli"),
                default_command="claude",
                default_args=["-p"],
            ),
            codex_cli=_build_brain_cli_config(
                "brain.codex_cli",
                raw.get("codex_cli"),
                default_command="codex",
                default_args=[],
            ),
        )
    except TypeError as exc:
        raise ConfigError(f"Invalid config section BrainConfig: {exc}") from exc


def _build_brain_cli_config(
    section_name: str,
    raw: Any,
    *,
    default_command: str,
    default_args: list[str],
) -> BrainCliAdapterConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config section must be a table: {section_name}")

    allowed = {field.name for field in fields(BrainCliAdapterConfig)}
    selected = {key: value for key, value in raw.items() if key in allowed}
    if "command" not in selected:
        selected["command"] = default_command
    if "args" not in selected:
        selected["args"] = list(default_args)
    args = selected.get("args")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ConfigError(f"{section_name}.args must be a list of strings")
    try:
        return BrainCliAdapterConfig(**selected)
    except TypeError as exc:
        raise ConfigError(f"Invalid config section {section_name}: {exc}") from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
