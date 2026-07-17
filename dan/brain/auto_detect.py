"""Auto-detection of available brain providers on the system - Production ready."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from dan.brain.claude_cli_contract import ClaudeCliEffortLevel
from dan.brain.groq_adapter import GROQ_MODELS

_CLAUDE_CLI_EFFORTS = [e.value for e in ClaudeCliEffortLevel]
_CODEX_CLI_EFFORTS = [e.value for e in ClaudeCliEffortLevel if e != ClaudeCliEffortLevel.MAX]


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    display_name: str
    available: bool
    models: list[str]
    efforts: list[str]
    streaming: bool
    tools: bool
    config_hint: str | None = None


# Allow tests to inject a custom `which` function
_which_fn: Callable[[str], str | None] | None = None


def set_which_fn(fn: Callable[[str], str | None] | None) -> None:
    """Set a custom `which` function for testing."""
    global _which_fn
    _which_fn = fn


def _which(cmd: str) -> str | None:
    if _which_fn is not None:
        return _which_fn(cmd)
    return shutil.which(cmd)


def _run_json(cmd: list[str], timeout: float = 5.0) -> dict | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            import json
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


def _run_text(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def detect_claude_cli() -> ProviderInfo:
    """Detect Claude CLI and available models."""
    path = _which("claude")
    if not path:
        return ProviderInfo(
            name="claude_cli",
            display_name="Claude CLI",
            available=False,
            models=[],
            efforts=_CLAUDE_CLI_EFFORTS,
            streaming=True,
            tools=True,
            config_hint="Install Claude CLI: npm install -g @anthropic-ai/claude-code",
        )

    return ProviderInfo(
        name="claude_cli",
        display_name="Claude CLI",
        available=True,
        models=["sonnet", "opus", "haiku", "fable"],
        efforts=_CLAUDE_CLI_EFFORTS,
        streaming=True,
        tools=True,
    )


def detect_codex_cli() -> ProviderInfo:
    """Detect Codex CLI and available models."""
    path = _which("codex")
    if not path:
        return ProviderInfo(
            name="codex_cli",
            display_name="Codex CLI",
            available=False,
            models=[],
            efforts=_CODEX_CLI_EFFORTS,
            streaming=False,
            tools=True,
            config_hint="Install Codex CLI: https://github.com/openai/codex",
        )

    return ProviderInfo(
        name="codex_cli",
        display_name="Codex CLI",
        available=True,
        models=["gpt-5", "gpt-5.5", "gpt-4o", "o3", "o3-mini", "o4-mini"],
        efforts=_CODEX_CLI_EFFORTS,
        streaming=False,
        tools=True,
    )


def detect_groq() -> ProviderInfo:
    """Detect Groq API availability via API key."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return ProviderInfo(
            name="groq",
            display_name="Groq API",
            available=False,
            models=[],
            efforts=[],
            streaming=True,
            tools=False,
            config_hint="Set GROQ_API_KEY environment variable",
        )

    return ProviderInfo(
        name="groq",
        display_name="Groq API",
        available=True,
        models=list(GROQ_MODELS),
        efforts=[],
        streaming=True,
        tools=False,
    )


def detect_all_providers() -> dict[str, ProviderInfo]:
    """Detect all available providers on the system."""
    return {
        "claude_cli": detect_claude_cli(),
        "codex_cli": detect_codex_cli(),
        "groq": detect_groq(),
    }


def get_available_adapter_names() -> list[str]:
    """Get list of adapter names that are available on this system."""
    providers = detect_all_providers()
    return [name for name, info in providers.items() if info.available]