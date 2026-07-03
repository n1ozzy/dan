"""macOS menu-bar shell for the cockpit (PANEL_CONTRACT §5, H1).

NSStatusItem + NSPopover + WKWebView rendering the SAME static cockpit
assets the browser uses. The shell owns no state and adds no authority:
it loads `jarvis/panel/assets/index.html` and seeds the transport token
the CLI already reads — every intent still travels the cockpit's own
HTTP/WS routes (thin client, ADR-002).

AppKit/WebKit imports are lazy: the module must import (and the test
suite must run) without PyObjC installed. Install the GUI extra with
`pip install -e '.[panel]'`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import JarvisConfig, load_config
from jarvis.panel.hotkey import HotkeyEdgeDetector, PttHotkeyClient, parse_hotkey
from jarvis.paths import resolve_runtime_paths
from jarvis.security.transport import load_api_token

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Must match API_TOKEN_STORAGE_KEY in jarvis/panel/assets/app.js.
COCKPIT_TOKEN_STORAGE_KEY = "jarvis-api-token"

# Menu-bar display height in points; the PNG carries 2x pixels for retina.
STATUS_ICON_HEIGHT = 18.0


def status_icon_path() -> Path:
    """Template wordmark for the status item (black + alpha; AppKit
    recolors template images for light/dark menu bars)."""

    return ASSETS_DIR / "menubar-icon.png"


class PanelShellError(Exception):
    """Raised when the menu-bar shell cannot be built or run."""


@dataclass(frozen=True)
class ShellSettings:
    api_base_url: str
    api_token: str | None
    index_path: Path
    width: int
    height: int
    ptt_hotkey: str = ""


def resolve_shell_settings(config: JarvisConfig) -> ShellSettings:
    paths = resolve_runtime_paths(config)
    return ShellSettings(
        api_base_url=config.panel.api_base_url,
        api_token=load_api_token(paths.runtime_dir),
        index_path=ASSETS_DIR / "index.html",
        width=int(config.panel.width),
        height=int(config.panel.height),
        ptt_hotkey=str(config.voice.ptt_hotkey),
    )


def token_bootstrap_script(token: str | None) -> str | None:
    """JS injected at document start so the cockpit finds the transport
    token without prompting. json.dumps escapes the value — the token is
    data, never script."""

    if token is None:
        return None
    return (
        f"window.localStorage.setItem({json.dumps(COCKPIT_TOKEN_STORAGE_KEY)}, "
        f"{json.dumps(token)});"
    )


def _import_gui_modules() -> tuple[object, object]:
    try:
        import AppKit
        import WebKit
    except ImportError as exc:
        raise PanelShellError(
            "PyObjC is not installed in this environment. Install the panel "
            "extra first: .venv/bin/pip install -e '.[panel]'"
        ) from exc
    return AppKit, WebKit


def probe(settings: ShellSettings) -> int:
    """0 = shell can run (PyObjC importable, cockpit assets on disk); 2 = not."""

    try:
        _import_gui_modules()
    except PanelShellError as exc:
        print(f"panel probe: {exc}", file=sys.stderr)
        return 2
    if not settings.index_path.is_file():
        print(f"panel probe: cockpit assets missing: {settings.index_path}", file=sys.stderr)
        return 2
    print(f"panel probe: ok (assets: {settings.index_path})")
    return 0


class MenuBarApp:
    def __init__(self, settings: ShellSettings):
        self._settings = settings
        # Strong references so ObjC objects outlive the setup calls.
        self._status_item = None
        self._popover = None
        self._controller = None
        self._hotkey_monitors: list = []

    def run(self) -> None:
        AppKit, WebKit = _import_gui_modules()
        if not self._settings.index_path.is_file():
            raise PanelShellError(
                f"Cockpit assets missing: {self._settings.index_path}"
            )

        app = AppKit.NSApplication.sharedApplication()
        # Accessory: menu-bar only, no Dock icon, no app switcher entry.
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        self._popover = self._build_popover(AppKit, WebKit)
        self._controller = self._build_controller(AppKit)
        self._status_item = self._build_status_item(AppKit, self._controller)
        self._install_hotkey_monitors(AppKit)

        app.run()

    def _install_hotkey_monitors(self, AppKit):  # noqa: N803 - ObjC module name
        """Watch a held modifier combo anywhere and drive PTT down/up.

        A flagsChanged monitor (global = other apps focused, local = our
        popover focused) feeds NSEvent.modifierFlags() — masked to the low 16
        device-dependent bits so left/right are distinguished — through the
        edge detector. Needs macOS Accessibility permission; without it the
        global monitor silently sees nothing (the local one still works while
        the panel is focused). A blank/zero hotkey installs nothing.
        """

        mask = parse_hotkey(self._settings.ptt_hotkey)
        if mask == 0:
            return
        detector = HotkeyEdgeDetector(mask)
        client = PttHotkeyClient(
            self._settings.api_base_url, self._settings.api_token
        )

        def handler(event):
            device_flags = int(event.modifierFlags()) & 0xFFFF
            client.dispatch(detector.update(device_flags))
            return event  # local monitor forwards the event; global ignores it

        flags_changed = AppKit.NSEventMaskFlagsChanged
        self._hotkey_monitors.append(
            AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                flags_changed, handler
            )
        )
        self._hotkey_monitors.append(
            AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                flags_changed, handler
            )
        )
        print(
            f"panel: globalny PTT hotkey aktywny ({self._settings.ptt_hotkey}). "
            "Jesli trzymanie skrotu nie wlacza nasluchu: Ustawienia systemowe > "
            "Prywatnosc i ochrona > Dostepnosc — wlacz aplikacje uruchamiajaca "
            "panel (Terminal/Python), potem zrestartuj panel.",
            file=sys.stderr,
        )

    def _build_popover(self, AppKit, WebKit):  # noqa: N803 - ObjC module names
        configuration = WebKit.WKWebViewConfiguration.alloc().init()
        bootstrap = token_bootstrap_script(self._settings.api_token)
        if bootstrap is not None:
            script = WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                bootstrap,
                WebKit.WKUserScriptInjectionTimeAtDocumentStart,
                True,
            )
            configuration.userContentController().addUserScript_(script)

        frame = AppKit.NSMakeRect(0, 0, self._settings.width, self._settings.height)
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(frame, configuration)
        # Match the cockpit's page background (#0e1116) so load and
        # rubber-band overscroll never flash a light underlay.
        webview.setUnderPageBackgroundColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                0x0E / 255, 0x11 / 255, 0x16 / 255, 1.0
            )
        )
        index_url = AppKit.NSURL.fileURLWithPath_(str(self._settings.index_path))
        assets_url = AppKit.NSURL.fileURLWithPath_(str(self._settings.index_path.parent))
        webview.loadFileURL_allowingReadAccessToURL_(index_url, assets_url)

        view_controller = AppKit.NSViewController.alloc().init()
        view_controller.setView_(webview)

        popover = AppKit.NSPopover.alloc().init()
        popover.setContentViewController_(view_controller)
        popover.setContentSize_(
            AppKit.NSMakeSize(self._settings.width, self._settings.height)
        )
        popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
        # The cockpit is dark-only (`color-scheme: dark`); pin the popover
        # chrome (arrow, edges) to dark so a light-mode menu bar does not
        # frame the dark content in white.
        popover.setAppearance_(
            AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
        )
        return popover

    def _build_controller(self, AppKit):  # noqa: N803
        shell = self

        class JarvisPanelController(AppKit.NSObject):
            def togglePanel_(self, sender):  # noqa: N802 - ObjC selector
                event = AppKit.NSApplication.sharedApplication().currentEvent()
                if event is not None and event.type() == AppKit.NSEventTypeRightMouseUp:
                    shell._show_quit_menu(AppKit)
                    return
                shell._toggle_popover(AppKit)

        return JarvisPanelController.alloc().init()

    def _build_status_item(self, AppKit, controller):  # noqa: N803
        status_bar = AppKit.NSStatusBar.systemStatusBar()
        item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = item.button()
        icon = self._load_status_icon(AppKit)
        if icon is not None:
            button.setImage_(icon)
        else:
            button.setTitle_("J")
        button.setToolTip_("Jarvis panel")
        button.setTarget_(controller)
        button.setAction_("togglePanel:")
        button.sendActionOn_(
            AppKit.NSEventMaskLeftMouseUp | AppKit.NSEventMaskRightMouseUp
        )
        return item

    def _load_status_icon(self, AppKit):  # noqa: N803
        path = status_icon_path()
        if not path.is_file():
            return None
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
        if image is None:
            return None
        pixel_w, pixel_h = image.size().width, image.size().height
        if pixel_h > 0:
            scale = STATUS_ICON_HEIGHT / pixel_h
            image.setSize_(AppKit.NSMakeSize(pixel_w * scale, STATUS_ICON_HEIGHT))
        image.setTemplate_(True)
        return image

    def _toggle_popover(self, AppKit):  # noqa: N803
        button = self._status_item.button()
        if self._popover.isShown():
            self._popover.performClose_(None)
            return
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, AppKit.NSRectEdgeMinY
        )

    def _show_quit_menu(self, AppKit):  # noqa: N803
        menu = AppKit.NSMenu.alloc().init()
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Jarvis Panel", "terminate:", "q"
        )
        menu.addItem_(quit_item)
        # Non-deprecated popup trick: attach the menu, synthesize a click so
        # AppKit opens it, then detach so left-click keeps toggling the popover.
        self._status_item.setMenu_(menu)
        self._status_item.button().performClick_(None)
        self._status_item.setMenu_(None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-panel")
    parser.add_argument("--config", help="Path to a Jarvis TOML config file")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Exit 0 when the shell can run (PyObjC + assets), 2 otherwise",
    )
    args = parser.parse_args(argv)

    settings = resolve_shell_settings(load_config(args.config))
    if args.probe:
        return probe(settings)

    try:
        MenuBarApp(settings).run()
    except PanelShellError as exc:
        print(f"PanelShellError: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
