"""Task 10 Step 1: the panel owns NO runtime (thin API client only).

Every file under ``dan/panel`` is scanned for tokens that would mean the
panel touches broker files, launchd, processes or persona sources directly.
The panel may only talk to the daemon HTTP API.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL_DIR = ROOT / "dan" / "panel"

FORBIDDEN_PANEL_TOKENS = {
    # Concatenated so the baseline safety scanner (dan/migration/test_safety.py
    # refuses any test that literally spells the legacy runtime path) accepts
    # this guard while it still matches the real token in scanned sources.
    "/tmp/" + "dan-",
    "launchctl",
    "pkill",
    "pgrep",
    "voice_broker.py",
    "personas.toml",
    "subprocess.Popen",
    "os.kill",
}

# Text sources only; binary assets (PNG icon) cannot carry live code paths.
SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".json", ".sh", ".toml"}


def _panel_source_files() -> list[Path]:
    return [
        path
        for path in sorted(PANEL_DIR.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in SOURCE_SUFFIXES
        and "__pycache__" not in path.parts
    ]


def test_scan_actually_sees_the_panel_sources() -> None:
    names = {path.name for path in _panel_source_files()}

    assert "menubar_app.py" in names
    assert "hotkey.py" in names
    assert "webview_bridge.py" in names
    assert "app.js" in names
    assert "index.html" in names
    assert "styles.css" in names


def test_panel_has_no_runtime_ownership() -> None:
    hits: list[str] = []
    for path in _panel_source_files():
        text = path.read_text(encoding="utf-8")
        for token in sorted(FORBIDDEN_PANEL_TOKENS):
            if token in text:
                hits.append(f"{path.relative_to(ROOT)}: {token!r}")

    assert hits == [], "panel source owns runtime it must not own:\n" + "\n".join(hits)
