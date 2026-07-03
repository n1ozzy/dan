"""Warm (persistent-process) Claude CLI brain adapter — PROTOTYP.

Trzyma JEDEN długo żyjący `claude -p --input-format stream-json` i podaje mu
kolejne tury przez otwarty stdin, zamiast spawnować proces co turę. Zmierzona
latencja: ~5,3 s (spawn co turę) -> ~2,1 s (ciepły). Kontekstu CLI nie
zachowuje (każda wiadomość = osobna sesja init) — bez znaczenia, bo Jarvis
sam składa kontekst w `format_cli_prompt`.

⚠️ PROTOTYP (świadomie bez pełnego hardeningu): brak timeoutu na blokujący
readline (jeśli claude zawiesi się bez `result`, tura wisi), brak drenażu
stderr, minimalne zarządzanie cyklem życia. Dowód latencji, nie produkcja.
Osobny plik obok `claude_cli_adapter.py` — celowo, żeby NIE kolidować z
FIX-07 (stdin deadlock) robionym równolegle w tamtym pliku. Docelowa wersja:
osobny task z TDD + lifecycle (health, timeout, restart-with-backoff).

Barge-in (FIX-09): choć proces jest współdzielony między turami, cancel musi
przerwać TYLKO bieżącą generację i NIE zostawić persistent procesu w złym
stanie. Realizacja = zabij + zrecykluj sesję: handle cancela z rejestru
sygnalizuje bieżący proces (odblokowuje wiszący readline), a odczyt kończy się
`BrainGenerationCancelled` (nie generyczny błąd) — więc orchestrator znaczy
turę CANCELLED, nie FAILED. Następny `generate` startuje świeży proces
(`_ensure_proc`). Handle NIE bierze `self._lock` (generate trzyma go przez całą
turę), więc sygnalizacja z wątku barge-in nie zakleszcza się.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Sequence
from typing import Any, Callable

from jarvis.brain.base import (
    BrainAdapterError,
    BrainGenerationCancelled,
    BrainRequest,
    BrainResponse,
)
from jarvis.brain.claude_cli_adapter import (
    _signal_process,
    _StreamJsonParser,
    format_cli_prompt,
)
from jarvis.brain.tool_call_parser import parse_tool_call_blocks
from jarvis.logging import get_logger

logger = get_logger(__name__)

# Doklejane do bazowych args (config [brain.claude_cli].args) — włączają tryb
# strumienia wejścia/wyjścia, w którym proces przyjmuje kolejne wiadomości.
_WARM_STREAM_ARGS = (
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
)


def _default_warm_process_factory(command: list[str]) -> subprocess.Popen[str]:
    """Spawn the persistent CLI in its own session so a barge-in kill takes the
    whole tree — the warm process is a long-lived node CLI that may fork, and an
    orphan must not survive the cancel holding the pipes (mirrors the streaming
    factory's rationale)."""

    return subprocess.Popen(  # noqa: S603 - fixed command from config
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        start_new_session=True,
    )


class ClaudeCliWarmAdapter:
    name = "claude_cli_warm"

    def __init__(
        self,
        *,
        command: str = "claude",
        args: Sequence[str] | None = None,
        model: str = "",
        timeout_seconds: float = 120,
        process_factory: Callable[[list[str]], Any] | None = None,
        generation_registry: Any | None = None,
    ) -> None:
        base = list(args) if args else ["-p"]
        if "-p" not in base and "--print" not in base:
            base = ["-p", *base]
        self._command = [command, *base, *_WARM_STREAM_ARGS]
        self.default_model = model.strip() or "claude-cli-warm"
        self.timeout_seconds = float(timeout_seconds)
        self._proc: Any | None = None
        self._lock = threading.Lock()
        self._process_factory = process_factory or _default_warm_process_factory
        self._generation_registry = generation_registry

    def available_models(self) -> list[str]:
        return [self.default_model]

    # -- process lifecycle (minimal) --------------------------------------

    def _ensure_proc(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        logger.info("warm claude: spawning persistent process")
        self._proc = self._process_factory(self._command)

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for closer in (lambda: proc.stdin and proc.stdin.close(), proc.terminate):
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        with self._lock:
            self._kill()

    # -- generation --------------------------------------------------------

    def generate(
        self,
        request: BrainRequest,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> BrainResponse:
        with self._lock:
            self._ensure_proc()
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            prompt = format_cli_prompt(request)
            message = json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    },
                }
            )
            parser = _StreamJsonParser(on_delta or (lambda _text: None))

            cancelled = threading.Event()

            def _cancel() -> None:
                # Barge-in leg 1 (§7): terminate THIS turn's process so the
                # blocked readline below unblocks with EOF; the flag lets that
                # EOF be told apart from a real crash. No self._lock here —
                # generate holds it for the whole turn, so taking it would
                # deadlock the cancelling thread.
                cancelled.set()
                _signal_process(proc, force=False)

            registered = bool(
                self._generation_registry is not None and request.turn_id
            )
            if registered:
                self._generation_registry.register(request.turn_id, _cancel)

            try:
                try:
                    proc.stdin.write(message + "\n")
                    proc.stdin.flush()
                    while True:
                        line = proc.stdout.readline()
                        if not line:  # EOF: cancelled, or the process just died
                            self._kill()
                            if cancelled.is_set():
                                raise BrainGenerationCancelled(
                                    "warm claude generation cancelled (barge-in)"
                                )
                            raise BrainAdapterError(
                                "warm claude closed stdout (process died)"
                            )
                        parser.feed_line(line)
                        try:
                            if json.loads(line).get("type") == "result":
                                break
                        except ValueError:
                            pass
                except (BrokenPipeError, OSError) as exc:
                    self._kill()
                    if cancelled.is_set():
                        raise BrainGenerationCancelled(
                            "warm claude generation cancelled (barge-in)"
                        ) from exc
                    raise BrainAdapterError(
                        f"warm claude io error: {exc}"
                    ) from exc

                if parser.result_is_error:
                    self._kill()  # zła sesja — następny generate wystartuje świeżą
                    raise BrainAdapterError("warm claude returned error result")

                text = parser.result_text or ""
                parsed = parse_tool_call_blocks(text)
                return BrainResponse(
                    text=parsed.text,
                    tool_calls=parsed.tool_calls,
                    model=self.default_model,
                    raw_metadata={
                        "adapter": self.name,
                        "warm": True,
                        "stateless": True,
                        "parsed_tool_call_count": len(parsed.tool_calls),
                    },
                )
            finally:
                if registered:
                    self._generation_registry.unregister(request.turn_id)


__all__ = ["ClaudeCliWarmAdapter"]
