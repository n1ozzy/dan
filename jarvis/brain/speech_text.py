"""Shared speech text generation utilities for brain adapters.

This module provides a unified way to generate concise, natural-sounding
speech text from the full display response and tool calls. All adapters
should use this to ensure consistent TTS behavior.
"""

from __future__ import annotations

import re
from typing import Any

from jarvis.security.redaction import redact_secret_text

# The model marks a listen-friendly rendering of its answer with this block.
# The chat keeps the rich text; only the inner text is sent to TTS.
_SPEECH_BLOCK = re.compile(r"\[\[GŁOS\]\](.*?)\[\[/GŁOS\]\]", re.DOTALL)
_TOOL_BLOCK = re.compile(
    r"<(?:jarvis_)?tool_(?:call|result)\b[^>]*>.*?</(?:jarvis_)?tool_(?:call|result)>",
    re.DOTALL | re.IGNORECASE,
)


def split_display_and_speech(text: str) -> tuple[str, str | None]:
    """Split a model answer into ``(display_text, speech_text)``.

    The model appends a ``[[GŁOS]]…[[/GŁOS]]`` block holding a short, natural
    form for listening. That block is stripped from the display (so the chat
    shows only the rich answer) and its inner text — whitespace collapsed —
    becomes the spoken form. No block, or an empty one, yields
    ``(text, None)`` so callers fall back to their own rendering.
    """

    match = _SPEECH_BLOCK.search(text)
    if match is None:
        return text, None
    speech = match.group(1).strip()
    display = _SPEECH_BLOCK.sub("", text)
    # The removed block leaves blank lines behind; collapse them.
    display = re.sub(r"\n{3,}", "\n\n", display).strip()
    return display, (speech or None)


def resolve_display_and_speech(text: str, tool_calls: list[Any]) -> tuple[str, str]:
    """Return ``(display_text, speech_text)`` for a model answer.

    Prefers the model's own redacted ``[[GŁOS]]`` form (natural, listen-ready);
    falls back to the derived strip when the model did not provide one. The
    display text always has the block removed.
    """

    display, model_speech = split_display_and_speech(text)
    speech = (
        _prepare_speech_text(model_speech)
        if model_speech is not None
        else generate_speech_text(display, tool_calls)
    )
    return display, speech


def _prepare_speech_text(text: str) -> str:
    """Strip Jarvis protocol blocks and redact secrets without rewriting tone."""

    without_protocol = _TOOL_BLOCK.sub("", str(text or ""))
    return redact_secret_text(without_protocol)


def generate_speech_text(text: str, tool_calls: list[Any]) -> str:
    """Use the model answer when it omitted ``[[GŁOS]]``.

    Args:
        text: The full display text (may contain markdown, code blocks, etc.)
        tool_calls: List of tool calls (dict or object with 'name' attribute/key)

    Returns:
        The model-authored text with protocol blocks removed and secrets redacted.
    """
    # Tool execution must not replace the model's actual spoken answer.
    del tool_calls
    return _prepare_speech_text(text)


def extract_tool_names(tool_calls: list[Any]) -> list[str]:
    """Extract tool names from a list of tool calls (dict or object)."""
    names = []
    for tc in tool_calls:
        if hasattr(tc, 'name'):
            names.append(tc.name)
        elif isinstance(tc, dict) and 'name' in tc:
            names.append(tc['name'])
    return names
