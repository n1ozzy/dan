"""Prompt 02 configuration, path, logging and CLI tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.config import ConfigError, load_config
from jarvis.logging import redact_secrets
from jarvis.paths import ensure_runtime_dirs, expand_user_path, resolve_runtime_paths


ROOT = Path(__file__).resolve().parents[1]


def canonical_config(**overrides: str | int | bool) -> str:
    daemon_port = overrides.get("daemon_port", 41741)
    brain_adapter = overrides.get("brain_adapter", "mock")
    runtime_home = overrides.get("runtime_home", "~/.jarvis")
    db_path = overrides.get("db_path", "~/.jarvis/jarvis.db")
    return f"""
[daemon]
name = "jarvisd"
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
enabled = true
max_active_blocks = 50
max_context_chars = 12000
worker_candidates_require_promotion = true

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
logs_dir = "~/.jarvis/logs"
runtime_dir = "~/.jarvis/runtime"
pid_file = "~/.jarvis/runtime/jarvisd.pid"
legacy_detection = "report_only"

[launchd]
enabled = false
label = "com.ozzy.jarvisd"
install_automatically = false
"""


def write_config(path: Path, **overrides: str | int | bool) -> Path:
    path.write_text(canonical_config(**overrides), encoding="utf-8")
    return path


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.pop("JARVIS_CONFIG", None)
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "jarvis.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_loads_example_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_CONFIG", raising=False)

    config = load_config()

    assert config.source_path == ROOT / "config" / "jarvis.example.toml"
    assert config.daemon.name == "jarvisd"
    assert config.daemon.port == 41741
    assert config.brain.default_adapter == "mock"
    assert config.launchd.label == "com.ozzy.jarvisd"


def test_default_voice_fillers_have_enough_variation(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.voice.speech import DEFAULT_FILLERS

    monkeypatch.delenv("JARVIS_CONFIG", raising=False)

    fillers = load_config().voice.fillers

    assert fillers == DEFAULT_FILLERS
    assert len(fillers) >= 40
    selected_fillers = {
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
    }
    assert selected_fillers.issubset(set(fillers))
    assert "Już sprawdzam." not in fillers
    assert len(set(fillers)) == len(fillers)
    dan_markers = (
        "kurwa",
        "spierdal",
        "jeb",
        "bajzel",
        "crash",
        "backend",
        "zjeb",
        "pustostan",
        "error 500",
        "mielę",
        "tnę",
    )
    assert sum(
        1 for filler in fillers if any(marker in filler.lower() for marker in dan_markers)
    ) >= 8
    rejected_markers = ("sram", "kibel", "pierdzi", "biegunk", "papier")
    assert not any(
        marker in filler.lower() for filler in fillers for marker in rejected_markers
    )


def test_explicit_config_path_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JARVIS_CONFIG", raising=False)
    config_path = write_config(tmp_path / "custom.toml", daemon_port=41888)

    config = load_config(config_path)

    assert config.source_path == config_path
    assert config.daemon.port == 41888


def test_jarvis_config_environment_variable_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path / "env.toml", brain_adapter="env-mock")
    monkeypatch.setenv("JARVIS_CONFIG", str(config_path))

    config = load_config()

    assert config.source_path == config_path
    assert config.brain.default_adapter == "env-mock"


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

    assert expand_user_path("~/.jarvis/jarvis.db") == tmp_path / ".jarvis" / "jarvis.db"


def test_resolve_runtime_paths_does_not_create_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = write_config(tmp_path / "paths.toml")
    config = load_config(config_path)

    paths = resolve_runtime_paths(config)

    assert paths.home == tmp_path / ".jarvis"
    assert paths.db_path == tmp_path / ".jarvis" / "jarvis.db"
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
        tmp_path / ".jarvis",
        tmp_path / ".jarvis" / "logs",
        tmp_path / ".jarvis" / "runtime",
    }
    actual_dirs = {path for path in tmp_path.rglob("*") if path.is_dir()}
    assert actual_dirs == expected_dirs
    assert not paths.db_path.exists()
    assert not paths.pid_file.exists()


# --- FIX-10: owner-only permissions on Jarvis-owned runtime state -------------


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
    from jarvis.store.db import close_quietly, initialize_database

    db_path = tmp_path / "state.db"
    conn = initialize_database(db_path)
    try:
        assert _mode(db_path) == 0o600
    finally:
        close_quietly(conn)


def test_log_file_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.logging import configure_logging

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
    assert payload["home"] == str(tmp_path / ".jarvis")
    assert payload["db_path"] == str(tmp_path / ".jarvis" / "jarvis.db")


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
    }

    result = run_cli("--config", str(config_path), "doctor")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == expected_keys
    assert payload["config_ok"] is True
    assert payload["launchd_label"] == "com.ozzy.jarvisd"
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
        "/Users/n1_ozzy/Documents/dev/dan",
        "/tmp/dan",
        "afplay",
        "--dangerously-skip-permissions",
    )
    roots = (
        ROOT / "jarvis",
        ROOT / "config",
        ROOT / "scripts",
        ROOT / "launchd",
    )
    text_suffixes = {".py", ".toml", ".md", ".sh", ".example"}
    offenders: list[tuple[str, str]] = []

    for root in roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
