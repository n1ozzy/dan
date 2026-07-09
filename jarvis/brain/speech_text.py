"""Shared speech text generation utilities for brain adapters.

This module provides a unified way to generate concise, natural-sounding
speech text from the full display response and tool calls. All adapters
should use this to ensure consistent TTS behavior.
"""

from __future__ import annotations

import re
from typing import Any

# The model marks a listen-friendly rendering of its answer with this block.
# The chat keeps the rich text; only the inner text is sent to TTS.
_SPEECH_BLOCK = re.compile(r"\[\[GŁOS\]\](.*?)\[\[/GŁOS\]\]", re.DOTALL)


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
    speech = " ".join(match.group(1).split())
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

    display, redacted = split_display_and_speech(text)
    speech = redacted or generate_speech_text(display, tool_calls)
    return display, speech


def generate_speech_text(text: str, tool_calls: list[Any]) -> str:
    """Generate a concise speech version from display text and tool calls.

    Args:
        text: The full display text (may contain markdown, code blocks, etc.)
        tool_calls: List of tool calls (dict or object with 'name' attribute/key)

    Returns:
        A short, natural Polish sentence suitable for TTS.
    """
    # If there are tool calls, announce what we're doing
    if tool_calls:
        names = []
        for tc in tool_calls:
            if hasattr(tc, 'name'):
                names.append(tc.name)
            elif isinstance(tc, dict) and 'name' in tc:
                names.append(tc['name'])
        if len(names) == 1:
            return f"Używam narzędzia {names[0]}."
        return f"Uruchamiam narzędzia: {', '.join(names)}."

    # Strip markdown and keep it short
    speech = text
    # Remove code blocks
    speech = re.sub(r'```[\s\S]*?```', '[kod]', speech)
    # Remove inline code
    speech = re.sub(r'`[^`]+`', '', speech)
    # Remove markdown formatting
    speech = re.sub(r'[#*_~`]', '', speech)
    # Collapse whitespace
    speech = ' '.join(speech.split())
    # Truncate
    if len(speech) > 200:
        speech = speech[:197] + '...'
    return speech if speech else 'Gotowe.'


def extract_tool_names(tool_calls: list[Any]) -> list[str]:
    """Extract tool names from a list of tool calls (dict or object)."""
    names = []
    for tc in tool_calls:
        if hasattr(tc, 'name'):
            names.append(tc.name)
        elif isinstance(tc, dict) and 'name' in tc:
            names.append(tc['name'])
    return names