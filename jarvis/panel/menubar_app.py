"""macOS menu-bar shell for the cockpit (PANEL_CONTRACT §5, H1).

NSStatusItem + borderless NSPanel + WKWebView rendering the SAME static
cockpit assets the browser uses. The shell owns no state and adds no
authority: it loads `jarvis/panel/assets/index.html` and seeds the
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

from jarvis.config import JarvisConfig, load_config
from jarvis.panel.hotkey import (
    HotkeyEdgeDetector,
    HotkeySpecError,
    PttHotkeyClient,
    accessibility_trust_state,
    fetch_effective_hotkey,
    parse_hotkey,
)
from jarvis.paths import resolve_runtime_paths

# PTT activation grace: how long the hotkey combo must stay held before the mic
# actually arms. A quick accidental brush (press+release faster than this) is
# ignored entirely. Client-side UX constant (Ozzy 2026-07-10: 400 ms).
PTT_ACTIVATION_GRACE_SECONDS = 0.4
from jarvis.security.transport import load_api_token

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

# Must match API_TOKEN_STORAGE_KEY in jarvis/panel/assets/app.js.
COCKPIT_TOKEN_STORAGE_KEY = "jarvis-api-token"

# Menu-bar display height in points; the PNG carries 2x pixels for retina.
STATUS_ICON_HEIGHT = 40.0

# --- Karta widżetu ----------------------------------------------------------
# Karta to własny borderless NSPanel: przezroczyste okno z systemowym cieniem,
# którego warstwa webview robi zaokrąglony clip (JEDNA geometria, bez
# systemowego bąbla popovera — druga krawędź, strzałka, szczelina). Żywą
# ramkę stanu — neon, który OBIEGA dookoła, gdy Jarvis myśli/pracuje, i barwi
# się stanem — rysuje CSS w webview, sterowany realnym stanem z JS (cockpit
# i tak pobiera /health, /state, /voice, /stream). Płaski CALayer.border nie
# potrafiłby ani biegnącego światła, ani gradientu; powłoka nie maluje koloru.
PANEL_CORNER_RADIUS = 12.0
# Odstęp karty od dołu paska menu, w punktach.
PANEL_TOP_GAP = 6.0
# Klik w ikonę przy otwartym panelu: mousedown potrafi najpierw zdjąć key
# z panelu (chowamy), a mouseup odpala togglePanel — bez tego okna czasowego
# panel zamykałby się i natychmiast otwierał z powrotem.
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
            f"window.JARVIS_API_BASE = {json.dumps(api_base_url.rstrip('/'))};"
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
        self._hotkey_monitors: list = []
        self._outside_click_monitor = None
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
        self._install_hotkey_monitors(AppKit)
        self._install_outside_click_monitor(AppKit)

        app.run()

    def _install_hotkey_monitors(self, AppKit):  # noqa: N803 - ObjC module name
        """Watch a held modifier combo anywhere and drive PTT down/up.

        A flagsChanged monitor (global = other apps focused, local = our
        panel focused) feeds NSEvent.modifierFlags() — masked to the low 16
        device-dependent bits so left/right are distinguished — through the
        edge detector. Needs macOS Accessibility permission; without it the
        global monitor silently sees nothing (the local one still works while
        the panel is focused). A blank/zero hotkey installs nothing.

        The combo is the daemon's *effective* `voice.ptt_hotkey` (the DB value
        the panel UI writes to), falling back to the static TOML only when the
        daemon can't be reached — otherwise the monitor would watch the config
        default while the user pressed the combo they set in the panel.
        """

        spec = (
            fetch_effective_hotkey(
                self._settings.api_base_url, self._settings.api_token
            )
            or self._settings.ptt_hotkey
        )
        try:
            mask = parse_hotkey(spec)
        except HotkeySpecError as exc:
            print(
                f"panel: nieprawidlowy skrot PTT {spec!r}: {exc} "
                "Globalny hotkey wylaczony — ustaw poprawny skrot w panelu.",
                file=sys.stderr,
            )
            return
        if mask == 0:
            return
        detector = HotkeyEdgeDetector(mask)
        client = PttHotkeyClient(
            self._settings.api_base_url, self._settings.api_token
        )

        # PTT activation grace (Ozzy 2026-07-10): a "down" edge does NOT arm the
        # mic immediately. We wait PTT_ACTIVATION_GRACE_SECONDS; only if the combo
        # is STILL held when the timer fires do we actually POST /voice/ptt/down.
        # An accidental brush (press+release faster than the grace) never touches
        # the mic — the release cancels the pending timer before it fires. The
        # detector still tracks physical key state; the grace only defers the
        # HTTP dispatch, so release semantics stay intact.
        ptt_state: dict[str, Any] = {"timer": None, "down_sent": False}

        def _fire_down() -> None:
            ptt_state["down_sent"] = True
            ptt_state["timer"] = None
            client.dispatch("down")

        def handler(event):
            device_flags = int(event.modifierFlags()) & 0xFFFF
            edge = detector.update(device_flags)
            if edge == "down":
                if PTT_ACTIVATION_GRACE_SECONDS <= 0:
                    _fire_down()
                else:
                    timer = threading.Timer(PTT_ACTIVATION_GRACE_SECONDS, _fire_down)
                    timer.daemon = True
                    ptt_state["down_sent"] = False
                    ptt_state["timer"] = timer
                    timer.start()
            elif edge == "up":
                pending = ptt_state.get("timer")
                if pending is not None:
                    pending.cancel()
                    ptt_state["timer"] = None
                if ptt_state.get("down_sent"):
                    client.dispatch("up")
                    ptt_state["down_sent"] = False
                # released within grace → never armed → send nothing
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
        # Globalny monitor flagsChanged dostaje zdarzenia TYLKO gdy proces
        # panelu ma uprawnienie Dostepnosc. Bez niego monitor jest wpiety, ale
        # handler nigdy sie nie odpala — skrot milczy, choc przycisk PRZYTRZYMAJ
        # (WebView→HTTP, bez uprawnien) dziala. Wykrywamy to i mowimy wprost,
        # zamiast udawac, ze hotkey jest aktywny.
        trust = accessibility_trust_state()
        if trust == "untrusted":
            print(
                f"panel: skrot PTT {spec!r} zarejestrowany, ale globalny hotkey "
                "NIE ZADZIALA — proces panelu nie ma uprawnienia Dostepnosc. "
                "Ustawienia systemowe > Prywatnosc i ochrona > Dostepnosc — "
                "wlacz aplikacje uruchamiajaca panel (Terminal/Python), potem "
                "zrestartuj panel. (Przycisk PRZYTRZYMAJ w panelu dziala bez "
                "tego uprawnienia.)",
                file=sys.stderr,
            )
        elif trust == "trusted":
            print(
                f"panel: globalny PTT hotkey aktywny ({spec}) — Dostepnosc "
                "przyznana.",
                file=sys.stderr,
            )
        else:  # unknown — nie potrafimy sprawdzic uprawnienia, damy hint
            print(
                f"panel: globalny PTT hotkey zarejestrowany ({spec}). Jesli "
                "trzymanie skrotu nie wlacza nasluchu: Ustawienia systemowe > "
                "Prywatnosc i ochrona > Dostepnosc — wlacz aplikacje "
                "uruchamiajaca panel (Terminal/Python), potem zrestartuj panel.",
                file=sys.stderr,
            )

    def _install_edit_menu(self, AppKit, app):  # noqa: N803 - ObjC module name
        """Standardowe skróty edycji (⌘A/⌘C/⌘V/⌘X/⌘Z) w polach webview.

        Bez menu głównego z pozycją Edit macOS nie routuje tych keyEquivalentów
        do first respondera (textarea kompozytora), więc kopiuj/wklej/zaznacz
        nie działają. Accessory app nie pokazuje paska menu, ale mainMenu i tak
        przetwarza skróty, gdy karta ma fokus — akcje trafiają do WKWebView,
        który implementuje copy:/paste:/selectAll:. """

        main_menu = AppKit.NSMenu.alloc().init()
        edit_container = AppKit.NSMenuItem.alloc().init()
        main_menu.addItem_(edit_container)
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edytuj")
        edit_container.setSubmenu_(edit_menu)

        # (tytuł, selektor, klawisz) — wielka litera implikuje ⇧ (Redo).
        entries = [
            ("Cofnij", "undo:", "z"),
            ("Ponów", "redo:", "Z"),
            (None, None, None),
            ("Wytnij", "cut:", "x"),
            ("Kopiuj", "copy:", "c"),
            ("Wklej", "paste:", "v"),
            ("Zaznacz wszystko", "selectAll:", "a"),
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

    def _install_outside_click_monitor(self, AppKit):  # noqa: N803
        """Klik poza panelem (w inną aplikację) chowa kartę. Globalny monitor
        nie widzi zdarzeń własnej apki, więc klik w panel ani w ikonę
        menubara go nie odpala — toggle zostaje przy togglePanel:."""

        mask = AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskRightMouseDown

        def handler(_event):
            if self._panel is not None and self._panel.isVisible():
                self._hide_panel()

        self._outside_click_monitor = (
            AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handler)
        )

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

        # Warstwa webview robi tylko zaokrąglony clip karty (żeby okno miało
        # miękkie rogi i pasujący cień). Ramkę stanu — kolor i animację —
        # rysuje CSS w webview, nie ta warstwa: jedna geometria, jedno źródło.
        webview.setWantsLayer_(True)
        layer = webview.layer()
        if layer is not None:
            layer.setCornerRadius_(PANEL_CORNER_RADIUS)
            layer.setMasksToBounds_(True)
        self._webview = webview

        shell = self

        class JarvisWidgetPanel(AppKit.NSPanel):
            def canBecomeKeyWindow(self):  # noqa: N802 - ObjC selector
                # Borderless okna domyślnie odmawiają key — bez tego textarea
                # kompozytora nie przyjmie ani jednego znaku.
                return True

            def windowDidResignKey_(self, _notification):  # noqa: N802
                shell._note_resign_key()

        panel = JarvisWidgetPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        # Okno jest przezroczyste: promień i ramkę rysuje wyłącznie warstwa
        # webview (jedna geometria), okno dokłada tylko systemowy cień.
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

        class JarvisPanelController(AppKit.NSObject):
            def togglePanel_(self, sender):  # noqa: N802 - ObjC selector
                event = AppKit.NSApplication.sharedApplication().currentEvent()
                if event is not None and event.type() == AppKit.NSEventTypeRightMouseUp:
                    shell._show_quit_menu(AppKit)
                    return
                shell._toggle_panel(AppKit)

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

    def _toggle_panel(self, AppKit):  # noqa: N803
        if self._panel.isVisible():
            self._hide_panel()
            return
        if time.monotonic() - self._hidden_at < PANEL_REOPEN_SUPPRESS_SECONDS:
            # Ten sam klik, który właśnie schował panel (resignKey na
            # mousedown) — nie otwieraj go z powrotem na mouseup.
            return
        self._show_panel(AppKit)

    def _show_panel(self, AppKit):  # noqa: N803
        """Karta ląduje wycentrowana pod ikoną menubara, przypięta pod
        paskiem menu, przycięta do widocznej krawędzi ekranu."""

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
        # Accessory + nonactivating panel nie dostaje sam fokusu klawiatury,
        # a bez key-window textarea kompozytora nie przyjmie znaku. Aktywujemy
        # apkę (bez Docka/menu — jesteśmy accessory), potem czynimy panel key.
        self._shown_at = time.monotonic()
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        self._panel.orderFrontRegardless()

    def _hide_panel(self) -> None:
        self._hidden_at = time.monotonic()
        self._panel.orderOut_(None)

    def _note_resign_key(self) -> None:
        # Utrata key-window chowa kartę (klik w inną apkę). Ale świeżo pokazany
        # panel potrafi raz zrezygnować z key, zanim aktywacja się ustabilizuje
        # — bez tego okna karta zamykałaby się natychmiast po otwarciu.
        if self._panel is None or not self._panel.isVisible():
            return
        if time.monotonic() - self._shown_at < PANEL_REOPEN_SUPPRESS_SECONDS:
            return
        self._hide_panel()

    def _show_quit_menu(self, AppKit):  # noqa: N803
        menu = AppKit.NSMenu.alloc().init()
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Jarvis Panel", "terminate:", "q"
        )
        menu.addItem_(quit_item)
        # Non-deprecated popup trick: attach the menu, synthesize a click so
        # AppKit opens it, then detach so left-click keeps toggling the panel.
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
