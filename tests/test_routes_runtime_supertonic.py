"""Regression tests for supertonic voice discovery in runtime projection helpers."""

from __future__ import annotations

import subprocess

from dan.api import routes_runtime


class _CompletedProcess:
    def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_parse_supertonic_voices_supports_mixed_case_and_filters_noise() -> None:
    raw = "available: M1 m2 F3, g4, voiceM5 and F10 M12"
    assert routes_runtime._parse_supertonic_voices(raw) == {
        "M1",
        "M2",
        "F3",
        "F10",
        "M12",
    }


def test_safe_probe_supertonic_voice_is_ok_when_voice_is_present(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_: object) -> _CompletedProcess:
        calls.append(list(command))
        return _CompletedProcess(0, "F1 M1")

    monkeypatch.setattr(routes_runtime.subprocess, "run", fake_run)

    status, resolved, warning = routes_runtime._safe_probe_supertonic_voice(
        "/tmp/supertonic",
        "m1",
    )

    assert status == "ok"
    assert resolved is None
    assert warning is None
    assert calls == [["/tmp/supertonic", "list-voices"]]


def test_safe_probe_supertonic_voice_warns_when_voice_missing(monkeypatch) -> None:
    def fake_run(command, **_: object) -> _CompletedProcess:
        return _CompletedProcess(0, "M1 F1")

    monkeypatch.setattr(routes_runtime.subprocess, "run", fake_run)

    status, resolved, warning = routes_runtime._safe_probe_supertonic_voice(
        "/tmp/supertonic",
        "M2",
    )

    assert status == "missing"
    assert resolved is None
    assert warning is not None
    assert "M2" in warning
    assert "M1" in warning
    assert "F1" in warning


def test_safe_probe_supertonic_voice_marks_timeout_as_unknown(monkeypatch) -> None:
    def fake_run(command, **_: object) -> None:
        raise subprocess.TimeoutExpired(["/tmp/supertonic", "list-voices"], timeout=2)

    monkeypatch.setattr(routes_runtime.subprocess, "run", fake_run)

    status, resolved, warning = routes_runtime._safe_probe_supertonic_voice(
        "/tmp/supertonic",
        "M1",
    )

    assert status == "unknown"
    assert resolved is None
    assert warning is not None
    assert "Timed out while validating supertonic voices" in warning
