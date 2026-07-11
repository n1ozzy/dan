"""Brain adapter selection for Jarvis v4.1 - Production ready."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from jarvis.brain.auto_detect import detect_all_providers
from jarvis.brain.base import BrainAdapter, BrainRequest, BrainResponse
from jarvis.brain.claude_cli_adapter import ClaudeCliAdapter
from jarvis.brain.claude_cli_warm_adapter import ClaudeCliWarmAdapter
from jarvis.brain.groq_adapter import create_groq_adapter
from jarvis.brain.sync_adapter import wrap_async_adapter
from jarvis.brain.test_adapter import create_test_adapter


class BrainManagerError(Exception):
    """Raised when a brain adapter cannot be selected."""


class BrainManager:
    """Selects a stateless brain adapter without owning provider session state.
    
    Production adapters only: claude_cli, groq. (Codex CLI intentionally not
    registered — Jarvis runs on Claude Code only, owner decree.)
    """

    def __init__(self, adapters: Iterable[BrainAdapter], default_adapter: str) -> None:
        self._adapters: dict[str, BrainAdapter] = {}
        for adapter in adapters:
            name = getattr(adapter, "name", None)
            if not isinstance(name, str) or not name:
                raise BrainManagerError("Brain adapter must expose a non-empty name")
            if name in self._adapters:
                raise BrainManagerError(f"Duplicate brain adapter registered: {name}")
            self._adapters[name] = adapter

        if default_adapter not in self._adapters:
            raise BrainManagerError(f"Default brain adapter is not registered: {default_adapter}")

        self._current_adapter_name = default_adapter

    @classmethod
    def from_config(
        cls, config: object, *, generation_registry: Any | None = None
    ) -> "BrainManager":
        brain_config = getattr(config, "brain", None)
        config_default = str(getattr(brain_config, "default_adapter", "claude_cli_warm") or "claude_cli_warm")

        adapters: list[BrainAdapter] = []

        # Jarvis runtime-lab is Claude-only. Warm Claude is preferred because it
        # avoids one subprocess per turn; plain Claude CLI stays as fallback.
        warm_config = getattr(brain_config, "claude_cli_warm", None)
        if warm_config is None:
            from types import SimpleNamespace
            warm_config = SimpleNamespace(
                command="claude",
                args=["-p"],
                model=getattr(brain_config, "default_model", ""),
                timeout_seconds=120,
                enabled=True,
            )
        if bool(getattr(warm_config, "enabled", True)) or config_default == "claude_cli_warm":
            adapters.append(
                ClaudeCliWarmAdapter(
                    command=getattr(warm_config, "command", "claude"),
                    args=getattr(warm_config, "args", ["-p"]),
                    model=getattr(warm_config, "model", getattr(brain_config, "default_model", "")),
                    timeout_seconds=getattr(warm_config, "timeout_seconds", 120),
                    generation_registry=generation_registry,
                )
            )

        claude_config = getattr(brain_config, "claude_cli", None)
        if claude_config is None:
            from types import SimpleNamespace
            claude_config = SimpleNamespace(
                command="claude",
                args=["-p"],
                model=getattr(brain_config, "default_model", ""),
                effort="",
                permission_mode="bypassPermissions",
                output_format="",
                input_format="",
                tools=[],
                allowed_tools=[],
                disallowed_tools=[],
                mcp_config_path="",
                strict_mcp_config=None,
                timeout_seconds=120,
                stream_args=None,
                enabled=True,
            )
        adapters.append(
            ClaudeCliAdapter(
                command=getattr(claude_config, "command", "claude"),
                args=getattr(claude_config, "args", ["-p"]),
                model=getattr(claude_config, "model", getattr(brain_config, "default_model", "")),
                effort=getattr(claude_config, "effort", ""),
                permission_mode=getattr(claude_config, "permission_mode", "bypassPermissions"),
                output_format=getattr(claude_config, "output_format", ""),
                input_format=getattr(claude_config, "input_format", ""),
                tools=getattr(claude_config, "tools", []),
                allowed_tools=getattr(claude_config, "allowed_tools", []),
                disallowed_tools=getattr(claude_config, "disallowed_tools", []),
                mcp_config_path=getattr(claude_config, "mcp_config_path", ""),
                strict_mcp_config=getattr(claude_config, "strict_mcp_config", None),
                timeout_seconds=getattr(claude_config, "timeout_seconds", 120),
                stream_args=getattr(claude_config, "stream_args", None),
                generation_registry=generation_registry,
            )
        )

        default_adapter = "claude_cli_warm" if any(a.name == "claude_cli_warm" for a in adapters) else "claude_cli"
        if config_default in {a.name for a in adapters}:
            default_adapter = config_default
        return cls(adapters, default_adapter=default_adapter)

    @property
    def current_adapter_name(self) -> str:
        return self._current_adapter_name

    def adapter_names(self) -> list[str]:
        return sorted(self._adapters)

    def get_adapter(self, name: str | None = None) -> BrainAdapter:
        selected_name = name or self._current_adapter_name
        try:
            return self._adapters[selected_name]
        except KeyError as exc:
            raise BrainManagerError(f"Unknown brain adapter: {selected_name}") from exc

    def switch_adapter(self, name: str) -> None:
        self.get_adapter(name)
        self._current_adapter_name = name

    def generate(
        self,
        request: BrainRequest,
        adapter_name: str | None = None,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> BrainResponse:
        adapter = self.get_adapter(adapter_name)
        if on_delta is not None and getattr(adapter, "supports_streaming", False):
            return adapter.generate(request, on_delta=on_delta)
        return adapter.generate(request)

    def supports_streaming(self, adapter_name: str | None = None) -> bool:
        adapter = self.get_adapter(adapter_name)
        return getattr(adapter, "supports_streaming", False)


def _should_register_cli_adapter(config: object, default_adapter: str, adapter_name: str) -> bool:
    return bool(getattr(config, "enabled", False)) or default_adapter == adapter_name