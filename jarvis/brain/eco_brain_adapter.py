"""Eco Brain adapter for Jarvis - integrates with local/self-hosted LLM endpoints."""

from __future__ import annotations

import os
from typing import Any, Callable

import httpx

from jarvis.brain.base import BrainAdapter, BrainAdapterError, BrainRequest, BrainResponse, BrainUsage


class EcoBrainAdapter(BrainAdapter):
    name = "eco_brain"
    default_model = "eco-brain-local"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str = "eco-brain",
        timeout_seconds: float = 120.0,
        generation_registry: Any | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("ECO_BRAIN_BASE_URL", "")).rstrip("/")
        if not self._base_url:
            raise BrainAdapterError("Eco Brain base_url not provided (ECO_BRAIN_BASE_URL env or config)")
        if not self._base_url.endswith("/chat/completions"):
            self._base_url = f"{self._base_url}/chat/completions"
        self._api_key = api_key or os.environ.get("ECO_BRAIN_API_KEY", "")
        self.default_model = model
        self._timeout = timeout_seconds
        self._generation_registry = generation_registry
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._cached_models: list[str] | None = None

    def available_models(self) -> list[str]:
        import asyncio
        return asyncio.run(self._fetch_models())

    async def _fetch_models(self) -> list[str]:
        if self._cached_models is not None:
            return self._cached_models
        try:
            # Try to fetch from /models endpoint
            models_url = self._base_url.replace("/chat/completions", "/models")
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            resp = await self._client.get(models_url, headers=headers)
            if resp.is_success:
                data = resp.json()
                models = [m.get("id", m.get("name", "")) for m in data.get("data", [])]
                if models:
                    self._cached_models = models
                    return models
        except Exception:
            pass
        self._cached_models = [self.default_model]
        return self._cached_models

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
            "max_tokens": request.settings.get("max_tokens", 4096),
        }

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        full_text = ""
        input_tokens = 0
        output_tokens = 0

        if on_delta is not None:
            async with self._client.stream("POST", self._base_url, json=payload, headers=headers) as resp:
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
            resp = await self._client.post(self._base_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            full_text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

        usage_obj = BrainUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        usage_obj.total_tokens = (usage_obj.input_tokens or 0) + (usage_obj.output_tokens or 0)

        return BrainResponse(
            text=full_text,
            speech_text=full_text,
            model=self.default_model,
            usage=usage_obj,
            raw_metadata={"provider": "eco_brain", "endpoint": self._base_url},
        )

    def _build_messages(self, request: BrainRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for block in request.memory_blocks:
            messages.append({"role": "system", "content": f"[{block.title}] {block.body}"})
        for msg in request.context_messages:
            messages.append({"role": msg.role, "content": msg.content})
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


def create_eco_brain_adapter(config: Any, generation_registry: Any | None = None) -> EcoBrainAdapter:
    brain_config = getattr(config, "brain", None)
    eco_config = getattr(brain_config, "eco_brain", None) or getattr(config, "eco_brain", None)
    base_url = getattr(eco_config, "base_url", "") or os.environ.get("ECO_BRAIN_BASE_URL", "")
    api_key = getattr(eco_config, "api_key", "") or os.environ.get("ECO_BRAIN_API_KEY", "")
    model = getattr(eco_config, "model", "eco-brain")
    timeout = getattr(eco_config, "timeout_seconds", 120)
    return EcoBrainAdapter(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout,
        generation_registry=generation_registry,
    )