"""Chain adapter for Jarvis - composite (Claude CLI → Bielik/Ollama)."""

from __future__ import annotations

import os
from typing import Any, Callable

from jarvis.brain.base import BrainAdapter, BrainAdapterError, BrainRequest, BrainResponse, BrainUsage
from jarvis.brain.claude_cli_adapter import ClaudeCliAdapter
from jarvis.brain.ollama_adapter import OllamaAdapter


class ChainAdapter(BrainAdapter):
    name = "chain"
    default_model = "chain-claude-bielik"

    def __init__(
        self,
        claude_adapter: ClaudeCliAdapter | None = None,
        ollama_adapter: OllamaAdapter | None = None,
        claude_model: str = "sonnet",
        ollama_model: str = "bielik-11b-v2.3-instruct:Q4_K_M",
        timeout_seconds: float = 120.0,
        generation_registry: Any | None = None,
    ) -> None:
        self._claude = claude_adapter
        self._ollama = ollama_adapter
        self._claude_model = claude_model
        self._ollama_model = ollama_model
        self._timeout = timeout_seconds
        self._generation_registry = generation_registry

    @classmethod
    def from_config(cls, config: Any, generation_registry: Any | None = None) -> "ChainAdapter":
        brain_config = getattr(config, "brain", None)
        claude_config = getattr(brain_config, "claude_cli", None)
        ollama_config = getattr(brain_config, "ollama", None)
        chain_config = getattr(brain_config, "chain", None)

        claude_adapter = None
        if claude_config:
            claude_adapter = ClaudeCliAdapter(
                command=getattr(claude_config, "command", "claude"),
                args=getattr(claude_config, "args", ["-p"]),
                model=getattr(claude_config, "model", "sonnet"),
                effort=getattr(claude_config, "effort", "high"),
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

        ollama_adapter = None
        if ollama_config:
            ollama_adapter = OllamaAdapter(
                host=getattr(ollama_config, "host", "http://localhost:11434"),
                model=getattr(ollama_config, "model", "bielik-11b-v2.3-instruct:Q4_K_M"),
                timeout_seconds=getattr(ollama_config, "timeout_seconds", 120),
                keep_alive=getattr(ollama_config, "keep_alive", "10m"),
            )

        return cls(
            claude_adapter=claude_adapter,
            ollama_adapter=ollama_adapter,
            claude_model=getattr(chain_config, "claude_model", "sonnet"),
            ollama_model=getattr(chain_config, "ollama_model", "bielik-11b-v2.3-instruct:Q4_K_M"),
            timeout_seconds=getattr(chain_config, "timeout_seconds", 120),
            generation_registry=generation_registry,
        )

    def available_models(self) -> list[str]:
        models = []
        if self._claude:
            models.append(f"claude:{self._claude_model}")
        if self._ollama:
            models.append(f"ollama:{self._ollama_model}")
        return models if models else [self.default_model]

    async def generate(
        self,
        request: BrainRequest,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> BrainResponse:
        if not self._claude or not self._ollama:
            raise BrainAdapterError("Chain adapter requires both Claude CLI and Ollama adapters")

        # Step 1: Get roast from Claude
        claude_request = BrainRequest(
            turn_id=request.turn_id,
            conversation_id=request.conversation_id,
            input_text=request.input_text,
            context_messages=request.context_messages,
            memory_blocks=request.memory_blocks,
            available_tools=request.available_tools,
            settings={**request.settings, "model": self._claude_model},
            metadata=request.metadata,
        )

        claude_resp = await self._claude.generate(claude_request, on_delta=on_delta)
        roast = claude_resp.text.strip()

        if not roast:
            raise BrainAdapterError("Claude returned empty response for chain")

        # Step 2: Sharpen with Bielik
        sharpen_prompt = (
            "Oto celny, złośliwy tekst. Przepisz go tak, by uderzał MOCNIEJ i bardziej "
            "bezczelnie, ZACHOWUJĄC dokładnie ten sam sens, dowcip i czuły punkt w jaki celuje "
            "(to ma być ta sama szpila wbita głębiej, nie nowa). Dorzuć wulgarność tam gdzie "
            "wzmacnia cios, nie na siłę. Zwróć SAM przepisany tekst, bez komentarzy:\n\n" + roast
        )

        ollama_request = BrainRequest(
            turn_id=request.turn_id,
            conversation_id=request.conversation_id,
            input_text=sharpen_prompt,
            context_messages=[],
            memory_blocks=[],
            available_tools=[],
            settings={"model": self._ollama_model, "temperature": 0.9},
            metadata={},
        )

        try:
            ollama_resp = await self._ollama.generate(ollama_request, on_delta=on_delta)
            sharp = ollama_resp.text.strip()

            # Clean up Bielik preambles
            for pre in (
                "Oto przepisany tekst",
                "Oto poprawiona wersja",
                "Przepisany tekst",
                "Oto tekst",
                "Oto wersja",
            ):
                if sharp.lower().startswith(pre.lower()):
                    nl = sharp.find("\n")
                    sharp = sharp[nl + 1:].lstrip() if nl != -1 else sharp
                    break

            final_text = sharp if sharp else roast
        except Exception as exc:
            print(f"Chain: Bielik sharpen failed ({type(exc).__name__}: {exc}) — returning Claude roast")
            final_text = roast

        usage = BrainUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )

        return BrainResponse(
            text=final_text,
            speech_text=final_text,
            model=self.default_model,
            usage=usage,
            raw_metadata={
                "provider": "chain",
                "claude_model": self._claude_model,
                "ollama_model": self._ollama_model,
                "claude_raw": roast[:200],
            },
        )


def create_chain_adapter(config: Any, generation_registry: Any | None = None) -> "ChainAdapter":
    return ChainAdapter.from_config(config, generation_registry)