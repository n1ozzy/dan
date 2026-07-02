"""Deterministic sentence chunker (G3, VOICE_STREAMING.md §3–§4).

Deltas in, sentence chunks out. Lives in jarvisd — adapters stay dumb pipes
and the broker only plays what is queued. Tool-call blocks hold emission
fail-closed: from the first character that could open `<jarvis_tool_call>`
nothing is emitted until the suspicion resolves, and a completed block is
never spoken.
"""

from __future__ import annotations


TOOL_CALL_OPEN = "<jarvis_tool_call>"
TOOL_CALL_CLOSE = "</jarvis_tool_call>"
DEFAULT_MIN_CHARS = 12
SENTENCE_TERMINATORS = (".", "!", "?", "…")

# Dotted tokens that are not sentence ends. Data, not grammar: extend the
# list when a new abbreviation misfires in practice.
ABBREVIATIONS = (
    "np.",
    "tzn.",
    "itd.",
    "itp.",
    "tj.",
    "dr.",
    "mgr.",
    "inż.",
    "mr.",
    "mrs.",
    "e.g.",
    "i.e.",
    "etc.",
)


class SentenceChunker:
    def __init__(self, *, min_chars: int = DEFAULT_MIN_CHARS) -> None:
        self._min_chars = int(min_chars)
        self._buffer = ""
        self._pending = ""  # accumulated text below min_chars

    def feed(self, delta: str) -> list[str]:
        if not isinstance(delta, str) or not delta:
            return []
        self._buffer += delta
        return self._drain()

    def flush(self) -> list[str]:
        chunks = self._drain(final=True)
        # Whatever remains is either an unresolved tool-call suspicion
        # (held fail-closed, never spoken) or plain tail text.
        tail = self._buffer
        self._buffer = ""
        if not self._suspicious(tail):
            remainder = (self._pending + tail).strip()
            self._pending = ""
            if remainder:
                chunks.append(remainder)
        else:
            pending = self._pending.strip()
            self._pending = ""
            if pending:
                chunks.append(pending)
        return chunks

    # -- internals ---------------------------------------------------------

    def _drain(self, *, final: bool = False) -> list[str]:
        chunks: list[str] = []
        while True:
            self._strip_complete_tool_calls()
            emitted, remainder = self._next_sentence(self._buffer)
            if emitted is None:
                break
            self._buffer = remainder
            candidate = (self._pending + " " + emitted).strip() if self._pending else emitted
            if len(candidate) < self._min_chars:
                self._pending = candidate
                continue
            self._pending = ""
            chunks.append(candidate)
        return chunks

    def _strip_complete_tool_calls(self) -> None:
        while True:
            start = self._buffer.find(TOOL_CALL_OPEN)
            if start < 0:
                return
            end = self._buffer.find(TOOL_CALL_CLOSE, start)
            if end < 0:
                return
            self._buffer = (
                self._buffer[:start] + " " + self._buffer[end + len(TOOL_CALL_CLOSE) :]
            )

    def _next_sentence(self, text: str) -> tuple[str | None, str]:
        """Find the earliest safe cut point before any tool-call suspicion."""

        limit = self._suspicion_index(text)
        index = 0
        while index < limit:
            char = text[index]
            if char == "\n":
                sentence = text[:index].strip()
                if sentence:
                    return sentence, text[index + 1 :]
                # Blank line: consume it and keep scanning the rest. (This
                # branch once returned bare None — a streamed "Jasne:\n\n…"
                # then crashed _drain and muted the rest of the turn.)
                return self._next_sentence(text[index + 1 :])
            if char in SENTENCE_TERMINATORS:
                end = index + 1
                # consume runs like "?!" or "..."
                while end < limit and text[end] in SENTENCE_TERMINATORS:
                    end += 1
                after_ok = end >= len(text) or text[end].isspace()
                if after_ok and not self._ends_with_abbreviation(text[:end]):
                    if end < len(text) or limit == len(text):
                        # A terminator at the very end of the buffer is only a
                        # cut when no more text can arrive before it (callers
                        # pass complete buffers to flush()).
                        if end < len(text):
                            sentence = text[:end].strip()
                            if sentence:
                                return sentence, text[end:].lstrip()
                index = end
                continue
            index += 1
        return None, text

    def _suspicion_index(self, text: str) -> int:
        """Index from which the buffer tail could open a tool-call block."""

        start = text.find(TOOL_CALL_OPEN)
        if start >= 0:
            return start
        # A trailing prefix of the opening tag is suspicious as well.
        max_prefix = min(len(TOOL_CALL_OPEN) - 1, len(text))
        for length in range(max_prefix, 0, -1):
            if text.endswith(TOOL_CALL_OPEN[:length]):
                return len(text) - length
        return len(text)

    def _suspicious(self, text: str) -> bool:
        return self._suspicion_index(text) < len(text)

    @staticmethod
    def _ends_with_abbreviation(text: str) -> bool:
        lowered = text.rstrip().lower()
        return any(lowered.endswith(abbr) for abbr in ABBREVIATIONS)


__all__ = ["ABBREVIATIONS", "SentenceChunker", "TOOL_CALL_CLOSE", "TOOL_CALL_OPEN"]
