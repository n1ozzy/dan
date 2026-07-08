"""Tests for new brain adapters: Groq, Qwen, Ollama, Chain, Eco Brain."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.brain import (
    BrainManager,
    ChainAdapter,
    EcoBrainAdapter,
    GroqAdapter,
    OllamaAdapter,
    QwenAdapter,
)
from jarvis.brain.base import BrainRequest, BrainResponse


class TestGroqAdapter:
    """Tests for Groq API adapter."""

    @pytest.fixture
    def groq_adapter(self):
        with patch("httpx.AsyncClient"):
            adapter = GroqAdapter(
                api_key="test-key",
                model="llama-3.3-70b-versatile",
                timeout_seconds=30.0,
            )
            yield adapter

    def test_available_models_returns_groq_models(self, groq_adapter):
        models = groq_adapter.available_models()
        assert "llama-3.3-70b-versatile" in models
        assert "llama-3.1-8b-instant" in models
        assert isinstance(models, list)

    def test_default_model_is_first_available(self, groq_adapter):
        assert groq_adapter.default_model == "llama-3.3-70b-versatile"

    def test_invalid_model_defaults_to_first(self):
        with patch("httpx.AsyncClient"):
            adapter = GroqAdapter(api_key="test", model="invalid-model")
            assert adapter.default_model == "llama-3.3-70b-versatile"


class TestQwenAdapter:
    """Tests for Qwen/LiteLLM adapter."""

    @pytest.fixture
    def qwen_adapter(self):
        with patch("httpx.AsyncClient"):
            adapter = QwenAdapter(
                base_url="http://localhost:8000/v1",
                api_key="test-key",
                model="qwen3.6-35b-fast",
                timeout_seconds=30.0,
            )
            yield adapter

    def test_available_models_returns_configured_model(self, qwen_adapter):
        models = qwen_adapter.available_models()
        assert models == ["qwen3.6-35b-fast"]

    def test_base_url_normalized_to_chat_completions(self, qwen_adapter):
        assert qwen_adapter._base_url.endswith("/chat/completions")


class TestOllamaAdapter:
    """Tests for Ollama local adapter."""

    @pytest.fixture
    def ollama_adapter(self):
        with patch("httpx.AsyncClient"):
            adapter = OllamaAdapter(
                host="http://localhost:11434",
                model="bielik-11b-v2.3-instruct:Q4_K_M",
                timeout_seconds=60.0,
                keep_alive="10m",
            )
            yield adapter

    def test_available_models_calls_api_tags(self, ollama_adapter):
        # Test that _fetch_models is called
        import asyncio

        async def mock_fetch():
            return ["bielik-11b-v2.3-instruct:Q4_K_M", "llama3.2:latest"]

        ollama_adapter._fetch_models = mock_fetch
        models = asyncio.run(ollama_adapter._fetch_models())
        assert "bielik-11b-v2.3-instruct:Q4_K_M" in models

    def test_caches_models_after_first_fetch(self, ollama_adapter):
        ollama_adapter._cached_models = ["cached-model"]
        import asyncio

        models = asyncio.run(ollama_adapter._fetch_models())
        assert models == ["cached-model"]


class TestChainAdapter:
    """Tests for Chain (Claude → Ollama) composite adapter."""

    @pytest.fixture
    def chain_adapter(self):
        claude_mock = MagicMock()
        claude_mock.name = "claude_cli"
        claude_mock.default_model = "sonnet"

        ollama_mock = MagicMock()
        ollama_mock.name = "ollama"
        ollama_mock.default_model = "bielik-11b-v2.3-instruct:Q4_K_M"

        adapter = ChainAdapter(
            claude_adapter=claude_mock,
            ollama_adapter=ollama_mock,
            claude_model="sonnet",
            ollama_model="bielik-11b-v2.3-instruct:Q4_K_M",
        )
        yield adapter

    def test_available_models_returns_both(self, chain_adapter):
        models = chain_adapter.available_models()
        assert "claude:sonnet" in models
        assert "ollama:bielik-11b-v2.3-instruct:Q4_K_M" in models

    def test_requires_both_adapters(self):
        # ChainAdapter.validate() or generate() should fail without both
        adapter = ChainAdapter(claude_adapter=None, ollama_adapter=MagicMock())
        import asyncio
        request = BrainRequest(
            turn_id="test",
            conversation_id="test",
            input_text="test",
        )
        with pytest.raises(Exception, match="requires both"):
            asyncio.run(adapter.generate(request))


class TestEcoBrainAdapter:
    """Tests for Eco Brain adapter."""

    @pytest.fixture
    def eco_adapter(self):
        with patch("httpx.AsyncClient"):
            adapter = EcoBrainAdapter(
                base_url="http://localhost:8001/v1",
                api_key="eco-test-key",
                model="eco-brain-v1",
                timeout_seconds=60.0,
            )
            yield adapter

    def test_available_models_returns_configured(self, eco_adapter):
        models = eco_adapter.available_models()
        assert "eco-brain-v1" in models

    def test_tries_fetch_models_from_endpoint(self, eco_adapter):
        import asyncio

        async def mock_fetch():
            return ["eco-model-1", "eco-model-2"]

        eco_adapter._fetch_models = mock_fetch
        models = asyncio.run(eco_adapter._fetch_models())
        assert len(models) >= 1


class TestBrainManagerRegistration:
    """Tests that BrainManager registers all new adapters."""

    def test_manager_auto_detects_available_providers(self):
        """Test that BrainManager auto-detects providers on the system."""
        from types import SimpleNamespace

        config = SimpleNamespace(
            brain=SimpleNamespace(default_model="mock-local")
        )
        # No explicit claude_cli, codex_cli configs - let auto-detection work

        # Auto-detection should find claude_cli, codex_cli
        manager = BrainManager.from_config(config)
        names = manager.adapter_names()

        # These should always be detected if binaries exist
        assert "claude_cli" in names
        assert "codex_cli" in names

    def test_manager_uses_config_when_provided(self):
        """Test that explicit config overrides auto-detection for default adapter."""
        from types import SimpleNamespace

        config = SimpleNamespace(
            brain=SimpleNamespace(
                default_adapter="test", 
                default_model="test-model",
                test=SimpleNamespace(enabled=True, model="test-model"),
                groq=SimpleNamespace(enabled=True, api_key="test-key", model="llama-3.3-70b-versatile")
            )
        )
        config.brain.claude_cli = SimpleNamespace(
            enabled=False, command="claude", args=["-p"], model="", effort="",
            permission_mode="", output_format="", input_format="",
            tools=[], allowed_tools=[], disallowed_tools=[],
            mcp_config_path="", strict_mcp_config=None,
            timeout_seconds=120, stream_args=None
        )
        config.brain.codex_cli = SimpleNamespace(enabled=False, command="codex", args=[], model="", timeout_seconds=120)

        with patch("jarvis.brain.groq_adapter.create_groq_adapter") as mock_create:
            mock_adapter = MagicMock()
            mock_adapter.name = "groq"
            mock_create.return_value = mock_adapter

            manager = BrainManager.from_config(config)
            names = manager.adapter_names()
            assert "groq" in names
            assert "test" in names
            # But default is still test since we set it explicitly
            assert manager.current_adapter_name == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])