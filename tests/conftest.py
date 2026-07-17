"""Shared test fixtures for DAN."""

from __future__ import annotations

import pytest
from collections.abc import Generator

import dan.brain.auto_detect as auto_detect


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