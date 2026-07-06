"""Brain adapter selection for Jarvis v4.1."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from typing import Any

from jarvis.brain.base import BrainAdapter, BrainRequest, BrainResponse
from jarvis.brain.claude_cli_adapter import ClaudeCliAdapter
from jarvis.brain.claude_cli_warm_adapter import ClaudeCliWarmAdapter
from jarvis.brain.codex_cli_adapter import CodexCliAdapter
from jarvis.brain.mock_adapter import MockBrainAdapter


class BrainManagerError(Exception):
    """Raised when a brain adapter cannot be selected."""


class BrainManager:
    """Selects a stateless brain adapter without owning provider session state."""

    def __init__(self, adapters: Iterable[BrainAdapter], default_adapter: str = "mock") -> None:
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
        default_adapter = getattr(brain_config, "default_adapter", "mock")
        default_model = getattr(brain_config, "default_model", "mock-local")
        adapters: list[BrainAdapter] = [MockBrainAdapter(default_model=default_model)]

        claude_config = getattr(brain_config, "claude_cli", None)
        if _should_register_cli_adapter(claude_config, default_adapter, "claude_cli"):
            adapters.append(
                ClaudeCliAdapter(
                    command=getattr(claude_config, "command", "claude"),
                    args=getattr(claude_config, "args", ["-p"]),
                    model=getattr(claude_config, "model", ""),
                    effort=getattr(claude_config, "effort", ""),
                    permission_mode=getattr(claude_config, "permission_mode", ""),
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

        # Ciepły wariant (PROTOTYP): dzieli config [brain.claude_cli], dokłada
        # tryb strumienia i trzyma proces ciepły. Rejestrowany tylko gdy jawnie
        # wybrany (default_adapter = "claude_cli_warm").
        if default_adapter == "claude_cli_warm" and claude_config is not None:
            adapters.append(
                ClaudeCliWarmAdapter(
                    command=getattr(claude_config, "command", "claude"),
                    args=getattr(claude_config, "args", ["-p"]),
                    model=getattr(claude_config, "model", ""),
                    timeout_seconds=getattr(claude_config, "timeout_seconds", 120),
                    generation_registry=generation_registry,
                )
            )

        codex_config = getattr(brain_config, "codex_cli", None)
        if _should_register_cli_adapter(codex_config, default_adapter, "codex_cli"):
            adapters.append(
                CodexCliAdapter(
                    command=getattr(codex_config, "command", "codex"),
                    args=getattr(codex_config, "args", []),
                    model=getattr(codex_config, "model", ""),
                    timeout_seconds=getattr(codex_config, "timeout_seconds", 120),
                )
            )

        return cls(
            adapters,
            default_adapter=default_adapter,
        )

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
        if on_delta is not None and _accepts_on_delta(adapter):
            return adapter.generate(request, on_delta=on_delta)
        # G0 §2 degradation: an adapter without streaming gets the plain
        # call; the caller sentence-cuts the final text after the fact.
        return adapter.generate(request)

    def supports_streaming(self, adapter_name: str | None = None) -> bool:
        """Whether the selected adapter can accept on_delta callbacks."""

        adapter = self.get_adapter(adapter_name)
        return _accepts_on_delta(adapter)


def _accepts_on_delta(adapter: BrainAdapter) -> bool:
    """Feature detection instead of a TypeError probe: a signature check can
    never mask a real TypeError raised inside a streaming adapter."""

    try:
        parameters = inspect.signature(adapter.generate).parameters
    except (TypeError, ValueError):
        return False
    return "on_delta" in parameters


def _should_register_cli_adapter(config: object, default_adapter: str, adapter_name: str) -> bool:
    return bool(getattr(config, "enabled", False)) or default_adapter == adapter_name
