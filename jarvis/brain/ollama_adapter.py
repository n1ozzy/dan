"""Ollama local LLM brain adapter for Jarvis."""

from __future__ import annotations

import os
from typing import Any

import httpx

from jarvis.brain.base import BrainAdapter, BrainAdapterError, BrainRequest, BrainResponse, BrainUsage


OLLAMA_DEFAULT_HOST = "http://localhost:11434"
OLLAMA_API_CHAT = "/api/chat"
OLLAMA_API_TAGS = "/api/tags"


class OllamaAdapter:
    name = "ollama"
    default_model = "bielik-11b-v2.3-instruct:Q4_K_M"

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120.0,
        keep_alive: str = "10m",
    ) -> None:
        self.host = (host or os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)).rstrip("/")
        self.default_model = (model or self.default_model).strip()
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self._client: httpx.AsyncClient | None = None
        self._cached_models: list[str] | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def _close_client(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def available_models(self) -> list[str]:
        import asyncio
        return asyncio.run(self._fetch_models())

    async def _fetch_models(self) -> list[str]:
        if self._cached_models is not None:
            return self._cached_models
        client = self._get_client()
        try:
            resp = await client.get(f"{self.host}{OLLAMA_API_TAGS}")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            self._cached_models = models
            return models
        except Exception:
            self._cached_models = []
            return []

    def generate(self, request: BrainRequest) -> BrainResponse:
        import asyncio
        return asyncio.run(self._generate_async(request))

    async def _generate_async(self, request: BrainRequest) -> BrainResponse:
        model = request.settings.get("model", self.default_model)
        messages = self._build_messages(request)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": request.settings.get("temperature", 0.7),
                "num_predict": request.settings.get("max_tokens", 4096),
                "num_ctx": request.settings.get("num_ctx", 4096),
            },
        }

        client = self._get_client()
        try:
            resp = await client.post(f"{self.host}{OLLAMA_API_CHAT}", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise BrainAdapterError(f"Ollama API error: {exc.response.status_code} {exc.response.text}") from exc
        except Exception as exc:
            raise BrainAdapterError(f"Ollama request failed: {exc}") from exc

        message = data.get("message", {})
        text = message.get("content", "").strip()

        usage = BrainUsage(
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            total_tokens=(data.get("prompt_eval_count", 0) + data.get("eval_count", 0)),
        )

        return BrainResponse(
            text=text,
            speech_text=text,
            model=model,
            usage=usage,
            raw_metadata={"provider": "ollama", "done": data.get("done")},
        )

    def _build_messages(self, request: BrainRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for block in request.memory_blocks:
            messages.append({"role": "system", "content": f"[{block.title}] {block.body}"})
        for msg in request.context_messages:
            role = msg.role if msg.role in ("user", "assistant", "system") else "user"
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": "user", "content": request.input_text})
        return messages


def create_ollama_adapter(config: Any, generation_registry: Any | None = None) -> OllamaAdapter:
    brain_config = getattr(config, "brain", None)
    ollama_config = getattr(brain_config, "ollama", None) or getattr(config, "ollama", None)
    host = getattr(ollama_config, "host", "") or getattr(brain_config, "ollama_host", "") or OLLAMA_DEFAULT_HOST
    model = getattr(ollama_config, "model", "") or getattr(brain_config, "default_model", "")
    timeout = getattr(ollama_config, "timeout_seconds", 120)
    keep_alive = getattr(ollama_config, "keep_alive", "10m")
    return OllamaAdapter(host=host, model=model, timeout_seconds=timeout, keep_alive=keep_alive)