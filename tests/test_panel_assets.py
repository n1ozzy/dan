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

REQUIRED_ROUTES = (
    "/health",
    "/state",
    "/input/text",
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


def test_index_splits_basic_and_advanced_views() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    # Operator-first: input/approvals/history are always visible; debug and
    # plumbing sections hide behind the "Zaawansowane" toggle.
    assert "advancedToggle" in markup
    for advanced_heading in (
        "apiHeading",
        "healthHeading",
        "memoryHeading",
        "toolsHeading",
        "settingsHeading",
        "eventsHeading",
        "runtimeHeading",
    ):
        section_start = markup.index(advanced_heading)
        assert "advanced" in markup[section_start - 200 : section_start], advanced_heading
    for basic_heading in ("inputHeading", "approvalsHeading", "historyHeading"):
        section_start = markup.index(basic_heading)
        assert "advanced" not in markup[section_start - 200 : section_start], basic_heading

    assert ".card.advanced" in styles
    assert "show-advanced" in styles
    assert "show-advanced" in script


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
