"""Textual editor for ``config/voice/personas.toml``.

The panel changes a persona's voice route through this module. Edits are
line-based so comments and layout outside the changed lines survive; the
inline comment of a changed line is dropped because it described the old
value. Validation is fail-closed — any error leaves the file untouched —
and the write is atomic (temp file + ``os.replace`` in the same directory).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SPEED_MIN = 0.5
SPEED_MAX = 2.0

_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*(?:#.*)?$")

# Voice and mastering are identifiers, not free text. Anything outside this
# shape (quotes, backslashes, newlines, control characters) is rejected before
# it reaches the writer rather than escaped into the file.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


class PersonaEditError(ValueError):
    """Raised when a persona edit cannot be applied safely."""


@dataclass(frozen=True)
class PersonaEdit:
    """Result of one applied edit: ``changes[field] == (old, new)``."""

    persona: str
    path: Path
    changes: dict[str, tuple[object, object]]


def _personas_path(catalog_dir: Path | str) -> Path:
    return Path(catalog_dir) / "personas.toml"


def _load(path: Path) -> dict:
    if not path.is_file():
        raise PersonaEditError(f"personas file not found: {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PersonaEditError(f"personas file is not valid TOML: {exc}") from exc


def list_personas(catalog_dir: Path | str) -> dict[str, dict]:
    """Return every persona section as ``{name: fields}``."""

    data = _load(_personas_path(catalog_dir))
    return {
        name: dict(fields)
        for name, fields in data.items()
        if isinstance(fields, dict)
    }


def _format_value(field_name: str, value: object) -> str:
    if field_name == "speed":
        # repr() of a float always round-trips and always carries a decimal
        # point, so the value stays a TOML float after the edit.
        return repr(float(value))
    text = str(value)
    if not _IDENTIFIER_RE.match(text):
        raise PersonaEditError(
            f"{field_name} {text!r} is not a plain identifier"
        )
    return '"' + text + '"'


def _rewrite_section(text: str, persona: str, updates: dict[str, object]) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_target = False
    pending = dict(updates)
    for line in lines:
        match = _SECTION_RE.match(line.strip())
        if match:
            in_target = match.group("name") == persona
            out.append(line)
            continue
        if in_target and pending:
            replaced = False
            for field_name in list(pending):
                if re.match(rf"^\s*{re.escape(field_name)}\s*=", line):
                    suffix = "\n" if line.endswith("\n") else ""
                    formatted = _format_value(field_name, pending.pop(field_name))
                    out.append(f"{field_name} = {formatted}{suffix}")
                    replaced = True
                    break
            if replaced:
                continue
        out.append(line)
    if pending:
        raise PersonaEditError(
            f"persona {persona!r} is missing fields: {sorted(pending)}"
        )
    return "".join(out)


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=".personas-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates 0600 and os.replace carries the temp file's mode onto
        # the target, so without this the catalog silently becomes owner-only
        # after the first edit and stays that way.
        if path.exists():
            shutil.copymode(path, tmp_name)
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself, not just the new file's bytes."""

    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def write_personas_text(catalog_dir: Path | str, content: str) -> None:
    """Atomically restore raw personas.toml content (API rollback path)."""

    _atomic_write(_personas_path(catalog_dir), content)


def _assert_no_collateral_change(
    before: dict, after: dict, persona: str, requested: set[str]
) -> None:
    """Prove the textual edit moved the requested fields and nothing else.

    Checking only the requested fields let a line that merely *looks* like a
    field — e.g. inside a multi-line string — be rewritten unnoticed whenever
    the requested value equalled the current one.
    """

    if set(before) != set(after):
        raise PersonaEditError("edit changed the set of personas")
    for name in before:
        old_section = before[name]
        new_section = after[name]
        if not isinstance(old_section, dict) or not isinstance(new_section, dict):
            if old_section != new_section:
                raise PersonaEditError(f"edit changed unrelated entry {name!r}")
            continue
        if set(old_section) != set(new_section):
            raise PersonaEditError(f"edit changed the fields of persona {name!r}")
        untouched = set(old_section) - (requested if name == persona else set())
        for field_name in untouched:
            if old_section[field_name] != new_section[field_name]:
                raise PersonaEditError(
                    f"edit changed unrequested field {field_name!r} "
                    f"of persona {name!r}"
                )


def set_persona_voice(
    catalog_dir: Path | str,
    persona: str,
    *,
    voice: str | None = None,
    speed: float | None = None,
    mastering: str | None = None,
    allowed_voices: Iterable[str] | None = None,
    allowed_mastering: Iterable[str] | None = None,
) -> PersonaEdit:
    """Apply a validated edit to one persona section and report old→new."""

    path = _personas_path(catalog_dir)
    data = _load(path)
    section = data.get(persona)
    if not isinstance(section, dict):
        raise PersonaEditError(f"unknown persona: {persona!r}")

    requested: dict[str, object] = {}
    if voice is not None:
        requested["voice"] = str(voice)
    if speed is not None:
        requested["speed"] = float(speed)
    if mastering is not None:
        requested["mastering"] = str(mastering)
    if not requested:
        raise PersonaEditError("nothing to change: pass voice, speed or mastering")

    if "voice" in requested and allowed_voices is not None:
        if requested["voice"] not in set(allowed_voices):
            raise PersonaEditError(
                f"voice {requested['voice']!r} is not in the allowed set"
            )
    if "mastering" in requested and allowed_mastering is not None:
        if requested["mastering"] not in set(allowed_mastering):
            raise PersonaEditError(
                f"mastering {requested['mastering']!r} is not in the allowed set"
            )
    if "speed" in requested:
        speed_value = float(requested["speed"])  # type: ignore[arg-type]
        if not (SPEED_MIN <= speed_value <= SPEED_MAX):
            raise PersonaEditError(
                f"speed {speed_value} outside allowed range "
                f"{SPEED_MIN}-{SPEED_MAX}"
            )

    changes: dict[str, tuple[object, object]] = {}
    for field_name, new_value in requested.items():
        if field_name not in section:
            raise PersonaEditError(
                f"persona {persona!r} has no field {field_name!r}"
            )
        changes[field_name] = (section[field_name], new_value)

    original = path.read_text(encoding="utf-8")
    updated = _rewrite_section(original, persona, requested)
    try:
        reparsed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise PersonaEditError(f"edit produced invalid TOML: {exc}") from exc
    for field_name, new_value in requested.items():
        if reparsed.get(persona, {}).get(field_name) != new_value:
            raise PersonaEditError(
                f"edit failed to apply {field_name!r} for persona {persona!r}"
            )
    _assert_no_collateral_change(data, reparsed, persona, set(requested))

    _atomic_write(path, updated)
    return PersonaEdit(persona=persona, path=path, changes=changes)
