"""Atomic Jarvis client for the already-running shared DAN voice broker."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


DEFAULT_REQUEST_DIR = Path("/tmp/dan-voice/req")
DEFAULT_SPOKEN_RECENT_PATH = Path("/tmp/dan-listen/spoken-recent.txt")


class SharedBrokerError(RuntimeError):
    """The shared broker request could not be published atomically."""


class SharedBrokerClient:
    """Publish one whole utterance using DAN's current file-queue contract."""

    def __init__(
        self,
        config: Any,
        *,
        request_dir: str | Path = DEFAULT_REQUEST_DIR,
        persona: str = "jarvis",
        clock: Callable[[], float] = time.time,
        pid: Callable[[], int] = os.getpid,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._request_dir = Path(request_dir)
        self._persona = persona
        self._clock = clock
        self._pid = pid
        self._nonce = nonce or (lambda: uuid.uuid4().hex)
        self._publish_lock = threading.Lock()
        self._last_published_ns = -1

    def enqueue(
        self,
        *,
        text: str,
        session: str,
        priority: int = 0,
        lane: str | None = None,
    ) -> Path:
        clean = str(text or "").strip()
        if not clean:
            raise SharedBrokerError("shared broker request text must not be empty")

        voice = (getattr(self._config, "persona_voices", None) or {}).get(
            self._persona,
            getattr(self._config, "supertonic_voice", None),
        )
        speed = (getattr(self._config, "persona_speeds", None) or {}).get(
            self._persona,
            getattr(self._config, "supertonic_speed", None),
        )
        profile = (getattr(self._config, "persona_mastering", None) or {}).get(
            self._persona,
            getattr(self._config, "mastering_profile", None),
        )
        request = {
            "text": clean,
            "engine": getattr(self._config, "default_tts", None),
            "session": (str(session or "?") or "?")[:8],
            "voice": voice,
            "speed": speed,
            "priority": int(priority),
            "profile": profile,
            "language": getattr(self._config, "supertonic_lang", "pl") or "pl",
        }
        if lane:
            # Diagnostic-only metadata. The shared broker intentionally ignores
            # unknown fields, so this does not create another playback lane.
            request["lane"] = str(lane)

        self._request_dir.mkdir(parents=True, exist_ok=True)
        with self._publish_lock:
            clock_value = self._clock()
            stamp = f"{clock_value:.6f}-{self._pid()}-{self._nonce()}"
            final = self._request_dir / f"{stamp}.json"
            temporary = Path(f"{final}.tmp")
            published_ns = max(
                int(clock_value * 1_000_000_000),
                self._last_published_ns + 1,
            )
            try:
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(request, handle, ensure_ascii=False)
                # DAN orders equal-priority requests by mtime. Stamp the hidden
                # temporary file before the atomic rename so commentary stays
                # before final even when both use the same wall-clock tick.
                os.utime(temporary, ns=(published_ns, published_ns))
                os.replace(temporary, final)
                self._last_published_ns = published_ns
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise SharedBrokerError(f"could not enqueue shared broker request: {exc}") from exc
        return final


__all__ = [
    "DEFAULT_REQUEST_DIR",
    "DEFAULT_SPOKEN_RECENT_PATH",
    "SharedBrokerClient",
    "SharedBrokerError",
]
