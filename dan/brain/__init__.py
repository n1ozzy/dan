"""Stateless brain adapter interface and mock implementation."""

from __future__ import annotations

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
from dan.brain.eco_brain_adapter import EcoBrainAdapter
from dan.brain.groq_adapter import GroqAdapter
from dan.brain.manager import BrainManager, BrainManagerError
from dan.brain.mock_adapter import MockBrainAdapter
from dan.brain.ollama_adapter import OllamaAdapter
from dan.brain.qwen_adapter import QwenAdapter

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
    "GroqAdapter",
    "QwenAdapter",
    "OllamaAdapter",
    "EcoBrainAdapter",
]
