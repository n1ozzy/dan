"""Pluggable STT engines (G4b, decree §7.4: STT = MLX whisper).

The mock engine serves every test and smoke; the real engine wraps
mlx-whisper with the model from config (mlx-community/whisper-large-v3-
turbo, already in the per-user cache — live inventory 2026-07-02).

§4a fact: MLX holds model+stream per thread, so ALL inference runs on one
dedicated worker thread owned by the engine — never on the caller's thread
and never on more than one. The engine never decides whether audio is
worth transcribing; that is the CaptureGate's job (jarvis/voice/vad.py).
"""

from __future__ import annotations

import importlib.util
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any


DEFAULT_STT_MODEL = "mlx-community/whisper-large-v3-turbo"
# 16 kHz / 16-bit / mono is the recorder's native shape; used to size the
# transcription timeout to how much audio was actually captured.
_BYTES_PER_AUDIO_SECOND = 16000 * 2
DEFAULT_STT_TIMEOUT_SECONDS = 30.0
DEFAULT_STT_TIMEOUT_PER_AUDIO_SECOND = 10.0


class STTEngineError(Exception):
    """Raised when an engine is unknown, unavailable, or fails to transcribe."""


class MockSTTEngine:
    """Deterministic engine double: logs calls, returns a preset transcript.

    The default transcript carries a fake sk-* secret on purpose — the
    established fixture rule: every smoke that persists a mock transcript
    thereby proves redaction at rest.
    """

    name = "mock"

    DEFAULT_TRANSCRIPT = (
        "Transkrypcja mock: klucz sk-mock-secret-123456 nie może przeżyć zapisu."
    )

    def __init__(self, *, transcript: str | None = None) -> None:
        self.transcript = self.DEFAULT_TRANSCRIPT if transcript is None else transcript
        self.calls: list[int] = []

    def transcribe(self, audio: bytes) -> str:
        self.calls.append(len(audio))
        return self.transcript

    def stop(self) -> None:  # symmetry with the real engine
        return None


def _mlx_whisper_available() -> bool:
    return importlib.util.find_spec("mlx_whisper") is not None


class MlxWhisperEngine:
    """Real STT through mlx-whisper (decree §7.4).

    One dedicated thread executes every model call (§4a fact); the model
    loads lazily on that thread at the first transcription. The audio comes
    in as WAV bytes and leaves the process only as a transient 0600 file in
    the private runtime workdir, unlinked in a finally block.
    """

    name = "mlx_whisper"

    def __init__(self, *, config: Any) -> None:
        if not _mlx_whisper_available():
            raise STTEngineError(
                "mlx_whisper is not importable (install the decreed package "
                "into the daemon's venv — MASTER_PLAN §7.4)."
            )
        voice_cfg = config.voice
        self._model = str(getattr(voice_cfg, "stt_model", DEFAULT_STT_MODEL) or DEFAULT_STT_MODEL)
        self._language = str(getattr(voice_cfg, "stt_language", "pl") or "pl")
        self._base_timeout = max(
            0.1,
            float(
                getattr(voice_cfg, "stt_timeout_seconds", DEFAULT_STT_TIMEOUT_SECONDS)
                or DEFAULT_STT_TIMEOUT_SECONDS
            ),
        )
        self._timeout_per_second = max(
            0.0,
            float(
                getattr(
                    voice_cfg,
                    "stt_timeout_per_audio_second",
                    DEFAULT_STT_TIMEOUT_PER_AUDIO_SECOND,
                )
                or DEFAULT_STT_TIMEOUT_PER_AUDIO_SECOND
            ),
        )
        workdir = Path(os.path.expanduser(str(config.runtime.runtime_dir))) / "voice"
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(workdir, 0o700)
        self.workdir = str(workdir)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-stt-mlx")

    def transcribe(self, audio: bytes) -> str:
        timeout = self._timeout_for(audio)
        future = self._executor.submit(self._transcribe_on_thread, audio)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError as exc:
            # A stuck MLX/Metal call can never be cancelled from here, so the
            # only way to free the pipeline is to abandon the poisoned worker
            # and hand the NEXT capture a fresh one (FIX-09).
            self._recycle_executor()
            raise STTEngineError(f"mlx-whisper timed out after {timeout:g}s") from exc
        except STTEngineError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalize model errors
            raise STTEngineError(f"mlx-whisper failed: {exc}") from exc

    def _timeout_for(self, audio: bytes) -> float:
        audio_seconds = len(audio) / _BYTES_PER_AUDIO_SECOND
        return self._base_timeout + audio_seconds * self._timeout_per_second

    def _recycle_executor(self) -> None:
        old = self._executor
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-stt-mlx"
        )
        # Do not wait: the abandoned thread is stuck inside the model call and
        # will exit on its own if/when the call ever returns.
        old.shutdown(wait=False, cancel_futures=True)

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- internals (dedicated thread only) ----------------------------------

    def _transcribe_on_thread(self, audio: bytes) -> str:
        assert threading.current_thread().name.startswith("jarvis-stt-mlx")
        path = Path(self.workdir) / f"stt-{uuid.uuid4().hex}.wav"
        try:
            path.touch(mode=0o600)
            path.write_bytes(audio)
            result = self._run_model(str(path))
        finally:
            path.unlink(missing_ok=True)
        return str(result.get("text", "") or "").strip()

    def _run_model(self, path: str) -> dict:
        import mlx_whisper  # heavy import stays off the daemon startup path

        # condition_on_previous_text=False: one utterance = one clean pass;
        # carrying context across captures amplifies hallucinations.
        return mlx_whisper.transcribe(
            path,
            path_or_hf_repo=self._model,
            language=self._language,
            condition_on_previous_text=False,
        )


def build_stt_engine(name: str, *, config: Any | None = None) -> Any:
    normalized = str(name or "").strip().lower().replace("-", "_")
    if normalized == "mock":
        return MockSTTEngine()
    if normalized == "mlx_whisper":
        if config is None:
            raise STTEngineError(
                "STT engine 'mlx_whisper' needs the daemon config "
                "(voice.stt_* and runtime.runtime_dir)."
            )
        return MlxWhisperEngine(config=config)
    raise STTEngineError(f"Unknown STT engine {name!r}.")


__all__ = [
    "DEFAULT_STT_MODEL",
    "MlxWhisperEngine",
    "MockSTTEngine",
    "STTEngineError",
    "build_stt_engine",
]
