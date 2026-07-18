"""H1 MenuBar shell: native NSStatusItem + borderless NSPanel + WKWebView
hosting the SAME cockpit assets (PANEL_CONTRACT §5). Thin client — the shell
adds zero authority: it renders assets and injects the transport token the
CLI already reads; every intent still travels the cockpit's HTTP/WS routes.
"""

from __future__ import annotations

import builtins
import stat
from pathlib import Path

import pytest

from dan.config import load_config
from dan.panel import menubar_app
from dan.panel.menubar_app import (
    MenuBarApp,
    PanelShellError,
    ShellSettings,
    panel_index_url,
    resolve_shell_settings,
    token_bootstrap_script,
)
from tests.test_api_smoke import write_config

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path):
    config_path = write_config(tmp_path / "dan.toml", tmp_path / "home" / "dan.db")
    return load_config(config_path)


class TestResolveShellSettings:
    def test_reads_panel_config_and_assets(self, tmp_path: Path) -> None:
        settings = resolve_shell_settings(_config(tmp_path))

        assert settings.api_base_url == "http://127.0.0.1:41741"
        assert settings.width == 420
        assert settings.height == 620
        assert settings.index_path == ROOT / "dan" / "panel" / "assets" / "index.html"
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
    def test_seeds_api_base_for_native_panel(self) -> None:
        script = token_bootstrap_script(
            "cafe1234",
            api_base_url="http://127.0.0.1:41888/",
        )

        assert "window.DAN_API_BASE" in script
        assert '"http://127.0.0.1:41888"' in script

    def test_seeds_cockpit_local_storage_key(self) -> None:
        script = token_bootstrap_script("cafe1234")

        assert "dan-api-token" in script
        assert '"cafe1234"' in script
        assert "localStorage.setItem" in script

    def test_token_is_json_escaped(self) -> None:
        script = token_bootstrap_script('to"ken\\evil')

        assert 'to\\"ken\\\\evil' in script

    def test_none_token_yields_no_script(self) -> None:
        assert token_bootstrap_script(None) is None


class TestMenuBarAppGuard:
    def test_panel_index_url_is_served_by_daemon_origin(self) -> None:
        assert (
            panel_index_url("http://127.0.0.1:41741/")
            == "http://127.0.0.1:41741/panel/index.html"
        )

    def test_module_imports_without_pyobjc(self) -> None:
        # The module was imported at the top of this file; the real guard is
        # that it must not import AppKit/WebKit at module import time.
        source = (ROOT / "dan" / "panel" / "menubar_app.py").read_text(encoding="utf-8")
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
        # This test requires PyObjC; skip if not available (e.g. in CI)
        try:
            import AppKit  # type: ignore
            import WebKit  # type: ignore
            import objc  # type: ignore
        except ImportError:
            pytest.skip("PyObjC not installed")

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


class TestPanelAppearance:
    """The cockpit is dark-only (`color-scheme: dark`); the widget chrome
    must follow, or a light-mode flash clashes with the content.
    GUI construction needs a display, so this is a source contract, same
    idiom as the lazy-import guard above."""

    def test_panel_forces_dark_appearance(self) -> None:
        source = (ROOT / "dan" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

        assert "NSAppearanceNameDarkAqua" in source
        assert "setAppearance_" in source

    def test_webview_underlay_matches_cockpit_background(self) -> None:
        source = (ROOT / "dan" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

        assert "setUnderPageBackgroundColor_" in source

    def test_edit_menu_wires_standard_shortcuts(self) -> None:
        # Bez menu Edit macOS nie routuje ⌘A/⌘C/⌘V/⌘X do pól webview.
        source = (ROOT / "dan" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

        assert "setMainMenu_" in source
        for selector in ('"copy:"', '"paste:"', '"cut:"', '"selectAll:"'):
            assert selector in source, selector


class TestWidgetPanel:
    """Karta widżetu = własny borderless NSPanel, nie NSPopover. Popover
    dokładał systemowy bąbel (druga krawędź, strzałka, mismatch promieni)
    pod naszą ramką stanu; własny panel ma JEDNĄ geometrię: warstwa webview
    z cornerRadius + 2pt borderem stanu, cień od okna, zero strzałki."""

    def _source(self) -> str:
        return (ROOT / "dan" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

    def test_shell_hosts_borderless_nonactivating_panel_not_popover(self) -> None:
        source = self._source()

        assert "NSPopover" not in source
        assert "NSWindowStyleMaskBorderless" in source
        assert "NSWindowStyleMaskNonactivatingPanel" in source
        assert "NSStatusWindowLevel" in source
        assert "setHasShadow_" in source

    def test_panel_card_owns_single_border_geometry(self) -> None:
        source = self._source()

        assert "PANEL_CORNER_RADIUS = 12.0" in source
        assert "setCornerRadius_" in source
        assert "setMasksToBounds_" in source
        # Przezroczyste okno: ramkę i promień rysuje wyłącznie warstwa karty.
        assert "setOpaque_(False)" in source
        assert "clearColor" in source

    def test_panel_can_take_keyboard_focus(self) -> None:
        # Borderless okna domyślnie nie mogą być key — bez tego override
        # textarea kompozytora nie przyjmie ani jednego znaku.
        assert "canBecomeKeyWindow" in self._source()

    def test_panel_hides_on_resign_key_without_global_monitors(self) -> None:
        # Task 9: dand jest jedynym globalnym obserwatorem zdarzeń — panel
        # chowa kartę wyłącznie delegatem okna (utrata key window), bez
        # żadnego monitora NSEvent (globalnego ani lokalnego).
        source = self._source()

        assert "windowDidResignKey" in source
        assert "NSEventMaskLeftMouseDown" not in source
        assert "addGlobalMonitorForEventsMatchingMask" not in source
        assert "addLocalMonitorForEventsMatchingMask" not in source

    def test_panel_positions_under_status_item(self) -> None:
        # Pozycja z ekranu ikony (frame okna przycisku status itemu),
        # wycentrowana pod ikoną i przypięta pod paskiem menu.
        assert "visibleFrame" in self._source()


class TestStateBorder:
    """Żywa ramka stanu OBIEGA dookoła, gdy DAN pracuje, i barwi się
    stanem — a to wymaga gradientu maskowanego do obrysu, którego płaski
    `CALayer.border` nie potrafi. Więc ramkę rysuje CSS w webview (sterowany
    realnym stanem z JS, który cockpit i tak pobiera), a natywna warstwa robi
    tylko zaokrąglony clip i cień okna. Powłoka nie odpytuje /health o kolor —
    jedno źródło stanu (JS cockpitu), jedna warstwa renderingu."""

    def _source(self) -> str:
        return (ROOT / "dan" / "panel" / "menubar_app.py").read_text(encoding="utf-8")

    def test_native_layer_only_clips_it_does_not_paint_state_color(self) -> None:
        source = self._source()

        assert "setCornerRadius_" in source
        assert "setMasksToBounds_" in source
        # Kolor/animacja ramki żyją w CSS, nie na natywnej warstwie.
        assert "setBorderColor_" not in source
        assert "BORDER_STATE_COLORS" not in source

    def test_shell_does_not_poll_health_for_border_color(self) -> None:
        source = self._source()

        # Poller /health -> kolor warstwy zniknął; stan bierze JS z danych,
        # które cockpit i tak pobiera (/health, /state, /voice, /stream).
        assert "classify_daemon_state" not in source
        assert "fetch_daemon_status" not in source
        assert "urllib" not in source

    def test_state_frame_lives_in_cockpit_css_not_native_layer(self) -> None:
        styles = (
            ROOT / "dan" / "panel" / "assets" / "styles.css"
        ).read_text(encoding="utf-8")
        markup = (
            ROOT / "dan" / "panel" / "assets" / "index.html"
        ).read_text(encoding="utf-8")

        assert "state-frame" in markup
        assert "conic-gradient" in styles


class TestStatusIcon:
    def test_icon_asset_exists_in_panel_assets(self) -> None:
        path = menubar_app.status_icon_path()

        assert path == ROOT / "dan" / "panel" / "assets" / "menubar-icon.png"
        assert path.is_file()

    def test_icon_is_a_png(self) -> None:
        head = menubar_app.status_icon_path().read_bytes()[:8]

        assert head == b"\x89PNG\r\n\x1a\n"


class TestRunbook:
    def test_runbook_documents_install_run_and_boundaries(self) -> None:
        text = (ROOT / "docs" / "runbooks" / "PANEL_MENUBAR.md").read_text(encoding="utf-8")
        lowered = text.lower()

        assert ".[panel]" in text
        assert "scripts/dan-panel" in text
        assert "thin client" in lowered
        assert "adr-002" in lowered
        assert "quit" in lowered


class TestLauncher:
    def test_launcher_script_execs_menubar_module(self) -> None:
        launcher = ROOT / "scripts" / "dan-panel"
        text = launcher.read_text(encoding="utf-8")

        assert "dan.panel.menubar_app" in text
        assert launcher.stat().st_mode & stat.S_IXUSR

    def test_launcher_never_touches_daemon_state(self) -> None:
        text = (ROOT / "scripts" / "dan-panel").read_text(encoding="utf-8")

        assert "dan.db" not in text
        assert "/tmp" not in text

    def test_launcher_rejects_example_config_as_runtime(self) -> None:
        text = (ROOT / "scripts" / "dan-panel").read_text(encoding="utf-8")

        assert "config/dan.example.toml is not a runtime config" in text
