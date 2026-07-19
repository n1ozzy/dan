"""Shared test fixtures for DAN."""

from __future__ import annotations

import importlib
import os
from collections.abc import Generator
from pathlib import Path

import pytest

import dan.brain.auto_detect as auto_detect

_EXECUTION_KILL_SWITCHES = ("DAN_DISABLE_AUDIO", "DAN_DISABLE_MIC")
_INJECTED_AUDIO_REGRESSION_MODULES = {
    "test_audio_player.py": (
        "dan.voice.player",
        "assert_audio_execution_allowed",
    ),
    "test_voice_recorder.py": (
        "dan.voice.recorder",
        "assert_microphone_execution_allowed",
    ),
    "test_voice_tts_supertonic.py": (
        "dan.voice.tts",
        "assert_audio_execution_allowed",
    ),
}


def pytest_sessionstart(session: pytest.Session) -> None:
    """Require the process-level guard before test-module collection begins."""

    if not any(os.environ.get(name) == "1" for name in _EXECUTION_KILL_SWITCHES):
        return
    config = session.config
    plugin = config.pluginmanager.get_plugin("tests.audio_guard_plugin")
    marker = getattr(plugin, "PLUGIN_LOADED_MARKER", None)
    marker_attribute = getattr(plugin, "CONFIG_MARKER_ATTRIBUTE", "")
    if (
        plugin is None
        or not marker_attribute
        or getattr(config, marker_attribute, None) is not marker
    ):
        raise pytest.UsageError(
            "tests.audio_guard_plugin must be loaded when audio or microphone "
            "execution is disabled"
        )


@pytest.fixture(autouse=True)
def allow_injected_audio_regression_edges(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Opt the three legacy fake-edge suites into their injected boundaries."""

    test_path = Path(str(request.node.path)).resolve()
    tests_root = Path(__file__).resolve().parent
    target = _INJECTED_AUDIO_REGRESSION_MODULES.get(test_path.name)
    if test_path.parent != tests_root or target is None:
        return
    module_name, guard_name = target
    module = importlib.import_module(module_name)
    real_guard = getattr(module, guard_name)

    def allow_injected_boundary(*, operation: str) -> None:
        if module_name == "dan.voice.player" and operation != "coreaudio playback":
            real_guard(operation=operation)

    monkeypatch.setattr(module, guard_name, allow_injected_boundary)


@pytest.fixture(autouse=True)
def reset_auto_detect() -> Generator[None, None, None]:
    """Reset auto-detection state after each test."""
    yield
    auto_detect.set_which_fn(None)


@pytest.fixture(autouse=True)
def stub_claude_model_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep live Claude model discovery deterministic and hermetic in tests.

    ``ClaudeCliAdapter.available_models`` resolves the real model list from the
    Claude Code CLI (cached in ~/.dan). Tests must never spawn ``claude`` nor
    write to the real ~/.dan, so we replace the resolver the adapter imported
    with a fixed list. Tests that exercise the resolver itself
    (test_claude_models.py) call ``resolve_available_models`` directly with an
    injected runner + tmp cache, so they are unaffected by this stub.
    """

    import dan.brain.claude_cli_adapter as cli_adapter

    def _fixed_models(command: str = "claude", **_kwargs: object) -> list[str]:
        return ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"]

    monkeypatch.setattr(cli_adapter, "resolve_available_models", _fixed_models)


@pytest.fixture
def cutover(tmp_path):
    """Synthetic cutover precondition harness (fixture tree in tmp_path)."""
    from tests.cutover_helpers import CutoverHarness

    return CutoverHarness(tmp_path)


@pytest.fixture
def cutover_fixture(tmp_path):
    """Synthetic full cutover/rollback harness (fixture tree in tmp_path)."""
    from tests.cutover_helpers import CutoverFixture

    return CutoverFixture(tmp_path)


@pytest.fixture
def mock_codex_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock codex CLI as available."""
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(
        routes_runtime.shutil,
        "which",
        lambda command: "/usr/bin/fake-codex" if command == "fake-codex" else None,
    )
    auto_detect.set_which_fn(lambda cmd: "/usr/bin/fake-codex" if cmd == "codex" else None)
    monkeypatch.setattr(routes_runtime, "_safe_probe_cli_version", lambda command: ("codex fake 1.0.0", "ok", None))
    monkeypatch.setattr(routes_runtime, "_safe_probe_codex_auth_status", lambda: ("logged_in", None))


@pytest.fixture
def mock_claude_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Claude CLI as available."""
    import dan.api.routes_runtime as routes_runtime

    monkeypatch.setattr(
        routes_runtime.shutil,
        "which",
        lambda command: "/usr/bin/fake-claude" if command == "fake-claude" else None,
    )
    auto_detect.set_which_fn(lambda cmd: "/usr/bin/fake-claude" if cmd == "claude" else None)
    monkeypatch.setattr(routes_runtime, "_safe_probe_cli_version", lambda command: ("claude fake 1.0.0", "ok", None))
    monkeypatch.setattr(routes_runtime, "_safe_probe_claude_auth_status", lambda command: ("Logged in as test@test.com", None))
