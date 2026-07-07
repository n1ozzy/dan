"""Shared speech text generation utilities for brain adapters.

This module provides a unified way to generate concise, natural-sounding
speech text from the full display response and tool calls. All adapters
should use this to ensure consistent TTS behavior.
"""

from __future__ import annotations

import re
from typing import Any


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