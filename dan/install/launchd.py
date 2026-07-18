"""Product launchd assets: exactly one label, com.dan.dand.

Legacy jobs (com.ozzy.jarvisd, com.dan.voice-broker, com.ozzy.voice-standup,
com.ozzy.menubar-controller) are NOT product labels; they are retired only by
the journaled cutover (Task 12/14), never by install.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLIST_EXAMPLE = REPO_ROOT / "launchd" / "com.dan.dand.plist.example"

PRODUCT_LABEL = "com.dan.dand"
PRODUCT_NAMESPACE = "com.dan."
# Known legacy labels that squat the com.dan. namespace but are not product.
LEGACY_NAMESPACE_LABELS = frozenset({"com.dan.voice-broker", "com.dan.xtts-server"})


def render_plist(home: Path) -> bytes:
    """Render the example plist for a concrete HOME."""

    text = PLIST_EXAMPLE.read_text(encoding="utf-8")
    if "__HOME__" not in text:
        raise ValueError(f"plist example lost its __HOME__ placeholder: {PLIST_EXAMPLE}")
    return text.replace("__HOME__", str(home)).encode("utf-8")


def product_launchd_labels(agents_dir: Path) -> list[str]:
    """Labels in agents_dir that claim the product namespace.

    A correct install yields exactly ["com.dan.dand"]. A stray second
    com.dan.* job counts as a product claim and fails the single-owner test.
    """

    labels: list[str] = []
    if not agents_dir.is_dir():
        return labels
    for path in sorted(agents_dir.glob("*.plist")):
        try:
            data = plistlib.loads(path.read_bytes())
        except (plistlib.InvalidFileException, ValueError, OSError):
            continue
        label = data.get("Label")
        if not isinstance(label, str):
            continue
        if label == PRODUCT_LABEL or (
            label.startswith(PRODUCT_NAMESPACE) and label not in LEGACY_NAMESPACE_LABELS
        ):
            labels.append(label)
    return sorted(labels)


__all__ = [
    "LEGACY_NAMESPACE_LABELS",
    "PLIST_EXAMPLE",
    "PRODUCT_LABEL",
    "product_launchd_labels",
    "render_plist",
]
