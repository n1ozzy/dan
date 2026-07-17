"""Import smoke tests for the final DAN package."""

from __future__ import annotations

import importlib
import importlib.util
from importlib.machinery import PathFinder
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


MODULES = (
    "dan",
    "dan.cli",
    "dan.config",
    "dan.paths",
    "dan.logging",
    "dan.daemon",
    "dan.daemon.app",
    "dan.daemon.lifecycle",
    "dan.daemon.state_machine",
    "dan.runtime",
    "dan.runtime.supervisor",
    "dan.runtime.models",
    "dan.api",
    "dan.api.routes_health",
    "dan.api.routes_state",
    "dan.api.routes_events",
    "dan.api.routes_input",
    "dan.api.routes_brain",
    "dan.api.routes_voice",
    "dan.api.routes_audio",
    "dan.api.routes_runtime",
    "dan.api.routes_workers",
    "dan.api.routes_memory",
    "dan.api.routes_tools",
    "dan.api.websocket",
    "dan.store",
    "dan.store.db",
    "dan.store.migrations",
    "dan.store.event_store",
    "dan.store.repositories",
    "dan.events",
    "dan.events.types",
    "dan.events.models",
    "dan.events.bus",
    "dan.turns",
    "dan.turns.models",
    "dan.turns.repository",
    "dan.turns.orchestrator",
    "dan.turns.policies",
    "dan.brain",
    "dan.brain.base",
    "dan.brain.manager",
    "dan.brain.context_builder",
    "dan.brain.mock_adapter",
    "dan.brain.claude_cli_adapter",
    "dan.brain.codex_cli_adapter",
    "dan.brain.openai_adapter",
    "dan.memory",
    "dan.memory.manager",
    "dan.memory.summarizer",
    "dan.memory.retrieval",
    "dan.memory.policies",
    "dan.audio",
    "dan.audio.models",
    "dan.audio.devices",
    "dan.audio.policy",
    "dan.voice",
    "dan.voice.models",
    "dan.voice.queue",
    "dan.voice.broker",
    "dan.voice.tts",
    "dan.voice.stt",
    "dan.voice.vad",
    "dan.voice.anti_echo",
    "dan.voice.listening",
    "dan.tools",
    "dan.tools.registry",
    "dan.tools.permissions",
    "dan.tools.shell_tool",
    "dan.tools.file_tool",
    "dan.tools.system_tool",
    "dan.workers",
    "dan.workers.jobs",
    "dan.workers.broker",
    "dan.workers.worker_events",
    "dan.workers.codex_worker",
    "dan.workers.claude_worker",
    "dan.workers.mock_worker",
    "dan.panel",
    "dan.panel.menubar_app",
    "dan.panel.webview_bridge",
)


def project_scripts(path: Path) -> dict[str, str]:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return payload["project"]["scripts"]


def test_final_python_package_is_dan() -> None:
    original_meta_path = sys.meta_path[:]
    original_path = sys.path[:]
    try:
        sys.meta_path[:] = [PathFinder]
        sys.path[:] = [str(Path(__file__).resolve().parents[1])]
        assert importlib.util.find_spec("dan") is not None
        assert importlib.util.find_spec("jarvis") is None
    finally:
        sys.meta_path[:] = original_meta_path
        sys.path[:] = original_path


def test_console_entrypoints_are_final_names() -> None:
    scripts = project_scripts(Path("pyproject.toml"))
    assert scripts["dan"] == "dan.cli:main"
    assert scripts["dand"] == "dan.cli:daemon_main"
    assert scripts["dan-memory-mcp"] == "dan.mcp.memory_server:main"
    assert not any("jarvis" in name for name in scripts)


def test_import_does_not_move_or_create_runtime_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    legacy = home / ".jarvis"
    legacy.mkdir(parents=True)
    sentinel = legacy / "sentinel"
    sentinel.write_bytes(b"legacy-state")
    environment = {**os.environ, "HOME": str(home)}

    completed = subprocess.run(
        [sys.executable, "-c", "import dan.paths"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert sentinel.read_bytes() == b"legacy-state"
    assert not (home / ".dan").exists()


def test_final_runtime_paths_resolve_under_dan_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dan.config import load_config
    from dan.paths import resolve_runtime_paths

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    paths = resolve_runtime_paths(load_config(Path("config/dan.example.toml")))

    assert paths.home == home / ".dan"
    assert paths.config_path == home / ".dan" / "config.toml"
    assert paths.db_path == home / ".dan" / "dan.db"
    assert paths.logs_dir == home / ".dan" / "logs"
    assert paths.runtime_dir == home / ".dan" / "runtime"
    assert paths.owner_path == home / ".dan" / "owner.toml"
    assert paths.secrets_path == home / ".dan" / "secrets.env"


def test_every_required_python_module_imports() -> None:
    for module_name in MODULES:
        importlib.import_module(module_name)
