"""Render the single versioned DAN canon with private owner data."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANON_PATH = REPO_ROOT / "config" / "persona" / "DAN.md"
DEFAULT_OWNER_PATH = Path.home() / ".dan" / "owner.toml"
OWNER_DISPLAY_NAME_TEMPLATE = "{{ owner.display_name }}"
_CANON_VERSION = re.compile(r"(?m)^DAN_CANON_VERSION:\s*([^\s]+)\s*$")


class PersonaError(RuntimeError):
    """The canonical persona or private owner profile is invalid."""


@dataclass(frozen=True)
class OwnerProfile:
    display_name: str


def load_owner(path: str | Path = DEFAULT_OWNER_PATH) -> OwnerProfile:
    owner_path = Path(path).expanduser()
    try:
        with owner_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise PersonaError(f"owner file does not exist: {owner_path}") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PersonaError(f"could not load owner file {owner_path}: {exc}") from exc
    owner = data.get("owner")
    if not isinstance(owner, dict):
        raise PersonaError(f"owner file must contain an [owner] table: {owner_path}")
    display_name = owner.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        raise PersonaError(f"owner.display_name must be a non-empty string: {owner_path}")
    if "{{" in display_name or "}}" in display_name:
        raise PersonaError("owner.display_name cannot contain template syntax")
    return OwnerProfile(display_name=display_name.strip())


def render_persona(
    canon_path: str | Path = DEFAULT_CANON_PATH,
    owner_path: str | Path = DEFAULT_OWNER_PATH,
    *,
    owner: OwnerProfile | None = None,
) -> str:
    path = Path(canon_path).expanduser()
    try:
        if not path.is_file():
            raise PersonaError(f"persona canon does not exist: {path}")
        canon = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PersonaError(f"persona canon is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise PersonaError(f"could not read persona canon {path}: {exc}") from exc
    if not canon.strip():
        raise PersonaError(f"persona canon is empty: {path}")
    version = _CANON_VERSION.search(canon)
    if version is None or version.group(1) != "1":
        raise PersonaError(f"persona canon has unknown canonical version: {path}")
    if OWNER_DISPLAY_NAME_TEMPLATE not in canon:
        return canon
    if owner is not None:
        profile = owner
    else:
        try:
            profile = load_owner(owner_path)
        except PersonaError as exc:
            if "does not exist" not in str(exc):
                raise
            profile = OwnerProfile(display_name="właściciel")
    rendered = canon.replace(OWNER_DISPLAY_NAME_TEMPLATE, profile.display_name)
    if "{{ owner." in rendered:
        raise PersonaError(f"persona canon contains an unresolved owner template: {path}")
    return rendered


__all__ = [
    "DEFAULT_CANON_PATH",
    "DEFAULT_OWNER_PATH",
    "OWNER_DISPLAY_NAME_TEMPLATE",
    "OwnerProfile",
    "PersonaError",
    "load_owner",
    "render_persona",
]
