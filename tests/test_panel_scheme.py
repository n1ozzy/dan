"""Panel asset resolution for daemon-served native shell assets.

These cover the pure ``resolve_panel_asset`` mapping (path -> file + MIME,
traversal guard). The daemon exposes these files under ``/panel/...`` so the
native WebView and API share one localhost origin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.panel.menubar_app import ASSETS_DIR, resolve_panel_asset


def test_root_path_serves_index_html() -> None:
    path, mime = resolve_panel_asset("/")
    assert path == (ASSETS_DIR / "index.html").resolve()
    assert mime == "text/html; charset=utf-8"


def test_named_asset_resolves_with_expected_mime() -> None:
    path, mime = resolve_panel_asset("/app.js")
    assert path == (ASSETS_DIR / "app.js").resolve()
    assert mime == "text/javascript; charset=utf-8"


def test_missing_asset_returns_none() -> None:
    path, _mime = resolve_panel_asset("/does-not-exist.js")
    assert path is None


@pytest.mark.parametrize(
    "attack",
    ["/../lifecycle.py", "/../../dan/daemon/lifecycle.py", "/../__init__.py"],
)
def test_path_traversal_escapes_root_and_is_refused(attack: str) -> None:
    path, _mime = resolve_panel_asset(attack)
    assert path is None


def test_resolver_honours_a_custom_assets_dir(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    path, mime = resolve_panel_asset("/index.html", assets_dir=tmp_path)
    assert path == (tmp_path / "index.html").resolve()
    assert mime == "text/html; charset=utf-8"
