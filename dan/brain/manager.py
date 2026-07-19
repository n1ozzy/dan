"""Brain adapter selection for DAN v4.1 - Production ready."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from dan.brain.base import BrainAdapter, BrainRequest, BrainResponse
from dan.brain.claude_cli_adapter import ClaudeCliAdapter
from dan.brain.groq_adapter import GroqAdapter, create_groq_adapter


class BrainManagerError(Exception):
    """Raised when a brain adapter cannot be selected."""


class BrainManager:
    """Own the single configured brain adapter and its daemon lifecycle."""

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
        cls,
        config: object,
        *,
        generation_registry: Any | None = None,
        state_path: Path | str | None = None,
    ) -> "BrainManager":
        brain_config = getattr(config, "brain", None)
        adapters: list[BrainAdapter] = []

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
                state_path=state_path,
                context_window_tokens=getattr(brain_config, "context_window_tokens", 200_000),
                checkpoint_percent=getattr(brain_config, "context_checkpoint_percent", 70.0),
                compact_percent=getattr(brain_config, "context_compact_percent", 80.0),
                recycle_percent=getattr(brain_config, "context_recycle_percent", 90.0),
            )
        )

        # Groq adapter (streaming, fast)
        groq_config = getattr(brain_config, "groq", None)
        if groq_config is not None:
            adapters.append(
                create_groq_adapter(config, generation_registry=generation_registry)
            )

        return cls(adapters, default_adapter="claude_cli")

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

    def session_snapshot(self, adapter_name: str | None = None) -> dict[str, Any]:
        adapter = self.get_adapter(adapter_name)
        snapshot = getattr(adapter, "session_snapshot", None)
        return dict(snapshot()) if callable(snapshot) else {}

    def close(self) -> None:
        for adapter in self._adapters.values():
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    def start(self) -> None:
        for adapter in self._adapters.values():
            start = getattr(adapter, "start", None)
            if callable(start):
                start()
