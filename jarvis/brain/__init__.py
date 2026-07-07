"""Stateless brain adapter interface and mock implementation."""

from __future__ import annotations

from jarvis.brain.base import (
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
from jarvis.brain.chain_adapter import ChainAdapter
from jarvis.brain.eco_brain_adapter import EcoBrainAdapter
from jarvis.brain.groq_adapter import GroqAdapter
from jarvis.brain.manager import BrainManager, BrainManagerError
from jarvis.brain.mock_adapter import MockBrainAdapter
from jarvis.brain.ollama_adapter import OllamaAdapter
from jarvis.brain.qwen_adapter import QwenAdapter

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
    "ChainAdapter",
    "EcoBrainAdapter",
]
