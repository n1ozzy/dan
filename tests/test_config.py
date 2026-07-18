"""Prompt 02 configuration, path, logging and CLI tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import dan.config as config_module
from dan.config import (
    BrainConfig,
    COMPILED_MEMORY_ENABLED_ENV,
    COMPILED_MEMORY_FORCE_DISABLED_ENV,
    ConfigError,
    compiled_memory_operator_env_controls,
    load_config,
)
from dan.logging import redact_secrets
from dan.memory.compiler import MemoryCompilerConfig
from dan.paths import ensure_runtime_dirs, expand_user_path, resolve_runtime_paths


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_install_defaults_to_the_single_persistent_claude_adapter() -> None:
    brain = BrainConfig()

    assert brain.default_adapter == "claude_cli"
    assert brain.provider_sessions_are_memory is False
    assert not hasattr(brain, "claude_cli_warm")
    assert brain.context_window_tokens == 200_000
    assert brain.context_checkpoint_percent == 70.0
    assert brain.context_compact_percent == 80.0
    assert brain.context_recycle_percent == 90.0


@pytest.mark.parametrize(
    ("brain_lines", "message"),
    (
        ("context_window_tokens = 0", "brain.context_window_tokens must be a positive int"),
        ("context_window_tokens = true", "brain.context_window_tokens must be a positive int"),
        (
            'context_checkpoint_percent = "70"',
            "brain context thresholds must be numbers",
        ),
        (
            "context_checkpoint_percent = 0",
            "0 < checkpoint < compact < recycle <= 100",
        ),
        (
            "context_checkpoint_percent = 80\ncontext_compact_percent = 80",
            "0 < checkpoint < compact < recycle <= 100",
        ),
        (
            "context_compact_percent = 90\ncontext_recycle_percent = 80",
            "0 < checkpoint < compact < recycle <= 100",
        ),
        (
            "context_recycle_percent = 101",
            "0 < checkpoint < compact < recycle <= 100",
        ),
    ),
)
def test_persistent_brain_context_policy_rejects_invalid_config(
    tmp_path: Path,
    brain_lines: str,
    message: str,
) -> None:
    config_path = tmp_path / "invalid-brain-policy.toml"
    content = canonical_config().replace(
        "context_budget_chars = 24000",
        f"context_budget_chars = 24000\n{brain_lines}",
        1,
    )
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)


def canonical_config(**overrides: str | int | bool) -> str:
    daemon_port = overrides.get("daemon_port", 41741)
    brain_adapter = overrides.get("brain_adapter", "mock")
    runtime_home = overrides.get("runtime_home", "~/.dan")
    db_path = overrides.get("db_path", "~/.dan/dan.db")
    memory_enabled = overrides.get("memory_enabled", True)
    compiled_context_enabled = overrides.get("compiled_context_enabled", False)
    compiled_context_max_items = overrides.get(
        "compiled_context_max_items", MemoryCompilerConfig().max_items
    )
    compiled_context_max_chars = overrides.get(
        "compiled_context_max_chars", MemoryCompilerConfig().max_chars
    )
    compiled_context_include_procedural = overrides.get(
        "compiled_context_include_procedural", False
    )
    return f"""
[daemon]
name = "dand"
host = "127.0.0.1"
port = {daemon_port}
log_level = "INFO"

[database]
path = "{db_path}"
migrations = "manual"
destroy_existing = false

[brain]
default_adapter = "{brain_adapter}"
default_model = "mock-local"
timeout_seconds = 60
context_budget_chars = 24000
provider_sessions_are_memory = false

[memory]
enabled = {str(memory_enabled).lower()}
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true
compiled_context_enabled = {str(compiled_context_enabled).lower()}
compiled_context_max_items = {compiled_context_max_items}
compiled_context_max_chars = {compiled_context_max_chars}
compiled_context_include_procedural = {str(compiled_context_include_procedural).lower()}

[voice]
enabled = false
speak_responses = false
broker_enabled = false
default_tts = "mock"
default_stt = "mock"
ptt_mode = "hold"
queue_persisted = true

[audio]
enabled = false
input_policy = "pin_builtin_mic"
preferred_input = "Mikrofon (MacBook Air)"
output_policy = "follow_system_default"
allow_bluetooth_microphone = false
always_listen_enabled = false

[panel]
enabled = false
api_base_url = "http://127.0.0.1:41741"
width = 420
height = 620

[security]
localhost_only = true
require_approval_for_shell = true
require_approval_for_file_write = true
require_approval_for_network = true
destructive_tools_enabled = false

[runtime]
home = "{runtime_home}"
logs_dir = "~/.dan/logs"
runtime_dir = "~/.dan/runtime"
pid_file = "~/.dan/runtime/dand.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.dan.dand"
install_automatically = false
"""


def write_config(path: Path, **overrides: str | int | bool) -> Path:
    path.write_text(canonical_config(**overrides), encoding="utf-8")
    return path


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.pop("DAN_CONFIG", None)
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "dan.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_config_fails_without_real_dan_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAN_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", tmp_path / "missing-repo.toml")

    with pytest.raises(ConfigError, match="DAN config not found"):
        load_config()


def test_loads_home_config_toml_before_repo_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home_config = home / ".dan" / "config.toml"
    repo_config = tmp_path / "repo" / "config" / "dan.toml"
    home_config.parent.mkdir(parents=True)
    repo_config.parent.mkdir(parents=True)
    write_config(home_config, daemon_port=41901)
    write_config(repo_config, daemon_port=41902)
    monkeypatch.delenv("DAN_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", repo_config)

    config = load_config()

    assert config.source_path == home_config
    assert config.daemon.port == 41901


def test_loads_repo_dan_toml_when_home_config_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_config = write_config(tmp_path / "repo-dan.toml", daemon_port=41903)
    monkeypatch.delenv("DAN_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", repo_config)

    config = load_config()

    assert config.source_path == repo_config
    assert config.daemon.port == 41903


def test_explicit_example_config_still_loads() -> None:
    config = load_config(ROOT / "config" / "dan.example.toml")

    assert config.source_path == ROOT / "config" / "dan.example.toml"
    assert config.daemon.name == "dand"
    assert config.daemon.port == 41741
    assert config.memory.compiled_context_enabled is False
    assert config.memory.compiled_context_max_items == MemoryCompilerConfig().max_items
    assert config.memory.compiled_context_max_chars == MemoryCompilerConfig().max_chars
    assert config.memory.compiled_context_include_procedural is False
    assert config.launchd.label == "com.dan.dand"


def test_example_config_compiled_memory_defaults_off() -> None:
    config = load_config(ROOT / "config" / "dan.example.toml")

    assert config.memory.compiled_context_enabled is False
    assert config.memory.compiled_context_max_items == MemoryCompilerConfig().max_items
    assert config.memory.compiled_context_max_chars == MemoryCompilerConfig().max_chars
    assert config.memory.compiled_context_include_procedural is False


def test_compiled_memory_operator_env_absent_has_no_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(COMPILED_MEMORY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)

    controls = compiled_memory_operator_env_controls()

    assert controls.enabled is None
    assert controls.force_disabled is False


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    (
        ("true", True),
        ("1", True),
        ("on", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("off", False),
        ("no", False),
    ),
)
def test_compiled_memory_operator_env_enabled_parses_typed_bool_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, raw_value)
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)

    assert compiled_memory_operator_env_controls().enabled is expected


def test_invalid_compiled_memory_operator_env_enabled_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPILED_MEMORY_ENABLED_ENV, "definitely")
    monkeypatch.delenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, raising=False)

    controls = compiled_memory_operator_env_controls()

    assert controls.enabled is False
    assert controls.force_disabled is False


def test_compiled_memory_operator_env_force_disabled_parses_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(COMPILED_MEMORY_ENABLED_ENV, raising=False)
    monkeypatch.setenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, "true")

    controls = compiled_memory_operator_env_controls()

    assert controls.enabled is None
    assert controls.force_disabled is True


def test_invalid_compiled_memory_operator_env_force_disabled_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(COMPILED_MEMORY_ENABLED_ENV, raising=False)
    monkeypatch.setenv(COMPILED_MEMORY_FORCE_DISABLED_ENV, "definitely")

    controls = compiled_memory_operator_env_controls()

    assert controls.enabled is None
    assert controls.force_disabled is True


def test_explicit_config_can_enable_compiled_memory_context(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "compiled-memory.toml",
        compiled_context_enabled=True,
        compiled_context_max_items=7,
        compiled_context_max_chars=2048,
        compiled_context_include_procedural=True,
    )

    config = load_config(config_path)

    assert config.memory.compiled_context_enabled is True
    assert config.memory.compiled_context_max_items == 7
    assert config.memory.compiled_context_max_chars == 2048
    assert config.memory.compiled_context_include_procedural is True


@pytest.mark.parametrize(
    ("line", "replacement", "message"),
    (
        (
            "compiled_context_enabled = false",
            'compiled_context_enabled = "true"',
            "memory.compiled_context_enabled must be a bool",
        ),
        (
            f"compiled_context_max_items = {MemoryCompilerConfig().max_items}",
            'compiled_context_max_items = "3"',
            "memory.compiled_context_max_items must be an int",
        ),
        (
            f"compiled_context_max_chars = {MemoryCompilerConfig().max_chars}",
            'compiled_context_max_chars = "1200"',
            "memory.compiled_context_max_chars must be an int",
        ),
        (
            "compiled_context_include_procedural = false",
            'compiled_context_include_procedural = "false"',
            "memory.compiled_context_include_procedural must be a bool",
        ),
    ),
)
def test_compiled_memory_config_rejects_invalid_types(
    tmp_path: Path,
    line: str,
    replacement: str,
    message: str,
) -> None:
    config_path = tmp_path / "invalid-compiled-memory.toml"
    config_path.write_text(
        canonical_config().replace(line, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)


def test_default_voice_fillers_have_enough_variation(monkeypatch: pytest.MonkeyPatch) -> None:
    from dan.voice.speech import DEFAULT_FILLERS

    monkeypatch.delenv("DAN_CONFIG", raising=False)

    fillers = load_config(ROOT / "config" / "dan.example.toml").voice.fillers

    assert len(fillers) >= 4
    assert fillers == DEFAULT_FILLERS
    assert len(set(fillers)) == len(fillers)


def test_explicit_config_path_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DAN_CONFIG", raising=False)
    config_path = write_config(tmp_path / "custom.toml", daemon_port=41888)

    config = load_config(config_path)

    assert config.source_path == config_path
    assert config.daemon.port == 41888


def test_dan_config_environment_variable_keeps_single_production_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path / "env.toml", brain_adapter="env-mock")
    monkeypatch.setenv("DAN_CONFIG", str(config_path))

    config = load_config()

    assert config.source_path == config_path
    assert config.brain.default_adapter == "claude_cli"


def test_invalid_toml_raises_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("[daemon\nname = 'broken'", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(config_path)


def test_missing_required_section_raises_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"
    config_path.write_text(canonical_config().replace("[daemon]", "[not_daemon]", 1), encoding="utf-8")

    with pytest.raises(ConfigError, match="Missing required config section: daemon"):
        load_config(config_path)


def test_runtime_path_expansion_resolves_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert expand_user_path("~/.dan/dan.db") == tmp_path / ".dan" / "dan.db"


def test_resolve_runtime_paths_does_not_create_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = write_config(tmp_path / "paths.toml")
    config = load_config(config_path)

    paths = resolve_runtime_paths(config)

    assert paths.home == tmp_path / ".dan"
    assert paths.db_path == tmp_path / ".dan" / "dan.db"
    assert not paths.home.exists()
    assert not paths.db_path.exists()
    assert not paths.pid_file.exists()


def test_ensure_runtime_dirs_creates_only_expected_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = write_config(tmp_path / "dirs.toml")
    paths = resolve_runtime_paths(load_config(config_path))

    ensure_runtime_dirs(paths)

    expected_dirs = {
        tmp_path / ".dan",
        tmp_path / ".dan" / "logs",
        tmp_path / ".dan" / "runtime",
    }
    actual_dirs = {path for path in tmp_path.rglob("*") if path.is_dir()}
    assert actual_dirs == expected_dirs
    assert not paths.db_path.exists()
    assert not paths.pid_file.exists()


# --- FIX-10: owner-only permissions on DAN-owned runtime state -------------


def _mode(path: Path) -> int:
    import stat

    return stat.S_IMODE(os.stat(path).st_mode)


def test_ensure_runtime_dirs_are_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = resolve_runtime_paths(load_config(write_config(tmp_path / "perm.toml")))

    ensure_runtime_dirs(paths)

    assert _mode(paths.home) == 0o700
    assert _mode(paths.logs_dir) == 0o700
    assert _mode(paths.runtime_dir) == 0o700


def test_database_file_is_owner_only(tmp_path: Path) -> None:
    from dan.store.db import close_quietly, initialize_database

    db_path = tmp_path / "state.db"
    conn = initialize_database(db_path)
    try:
        assert _mode(db_path) == 0o600
    finally:
        close_quietly(conn)


def test_log_file_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dan.logging import configure_logging

    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(write_config(tmp_path / "log.toml"))
    paths = resolve_runtime_paths(config)

    logger = configure_logging(config, paths)
    try:
        assert _mode(paths.log_file) == 0o600
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_database_config_dropped_dead_legacy_flags(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path / "db.toml"))

    assert not hasattr(config.database, "migrations")
    assert not hasattr(config.database, "destroy_existing")


def test_config_with_legacy_database_flags_still_loads(tmp_path: Path) -> None:
    # canonical_config still emits migrations/destroy_existing under [database];
    # removing the fields must be backward compatible — _build_section drops
    # unknown keys, so an old config keeps loading instead of erroring.
    config = load_config(write_config(tmp_path / "legacy.toml"))

    assert str(config.database.path)


def test_cli_config_show_returns_valid_json(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "cli.toml", daemon_port=41999)

    result = run_cli("--config", str(config_path), "config", "show")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["daemon"]["port"] == 41999
    assert payload["brain"]["default_model"] == "mock-local"


def test_cli_paths_show_returns_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = write_config(tmp_path / "cli-paths.toml")

    result = run_cli("--config", str(config_path), "paths", "show", env={"HOME": str(tmp_path)})

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["home"] == str(tmp_path / ".dan")
    assert payload["db_path"] == str(tmp_path / ".dan" / "dan.db")


def test_cli_doctor_returns_expected_keys(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "doctor.toml")
    expected_keys = {
        "config_ok",
        "runtime_home",
        "db_path",
        "logs_dir",
        "runtime_dir",
        "launchd_label",
        "voice_enabled",
        "brain_adapter",
        "daemon_host",
        "daemon_port",
        "daemon",
        "voice_runtime",
    }

    result = run_cli("--config", str(config_path), "doctor")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == expected_keys
    assert payload["config_ok"] is True
    assert payload["launchd_label"] == "com.dan.dand"
    assert payload["voice_enabled"] is False


def test_secret_redaction_redacts_api_key_like_values() -> None:
    original = (
        "OPENAI_API_KEY=sk-proj-abc123 "
        "ANTHROPIC_API_KEY=sk-ant-secret "
        "GROQ_API_KEY=gsk_secret "
        "xi-api-key: xi_12345 "
        "Authorization: Bearer secret-token"
    )

    redacted = redact_secrets(original)

    assert "sk-proj-abc123" not in redacted
    assert "sk-ant-secret" not in redacted
    assert "gsk_secret" not in redacted
    assert "xi_12345" not in redacted
    assert "secret-token" not in redacted
    assert redacted.count("[REDACTED]") >= 5


def test_runtime_files_do_not_contain_forbidden_legacy_strings() -> None:
    forbidden = (
        "/Users/" "n1_ozzy" "/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "dan",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".toml", ".md", ".sh", ".example"}
    offenders: list[tuple[str, str]] = []
    allowed_contracts = {("dan/voice/shared_broker.py", "/tmp/dan")}

    for root in roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
                text = path.read_text(encoding="utf-8")
                for snippet in forbidden:
                    relative = str(path.relative_to(ROOT))
                    if (relative, snippet) in allowed_contracts:
                        continue
                    if snippet in text:
                        offenders.append((relative, snippet))

    assert offenders == []
