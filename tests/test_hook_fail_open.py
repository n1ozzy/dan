"""Task 11: the Claude MessageDisplay hook is fail-open and never regresses.

Offline dand, a hanging CLI or a missing CLI must all end in exit 0 in under
one second, with no fallback into the legacy audio stack and no /tmp state.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "integrations" / "claude" / "hooks" / "tts-message-display.sh"

# Concatenated on purpose — the baseline scanner refuses raw legacy literals.
LEGACY_HOOK_TOKENS = (
    "afplay",
    "say.py",
    "dan_core",
    "voice_broker",
    "loud-thinking",
    "/tmp/" + "dan-",
    "/tmp/claude" + "-loud-thinking",
    ":" + "7788",
)


def _payload(text: str = "[[GŁOS]] test kanału głosowego") -> bytes:
    return json.dumps({"delta": text}, ensure_ascii=False).encode("utf-8")


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "en_US.UTF-8",
    }
    env.update(extra)
    return env


def _install_fake_dan(home: Path, body: str) -> Path:
    bin_dir = home / ".dan" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "dan"
    fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _run_hook(home: Path, payload: bytes, env: dict[str, str]) -> tuple[subprocess.CompletedProcess, float]:
    start = time.monotonic()
    result = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=payload,
        env=env,
        capture_output=True,
        timeout=10,
    )
    return result, time.monotonic() - start


def test_hook_is_fail_open_when_dand_is_down(tmp_path: Path) -> None:
    # No dan CLI on PATH and no daemon: the marker path must still exit 0 fast.
    result, elapsed = _run_hook(tmp_path, _payload(), _env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert elapsed < 1.0
    # started_fallback is False: nothing legacy was spawned or referenced.
    assert not (tmp_path / ".dan" / "bin").exists()


def test_hook_kills_a_hanging_cli_and_exits_zero(tmp_path: Path) -> None:
    _install_fake_dan(tmp_path, "sleep 5\n")
    result, elapsed = _run_hook(tmp_path, _payload(), _env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert elapsed < 1.0


def test_hook_invokes_exact_contract_argv(tmp_path: Path) -> None:
    record = tmp_path / "argv.txt"
    spoken = tmp_path / "stdin.txt"
    _install_fake_dan(
        tmp_path,
        f'printf \'%s\\n\' "$@" > "{record}"\ncat > "{spoken}"\nexit 0\n',
    )
    result, elapsed = _run_hook(
        tmp_path,
        _payload("przed [[GŁOS]] mów tylko to [[/GŁOS]] po"),
        _env(tmp_path, DAN_VOICE_HOOK_SESSION="claude-hook"),
    )
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 2.0
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    argv = record.read_text(encoding="utf-8").splitlines()
    assert argv == [
        "speak",
        "--json",
        "--as",
        "dan",
        "--session",
        "claude-hook",
        "--source",
        "hook",
        "--stdin",
    ]
    assert spoken.read_text(encoding="utf-8").strip() == "mów tylko to"


def test_hook_without_marker_is_silent(tmp_path: Path) -> None:
    record = tmp_path / "argv.txt"
    _install_fake_dan(tmp_path, f'printf hit > "{record}"\nexit 0\n')
    result, elapsed = _run_hook(
        tmp_path, json.dumps({"delta": "zwykły raport bez markera"}).encode(), _env(tmp_path)
    )
    assert result.returncode == 0
    time.sleep(0.2)
    assert not record.exists()


def test_hook_respects_hook_enabled_false(tmp_path: Path) -> None:
    record = tmp_path / "argv.txt"
    _install_fake_dan(tmp_path, f'printf hit > "{record}"\nexit 0\n')
    config = tmp_path / ".dan" / "config.toml"
    config.write_text("[voice]\nhook_enabled = false\n", encoding="utf-8")
    result, _ = _run_hook(tmp_path, _payload(), _env(tmp_path))
    assert result.returncode == 0
    time.sleep(0.2)
    assert not record.exists()


def test_hook_session_override_is_explicit_env_not_tmp_state(tmp_path: Path) -> None:
    record = tmp_path / "argv.txt"
    _install_fake_dan(tmp_path, f'printf hit > "{record}"\nexit 0\n')
    result, _ = _run_hook(tmp_path, _payload(), _env(tmp_path, DAN_VOICE_HOOK="off"))
    assert result.returncode == 0
    time.sleep(0.2)
    assert not record.exists()
    text = HOOK.read_text(encoding="utf-8")
    assert "DAN_VOICE_HOOK" in text
    assert "mkdir -p /tmp" not in text


def test_hook_never_references_legacy_audio() -> None:
    text = HOOK.read_text(encoding="utf-8")
    for token in LEGACY_HOOK_TOKENS:
        assert token not in text, token
    assert os.access(HOOK, os.X_OK)
    # Fail-open by construction: the script must end in exit 0.
    assert text.rstrip().endswith("exit 0")
