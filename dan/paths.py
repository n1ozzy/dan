"""Runtime path resolution for DAN-owned local state."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dan.config import DANConfig


# DAN-owned state is single-user and may hold secrets (DB rows, logs); it is
# created owner-only. Dirs 0700, files 0600 — mirrors security/transport.py and
# macos/screen.py. Mode on mkdir is umask-subject, so we chmod explicitly too.
RUNTIME_DIR_MODE = 0o700
RUNTIME_FILE_MODE = 0o600


def secure_path(path: Path | str, mode: int) -> None:
    """Best-effort tighten permissions on a DAN-owned path.

    A chmod that fails (unusual filesystem, a race) must not crash startup —
    the bind is localhost-only and the parent dir is already owner-only."""

    try:
        os.chmod(path, mode)
    except OSError:
        pass


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    config_path: Path
    db_path: Path
    logs_dir: Path
    runtime_dir: Path
    owner_path: Path
    secrets_path: Path
    pid_file: Path
    log_file: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def expand_user_path(path: str) -> Path:
    """Expand a user-configured path into an absolute filesystem path."""

    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded


def resolve_runtime_paths(config: DANConfig) -> RuntimePaths:
    """Resolve runtime paths without creating files or directories."""

    home = expand_user_path(config.runtime.home)
    logs_dir = expand_user_path(config.runtime.logs_dir)
    return RuntimePaths(
        home=home,
        config_path=home / "config.toml",
        db_path=expand_user_path(config.database.path),
        logs_dir=logs_dir,
        runtime_dir=expand_user_path(config.runtime.runtime_dir),
        owner_path=home / "owner.toml",
        secrets_path=home / "secrets.env",
        pid_file=expand_user_path(config.runtime.pid_file),
        log_file=logs_dir / "dand.log",
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """Create only the DAN runtime directories needed before use."""

    for directory in (paths.home, paths.logs_dir, paths.runtime_dir):
        directory.mkdir(mode=RUNTIME_DIR_MODE, parents=True, exist_ok=True)
        # exist_ok dirs keep their old (possibly looser) mode; chmod fixes that.
        secure_path(directory, RUNTIME_DIR_MODE)


def paths_to_jsonable(paths: RuntimePaths) -> dict[str, Any]:
    return paths.to_dict()
