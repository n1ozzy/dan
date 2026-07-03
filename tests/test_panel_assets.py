"""Static Jarvis cockpit asset contract tests."""

from __future__ import annotations

from pathlib import Path

from tests.git_guards import assert_schema_and_migrations_unchanged


ROOT = Path(__file__).resolve().parents[1]
PANEL_DIR = ROOT / "jarvis" / "panel" / "assets"
INDEX_HTML = PANEL_DIR / "index.html"
APP_JS = PANEL_DIR / "app.js"
STYLES_CSS = PANEL_DIR / "styles.css"
RUNBOOK = ROOT / "docs" / "runbooks" / "PANEL_COCKPIT.md"

# Uwaga: /voice/ptt/down|up wypadły z panelu celowo — trzymanie PTT żyje na
# globalnym hotkeyu (menubar_app), panel ustawia tylko TRYB lock/unlock.
REQUIRED_ROUTES = (
    "/health",
    "/state",
    "/input/text",
    "/voice/listen/lock",
    "/voice/listen/unlock",
    "/voice/listening",
    "/conversations",
    "/turns",
    "/memory",
    "/tools",
    "/approvals",
    "/events",
    "/stream",
    "/runtime/processes",
    "/settings",
    "/brain/adapters",
    "/brain/switch",
)

FORBIDDEN_APP_SNIPPETS = (
    "eval(",
    "innerHTML",
    "launchctl",
    "pkill",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)

FORBIDDEN_RUNTIME_SNIPPETS = (
    "/Users/n1_ozzy/Documents/dev/dan",
    "/tmp/dan",
    "afplay",
    "--dangerously-skip-permissions",
)


def test_panel_asset_files_exist() -> None:
    assert INDEX_HTML.is_file()
    assert APP_JS.is_file()
    assert STYLES_CSS.is_file()


def test_index_references_static_js_and_css() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")

    assert "./app.js" in markup
    assert "./styles.css" in markup


def test_app_previews_approval_arguments() -> None:
    # Karta zgody musi pokazywać CO model chce zrobić (argumenty wywołania),
    # nie tylko nazwę narzędzia i id — inaczej człowiek zatwierdza w ciemno.
    script = APP_JS.read_text(encoding="utf-8")

    assert "payload.arguments" in script
    assert "argument-line" in script


def test_app_fetches_turns_newest_first() -> None:
    # Kliknięta rozmowa ma od razu pokazywać najnowszą wymianę na górze;
    # domyślne oldest-first + limit ucinało świeże tury i chowało je na dole.
    script = APP_JS.read_text(encoding="utf-8")

    assert "newest_first=true" in script


def test_cockpit_is_single_view_app_with_tabbar() -> None:
    # Architektura popover-first: jeden widok naraz (Czat / Zgody / Pamięć /
    # System) + dolny pasek zakładek. Czat = pełna powierzchnia, dymki
    # user/jarvis z metą; strona się nie scrolluje, widoki przewijają się
    # wewnętrznie.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "tabbar" in markup
    assert "tab-button" in markup
    assert 'data-view="chat"' in markup
    for view_id in ("view-chat", "view-approvals", "view-memory", "view-system"):
        assert view_id in markup, view_id
    assert "chat-toolbar" in markup
    assert "conversationSelect" in markup
    assert "newConversationButton" in markup
    assert "composer" in markup
    assert "chat-log" in markup
    assert "tabbar" in styles
    assert "tab-button" in styles
    assert "chat-bubble" in styles
    assert "switchView" in script
    assert "chat-bubble user" in script
    assert "chat-bubble jarvis" in script
    assert "chat-meta" in script


def test_cockpit_state_signals_are_quiet_structure() -> None:
    # Stan systemu: pill stanu + klasy na <body> (offline wygasza kompozytor,
    # has-pending barwi sygnały zgód) + żywa ramka stanu (patrz osobny test).
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "state-pill" in markup
    assert "state-pill" in styles
    assert "offline-hero" in script
    assert "offline" in script
    assert "has-pending" in script
    assert "has-pending" in styles


def test_state_frame_is_animated_and_driven_by_live_state() -> None:
    # Sygnatura panelu: neonowa ramka na krawędzi karty, która OBIEGA dookoła,
    # gdy Jarvis pracuje (THINKING/SPEAKING/LISTENING) i barwi się stanem
    # (teal online, bursztyn gdy czekają zgody, czerwień offline). Ruch niesie
    # informację o trwającym procesie — nie jest dekoracją; spoczynek (IDLE)
    # zostawia ramkę statyczną. Renderuje ją CSS w webview, sterowany realnym
    # stanem z JS — jedna geometria, bez malowania natywnej warstwy co klatkę.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    # Element ramki w dokumencie, dekoracyjny (aria-hidden), poza tab-orderem.
    assert "state-frame" in markup
    assert "state-frame" in styles

    # Obiegające światło = conic-gradient obracany przez animowany kąt.
    assert "conic-gradient" in styles
    assert "@keyframes" in styles
    # Obrót zatrzymuje się przy ograniczonym ruchu — ramka wtedy tylko barwi.
    assert "prefers-reduced-motion" in styles

    # JS steruje ramką realnym stunem: online/offline, liczba zgód i to, czy
    # daemon właśnie pracuje. Stany daemona z RuntimeState (state_machine.py).
    assert "applyStateFrame" in script
    for runtime_state in ("THINKING", "SPEAKING", "LISTENING"):
        assert runtime_state in script, runtime_state
    # Ramka reaguje na strumień: state.changed przełącza pracę/spoczynek.
    assert "runtimeState" in script


def test_history_click_scrolls_chat_pane_not_page() -> None:
    # Dymki żyją w przewijanym kontenerze obok listy rozmów; klik nie skacze
    # po stronie (stare obejście scrollIntoView na turnList usunięte).
    script = APP_JS.read_text(encoding="utf-8")

    assert "turnList.scrollIntoView" not in script
    assert "scrollTop" in script


def test_app_references_required_daemon_routes() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    missing = [route for route in REQUIRED_ROUTES if route not in script]

    assert missing == []


def test_index_has_settings_section_with_brain_switch() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")

    assert "settingsHeading" in markup
    assert "settingsList" in markup
    assert "settingKey" in markup
    assert "settingValue" in markup
    assert "brainAdapterSelect" in markup
    assert "switchBrainButton" in markup


def test_app_settings_are_rendered_from_daemon_truth_only() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    # Thin client: settings and brain state come from the daemon on every
    # render; a mutation POSTs and then re-fetches instead of patching a
    # local copy.
    assert "refreshSettings" in script
    assert "/brain/adapters" in script
    assert "/brain/switch" in script
    # The API token is the only value the cockpit keeps in local storage.
    setter_calls = [
        line for line in script.splitlines() if "localStorage.setItem" in line
    ]
    assert all("API_TOKEN_STORAGE_KEY" in line for line in setter_calls)
    assert len(setter_calls) >= 1


def test_panel_cockpit_runbook_documents_settings_section() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "get /settings" in lowered
    assert "post /settings" in lowered
    assert "/brain/adapters" in text
    assert "/brain/switch" in text
    assert "re-fetch" in lowered


def test_app_avoids_unsafe_or_legacy_runtime_snippets() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    offenders = [snippet for snippet in FORBIDDEN_APP_SNIPPETS if snippet in script]

    assert offenders == []


def test_app_uses_safe_text_rendering_for_dynamic_data() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    assert "textContent" in script or "createTextNode" in script
    assert "dangerouslySetInnerHTML" not in script


def test_panel_assets_do_not_reference_external_cdns() -> None:
    for path in (INDEX_HTML, APP_JS, STYLES_CSS):
        text = path.read_text(encoding="utf-8")
        assert "cdn." not in text.lower()
        assert "unpkg.com" not in text.lower()
        assert "jsdelivr" not in text.lower()

    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    assert "http://" not in markup
    assert "https://" not in markup
    assert "http://" not in styles
    assert "https://" not in styles


def test_index_splits_basic_and_collapsible_views() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")

    # Operator-first: czat/kompozytor/zgody/rozmowy zawsze widoczne; pamięć,
    # narzędzia, zdarzenia i "Zaawansowane" (API/health/ustawienia/runtime)
    # to natywnie zwijane <details>, domyślnie zwinięte.
    assert "<details" in markup
    assert "<summary" in markup
    assert "<details open" not in markup

    first_details = markup.index("<details")
    for basic_marker in (
        "composer",
        "approvalsHeading",
        "conversationSelect",
        "chat-log",
        "memoryHeading",
    ):
        assert markup.index(basic_marker) < first_details, basic_marker
    for collapsible_heading in (
        "toolsHeading",
        "eventsHeading",
        "advancedHeading",
        "apiHeading",
        "healthHeading",
        "settingsHeading",
        "runtimeHeading",
    ):
        assert markup.index(collapsible_heading) > first_details, collapsible_heading

    assert "collapsible" in styles
    assert "summary" in styles


def test_pending_approvals_have_badge_and_chat_nudge() -> None:
    # Zgody sygnalizowane dwukanałowo: licznik na zakładce Zgody oraz
    # bursztynowy przerywnik w czacie (poza widokiem zgód) — decyzji nie
    # wolno przegapić.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "approvalsBadge" in markup
    assert "approvalNudge" in markup
    assert "approvals-badge" in styles
    assert "approval-nudge" in styles
    assert "prefers-reduced-motion" in styles
    assert "setPendingBadge" in script
    assert "updateApprovalSignals" in script


def test_approvals_refresh_rides_stream_with_heartbeat_fallback() -> None:
    # Zgody odświeżają się z eventów approval.* na WebSocketcie /stream;
    # heartbeat /health (pending_approval_count) zostaje jako fallback, gdy
    # stream leży albo event przepadł.
    script = APP_JS.read_text(encoding="utf-8")

    assert 'startsWith("approval.")' in script
    assert "syncPendingApprovals" in script
    assert "pending_approval_count" in script


def test_app_renders_relative_times_with_full_date_tooltip() -> None:
    # "2 min temu" zamiast surowego ISO; pełna data w tooltipie; ticker
    # dosowieża etykiety, żeby otwarty panel nie kłamał po godzinie.
    script = APP_JS.read_text(encoding="utf-8")

    assert "formatRelative" in script
    assert "min temu" in script
    assert "dataset.timestamp" in script
    assert ".title = " in script


def test_app_titles_conversations_from_first_input() -> None:
    # Kafelek rozmowy nazywa się początkiem pierwszego input_text (cache po
    # id), nie generycznym "Rozmowa 15:47"; zegar zostaje jako fallback.
    script = APP_JS.read_text(encoding="utf-8")

    assert "ensureConversationTitle" in script
    assert "limit=1" in script


def test_memory_rows_expose_priority_and_disable_actions() -> None:
    # Blok pamięci: wyłączenie (DELETE /memory/{id}), edycja priorytetu
    # (PATCH /memory/{id}) i pochodzenie bloku (proposed_by/promoted_by).
    script = APP_JS.read_text(encoding="utf-8")

    assert '"PATCH"' in script
    assert '"DELETE"' in script
    assert "proposed_by" in script
    assert "promoted_by" in script
    assert "Wyłącz" in script


def test_composer_has_voice_mode_switch() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    # Głos w kompozytorze: segmenty PTT | Nasłuch obok siebie jako wybór
    # TRYBU słuchania (nie hold-button — trzymanie PTT robi globalny hotkey
    # w menubar_app), plus status mikrofonu z falą aktywną tylko przy
    # zbieraniu. Live refresh jedzie na listening.* ze streamu.
    first_details = markup.index("<details")
    for element_id in ("pttModeButton", "listenToggle", "voiceStatus"):
        assert element_id in markup, element_id
        assert markup.index(element_id) < first_details, element_id
    assert "voice-mode" in markup
    assert "voice-mode" in styles
    assert "setVoiceMode" in script
    assert "hud-item.live .wave" in styles
    assert 'startsWith("listening.")' in script


def test_composer_sends_beside_the_field() -> None:
    # Układ komunikatorowy: pole i „Wyślij” w JEDNYM rzędzie (przycisk po
    # prawej, wyrównany do dołu), a status mikrofonu jako dyskretna linijka
    # POD polem — nie rząd kontrolek pod textareą jak wcześniej.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")

    assert "composer-row" in markup
    assert "composer-row" in styles

    # textarea i przycisk Wyślij żyją w tym samym rzędzie composer-row,
    # w tej kolejności (pole, potem przycisk po prawej).
    row = markup.index("composer-row")
    send = markup.index("sendButton")
    textarea = markup.index("textInput")
    status = markup.index("composer-status")
    assert row < textarea < send, "textarea przed Wyślij w rzędzie"
    assert send < status, "status mikrofonu pod rzędem pola, nie w nim"

    # Rząd to grid z elastycznym polem i przyciskiem przy dole.
    assert "composer-status" in styles


def test_app_sends_text_input_on_enter() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    assert "keydown" in script
    assert "requestSubmit" in script
    assert "shiftKey" in script


def test_css_has_compact_width_friendly_layout() -> None:
    styles = STYLES_CSS.read_text(encoding="utf-8")

    assert "480px" in styles
    assert "760px" in styles
    assert "overflow" in styles
    assert "grid" in styles or "flex" in styles


def test_panel_cockpit_runbook_documents_boundaries() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "not the final macos menubar panel" in lowered
    assert "not a source of truth" in lowered
    assert "no websocket mutations" in lowered
    assert "no voice" in lowered
    assert "no native menubar" in lowered
    assert "display-only" in lowered


def test_panel_cockpit_runbook_documents_read_only_stream() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "/stream" in text
    assert "adr-019" in lowered
    assert "read-only" in lowered
    assert "jarvis-token." in text
    assert "output_omitted" in text


def test_app_stream_client_is_read_only() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    assert "new WebSocket" in script
    assert "jarvis-token." in script
    # Display-only client: the socket never sends application data.
    assert ".send(" not in script


def test_panel_cockpit_runbook_documents_local_cors_development() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "python3 -m http.server 41800" in text
    assert "http://127.0.0.1:41800" in text
    assert "http://127.0.0.1:<daemon-port>" in text
    assert "bare" in lowered
    assert "relative url" in lowered
    assert "origin: null" in lowered
    assert "wildcard cors" in lowered
    assert "credentials" in lowered
    assert "not auth or csrf hardening" in lowered


def test_schema_and_migrations_are_unchanged() -> None:
    assert_schema_and_migrations_unchanged(ROOT)


def test_runtime_files_avoid_forbidden_legacy_strings() -> None:
    scanned_roots = (ROOT / "jarvis", ROOT / "scripts")
    text_suffixes = {".py", ".sql", ".toml", ".md", ".sh", ".example", ".html", ".js", ".css", ""}
    offenders: list[tuple[str, str]] = []

    for root in scanned_roots:
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix not in text_suffixes:
                continue
            # errors="replace": the forbidden snippets are ASCII, and a stray
            # binary (e.g. Finder's .DS_Store) must not crash the scan.
            text = path.read_text(encoding="utf-8", errors="replace")
            for snippet in FORBIDDEN_RUNTIME_SNIPPETS:
                if snippet in text:
                    offenders.append((str(path.relative_to(ROOT)), snippet))

    assert offenders == []
