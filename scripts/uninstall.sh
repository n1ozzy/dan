#!/usr/bin/env bash
# Uninstall the DAN host surface using ONLY the install manifest.
#
# Removes exactly the paths the installer recorded (restoring backups where
# a path was replaced) and nothing else. It NEVER deletes:
#   - the database        ~/.dan/dan.db (or any *.db under ~/.dan)
#   - owner data          ~/.dan/owner.toml, ~/.dan/secrets.env
#   - migration backups   ~/.dan/migration/, ~/.dan/backups/
#   - anything under ~/.claude/archive
set -euo pipefail

MANIFEST="$HOME/.dan/install-manifest.json"

if [ ! -f "$MANIFEST" ]; then
  echo "No install manifest at $MANIFEST - nothing to uninstall." >&2
  exit 1
fi

PYTHON="$(command -v python3)"

"$PYTHON" - "$MANIFEST" "$HOME" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
home = Path(sys.argv[2]).resolve()
data = json.loads(manifest_path.read_text(encoding="utf-8"))

PROTECTED_SUFFIXES = (".db", ".db-wal", ".db-shm")
BLOCK_BEGIN = "<!-- BEGIN DAN MANAGED BLOCK (dan-runtime) -->"
BLOCK_END = "<!-- END DAN MANAGED BLOCK (dan-runtime) -->"

def refuse(path: Path, why: str) -> None:
    print(f"SKIP (protected, {why}): {path}")

def strip_managed_block(path: Path) -> None:
    """Remove ONLY the named DAN block; owner text stays whatever its history."""
    text = path.read_text(encoding="utf-8")
    head, _, rest = text.partition(BLOCK_BEGIN)
    _, marker, tail = rest.partition(BLOCK_END)
    if not marker:
        print(f"SKIP (broken managed block): {path}")
        return
    merged = (head.rstrip("\n") + "\n" if head.strip() else "") + tail.lstrip("\n")
    if merged.strip():
        path.write_text(merged, encoding="utf-8")
        print(f"stripped managed block: {path}")
    else:
        path.unlink()
        print(f"removed:  {path}")

for entry in reversed(data.get("entries", [])):
    path = Path(entry["path"])
    resolved = path.resolve()
    if home not in resolved.parents and resolved != home:
        refuse(path, "outside HOME")
        continue
    parts = resolved.parts
    if ".claude" in parts and "archive" in parts:
        refuse(path, "claude archive")
        continue
    if resolved.suffix in PROTECTED_SUFFIXES or "migration" in parts:
        refuse(path, "database/migration")
        continue
    if not (path.exists() or path.is_symlink()):
        continue
    if path.is_file() and not path.is_symlink():
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = ""
        if BLOCK_BEGIN in content:
            strip_managed_block(path)
            continue
    path.unlink()
    print(f"removed:  {path}")

for raw in sorted(data.get("dirs_created", []), key=lambda value: -len(Path(value).parts)):
    directory = Path(raw)
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
        print(f"rmdir:    {directory}")

manifest_path.unlink()
print(f"removed:  {manifest_path}")
print("Done. dan.db, owner data and migration backups were left untouched.")
PY

echo ""
echo "If the launchd agent is loaded, boot it out deliberately with:"
echo "  scripts/uninstall-launchd.sh --yes"
