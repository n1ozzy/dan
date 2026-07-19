#!/usr/bin/env python3
"""Nemovisa — Groq streaming + Supertonic TTS + mlx-whisper STT. Standalone, no DAN configs."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

import httpx

CONFIG_PATH = Path(__file__).parent / "config.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

BRAIN = CFG["brain"]
TTS = CFG["tts"]
STT = CFG["stt"]
AUDIO = CFG["audio"]
PERSONA = CFG["persona"]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SUPER_SERVE = TTS["serve_url"]

# Ensure ffmpeg in PATH for mlx-whisper
os.environ["PATH"] = f"/Users/n1_ozzy/.homebrew/bin:{os.environ['PATH']}"


def supertonic_say(text: str, voice: str = None) -> bytes:
    """Synthesize via supertonic serve (warm model)."""
    voice = voice or TTS["voice"]
    payload = {
        "input": text,
        "voice": voice,
        "model": TTS["model"],
        "speed": TTS["speed"],
    }
    if TTS.get("mastering") and TTS["mastering"] != "raw":
        payload["mastering"] = TTS["mastering"]

    resp = httpx.post(f"{SUPER_SERVE}/v1/audio/speech", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.content


def play_wav(wav_bytes: bytes):
    """Play WAV via afplay (macOS)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        path = f.name
    try:
        subprocess.run(["afplay", path], check=True)
    finally:
        os.unlink(path)


class PushToTalkRecorder:
    """Spacebar push-to-talk recorder using sox."""

    def __init__(self):
        self.recording = False
        self.proc = None
        self.path = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.recording:
                return
            self.path = Path(tempfile.mktemp(suffix=".wav"))
            self.path.touch(mode=0o600)
            cmd = [
                AUDIO["sox_binary"],
                "-q",
                "-d",
                "-r", str(AUDIO["sample_rate"]),
                "-c", "1",
                "-b", "16",
                "-e", "signed-integer",
                str(self.path),
            ]
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.recording = True
                print("[🔴] Recording... (space to stop)")
            except OSError as e:
                print(f"[❌] Failed to start recorder: {e}")
                self.recording = False

    def stop(self) -> bytes | None:
        with self.lock:
            if not self.recording or self.proc is None:
                return None
            self.recording = False

        # Graceful stop with SIGINT (sox finalizes WAV header)
        for sig, grace in ((15, 5.0), (9, 2.0)):  # SIGTERM, SIGKILL
            try:
                self.proc.send_signal(sig)
                self.proc.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
            except ProcessLookupError:
                break
        else:
            self.proc.kill()
            self.proc.wait(timeout=5.0)

        if self.path is None or not self.path.exists():
            return None

        try:
            audio = self.path.read_bytes()
        finally:
            self.path.unlink(missing_ok=True)

        return audio if len(audio) > 1024 else None


recorder = PushToTalkRecorder()


def record_while_space_held() -> bytes | None:
    """Record while spacebar is held (macOS)."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    print("\n[🎙] Hold SPACE to talk, release to send...")

    def key_listener():
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch == " ":
                    if not recorder.recording:
                        recorder.start()
                    else:
                        break
                time.sleep(0.01)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    listener_thread = threading.Thread(target=key_listener, daemon=True)
    listener_thread.start()

    # Wait for space release
    listener_thread.join()

    print("[⏹] Stopped recording")
    return recorder.stop()


def transcribe(wav_bytes: bytes) -> str:
    """Transcribe via mlx-whisper (needs ffmpeg in PATH)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        path = f.name
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            path,
            path_or_hf_repo=STT["model"],
            language=STT["language"],
            fp16=True,
        )
        return (result.get("text") or "").strip()
    finally:
        os.unlink(path)


async def groq_stream(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Stream from Groq."""
    headers = {
        "Authorization": f"Bearer {BRAIN['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": BRAIN["model"],
        "messages": messages,
        "stream": True,
        "temperature": BRAIN["temperature"],
        "max_tokens": BRAIN["max_tokens"],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", GROQ_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except Exception:
                    pass


async def speak(text: str):
    """Synthesize and play."""
    wav = supertonic_say(text)
    play_wav(wav)


async def main():
    print(f"\n=== {PERSONA['name']} ===")
    print("Groq streaming + Supertonic TTS (M3) + mlx-whisper STT")
    print("Hold SPACE to talk, release to send. Ctrl+C to quit.\n")

    # Warm up TTS serve
    try:
        httpx.get(f"{SUPER_SERVE}/v1/health", timeout=5)
        print("Supertonic serve: OK")
    except Exception as e:
        print(f"Supertonic serve not ready: {e}")
        print("Start: supertonic serve --model supertonic-3 --port 7788")
        return

    messages = [{"role": "system", "content": PERSONA["system_prompt"]}]

    while True:
        try:
            # Record
            audio = record_while_space_held()
            if not audio:
                print("[⚠] No audio captured")
                continue

            # Transcribe
            print("[🔍] Transcribing...")
            text = transcribe(audio)
            if not text:
                print("[⚠] Nothing understood")
                continue
            print(f"[👤] Ty: {text}")

            # Brain
            messages.append({"role": "user", "content": text})
            print(f"[{PERSONA['name']}] ", end="", flush=True)

            full = ""
            async for delta in groq_stream(messages):
                print(delta, end="", flush=True)
                full += delta
            print()

            messages.append({"role": "assistant", "content": full})

            # Speak
            await speak(full)

        except KeyboardInterrupt:
            print("\n\nDo widzenia, kurwa.")
            break
        except Exception as e:
            print(f"\n[❌] Błąd: {e}")


if __name__ == "__main__":
    asyncio.run(main())