"""Brain adapter selection for Jarvis v4.1."""

from __future__ import annotations

from collections.abc import Iterable

from jarvis.brain.base import BrainAdapter, BrainRequest, BrainResponse
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
    def from_config(cls, config: object) -> "BrainManager":
        brain_config = getattr(config, "brain", None)
        default_adapter = getattr(brain_config, "default_adapter", "mock")
        default_model = getattr(brain_config, "default_model", "mock-local")
        return cls(
            [MockBrainAdapter(default_model=default_model)],
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

    def generate(self, request: BrainRequest, adapter_name: str | None = None) -> BrainResponse:
        adapter = self.get_adapter(adapter_name)
        return adapter.generate(request)
