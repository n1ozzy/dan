"""Static Jarvis cockpit asset contract tests."""

from __future__ import annotations

import subprocess
import textwrap
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
    "/voice/queue",
    "/conversations",
    "/turns",
    "/memory",
    "/memory/items",
    "/tools",
    "/approvals",
    "/events",
    "/stream",
    "/runtime/processes",
    "/audio/devices",
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
    assert "approval-arg" in script


def test_approvals_view_reads_at_a_glance() -> None:
    # Redesign zad. 4: pusty stan jest spokojnym komunikatem (nie ramką-
    # inputem), a karta zgody czyta się na rzut oka — ludzka nazwa narzędzia,
    # chip ryzyka po polsku z kolorem wg wagi, argumenty jako tabelka,
    # jednoznaczne przyciski.
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    # Pusty stan: wycentrowany znak ✓, nie wiersz wyglądający jak formularz.
    assert "empty-state" in script
    assert "empty-state" in styles
    assert "Nic nie czeka" in script

    # Ludzkie nazwy narzędzi i etykiety ryzyka (mapa PL, współdzielona z LOGI).
    assert "TOOL_LABELS" in script
    assert "toolLabel" in script
    assert "riskLabel" in script
    assert "riskTier" in script

    # Chip ryzyka barwiony wagą: odczyt / zapis / destructive.
    assert "risk-chip" in script
    assert "risk-chip" in styles

    # Decyzja jednoznaczna.
    assert "Zatwierdź" in script
    assert "Odrzuć" in script


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
    for view_id in ("view-chat", "view-approvals", "view-memory", "view-logs", "view-system"):
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
    # Stan systemu: żywa ramka (patrz osobny test) + klasy na <body> (offline
    # wygasza kompozytor, has-pending barwi sygnały zgód). Osobny pill stanu
    # usunięty — ramka niesie stan, redundantny wskaźnik zbędny.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "state-pill" not in markup
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


def test_runtime_overview_is_read_only_inventory_from_existing_safe_routes() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "runtimeOverviewHeading" in markup
    assert "runtimeOverviewList" in markup

    assert "refreshRuntimeOverview" in script
    assert "renderRuntimeOverview" in script
    for route in (
        "/health",
        "/state",
        "/settings",
        "/brain/adapters",
        "/audio/devices",
        "/voice/listening",
        "/voice/queue?limit=12",
        "/tools",
        "/events?latest=true&limit=50",
    ):
        assert route in script

    for heading in (
        "Runtime",
        "Brain/Provider",
        "Voice Runtime",
        "Tools/Internet",
        "Logs/Trace",
        "Developer/Test",
    ):
        assert heading in script
    assert "not exposed by current API" in script
    assert "unknown" in script
    assert "read-only" in script


def test_runtime_overview_sections_are_ordered_and_source_aware() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    headings = (
        'title: "Runtime"',
        'title: "Brain/Provider"',
        'title: "Voice Runtime"',
        'title: "Tools/Internet"',
        'title: "Logs/Trace"',
        'title: "Developer/Test"',
    )
    positions = [script.index(heading) for heading in headings]
    assert positions == sorted(positions)

    assert "RUNTIME_OVERVIEW_FIELD_SOURCES" in script
    assert "runtimeOverviewFieldRows" in script
    assert "runtimeOverviewReadiness" in script
    assert "runtimeOverviewSourceFailures" in script
    assert "source:" in script
    assert "readiness:" in script


def test_runtime_overview_pins_real_diagnostic_fields() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    for expected in (
        "configured adapter/provider",
        "effective adapter/provider",
        "configured model",
        "effective model(s)",
        "configured TTS",
        "effective TTS",
        "configured STT",
        "effective STT",
        "playback engine",
        "recorder/input engine",
        "PTT mode/hotkey",
        "broker enabled",
        "speak responses",
        "queue counts",
        "last failure source",
        "backend data gaps",
        "warnings summary",
    ):
        assert expected in script


def test_runtime_overview_detects_network_tools_beyond_network_risk_only() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    assert "networkToolCandidates" in script
    assert "toolSupportsNetwork" in script
    assert "description" in script
    assert "internet" in script


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

    # Operator-first: czat/kompozytor/zgody/rozmowy zawsze widoczne. Zdarzenia
    # przeniesione do własnej zakładki LOGI, narzędzia do płaskiej sekcji
    # System („Możliwości Jarvisa”) — żadne z nich nie jest już zwijane.
    # Surowa diagnostyka (API/health/ustawienia/runtime) zostaje w <details>.
    assert "<details" in markup
    assert "<summary" in markup
    assert "<details open" not in markup

    # Zdarzenia nie są już zwijaną sekcją — mają zakładkę.
    assert "eventsHeading" not in markup
    assert "view-logs" in markup

    first_details = markup.index("<details")
    for basic_marker in (
        "composer",
        "view-approvals",
        "conversationSelect",
        "chat-log",
        "memoryHeading",
    ):
        assert markup.index(basic_marker) < first_details, basic_marker
    for collapsible_heading in (
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


def test_live_stream_refreshes_chat_memory_voice_and_runtime_views() -> None:
    # Runtime truth changes arrive as persisted events. The panel must use them
    # to refresh visible state, not require manual tab clicks after each turn,
    # memory write, queue change, or daemon runtime update.
    script = APP_JS.read_text(encoding="utf-8")

    assert "scheduleHistoryRefresh" in script
    assert "scheduleMemoryRefresh" in script
    assert "scheduleRuntimeRefresh" in script
    assert 'type.startsWith("turn.")' in script
    assert 'type.startsWith("input.")' in script
    assert 'type.startsWith("memory.")' in script
    assert 'type.startsWith("voice.")' in script


def test_approval_cards_support_memory_save_one_click_and_idempotent_execute() -> None:
    # Memory-save approvals should feel like one operator action while the
    # backend keeps explicit approve+execute semantics. Duplicate execute 409s
    # are a completed state in the UI, not a red error.
    script = APP_JS.read_text(encoding="utf-8")

    assert "Zatwierdź i zapisz" in script
    assert "approveAndExecuteApproval" in script
    assert "isMemoryApproval" in script
    assert "isAlreadyExecutedConflict" in script
    assert "already executed" in script
    assert "approval.status" in script
    assert "approval.action_type" in script


def test_memory_approve_execute_failure_preserves_approved_retry_state(tmp_path: Path) -> None:
    # The one-click memory flow is approve + explicit execute. If execute fails
    # after approve succeeds, retry must execute the approved row instead of
    # sending approve again and hitting "Approval is not pending".
    harness = tmp_path / "approval-flow-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            class FakeNode {{
              constructor(tagName, id = "") {{
                this.tagName = tagName;
                this.id = id;
                this.children = [];
                this.parentNode = null;
                this.dataset = {{}};
                this.hidden = false;
                this.disabled = false;
                this.className = "";
                this.textContent = "";
                this.listeners = {{}};
                this.attributes = {{}};
                this.classList = {{
                  add: (name) => this._addClass(name),
                  remove: (name) => this._removeClass(name),
                  toggle: (name, force) => this._toggleClass(name, force),
                }};
              }}
              get firstChild() {{
                return this.children[0] || null;
              }}
              append(...items) {{
                for (const item of items) {{
                  this.appendChild(item);
                }}
              }}
              appendChild(item) {{
                if (item === null || item === undefined) {{
                  return item;
                }}
                item.parentNode = this;
                this.children.push(item);
                return item;
              }}
              removeChild(item) {{
                this.children = this.children.filter((child) => child !== item);
                item.parentNode = null;
                return item;
              }}
              setAttribute(name, value) {{
                this.attributes[name] = String(value);
              }}
              addEventListener(name, callback) {{
                this.listeners[name] = callback;
              }}
              querySelectorAll(selector) {{
                const matches = [];
                const visit = (node) => {{
                  for (const child of node.children) {{
                    if (selector === "button" && child.tagName === "button") {{
                      matches.push(child);
                    }}
                    visit(child);
                  }}
                }};
                visit(this);
                return matches;
              }}
              closest(selector) {{
                let node = this;
                while (node) {{
                  const classes = String(node.className || "").split(/\\s+/);
                  if (selector === ".approval-card" && classes.includes("approval-card")) {{
                    return node;
                  }}
                  node = node.parentNode;
                }}
                return null;
              }}
              _addClass(name) {{
                const classes = new Set(String(this.className || "").split(/\\s+/).filter(Boolean));
                classes.add(name);
                this.className = Array.from(classes).join(" ");
              }}
              _removeClass(name) {{
                const classes = new Set(String(this.className || "").split(/\\s+/).filter(Boolean));
                classes.delete(name);
                this.className = Array.from(classes).join(" ");
              }}
              _toggleClass(name, force) {{
                const enabled = force === undefined ? !String(this.className).split(/\\s+/).includes(name) : Boolean(force);
                if (enabled) {{
                  this._addClass(name);
                }} else {{
                  this._removeClass(name);
                }}
                return enabled;
              }}
            }}

            const nodes = new Map();
            const node = (id) => {{
              if (!nodes.has(id)) {{
                nodes.set(id, new FakeNode("div", id));
              }}
              return nodes.get(id);
            }};
            const flatten = (root) => [
              root,
              ...root.children.flatMap((child) => flatten(child)),
            ];
            const response = (status, payload) => ({{
              status,
              ok: status >= 200 && status < 300,
              text: async () => JSON.stringify(payload),
            }});
            const calls = [];
            const approvedApproval = {{
              id: "ap-1",
              status: "approved",
              action_type: "tool:memory_save",
              risk: "memory_write",
              requested_by: "model",
              created_at: "2026-07-05T12:00:00+00:00",
              payload: {{
                tool_name: "memory_save",
                arguments: {{ title: "Projekt", body: "zapamiętaj fakt" }},
              }},
            }};

            const context = {{
              console,
              URL,
              setTimeout: () => 0,
              clearTimeout: () => {{}},
              window: {{
                localStorage: {{
                  getItem: () => "token",
                  setItem: () => {{}},
                  removeItem: () => {{}},
                }},
                prompt: () => "",
                setInterval: () => 0,
              }},
              document: {{
                body: node("body"),
                addEventListener: () => {{}},
                getElementById: (id) => node(id),
                querySelectorAll: () => [],
                createElement: (tagName) => new FakeNode(tagName),
              }},
              fetch: async (url, init = {{}}) => {{
                const parsed = new URL(url);
                const path = `${{parsed.pathname}}${{parsed.search}}`;
                calls.push({{ path, method: init.method || "GET" }});
                if (path === "/approvals/ap-1/approve") {{
                  return response(200, {{ approval: approvedApproval }});
                }}
                if (path === "/approvals/ap-1/execute") {{
                  return response(500, {{ error: "execute failed" }});
                }}
                if (path === "/approvals?limit=25") {{
                  return response(200, {{ approvals: [] }});
                }}
                return response(200, {{}});
              }},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});
            context.bindElements();

            (async () => {{
              await context.approveAndExecuteApproval("ap-1", new FakeNode("button"));

              assert.deepStrictEqual(
                calls.map((call) => call.path),
                ["/approvals/ap-1/approve", "/approvals/ap-1/execute", "/approvals?limit=25"],
              );
              assert.match(node("approvalsError").textContent, /execute failed/);
              const buttons = flatten(node("approvalList")).filter((item) => item.tagName === "button");
              assert.strictEqual(
                buttons.some((button) => button.textContent === "Zatwierdź i zapisz"),
                false,
              );
              const executeButton = buttons.find((button) => button.textContent === "Wykonaj zatwierdzone");
              assert.ok(executeButton, "approved card should render execute retry");

              await executeButton.listeners.click();
              assert.strictEqual(
                calls.filter((call) => call.path === "/approvals/ap-1/approve").length,
                1,
              );
              assert.strictEqual(
                calls.filter((call) => call.path === "/approvals/ap-1/execute").length,
                2,
              );
            }})().catch((error) => {{
              console.error(error && error.stack ? error.stack : error);
              process.exit(1);
            }});
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(harness)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_logs_and_system_show_voice_cutoff_diagnostics() -> None:
    # Voice cutoff triage needs enough data in-panel to see whether a row was
    # queued, started, finished, failed, or cancelled without exposing full
    # secret-bearing payloads as raw blobs.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "voiceQueueList" in markup
    assert "/voice/queue" in script
    assert "refreshVoiceQueue" in script
    assert "eventPayloadSummary" in script
    assert "payload-line" in script
    assert "payload-line" in styles


def test_event_payload_summary_is_whitelisted_and_redacted(tmp_path: Path) -> None:
    harness = tmp_path / "event-summary-redaction-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            const context = {{
              console,
              document: {{
                addEventListener: () => {{}},
              }},
              window: {{}},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const secretValue = "sk-" + "panel-event-secret";
            const arbitrary = context.eventPayloadSummary({{
              detail: `Authorization: Bearer ${{secretValue}}`,
              token: secretValue,
              secret: secretValue,
              password: secretValue,
              auth: `Bearer ${{secretValue}}`,
              headers: {{ authorization: `Bearer ${{secretValue}}` }},
              cookies: `jarvis=${{secretValue}}`,
              api_key: secretValue,
            }});
            assert.strictEqual(arbitrary, "");

            const whitelisted = context.eventPayloadSummary({{
              status: "failed",
              reason: "tool_failed",
              error: `password=${{secretValue}} Authorization: Bearer ${{secretValue}}`,
              detail: `must not render ${{secretValue}}`,
              token: secretValue,
            }});
            assert.match(whitelisted, /status: failed/);
            assert.match(whitelisted, /reason: tool_failed/);
            assert.match(whitelisted, /error:/);
            assert.match(whitelisted, /\\[REDACTED\\]/);
            assert.ok(!whitelisted.includes(secretValue), whitelisted);
            assert.ok(!whitelisted.includes("must not render"), whitelisted);
            assert.ok(!whitelisted.includes("detail:"), whitelisted);
            assert.ok(!whitelisted.includes("token:"), whitelisted);
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(harness)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_memory_view_reads_legacy_blocks_and_memory_os_items() -> None:
    # Approved memory_save activates Memory OS items, while manual panel notes
    # still use legacy memory_blocks. The panel should show both surfaces
    # without changing prompt-selection policy.
    script = APP_JS.read_text(encoding="utf-8")

    assert "/memory/items" in script
    assert "memoryItemToPanelRow" in script
    assert "legacy_block" in script
    assert "memory_os_item" in script


def test_memory_view_is_obvious_on_arrival() -> None:
    # Redesign zad. 5: człowiek pierwszy raz na zakładce wie, co to jest i co
    # wpisać. Formularz schowany za „+ Nowa notatka”; pola mają labele; rodzaj
    # to select ze znanymi wartościami (MEMORY_KINDS), nie gołe pole.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    # Formularz w rozwijanym <details> — domyślnie widać listę + przycisk.
    assert "memory-new" in markup
    assert "Nowa notatka" in markup

    # Rodzaj jako select z realnymi wartościami z daemona (MEMORY_KINDS).
    assert "<select id=\"memoryKind\"" in markup
    for kind in ("identity", "user_preference", "project", "fact", "summary", "temporary"):
        assert f'value="{kind}"' in markup, kind

    # Pola mają widoczne labele, nie tylko placeholdery.
    assert "field-label" in markup
    assert "field-label" in styles

    # Ludzkie nazwy rodzaju + pochodzenie po polsku na blokach listy.
    assert "MEMORY_KIND_LABELS" in script
    assert "zaproponował" in script


def test_logs_tab_reads_like_a_polish_diary() -> None:
    # Zad. 6: LOGI to własna zakładka — strumień zdarzeń po ludzku (mapa
    # typów na polskie etykiety), z prostym filtrem i metą #id · źródło · czas.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert 'data-view="logs"' in markup
    assert "logFilter" in markup

    # Mapa typów zdarzeń na polskie etykiety + fallback po rodzinie.
    assert "EVENT_LABELS" in script
    assert "eventLabel" in script
    for pair in ('"turn.finished"', '"listening.lease.created"', '"memory.updated"'):
        assert pair in script, pair

    # Filtr grupuje strumień (tury / głos / zgody / narzędzia).
    assert "eventMatchesFilter" in script


def test_tools_live_in_system_as_capabilities() -> None:
    # Zad. 6: narzędzia to rejestr możliwości, nie log — płaska sekcja w
    # System z ludzką nazwą, opisem i etykietą polityki zgód po polsku.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "toolList" in markup
    # renderowane przez wspólne mapy PL (nie surowe „file_read - file_read”).
    assert "toolLabel" in script
    assert "riskLabel" in script
    # Sekcja narzędzi opisana jako możliwości.
    assert "Możliwości" in markup


def test_stream_status_indicator_is_gone() -> None:
    # Zad. 7: wskaźnik „live/stream off” zniknął z UI — o życiu łącza mówi
    # ramka stanu. Reconnect streamu zostaje (setStreamStatus jako no-op).
    markup = INDEX_HTML.read_text(encoding="utf-8")

    assert "streamStatus" not in markup
    assert "stream off" not in markup


def test_voice_mode_lives_in_system_status_in_composer() -> None:
    # Zad. 1: wybór TRYBU słuchania (PTT | Nasłuch) przeniesiony do sekcji
    # „Głos” w System (nie hold-button — trzymanie PTT robi globalny hotkey
    # w menubar_app). W kompozytorze zostaje SAM status mikrofonu z falą
    # aktywną tylko przy zbieraniu. Live refresh jedzie na listening.* ze
    # streamu.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    # Segmenty trybu żyją w sekcji Głos (System), obok opisu z hotkeyem.
    assert "voiceHeading" in markup
    assert markup.index("voiceHeading") < markup.index("pttModeButton")
    assert markup.index("pttModeButton") < markup.index("listenToggle")

    # Status mikrofonu zostaje w kompozytorze (przed pierwszym <details>).
    first_details = markup.index("<details")
    assert markup.index("voiceStatus") < first_details

    assert "voice-mode" in markup
    assert "voice-mode" in styles
    assert "setVoiceMode" in script
    assert "hud-item.live .wave" in styles
    assert 'startsWith("listening.")' in script


def test_system_view_is_human_readable() -> None:
    # Zad. 8: System rozbity na płaskie, opisane sekcje (Mózg / Głos /
    # Połączenie / Ustawienia surowe / Możliwości); surowizna schowana w
    # „Diagnostyka (surowe)”. Stan daemona po ludzku, model aktywny/domyślny.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    for heading in ("brainHeading", "voiceHeading", "connectionHeading", "settingsHeading"):
        assert heading in markup, heading

    # Ludzki stan daemona zamiast surowej kv-listy na wierzchu.
    assert "healthHumanList" in markup
    assert "renderHealthHuman" in script
    assert "Działa od" in script

    # Model po ludzku (aktywny / domyślny), nie „current - default”.
    assert "aktywny:" in script
    assert "domyślny:" in script

    # Surowa diagnostyka pod jednym rozwijanym „Diagnostyka (surowe)”.
    assert "Diagnostyka (surowe)" in markup


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
