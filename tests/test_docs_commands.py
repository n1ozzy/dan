"""Task 13: operator docs exist and every documented `dan` command parses."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = {
    "README.md",
    "docs/CO-JEST-GDZIE.md",
    "docs/GLOS-I-KOLEJKA.md",
    "docs/PANEL.md",
    "docs/RADIO-DAN.md",
    "docs/PRZENOSZENIE.md",
    "docs/ODZYSKIWANIE.md",
}

_FENCE = re.compile(r"```(?:bash|sh|shell|console)\n(.*?)```", re.DOTALL)
# Shell constructs after which argument extraction stops: the parse test
# validates the `dan ...` invocation itself, not the surrounding pipeline.
_SHELL_BREAKS = ("|", "<", ">", "&&", ";", "#")


def extract_shell_examples(doc_path: Path) -> list[str]:
    """All lines inside shell fences, stripped, prompts removed."""
    text = doc_path.read_text(encoding="utf-8")
    lines: list[str] = []
    for block in _FENCE.findall(text):
        for raw in block.splitlines():
            line = raw.strip()
            if line.startswith("$ "):
                line = line[2:]
            if line:
                lines.append(line)
    return lines


def extract_dan_commands() -> list[str]:
    commands: list[str] = []
    for relative in sorted(REQUIRED_DOCS):
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        for line in extract_shell_examples(path):
            if line.startswith("dan "):
                commands.append(line)
    return commands


def _command_arguments(command: str) -> list[str]:
    tokens = shlex.split(command, posix=True)
    arguments: list[str] = []
    for token in tokens[1:]:
        if token in _SHELL_BREAKS or token.startswith("<<"):
            break
        arguments.append(token)
    return arguments


def _cli_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", os.defpath),
        "DAN_TEST_MODE": "1",
        "DAN_DISABLE_AUDIO": "1",
        "DAN_DISABLE_MIC": "1",
        "PYTHONNOUSERSITE": "1",
    }


def test_required_docs_exist() -> None:
    missing = [doc for doc in sorted(REQUIRED_DOCS) if not (REPO_ROOT / doc).is_file()]
    assert missing == []


def test_docs_contain_dan_commands() -> None:
    assert extract_dan_commands(), "required docs document no `dan ...` command at all"


def test_every_documented_command_parses(tmp_path: Path) -> None:
    """Each documented `dan ...` invocation must parse against the real CLI.

    `--help` is appended so argparse validates the full subcommand path and
    every earlier flag/choice without executing network or daemon calls.
    Runs against a throwaway HOME so nothing touches the real ~/.dan.
    """
    failures: list[str] = []
    for command in extract_dan_commands():
        arguments = _command_arguments(command)
        completed = subprocess.run(
            [sys.executable, "-m", "dan.cli", *arguments, "--help"],
            cwd=REPO_ROOT,
            env=_cli_environment(tmp_path),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            failures.append(f"{command!r}: rc={completed.returncode} {completed.stderr.strip()}")
    assert failures == []


def test_voice_doc_contains_six_real_examples() -> None:
    examples = extract_shell_examples(REPO_ROOT / "docs" / "GLOS-I-KOLEJKA.md")
    dan_examples = [line for line in examples if line.startswith("dan ")]
    assert len(dan_examples) >= 6, dan_examples
