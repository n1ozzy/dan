"""H1 MenuBar shell: native NSStatusItem + NSPopover + WKWebView hosting
the SAME cockpit assets (PANEL_CONTRACT §5). Thin client — the shell adds
zero authority: it renders assets and injects the transport token the CLI
already reads; every intent still travels the cockpit's HTTP/WS routes.
"""

from __future__ import annotations

import builtins
import stat
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.panel import menubar_app
from jarvis.panel.menubar_app import (
    MenuBarApp,
    PanelShellError,
    ShellSettings,
    resolve_shell_settings,
    token_bootstrap_script,
)
from tests.test_api_smoke import write_config

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path):
    config_path = write_config(tmp_path / "jarvis.toml", tmp_path / "home" / "jarvis.db")
    return load_config(config_path)


class TestResolveShellSettings:
    def test_reads_panel_config_and_assets(self, tmp_path: Path) -> None:
        settings = resolve_shell_settings(_config(tmp_path))

        assert settings.api_base_url == "http://127.0.0.1:41741"
        assert settings.width == 420
        assert settings.height == 620
        assert settings.index_path == ROOT / "jarvis" / "panel" / "assets" / "index.html"
        assert settings.index_path.is_file()

    def test_reads_api_token_from_runtime_dir(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        runtime_dir = tmp_path / "home" / "runtime"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "api-token").write_text("cafe1234\n", encoding="utf-8")

        settings = resolve_shell_settings(config)

        assert settings.api_token == "cafe1234"

    def test_missing_token_resolves_to_none(self, tmp_path: Path) -> None:
        settings = resolve_shell_settings(_config(tmp_path))

        assert settings.api_token is None


class TestTokenBootstrapScript:
    def test_seeds_cockpit_local_storage_key(self) -> None:
        script = token_bootstrap_script("cafe1234")

        assert "jarvis-api-token" in script
        assert '"cafe1234"' in script
        assert "localStorage.setItem" in script

    def test_token_is_json_escaped(self) -> None:
        script = token_bootstrap_script('to"ken\\evil')

        assert 'to\\"ken\\\\evil' in script

    def test_none_token_yields_no_script(self) -> None:
        assert token_bootstrap_script(None) is None


class TestMenuBarAppGuard:
    def test_module_imports_without_pyobjc(self) -> None:
        # The module was imported at the top of this file; the real guard is
        # that it must not import AppKit/WebKit at module import time.
        source = (ROOT / "jarvis" / "panel" / "menubar_app.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            # Only top-level (column-0) imports run at module import time;
            # indented ones are the intended lazy imports inside functions.
            assert not line.startswith(("import AppKit", "from AppKit")), line
            assert not line.startswith(("import WebKit", "from WebKit")), line

    def test_run_without_pyobjc_raises_actionable_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object):
            if name in {"AppKit", "WebKit", "objc"}:
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        app = MenuBarApp(resolve_shell_settings(_config(tmp_path)))

        with pytest.raises(PanelShellError) as excinfo:
            app.run()

        assert ".[panel]" in str(excinfo.value)


class TestProbe:
    def test_probe_ok_when_pyobjc_and_assets_present(self, tmp_path: Path) -> None:
        assert menubar_app.probe(resolve_shell_settings(_config(tmp_path))) == 0

    def test_probe_2_when_pyobjc_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object):
            if name in {"AppKit", "WebKit", "objc"}:
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        assert menubar_app.probe(resolve_shell_settings(_config(tmp_path))) == 2

    def test_probe_2_when_assets_missing(self, tmp_path: Path) -> None:
        settings = ShellSettings(
            api_base_url="http://127.0.0.1:41741",
            api_token=None,
            index_path=tmp_path / "nope" / "index.html",
            width=420,
            height=620,
        )

        assert menubar_app.probe(settings) == 2


class TestPopoverAppearance:
    """The cockpit is dark-only (`color-scheme: dark`); the popover chrome
    must follow, or the light-mode arrow/flash clashes with the content.
    GUI construction needs a display, so this is a source contract, same
    idiom as the lazy-import guard above."""

    def test_popover_forces_dark_appearance(self) -> None:
        source = (ROOT / "jarvis" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

        assert "NSAppearanceNameDarkAqua" in source
        assert "setAppearance_" in source

    def test_webview_underlay_matches_cockpit_background(self) -> None:
        source = (ROOT / "jarvis" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

        assert "setUnderPageBackgroundColor_" in source


class TestStatusIcon:
    def test_icon_asset_exists_in_panel_assets(self) -> None:
        path = menubar_app.status_icon_path()

        assert path == ROOT / "jarvis" / "panel" / "assets" / "menubar-icon.png"
        assert path.is_file()

    def test_icon_is_a_png(self) -> None:
        head = menubar_app.status_icon_path().read_bytes()[:8]

        assert head == b"\x89PNG\r\n\x1a\n"


class TestRunbook:
    def test_runbook_documents_install_run_and_boundaries(self) -> None:
        text = (ROOT / "docs" / "runbooks" / "PANEL_MENUBAR.md").read_text(encoding="utf-8")
        lowered = text.lower()

        assert ".[panel]" in text
        assert "scripts/jarvis-panel" in text
        assert "thin client" in lowered
        assert "adr-002" in lowered
        assert "quit" in lowered


class TestLauncher:
    def test_launcher_script_execs_menubar_module(self) -> None:
        launcher = ROOT / "scripts" / "jarvis-panel"
        text = launcher.read_text(encoding="utf-8")

        assert "jarvis.panel.menubar_app" in text
        assert launcher.stat().st_mode & stat.S_IXUSR

    def test_launcher_never_touches_daemon_state(self) -> None:
        text = (ROOT / "scripts" / "jarvis-panel").read_text(encoding="utf-8")

        assert "jarvis.db" not in text
        assert "/tmp" not in text
