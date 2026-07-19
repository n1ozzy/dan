"""Stateless brain adapter interface and mock implementation."""

from __future__ import annotations

from importlib import import_module

from dan.brain.base import (
    BrainAdapter,
    BrainAdapterError,
    BrainGenerationCancelled,
    BrainMemoryBlock,
    BrainMessage,
    BrainRequest,
    BrainResponse,
    BrainToolCall,
    BrainToolSpec,
    BrainUsage,
)
from dan.brain.manager import BrainManager, BrainManagerError
from dan.brain.mock_adapter import MockBrainAdapter

_OPTIONAL_ADAPTER_MODULES = {
    "QwenAdapter": "dan.brain.qwen_adapter",
    "OllamaAdapter": "dan.brain.ollama_adapter",
    "EcoBrainAdapter": "dan.brain.eco_brain_adapter",
}

__all__ = [
    "BrainAdapter",
    "BrainAdapterError",
    "BrainGenerationCancelled",
    "BrainMemoryBlock",
    "BrainMessage",
    "BrainRequest",
    "BrainResponse",
    "BrainToolCall",
    "BrainToolSpec",
    "BrainUsage",
    "BrainManager",
    "BrainManagerError",
    "MockBrainAdapter",
    "QwenAdapter",
    "OllamaAdapter",
    "EcoBrainAdapter",
]


def __getattr__(name: str):
    module_name = _OPTIONAL_ADAPTER_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_name), name)
