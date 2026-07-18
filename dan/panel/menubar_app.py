"""macOS menu-bar shell for the cockpit (PANEL_CONTRACT §5, H1).

NSStatusItem + borderless NSPanel + WKWebView rendering the SAME static
cockpit assets the browser uses. The shell owns no state and adds no
authority: it loads `dan/panel/assets/index.html` and seeds the
transport token the CLI already reads — every intent still travels the
cockpit's own HTTP/WS routes (thin client, ADR-002).

AppKit/WebKit imports are lazy: the module must import (and the test
suite must run) without PyObjC installed. Install the GUI extra with
`pip install -e '.[panel]'`.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dan.config import DANConfig, load_config
# Task 9: the panel owns NO global key/mouse monitor anymore — the daemon's
# MacOSHotkeyMonitor (dan/input/macos_event_tap.py) is the one event tap on
# the machine, PTT activation grace included. The panel only displays hotkey
# state and posts the manual PTT intent from the web UI.
from dan.panel.hotkey import accessibility_trust_state, fetch_effective_hotkey
from dan.paths import resolve_runtime_paths
from dan.security.transport import load_api_token

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# MIME types for the handful of static files the cockpit ships. Kept explicit
# (not mimetypes.guess_type) so the contract is visible and deterministic.
_ASSET_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
}
_DEFAULT_ASSET_MIME = "application/octet-stream"


def resolve_panel_asset(url_path: str, assets_dir: Path = ASSETS_DIR) -> tuple[Path | None, str]:
    """Map a daemon ``/panel`` asset path to a file under ``assets_dir``.

    Returns ``(path, mime)`` for a real file inside the assets root, or
    ``(None, mime)`` when the target is missing or escapes the root (path
    traversal). The daemon answers ``None`` with a 404 — the panel asset route
    must never be able to read outside its bundle.
    """

    root = assets_dir.resolve()
    relative = url_path.lstrip("/") or "index.html"
    candidate = (root / relative).resolve()
    mime = _ASSET_MIME_TYPES.get(candidate.suffix.lower(), _DEFAULT_ASSET_MIME)

    if root != candidate and root not in candidate.parents:
        return None, mime  # escaped the assets root
    if not candidate.is_file():
        return None, mime
    return candidate, mime

# Must match API_TOKEN_STORAGE_KEY in dan/panel/assets/app.js.
COCKPIT_TOKEN_STORAGE_KEY = "dan-api-token"

# Menu-bar display height in points; the PNG carries 2x pixels for retina.
STATUS_ICON_HEIGHT = 40.0

# --- Widget card ------------------------------------------------------------
# The card is its own borderless NSPanel: a transparent window with the system
# shadow, whose webview layer does the rounded clip (ONE geometry, no system
# popover bubble — a second edge, an arrow, a gap). The live state frame —
# the neon that RUNS AROUND while DAN thinks/works, and takes on the state
# color — is drawn by CSS in the webview, driven by the real state from JS
# (the cockpit fetches /health, /state, /voice, /stream anyway). A flat
# CALayer.border could do neither the running light nor the gradient; the
# shell paints no color.
PANEL_CORNER_RADIUS = 12.0
# Gap between the card and the bottom of the menu bar, in points.
PANEL_TOP_GAP = 6.0
# A click on the icon while the panel is open: mousedown can first take key
# status away from the panel (we hide it), and mouseup fires togglePanel —
# without this time window the panel would close and immediately reopen.
PANEL_REOPEN_SUPPRESS_SECONDS = 0.3


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


def resolve_shell_settings(config: DANConfig) -> ShellSettings:
    paths = resolve_runtime_paths(config)
    return ShellSettings(
        api_base_url=config.panel.api_base_url,
        api_token=load_api_token(paths.runtime_dir),
        index_path=ASSETS_DIR / "index.html",
        width=int(config.panel.width),
        height=int(config.panel.height),
        ptt_hotkey=str(config.voice.ptt_hotkey),
    )


def panel_index_url(api_base_url: str) -> str:
    return f"{api_base_url.rstrip('/')}/panel/index.html"


def token_bootstrap_script(
    token: str | None,
    *,
    api_base_url: str | None = None,
) -> str | None:
    """JS injected at document start so the cockpit finds native settings.

    json.dumps escapes the values — config and token are data, never script.
    """

    statements: list[str] = []
    if api_base_url is not None:
        statements.append(
            f"window.DAN_API_BASE = {json.dumps(api_base_url.rstrip('/'))};"
        )
    if token is not None:
        statements.append(
            f"window.localStorage.setItem({json.dumps(COCKPIT_TOKEN_STORAGE_KEY)}, "
            f"{json.dumps(token)});"
        )
    if not statements:
        return None
    return "".join(statements)


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
        self._panel = None
        self._controller = None
        self._webview = None
        self._hidden_at = 0.0
        self._shown_at = 0.0

    def run(self) -> None:
        AppKit, WebKit = _import_gui_modules()
        if not self._settings.index_path.is_file():
            raise PanelShellError(
                f"Cockpit assets missing: {self._settings.index_path}"
            )

        app = AppKit.NSApplication.sharedApplication()
        # Accessory: menu-bar only, no Dock icon, no app switcher entry.
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        self._install_edit_menu(AppKit, app)
        self._controller = self._build_controller(AppKit)
        self._panel = self._build_panel(AppKit, WebKit)
        self._status_item = self._build_status_item(AppKit, self._controller)
        self._report_hotkey_state()

        app.run()

    def _report_hotkey_state(self) -> None:
        """Display-only hotkey status (Task 9): dand owns the global tap.

        The panel installs no NSEvent monitor of any kind — it just tells the
        operator what combo the daemon binds and whether Accessibility trust
        (now needed by the *daemon* executable, not the panel) looks healthy.
        """

        spec = (
            fetch_effective_hotkey(
                self._settings.api_base_url, self._settings.api_token
            )
            or self._settings.ptt_hotkey
            or "(none)"
        )
        trust = accessibility_trust_state()
        print(
            f"panel: the global PTT hotkey ({spec}) is handled by the dand daemon — "
            f"the panel only displays its state (Accessibility: {trust}). The HOLD "
            "button in the panel works regardless of permissions.",
            file=sys.stderr,
        )

    def _install_edit_menu(self, AppKit, app):  # noqa: N803 - ObjC module name
        """Standard edit shortcuts (⌘A/⌘C/⌘V/⌘X/⌘Z) in webview fields.

        Without a main menu carrying an Edit item, macOS does not route these
        keyEquivalents to the first responder (the composer textarea), so
        copy/paste/select-all do not work. An accessory app shows no menu bar,
        but mainMenu still processes shortcuts while the card has focus — the
        actions land in WKWebView, which implements copy:/paste:/selectAll:. """

        main_menu = AppKit.NSMenu.alloc().init()
        edit_container = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(edit_container)
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
        edit_container.setSubmenu_(edit_menu)

        # (title, selector, key) — an uppercase letter implies ⇧ (Redo).
        entries = [
            ("Undo", "undo:", "z"),
            ("Redo", "redo:", "Z"),
            (None, None, None),
            ("Cut", "cut:", "x"),
            ("Copy", "copy:", "c"),
            ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a"),
        ]
        for title, action, key in entries:
            if title is None:
                edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, key
            )
            edit_menu.addItem_(item)

        app.setMainMenu_(main_menu)

    def _build_panel(self, AppKit, WebKit):  # noqa: N803 - ObjC module names
        configuration = WebKit.WKWebViewConfiguration.alloc().init()
        # Load the cockpit from the daemon origin so WebKit fetches API routes
        # same-origin. Custom-scheme pages can send localhost requests, but newer
        # WebKit builds still reject exposing those responses to fetch().
        bootstrap = token_bootstrap_script(
            self._settings.api_token,
            api_base_url=self._settings.api_base_url,
        )
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
        index_url = AppKit.NSURL.URLWithString_(panel_index_url(self._settings.api_base_url))
        webview.loadRequest_(AppKit.NSURLRequest.requestWithURL_(index_url))

        # The webview layer only does the rounded card clip (so the window has
        # soft corners and a matching shadow). The state frame — color and
        # animation — is drawn by CSS in the webview, not this layer: one
        # geometry, one source.
        webview.setWantsLayer_(True)
        layer = webview.layer()
        if layer is not None:
            layer.setCornerRadius_(PANEL_CORNER_RADIUS)
            layer.setMasksToBounds_(True)
        self._webview = webview

        shell = self

        class DANWidgetPanel(AppKit.NSPanel):
            def canBecomeKeyWindow(self):  # noqa: N802 - ObjC selector
                # Borderless windows refuse key status by default — without
                # this the composer textarea would not accept a single character.
                return True

            def windowDidResignKey_(self, _notification):  # noqa: N802
                shell._note_resign_key()

        panel = DANWidgetPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        # The window is transparent: the radius and the frame are drawn solely
        # by the webview layer (one geometry), the window adds only the system shadow.
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        # The cockpit is dark-only (`color-scheme: dark`); pin the panel
        # chrome to dark so a light-mode desktop does not tint the card.
        panel.setAppearance_(
            AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
        )
        panel.setContentView_(webview)
        panel.setDelegate_(panel)
        return panel

    def _build_controller(self, AppKit):  # noqa: N803
        shell = self

        class DANPanelController(AppKit.NSObject):
            def togglePanel_(self, sender):  # noqa: N802 - ObjC selector
                event = AppKit.NSApplication.sharedApplication().currentEvent()
                if event is not None and event.type() == AppKit.NSEventTypeRightMouseUp:
                    shell._show_quit_menu(AppKit)
                    return
                shell._toggle_panel(AppKit)

        return DANPanelController.alloc().init()

    def _build_status_item(self, AppKit, controller):  # noqa: N803
        status_bar = AppKit.NSStatusBar.systemStatusBar()
        item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = item.button()
        icon = self._load_status_icon(AppKit)
        if icon is not None:
            button.setImage_(icon)
        else:
            button.setTitle_("J")
        button.setToolTip_("DAN panel")
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

    def _toggle_panel(self, AppKit):  # noqa: N803
        if self._panel.isVisible():
            self._hide_panel()
            return
        if time.monotonic() - self._hidden_at < PANEL_REOPEN_SUPPRESS_SECONDS:
            # The same click that just hid the panel (resignKey on
            # mousedown) — do not reopen it on mouseup.
            return
        self._show_panel(AppKit)

    def _show_panel(self, AppKit):  # noqa: N803
        """The card lands centered under the menubar icon, pinned below the
        menu bar, clamped to the visible screen edge."""

        button = self._status_item.button()
        button_window = button.window()
        anchor = button_window.frame()
        width = float(self._settings.width)
        height = float(self._settings.height)

        x = anchor.origin.x + anchor.size.width / 2.0 - width / 2.0
        y = anchor.origin.y - PANEL_TOP_GAP - height

        screen = button_window.screen() or AppKit.NSScreen.mainScreen()
        if screen is not None:
            visible = screen.visibleFrame()
            min_x = visible.origin.x + PANEL_TOP_GAP
            max_x = visible.origin.x + visible.size.width - width - PANEL_TOP_GAP
            x = max(min_x, min(x, max_x))

        self._panel.setFrame_display_(
            AppKit.NSMakeRect(x, y, width, height), True
        )
        # An accessory + nonactivating panel does not get keyboard focus on
        # its own, and without a key window the composer textarea accepts no
        # character. We activate the app (no Dock/menu — we are an accessory),
        # then make the panel key.
        self._shown_at = time.monotonic()
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        self._panel.orderFrontRegardless()

    def _hide_panel(self) -> None:
        self._hidden_at = time.monotonic()
        self._panel.orderOut_(None)

    def _note_resign_key(self) -> None:
        # Losing the key window hides the card (a click into another app). But
        # a freshly shown panel can resign key once before activation stabilizes
        # — without this window the card would close immediately after opening.
        if self._panel is None or not self._panel.isVisible():
            return
        if time.monotonic() - self._shown_at < PANEL_REOPEN_SUPPRESS_SECONDS:
            return
        self._hide_panel()

    def _show_quit_menu(self, AppKit):  # noqa: N803
        menu = AppKit.NSMenu.alloc().init()
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit DAN Panel", "terminate:", "q"
        )
        menu.addItem_(quit_item)
        # Non-deprecated popup trick: attach the menu, synthesize a click so
        # AppKit opens it, then detach so left-click keeps toggling the panel.
        self._status_item.setMenu_(menu)
        self._status_item.button().performClick_(None)
        self._status_item.setMenu_(None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dan-panel")
    parser.add_argument("--config", help="Path to a DAN TOML config file")
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
