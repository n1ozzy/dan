"""Sync wrapper for async brain adapters."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from jarvis.brain.base import BrainAdapter, BrainRequest, BrainResponse, BrainGenerationCancelled


class SyncAdapterWrapper(BrainAdapter):
    """Wrap an async BrainAdapter to provide a sync generate() method."""

    def __init__(self, async_adapter: Any) -> None:
        self._adapter = async_adapter
        self.name = async_adapter.name
        self.default_model = async_adapter.default_model
        self.supports_streaming = getattr(async_adapter, "supports_streaming", False)

    def available_models(self) -> list[str]:
        return self._adapter.available_models()

    def generate(
        self,
        request: BrainRequest,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> BrainResponse:
        try:
            # Check if there's already a running event loop
            try:
                loop = asyncio.get_running_loop()
                # If we're in an async context, we can't use run_until_complete
                # Create a new thread with its own event loop
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run, self._adapter.generate(request, on_delta=on_delta)
                    )
                    return future.result()
            except RuntimeError:
                # No running loop, safe to use asyncio.run
                return asyncio.run(self._adapter.generate(request, on_delta=on_delta))
        except BrainGenerationCancelled:
            raise
        except Exception as exc:
            # Re-raise as BrainAdapterError to maintain contract
            from jarvis.brain.base import BrainAdapterError
            raise BrainAdapterError(f"Async adapter failed: {exc}") from exc


def wrap_async_adapter(adapter: Any) -> BrainAdapter:
    """Wrap an async adapter if needed, otherwise return as-is."""
    if hasattr(adapter, 'generate') and asyncio.iscoroutinefunction(adapter.generate):
        return SyncAdapterWrapper(adapter)
    return adapter