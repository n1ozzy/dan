"""Groq API brain adapter for Jarvis."""

from __future__ import annotations

import os
from typing import Any, Callable

import httpx

from jarvis.brain.base import (
    BrainAdapter,
    BrainAdapterError,
    BrainRequest,
    BrainResponse,
    BrainUsage,
)


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]


class GroqAdapter(BrainAdapter):
    name = "groq"
    supports_streaming = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "llama-3.3-70b-versatile",
        timeout_seconds: float = 60.0,
        generation_registry: Any | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self._api_key:
            raise BrainAdapterError("Groq API key not provided (GROQ_API_KEY env or config)")
        self.default_model = model if model in GROQ_MODELS else GROQ_MODELS[0]
        self._timeout = timeout_seconds
        self._generation_registry = generation_registry
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    def available_models(self) -> list[str]:
        return list(GROQ_MODELS)

    async def generate(
        self,
        request: BrainRequest,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> BrainResponse:
        messages = self._build_messages(request)
        payload = {
            "model": self.default_model,
            "messages": messages,
            "stream": on_delta is not None,
            "temperature": request.settings.get("temperature", 0.7),
            "max_tokens": request.settings.get("max_tokens", 2048),
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        full_text = ""
        input_tokens = 0
        output_tokens = 0

        if on_delta is not None:
            async with self._client.stream("POST", GROQ_ENDPOINT, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        import json
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_text += content
                            on_delta(content)
                        usage = chunk.get("usage")
                        if usage:
                            input_tokens = usage.get("prompt_tokens", 0)
                            output_tokens = usage.get("completion_tokens", 0)
                    except Exception:
                        continue
        else:
            resp = await self._client.post(GROQ_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            full_text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

        usage = BrainUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        usage.total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)

        return BrainResponse(
            text=full_text,
            speech_text=full_text,
            model=self.default_model,
            usage=usage,
            raw_metadata={"provider": "groq"},
        )

    def _build_messages(self, request: BrainRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for msg in request.context_messages:
            messages.append({"role": msg.role, "content": msg.content})
        for block in request.memory_blocks:
            messages.append({"role": "system", "content": f"[{block.title}] {block.body}"})
        messages.append({"role": "user", "content": request.input_text})
        return messages

    async def close(self) -> None:
        await self._client.aclose()

    def __del__(self) -> None:
        try:
            import asyncio
            if not self._client.is_closed:
                asyncio.create_task(self._client.aclose())
        except Exception:
            pass


def create_groq_adapter(config: Any, generation_registry: Any | None = None) -> GroqAdapter:
    brain_config = getattr(config, "brain", None)
    groq_config = getattr(brain_config, "groq", None) or getattr(config, "groq", None)
    api_key = getattr(groq_config, "api_key", "") or os.environ.get("GROQ_API_KEY", "")
    model = getattr(groq_config, "model", "") or getattr(brain_config, "default_model", "")
    timeout = getattr(groq_config, "timeout_seconds", 60)
    return GroqAdapter(api_key=api_key, model=model, timeout_seconds=timeout, generation_registry=generation_registry)