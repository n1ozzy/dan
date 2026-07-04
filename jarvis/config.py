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
DEFAULT_VOICE_FILLERS: tuple[str, ...] = (
    "A spierdalaj...",
    "Kurwa, grzebię.",
    "Sekunda, tnę bajzel.",
    "Moment, mielę crash log.",
    "Czekaj, backend po wypadku gada.",
    "Dobra, error 500 się spowiada.",
    "Zaraz, kroję ten zjebany problem.",
    "Pustostan logiki się ładuje.",
    "Daj sekundę, jadę po faktach.",
    "Spokojnie, ten stack zaraz klęknie.",
    "Patrzę, bo to śmierdzi gównem.",
    "Dobra, rozbrajam kompromitację.",
    "Panie, robi się.",
    "No i cyk, analiza.",
    "Co tu się odjaniepawla...",
    "Będzie pan zadowolony.",
    "To się zaraz wyklepie.",
    "Daj pan chwilę.",
    "Nie no, pięknie.",
    "Kto to panu tak zrobił?",
    "Robi się, szefie.",
    "Chwila, zaraz to ogarnę.",
    "Ale urwał loga.",
    "Janusz debugowania wchodzi.",
    "Nosacz odpala analizę.",
    "Cyk, cyk, debug.",
    "Oj będzie grzebane.",
    "Panie, to nie takie hop-siup.",
    "Zaraz będzie gitara.",
    "Spokojnie, kontrolowany chaos.",
    "Dobra, tryb szwagra odpalony.",
    "Jeszcze sekunda i będzie elegancko.",
    "Jakoś to będzie, ale sprawdzę.",
    "Nie dotykać, samo się psuje.",
    "Dobra, tu trzeba sposobem.",
    "Kurwa, moment.",
    "No dobra, lecimy z tym bigosem.",
    "Pięknie, system robi fikołka.",
    "Kurwa, ale tu chlew.",
    "Dobra, kto to spierdolił?",
    "Sekunda, zaraz to zwyzywam.",
    "Dobra, gaszę ten burdel.",
    "Czekaj, robię sekcję zwłok.",
    "Ten stack sam się prosi o liścia.",
    "Moment, odklejam ten syf.",
    "No i mamy techniczne disco polo.",
    "Czekaj, system dostał z liścia.",
    "Kurwa, znowu magia z dupy.",
    "Ten kod brzmi jak krzyk o pomoc.",
    "Moment, szukam winnego klauna.",
    "Spokojnie, zaraz będzie egzekucja.",
    "Dobra, łamię ten error przez kolano.",
    "Kurwa, ale tu pachnie legacy.",
    "Czekaj, backend robi fikołka.",
    "No dobra, wchodzę w ten bajzel.",
    "Dobra, odpalam tryb chamstwa.",
    "Czekaj, logi zaraz zaczną śpiewać.",
)
DEFAULT_COMPILED_CONTEXT_MAX_ITEMS = 3
DEFAULT_COMPILED_CONTEXT_MAX_CHARS = 1200


class ConfigError(RuntimeError):
    """Raised when Jarvis configuration cannot be loaded."""


@dataclass(frozen=True)
class DaemonConfig:
    name: str = "jarvisd"
    host: str = "127.0.0.1"
    port: int = 41741
    log_level: str = "INFO"
    # jarvisd.log rotation (FIX-11): the daemon is always-on (launchd RunAtLoad)
    # so a plain FileHandler would grow without bound. Defaults cap the log at
    # ~60 MiB (1 active + 5 rotated × 10 MiB). max_bytes=0 disables rotation.
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = "~/.jarvis/jarvis.db"
    # Schema is always applied via ensure_schema at startup; there were dead
    # `migrations`/`destroy_existing` flags here that nothing read. They are
    # dropped (FIX-10). _build_section ignores unknown keys, so old configs
    # that still set them keep loading.


@dataclass(frozen=True)
class BrainCliAdapterConfig:
    enabled: bool = False
    command: str = ""
    args: list[str] = field(default_factory=list)
    model: str = ""
    timeout_seconds: int = 120
    # Streaming flags appended when a turn wants deltas (G4d). None = the
    # adapter's own defaults (claude: --output-format stream-json --verbose
    # --include-partial-messages).
    stream_args: list[str] | None = None


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
    compiled_context_enabled: bool = False
    compiled_context_max_items: int = DEFAULT_COMPILED_CONTEXT_MAX_ITEMS
    compiled_context_max_chars: int = DEFAULT_COMPILED_CONTEXT_MAX_CHARS
    compiled_context_include_procedural: bool = False


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    speak_responses: bool = False
    broker_enabled: bool = False
    default_tts: str = "mock"
    default_stt: str = "mock"
    ptt_mode: str = "hold"
    # Global push-to-talk hotkey held anywhere on the desktop (source
    # "global_hotkey"). Empty = no global hotkey (panel button still works).
    # Spec is a "+"-joined set of side-qualified modifiers, e.g.
    # "left_cmd+left_shift" (see jarvis/panel/hotkey.py). The panel's native
    # shell watches these keys and drives /voice/ptt/{down,up}; it needs
    # macOS Accessibility permission to observe keys outside its own window.
    ptt_hotkey: str = ""
    queue_persisted: bool = True
    recorder: str = "mock"
    # sox recorder (G4a): leases decide WHEN it runs, the AudioDeviceManager
    # decides WHICH input it uses (ADR-012). Empty binary = PATH lookup.
    # highpass/gain are the §4a empirical facts (80 Hz against hum; gain, if
    # any, must precede a future `silence` effect) — to be confronted with
    # the first real recording at the G4 live gate.
    recorder_binary: str = ""
    recorder_sample_rate: int = 16000
    recorder_highpass_hz: int = 80
    recorder_gain_db: float = 0.0
    # Locked-mode segmentation (FIX-09): rotate the capture every N seconds so
    # transcripts flow during a long sticky-listen lease instead of only when it
    # ends. 0 disables it (one capture per lease). Hold mode is unaffected in
    # practice — a PTT press is far shorter than a segment. Interval to be
    # confirmed at the G4 live gate (too short splits utterances mid-word).
    recorder_segment_seconds: float = 8.0
    # STT (G4b, decree §7.4). The gate thresholds and the junk list are the
    # mandatory hallucination filters (live-confirmed fact: silence
    # transcribes as „Dziękuję."); thresholds to be calibrated against the
    # first real recordings at the G4 live gate.
    stt_model: str = "mlx-community/whisper-large-v3-turbo"
    stt_language: str = "pl"
    # Transcription timeout (FIX-09): a stuck MLX/Metal call would otherwise
    # block the single STT worker forever. The bound scales with the captured
    # audio length: base + seconds_of_audio * per_audio_second.
    stt_timeout_seconds: float = 30.0
    stt_timeout_per_audio_second: float = 10.0
    stt_min_rms: int = 300
    stt_min_voiced_seconds: float = 0.3
    stt_min_voiced_ratio: float = 0.05
    stt_junk_phrases: tuple[str, ...] = (
        "dziękuję",
        "dziękuję bardzo",
        "dziękuję za oglądanie",
        "dzięki za oglądanie",
        "dziękuję za uwagę",
        "napisy stworzone przez społeczność amara.org",
        "napisy wykonane przez społeczność amara.org",
        "zapraszam na kolejny film",
        "do zobaczenia w kolejnym filmie",
        "thank you",
        "thank you for watching",
        "thanks for watching",
    )
    # Anti-echo (G4c): a transcript overlapping recently spoken TTS this much
    # is dropped before it can become a turn. Content-based, driven by DB
    # state (voice_queue rows that reached playback), never /tmp; thresholds
    # calibrated at the G4 live gate together with the stt_min_* values.
    anti_echo_window_seconds: int = 30
    anti_echo_overlap_threshold: float = 0.75
    # How long a voice turn retries a busy pipeline (e.g. a barged-in turn
    # still winding down) before the transcript is dropped with a log.
    transcript_turn_retry_seconds: float = 10.0
    ptt_hold_ttl_seconds: int = 30
    listen_lock_ttl_seconds: int = 600
    # Daemon-side lease TTL enforcement (FIX-04b): how often the sweeper
    # expires stale leases when the client never calls release().
    lease_sweep_interval_seconds: float = 5.0
    fillers: tuple[str, ...] = DEFAULT_VOICE_FILLERS
    filler_after_ms: int = 800
    min_sentence_chars: int = 12
    # Supertonic (decree §7.3; defaults from Ozzy's audition + live inventory
    # 2026-07-02). Empty binary = auto-detect (venv bin next to python, PATH).
    supertonic_binary: str = ""
    supertonic_voice: str = "M1"
    supertonic_lang: str = "pl"
    supertonic_steps: int = 14
    supertonic_speed: float = 1.35
    # G4 live-gate measurement (2026-07-02, N=5 per point): above ~1.15
    # speed supertonic clips the final phoneme of short sentences and above
    # ~1.25 sometimes emits a near-silent file; sentences of >=24 chars are
    # clean at 1.35. Sentences up to short_sentence_chars are synthesized at
    # short_sentence_speed (only ever slower, never faster). 0 = disabled.
    supertonic_short_sentence_chars: int = 24
    supertonic_short_sentence_speed: float = 1.0
    # sox's player: sox is part of the decreed stack (§7.4) and its legacy
    # macOS counterpart is a banned string in this repo (DAN's direct-play sin).
    playback_binary: str = "play"
    tts_timeout_seconds: int = 120
    # Playback pads (G4 live-gate fact 2026-07-02): each chunk is its own
    # `play` process, so the device stream opens/closes on chunk boundaries —
    # clicks, swallowed tails, on Bluetooth whole missing words. Pads keep
    # the process alive past the audible audio. 0.0 = no pad effect at all.
    playback_pad_start_seconds: float = 0.0
    playback_pad_end_seconds: float = 0.0
    # Pronunciation map (data, not code): anglicisms spoken Polish-
    # phonetically, e.g. runtime -> rantajm. Case-insensitive substring
    # match, so inflections ("runtime'ie") keep their endings.
    tts_pronunciations: dict[str, str] = field(default_factory=dict)


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
    width: int = 480
    height: int = 760


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
        memory=_build_memory_config(raw["memory"]),
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


def _build_memory_config(raw: dict[str, Any]) -> MemoryConfig:
    allowed = {field.name for field in fields(MemoryConfig)}
    selected = {key: value for key, value in raw.items() if key in allowed}
    _require_config_bool(
        "memory.compiled_context_enabled",
        selected.get("compiled_context_enabled"),
    )
    _require_config_int(
        "memory.compiled_context_max_items",
        selected.get("compiled_context_max_items"),
    )
    _require_config_int(
        "memory.compiled_context_max_chars",
        selected.get("compiled_context_max_chars"),
    )
    _require_config_bool(
        "memory.compiled_context_include_procedural",
        selected.get("compiled_context_include_procedural"),
    )
    try:
        return MemoryConfig(**selected)
    except TypeError as exc:
        raise ConfigError(f"Invalid config section MemoryConfig: {exc}") from exc


def _require_config_bool(name: str, value: Any) -> None:
    if value is not None and not isinstance(value, bool):
        raise ConfigError(f"{name} must be a bool")


def _require_config_int(name: str, value: Any) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ConfigError(f"{name} must be an int")


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
    stream_args = selected.get("stream_args")
    if stream_args is not None and (
        not isinstance(stream_args, list)
        or not all(isinstance(item, str) for item in stream_args)
    ):
        raise ConfigError(f"{section_name}.stream_args must be a list of strings")
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
