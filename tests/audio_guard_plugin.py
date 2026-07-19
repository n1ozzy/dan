"""Pytest containment for known audio and microphone subprocesses."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from dan.audio.execution import AUDIO_EXECUTABLE_NAMES, AudioExecutionDisabled

CONFIG_MARKER_ATTRIBUTE = "_dan_audio_guard_plugin_loaded"
PLUGIN_LOADED_MARKER = object()

_ORIGINAL_POPEN = subprocess.Popen


def _as_executable(value: object) -> str | None:
    try:
        return os.fsdecode(value)  # type: ignore[arg-type]
    except TypeError:
        return None


def _command_executable(command: object) -> str | None:
    if isinstance(command, (str, bytes, os.PathLike)):
        decoded = _as_executable(command)
        if decoded is None:
            return None
        try:
            return shlex.split(decoded)[0]
        except (IndexError, ValueError):
            return None
    if isinstance(command, Sequence) and command:
        return _as_executable(command[0])
    return None


def _guarded_popen(
    args: object,
    *popenargs: object,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    override = kwargs.get("executable")
    executable = _as_executable(override) if override is not None else _command_executable(args)
    name = Path(executable).name.casefold() if executable else ""
    if name in AUDIO_EXECUTABLE_NAMES:
        raise AudioExecutionDisabled(f"audio subprocess execution disabled: {name}")
    return _ORIGINAL_POPEN(args, *popenargs, **kwargs)  # type: ignore[arg-type]


def pytest_configure(config: pytest.Config) -> None:
    subprocess.Popen = _guarded_popen  # type: ignore[assignment,misc]
    setattr(config, CONFIG_MARKER_ATTRIBUTE, PLUGIN_LOADED_MARKER)


def pytest_unconfigure(config: pytest.Config) -> None:
    if subprocess.Popen is _guarded_popen:
        subprocess.Popen = _ORIGINAL_POPEN  # type: ignore[assignment,misc]
    if getattr(config, CONFIG_MARKER_ATTRIBUTE, None) is PLUGIN_LOADED_MARKER:
        delattr(config, CONFIG_MARKER_ATTRIBUTE)
