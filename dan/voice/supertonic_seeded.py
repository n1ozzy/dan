"""Project-owned deterministic adapter for the pinned Supertonic runtime.

Supertonic 1.3.1 samples its latent with ``numpy.random.randn`` and exposes no
seed in either its Python or HTTP contract.  This module keeps the dependency
untouched and puts the seed at the only safe point: under the synthesis lock,
immediately before ``TTS.synthesize``.  The same helper powers the supervised
warm server and the one-shot fallback renderer.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

SEED_MAX = (2**32) - 1
SEED_PROTOCOL_VERSION = "1"
SEED_PROTOCOL_HEADER = "X-DAN-Seed-Protocol"
SYNTHESIS_SEED_HEADER = "X-DAN-Synthesis-Seed"


class SeedValidationError(ValueError):
    """A synthesis seed is missing, coerced, or outside NumPy's uint32 range."""


def validate_seed(seed: object) -> int:
    if type(seed) is not int or not 0 <= seed <= SEED_MAX:
        raise SeedValidationError(
            f"seed must be an integer between 0 and {SEED_MAX}"
        )
    return seed


def synthesize_seeded(
    tts: Any,
    *,
    seed: object,
    lock: Any,
    text: str,
    **kwargs: Any,
) -> tuple[np.ndarray, np.ndarray]:
    """Run one deterministic synthesis with no RNG-consuming gap.

    The lock is deliberately supplied by the owner.  The warm server passes
    its process-wide inference lock; the one-shot renderer passes a local lock.
    """

    checked = validate_seed(seed)
    with lock:
        np.random.seed(checked)
        return tts.synthesize(text=text, **kwargs)


def encode_wav(wav: np.ndarray, sample_rate: int) -> bytes:
    """Encode PCM16 WAV identically in the warm and one-shot paths."""

    if wav.ndim == 2:
        wav = wav.squeeze(0)
    output = io.BytesIO()
    sf.write(output, wav, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def seeded_supertonic_argv(
    python_executable: str | None = None,
) -> tuple[str, ...]:
    """Command prefix shared by dand supervision and CLI fallback."""

    return (
        python_executable or sys.executable,
        "-m",
        "dan.voice.supertonic_seeded",
    )


@dataclass
class SeededServerState:
    model: str = "supertonic-3"
    tts: Any = None
    synth_lock: Any = field(default_factory=threading.Lock)
    is_ready: bool = False


def _headers(seed: int | None = None) -> dict[str, str]:
    headers = {SEED_PROTOCOL_HEADER: SEED_PROTOCOL_VERSION}
    if seed is not None:
        headers[SYNTHESIS_SEED_HEADER] = str(seed)
    return headers


def create_app(*, state: SeededServerState | None = None, model: str = "supertonic-3"):
    """Create the sole warm server used by dand.

    Imports stay lazy so deterministic helper tests do not require the optional
    HTTP extra.  The server intentionally exposes only DAN's two used routes.
    """

    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse, Response
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:  # pragma: no cover - exercised by installer smoke
        raise RuntimeError(
            "seeded Supertonic serve requires supertonic[serve]"
        ) from exc

    server_state = state or SeededServerState(model=model)

    @asynccontextmanager
    async def lifespan(_app: Any):
        if server_state.tts is None:
            from supertonic import TTS

            server_state.tts = TTS(model=server_state.model)
        server_state.is_ready = True
        try:
            yield
        finally:
            server_state.is_ready = False

    app = FastAPI(title="DAN Seeded Supertonic", lifespan=lifespan)
    app.state.server_state = server_state

    class SeededTTSRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        text: str = Field(..., min_length=1)
        voice: str = "M1"
        lang: str | None = None
        speed: float | None = Field(None, ge=0.7, le=2.0)
        steps: int | None = Field(None, ge=1, le=100)
        max_chunk_length: int | None = Field(None, ge=1, le=10_000)
        silence_duration: float | None = Field(None, ge=0.0, le=10.0)
        seed: int = Field(..., strict=True, ge=0, le=SEED_MAX)

    @app.get("/v1/health")
    def health():
        tts = server_state.tts
        status = 200 if server_state.is_ready and tts is not None else 503
        return JSONResponse(
            status_code=status,
            content={
                "status": "ok" if status == 200 else "loading",
                "model": server_state.model,
                "sample_rate": getattr(tts, "sample_rate", None),
                "seed_protocol": SEED_PROTOCOL_VERSION,
            },
            headers=_headers(),
        )

    def synthesize(req: Any):
        tts = server_state.tts
        if not server_state.is_ready or tts is None:
            return JSONResponse(
                status_code=503,
                content={"error": {"message": "server not ready", "code": "not_ready"}},
                headers=_headers(),
            )
        try:
            if req.voice not in tts.voice_style_names:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "message": f"unknown voice {req.voice!r}",
                            "code": "unknown_voice",
                        }
                    },
                    headers=_headers(),
                )
            style = tts.get_voice_style(req.voice)
            kwargs: dict[str, Any] = {"voice_style": style}
            if req.lang is not None:
                kwargs["lang"] = req.lang
            if req.speed is not None:
                kwargs["speed"] = req.speed
            if req.steps is not None:
                kwargs["total_steps"] = req.steps
            if req.max_chunk_length is not None:
                kwargs["max_chunk_length"] = req.max_chunk_length
            if req.silence_duration is not None:
                kwargs["silence_duration"] = req.silence_duration
            wav, _duration = synthesize_seeded(
                tts,
                text=req.text,
                seed=req.seed,
                lock=server_state.synth_lock,
                **kwargs,
            )
            return Response(
                content=encode_wav(wav, tts.sample_rate),
                media_type="audio/wav",
                headers=_headers(req.seed),
            )
        except Exception as exc:  # noqa: BLE001 - local API returns a stable envelope
            logging.getLogger(__name__).exception("seeded synthesis failed")
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"synthesis failed: {exc}",
                        "code": "synthesis_failed",
                    }
                },
                headers=_headers(),
            )

    # ``from __future__ import annotations`` would otherwise leave this local
    # model as an unresolved string and FastAPI would misclassify ``req`` as a
    # query parameter. Register with the concrete class object instead.
    synthesize.__annotations__["req"] = SeededTTSRequest
    app.post("/v1/tts")(synthesize)
    return app


def _render(args: argparse.Namespace) -> int:
    from supertonic import TTS

    tts = TTS(model=args.model)
    style = (
        tts.get_voice_style_from_path(args.custom_style_path)
        if args.custom_style_path
        else tts.get_voice_style(args.voice)
    )
    wav, _duration = synthesize_seeded(
        tts,
        text=args.text,
        voice_style=style,
        seed=args.seed,
        lock=threading.Lock(),
        lang=args.lang,
        speed=args.speed,
        total_steps=args.steps,
        max_chunk_length=args.max_chunk_length,
        silence_duration=args.silence_duration,
    )
    output = Path(args.output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_bytes(encode_wav(wav, tts.sample_rate))
    return 0


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "seeded Supertonic serve requires supertonic[serve]"
        ) from exc
    uvicorn.run(
        create_app(model=args.model),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dan.voice.supertonic_seeded")
    commands = parser.add_subparsers(dest="command", required=True)

    render = commands.add_parser("render")
    render.add_argument("text")
    render.add_argument("-o", "--output", required=True)
    render.add_argument("--model", default="supertonic-3")
    render.add_argument("--voice", default="M1")
    render.add_argument("--custom-style-path")
    render.add_argument("--lang", default="pl")
    render.add_argument("--steps", type=int, default=14)
    render.add_argument("--speed", type=float, default=1.05)
    render.add_argument("--max-chunk-length", type=int, default=400)
    render.add_argument("--silence-duration", type=float, default=0.0)
    render.add_argument("--seed", type=int, required=True)

    serve = commands.add_parser("serve")
    serve.add_argument("--model", default="supertonic-3")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7788)
    serve.add_argument("--log-level", default="warning")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "render":
        validate_seed(args.seed)
        return _render(args)
    return _serve(args)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SEED_MAX",
    "SEED_PROTOCOL_HEADER",
    "SEED_PROTOCOL_VERSION",
    "SYNTHESIS_SEED_HEADER",
    "SeedValidationError",
    "SeededServerState",
    "create_app",
    "encode_wav",
    "seeded_supertonic_argv",
    "synthesize_seeded",
    "validate_seed",
]
