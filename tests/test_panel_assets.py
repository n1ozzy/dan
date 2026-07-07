"""Static Jarvis cockpit asset contract tests."""

from __future__ import annotations

import subprocess
import textwrap
import re
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
    "/runtime/settings/apply",
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


def test_index_has_active_settings_sections_with_apply_controls() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")

    assert "settingsHeading" in markup
    assert "settingsList" in markup
    assert "settingKey" in markup
    assert "settingValue" in markup
    assert "activeBrainProviderSelect" in markup
    assert "activeBrainModelSelect" in markup
    assert "activeBrainEffortSelect" in markup
    assert "applyBrainSettingsButton" in markup
    assert "applyTtsSettingsButton" in markup
    assert "applySttSettingsButton" in markup
    assert "applyPttSettingsButton" in markup
    assert "applyPersonaSettingsButton" in markup


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
        "/runtime/settings",
        "/brain/adapters",
        "/audio/devices",
        "/voice/listening",
        "/voice/runtime",
        "/voice/queue?limit=12",
        "/tools",
        "/events?latest=true&limit=50",
    ):
        assert route in script

    for heading in (
        "Runtime",
        "Turn State",
        "Readiness / Blockers",
        "Brain/Provider",
        "Latest turn trace",
        "Debug timeline",
        "Voice Settings: Capture/Input",
        "Voice Settings: STT/Transcription",
        "Voice Settings: Endpointing/VAD/PTT",
        "Voice Settings: TTS/Voice Model",
        "Voice Settings: Playback",
        "Voice Settings: Queue/Barge-in",
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
        'title: "Turn State"',
        'title: "Readiness / Blockers"',
        'title: "Brain/Provider"',
        'title: "Latest turn trace"',
        'title: "Debug timeline"',
        'title: "Voice Settings: Capture/Input"',
        'title: "Voice Settings: STT/Transcription"',
        'title: "Voice Settings: Endpointing/VAD/PTT"',
        'title: "Voice Settings: TTS/Voice Model"',
        'title: "Voice Settings: Playback"',
        'title: "Voice Settings: Queue/Barge-in"',
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
        "active provider/adapter",
        "active model",
        "configured provider list",
        "provider availability/configured status",
        "credentials status",
        "context budget/window",
        "streaming support",
        "tools support",
        "effort allowed values",
        "effort current/status",
        "fast support",
        "latest provider used by last turn",
        "latest provider error",
        "unsupported by current provider/model",
        "fast disabled/unsupported",
        "missing local model",
        "Turn State",
        "current_turn_id",
        "current_conversation_id",
        "current_turn_source",
        "generation_state",
        "current_speech_id",
        "interrupted_turn_id",
        "interruption_reason",
        "cancelled_speech_id",
        "turnStateValue",
        "Readiness / Blockers",
        "OK",
        "Missing",
        "Invalid",
        "Unknown",
        "Warning",
        "daemon config",
        "database path",
        "panel backend connected",
        "brain provider command",
        "TTS provider",
        "STT provider",
        "recorder/playback command",
        "network/tools capability",
        "readinessSummary",
        "Latest turn trace",
        "Debug timeline",
        "turn_id",
        "conversation_id",
        "provider/adapter/model used",
        "effort/fast",
        "memory included count",
        "memory excluded count",
        "approvals requested/executed count",
        "tools attempted count",
        "voice rows created filler/final/error",
        "speech cancellation/interruption reason",
        "user input received",
        "STT done",
        "generation started",
        "generation done",
        "TTS queued",
        "playback started",
        "playback finished",
        "newest-first safe events",
        "debugTimelineSummary",
        "traceLatestSafeError",
        "input policy",
        "recorder backend/command",
        "STT provider",
        "STT model/path",
        "PTT mode",
        "silence threshold/duration",
        "TTS provider",
        "voice id/profile/model",
        "playback engine/command",
        "cancelled reason",
        "interrupted previous response",
        "queue counts",
        "mock adapter/provider",
        "last failure source",
        "backend data gaps",
        "warnings summary",
    ):
        assert expected in script


def test_mission_control_operator_shell_is_present_and_read_only() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    for marker in (
        "missionControlHeading",
        "Refresh Mission Control",
        "missionControlSummary",
        "missionControlModules",
        "missionControlChecklist",
        "voiceDoctorList",
        "providerDoctorList",
        "missionControlRefreshStatus",
    ):
        assert marker in markup

    for marker in (
        "mission-control",
        "mission-summary",
        "module-grid",
        "doctor-grid",
        "checklist-grid",
        "poc-badge",
    ):
        assert marker in styles

    for marker in (
        "POC mode - not production",
        "Jarvis POC status",
        "operatorSummaryFromSnapshot",
        "renderMissionControl",
        "renderVoiceDoctor",
        "renderProviderDoctor",
        "pocChecklistItems",
        "refreshMissionControl",
        "MISSION_CONTROL_ENDPOINTS",
        "POC_NO_PERSISTENCE_GUARD",
        "no config writes",
        "no provider switch execution",
        "no microphone activation",
        "no external API/provider calls",
        "no raw secret rendering",
    ):
        assert marker in script


def test_system_runtime_cards_keep_values_readable_at_panel_width() -> None:
    # Regression: Mission Control cards used 190px auto-fit columns plus
    # overflow-wrap:anywhere on kv values, so normal values could render one
    # character per line in the native panel.
    styles = STYLES_CSS.read_text(encoding="utf-8")

    assert "#view-system.view" in styles
    assert "overflow-x: hidden" in styles
    assert "grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))" in styles
    assert "grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))" in styles
    assert "grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr))" in styles
    assert "grid-template-columns: auto minmax(0, 1fr)" in styles
    assert "grid-template-columns: minmax(0, 1fr) auto" in styles
    assert "#view-system .kv-list" in styles
    assert "grid-template-columns: minmax(96px, 42%) minmax(0, 1fr)" in styles
    assert "overflow-wrap: break-word" in styles
    assert "word-break: normal" in styles
    assert "@media (max-width: 620px)" in styles


def test_operator_summary_model_computes_status_and_next_action(tmp_path: Path) -> None:
    harness = tmp_path / "mission-control-summary-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            const context = {{
              console,
              document: {{ addEventListener: () => {{}} }},
              window: {{}},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const projection = (value, status = "ok", warning = null) => ({{
              value,
              effective_value: value,
              status,
              warning,
            }});
            const baseSnapshot = {{
              sourceStatus: {{
                health: {{ ok: true, path: "/health" }},
                state: {{ ok: true, path: "/state" }},
                runtimeSettings: {{ ok: true, path: "/runtime/settings" }},
                voice: {{ ok: true, path: "/voice/listening" }},
                voiceRuntime: {{ ok: true, path: "/voice/runtime" }},
                voiceQueue: {{ ok: true, path: "/voice/queue?limit=12" }},
                approvals: {{ ok: true, path: "/approvals?limit=25" }},
                memory: {{ ok: true, path: "/memory?active_only=true&limit=25" }},
                tools: {{ ok: true, path: "/tools" }},
                events: {{ ok: true, path: "/events?latest=true&limit=50" }},
              }},
              health: {{ service: "jarvisd", state: "IDLE", voice_enabled: true }},
              state: {{ state: "IDLE", pending_approval_count: 0, brain_adapter: "mock" }},
              runtimeSettings: {{
                runtime_readiness: {{
                  top_blockers: projection([], "ok"),
                  warnings: projection([], "ok"),
                  panel_backend_connected: projection("yes", "ok"),
                  tts_provider: projection("mock", "ok"),
                  stt_provider: projection("mock", "ok"),
                }},
                brain: {{
                  current_adapter: projection("mock", "ok"),
                  providers: projection([
                    {{
                      name: "mock",
                      status: "ok",
                      current: true,
                      configured: true,
                      available: true,
                      kind: "Developer/Test",
                      current_model: projection("mock-local", "ok"),
                      provider_command_status: projection("yes", "ok"),
                      provider_credentials_status: projection("unknown", "unknown"),
                      fast_supported: projection("no", "unsupported"),
                      tools_support: projection("no", "unsupported"),
                      streaming_support: projection("no", "unsupported"),
                    }},
                  ]),
                }},
                latest_turn_trace: {{
                  turn_id: projection("turn-1", "ok"),
                  source: projection("text", "ok"),
                  latest_safe_error: projection(null, "ok"),
                }},
              }},
              voiceRuntime: {{
                voice_runtime: {{
                  voice_enabled: true,
                  groups: {{
                    capture_input: {{ readiness: "ok" }},
                    stt_transcription: {{ readiness: "ok" }},
                    endpointing_vad_ptt: {{ readiness: "ok" }},
                    tts_voice_model: {{ readiness: "ok" }},
                    playback: {{ readiness: "ok" }},
                    queue_barge_in: {{ readiness: "ok" }},
                  }},
                }},
              }},
              voiceQueue: {{ voice_queue: [] }},
              approvals: {{ approvals: [] }},
              memory: {{ memory: [] }},
              memoryItems: {{ items: [] }},
              tools: {{ tools: [] }},
              events: {{ events: [] }},
              failures: [],
            }};

            const ready = context.operatorSummaryFromSnapshot(baseSnapshot);
            assert.strictEqual(ready.status, "ready");
            assert.match(ready.statusLine, /Ready enough/i);
            assert.match(ready.nextAction, /send text turn/i);

            const blockedSnapshot = JSON.parse(JSON.stringify(baseSnapshot));
            blockedSnapshot.runtimeSettings.runtime_readiness.top_blockers = projection(
              ["Voice enabled but TTS provider is missing.", "PTT source invalid warning."],
              "missing",
            );
            blockedSnapshot.runtimeSettings.runtime_readiness.tts_provider = projection("", "missing");
            const blocked = context.operatorSummaryFromSnapshot(blockedSnapshot);
            assert.strictEqual(blocked.status, "blocked");
            assert.match(blocked.statusLine, /Blocked/i);
            assert.deepStrictEqual([...blocked.blockers.slice(0, 2)], [
              "Voice enabled but TTS provider is missing.",
              "PTT source invalid warning.",
            ]);

            const offline = context.operatorSummaryFromSnapshot({{
              sourceStatus: {{ health: {{ ok: false, path: "/health" }} }},
              failures: ["/health"],
            }});
            assert.strictEqual(offline.status, "offline");
            assert.match(offline.statusLine, /backend offline/i);
            assert.match(offline.nextAction, /daemon/i);
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


def test_mission_control_refresh_plan_is_safe_read_only_gets(tmp_path: Path) -> None:
    harness = tmp_path / "mission-control-refresh-plan-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            const context = {{
              console,
              document: {{ addEventListener: () => {{}} }},
              window: {{}},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const plan = context.missionControlSafeEndpointPlan();
            assert.ok(plan.length >= 10);
            assert.ok(plan.every((entry) => entry.method === "GET"));
            assert.ok(plan.some((entry) => entry.path === "/runtime/settings"));
            assert.ok(plan.some((entry) => entry.path === "/voice/queue?limit=12"));
            assert.ok(plan.some((entry) => entry.path === "/approvals?limit=25"));
            assert.ok(plan.some((entry) => entry.path === "/memory/items"));
            assert.ok(plan.some((entry) => entry.path === "/events?latest=true&limit=50"));

            const forbidden = [
              "/settings",
              "/brain/switch",
              "/voice/listen/lock",
              "/voice/listen/unlock",
              "/voice/ptt/down",
              "/voice/ptt/up",
              "/input/text",
              "/approvals/{{id}}/execute",
            ];
            for (const path of forbidden) {{
              assert.ok(
                !plan.some((entry) => entry.path === path && entry.method !== "GET"),
                `unsafe endpoint in mission control plan: ${{path}}`,
              );
            }}
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


def test_poc_checklist_voice_and_provider_doctors_pin_required_diagnostics() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    for item in (
        "Daemon alive",
        "Panel connected",
        "Text turn path available",
        "Voice queue observable",
        "PTT available",
        "Brain provider known",
        "Voice TTS status known",
        "Voice STT status known",
        "Tools/Internet status known",
        "Memory visible",
        "Approval visible",
        "Latest turn trace visible",
    ):
        assert item in script

    for voice_marker in (
        "speak_responses",
        "broker_enabled",
        "default_tts",
        "default_stt",
        "TTS readiness",
        "STT readiness",
        "playback readiness",
        "capture policy",
        "PTT mode",
        "listening lease state",
        "current speaking item",
        "last cancellation reason",
        "interrupted_previous_response",
        "latest voice error",
        "voice disabled",
        "speak disabled",
        "broker disabled",
        "TTS missing",
        "STT missing",
        "queue stuck",
        "cancellation path unavailable",
        "PTT source invalid warning",
    ):
        assert voice_marker in script

    for provider_marker in (
        "active provider/adapter",
        "active model",
        "command status",
        "credentials status",
        "effort support",
        "fast support",
        "context budget/window",
        "streaming support",
        "tools support",
        "local runtime status",
        "latest provider error",
        "provider command missing",
        "provider configured but unavailable",
        "model missing/unknown",
        "effort unsupported",
        "fast unsupported",
        "local model missing",
        "mock/dev selected",
        "credentials unknown/missing",
    ):
        assert provider_marker in script


def test_settings_preview_cockpit_shell_is_present() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "settingsPreviewHeading" in markup
    assert "Settings Preview" in markup
    assert "settingsPreviewList" in markup
    assert "settingsPreviewSaveButton" not in markup
    assert "Save not implemented in POC" not in markup

    for heading in (
        "Brain / Provider",
        "Voice / TTS",
        "Voice / STT",
        "Endpointing / PTT",
        "Queue / Barge-in",
        "Tools / Internet",
        "Personality",
        "Developer / Test",
    ):
        assert heading in script

    for contract_name in (
        "refreshSettingsPreview",
        "renderSettingsPreview",
        "settingsPreviewModelFromPayload",
        "settingsPreviewApplyOverride",
        "settingsPreviewEvaluate",
        "settingsPreviewControlChanged",
        "settingsPreviewDiffRows",
        "renderSettingsPreviewDiff",
    ):
        assert contract_name in script


def test_tools_internet_controls_are_truthful_and_not_fake_apply_capable(tmp_path: Path) -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    for label in (
        "Tools",
        "Internet access",
        "Require approval for network",
        "Require approval for file writes",
        "Require approval for shell",
    ):
        assert label in markup
    for misleading_label in (
        ">tools.enabled<",
        ">network approval<",
        ">file write approval<",
        ">shell approval<",
    ):
        assert misleading_label not in markup

    harness = tmp_path / "tools-internet-truth-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                type: "",
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{
                  children.push(child);
                  return child;
                }},
                append(...nodes) {{
                  for (const node of nodes) this.appendChild(node);
                }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{
                  return children[0] || null;
                }},
                addEventListener() {{}},
              }};
            }}

            function textOf(node) {{
              if (!node) return "";
              return [node.textContent || "", ...(node.children || []).map(textOf)].join(" ");
            }}

            const requests = [];
            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              window: {{}},
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              fetch: async (url, init) => {{
                requests.push({{ url, init }});
                return {{
                  ok: true,
                  status: 200,
                  text: async () => JSON.stringify({{ ok: true, applied: [], runtime_settings: payload }}),
                }};
              }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const payload = {{
              tools: {{
                tools_enabled: {{
                  value: true,
                  effective_value: true,
                  source: "settings",
                  status: "ok",
                  editable_later: true,
                  warning: null,
                }},
                tools_master_flag: {{
                  value: "enabled",
                  effective_value: "enabled",
                  source: "settings",
                  status: "ok",
                  editable_later: true,
                  warning: null,
                }},
                tool_registry_status: {{
                  value: "registered",
                  effective_value: "registered",
                  source: "runtime_detected",
                  status: "ok",
                  editable_later: false,
                  warning: null,
                }},
                internet_capability: {{
                  value: {{ state: "unavailable", registered_network_tools: [] }},
                  effective_value: {{ state: "unavailable", registered_network_tools: [] }},
                  source: "runtime_detected",
                  status: "missing",
                  editable_later: false,
                  warning: "Internet unavailable: no network/search tool registered",
                }},
                network_search_tool: {{
                  value: "missing",
                  effective_value: "missing",
                  source: "runtime_detected",
                  status: "missing",
                  editable_later: false,
                  warning: "no network/search tool registered",
                }},
                network_policy: {{
                  value: "approval_required",
                  effective_value: "approval_required",
                  source: "config",
                  status: "ok",
                  editable_later: true,
                  warning: "network enabled but no network tool registered",
                }},
                approval_required_tools: {{
                  value: ["network", "shell", "file_write"],
                  effective_value: ["network", "shell", "file_write"],
                  source: "config",
                  status: "ok",
                  editable_later: true,
                  warning: null,
                }},
              }},
              capability_graph: {{
                tools_capabilities: {{
                  apply_capabilities: {{
                    "tools.enabled": {{
                      apply_capable: false,
                      requires_restart: true,
                      blocker: "tool registry enable/disable is not apply-capable in POC; requires daemon restart",
                    }},
                    "tools.network_enabled": {{
                      apply_capable: false,
                      requires_restart: true,
                      blocker: "network capability is registry-backed; no live network tool toggle is wired in POC",
                    }},
                    "security.require_approval_for_network": {{
                      apply_capable: false,
                      requires_restart: true,
                      blocker: "runtime tool/network policy reload not implemented in POC; requires daemon restart",
                    }},
                    "security.require_approval_for_shell": {{
                      apply_capable: false,
                      requires_restart: true,
                      blocker: "runtime tool/network policy reload not implemented in POC; requires daemon restart",
                    }},
                    "security.require_approval_for_file_write": {{
                      apply_capable: false,
                      requires_restart: true,
                      blocker: "runtime tool/network policy reload not implemented in POC; requires daemon restart",
                    }},
                  }},
                }},
              }},
              settings_preview: {{
                sections: {{
                  tools_internet: {{
                    fields: {{
                      tools_support: {{ current: "yes", effective: "yes", status: "ok" }},
                    }},
                  }},
                }},
              }},
            }};
            context.payload = payload;

            vm.runInContext(`
              el.activeToolsSettingsSection = createNode("section");
              el.toolsSectionDescription = createNode("p");
              el.toolsControlGrid = createNode("div");
              el.toolsSummaryDetails = createNode("dl");
              el.toolsEnabledToggle = createNode("input");
              el.toolsNetworkEnabledToggle = createNode("input");
              el.toolsNetworkApprovalToggle = createNode("input");
              el.toolsShellApprovalToggle = createNode("input");
              el.toolsFileWriteApprovalToggle = createNode("input");
              el.toolsInternetSummary = createNode("dd");
              el.toolsNetworkPolicySummary = createNode("dd");
              el.toolsRegistrySummary = createNode("dd");
              el.toolsInternetStatusList = createNode("div");
              el.toolsApplyStatus = createNode("p");
              el.resetToolsPreviewButton = createNode("button");
              el.applyToolsSettingsButton = createNode("button");
              el.activeSettingsStatus = createNode("p");
              cockpit.online = true;
              cockpit.runtimeSettingsApply.payload = payload;
              renderToolsApplyControls(payload);
              globalThis.__el = el;
            `, context);

            assert.strictEqual(context.__el.toolsEnabledToggle.disabled, true);
            assert.strictEqual(context.__el.toolsNetworkEnabledToggle.disabled, true);
            assert.strictEqual(context.__el.toolsNetworkApprovalToggle.disabled, true);
            assert.strictEqual(context.__el.toolsShellApprovalToggle.disabled, true);
            assert.strictEqual(context.__el.toolsFileWriteApprovalToggle.disabled, true);
            assert.strictEqual(context.__el.applyToolsSettingsButton.disabled, true);
            assert.strictEqual(context.__el.resetToolsPreviewButton.disabled, true);
            assert.strictEqual(context.__el.resetToolsPreviewButton.hidden, true);
            assert.strictEqual(context.__el.toolsSectionDescription.hidden, true);
            assert.strictEqual(context.__el.activeToolsSettingsSection.classList.contains("compact-only"), true);
            assert.strictEqual(context.__el.toolsControlGrid.hidden, true);
            assert.strictEqual(context.__el.toolsSummaryDetails.hidden, true);
            assert.match(context.__el.toolsInternetSummary.textContent, /Missing|Unavailable/i);
            assert.match(context.__el.toolsApplyStatus.textContent, /Blocked/i);
            assert.match(context.__el.toolsApplyStatus.textContent, /Install\\/register a network search tool/i);
            assert.doesNotMatch(context.__el.toolsApplyStatus.textContent, /runtime tool\\/network policy reload|not runtime-apply-capable|tools\\.enabled/i);

            const statusText = textOf(context.__el.toolsInternetStatusList);
            assert.match(statusText, /Tools \\/ Internet/i);
            assert.match(statusText, /Tools\\s+On/i);
            assert.match(statusText, /Internet access\\s+Missing/i);
            assert.match(statusText, /Reason\\s+No network\\/search tool registered/i);
            assert.match(statusText, /Action\\s+Install\\/register a network search tool/i);
            assert.match(statusText, /Policy\\s+Approval required/i);
            assert.match(statusText, /Apply\\s+disabled/i);
            assert.doesNotMatch(statusText, /What this means|What can I do\\?|tools\\.enabled|security\\.require_approval|approval_required|runtime tool\\/network policy reload|tool registry enable\\/disable|not runtime-apply-capable/i);

            payload.capability_graph.tools_capabilities.apply_capabilities["tools.enabled"] = {{
              apply_capable: true,
              requires_restart: false,
              blocker: null,
            }};
            payload.capability_graph.tools_capabilities.apply_capabilities["security.require_approval_for_network"] = {{
              apply_capable: true,
              requires_restart: false,
              blocker: null,
            }};
            vm.runInContext(`renderToolsApplyControls(payload);`, context);

            assert.strictEqual(context.__el.toolsControlGrid.hidden, true);
            assert.strictEqual(context.__el.toolsEnabledToggle.disabled, true);
            assert.strictEqual(context.__el.toolsNetworkApprovalToggle.disabled, true);
            assert.strictEqual(context.__el.applyToolsSettingsButton.disabled, true);
            assert.match(context.__el.toolsApplyStatus.textContent, /Apply disabled/i);
            assert.match(context.__el.toolsApplyStatus.textContent, /Install\\/register a network search tool/i);

            (async () => {{
              context.__el.toolsNetworkApprovalToggle.disabled = false;
              context.__el.toolsNetworkApprovalToggle.checked = false;
              context.__el.applyToolsSettingsButton.disabled = true;
              await context.applyRuntimeSettingsGroup("tools");
              assert.strictEqual(requests.length, 0);
            }})().catch((error) => {{
              console.error(error);
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


def test_system_unknown_runtime_values_disable_apply_and_do_not_create_pending_change(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "unknown-runtime-values-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                title: "",
                type: "",
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{
                  children.push(child);
                  return child;
                }},
                append(...nodes) {{
                  for (const node of nodes) this.appendChild(node);
                }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{
                  return children[0] || null;
                }},
                addEventListener() {{}},
              }};
            }}

            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              window: {{}},
              fetch: async () => {{
                throw new Error("unexpected request");
              }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const payload = {{
              brain: {{
                current_adapter: {{ effective_value: "claude_cli" }},
              }},
              voice: {{
                ptt_mode: {{ effective_value: null, status: "unknown" }},
              }},
              capability_graph: {{
                brain_capabilities: {{
                  current_provider: "claude_cli",
                  current_model: "claude-opus",
                  providers: [
                    {{
                      id: "claude_cli",
                      label: "Claude CLI",
                      available: true,
                      models: [{{ id: "claude-opus", label: "Claude Opus", available: true }}],
                      current_model: "claude-opus",
                      allowed_effort_values: ["low", "medium"],
                      fast_supported: false,
                      command_status: "yes",
                    }},
                  ],
                }},
              }},
              settings_preview: {{
                sections: {{
                  brain_provider: {{
                    label: "Brain / Provider",
                    fields: {{
                      provider: {{ id: "brain_provider.provider", label: "Provider", current: "claude_cli", effective: "claude_cli", status: "ok", editable_now: true }},
                      model: {{ id: "brain_provider.model", label: "Model", current: "claude-opus", effective: "claude-opus", status: "ok", editable_now: true }},
                      effort: {{ id: "brain_provider.effort", label: "Effort", current: null, effective: null, status: "unknown", source: "runtime_detected", allowed_values: ["low", "medium"], editable_now: true }},
                      fast: {{ id: "brain_provider.fast", label: "Fast mode", current: null, effective: null, status: "unknown", source: "runtime_detected", editable_now: false }},
                    }},
                  }},
                  endpointing_ptt: {{
                    label: "Endpointing / PTT",
                    fields: {{
                      ptt_mode: {{ id: "endpointing_ptt.ptt_mode", label: "PTT mode", current: null, effective: null, status: "unknown", source: "runtime_detected", allowed_values: ["hold"], editable_now: true }},
                      merge_window: {{ id: "endpointing_ptt.merge_window", label: "Merge window", current: null, effective: null, status: "unknown", source: "runtime_detected", editable_now: false }},
                      listening_lease_state: {{ id: "endpointing_ptt.listening_lease_state", label: "Listening mode", current: null, effective: null, status: "unknown", source: "runtime_detected" }},
                      interrupt_policy: {{ id: "endpointing_ptt.interrupt_policy", label: "Interrupt policy", current: null, effective: null, status: "unknown", source: "runtime_detected" }},
                      ptt_hotkey: {{ id: "endpointing_ptt.ptt_hotkey", label: "PTT hotkey", current: null, effective: null, status: "unknown", source: "runtime_detected" }},
                    }},
                  }},
                }},
              }},
            }};

            context.payload = payload;
            vm.runInContext(`
              el.activeBrainProviderSelect = createNode("select");
              el.activeBrainModelSelect = createNode("select");
              el.activeBrainEffortSelect = createNode("select");
              el.activeBrainFastToggle = createNode("input");
              el.brainSettingsSummaryList = createNode("div");
              el.brainApplyStatus = createNode("p");
              el.applyBrainSettingsButton = createNode("button");
              el.pttModeSelect = createNode("select");
              el.pttMergeWindowInput = createNode("input");
              el.pttListeningSummary = createNode("dd");
              el.pttBargeInSummary = createNode("dd");
              el.pttHotkeySummary = createNode("dd");
              el.pttVadSummary = createNode("dd");
              el.pttApplyStatus = createNode("p");
              el.applyPttSettingsButton = createNode("button");
              cockpit.online = true;
              cockpit.runtimeSettingsApply.payload = payload;
              cockpit.settingsPreview.payload = payload;
              globalThis.__el = el;
              globalThis.__brainDraft = {{ "brain.effort": "low" }};
              globalThis.__pttDraft = {{ "voice.ptt_mode": "hold" }};
            `, context);

            assert.deepStrictEqual(
              JSON.parse(JSON.stringify(context.runtimeSettingsChangedKeys("brain", context.__brainDraft, payload))),
              [],
            );
            assert.strictEqual(
              context.runtimeSettingsPendingMessage("brain", context.__brainDraft, payload),
              "",
            );
            assert.deepStrictEqual(
              JSON.parse(JSON.stringify(context.runtimeSettingsChangedKeys("ptt", context.__pttDraft, payload))),
              [],
            );

            context.renderBrainApplyControls(payload);
            assert.doesNotMatch(context.__el.brainApplyStatus.textContent, /Unknown -> low/i);
            assert.match(context.__el.brainApplyStatus.textContent, /Effective value: Unknown/i);
            assert.match(context.__el.brainApplyStatus.textContent, /Reason: runtime does not report this setting/i);
            assert.match(context.__el.brainApplyStatus.textContent, /Apply disabled/i);
            assert.strictEqual(context.__el.activeBrainEffortSelect.disabled, true);
            assert.strictEqual(context.__el.applyBrainSettingsButton.disabled, true);

            context.renderPttApplyControls(payload);
            assert.doesNotMatch(context.__el.pttApplyStatus.textContent, /Unknown -> hold/i);
            assert.match(context.__el.pttApplyStatus.textContent, /Effective value: Unknown/i);
            assert.match(context.__el.pttApplyStatus.textContent, /Reason: runtime does not report this setting/i);
            assert.strictEqual(context.__el.pttModeSelect.disabled, true);
            assert.strictEqual(context.__el.applyPttSettingsButton.disabled, true);
            assert.match(context.__el.pttListeningSummary.textContent, /Effective value: Unknown/i);
            assert.match(context.__el.pttListeningSummary.textContent, /runtime does not report this setting/i);
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


def test_claude_cli_provider_contract_renders_and_apply_semantics_gate(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "claude-cli-contract-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                childNodes: children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                title: "",
                type: "",
                dataset: {{}},
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{
                  children.push(child);
                  return child;
                }},
                append(...nodes) {{
                  for (const node of nodes) this.appendChild(node);
                }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{
                  return children[0] || null;
                }},
                addEventListener() {{}},
              }};
            }}

            function collectText(node) {{
              if (!node) return "";
              return [node.textContent || "", ...(node.children || []).map(collectText)].join(" ");
            }}

            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              window: {{}},
              fetch: async () => {{
                throw new Error("unexpected request");
              }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            function payloadWithSemantics(applySemantics) {{
              return {{
                brain: {{
                  current_adapter: {{ effective_value: "claude_cli" }},
                }},
                capability_graph: {{
                  brain_capabilities: {{
                    current_provider: "claude_cli",
                    current_model: "claude-sonnet",
                    providers: [
                      {{
                        id: "claude_cli",
                        provider_id: "claude_cli",
                        label: "Claude CLI",
                        kind: "cli",
                        transport: "subprocess",
                        available: true,
                        models: [{{ id: "claude-sonnet", label: "Claude Sonnet", available: true }}],
                        current_model: "claude-sonnet",
                        selected_model: "claude-sonnet",
                        effective_model: "claude-sonnet",
                        model_source: "jarvis_explicit",
                        allowed_effort_values: ["low", "xhigh"],
                        selected_effort: "xhigh",
                        effective_effort: "xhigh",
                        effort_source: "jarvis_explicit",
                        fast_supported: false,
                        command_status: "found",
                        auth_status: "logged_in",
                        permission_mode: "acceptEdits",
                        tools: ["Bash", "Read"],
                        allowed_tools: ["file_read"],
                        disallowed_tools: ["network"],
                        mcp_config_status: "missing",
                        strict_mcp_config: "unknown",
                        output_format: "stream-json",
                        input_format: "text",
                        streaming_supported: true,
                        streaming_supported_state: "yes",
                        partial_messages_supported: "yes",
                        hook_events_supported: "unknown",
                        apply_semantics: applySemantics,
                        apply_eligibility: {{
                          mode: applySemantics,
                          next_turn: applySemantics === "next_turn",
                          reason: applySemantics === "next_turn" ? null : "Backend says Claude must start a new provider session.",
                        }},
                        command_preview: "claude -p --model claude-sonnet --effort xhigh --permission-mode acceptEdits --tools Bash,Read --output-format stream-json",
                      }},
                    ],
                  }},
                }},
                settings_preview: {{
                  sections: {{
                    brain_provider: {{
                      label: "Brain / Provider",
                      fields: {{
                        provider: {{ id: "brain_provider.provider", label: "Provider", current: "claude_cli", effective: "claude_cli", status: "ok", editable_now: true }},
                        model: {{ id: "brain_provider.model", label: "Model", current: "claude-sonnet", effective: "claude-sonnet", status: "ok", editable_now: true, allowed_values: ["claude-sonnet"] }},
                        effort: {{ id: "brain_provider.effort", label: "Effort", current: "xhigh", effective: "xhigh", status: "ok", allowed_values: ["low", "xhigh"], editable_now: true }},
                        fast: {{ id: "brain_provider.fast", label: "Fast", current: false, effective: false, status: "unsupported", editable_now: false }},
                        command_preview: {{ id: "brain_provider.command_preview", label: "Next-turn command preview", current: "claude -p --model claude-sonnet --effort xhigh", effective: "claude -p --model claude-sonnet --effort xhigh", status: "ok" }},
                        apply_semantics: {{ id: "brain_provider.apply_semantics", label: "Apply semantics", current: applySemantics, effective: applySemantics, status: applySemantics === "next_turn" ? "ok" : "unsupported" }},
                      }},
                    }},
                  }},
                }},
              }};
            }}

            vm.runInContext(`
              el.activeBrainProviderSelect = createNode("select");
              el.activeBrainModelSelect = createNode("select");
              el.activeBrainEffortSelect = createNode("select");
              el.activeBrainFastToggle = createNode("input");
              el.brainSettingsSummaryList = createNode("div");
              el.brainApplyStatus = createNode("p");
              el.applyBrainSettingsButton = createNode("button");
              cockpit.online = true;
              globalThis.__el = el;
            `, context);

            const nextTurnPayload = payloadWithSemantics("next_turn");
            context.nextTurnPayload = nextTurnPayload;
            vm.runInContext(`
              cockpit.runtimeSettingsApply.payload = nextTurnPayload;
              renderBrainApplyControls(nextTurnPayload);
            `, context);
            const summary = collectText(context.__el.brainSettingsSummaryList);
            assert.match(summary, /Claude CLI provider contract/);
            assert.match(summary, /claude-sonnet/);
            assert.match(summary, /xhigh/);
            assert.match(summary, /acceptEdits/);
            assert.match(summary, /Bash/);
            assert.match(summary, /stream-json/);
            assert.match(summary, /--model claude-sonnet/);
            assert.doesNotMatch(summary, /sk-/);

            const newSessionPayload = payloadWithSemantics("requires_new_session");
            context.newSessionPayload = newSessionPayload;
            vm.runInContext(`
              cockpit.runtimeSettingsApply.payload = newSessionPayload;
              renderBrainApplyControls(newSessionPayload);
            `, context);
            assert.strictEqual(context.__el.applyBrainSettingsButton.disabled, true);
            assert.match(context.__el.brainApplyStatus.textContent, /new provider session/i);
            assert.strictEqual(
              context.providerApplyBlocker(newSessionPayload.capability_graph.brain_capabilities.providers[0], "claude_cli"),
              "Backend says Claude must start a new provider session.",
            );
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


def test_settings_apply_feedback_preserves_pending_preview_and_reset_is_explicit(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "settings-apply-feedback-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                type: "",
                title: "",
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{ children.push(child); return child; }},
                append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{ return children[0] || null; }},
                addEventListener() {{}},
              }};
            }}

            function payloadWithSpeakResponses(value) {{
              return {{
                voice: {{
                  speak_responses: {{
                    value,
                    effective_value: value,
                    source: "config",
                    status: "ok",
                    editable_now: true,
                    warning: null,
                  }},
                  broker_enabled: {{ effective_value: false }},
                  default_tts: {{ effective_value: "mock" }},
                  default_stt: {{ effective_value: "mock" }},
                }},
                capability_graph: {{
                  voice_capabilities: {{
                    tts_providers: [
                      {{ id: "mock", label: "Mock", available: true }},
                    ],
                    stt_providers: [],
                  }},
                }},
                settings_preview: {{
                  sections: {{
                    voice_tts: {{
                    fields: {{
                        tts_provider: {{ current: "mock", effective: "mock", editable_now: true, status: "ok" }},
                        tts_model: {{ current: "", effective: "", allowed_values: [], editable_now: true, status: "ok" }},
                        voice_id: {{ current: "", effective: "", allowed_values: [], editable_now: true, status: "ok" }},
                        voice_profile: {{ current: "", effective: "", allowed_values: [], editable_now: true, status: "ok" }},
                        speed_or_rate: {{ current: 1, effective: 1, editable_now: true, status: "ok" }},
                      }},
                    }},
                  }},
                }},
              }};
            }}

            const requests = [];
            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              window: {{}},
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              fetch: async (url, init = {{}}) => {{
                const parsed = new URL(url);
                requests.push({{ path: parsed.pathname, init }});
                return {{
                  ok: true,
                  status: 200,
                  text: async () => JSON.stringify(payloadWithSpeakResponses(false)),
                }};
              }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const effectiveFalse = payloadWithSpeakResponses(false);
            context.payload = effectiveFalse;
            vm.runInContext(`
              el.voiceSpeakResponsesToggle = createNode("input");
              el.voiceBrokerEnabledToggle = createNode("input");
              el.voiceTtsSelect = createNode("select");
              el.voiceTtsModelSelect = createNode("select");
              el.voiceVoiceIdSelect = createNode("select");
              el.voiceProfileSelect = createNode("select");
              el.voiceSpeedInput = createNode("input");
              el.voiceTtsStatusList = createNode("div");
              el.ttsApplyStatus = createNode("p");
              el.voiceApplyStatus = createNode("p");
              el.applyTtsSettingsButton = createNode("button");
              el.activeSettingsStatus = createNode("p");
              el.settingsPreviewList = createNode("div");
              el.settingsPreviewError = createNode("p");
              cockpit.online = true;
              cockpit.runtimeSettingsApply.payload = payload;
              cockpit.settingsPreview.payload = payload;
              renderTtsApplyControls(payload);
              globalThis.__el = el;
            `, context);

            assert.strictEqual(context.__el.voiceSpeakResponsesToggle.checked, false);
            context.__el.voiceSpeakResponsesToggle.checked = true;
            context.updateTtsControlOptions();
            assert.match(context.__el.ttsApplyStatus.textContent, /Pending/i);
            assert.match(context.__el.ttsApplyStatus.textContent, /OFF -> ON|false -> true/i);
            assert.strictEqual(context.__el.applyTtsSettingsButton.disabled, false);
            assert.strictEqual(requests.length, 0);

            context.payload = payloadWithSpeakResponses(false);
            vm.runInContext(`
              cockpit.runtimeSettingsApply.payload = payload;
              renderTtsApplyControls(payload);
            `, context);
            assert.strictEqual(context.__el.voiceSpeakResponsesToggle.checked, true);
            assert.match(context.__el.ttsApplyStatus.textContent, /pending preview preserved|Pending/i);

            context.resetSettingsPreview();
            assert.strictEqual(context.__el.voiceSpeakResponsesToggle.checked, false);
            assert.match(context.__el.ttsApplyStatus.textContent, /Preview reset|No live TTS changes/i);
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


def test_brain_apply_uses_backend_valid_next_turn_fields_only(tmp_path: Path) -> None:
    harness = tmp_path / "brain-backend-apply-plan-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                childNodes: children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                title: "",
                type: "",
                dataset: {{}},
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{ children.push(child); return child; }},
                append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{ return children[0] || null; }},
                addEventListener() {{}},
              }};
            }}

            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              window: {{}},
              fetch: async () => {{ throw new Error("unexpected request"); }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{ filename: "app.js" }});

            const payload = {{
              brain: {{ current_adapter: {{ effective_value: "claude_cli" }} }},
              capability_graph: {{
                brain_capabilities: {{
                  current_provider: "claude_cli",
                  current_model: "claude-old",
                  providers: [
                    {{
                      id: "claude_cli",
                      provider_id: "claude_cli",
                      label: "Claude CLI",
                      kind: "cli",
                      transport: "subprocess",
                      available: true,
                      models: [
                        {{ id: "claude-old", label: "Claude Old", available: true }},
                        {{ id: "claude-new", label: "Claude New", available: true }},
                      ],
                      current_model: "claude-old",
                      selected_model: "claude-old",
                      effective_model: "claude-old",
                      model_source: "jarvis_explicit",
                      allowed_effort_values: ["low", "high"],
                      selected_effort: "low",
                      effective_effort: "low",
                      effort_source: "jarvis_explicit",
                      fast_supported: false,
                      command_status: "found",
                      auth_status: "logged_in",
                      permission_mode: "manual",
                      tools: [],
                      allowed_tools: [],
                      disallowed_tools: [],
                      output_format: "stream-json",
                      input_format: "text",
                      streaming_supported: true,
                      streaming_supported_state: "yes",
                      partial_messages_supported: "yes",
                      apply_semantics: "next_turn",
                      apply_capable: true,
                      apply_disabled_reason: null,
                      command_preview: "fake-claude -p --model claude-old --effort low",
                    }},
                    {{
                      id: "mock",
                      label: "mock/dev",
                      kind: "Developer/Test",
                      developer_only: true,
                      available: true,
                      models: [{{ id: "mock-local", label: "mock-local", available: true }}],
                      current_model: "mock-local",
                      allowed_effort_values: [],
                      command_status: "found",
                      apply_semantics: "not_apply_capable",
                    }},
                  ],
                }},
              }},
              settings_preview: {{
                sections: {{
                  brain_provider: {{
                    label: "Brain / Provider",
                    apply_semantics: "next_turn",
                    apply_capable: true,
                    apply_disabled_reason: null,
                    pending_changes: [],
                    valid_next_turn_changes: ["brain.model"],
                    requires_new_session_changes: ["brain.provider", "brain.effort"],
                    requires_restart_changes: [],
                    fields: {{
                      provider: {{ id: "brain_provider.provider", label: "Provider", current: "claude_cli", effective: "claude_cli", status: "ok", editable_now: false, editable_later: true, allowed_values: ["claude_cli"], disabled_values: [{{ value: "mock", reason: "Developer/Test only." }}] }},
                      model: {{ id: "brain_provider.model", label: "Model", current: "claude-old", effective: "claude-old", status: "ok", editable_now: true, allowed_values: ["claude-old", "claude-new"] }},
                      effort: {{ id: "brain_provider.effort", label: "Effort", current: "low", effective: "low", status: "ok", editable_now: true, allowed_values: ["low", "high"] }},
                      fast: {{ id: "brain_provider.fast", label: "Fast", current: false, effective: false, status: "unsupported", editable_now: false }},
                      command_preview: {{ id: "brain_provider.command_preview", label: "Next-turn command preview", current: "fake-claude -p --model claude-old --effort low", effective: "fake-claude -p --model claude-old --effort low", status: "ok" }},
                      apply_semantics: {{ id: "brain_provider.apply_semantics", label: "Apply semantics", current: "next_turn", effective: "next_turn", status: "ok" }},
                    }},
                  }},
                }},
              }},
            }};
            context.payload = payload;

            vm.runInContext(`
              el.activeBrainProviderSelect = createNode("select");
              el.activeBrainModelSelect = createNode("select");
              el.activeBrainEffortSelect = createNode("select");
              el.activeBrainFastToggle = createNode("input");
              el.brainSettingsSummaryList = createNode("div");
              el.brainApplyStatus = createNode("p");
              el.applyBrainSettingsButton = createNode("button");
              cockpit.online = true;
              cockpit.runtimeSettingsApply.payload = payload;
              cockpit.settingsPreview.payload = payload;
              cockpit.settingsPreview.model = settingsPreviewModelFromPayload(payload);
              globalThis.__model = cockpit.settingsPreview.model;
              globalThis.__el = el;
            `, context);

            const evaluatedProvider = context.settingsPreviewFieldById(context.__model, "brain_provider.provider");
            assert.deepStrictEqual(JSON.parse(JSON.stringify(evaluatedProvider.allowed_values)), ["claude_cli"]);

            vm.runInContext(`
              renderBrainApplyControls(payload);
              el.activeBrainModelSelect.value = "claude-new";
              el.activeBrainEffortSelect.value = "high";
            `, context);

            const draft = context.runtimeSettingsPayloadForGroup("brain", context.runtimeSettingsDraftForGroup("brain"));
            const filtered = context.runtimeSettingsApplyPayloadForGroup("brain", draft);
            assert.deepStrictEqual(JSON.parse(JSON.stringify(filtered)), {{
              "brain.model": "claude-new",
            }});
            assert.match(context.runtimeSettingsPendingMessage("brain", draft, payload), /model/i);
            assert.doesNotMatch(context.runtimeSettingsPendingMessage("brain", draft, payload), /Effort/);
            assert.match(context.runtimeSettingsGroupApplyBlockedReason("brain", draft, payload), /Not apply-capable|Requires/);
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


def test_codex_brain_apply_uses_backend_model_only_pending_rows(tmp_path: Path) -> None:
    harness = tmp_path / "codex-brain-model-only-apply-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                childNodes: children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                title: "",
                type: "",
                dataset: {{}},
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{ children.push(child); return child; }},
                append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{ return children[0] || null; }},
                addEventListener() {{}},
              }};
            }}

            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              window: {{}},
              fetch: async () => {{ throw new Error("unexpected request"); }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{ filename: "app.js" }});

            const payload = {{
              brain: {{ current_adapter: {{ effective_value: "codex_cli" }} }},
              capability_graph: {{
                brain_capabilities: {{
                  current_provider: "codex_cli",
                  current_model: "codex-old",
                  providers: [
                    {{
                      id: "codex_cli",
                      provider_id: "codex_cli",
                      label: "Codex CLI",
                      kind: "cli",
                      transport: "subprocess",
                      available: true,
                      models: [
                        {{ id: "codex-old", label: "Codex Old", available: true }},
                        {{ id: "codex-new", label: "Codex New", available: true }},
                      ],
                      current_model: "codex-old",
                      selected_model: "codex-old",
                      effective_model: "codex-old",
                      model_source: "jarvis_explicit",
                      allowed_effort_values: [],
                      fast_supported: false,
                      command_status: "found",
                      auth_status: "logged_in",
                      apply_semantics: "next_turn",
                      apply_capable: true,
                      apply_disabled_reason: null,
                      command_preview: "fake-codex exec --model codex-old",
                    }},
                    {{
                      id: "mock",
                      label: "mock/dev",
                      kind: "Developer/Test",
                      developer_only: true,
                      available: true,
                      models: [{{ id: "mock-local", label: "mock-local", available: true }}],
                      current_model: "mock-local",
                      allowed_effort_values: [],
                      command_status: "found",
                      apply_semantics: "not_apply_capable",
                    }},
                  ],
                }},
              }},
              settings_preview: {{
                sections: {{
                  brain_provider: {{
                    label: "Brain / Provider",
                    apply_semantics: "next_turn",
                    apply_capable: true,
                    apply_disabled_reason: null,
                    pending_changes: [],
                    valid_next_turn_changes: ["brain.model"],
                    requires_new_session_changes: ["brain.provider"],
                    requires_restart_changes: [],
                    fields: {{
                      provider: {{ id: "brain_provider.provider", label: "Provider", current: "codex_cli", effective: "codex_cli", status: "ok", editable_now: false, editable_later: true, allowed_values: ["codex_cli"], disabled_values: [{{ value: "mock", reason: "Developer/Test only." }}] }},
                      model: {{ id: "brain_provider.model", label: "Model", current: "codex-old", effective: "codex-old", status: "ok", editable_now: true, allowed_values: ["codex-old", "codex-new"], apply_capable: true }},
                      effort: {{ id: "brain_provider.effort", label: "Effort", current: null, effective: null, status: "unsupported", editable_now: false, allowed_values: [], apply_capable: false, apply_disabled_reason: "not_apply_capable" }},
                      fast: {{ id: "brain_provider.fast", label: "Fast", current: false, effective: false, status: "unsupported", editable_now: false, apply_capable: false }},
                      command_preview: {{ id: "brain_provider.command_preview", label: "Next-turn command preview", current: "fake-codex exec --model codex-old", effective: "fake-codex exec --model codex-old", status: "ok" }},
                      apply_semantics: {{ id: "brain_provider.apply_semantics", label: "Apply semantics", current: "next_turn", effective: "next_turn", status: "ok" }},
                    }},
                  }},
                }},
              }},
            }};
            context.payload = payload;

            vm.runInContext(`
              el.activeBrainProviderSelect = createNode("select");
              el.activeBrainModelSelect = createNode("select");
              el.activeBrainEffortSelect = createNode("select");
              el.activeBrainFastToggle = createNode("input");
              el.brainSettingsSummaryList = createNode("div");
              el.brainApplyStatus = createNode("p");
              el.applyBrainSettingsButton = createNode("button");
              cockpit.online = true;
              cockpit.runtimeSettingsApply.payload = payload;
              cockpit.settingsPreview.payload = payload;
              renderBrainApplyControls(payload);
              el.activeBrainModelSelect.value = "codex-new";
              updateBrainControlOptions();
              globalThis.__el = el;
            `, context);

            const providerValues = context.__el.activeBrainProviderSelect.childNodes.map((option) => option.value);
            assert.deepStrictEqual(providerValues, ["codex_cli"]);
            assert.strictEqual(context.__el.activeBrainEffortSelect.disabled, true);
            const draft = context.runtimeSettingsPayloadForGroup("brain", context.runtimeSettingsDraftForGroup("brain"));
            const filtered = context.runtimeSettingsApplyPayloadForGroup("brain", draft);
            assert.deepStrictEqual(JSON.parse(JSON.stringify(filtered)), {{
              "brain.model": "codex-new",
            }});
            assert.match(context.runtimeSettingsPendingMessage("brain", draft, payload), /model/i);
            assert.doesNotMatch(context.runtimeSettingsPendingMessage("brain", draft, payload), /Effort|permission/i);
            assert.strictEqual(context.__el.applyBrainSettingsButton.disabled, false);
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


def test_codex_brain_apply_disabled_for_missing_auth_and_unknown_model_values(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "codex-brain-disabled-auth-unknown-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            function createNode(tag) {{
              const children = [];
              const classes = new Set();
              return {{
                tagName: tag,
                children,
                childNodes: children,
                textContent: "",
                className: "",
                hidden: false,
                disabled: false,
                checked: false,
                value: "",
                title: "",
                type: "",
                dataset: {{}},
                classList: {{
                  toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                  contains: (name) => classes.has(name),
                }},
                appendChild(child) {{ children.push(child); return child; }},
                append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                removeChild(child) {{
                  const index = children.indexOf(child);
                  if (index >= 0) children.splice(index, 1);
                  return child;
                }},
                get firstChild() {{ return children[0] || null; }},
                addEventListener() {{}},
              }};
            }}

            const requests = [];
            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41741" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              createNode,
              document: {{
                addEventListener: () => {{}},
                createElement: createNode,
              }},
              window: {{}},
              fetch: async (url, init = {{}}) => {{
                const parsed = new URL(url);
                requests.push({{ path: parsed.pathname, method: init.method || "GET" }});
                return {{
                  ok: true,
                  status: 200,
                  text: async () => JSON.stringify({{ ok: true, runtime_settings: {{}} }}),
                }};
              }},
            }};
            context.window.localStorage = context.localStorage;
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{ filename: "app.js" }});

            function codexPayload(overrides = {{}}) {{
              const section = overrides.section || {{
                apply_semantics: "not_apply_capable",
                apply_capable: false,
                apply_disabled_reason: "missing_auth",
                pending_changes: [],
                valid_next_turn_changes: [],
                requires_new_session_changes: [],
                requires_restart_changes: [],
              }};
              return {{
                brain: {{ current_adapter: {{ effective_value: "codex_cli" }} }},
                capability_graph: {{
                  brain_capabilities: {{
                    current_provider: "codex_cli",
                    current_model: overrides.currentModel || "codex-old",
                    providers: [{{
                      id: "codex_cli",
                      provider_id: "codex_cli",
                      label: "Codex CLI",
                      kind: "cli",
                      transport: "subprocess",
                      available: overrides.available !== false,
                      models: [{{ id: "codex-old", label: "Codex Old", available: true }}, {{ id: "codex-new", label: "Codex New", available: true }}],
                      current_model: overrides.currentModel || "codex-old",
                      selected_model: overrides.currentModel || "codex-old",
                      effective_model: overrides.currentModel || "codex-old",
                      model_source: "jarvis_explicit",
                      allowed_effort_values: [],
                      fast_supported: false,
                      command_status: "found",
                      auth_status: overrides.authStatus || "missing",
                      apply_semantics: section.apply_semantics,
                      apply_capable: section.apply_capable,
                      apply_disabled_reason: section.apply_disabled_reason,
                      apply_semantics_reason: overrides.reason || "Codex CLI auth is missing.",
                      command_preview: "fake-codex exec --model codex-old",
                    }}],
                  }},
                }},
                settings_preview: {{
                  sections: {{
                    brain_provider: {{
                      label: "Brain / Provider",
                      ...section,
                      fields: {{
                        provider: {{ id: "brain_provider.provider", label: "Provider", current: "codex_cli", effective: "codex_cli", status: "ok", editable_now: false, editable_later: true, allowed_values: ["codex_cli"] }},
                        model: {{ id: "brain_provider.model", label: "Model", current: overrides.fieldModel || "codex-old", effective: overrides.fieldModel || "codex-old", status: overrides.modelStatus || "ok", editable_now: Boolean(section.apply_capable), allowed_values: ["codex-old", "codex-new"], apply_capable: section.valid_next_turn_changes.includes("brain.model") }},
                        effort: {{ id: "brain_provider.effort", label: "Effort", current: null, effective: null, status: "unsupported", editable_now: false, allowed_values: [], apply_capable: false, apply_disabled_reason: "not_apply_capable" }},
                        fast: {{ id: "brain_provider.fast", label: "Fast", current: false, effective: false, status: "unsupported", editable_now: false, apply_capable: false }},
                      }},
                    }},
                  }},
                }},
              }};
            }}

            vm.runInContext(`
              el.activeBrainProviderSelect = createNode("select");
              el.activeBrainModelSelect = createNode("select");
              el.activeBrainEffortSelect = createNode("select");
              el.activeBrainFastToggle = createNode("input");
              el.brainSettingsSummaryList = createNode("div");
              el.brainApplyStatus = createNode("p");
              el.applyBrainSettingsButton = createNode("button");
              cockpit.online = true;
              globalThis.__el = el;
            `, context);

            const missingAuthPayload = codexPayload();
            context.payload = missingAuthPayload;
            vm.runInContext(`
              cockpit.runtimeSettingsApply.payload = payload;
              cockpit.settingsPreview.payload = payload;
              renderBrainApplyControls(payload);
              el.activeBrainModelSelect.value = "codex-new";
              updateBrainControlOptions();
            `, context);
            assert.strictEqual(context.__el.applyBrainSettingsButton.disabled, true);
            assert.match(context.__el.brainApplyStatus.textContent, /auth|not apply-capable/i);

            (async () => {{
              await context.applyRuntimeSettingsGroup("brain");
              assert.strictEqual(requests.length, 0);
            }})().catch((error) => {{
              console.error(error);
              process.exit(1);
            }});

            const unknownPayload = codexPayload({{
              currentModel: "unknown",
              fieldModel: "unknown",
              modelStatus: "unknown",
              authStatus: "logged_in",
              reason: "",
              section: {{
                apply_semantics: "next_turn",
                apply_capable: true,
                apply_disabled_reason: null,
                pending_changes: [],
                valid_next_turn_changes: ["brain.model"],
                requires_new_session_changes: [],
                requires_restart_changes: [],
              }},
            }});
            context.payload = unknownPayload;
            vm.runInContext(`
              cockpit.runtimeSettingsApply.payload = payload;
              cockpit.settingsPreview.payload = payload;
              renderBrainApplyControls(payload);
              el.activeBrainModelSelect.value = "codex-new";
              updateBrainControlOptions();
            `, context);
            const unknownDraft = context.runtimeSettingsPayloadForGroup("brain", context.runtimeSettingsDraftForGroup("brain"));
            assert.deepStrictEqual(
              JSON.parse(JSON.stringify(context.runtimeSettingsChangedKeys("brain", unknownDraft, unknownPayload))),
              [],
            );
            assert.strictEqual(context.runtimeSettingsPendingMessage("brain", unknownDraft, unknownPayload), "");
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


def test_brain_apply_disabled_for_reload_only_brain_field_changes(tmp_path: Path) -> None:
        harness = tmp_path / "brain-reload-only-disabled-harness.js"
        harness.write_text(
            textwrap.dedent(
                f"""
                const assert = require("assert");
                const fs = require("fs");
                const vm = require("vm");

                function createNode(tag) {{
                  const children = [];
                  const classes = new Set();
                  return {{
                    tagName: tag,
                    children,
                    textContent: "",
                    className: "",
                    hidden: false,
                    disabled: false,
                    checked: false,
                    value: "",
                    type: "",
                    classList: {{
                      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                      contains: (name) => classes.has(name),
                    }},
                    appendChild(child) {{ children.push(child); return child; }},
                    append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                    removeChild(child) {{
                      const index = children.indexOf(child);
                      if (index >= 0) children.splice(index, 1);
                      return child;
                    }},
                    get firstChild() {{ return children[0] || null; }},
                    addEventListener() {{}},
                  }};
                }}

                const requests = [];
                const context = {{
                  console,
                  URL,
                  location: {{ origin: "http://127.0.0.1:41741" }},
                  localStorage: {{
                    getItem: () => "token",
                    setItem: () => {{}},
                    removeItem: () => {{}},
                  }},
                  createNode,
                  window: {{}},
                  document: {{
                    addEventListener: () => {{}},
                    createElement: createNode,
                  }},
                  fetch: async (url, init = {{}}) => {{
                    const parsed = new URL(url);
                    requests.push({{ path: parsed.pathname, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null }});
                    return {{
                      ok: true,
                      status: 200,
                      text: async () => JSON.stringify({{ ok: true, runtime_settings: {{}} }}),
                    }};
                  }},
                }};
                context.window.localStorage = context.localStorage;
                context.globalThis = context;
                vm.createContext(context);
                vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
                  filename: "app.js",
                }});

                const payload = {{
                  brain: {{
                    current_adapter: {{ effective_value: "claude_cli" }},
                  }},
                  capability_graph: {{
                    brain_capabilities: {{
                      current_provider: "claude_cli",
                      current_model: "claude-opus",
                      providers: [{{
                        id: "claude_cli",
                        label: "Claude CLI",
                        available: true,
                        models: [{{ id: "claude-opus", label: "Claude Opus", available: true }}],
                        current_model: "claude-opus",
                        allowed_effort_values: ["low", "max"],
                        fast_supported: true,
                        command_status: "ok",
                      }}],
                    }},
                  }},
                  settings_preview: {{
                    sections: {{
                      brain_provider: {{
                        fields: {{
                          provider: {{ id: "brain_provider.provider", label: "Provider", current: "claude_cli", effective: "claude_cli", editable_now: true, status: "ok", allowed_values: ["claude_cli"] }},
                          model: {{ id: "brain_provider.model", label: "Model", current: "claude-opus", effective: "claude-opus", editable_now: true, status: "ok", allowed_values: ["claude-opus"] }},
                          effort: {{ id: "brain_provider.effort", label: "Effort", current: "low", effective: "low", editable_now: true, status: "ok", allowed_values: ["low", "max"], requires_restart: true }},
                          fast: {{ id: "brain_provider.fast", label: "Fast", current: false, effective: false, editable_now: true, status: "ok", allows: [] }},
                        }},
                      }},
                    }},
                  }},
                }};
                context.payload = payload;

                vm.runInContext(`
                  el.activeBrainProviderSelect = createNode("select");
                  el.activeBrainModelSelect = createNode("select");
                  el.activeBrainEffortSelect = createNode("select");
                  el.activeBrainFastToggle = createNode("input");
                  el.brainSettingsSummaryList = createNode("div");
                  el.brainApplyStatus = createNode("p");
                  el.applyBrainSettingsButton = createNode("button");
                  cockpit.online = true;
                  cockpit.runtimeSettingsApply.payload = payload;
                  cockpit.settingsPreview.payload = payload;
                  globalThis.__el = el;
                `, context);

                    vm.runInContext(`
                      renderBrainApplyControls(payload);
                      const draft = runtimeSettingsPayloadForGroup("brain", runtimeSettingsDraftForGroup("brain"));
                      draft["brain.effort"] = "max";
                      runtimeSettingsSetPreview("brain", draft);
                      el.activeBrainEffortSelect.value = "max";
                      updateBrainControlOptions();
                    `, context);

                    const changed = context.runtimeSettingsPayloadForGroup("brain", context.runtimeSettingsDraftForGroup("brain"));
                    assert.deepStrictEqual(
                      JSON.parse(JSON.stringify(context.runtimeSettingsChangedKeys("brain", changed, context.payload))),
                  ["brain.effort"],
                );
                assert.match(context.__el.brainApplyStatus.textContent, /Requires restart/);
                assert.strictEqual(context.__el.applyBrainSettingsButton.disabled, true);

                (async () => {{
                  await context.applyRuntimeSettingsGroup("brain");
                  assert.strictEqual(requests.length, 0);
                }})().catch((error) => {{
                  console.error(error);
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


def test_developer_only_brain_provider_preserved_without_auto_switch_or_pending(tmp_path: Path) -> None:
        harness = tmp_path / "brain-developer-provider-preserve-harness.js"
        harness.write_text(
            textwrap.dedent(
                f"""
                const assert = require("assert");
                const fs = require("fs");
                const vm = require("vm");

                function createNode(tag) {{
                  const children = [];
                  const classes = new Set();
                  return {{
                    tagName: tag,
                    children,
                    textContent: "",
                    className: "",
                    hidden: false,
                    disabled: false,
                    checked: false,
                    value: "",
                    type: "",
                    classList: {{
                      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
                      contains: (name) => classes.has(name),
                    }},
                    appendChild(child) {{ children.push(child); return child; }},
                    append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                    removeChild(child) {{
                      const index = children.indexOf(child);
                      if (index >= 0) children.splice(index, 1);
                      return child;
                    }},
                    get firstChild() {{ return children[0] || null; }},
                    get childNodes() {{ return this.children; }},
                    addEventListener() {{}},
                  }};
                }}

                const context = {{
                  console,
                  URL,
                  location: {{ origin: "http://127.0.0.1:41741" }},
                  localStorage: {{
                    getItem: () => "token",
                    setItem: () => {{}},
                    removeItem: () => {{}},
                  }},
                  createNode,
                  window: {{}},
                  document: {{
                    addEventListener: () => {{}},
                    createElement: createNode,
                  }},
                  fetch: async () => {{
                    throw new Error("unexpected request");
                  }},
                }};
                context.window.localStorage = context.localStorage;
                context.globalThis = context;
                vm.createContext(context);
                vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
                  filename: "app.js",
                }});

                const payload = {{
                  brain: {{
                    current_adapter: {{ effective_value: "mock" }},
                  }},
                  capability_graph: {{
                    brain_capabilities: {{
                      current_provider: "mock",
                      current_model: "mock-local",
                      providers: [
                        {{
                          id: "claude_cli",
                          label: "Claude CLI",
                          available: true,
                          developer_only: false,
                          models: [{{ id: "claude-opus", label: "Claude Opus", available: true }}],
                          current_model: "claude-opus",
                          allowed_effort_values: ["low", "high"],
                          fast_supported: true,
                        }},
                      ],
                    }},
                  }},
                  settings_preview: {{
                    sections: {{
                      brain_provider: {{
                        fields: {{
                          provider: {{ id: "brain_provider.provider", current: "mock", effective: "mock", status: "unknown", editable_now: false, allowed_values: ["claude_cli"] }},
                          model: {{ id: "brain_provider.model", current: null, effective: null, status: "missing", editable_now: false, allowed_values: [] }},
                          effort: {{ id: "brain_provider.effort", current: null, effective: null, status: "unsupported", editable_now: false, allowed_values: [] }},
                          fast: {{ id: "brain_provider.fast", current: null, effective: null, status: "unsupported", editable_now: false, allowed_values: [] }},
                        }},
                      }},
                    }},
                  }},
                }};
                context.payload = payload;

                    vm.runInContext(`
                      el.activeBrainProviderSelect = createNode("select");
                      el.activeBrainModelSelect = createNode("select");
                      el.activeBrainEffortSelect = createNode("select");
                      el.activeBrainFastToggle = createNode("input");
                      el.brainSettingsSummaryList = createNode("div");
                      el.brainApplyStatus = createNode("p");
                      el.applyBrainSettingsButton = createNode("button");
                      globalThis.__el = el;
                      cockpit.online = true;
                      cockpit.runtimeSettingsApply.payload = payload;
                      cockpit.settingsPreview.payload = payload;
                      renderBrainApplyControls(payload);
                    `, context);

                    const draft = JSON.parse(JSON.stringify(context.runtimeSettingsPayloadForGroup("brain", context.runtimeSettingsDraftForGroup("brain"))));
                    assert.deepStrictEqual(
                      JSON.parse(JSON.stringify(context.runtimeSettingsChangedKeys("brain", draft, payload))),
                      [],
                    );
                    assert.strictEqual(context.__el.activeBrainProviderSelect.value, "");
                    assert.strictEqual(context.__el.activeBrainProviderSelect.disabled, false);
                    assert.strictEqual(context.__el.applyBrainSettingsButton.disabled, true);
                    assert.doesNotMatch(context.__el.brainApplyStatus.textContent, /Pending change/i);
                    if (context.__el.activeBrainProviderSelect.childNodes.length) {{
                      assert.strictEqual(context.__el.activeBrainProviderSelect.childNodes[0].textContent, "No normal provider active");
                      assert.doesNotMatch(context.__el.activeBrainProviderSelect.childNodes[0].textContent, /mock|Developer\\/Test/i);
                    }}
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


def test_personality_apply_disabled_when_persona_requires_restart(tmp_path: Path) -> None:
        harness = tmp_path / "personality-requires-restart-harness.js"
        harness.write_text(
            textwrap.dedent(
                f"""
                const assert = require("assert");
                const fs = require("fs");
                const vm = require("vm");

                function createNode(tag) {{
                  const children = [];
                  return {{
                    tagName: tag,
                    children,
                    textContent: "",
                    className: "",
                    hidden: false,
                    disabled: false,
                    checked: false,
                    value: "",
                    type: "",
                    appendChild(child) {{ children.push(child); return child; }},
                    append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                    removeChild(child) {{
                      const index = children.indexOf(child);
                      if (index >= 0) children.splice(index, 1);
                      return child;
                    }},
                    get firstChild() {{ return children[0] || null; }},
                    addEventListener() {{}},
                  }};
                }}

                const requests = [];
                const context = {{
                  console,
                  URL,
                  location: {{ origin: "http://127.0.0.1:41741" }},
                  localStorage: {{
                    getItem: () => "token",
                    setItem: () => {{}},
                    removeItem: () => {{}},
                  }},
                  createNode,
                  window: {{}},
                  document: {{
                    addEventListener: () => {{}},
                    createElement: createNode,
                  }},
                  fetch: async (url, init = {{}}) => {{
                    const parsed = new URL(url);
                    requests.push({{ path: parsed.pathname, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null }});
                    return {{
                      ok: true,
                      status: 200,
                      text: async () => JSON.stringify({{ runtime_settings: {{}} }}),
                    }};
                  }},
                }};
                context.window.localStorage = context.localStorage;
                context.globalThis = context;
                vm.createContext(context);
                vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
                  filename: "app.js",
                }});

                const payload = {{
                  capability_graph: {{
                    brain_capabilities: {{
                      current_provider: "claude_cli",
                      providers: [{{ id: "claude_cli", available: true, models: [], allowed_effort_values: [], fast_supported: false }}],
                    }},
                  }},
                  settings_preview: {{
                    sections: {{
                      personality: {{
                        fields: {{
                          active_persona: {{ id: "personality.active_persona", current: "default", effective: "default", editable_now: true, status: "ok", allowed_values: ["default", "friend"], requires_restart: true }},
                        }},
                      }},
                    }},
                  }},
                }};
                context.payload = payload;

                vm.runInContext(`
                  el.personaProfileSelect = createNode("select");
                  el.personalityStatusList = createNode("div");
                  el.personaApplyStatus = createNode("p");
                  el.applyPersonaSettingsButton = createNode("button");
                  globalThis.__el = el;
                  cockpit.online = true;
                  cockpit.runtimeSettingsApply.payload = payload;
                  cockpit.settingsPreview.payload = payload;
                  renderPersonaApplyControls(payload);
                  el.personaProfileSelect.value = "friend";
                  updatePersonaControlOptions();
                `, context);

                assert.match(context.__el.personaApplyStatus.textContent, /Requires restart/);
                assert.strictEqual(context.__el.applyPersonaSettingsButton.disabled, true);
                (async () => {{
                  await context.applyRuntimeSettingsGroup("persona");
                  assert.strictEqual(requests.length, 0);
                }})().catch((error) => {{
                  console.error(error);
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


def test_tts_speak_responses_disabled_when_tts_group_blocked_and_disabled_without_independent_apply(tmp_path: Path) -> None:
        harness = tmp_path / "tts-speak-responses-blocked-harness.js"
        harness.write_text(
            textwrap.dedent(
                f"""
                const assert = require("assert");
                const fs = require("fs");
                const vm = require("vm");

                function createNode(tag) {{
                  const children = [];
                  return {{
                    tagName: tag,
                    children,
                    textContent: "",
                    className: "",
                    hidden: false,
                    disabled: false,
                    checked: false,
                    value: "",
                    classList: {{
                      add: () => {{}},
                      remove: () => {{}},
                      toggle: () => {{}},
                      contains: () => false,
                    }},
                    appendChild(child) {{ children.push(child); return child; }},
                    append(...nodes) {{ for (const node of nodes) this.appendChild(node); }},
                    removeChild(child) {{
                      const index = children.indexOf(child);
                      if (index >= 0) children.splice(index, 1);
                      return child;
                    }},
                    get firstChild() {{ return children[0] || null; }},
                    addEventListener() {{}},
                  }};
                }}

                const requests = [];
                const context = {{
                  console,
                  URL,
                  location: {{ origin: "http://127.0.0.1:41741" }},
                  localStorage: {{
                    getItem: () => "token",
                    setItem: () => {{}},
                    removeItem: () => {{}},
                  }},
                  createNode,
                  window: {{}},
                  document: {{
                    addEventListener: () => {{}},
                    createElement: createNode,
                  }},
                  fetch: async (url, init = {{}}) => {{
                    const parsed = new URL(url);
                    requests.push({{ path: parsed.pathname, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null }});
                    return {{
                      ok: true,
                      status: 200,
                      text: async () => JSON.stringify({{ runtime_settings: {{}} }}),
                    }};
                  }},
                }};
                context.window.localStorage = context.localStorage;
                context.globalThis = context;
                vm.createContext(context);
                vm.runInContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
                  filename: "app.js",
                }});

                const blockedPayload = {{
                  capability_graph: {{
                    tools_capabilities: {{
                      apply_capabilities: {{
                        "voice.speak_responses": {{ apply_capable: false }},
                      }},
                    }},
                    voice_capabilities: {{
                      tts_providers: [{{ id: "mock", label: "Mock", available: false }}],
                      stt_providers: [],
                    }},
                  }},
                  settings_preview: {{
                    sections: {{
                      voice_tts: {{
                        fields: {{
                          tts_provider: {{ current: "", effective: "", status: "missing", editable_now: false, blocker: "tts provider unavailable" }},
                          tts_model: {{ current: "", effective: "", allowed_values: [], editable_now: false }},
                          voice_id: {{ current: "", effective: "", allowed_values: [], editable_now: false }},
                        }},
                      }},
                    }},
                  }},
                  voice: {{
                    speak_responses: {{ effective_value: false }},
                    default_tts: {{ effective_value: "" }},
                  }},
                }};
                context.payload = blockedPayload;

                vm.runInContext(`
                  el.voiceSpeakResponsesToggle = createNode("input");
                  el.voiceBrokerEnabledToggle = createNode("input");
                  el.voiceTtsSelect = createNode("select");
                  el.voiceTtsModelSelect = createNode("select");
                  el.voiceVoiceIdSelect = createNode("select");
                  el.voiceProfileSelect = createNode("select");
                  el.voiceSpeedInput = createNode("input");
                  el.voiceTtsStatusList = createNode("div");
                  el.ttsApplyStatus = createNode("p");
                  el.applyTtsSettingsButton = createNode("button");
                  globalThis.__el = el;
                  cockpit.online = true;
                  cockpit.runtimeSettingsApply.payload = payload;
                  cockpit.settingsPreview.payload = payload;
                  renderTtsApplyControls(payload);
                `, context);

                assert.strictEqual(context.__el.voiceSpeakResponsesToggle.disabled, true);
                context.__el.voiceSpeakResponsesToggle.checked = true;
                context.updateTtsControlOptions();
                assert.match(context.__el.ttsApplyStatus.textContent, /Blocked/);
                assert.doesNotMatch(context.__el.ttsApplyStatus.textContent, /OFF -> ON|false -> true/i);

                const independentPayload = JSON.parse(JSON.stringify(blockedPayload));
                independentPayload.settings_preview.sections.voice_tts.fields.tts_provider.status = "missing";
                independentPayload.settings_preview.sections.voice_tts.fields.tts_provider.apply_capable = true;
                independentPayload.settings_preview.sections.voice_tts.fields.tts_provider.blocker = null;
                independentPayload.settings_preview.sections.voice_tts.fields.tts_provider.editable_now = true;
                independentPayload.capability_graph.tools_capabilities.apply_capabilities["voice.speak_responses"].apply_capable = true;
                independentPayload.voice = {{
                  speak_responses: {{ effective_value: false }},
                  default_tts: {{ effective_value: "" }},
                }};
                context.independentPayload = independentPayload;

                vm.runInContext(`
                  const payload = independentPayload;
                  cockpit.runtimeSettingsApply.payload = payload;
                  cockpit.settingsPreview.payload = independentPayload;
                  renderTtsApplyControls(independentPayload);
                `, context);
                assert.strictEqual(context.__el.voiceSpeakResponsesToggle.disabled, false);
                assert.match(context.__el.ttsApplyStatus.textContent, /Blocked/);
                assert.match(context.__el.ttsApplyStatus.textContent, /Requires restart|restart|Blocked/);
                assert.strictEqual(context.__el.applyTtsSettingsButton.disabled, true);
                (async () => {{
                  await context.applyRuntimeSettingsGroup("tts");
                  assert.strictEqual(requests.length, 0);
                }})().catch((error) => {{
                  console.error(error);
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


def test_system_tab_has_active_settings_apply_sections_before_diagnostics() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    logs_view = markup[markup.index('id="view-logs"') : markup.index('id="view-system"')]
    system_view = markup[markup.index('id="view-system"') : markup.index('<nav class="tabbar"')]

    ordered_ids = [
        "missionControlHeading",
        "activeBrainSettingsHeading",
        "voiceTtsSettingsHeading",
        "voiceSttSettingsHeading",
        "activePttSettingsHeading",
        "activeToolsSettingsHeading",
        "activePersonalitySettingsHeading",
    ]
    positions = [system_view.index(item) for item in ordered_ids]
    assert positions == sorted(positions)

    for marker in (
        "applyBrainSettingsButton",
        "applyTtsSettingsButton",
        "applySttSettingsButton",
        "applyPttSettingsButton",
        "applyToolsSettingsButton",
        "toolsInternetStatusList",
        "applyPersonaSettingsButton",
        "resetBrainPreviewButton",
        "resetTtsPreviewButton",
        "resetSttPreviewButton",
        "resetToolsPreviewButton",
    ):
        assert marker in system_view

    for diagnostic_id in (
        "quickHealthHeading",
        "queueBargeInHeading",
        "memoryApprovalsHeading",
        "developerTestHeading",
        "latestTurnTraceHeading",
        "runtimeLogsSummaryHeading",
        "runtimeOverviewHeading",
        "settingsPreviewHeading",
        "toolsHeading",
    ):
        assert diagnostic_id not in system_view
        assert diagnostic_id in logs_view

    assert "activeVoiceSettingsHeading" not in markup
    assert "Voice controls" not in markup
    assert "eventList" not in system_view


def test_system_tab_is_actionable_first_and_logs_own_diagnostics() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")
    logs_view = markup[markup.index('id="view-logs"') : markup.index('id="view-system"')]
    system_view = markup[markup.index('id="view-system"') : markup.index('<nav class="tabbar"')]

    assert system_view.index("missionControlHeading") < system_view.index("activeBrainSettingsHeading")
    assert system_view.index("activeBrainSettingsHeading") < system_view.index("activeToolsSettingsHeading")
    assert "connectionHeading" not in system_view
    assert "settingsHeading" not in system_view
    assert "Połączenie" not in system_view
    assert "Ustawienia surowe" not in system_view
    assert "Runtime Diagnostics" not in system_view
    assert "Settings Preview" not in system_view
    assert "Latest Turn Trace" not in system_view
    assert "Runtime Logs" not in system_view
    assert "Możliwości Jarvisa" not in system_view
    assert "Diagnostyka (surowe)" not in system_view

    for moved_heading in (
        "Events",
        "Runtime diagnostics",
        "Provider diagnostics",
        "Voice diagnostics",
        "Tools / Internet diagnostics",
        "Raw technical details",
        "Runtime summary",
        "Voice queue",
        "Memory / Approvals",
        "Developer / Test",
        "Latest Turn Trace",
        "Settings Preview",
        "Połączenie",
        "Ustawienia surowe",
    ):
        assert moved_heading in logs_view

    for dump_label in ("Value", "Status", "Action"):
        assert not re.search(rf"(?<![A-Za-z]){dump_label}: ", script)


def test_system_tab_uses_human_operator_labels_not_backend_keys() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")
    system_view = markup[markup.index('id="view-system"') : markup.index('<nav class="tabbar"')]

    for human_label in (
        ">Tools enabled<",
        ">Internet access<",
        ">Require approval for network<",
        ">Require approval for shell<",
        ">Require approval for file writes<",
        ">Speak responses<",
        ">Fast mode<",
        ">Voice broker<",
        ">Text-to-speech engine<",
        ">Speech model<",
    ):
        assert human_label in system_view

    for control_id, label in (
        ("toolsEnabledToggle", "Tools enabled"),
        ("toolsNetworkApprovalToggle", "Require approval for network"),
        ("toolsShellApprovalToggle", "Require approval for shell"),
        ("toolsFileWriteApprovalToggle", "Require approval for file writes"),
        ("voiceSpeakResponsesToggle", "Speak responses"),
        ("activeBrainFastToggle", "Fast mode"),
    ):
        assert re.search(
            rf'<input id="{control_id}"[^>]*>\s*<span>{re.escape(label)}</span>',
            system_view,
            re.S,
        ), control_id

    assert re.search(
        r'<div class="inline-toggle read-only-setting">\s*<span>Internet access</span>\s*<span id="toolsNetworkEnabledToggle" class="status-badge" role="status">unknown</span>',
        system_view,
        re.S,
    )
    assert re.search(
        r'<div class="inline-toggle read-only-setting">\s*<span>Voice broker</span>\s*<span id="voiceBrokerEnabledToggle" class="status-badge" role="status">unknown</span>',
        system_view,
        re.S,
    )
    assert "grid-template-columns: 18px minmax(0, 1fr);" in styles
    assert "inline-size: 16px;" in styles
    assert "word-break: keep-all;" in styles

    for raw_label in (
        ">tools.enabled<",
        ">security.require_approval_for_network<",
        ">voice.default_tts<",
        ">brain.current_adapter<",
        ">speak_responses<",
        ">broker_enabled<",
        ">Configured tools.enabled<",
    ):
        assert raw_label not in system_view


def test_system_tab_has_requested_cockpit_section_headings() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    system_view = markup[markup.index('id="view-system"') : markup.index('<nav class="tabbar"')]
    expected_headings = [
        "Mission Control",
        "Brain / Provider",
        "Voice / TTS",
        "Voice / STT",
        "Endpointing / PTT",
        "Tools / Internet",
        "Personality",
    ]
    for heading in expected_headings:
        assert heading in system_view

    assert "Developer / Test" not in system_view
    assert "Mock" not in system_view


def test_runtime_settings_apply_payload_is_allowlisted_and_posts(tmp_path: Path) -> None:
    harness = tmp_path / "runtime-settings-apply-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            const requests = [];
            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41800" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              fetch: async (url, init = {{}}) => {{
                const parsed = new URL(url);
                requests.push({{
                  path: parsed.pathname,
                  method: init.method || "GET",
                  body: init.body ? JSON.parse(init.body) : null,
                }});
                return {{
                  ok: true,
                  status: 200,
                  text: async () => JSON.stringify({{
                    ok: true,
                    applied: Object.keys(JSON.parse(init.body).settings),
                    runtime_settings: {{}},
                  }}),
                }};
              }},
              document: {{
                addEventListener: () => {{}},
              }},
              window: {{
                localStorage: {{
                  getItem: () => "token",
                  setItem: () => {{}},
                  removeItem: () => {{}},
                }},
                addEventListener: () => {{}},
                prompt: () => "token",
              }},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            const filtered = context.runtimeSettingsPayloadForGroup("brain", {{
              "brain.provider": "claude_cli",
              "brain.model": "claude-cli",
              "voice.speak_responses": true,
              "unknown.setting": "bad",
            }});
            assert.deepStrictEqual(JSON.parse(JSON.stringify(filtered)), {{
              "brain.provider": "claude_cli",
              "brain.model": "claude-cli",
            }});
            assert.match(
              context.providerApplyBlocker({{
                id: "codex_cli",
                available: false,
                blocker: "Provider command is missing.",
              }}, "claude_cli"),
              /Provider command is missing/,
            );
            const backendError = new Error("unsupported");
            backendError.detail = {{
              status: 422,
              payload: {{ blockers: ["not apply-capable in POC"] }},
            }};
            assert.strictEqual(
              context.runtimeSettingsErrorMessage(backendError),
              "Blocked. No action available in this POC.",
            );
            const toolsFiltered = context.runtimeSettingsPayloadForGroup("tools", {{
              "security.require_approval_for_network": false,
              "tools.network_enabled": true,
              "voice.speak_responses": true,
              "unknown.setting": "bad",
            }});
            assert.deepStrictEqual(JSON.parse(JSON.stringify(toolsFiltered)), {{
              "tools.network_enabled": true,
              "security.require_approval_for_network": false,
            }});
            assert.strictEqual(typeof context.setRuntimeSettingsApplyBusy, "function");
            const classSet = () => {{
              const values = new Set();
              return {{
                toggle: (name, enabled) => enabled ? values.add(name) : values.delete(name),
                contains: (name) => values.has(name),
              }};
            }};
            const buttons = [{{ disabled: false, classList: classSet() }}, {{ disabled: false, classList: classSet() }}];
            context.setRuntimeSettingsApplyBusy(true, buttons);
            assert.ok(buttons.every((button) => button.disabled));
            assert.ok(buttons.every((button) => button.classList.contains("busy")));

            (async () => {{
              await context.postRuntimeSettingsApply(filtered);
              assert.strictEqual(requests.length, 1);
              assert.strictEqual(requests[0].path, "/runtime/settings/apply");
              assert.strictEqual(requests[0].method, "POST");
              assert.deepStrictEqual(JSON.parse(JSON.stringify(requests[0].body)), {{
                settings: {{
                  "brain.provider": "claude_cli",
                  "brain.model": "claude-cli",
                }},
              }});
            }})().catch((error) => {{
              console.error(error);
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


def test_settings_preview_local_model_catches_invalid_provider_voice_combos(
    tmp_path: Path,
    ) -> None:
    harness = tmp_path / "settings-preview-model-harness.js"
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

            function previewField(id, current, extra = {{}}) {{
              return Object.assign({{
                id,
                label: id,
                current,
                effective: current,
                status: "ok",
                source: "runtime_detected",
                allowed_values: [],
                disabled_values: [],
                warning: null,
                blocker: null,
                dependencies: [],
                invalidates: [],
                requires_restart: false,
                requires_reload: false,
                editable_now: true,
                editable_later: false,
                developer_only: false,
              }}, extra);
            }}

            const payload = {{
              settings_preview: {{
                preview_only: true,
                sections: {{
                  brain_provider: {{
                    id: "brain_provider",
                    label: "Brain / Provider",
                    fields: {{
                      provider: previewField("brain_provider.provider", "claude_cli", {{
                        allowed_values: ["claude_cli", "codex_cli", "ollama", "mock"],
                        disabled_values: [{{ value: "mock", reason: "Developer/Test only" }}],
                        invalidates: ["brain_provider.model", "brain_provider.effort", "brain_provider.fast"],
                      }}),
                      model: previewField("brain_provider.model", "claude-pro"),
                      effort: previewField("brain_provider.effort", "max", {{
                        allowed_values: ["low", "max"],
                      }}),
                      fast: previewField("brain_provider.fast", true, {{
                        allowed_values: [true, false],
                      }}),
                      command_status: previewField("brain_provider.command_status", "ok"),
                      credentials_or_command_status: previewField("brain_provider.credentials_or_command_status", "ok"),
                      tools_support: previewField("brain_provider.tools_support", "yes"),
                      streaming_support: previewField("brain_provider.streaming_support", "yes"),
                    }},
                  }},
                  voice_tts: {{
                    id: "voice_tts",
                    label: "Voice / TTS",
                    fields: {{
                      tts_provider: previewField("voice_tts.tts_provider", "", {{
                        status: "missing",
                        blocker: "Voice enabled but TTS provider is missing.",
                      }}),
                      tts_model: previewField("voice_tts.tts_model", null, {{ status: "missing" }}),
                      voice_id: previewField("voice_tts.voice_id", null, {{ status: "missing" }}),
                    }},
                  }},
                  voice_stt: {{
                    id: "voice_stt",
                    label: "Voice / STT",
                    fields: {{
                      stt_provider: previewField("voice_stt.stt_provider", "", {{
                        status: "missing",
                        blocker: "Voice enabled but STT provider is missing.",
                      }}),
                      stt_model: previewField("voice_stt.stt_model", null, {{ status: "missing" }}),
                    }},
                  }},
                  queue_barge_in: {{
                    id: "queue_barge_in",
                    label: "Queue / Barge-in",
                    fields: {{
                      cancel_support: previewField("queue_barge_in.cancel_support", "no"),
                    }},
                  }},
                }},
              }},
              capability_graph: {{
                brain_capabilities: {{
                  current_provider: "claude_cli",
                  providers: [
                    {{
                      id: "claude_cli",
                      label: "Claude CLI",
                      kind: "Provider",
                      available: true,
                      configured: true,
                      developer_only: false,
                      models: [{{ id: "claude-pro", label: "claude-pro", available: true }}],
                      current_model: "claude-pro",
                      allowed_effort_values: ["low", "max"],
                      fast_supported: true,
                      tools_supported: true,
                      streaming_supported: true,
                      command_status: "ok",
                    }},
                    {{
                      id: "codex_cli",
                      label: "Codex CLI",
                      kind: "Provider",
                      available: true,
                      configured: true,
                      developer_only: false,
                      models: [{{ id: "codex-lite", label: "codex-lite", available: true }}],
                      current_model: "codex-lite",
                      allowed_effort_values: ["low"],
                      fast_supported: false,
                      tools_supported: true,
                      streaming_supported: false,
                      command_status: "ok",
                    }},
                    {{
                      id: "ollama",
                      label: "Ollama",
                      kind: "Local",
                      available: false,
                      configured: false,
                      developer_only: false,
                      models: [],
                      current_model: null,
                      allowed_effort_values: [],
                      fast_supported: false,
                      tools_supported: false,
                      streaming_supported: false,
                      command_status: "missing",
                      blocker: "Local runtime/model missing.",
                    }},
                    {{
                      id: "mock",
                      label: "Mock",
                      kind: "Developer/Test",
                      available: true,
                      configured: true,
                      developer_only: true,
                      models: [{{ id: "mock-local", label: "mock-local", available: true }}],
                      current_model: "mock-local",
                      allowed_effort_values: [],
                      fast_supported: false,
                      tools_supported: false,
                      streaming_supported: false,
                      command_status: "ok",
                    }},
                  ],
                }},
                voice_capabilities: {{
                  tts_providers: [],
                  stt_providers: [],
                  cancellation_support: false,
                }},
                local_capabilities: {{ runtimes: [] }},
              }},
              compatibility_warnings: [],
            }};

            const model = context.settingsPreviewModelFromPayload(payload);
            assert.strictEqual(
              model.sections.voice_tts.fields.tts_provider.blocker,
              "Voice enabled but TTS provider is missing.",
            );
            assert.strictEqual(
              model.sections.voice_stt.fields.stt_provider.blocker,
              "Voice enabled but STT provider is missing.",
            );

            const codexPreview = context.settingsPreviewApplyOverride(
              model,
              "brain_provider.provider",
              "codex_cli",
            );
            const diffRows = context.settingsPreviewDiffRows(codexPreview);
            assert.ok(diffRows.some((row) =>
              row.fieldId === "brain_provider.provider" &&
              row.current === "claude_cli" &&
              row.preview === "codex_cli"
            ));
            assert.ok(diffRows.some((row) =>
              row.fieldId === "brain_provider.effort" &&
              /reset required/i.test(row.message)
            ));
            assert.ok(diffRows.some((row) =>
              row.fieldId === "brain_provider.fast" &&
              /does not support fast/i.test(row.message)
            ));
            assert.strictEqual(codexPreview.sections.brain_provider.fields.effort.status, "invalid");
            assert.match(codexPreview.sections.brain_provider.fields.effort.blocker, /reset required/i);
            assert.strictEqual(codexPreview.sections.brain_provider.fields.fast.status, "unsupported");
            assert.ok(
              codexPreview.sections.brain_provider.fields.fast.disabled_values.some((item) =>
                item.value === true && /does not support fast/i.test(item.reason),
              ),
            );

            const localPreview = context.settingsPreviewApplyOverride(
              model,
              "brain_provider.provider",
              "ollama",
            );
            assert.strictEqual(localPreview.sections.brain_provider.fields.model.status, "missing");
            assert.match(localPreview.sections.brain_provider.fields.model.blocker, /local.*model/i);
            assert.notStrictEqual(
              localPreview.sections.brain_provider.fields.credentials_or_command_status.effective,
              "ok",
            );
            assert.strictEqual(
              localPreview.sections.brain_provider.fields.credentials_or_command_status.effective,
              "missing",
            );
            assert.strictEqual(
              localPreview.sections.brain_provider.fields.credentials_or_command_status.status,
              "missing",
            );

            const mockPreview = context.settingsPreviewApplyOverride(
              model,
              "brain_provider.provider",
              "mock",
            );
            assert.strictEqual(mockPreview.sections.brain_provider.fields.provider.developer_only, true);
            assert.match(mockPreview.sections.brain_provider.fields.provider.warning, /Developer\\/Test/);
            const redactedPreviewText = context.settingsPreviewValue({{
              command: "sk-panel-settings-preview-secret",
            }});
            assert.ok(!redactedPreviewText.includes("sk-panel-settings-preview-secret"));
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
    assert "scheduleRuntimeOverviewRefresh" in script
    assert 'type.startsWith("turn.")' in script
    assert 'type.startsWith("input.")' in script
    assert 'type.startsWith("memory.")' in script
    assert 'type.startsWith("voice.")' in script


def test_live_stream_debounces_runtime_overview_refresh_for_relevant_events(tmp_path: Path) -> None:
    harness = tmp_path / "runtime-overview-live-refresh-harness.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            class FakeNode {{
              constructor(tagName = "div") {{
                this.tagName = tagName;
                this.children = [];
                this.parentNode = null;
                this.className = "";
                this.dataset = {{}};
                this.listeners = {{}};
                this.value = "";
                this.title = "";
                this.hidden = false;
                this.disabled = false;
                this._text = "";
                this.classList = {{
                  add: () => {{}},
                  remove: () => {{}},
                  toggle: () => false,
                }};
              }}
              set textContent(value) {{
                this._text = String(value);
                this.children = [];
              }}
              get textContent() {{
                return this._text + this.children.map((child) => child.textContent || "").join("");
              }}
              append(...nodes) {{
                for (const node of nodes) {{
                  this.appendChild(node);
                }}
              }}
              appendChild(node) {{
                this.children.push(node);
                node.parentNode = this;
                return node;
              }}
              removeChild(node) {{
                const index = this.children.indexOf(node);
                if (index >= 0) {{
                  this.children.splice(index, 1);
                }}
                node.parentNode = null;
                return node;
              }}
              addEventListener(type, listener) {{
                this.listeners[type] = listener;
              }}
              setAttribute() {{}}
              focus() {{}}
              get firstChild() {{
                return this.children[0] || null;
              }}
              querySelector() {{
                return null;
              }}
              querySelectorAll() {{
                return [];
              }}
            }}

            const nodes = new Map();
            function node(id) {{
              if (!nodes.has(id)) {{
                nodes.set(id, new FakeNode(id));
              }}
              return nodes.get(id);
            }}

            const timers = [];
            let timerId = 1;
            const requests = [];
            const response = (payload) => ({{
              status: 200,
              ok: true,
              text: async () => JSON.stringify(payload),
            }});
            const payloadFor = (path) => {{
              if (path.startsWith("/conversations")) {{
                return {{ conversations: [] }};
              }}
              if (path.startsWith("/turns")) {{
                return {{ turns: [] }};
              }}
              if (path === "/runtime/settings") {{
                return {{
                  runtime_readiness: {{}},
                  current_turn_state: {{}},
                  latest_turn_trace: {{}},
                  brain: {{}},
                }};
              }}
              if (path.startsWith("/events")) {{
                return {{ events: [] }};
              }}
              return {{}};
            }};

            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41800" }},
              localStorage: {{
                getItem: () => "token",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              setTimeout: (callback, ms) => {{
                const handle = {{ id: timerId++, callback, ms, cleared: false }};
                timers.push(handle);
                return handle;
              }},
              clearTimeout: (handle) => {{
                if (handle) {{
                  handle.cleared = true;
                }}
              }},
              setInterval: () => 0,
              clearInterval: () => {{}},
              WebSocket: class {{}},
              fetch: async (url) => {{
                const parsed = new URL(url);
                const path = `${{parsed.pathname}}${{parsed.search}}`;
                requests.push(path);
                return response(payloadFor(path));
              }},
              document: {{
                body: node("body"),
                addEventListener: () => {{}},
                createElement: (tagName) => new FakeNode(tagName),
                createTextNode: (text) => {{
                  const textNode = new FakeNode("#text");
                  textNode.textContent = text;
                  return textNode;
                }},
                getElementById: (id) => node(id),
                querySelectorAll: () => [],
              }},
              window: {{
                localStorage: {{
                  getItem: () => "token",
                  setItem: () => {{}},
                  removeItem: () => {{}},
                }},
                addEventListener: () => {{}},
                prompt: () => "token",
              }},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});
            context.bindElements();
            context.bindEvents();

            const frame = (id, type) => JSON.stringify({{
              type: "event",
              event: {{
                id,
                type,
                source: "test",
                created_at: "2026-07-06T12:00:00Z",
                payload: {{}},
              }},
            }});

            (async () => {{
              context.handleStreamMessage(frame(1, "turn.finished"));
              context.handleStreamMessage(frame(2, "turn.context.built"));

              const due = timers.filter((timer) => !timer.cleared);
              await Promise.all(due.map((timer) => timer.callback()));

              const runtimeSettingsCalls = requests.filter((path) => path === "/runtime/settings");
              assert.strictEqual(runtimeSettingsCalls.length, 1);
            }})().catch((error) => {{
              console.error(error);
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


def _run_logs_harness(tmp_path: Path, name: str, body: str) -> None:
    harness = tmp_path / f"{name}.js"
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            class FakeNode {{
              constructor(tagName = "div") {{
                this.tagName = tagName;
                this.children = [];
                this.parentNode = null;
                this.className = "";
                this.dataset = {{}};
                this.listeners = {{}};
                this.value = "";
                this.title = "";
                this._text = "";
              }}
              set textContent(value) {{
                this._text = String(value);
                this.children = [];
              }}
              get textContent() {{
                return this._text + this.children.map((child) => child.textContent || "").join("");
              }}
              append(...nodes) {{
                for (const node of nodes) {{
                  this.appendChild(node);
                }}
              }}
              appendChild(node) {{
                this.children.push(node);
                node.parentNode = this;
                return node;
              }}
              insertBefore(node, reference) {{
                if (!reference) {{
                  return this.appendChild(node);
                }}
                const index = this.children.indexOf(reference);
                if (index < 0) {{
                  return this.appendChild(node);
                }}
                this.children.splice(index, 0, node);
                node.parentNode = this;
                return node;
              }}
              removeChild(node) {{
                const index = this.children.indexOf(node);
                if (index >= 0) {{
                  this.children.splice(index, 1);
                }}
                node.parentNode = null;
                return node;
              }}
              remove() {{
                if (this.parentNode) {{
                  this.parentNode.removeChild(this);
                }}
              }}
              replaceChildren(...nodes) {{
                for (const child of [...this.children]) {{
                  child.parentNode = null;
                }}
                this.children = [];
                this._text = "";
                for (const node of nodes) {{
                  this.appendChild(node);
                }}
              }}
              addEventListener(type, listener) {{
                this.listeners[type] = listener;
              }}
              setAttribute() {{}}
              focus() {{}}
              get firstChild() {{
                return this.children[0] || null;
              }}
              get lastChild() {{
                return this.children[this.children.length - 1] || null;
              }}
              querySelector(selector) {{
                if (selector !== ".empty-row") {{
                  return null;
                }}
                return findNode(this, (node) =>
                  String(node.className || "").split(/\\s+/).includes("empty-row"),
                );
              }}
              querySelectorAll() {{
                return [];
              }}
            }}

            function findNode(node, predicate) {{
              if (predicate(node)) {{
                return node;
              }}
              for (const child of node.children) {{
                const found = findNode(child, predicate);
                if (found) {{
                  return found;
                }}
              }}
              return null;
            }}

            const nodes = new Map();
            function node(id) {{
              if (!nodes.has(id)) {{
                nodes.set(id, new FakeNode(id));
              }}
              return nodes.get(id);
            }}

            const context = {{
              console,
              URL,
              location: {{ origin: "http://127.0.0.1:41800" }},
              localStorage: {{
                getItem: () => "",
                setItem: () => {{}},
                removeItem: () => {{}},
              }},
              setTimeout: () => 0,
              clearTimeout: () => {{}},
              setInterval: () => 0,
              clearInterval: () => {{}},
              WebSocket: class {{}},
              document: {{
                addEventListener: () => {{}},
                createElement: (tagName) => new FakeNode(tagName),
                createTextNode: (text) => {{
                  const textNode = new FakeNode("#text");
                  textNode.textContent = text;
                  return textNode;
                }},
                getElementById: (id) => node(id),
                querySelectorAll: () => [],
              }},
              window: {{
                addEventListener: () => {{}},
              }},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});
            context.bindElements();
            context.bindEvents();

            function event(id, type = "state.changed") {{
              return {{
                id,
                type,
                source: "test",
                created_at: "2026-07-06T12:00:00Z",
                payload: {{ status: `status-${{id}}` }},
              }};
            }}

            function eventIds() {{
              return node("eventList").children
                .map((row) => {{
                  const match = row.textContent.match(/#(\\d+)\\s*·/);
                  return match ? Number(match[1]) : null;
                }})
                .filter((id) => id !== null);
            }}

            {body}
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


def test_safe_debug_timeline_classifies_families_and_never_dumps_raw_payloads(tmp_path: Path) -> None:
    harness = tmp_path / "event-family-timeline-harness.js"
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

            const secretValue = "sk-" + "timeline-secret";
            const rows = [
              {{
                id: 9,
                type: "voice.speak.failed",
                created_at: "2026-07-06T12:00:09Z",
                payload: {{
                  status: "failed",
                  error: `Authorization: Bearer ${{secretValue}}`,
                  raw_payload: `must-not-render-${{secretValue}}`,
                }},
              }},
              {{
                id: 10,
                type: "tool.finished",
                created_at: "2026-07-06T12:00:10Z",
                payload: {{
                  status: "ok",
                  tool_name: "shell_read",
                  arguments: {{ command: `echo ${{secretValue}}` }},
                }},
              }},
            ];

            const latest = context.safeEventTimelineItem(rows[1]);
            assert.strictEqual(latest.family, "tool");
            assert.strictEqual(latest.status, "ok");
            assert.strictEqual(latest.type, "tool.finished");
            assert.ok(!latest.summary.includes(secretValue), latest.summary);
            assert.ok(!latest.summary.includes("arguments:"), latest.summary);

            const failed = context.safeEventTimelineItem(rows[0]);
            assert.strictEqual(failed.family, "voice");
            assert.strictEqual(failed.severity, "error");
            assert.match(failed.summary, /\\[REDACTED\\]/);
            assert.ok(!failed.summary.includes(secretValue), failed.summary);
            assert.ok(!failed.summary.includes("raw_payload"), failed.summary);

            const timeline = context.debugTimelineSummary(rows);
            assert.ok(timeline.indexOf("#10") < timeline.indexOf("#9"), timeline);
            assert.match(timeline, /family: tool/);
            assert.match(timeline, /status: ok/);
            assert.match(timeline, /family: voice/);
            assert.ok(!timeline.includes(secretValue), timeline);
            assert.ok(!timeline.includes("raw_payload"), timeline);
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


def test_logs_render_latest_events_newest_first(tmp_path: Path) -> None:
    _run_logs_harness(
        tmp_path,
        "logs-latest-ordering-harness",
        """
        context.renderEvents([event(30), event(20), event(10)]);

        assert.deepStrictEqual(eventIds(), [30, 20, 10]);
        """,
    )


def test_logs_keep_live_events_at_top_without_duplicates(tmp_path: Path) -> None:
    _run_logs_harness(
        tmp_path,
        "logs-live-ordering-harness",
        """
        context.renderEvents([event(3), event(2), event(1)]);
        context.prependLiveEvent(event(4));
        context.prependLiveEvent(event(3));

        assert.deepStrictEqual(eventIds().slice(0, 4), [4, 3, 2, 1]);
        assert.strictEqual(eventIds().filter((id) => id === 3).length, 1);
        """,
    )


def test_stream_hello_does_not_advance_reconnect_cursor(tmp_path: Path) -> None:
    _run_logs_harness(
        tmp_path,
        "stream-hello-cursor-harness",
        """
        context.handleStreamMessage(JSON.stringify({
          type: "event",
          event: event(4, "tool.finished"),
        }));
        assert.strictEqual(context.streamUrl(), "ws://127.0.0.1:41741/stream?after_id=4");

        context.handleStreamMessage(JSON.stringify({
          type: "stream.hello",
          latest_event_id: 8,
          start_after_id: 4,
        }));

        assert.strictEqual(context.streamUrl(), "ws://127.0.0.1:41741/stream?after_id=4");

        context.handleStreamMessage(JSON.stringify({
          type: "event",
          event: event(6, "tool.finished"),
        }));
        context.handleStreamMessage(JSON.stringify({
          type: "event",
          event: event(6, "tool.finished"),
        }));

        assert.strictEqual(context.streamUrl(), "ws://127.0.0.1:41741/stream?after_id=6");
        assert.strictEqual(eventIds().filter((id) => id === 6).length, 1);
        """,
    )


def test_logs_trim_cache_by_keeping_newest_events(tmp_path: Path) -> None:
    _run_logs_harness(
        tmp_path,
        "logs-cache-trimming-harness",
        """
        const polled = Array.from({ length: 260 }, (_, index) => event(260 - index));
        context.renderEvents(polled);
        context.prependLiveEvent(event(261));

        node("logFilter").value = "all";
        node("logFilter").listeners.change();

        const ids = eventIds();
        assert.strictEqual(ids[0], 261);
        assert.ok(ids.includes(260), "newest polled event should survive cache trimming");
        assert.ok(!ids.includes(1), "oldest polled event should be trimmed first");
        """,
    )


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


def test_tools_registry_details_live_in_logi_as_diagnostics() -> None:
    # The registry is read-only diagnostics. System keeps only actionable
    # Tools / Internet controls or compact blockers tied to those controls.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")
    logs_view = markup[markup.index('id="view-logs"') : markup.index('id="view-system"')]
    system_view = markup[markup.index('id="view-system"') : markup.index('<nav class="tabbar"')]

    assert "toolList" in logs_view
    assert "toolList" not in system_view
    # renderowane przez wspólne mapy PL (nie surowe „file_read - file_read”).
    assert "toolLabel" in script
    assert "riskLabel" in script
    assert "Tools / Internet diagnostics" in logs_view


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
    # System is actionable-first; LOGI owns read-only diagnostics.
    markup = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")
    logs_view = markup[markup.index('id="view-logs"') : markup.index('id="view-system"')]
    system_view = markup[markup.index('id="view-system"') : markup.index('<nav class="tabbar"')]

    for heading in (
        "missionControlHeading",
        "activeBrainSettingsHeading",
        "voiceTtsSettingsHeading",
        "voiceSttSettingsHeading",
        "activePttSettingsHeading",
        "activeToolsSettingsHeading",
        "activePersonalitySettingsHeading",
    ):
        assert heading in system_view, heading

    for heading in (
        "quickHealthHeading",
        "queueBargeInHeading",
        "runtimeOverviewHeading",
        "settingsPreviewHeading",
        "toolsHeading",
        "connectionHeading",
        "settingsHeading",
    ):
        assert heading not in system_view, heading
        assert heading in logs_view, heading

    # Ludzki stan daemona żyje w diagnostyce, nie w głównym przepływie Systemu.
    assert "healthHumanList" in markup
    assert "renderHealthHuman" in script
    assert "Działa od" in script

    # Model po ludzku (aktywny / domyślny), nie „current - default”.
    assert "aktywny:" in script
    assert "domyślny:" in script

    # Surowa diagnostyka jest w LOGI, nie dominuje Systemu.
    assert "Raw technical details" in logs_view
    assert system_view.index("missionControlHeading") < system_view.index("activeBrainSettingsHeading")


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
