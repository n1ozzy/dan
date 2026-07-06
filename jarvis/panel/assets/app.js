"use strict";

const DEFAULT_API_BASE = "http://127.0.0.1:41741";

const cockpit = {
  apiBase: DEFAULT_API_BASE,
  online: false,
  selectedConversationId: null,
  approvedApprovals: new Map(),
  // Tytuły rozmów dorabiane z pierwszego input_text (GET /turns?limit=1);
  // cache po id rozmowy, bo pierwsza tura się nie zmienia.
  conversationTitles: new Map(),
  pendingApprovalCount: 0,
  // Ostatni znany stan pracy daemona (RuntimeState: IDLE/LISTENING/THINKING/
  // SPEAKING) — steruje żywą ramką: praca => neon obiega, spoczynek => spokój.
  runtimeState: "IDLE",
  // Zakładka LOGI: aktywny filtr + ostatnia partia zdarzeń, żeby zmiana
  // filtra przerysowała dziennik bez ponownego strzału do daemona.
  logFilter: "all",
  lastEvents: [],
  // Tryb "nowa rozmowa": nie auto-wybieraj najnowszej rozmowy przy refreshu,
  // dopóki operator nie wyśle pierwszej wiadomości.
  composingNew: false,
  healthRetryTimer: null,
  voice: {
    enabled: false,
    listening: false,
    leases: [],
  },
  settingsPreview: {
    payload: null,
    model: null,
    overrides: {},
  },
  missionControl: {
    snapshot: null,
    lastRefreshAt: null,
    refreshing: false,
  },
  stream: {
    socket: null,
    base: null,
    lastEventId: 0,
    retryMs: 2000,
    reconnectTimer: null,
    approvalsTimer: null,
    historyTimer: null,
    memoryTimer: null,
    runtimeTimer: null,
    runtimeOverviewTimer: null,
    settingsTimer: null,
    voiceTimer: null,
    voiceQueueTimer: null,
    connected: false,
  },
};

const STREAM_SUBPROTOCOL = "jarvis.v1";
const STREAM_TOKEN_SUBPROTOCOL_PREFIX = "jarvis-token.";
const STREAM_MAX_RETRY_MS = 15000;
const MAX_LIVE_EVENT_ROWS = 50;
// When the daemon is unreachable at load (e.g. panel started before the daemon
// finished booting), re-poll health on this interval so the panel recovers on
// its own instead of getting stuck on "unknown"/"offline" until a manual click.
const HEALTH_RETRY_MS = 2000;
// Steady status heartbeat: re-check health/state on this interval so the pill
// is never stuck on a stale "unknown" after a startup race or a daemon restart
// under a live panel. On a fresh reconnect it triggers a full refreshAll().
const HEALTH_POLL_MS = 3000;
// Fallback refresh for core live panes when the websocket stream is down.
const LIVE_FALLBACK_POLL_MS = 5000;
const RUNTIME_OVERVIEW_UNKNOWN = "unknown";
const RUNTIME_OVERVIEW_NOT_EXPOSED = "not exposed by current API";
const RUNTIME_OVERVIEW_READ_ONLY = "read-only";
const RUNTIME_OVERVIEW_FIELD_STATUS_ORDER = ["ok", "missing", "invalid", "unsupported", "unknown"];
const RUNTIME_OVERVIEW_READINESS = Object.freeze({
  OK: "ok",
  MISSING: "missing",
  INVALID: "invalid",
  UNSUPPORTED: "unsupported",
  UNKNOWN: "unknown",
});
const POC_NO_PERSISTENCE_GUARD = true;
const MISSION_CONTROL_ENDPOINTS = Object.freeze([
  { key: "health", path: "/health", method: "GET" },
  { key: "state", path: "/state", method: "GET" },
  { key: "settings", path: "/settings", method: "GET" },
  { key: "runtimeSettings", path: "/runtime/settings", method: "GET" },
  { key: "runtimeProcesses", path: "/runtime/processes", method: "GET" },
  { key: "brain", path: "/brain/adapters", method: "GET" },
  { key: "audio", path: "/audio/devices", method: "GET" },
  { key: "voice", path: "/voice/listening", method: "GET" },
  { key: "voiceRuntime", path: "/voice/runtime", method: "GET" },
  { key: "voiceQueue", path: "/voice/queue?limit=12", method: "GET" },
  { key: "tools", path: "/tools", method: "GET" },
  { key: "approvals", path: "/approvals?limit=25", method: "GET" },
  { key: "memory", path: "/memory?active_only=true&limit=25", method: "GET" },
  { key: "memoryItems", path: "/memory/items", method: "GET" },
  { key: "events", path: "/events?latest=true&limit=50", method: "GET" },
]);
const RUNTIME_OVERVIEW_FIELD_SOURCES = Object.freeze({
  health: "Health",
  state: "State",
  settings: "Settings",
  runtimeSettings: "Runtime settings",
  runtimeProcesses: "Runtime processes",
  brain: "Brain adapters",
  audio: "Audio devices",
  voice: "Voice listening",
  voiceRuntime: "Voice runtime",
  voiceQueue: "Voice queue",
  tools: "Tools registry",
  approvals: "Approvals",
  memory: "Memory",
  memoryItems: "Memory OS",
  events: "Events",
  contract: "Jarvis contract",
});

// Ludzkie nazwy narzędzi (rejestr daemona) — używane w kartach zgód i w
// sekcji „Możliwości Jarvisa”. Fallback dla rodzin ui_/screen_/terminal_,
// a na końcu surowa nazwa, żeby nowe narzędzie nigdy nie zniknęło z widoku.
const TOOL_LABELS = {
  file_read: "Odczyt pliku",
  file_write: "Zapis pliku",
  memory_save: "Zapis do pamięci",
  shell_read: "Polecenie w terminalu",
  screen_read_window: "Odczyt okna ekranu",
  screen_ocr_region: "Odczyt ekranu (OCR)",
  system_status: "Stan systemu",
  ui_active_app: "Sterowanie UI: aktywna aplikacja",
  ui_read_window: "Sterowanie UI: odczyt okna",
  ui_click: "Sterowanie UI: kliknięcie",
  ui_type: "Sterowanie UI: pisanie",
  ui_focus_app: "Sterowanie UI: fokus aplikacji",
  terminal_read_screen: "Odczyt ekranu terminala",
  terminal_paste: "Wklejenie do terminala",
  echo: "Echo (test)",
  approval_probe: "Sonda zgód (demo)",
};

function toolLabel(name) {
  const key = typeof name === "string" ? name : "";
  if (TOOL_LABELS[key]) {
    return TOOL_LABELS[key];
  }
  const humanTail = (prefix, lead) =>
    `${lead}: ${key.slice(prefix.length).replace(/_/g, " ")}`;
  if (key.startsWith("ui_")) {
    return humanTail("ui_", "Sterowanie UI");
  }
  if (key.startsWith("screen_")) {
    return humanTail("screen_", "Ekran");
  }
  if (key.startsWith("terminal_")) {
    return humanTail("terminal_", "Terminal");
  }
  return key || "narzędzie";
}

// Etykiety klas ryzyka po polsku (PermissionClass w daemonie).
const RISK_LABELS = {
  safe_read: "bezpieczny odczyt",
  safe_status: "odczyt stanu",
  file_read: "czyta pliki",
  file_write: "pisze pliki",
  shell_read: "czyta przez terminal",
  shell_write: "pisze przez terminal",
  network: "sieć",
  destructive: "destrukcyjne — zawsze pyta",
  ui_read: "czyta interfejs",
  ui_act: "steruje interfejsem",
  screen_read: "czyta ekran",
  terminal_read: "czyta terminal",
  terminal_write: "pisze do terminala",
  memory_write: "zapis do pamięci",
};

function riskLabel(risk) {
  const key = typeof risk === "string" ? risk : "";
  return RISK_LABELS[key] || key || "nieznane";
}

// Waga ryzyka dla koloru chipa: odczyty spokojne (szarość), zapisy uważne
// (bursztyn), destructive alarmowe (czerwień). Nieznane traktuj jak zapis.
const RISK_TIERS = {
  safe_read: "read",
  safe_status: "read",
  file_read: "read",
  shell_read: "read",
  ui_read: "read",
  screen_read: "read",
  terminal_read: "read",
  network: "read",
  file_write: "write",
  shell_write: "write",
  ui_act: "write",
  terminal_write: "write",
  memory_write: "write",
  destructive: "destructive",
};

function riskTier(risk) {
  const key = typeof risk === "string" ? risk : "";
  return RISK_TIERS[key] || "write";
}

// Rodzaje bloków pamięci po polsku (MEMORY_KINDS w daemonie).
const MEMORY_KIND_LABELS = {
  identity: "Tożsamość",
  user_preference: "Preferencja",
  project: "Projekt",
  fact: "Fakt",
  summary: "Podsumowanie",
  temporary: "Tymczasowe",
};

function memoryKindLabel(kind) {
  const key = typeof kind === "string" ? kind : "";
  return MEMORY_KIND_LABELS[key] || key || "notatka";
}

// Typy zdarzeń daemona po ludzku (EventType). Fallback po rodzinie, na końcu
// surowy typ — dziennik nigdy nie gubi wiersza, ale prawie zawsze mówi po
// polsku. Nazwy krótkie i operatorskie („Nasłuch: początek”, nie techniczne
// „listening.lease.created”).
const EVENT_LABELS = {
  "daemon.started": "Daemon: start",
  "daemon.stopped": "Daemon: zatrzymany",
  "daemon.failed": "Daemon: błąd",
  "state.changed": "Zmiana stanu",
  "input.text.received": "Wiadomość tekstowa",
  "input.voice.transcribed": "Głos rozpoznany",
  "input.rejected": "Wejście odrzucone",
  "turn.started": "Tura: start",
  "turn.context.built": "Tura: kontekst gotowy",
  "turn.finished": "Tura: koniec",
  "turn.failed": "Tura: błąd",
  "turn.cancelled": "Tura: przerwana",
  "brain.requested": "Model: zapytanie",
  "brain.responded": "Model: odpowiedź",
  "brain.failed": "Model: błąd",
  "brain.cancelled": "Model: przerwane",
  "brain.switched": "Model: przełączony",
  "voice.speak.queued": "Mowa: w kolejce",
  "voice.speak.started": "Mowa: start",
  "voice.speak.finished": "Wypowiedź zakończona",
  "voice.speak.cancelled": "Mowa: przerwana",
  "voice.speak.failed": "Mowa: błąd",
  "audio.devices.snapshot": "Audio: urządzenia",
  "listening.lease.created": "Nasłuch: początek",
  "listening.lease.released": "Nasłuch: koniec",
  "listening.lease.expired": "Nasłuch: wygasł",
  "listening.lease.cancelled": "Nasłuch: anulowany",
  "memory.updated": "Pamięć: zaktualizowana",
  "memory.disabled": "Pamięć: notatka wyłączona",
  "memory.candidate.created": "Pamięć: propozycja",
  "memory.candidate.promoted": "Pamięć: zatwierdzona",
  "approval.created": "Zgoda: prośba",
  "approval.approved": "Zgoda: zatwierdzona",
  "approval.rejected": "Zgoda: odrzucona",
  "approval.expired": "Zgoda: wygasła",
  "tool.requested": "Narzędzie: prośba",
  "tool.approval.required": "Narzędzie: wymaga zgody",
  "tool.approved": "Narzędzie: dopuszczone",
  "tool.rejected": "Narzędzie: odrzucone",
  "tool.started": "Narzędzie: start",
  "tool.finished": "Narzędzie: koniec",
  "tool.failed": "Narzędzie: błąd",
  "error.raised": "Błąd",
  "worker.job.created": "Zadanie w tle: utworzone",
  "worker.job.progress": "Zadanie w tle: postęp",
  "worker.job.finished": "Zadanie w tle: koniec",
  "worker.job.failed": "Zadanie w tle: błąd",
  "worker.job.cancelled": "Zadanie w tle: przerwane",
  "runtime.legacy.conflict.detected": "Runtime: konflikt legacy",
  "runtime.process.observed": "Runtime: proces",
};

function eventLabel(type) {
  const key = typeof type === "string" ? type : "";
  if (EVENT_LABELS[key]) {
    return EVENT_LABELS[key];
  }
  if (key.startsWith("turn.")) return "Tura: …";
  if (key.startsWith("voice.")) return "Mowa: …";
  if (key.startsWith("listening.")) return "Nasłuch: …";
  if (key.startsWith("approval.")) return "Zgoda: …";
  if (key.startsWith("tool.")) return "Narzędzie: …";
  if (key.startsWith("memory.")) return "Pamięć: …";
  if (key.startsWith("brain.")) return "Model: …";
  return key || "zdarzenie";
}

// Filtr dziennika wg rodziny typu: tury / głos / zgody / narzędzia.
function eventMatchesFilter(type, filter) {
  if (!filter || filter === "all") {
    return true;
  }
  const key = typeof type === "string" ? type : "";
  if (filter === "turns") {
    return key.startsWith("turn.");
  }
  if (filter === "voice") {
    return (
      key.startsWith("voice.") ||
      key.startsWith("listening.") ||
      key.startsWith("audio.") ||
      key === "input.voice.transcribed"
    );
  }
  if (filter === "approvals") {
    return key.startsWith("approval.");
  }
  if (filter === "tools") {
    return key.startsWith("tool.");
  }
  return true;
}

const el = {};

// Relative labels ("2 min temu") drift while the panel sits open; refresh
// every rendered [data-timestamp] node on this interval.
const RELATIVE_TIME_TICK_MS = 60000;

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  el.apiBaseInput.value = DEFAULT_API_BASE;
  bindEvents();
  refreshAll();
  window.setInterval(pollHealth, HEALTH_POLL_MS);
  window.setInterval(pollLiveFallback, LIVE_FALLBACK_POLL_MS);
  window.setInterval(refreshRelativeTimes, RELATIVE_TIME_TICK_MS);
});

// Heartbeat tick: keep the status pill current, and when the daemon comes back
// after being unreachable, repopulate every section (not just the pill).
async function pollHealth() {
  const wasOnline = cockpit.online;
  const ok = await refreshHealthAndState();
  if (ok && !wasOnline) {
    refreshAll();
  }
}

async function pollLiveFallback() {
  if (!cockpit.online || cockpit.stream.connected) {
    return;
  }
  await Promise.allSettled([
    refreshHistory(),
    refreshMemory(),
    refreshToolsAndApprovals(),
    refreshSettingsPreview(),
    refreshVoice(),
    refreshVoiceQueue(),
    refreshRuntimeOverview(),
    refreshEvents(),
    refreshRuntime(),
  ]);
}

function bindElements() {
  const ids = [
    "stateLabel",
    "refreshAllButton",
    "apiBaseInput",
    "healthHumanList",
    "healthStateList",
    "healthError",
    "textForm",
    "textInput",
    "sendButton",
    "inputError",
    "newConversationButton",
    "conversationSelect",
    "turnList",
    "historyError",
    "refreshMemoryButton",
    "memoryForm",
    "memoryKind",
    "memoryTitle",
    "memoryPriority",
    "memoryBody",
    "createMemoryButton",
    "memoryList",
    "memoryError",
    "refreshToolsButton",
    "toolList",
    "approvalList",
    "approvalsError",
    "approvalsBadge",
    "approvalNudge",
    "toolsError",
    "refreshMissionControlButton",
    "missionControlSummary",
    "missionControlModules",
    "missionControlChecklist",
    "voiceDoctorList",
    "providerDoctorList",
    "missionControlRefreshStatus",
    "refreshSettingsPreviewButton",
    "settingsPreviewList",
    "settingsPreviewError",
    "settingsPreviewSaveButton",
    "refreshSettingsButton",
    "refreshRuntimeOverviewButton",
    "runtimeOverviewList",
    "runtimeOverviewError",
    "brainAdapterSelect",
    "switchBrainButton",
    "brainAdapterLabel",
    "settingsForm",
    "settingKey",
    "settingValue",
    "saveSettingButton",
    "settingsList",
    "settingsError",
    "refreshEventsButton",
    "logFilter",
    "eventList",
    "eventsError",
    "refreshRuntimeButton",
    "runtimeList",
    "runtimeObservationList",
    "runtimeError",
    "pttModeButton",
    "listenToggle",
    "voiceStatus",
    "voiceStatusText",
    "voiceError",
    "voiceQueueList",
  ];

  for (const id of ids) {
    el[id] = document.getElementById(id);
  }
}

function bindEvents() {
  el.refreshAllButton.addEventListener("click", refreshAll);
  el.refreshMissionControlButton.addEventListener("click", refreshMissionControl);
  el.conversationSelect.addEventListener("change", async () => {
    const conversationId = el.conversationSelect.value;
    if (!conversationId) {
      return;
    }
    cockpit.selectedConversationId = conversationId;
    cockpit.composingNew = false;
    clearError(el.historyError);
    try {
      await refreshTurns(conversationId);
    } catch (error) {
      clearNode(el.turnList);
      renderError(el.historyError, error);
    }
  });
  el.refreshMemoryButton.addEventListener("click", refreshMemory);
  el.refreshToolsButton.addEventListener("click", refreshToolsAndApprovals);
  el.refreshSettingsPreviewButton.addEventListener("click", refreshSettingsPreview);
  el.refreshSettingsButton.addEventListener("click", refreshSettings);
  el.refreshRuntimeOverviewButton.addEventListener("click", refreshRuntimeOverview);
  el.switchBrainButton.addEventListener("click", switchBrain);
  el.settingsForm.addEventListener("submit", saveSetting);
  el.refreshEventsButton.addEventListener("click", refreshEvents);
  el.logFilter.addEventListener("change", () => {
    cockpit.logFilter = el.logFilter.value || "all";
    renderEvents(cockpit.lastEvents);
  });
  el.refreshRuntimeButton.addEventListener("click", refreshRuntime);
  el.textForm.addEventListener("submit", sendTextInput);
  el.textInput.addEventListener("keydown", (event) => {
    // Enter sends; Shift+Enter keeps inserting a newline.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el.textForm.requestSubmit();
    }
  });
  // Pole rośnie z treścią (2 → ~5 rzędów), potem przewija się wewnątrz —
  // komunikatorowy composer bez ręcznego ciągnięcia uchwytu.
  el.textInput.addEventListener("input", autoGrowComposer);
  for (const tab of document.querySelectorAll(".tab-button")) {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  }
  el.approvalNudge.addEventListener("click", () => switchView("approvals"));
  el.newConversationButton.addEventListener("click", () => {
    cockpit.selectedConversationId = null;
    cockpit.composingNew = true;
    ensureNewConversationOption();
    renderEmpty(el.turnList, "Nowa rozmowa — napisz pierwszą wiadomość poniżej.");
    el.textInput.focus();
  });
  el.pttModeButton.addEventListener("click", () => setVoiceMode("ptt"));
  el.listenToggle.addEventListener("click", () => setVoiceMode("listen"));
  el.memoryForm.addEventListener("submit", createMemoryBlock);
  el.apiBaseInput.addEventListener("change", () => {
    const nextBase = el.apiBaseInput.value.trim();
    cockpit.apiBase = nextBase || DEFAULT_API_BASE;
    el.apiBaseInput.value = cockpit.apiBase;
    cockpit.selectedConversationId = null;
    cockpit.approvedApprovals.clear();
    cockpit.conversationTitles.clear();
    disconnectStream("api base changed");
    cockpit.stream.lastEventId = 0;
    refreshAll();
  });
}

async function refreshAll() {
  const healthOk = await refreshHealthAndState();
  if (!healthOk) {
    clearDynamicSections();
    disconnectStream("daemon offline");
    scheduleHealthRetry();
    return;
  }
  cancelHealthRetry();

  await Promise.all([
    refreshVoice(),
    refreshVoiceQueue(),
    refreshHistory(),
    refreshMemory(),
    refreshToolsAndApprovals(),
    refreshSettingsPreview(),
    refreshSettings(),
    refreshRuntimeOverview(),
    refreshEvents(),
    refreshRuntime(),
  ]);
  connectStream();
}

// Keep re-polling health while the daemon is unreachable so the panel heals
// itself once the daemon comes up (order-of-startup no longer matters). A
// successful refreshAll() cancels the pending retry.
function scheduleHealthRetry() {
  if (cockpit.healthRetryTimer) {
    return;
  }
  cockpit.healthRetryTimer = window.setTimeout(() => {
    cockpit.healthRetryTimer = null;
    refreshAll();
  }, HEALTH_RETRY_MS);
}

function cancelHealthRetry() {
  if (cockpit.healthRetryTimer) {
    window.clearTimeout(cockpit.healthRetryTimer);
    cockpit.healthRetryTimer = null;
  }
}

async function refreshVoice() {
  clearError(el.voiceError);
  try {
    const payload = await requestJson("/voice/listening");
    cockpit.voice.enabled = Boolean(payload.voice_enabled);
    cockpit.voice.listening = Boolean(payload.listening);
    cockpit.voice.leases = Array.isArray(payload.leases) ? payload.leases : [];
  } catch (error) {
    cockpit.voice.enabled = false;
    cockpit.voice.listening = false;
    cockpit.voice.leases = [];
    renderError(el.voiceError, error);
  }
  renderVoice();
}

async function refreshVoiceQueue() {
  if (!el.voiceQueueList) {
    return;
  }
  try {
    const payload = await requestJson("/voice/queue?limit=12");
    renderVoiceQueue(Array.isArray(payload.voice_queue) ? payload.voice_queue : []);
  } catch (error) {
    clearNode(el.voiceQueueList);
    const row = document.createElement("div");
    row.className = "list-row";
    appendLine(row, "Kolejka głosu niedostępna", "input-line");
    appendLine(row, error.message || "request failed", "muted");
    el.voiceQueueList.appendChild(row);
  }
}

function renderVoiceQueue(rows) {
  clearNode(el.voiceQueueList);
  if (rows.length === 0) {
    renderEmpty(el.voiceQueueList, "Kolejka głosu pusta");
    return;
  }
  for (const item of rows) {
    const row = document.createElement("div");
    row.className = "list-row";
    appendLine(
      row,
      `${item.status || "unknown"} · ${item.kind || "sentence"} #${item.seq ?? "?"}`,
      "input-line",
    );
    appendLine(
      row,
      `${shortId(item.id)} · turn ${shortId(item.turn_id)} · ${item.interrupt_policy || "no_interrupt"}`,
      "muted",
    );
    if (item.text_preview) {
      appendLine(row, item.text_preview, "payload-line");
    }
    if (item.error) {
      appendLine(row, item.error, "error-line");
    }
    const timing = [
      item.created_at ? `utworzono ${formatRelative(item.created_at)}` : null,
      item.spoken_at ? `start audio ${formatRelative(item.spoken_at)}` : null,
    ].filter(Boolean);
    if (timing.length > 0) {
      appendLine(row, timing.join(" · "), "muted");
    }
    el.voiceQueueList.appendChild(row);
  }
}

function renderVoice() {
  const usable = cockpit.online && cockpit.voice.enabled && !POC_NO_PERSISTENCE_GUARD;
  el.pttModeButton.disabled = !usable;
  el.listenToggle.disabled = !usable;

  const locked = cockpit.voice.leases.some((lease) => lease.mode === "locked");
  el.pttModeButton.classList.toggle("active", usable && !locked);
  el.listenToggle.classList.toggle("active", usable && locked);

  let status = "cisza — przytrzymaj hotkey PTT";
  if (!cockpit.online) {
    status = "daemon offline";
  } else if (!cockpit.voice.enabled) {
    status = "głos wyłączony w configu";
  } else if (POC_NO_PERSISTENCE_GUARD) {
    status = "voice read-only in Mission Control POC";
  } else if (cockpit.voice.listening) {
    const holding = cockpit.voice.leases.some((lease) => lease.mode === "hold");
    status = holding ? "słucha (PTT)" : "słucha (nasłuch)";
  } else if (locked) {
    status = "nasłuch uzbrojony";
  }
  setText(el.voiceStatusText, status);
  // Fala przy statusie ożywa tylko, gdy mikrofon naprawdę zbiera.
  el.voiceStatus.classList.toggle("live", cockpit.voice.listening);
  // Status pokazujemy tylko, gdy mikrofon faktycznie słucha — cisza/spoczynek
  // nie ma po co zajmować miejsca „cisza — przytrzymaj hotkey" pod polem.
  el.voiceStatus.hidden = !cockpit.voice.listening;
  // Zbierający mikrofon to też „praca” — ramka ma wtedy obiegać.
  applyStateFrame();
}

// Panel ustawia TRYB słuchania (PTT vs ciągły nasłuch); samo trzymanie PTT
// żyje na globalnym hotkeyu (menubar), nie na przycisku w webview.
async function setVoiceMode(mode) {
  if (POC_NO_PERSISTENCE_GUARD) {
    renderError(
      el.voiceError,
      makeRequestError("Mission Control POC is read-only; no microphone activation from panel.", {
        route: mode,
      }),
    );
    return;
  }
  if (!cockpit.online || !cockpit.voice.enabled) {
    return;
  }
  const locked = cockpit.voice.leases.some((lease) => lease.mode === "locked");
  const path =
    mode === "listen" && !locked
      ? "/voice/listen/lock"
      : mode === "ptt" && locked
        ? "/voice/listen/unlock"
        : null;
  if (!path) {
    return;
  }
  clearError(el.voiceError);
  setBusy(el.pttModeButton, true);
  setBusy(el.listenToggle, true);
  try {
    await requestJson(path, { method: "POST", body: {} });
  } catch (error) {
    renderError(el.voiceError, error);
  } finally {
    setBusy(el.pttModeButton, false);
    setBusy(el.listenToggle, false);
  }
  await refreshVoice();
}

async function refreshHealthAndState() {
  clearError(el.healthError);

  try {
    const health = await requestJson("/health");
    setOnline(true);

    let statePayload = {};
    try {
      statePayload = await requestJson("/state");
    } catch (error) {
      renderError(el.healthError, error);
    }

    const merged = { ...health, ...statePayload };
    setText(el.stateLabel, merged.state || "unknown");
    if (merged.state) {
      cockpit.runtimeState = merged.state;
    }
    applyStateFrame();
    syncPendingApprovals(merged.pending_approval_count);
    renderHealthHuman(merged);
    renderKeyValues(el.healthStateList, [
      ["service", merged.service],
      ["state", merged.state],
      ["started", merged.started],
      ["schema_version", merged.schema_version],
      ["pending_approval_count", merged.pending_approval_count],
      ["brain_adapter", merged.brain_adapter],
      ["voice_enabled", merged.voice_enabled],
    ]);
    return true;
  } catch (error) {
    setOnline(false);
    setText(el.stateLabel, "offline");
    clearNode(el.healthHumanList);
    clearNode(el.healthStateList);
    renderError(el.healthError, error);
    return false;
  }
}

// Ludzki stan daemona w sekcji Połączenie: 3–4 pozycje po polsku zamiast
// surowej kv-listy (ta zostaje w „Diagnostyka (surowe)”).
function renderHealthHuman(merged) {
  renderKeyValues(el.healthHumanList, [
    ["Działa od", merged.started ? formatRelative(merged.started) : "n/a"],
    ["Wersja schematu", merged.schema_version],
    ["Głos", merged.voice_enabled ? "włączony" : "wyłączony"],
  ]);
}

async function refreshRuntimeOverview() {
  if (!el.runtimeOverviewList) {
    return;
  }
  clearError(el.runtimeOverviewError);

  const endpoints = missionControlSafeEndpointPlan();
  const settled = await Promise.allSettled(
    endpoints.map((entry) => requestJson(entry.path)),
  );
  const snapshot = { failures: [], sourceStatus: {} };
  for (let index = 0; index < endpoints.length; index += 1) {
    const { key, path } = endpoints[index];
    const result = settled[index];
    if (result.status === "fulfilled") {
      snapshot[key] = result.value || {};
      snapshot.sourceStatus[key] = { ok: true, path };
    } else {
      snapshot.failures.push(path);
      snapshot.sourceStatus[key] = { ok: false, path };
    }
  }

  cockpit.missionControl.snapshot = snapshot;
  cockpit.missionControl.lastRefreshAt = new Date().toISOString();
  renderMissionControl(snapshot);
  renderRuntimeOverview(snapshot);
}

function missionControlSafeEndpointPlan() {
  return MISSION_CONTROL_ENDPOINTS.map((entry) => ({
    key: entry.key,
    path: entry.path,
    method: "GET",
  }));
}

async function refreshMissionControl() {
  if (cockpit.missionControl.refreshing) {
    setText(el.missionControlRefreshStatus, "Refresh already running");
    return;
  }
  cockpit.missionControl.refreshing = true;
  setMissionControlRefreshBusy(true);
  setText(el.missionControlRefreshStatus, "Refresh started");
  try {
    const healthOk = await refreshHealthAndState();
    if (!healthOk) {
      renderMissionControl(missionControlOfflineSnapshot());
      setText(el.missionControlRefreshStatus, "Refresh finished: backend offline");
      return;
    }
    await Promise.allSettled([
      refreshVoice(),
      refreshVoiceQueue(),
      refreshToolsAndApprovals(),
      refreshMemory(),
      refreshEvents(),
      refreshSettingsPreview(),
      refreshRuntime(),
      refreshRuntimeOverview(),
    ]);
    setText(
      el.missionControlRefreshStatus,
      `Refresh finished ${formatClock(cockpit.missionControl.lastRefreshAt)}`,
    );
  } catch (error) {
    setText(el.missionControlRefreshStatus, `Refresh error: ${error.message || "request failed"}`);
    renderMissionControl(missionControlOfflineSnapshot(error));
  } finally {
    cockpit.missionControl.refreshing = false;
    setMissionControlRefreshBusy(false);
  }
}

function setMissionControlRefreshBusy(busy) {
  if (!el.refreshMissionControlButton) {
    return;
  }
  el.refreshMissionControlButton.disabled = busy;
  el.refreshMissionControlButton.classList.toggle("busy", busy);
}

function missionControlOfflineSnapshot(error) {
  return {
    sourceStatus: {
      health: { ok: false, path: "/health" },
      runtimeSettings: { ok: false, path: "/runtime/settings" },
    },
    failures: ["/health"],
    latestError: error && error.message ? error.message : "backend offline",
  };
}

const SETTINGS_PREVIEW_SECTION_ORDER = [
  "brain_provider",
  "voice_tts",
  "voice_stt",
  "endpointing_ptt",
  "queue_barge_in",
  "tools_internet",
  "personality",
  "developer_test",
];

const SETTINGS_PREVIEW_SECTION_LABELS = {
  brain_provider: "Brain / Provider",
  voice_tts: "Voice / TTS",
  voice_stt: "Voice / STT",
  endpointing_ptt: "Endpointing / PTT",
  queue_barge_in: "Queue / Barge-in",
  tools_internet: "Tools / Internet",
  personality: "Personality",
  developer_test: "Developer / Test",
};

const SETTINGS_PREVIEW_CONTROL_FIELDS = new Set([
  "brain_provider.provider",
  "brain_provider.model",
  "brain_provider.effort",
  "brain_provider.fast",
  "voice_tts.tts_provider",
  "voice_tts.tts_model",
  "voice_tts.voice_id",
  "voice_tts.speed_or_rate",
  "voice_stt.stt_provider",
  "voice_stt.stt_model",
  "queue_barge_in.manual_cancel_available",
]);

async function refreshSettingsPreview() {
  if (!el.settingsPreviewList) {
    return;
  }
  clearError(el.settingsPreviewError);
  try {
    const payload = await requestJson("/runtime/settings");
    cockpit.settingsPreview.payload = payload;
    cockpit.settingsPreview.model = settingsPreviewModelFromPayload(
      payload,
      cockpit.settingsPreview.overrides,
    );
    renderSettingsPreview(cockpit.settingsPreview.model);
  } catch (error) {
    clearNode(el.settingsPreviewList);
    renderError(el.settingsPreviewError, error);
  }
}

function settingsPreviewModelFromPayload(payload, overrides = {}) {
  const preview = safeObject(safeObject(payload).settings_preview);
  const model = {
    previewOnly: preview.preview_only !== false,
    saveImplemented: Boolean(preview.save_implemented),
    saveDisabledReason: preview.save_disabled_reason || "Save not implemented in POC",
    sections: settingsPreviewCloneSections(safeObject(preview.sections)),
    capabilityGraph: safeObject(safeObject(payload).capability_graph),
    compatibilityWarnings: Array.isArray(safeObject(payload).compatibility_warnings)
      ? payload.compatibility_warnings
      : Array.isArray(payload.compatibility_warnings)
        ? payload.compatibility_warnings
        : [],
    overrides: { ...safeObject(overrides) },
  };
  return settingsPreviewEvaluate(model);
}

function settingsPreviewApplyOverride(model, fieldId, value) {
  const next = {
    ...model,
    sections: settingsPreviewCloneSections(model.sections),
    overrides: { ...safeObject(model.overrides), [fieldId]: value },
  };
  return settingsPreviewEvaluate(next);
}

function settingsPreviewEvaluate(model) {
  const evaluated = {
    ...model,
    sections: settingsPreviewCloneSections(model.sections),
    overrides: { ...safeObject(model.overrides) },
  };

  for (const [fieldId, value] of Object.entries(evaluated.overrides)) {
    const field = settingsPreviewFieldById(evaluated, fieldId);
    if (!field) {
      continue;
    }
    field.effective = value;
    if (field.current !== value) {
      field.warning = field.warning || "Preview override active; reset required before save exists.";
    }
  }

  settingsPreviewEvaluateBrain(evaluated);
  settingsPreviewEvaluateVoiceTts(evaluated);
  settingsPreviewEvaluateVoiceStt(evaluated);
  settingsPreviewEvaluateQueueBargeIn(evaluated);
  return evaluated;
}

function settingsPreviewEvaluateBrain(model) {
  const providerField = settingsPreviewFieldById(model, "brain_provider.provider");
  if (!providerField) {
    return;
  }
  const provider = settingsPreviewBrainProvider(model, providerField.effective);
  const providerIds = settingsPreviewBrainProviders(model).map((item) => item.id);
  providerField.allowed_values = providerIds;
  providerField.disabled_values = settingsPreviewBrainProviders(model)
    .map((item) => settingsPreviewDisabledProviderOption(item))
    .filter(Boolean);
  providerField.invalidates = uniqueNonEmpty([
    ...(Array.isArray(providerField.invalidates) ? providerField.invalidates : []),
    "brain_provider.command_status",
    "brain_provider.credentials_or_command_status",
  ]);
  providerField.developer_only = Boolean(provider && provider.developer_only);
  if (!provider) {
    providerField.status = "invalid";
    providerField.blocker = "Selected provider is not present in backend capability graph.";
    return;
  }
  providerField.status = provider.available ? "ok" : "missing";
  providerField.blocker = provider.available ? null : provider.blocker || "Provider is unavailable.";
  providerField.warning = provider.developer_only
    ? "Developer/Test provider selected."
    : providerField.warning;

  const modelField = settingsPreviewFieldById(model, "brain_provider.model");
  const effortField = settingsPreviewFieldById(model, "brain_provider.effort");
  const fastField = settingsPreviewFieldById(model, "brain_provider.fast");
  const toolsField = settingsPreviewFieldById(model, "brain_provider.tools_support");
  const streamingField = settingsPreviewFieldById(model, "brain_provider.streaming_support");
  const contextField = settingsPreviewFieldById(model, "brain_provider.context_budget");
  const commandField = settingsPreviewFieldById(model, "brain_provider.command_status");
  const credentialsField = settingsPreviewFieldById(model, "brain_provider.credentials_or_command_status");

  const models = settingsPreviewProviderModels(provider);
  if (modelField) {
    modelField.allowed_values = models.map((item) => item.id);
    modelField.dependencies = ["brain_provider.provider"];
    if (models.length === 0) {
      const localProvider = provider.local_runtime || /local/i.test(String(provider.kind || ""));
      modelField.status = "missing";
      modelField.blocker = localProvider
        ? "Local provider selected but no local model exists."
        : "Selected provider has no allowed models.";
    } else if (!models.some((item) => item.id === modelField.effective)) {
      modelField.status = "invalid";
      modelField.blocker = "Selected model is stale for this provider; reset required.";
    } else {
      modelField.status = "ok";
      modelField.blocker = null;
    }
  }

  if (effortField) {
    const allowedEfforts = Array.isArray(provider.allowed_effort_values)
      ? provider.allowed_effort_values
      : [];
    effortField.allowed_values = allowedEfforts;
    effortField.dependencies = ["brain_provider.provider", "brain_provider.model"];
    if (effortField.effective === null || effortField.effective === undefined || effortField.effective === "") {
      effortField.status = allowedEfforts.length > 0 ? "missing" : "unsupported";
      effortField.blocker = null;
    } else if (allowedEfforts.length === 0) {
      effortField.status = "unsupported";
      effortField.blocker = "Selected provider/model does not support effort; reset required.";
    } else if (!allowedEfforts.includes(effortField.effective)) {
      effortField.status = "invalid";
      effortField.blocker = "Selected effort is stale for this provider/model; reset required.";
    } else {
      effortField.status = "ok";
      effortField.blocker = null;
    }
  }

  if (fastField) {
    fastField.allowed_values = [true, false];
    fastField.dependencies = ["brain_provider.provider", "brain_provider.model"];
    fastField.disabled_values = provider.fast_supported
      ? []
      : [{ value: true, reason: "Selected provider/model does not support fast mode." }];
    if (!provider.fast_supported && fastField.effective === true) {
      fastField.status = "unsupported";
      fastField.blocker = "Fast is enabled but selected provider/model does not support fast.";
    } else {
      fastField.status = "ok";
      fastField.blocker = null;
    }
  }

  if (toolsField) {
    toolsField.effective = provider.tools_supported ? "yes" : "no";
    toolsField.current = toolsField.current || toolsField.effective;
    toolsField.status = provider.tools_supported ? "ok" : "unsupported";
  }
  if (streamingField) {
    streamingField.effective = provider.streaming_supported ? "yes" : "no";
    streamingField.current = streamingField.current || streamingField.effective;
    streamingField.status = provider.streaming_supported ? "ok" : "unsupported";
  }
  if (contextField) {
    contextField.effective = safeObject(provider.context_info).budget_chars || null;
    contextField.status = contextField.effective ? "ok" : "unknown";
  }
  if (commandField) {
    const command = settingsPreviewProviderCommandState(provider);
    commandField.effective = command.value;
    commandField.status = command.status;
    commandField.blocker = command.blocker;
    commandField.dependencies = ["brain_provider.provider"];
  }
  if (credentialsField) {
    const credentials = settingsPreviewProviderCredentialsOrCommandState(provider);
    credentialsField.effective = credentials.value;
    credentialsField.status = credentials.status;
    credentialsField.blocker = credentials.blocker;
    credentialsField.dependencies = ["brain_provider.provider"];
  }
}

function settingsPreviewProviderCommandState(provider) {
  const support = settingsPreviewSupportState(firstPresent(
    safeObject(provider).command_status,
    safeObject(provider).provider_command_status,
  ));
  if (support === "yes") {
    return { value: "yes", status: "ok", blocker: null };
  }
  if (support === "no") {
    return { value: "no", status: "missing", blocker: "Provider command is missing." };
  }
  if (provider && provider.available === false) {
    return { value: "unknown", status: "missing", blocker: "Provider command readiness is unavailable." };
  }
  return { value: "unknown", status: "unknown", blocker: null };
}

function settingsPreviewProviderCredentialsOrCommandState(provider) {
  const support = settingsPreviewSupportState(firstPresent(
    safeObject(provider).command_status,
    safeObject(provider).provider_command_status,
  ));
  if (support === "yes") {
    return { value: "ok", status: "ok", blocker: null };
  }
  if (support === "no") {
    return {
      value: "missing",
      status: "missing",
      blocker: "Provider command or credential readiness is missing.",
    };
  }
  if (provider && provider.available === false) {
    return {
      value: "unavailable",
      status: "missing",
      blocker: "Provider command or credential readiness is unavailable.",
    };
  }
  return { value: "unknown", status: "unknown", blocker: null };
}

function settingsPreviewSupportState(value) {
  const raw = typeof value === "object" && value !== null ? projectionValue(value) : value;
  if (typeof raw === "boolean") {
    return raw ? "yes" : "no";
  }
  const normalized = String(raw ?? "").trim().toLowerCase();
  if (["yes", "true", "supported", "available", "ok", "enabled"].includes(normalized)) {
    return "yes";
  }
  if (["no", "false", "unsupported", "missing", "unavailable", "disabled"].includes(normalized)) {
    return "no";
  }
  return "unknown";
}

function settingsPreviewEvaluateVoiceTts(model) {
  const providerField = settingsPreviewFieldById(model, "voice_tts.tts_provider");
  if (!providerField) {
    return;
  }
  const providers = settingsPreviewVoiceProviders(model, "tts");
  const provider = providers.find((item) => item.id === providerField.effective);
  providerField.allowed_values = providers.map((item) => item.id);
  providerField.disabled_values = providers
    .filter((item) => !item.available)
    .map((item) => ({ value: item.id, reason: "TTS provider is unavailable." }));
  if (!providerField.effective) {
    providerField.status = "missing";
    providerField.blocker = providerField.blocker || "Voice enabled but TTS provider is missing.";
  } else if (!provider) {
    providerField.status = "invalid";
    providerField.blocker = "Selected TTS provider is not present in backend capability graph.";
  } else if (!provider.available) {
    providerField.status = "missing";
    providerField.blocker = "Selected TTS provider is unavailable.";
  } else {
    providerField.status = "ok";
    providerField.blocker = null;
  }

  const ttsModel = settingsPreviewFieldById(model, "voice_tts.tts_model");
  const voiceId = settingsPreviewFieldById(model, "voice_tts.voice_id");
  const speed = settingsPreviewFieldById(model, "voice_tts.speed_or_rate");
  const providerModels = provider && Array.isArray(provider.models) ? provider.models : [];
  const voiceIds = provider && Array.isArray(provider.voice_ids) ? provider.voice_ids : [];
  const speedSupported = Boolean(provider && safeObject(provider.controls).speed);

  if (ttsModel) {
    ttsModel.allowed_values = providerModels.map((item) => item.id);
    if (providerModels.length === 0) {
      ttsModel.status = providerField.effective && providerField.effective !== "mock" ? "missing" : "unknown";
    } else if (ttsModel.effective && !providerModels.some((item) => item.id === ttsModel.effective)) {
      ttsModel.status = "invalid";
      ttsModel.blocker = "Selected TTS model is stale for this provider; reset required.";
    } else {
      ttsModel.status = "ok";
      ttsModel.blocker = null;
    }
  }
  if (voiceId) {
    voiceId.allowed_values = voiceIds;
    if (providerField.effective === "supertonic" && !voiceId.effective) {
      voiceId.status = "missing";
      voiceId.blocker = "TTS provider requires voice_id.";
    } else if (voiceIds.length > 0 && voiceId.effective && !voiceIds.includes(voiceId.effective)) {
      voiceId.status = "invalid";
      voiceId.blocker = "Selected voice_id is stale for this TTS provider; reset required.";
    } else {
      voiceId.status = providerField.effective === "supertonic" ? "ok" : "unknown";
      voiceId.blocker = null;
    }
  }
  if (speed) {
    speed.allowed_values = speedSupported ? speed.allowed_values.length > 0 ? speed.allowed_values : [0.8, 1.0, 1.15, 1.35] : [];
    speed.disabled_values = speedSupported
      ? []
      : [{ value: "speed", reason: "Selected TTS provider does not support speed/rate." }];
    speed.status = speedSupported ? "ok" : "unsupported";
  }
}

function settingsPreviewEvaluateVoiceStt(model) {
  const providerField = settingsPreviewFieldById(model, "voice_stt.stt_provider");
  if (!providerField) {
    return;
  }
  const providers = settingsPreviewVoiceProviders(model, "stt");
  const provider = providers.find((item) => item.id === providerField.effective);
  providerField.allowed_values = providers.map((item) => item.id);
  providerField.disabled_values = providers
    .filter((item) => !item.available)
    .map((item) => ({ value: item.id, reason: "STT provider is unavailable." }));
  if (!providerField.effective) {
    providerField.status = "missing";
    providerField.blocker = providerField.blocker || "Voice enabled but STT provider is missing.";
  } else if (!provider) {
    providerField.status = "invalid";
    providerField.blocker = "Selected STT provider is not present in backend capability graph.";
  } else if (!provider.available) {
    providerField.status = "missing";
    providerField.blocker = "Selected STT provider/runtime is unavailable.";
  } else {
    providerField.status = "ok";
    providerField.blocker = null;
  }

  const sttModel = settingsPreviewFieldById(model, "voice_stt.stt_model");
  const endpointing = settingsPreviewFieldById(model, "voice_stt.endpointing_support");
  const providerModels = provider && Array.isArray(provider.models) ? provider.models : [];
  if (sttModel) {
    sttModel.allowed_values = providerModels.map((item) => item.id);
    if (providerField.effective && providerField.effective !== "mock" && providerModels.length === 0) {
      sttModel.status = "missing";
      sttModel.blocker = "Selected STT provider has no model/runtime in capability graph.";
    } else if (providerModels.length > 0 && sttModel.effective && !providerModels.some((item) => item.id === sttModel.effective)) {
      sttModel.status = "invalid";
      sttModel.blocker = "Selected STT model is stale for this provider; reset required.";
    } else {
      sttModel.status = sttModel.effective ? "ok" : "missing";
      sttModel.blocker = null;
    }
  }
  if (endpointing && provider) {
    endpointing.effective = Boolean(provider.endpointing_support);
    endpointing.status = provider.endpointing_support ? "ok" : "unsupported";
  }
}

function settingsPreviewEvaluateQueueBargeIn(model) {
  const manualCancel = settingsPreviewFieldById(model, "queue_barge_in.manual_cancel_available");
  const cancelSupport = settingsPreviewFieldById(model, "queue_barge_in.cancel_support");
  const voice = safeObject(safeObject(model.capabilityGraph).voice_capabilities);
  if (!manualCancel || !cancelSupport) {
    return;
  }
  const supportsCancel = Boolean(voice.cancellation_support);
  if (manualCancel.effective === true && !supportsCancel) {
    manualCancel.status = "unsupported";
    manualCancel.blocker = "Barge-in/manual cancel preview requires cancellation support.";
    cancelSupport.status = "unsupported";
  }
}

function settingsPreviewCloneSections(sections) {
  const cloned = {};
  for (const [sectionId, section] of Object.entries(safeObject(sections))) {
    const fields = {};
    for (const [fieldId, field] of Object.entries(safeObject(section.fields))) {
      fields[fieldId] = settingsPreviewCloneField(field, sectionId, fieldId);
    }
    cloned[sectionId] = {
      id: section.id || sectionId,
      label: section.label || SETTINGS_PREVIEW_SECTION_LABELS[sectionId] || sectionId,
      fields,
    };
  }
  return cloned;
}

function settingsPreviewCloneField(field, sectionId, fieldId) {
  const source = safeObject(field);
  const id = source.id || `${sectionId}.${fieldId}`;
  const current = source.current !== undefined ? source.current : null;
  return {
    id,
    label: source.label || fieldId,
    current,
    effective: source.effective !== undefined ? source.effective : current,
    status: source.status || "unknown",
    source: source.source || "unknown",
    allowed_values: Array.isArray(source.allowed_values) ? [...source.allowed_values] : [],
    disabled_values: Array.isArray(source.disabled_values) ? source.disabled_values.map((item) => ({ ...safeObject(item) })) : [],
    warning: source.warning || null,
    blocker: source.blocker || null,
    dependencies: Array.isArray(source.dependencies) ? [...source.dependencies] : [],
    invalidates: Array.isArray(source.invalidates) ? [...source.invalidates] : [],
    requires_restart: Boolean(source.requires_restart),
    requires_reload: Boolean(source.requires_reload),
    editable_now: Boolean(source.editable_now),
    editable_later: Boolean(source.editable_later),
    developer_only: Boolean(source.developer_only),
  };
}

function settingsPreviewFieldById(model, fieldId) {
  const [sectionId, key] = String(fieldId || "").split(".");
  return safeObject(safeObject(model.sections)[sectionId]).fields
    ? safeObject(safeObject(model.sections)[sectionId]).fields[key]
    : null;
}

function settingsPreviewBrainProviders(model) {
  const brain = safeObject(safeObject(model.capabilityGraph).brain_capabilities);
  return Array.isArray(brain.providers) ? brain.providers : [];
}

function settingsPreviewBrainProvider(model, providerId) {
  return settingsPreviewBrainProviders(model).find((item) => item.id === providerId) || null;
}

function settingsPreviewProviderModels(provider) {
  return Array.isArray(safeObject(provider).models) ? provider.models : [];
}

function settingsPreviewVoiceProviders(model, type) {
  const voice = safeObject(safeObject(model.capabilityGraph).voice_capabilities);
  const key = type === "stt" ? "stt_providers" : "tts_providers";
  return Array.isArray(voice[key]) ? voice[key] : [];
}

function settingsPreviewDisabledProviderOption(provider) {
  if (provider.developer_only) {
    return { value: provider.id, reason: "Developer/Test only" };
  }
  if (!provider.available) {
    return { value: provider.id, reason: provider.blocker || "Provider is unavailable" };
  }
  return null;
}

function renderSettingsPreview(model) {
  clearNode(el.settingsPreviewList);
  if (!model || Object.keys(safeObject(model.sections)).length === 0) {
    renderEmpty(el.settingsPreviewList, "Settings preview unavailable");
    return;
  }
  const banner = document.createElement("article");
  banner.className = "list-row settings-preview-banner";
  appendLine(banner, "Preview only - no Save behavior", "input-line");
  appendLine(
    banner,
    "Local changes show diffs, invalidated children, warnings, blockers, and restart/reload requirements. Nothing is persisted.",
    "muted",
  );
  el.settingsPreviewList.appendChild(banner);

  const diff = renderSettingsPreviewDiff(model);
  if (diff) {
    el.settingsPreviewList.appendChild(diff);
  }

  const warningRows = settingsPreviewWarningRows(model);
  if (warningRows.length > 0) {
    const warningsCard = document.createElement("article");
    warningsCard.className = "list-row settings-preview-warning-card";
    appendLine(warningsCard, "Compatibility warnings", "input-line");
    for (const warning of warningRows) {
      appendLine(warningsCard, warning, "muted");
    }
    el.settingsPreviewList.appendChild(warningsCard);
  }
  for (const sectionId of SETTINGS_PREVIEW_SECTION_ORDER) {
    const section = safeObject(model.sections)[sectionId];
    if (!section) {
      continue;
    }
    const card = document.createElement("article");
    card.className = "list-row settings-preview-section";
    appendLine(card, section.label || SETTINGS_PREVIEW_SECTION_LABELS[sectionId] || sectionId, "input-line");
    const fields = safeObject(section.fields);
    for (const key of Object.keys(fields)) {
      card.appendChild(renderSettingsPreviewField(fields[key], model));
    }
    el.settingsPreviewList.appendChild(card);
  }
}

function renderSettingsPreviewDiff(model) {
  const rows = settingsPreviewDiffRows(model);
  if (rows.length === 0) {
    return null;
  }
  const card = document.createElement("article");
  card.className = "list-row settings-preview-diff";
  appendLine(card, "Preview Diff", "input-line");
  for (const row of rows) {
    const item = document.createElement("div");
    item.className = "settings-preview-field";
    appendLine(item, row.field, "input-line");
    const values = document.createElement("dl");
    values.className = "kv-list settings-preview-values";
    renderKeyValues(values, [
      ["old value", row.oldValue],
      ["preview value", row.previewValue],
      ["invalidated children", row.invalidatedChildren],
      ["warnings introduced", row.warningsIntroduced],
      ["blockers introduced", row.blockersIntroduced],
      ["restart/reload", row.restartReload],
    ]);
    item.appendChild(values);
    card.appendChild(item);
  }
  return card;
}

function settingsPreviewDiffRows(model) {
  const rows = new Map();
  for (const fieldId of Object.keys(safeObject(model.overrides))) {
    const field = settingsPreviewFieldById(model, fieldId);
    if (!field) {
      continue;
    }
    const invalidated = Array.isArray(field.invalidates) ? field.invalidates : [];
    rows.set(field.id, settingsPreviewDiffRow(field, invalidated));
    for (const childId of invalidated) {
      const child = settingsPreviewFieldById(model, childId);
      if (!child) {
        continue;
      }
      if (
        child.warning ||
        child.blocker ||
        ["invalid", "missing", "unsupported"].includes(child.status) ||
        child.current !== child.effective
      ) {
        rows.set(child.id, settingsPreviewDiffRow(child, child.invalidates || []));
      }
    }
  }
  return [...rows.values()];
}

function settingsPreviewDiffRow(field, invalidated) {
  const invalidatedChildren = Array.isArray(invalidated) ? invalidated : [];
  const warnings = [];
  const blockers = [];
  if (field.warning) warnings.push(field.warning);
  if (field.blocker) blockers.push(field.blocker);
  const restartReload = [
    field.requires_restart ? "restart required" : null,
    field.requires_reload ? "reload required" : null,
  ].filter(Boolean).join(", ") || "none";
  const message = uniqueNonEmpty([...blockers, ...warnings]).join("; ") ||
    (invalidatedChildren.length > 0 ? "Preview invalidates dependent settings." : "Preview value changed.");
  const current = settingsPreviewValue(field.current);
  const preview = settingsPreviewValue(field.effective);
  return {
    field: field.id,
    fieldId: field.id,
    oldValue: current,
    current,
    previewValue: preview,
    preview,
    invalidatedChildren: invalidatedChildren.length > 0 ? invalidatedChildren.join(", ") : "none",
    warningsIntroduced: uniqueNonEmpty(warnings).join("; ") || "none",
    blockersIntroduced: uniqueNonEmpty(blockers).join("; ") || "none",
    restartReload,
    message,
  };
}

function renderSettingsPreviewField(field, model) {
  const row = document.createElement("div");
  row.className = `settings-preview-field status-${field.status || "unknown"}`;

  const header = document.createElement("div");
  header.className = "settings-preview-field-head";
  const label = document.createElement("strong");
  setText(label, field.label || field.id);
  const badge = document.createElement("span");
  badge.className = `settings-preview-badge status-${field.status || "unknown"}`;
  setText(badge, field.status || "unknown");
  header.append(label, badge);
  row.appendChild(header);

  const values = document.createElement("dl");
  values.className = "kv-list settings-preview-values";
  const valueRows = [
    ["current", settingsPreviewValue(field.current)],
    ["effective", settingsPreviewValue(field.effective)],
    ["source", field.source || "unknown"],
  ];
  if (Array.isArray(field.allowed_values) && field.allowed_values.length > 0) {
    valueRows.push(["allowed", field.allowed_values.map(settingsPreviewValue).join(", ")]);
  }
  if (Array.isArray(field.disabled_values) && field.disabled_values.length > 0) {
    valueRows.push(["disabled", field.disabled_values.map((item) => `${settingsPreviewValue(item.value)} (${item.reason || "disabled"})`).join(", ")]);
  }
  if (Array.isArray(field.dependencies) && field.dependencies.length > 0) {
    valueRows.push(["depends on", field.dependencies.join(", ")]);
  }
  if (Array.isArray(field.invalidates) && field.invalidates.length > 0) {
    valueRows.push(["invalidates", field.invalidates.join(", ")]);
  }
  if (field.requires_restart || field.requires_reload) {
    valueRows.push(["change impact", [field.requires_restart ? "restart" : null, field.requires_reload ? "reload" : null].filter(Boolean).join(" + ")]);
  }
  if (field.editable_now || field.editable_later || field.developer_only) {
    valueRows.push(["badges", [
      field.editable_now ? "editable now (preview only)" : null,
      field.editable_later ? "editable later" : null,
      field.developer_only ? "Developer/Test" : null,
    ].filter(Boolean).join(", ")]);
  }
  renderKeyValues(values, valueRows);
  row.appendChild(values);

  const message = field.blocker || field.warning;
  if (message) {
    appendLine(row, message, field.blocker ? "error-line" : "muted");
  }

  const control = settingsPreviewControlForField(field, model);
  if (control) {
    row.appendChild(control);
  }
  return row;
}

function settingsPreviewControlForField(field, model) {
  if (!SETTINGS_PREVIEW_CONTROL_FIELDS.has(field.id)) {
    return null;
  }
  if (typeof field.effective === "boolean") {
    const label = document.createElement("label");
    label.className = "settings-preview-control";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(field.effective);
    input.disabled = !field.editable_now && !field.editable_later;
    input.addEventListener("change", () => settingsPreviewControlChanged(field.id, input.checked));
    const text = document.createElement("span");
    setText(text, "Preview toggle");
    label.append(input, text);
    return label;
  }
  const allowed = Array.isArray(field.allowed_values) ? field.allowed_values : [];
  if (allowed.length === 0) {
    return null;
  }
  const label = document.createElement("label");
  label.className = "settings-preview-control";
  const text = document.createElement("span");
  setText(text, "Preview");
  const select = document.createElement("select");
  select.disabled = !field.editable_now && !field.editable_later;
  for (const value of allowed) {
    const option = document.createElement("option");
    option.value = settingsPreviewOptionValue(value);
    setText(option, settingsPreviewValue(value));
    option.disabled = settingsPreviewOptionDisabled(field, value);
    if (settingsPreviewOptionValue(value) === settingsPreviewOptionValue(field.effective)) {
      option.selected = true;
    }
    select.appendChild(option);
  }
  select.addEventListener("change", () => {
    settingsPreviewControlChanged(field.id, settingsPreviewParseControlValue(select.value, allowed));
  });
  label.append(text, select);
  return label;
}

function settingsPreviewControlChanged(fieldId, value) {
  cockpit.settingsPreview.overrides = {
    ...safeObject(cockpit.settingsPreview.overrides),
    [fieldId]: value,
  };
  cockpit.settingsPreview.model = settingsPreviewApplyOverride(
    cockpit.settingsPreview.model || settingsPreviewModelFromPayload(cockpit.settingsPreview.payload || {}),
    fieldId,
    value,
  );
  renderSettingsPreview(cockpit.settingsPreview.model);
}

function settingsPreviewOptionDisabled(field, value) {
  return (field.disabled_values || []).some((item) => item.value === value);
}

function settingsPreviewOptionValue(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function settingsPreviewParseControlValue(raw, allowed) {
  for (const value of allowed) {
    if (settingsPreviewOptionValue(value) === raw) {
      return value;
    }
  }
  return raw;
}

function settingsPreviewWarningRows(model) {
  const rows = [];
  for (const warning of model.compatibilityWarnings || []) {
    const item = safeObject(warning);
    rows.push(`${item.severity || "warning"} · ${item.group || "settings"} · ${item.message || item.id || "warning"}`);
  }
  return rows.slice(0, 8);
}

function settingsPreviewValue(value) {
  if (value === undefined || value === null || value === "") {
    return "unknown";
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.map(settingsPreviewValue).join(", ") : "none";
  }
  if (typeof value === "object") {
    return redactEventSummaryText(JSON.stringify(value));
  }
  return redactEventSummaryText(String(value));
}

function renderRuntimeOverview(snapshot) {
  clearNode(el.runtimeOverviewList);

  const sections = runtimeOverviewSections(snapshot || {});
  for (const section of sections) {
    const row = document.createElement("article");
    row.className = "list-row";
    appendLine(row, section.title, "input-line");

    const values = document.createElement("dl");
    values.className = "kv-list";
    renderKeyValues(values, section.rows);
    row.appendChild(values);
    el.runtimeOverviewList.appendChild(row);
  }

  const failures = Array.isArray(snapshot.failures) ? snapshot.failures : [];
  if (failures.length > 0) {
    el.runtimeOverviewError.hidden = false;
    setText(
      el.runtimeOverviewError,
      `Some runtime overview sources failed: ${failures.join(", ")}`,
    );
  }
}

function renderMissionControl(snapshot) {
  if (!el.missionControlSummary) {
    return;
  }
  const safeSnapshot = snapshot || {};
  const summary = operatorSummaryFromSnapshot(safeSnapshot);
  renderMissionSummary(summary);
  renderMissionModules(safeSnapshot, summary);
  renderPocChecklist(safeSnapshot);
  renderVoiceDoctor(safeSnapshot);
  renderProviderDoctor(safeSnapshot);
}

function renderMissionSummary(summary) {
  clearNode(el.missionControlSummary);

  const head = document.createElement("div");
  head.className = "mission-summary-head";
  const title = document.createElement("p");
  title.className = "mission-summary-title";
  setText(title, `Jarvis POC status: ${summary.statusLine}`);
  head.append(title, missionStatusChip(summary.status, summary.status));
  el.missionControlSummary.appendChild(head);

  const values = document.createElement("dl");
  values.className = "kv-list";
  renderKeyValues(values, [
    ["top blockers", summary.blockers.length > 0 ? summary.blockers.slice(0, 3).join("; ") : "none"],
    ["top warnings", summary.warnings.length > 0 ? summary.warnings.slice(0, 3).join("; ") : "none"],
    ["next action", summary.nextAction],
    ["last refresh", summary.lastRefreshTime],
    ["backend", summary.backendConnected ? "connected" : "offline"],
    ["last important event", summary.lastImportantEvent || "none"],
    ["safety", summary.safetyGuarantee],
  ]);
  el.missionControlSummary.appendChild(values);
}

function missionStatusChip(status, label) {
  const chip = document.createElement("span");
  chip.className = `status-chip status-${status || "unknown"}`;
  setText(chip, label || "unknown");
  return chip;
}

function renderMissionModules(snapshot, summary) {
  clearNode(el.missionControlModules);
  const context = runtimeOverviewContext(snapshot || {});
  for (const card of missionControlModuleCards(context, summary)) {
    el.missionControlModules.appendChild(missionModuleCard(card));
  }
}

function missionModuleCard(card) {
  const row = document.createElement("article");
  row.className = `list-row mission-card status-${card.status || "unknown"}`;
  const head = document.createElement("div");
  head.className = "mission-summary-head";
  const title = document.createElement("p");
  title.className = "input-line";
  setText(title, card.title);
  head.append(title, missionStatusChip(card.status || "unknown", card.status || "unknown"));
  row.appendChild(head);

  const values = document.createElement("dl");
  values.className = "kv-list";
  renderKeyValues(values, card.rows);
  row.appendChild(values);
  return row;
}

function missionControlModuleCards(context, summary) {
  return [
    {
      title: "Lifecycle",
      status: summary.backendConnected ? "ready" : "offline",
      rows: [
        ["daemon status", firstPresent(context.state.state, context.health.state, context.health.service)],
        ["panel status", summary.backendConnected ? "backend connected" : "backend offline"],
        ["runtime dir/log dir", runtimeDirLogSummary(context)],
        ["last backend status", sourceStatusSummary(context, ["health", "state", "runtimeSettings"])],
        ["scripts/jarvis status hint", scriptsJarvisStatusHint(context)],
      ],
    },
    {
      title: "Brain / Provider",
      status: moduleStatusFromReadiness(providerCapabilityReadiness(context, context.activeAdapter)),
      rows: [
        ["active provider", firstPresent(projectionValue(context.brainRuntime.current_adapter), context.activeAdapter)],
        ["model", firstPresent(providerCurrentModel(context), configuredBrainModel(context))],
        ["effort / fast", `${providerEffortStatus(context)} / ${providerFastSupport(context)}`],
        ["command", providerCommandStatus(context)],
        ["credentials", providerCredentialsStatus(context)],
        ["unsupported stale values", providerCompatibilityWarnings(context).join("; ") || "none"],
        ["mock/dev warning", providerMockDevWarning(context)],
      ],
    },
    {
      title: "Voice Pipeline",
      status: moduleStatusFromReadiness(voiceRuntimeOverallReadiness(context)),
      rows: [
        voicePipelineRow(context, "capture_input", "Capture/Input", recorderEngine(context)),
        voicePipelineRow(context, "stt_transcription", "STT", firstPresent(configuredStt(context), effectiveStt(context))),
        voicePipelineRow(context, "endpointing_vad_ptt", "Endpointing/PTT", missionPttSummary(context)),
        voicePipelineRow(context, "tts_voice_model", "TTS/Voice Model", firstPresent(configuredTts(context), effectiveTts(context))),
        voicePipelineRow(context, "playback", "Playback", playbackEngine(context)),
        voicePipelineRow(context, "queue_barge_in", "Queue/Barge-in", voiceQueueSummary(context.queueRows, context.voiceQueue)),
      ],
    },
    {
      title: "Memory / Approval",
      status: moduleStatusFromSources(context, ["memory", "memoryItems", "approvals"]),
      rows: [
        ["memory enabled", memoryEnabledSummary(context)],
        ["latest memory status", latestMemoryStatus(context)],
        ["pending approvals", pendingApprovalSummary(context)],
        ["already-executed/idempotent", cockpit.approvedApprovals.size > 0 ? "approved retry state visible" : "not active"],
        ["approval blocker", approvalBlocker(context)],
      ],
    },
    {
      title: "Tools / Internet",
      status: moduleStatusFromSources(context, ["tools"]),
      rows: [
        ["tools", context.tools.length > 0 ? `${context.tools.length} known` : "unknown/none"],
        ["internet/network", networkToolSummary(context.tools)],
        ["approval-required tools", toolRiskSummary(context.tools)],
        ["provider tools support", providerSupportValue(context, "tools_support")],
      ],
    },
    {
      title: "Trace / Logs",
      status: moduleStatusFromSources(context, ["events", "runtimeSettings"]),
      rows: [
        ["latest turn", latestTurnBrief(context)],
        ["latest event time", latestEventTime(context.events)],
        ["latest safe error", traceLatestSafeError(context)],
        ["logs newest-first", "newest-first and redacted"],
      ],
    },
  ];
}

function voicePipelineRow(context, groupKey, label, currentValue) {
  const status = voiceRuntimeGroupReadiness(context, groupKey);
  const warnings = voiceRuntimeGroupWarnings(context, groupKey);
  const blocker = warnings.find((item) => /missing|invalid|disabled|unavailable|block/i.test(item));
  const latestError = latestVoiceLayerError(context, groupKey, ["voice", "audio", "speech", "stt", "tts"]);
  return [
    label,
    [
      status,
      `current: ${overviewValue(currentValue)}`,
      blocker ? `blocker: ${overviewValue(blocker)}` : null,
      !blocker && warnings.length > 0 ? `warning: ${warnings[0]}` : null,
      latestError && latestError !== "none in recent events" ? `latest safe error: ${latestError}` : null,
    ].filter(Boolean).join(" · "),
  ];
}

function renderPocChecklist(snapshot) {
  clearNode(el.missionControlChecklist);
  for (const item of pocChecklistItems(snapshot || {})) {
    const row = document.createElement("article");
    row.className = `checklist-item status-${item.status}`;
    const head = document.createElement("p");
    head.className = "input-line";
    const label = document.createElement("span");
    setText(label, item.label);
    head.append(label, missionStatusChip(item.status, item.status));
    row.appendChild(head);
    appendLine(row, item.why, "payload-line");
    appendLine(row, `source: ${item.source}`, "muted");
    appendLine(row, `manual: ${item.hint}`, "muted");
    el.missionControlChecklist.appendChild(row);
  }
}

function pocChecklistItems(snapshot) {
  const context = runtimeOverviewContext(snapshot || {});
  const backend = operatorBackendConnected(context);
  const runtimeLoaded = operatorRuntimeProjectionLoaded(context);
  const queueVisible = runtimeOverviewSourceAvailable(context, "voiceQueue");
  const eventsVisible = runtimeOverviewSourceAvailable(context, "events");
  const memoryVisible =
    runtimeOverviewSourceAvailable(context, "memory") ||
    runtimeOverviewSourceAvailable(context, "memoryItems");
  const approvalsVisible = runtimeOverviewSourceAvailable(context, "approvals");
  const providerKnown = firstPresent(
    projectionValue(context.brainRuntime.current_adapter),
    context.activeAdapter,
  );
  const voiceGroups = Object.keys(voiceRuntimeGroups(context));
  const turnId = traceValue(context, "turn_id");
  return [
    checklistItem(
      "Lifecycle alive",
      backend ? "pass" : "fail",
      backend ? "daemon health/state loaded" : "backend offline or health missing",
      "/health + /state",
      "Run scripts/jarvis status if this fails.",
    ),
    checklistItem(
      "Text turn path available",
      backend && runtimeLoaded ? "pass" : backend ? "manual" : "fail",
      backend ? "panel can send POST /input/text outside Mission Control" : "backend offline",
      "panel composer + runtime projection",
      "Send one short text turn from Chat.",
    ),
    checklistItem(
      "Panel live refresh active",
      cockpit.stream.connected ? "pass" : backend ? "manual" : "fail",
      cockpit.stream.connected ? "WebSocket stream is live" : "fallback polling/refresh button available",
      "/stream + fallback refresh",
      "Watch logs update after one new event.",
    ),
    checklistItem(
      "PTT available",
      pttChecklistStatus(context),
      pttChecklistWhy(context),
      "/voice/runtime endpointing_vad_ptt",
      "Hold the native hotkey; Mission Control must not activate the mic.",
    ),
    checklistItem(
      "Voice queue observable",
      queueVisible ? "pass" : "unknown",
      queueVisible ? "/voice/queue projection loaded" : "voice queue source missing",
      "/voice/queue?limit=12",
      "Speak once and verify queued/final/error rows appear.",
    ),
    checklistItem(
      "Barge-in/cancel observable",
      bargeInChecklistStatus(context),
      bargeInChecklistWhy(context),
      "/voice/runtime queue_barge_in + voice.speak.cancelled",
      "Interrupt speech and verify cancellation reason appears.",
    ),
    checklistItem(
      "Memory visible",
      memoryVisible ? "pass" : "unknown",
      memoryVisible ? "memory summaries/items loaded" : "memory source missing",
      "/memory + /memory/items",
      "Create or approve memory, then refresh.",
    ),
    checklistItem(
      "Approval visible",
      approvalsVisible ? "manual" : "unknown",
      approvalsVisible ? "approval list source loaded" : "approvals source missing",
      "/approvals?limit=25",
      "Create memory approval to verify decision cards.",
    ),
    checklistItem(
      "Provider status known",
      providerKnown ? "pass" : "unknown",
      providerKnown ? `provider ${overviewValue(providerKnown)} visible` : "active provider missing",
      "/runtime/settings brain.providers",
      "Send a turn and verify provider/model in latest trace.",
    ),
    checklistItem(
      "Voice settings split visible",
      voiceGroups.length >= 4 ? "pass" : "unknown",
      voiceGroups.length >= 4 ? "voice runtime groups split by layer" : "voice runtime groups missing",
      "/voice/runtime",
      "Verify Capture/STT/PTT/TTS/Playback/Queue rows.",
    ),
    checklistItem(
      "Latest turn trace visible",
      turnId ? "pass" : "unknown",
      turnId ? `latest turn ${shortId(turnId)} visible` : "no latest turn trace yet",
      "/runtime/settings latest_turn_trace",
      "Run one text or voice turn.",
    ),
    checklistItem(
      "Logs newest-first and redacted",
      eventsVisible ? "pass" : "unknown",
      eventsVisible ? "safe timeline path active" : "events source missing",
      "/events?latest=true&limit=50",
      "Confirm latest event is first and secrets are redacted.",
    ),
  ];
}

function checklistItem(label, status, why, source, hint) {
  return { label, status, why, source, hint };
}

function renderVoiceDoctor(snapshot) {
  clearNode(el.voiceDoctorList);
  const context = runtimeOverviewContext(snapshot || {});
  el.voiceDoctorList.appendChild(doctorKvCard("Voice Doctor", voiceDoctorRows(context)));
  el.voiceDoctorList.appendChild(doctorKvCard("Diagnosis", [
    ["rules", voiceDoctorDiagnoses(context).join("; ") || "none"],
    ["what this means", voiceDoctorMeaning(context)],
  ]));
}

function renderProviderDoctor(snapshot) {
  clearNode(el.providerDoctorList);
  const context = runtimeOverviewContext(snapshot || {});
  el.providerDoctorList.appendChild(doctorKvCard("Provider Doctor", providerDoctorRows(context)));
  el.providerDoctorList.appendChild(doctorKvCard("Diagnosis", [
    ["rules", providerDoctorDiagnoses(context).join("; ") || "none"],
    ["what this means", providerDoctorMeaning(context)],
  ]));
}

function doctorKvCard(title, rows) {
  const row = document.createElement("article");
  row.className = "list-row";
  appendLine(row, title, "input-line");
  const values = document.createElement("dl");
  values.className = "kv-list";
  renderKeyValues(values, rows);
  row.appendChild(values);
  return row;
}

function voiceDoctorRows(context) {
  return [
    ["speak_responses", firstPresent(voiceRuntimeConfiguredValue(context, "tts_voice_model", ["speak_responses"]), configuredSetting(context, ["voice.speak_responses", "speak_responses"]))],
    ["broker_enabled", firstPresent(voiceRuntimeEffectiveValue(context, "playback", ["broker_enabled"]), configuredSetting(context, ["voice.broker_enabled", "broker_enabled"]))],
    ["default_tts", firstPresent(configuredTts(context), effectiveTts(context))],
    ["default_stt", firstPresent(configuredStt(context), effectiveStt(context))],
    ["TTS readiness", voiceRuntimeGroupReadiness(context, "tts_voice_model")],
    ["STT readiness", voiceRuntimeGroupReadiness(context, "stt_transcription")],
    ["playback readiness", voiceRuntimeGroupReadiness(context, "playback")],
    ["capture policy", firstPresent(voiceRuntimeConfiguredValue(context, "capture_input", ["input_policy"]), configuredSetting(context, ["voice.input_policy"]))],
    ["PTT mode", missionPttSummary(context)],
    ["listening lease state", firstPresent(voiceRuntimeEffectiveValue(context, "endpointing_vad_ptt", ["active_leases", "lease_modes"]), context.voice.listening)],
    ["queue counts", voiceQueueCounts(context.queueRows)],
    ["current speaking item", currentSpeakingItem(context)],
    ["last cancellation reason", firstPresent(latestQueueValue(context.queueRows, ["cancellation_reason", "cancel_reason"]), latestBargeInSummary(context.events))],
    ["interrupted_previous_response", firstPresent(latestEventPayloadValue(context.events, ["interrupted_previous_response"]), traceValue(context, "interrupted_previous_response"))],
    ["latest voice error", latestVoiceLayerError(context, "tts_voice_model", ["voice", "audio", "speech", "stt", "tts"])],
  ];
}

function voiceDoctorDiagnoses(context) {
  const diagnoses = [];
  if (!operatorBackendConnected(context)) {
    diagnoses.push("backend offline");
  }
  if (context.voiceEnabled === false) {
    diagnoses.push("voice disabled");
  }
  const speak = firstPresent(voiceRuntimeConfiguredValue(context, "tts_voice_model", ["speak_responses"]), configuredSetting(context, ["voice.speak_responses", "speak_responses"]));
  if (speak === false || String(speak).toLowerCase() === "false") {
    diagnoses.push("speak disabled");
  }
  const broker = firstPresent(voiceRuntimeEffectiveValue(context, "playback", ["broker_enabled"]), configuredSetting(context, ["voice.broker_enabled", "broker_enabled"]));
  if (broker === false || String(broker).toLowerCase() === "false") {
    diagnoses.push("broker disabled");
  }
  if (voiceRuntimeGroupReadiness(context, "tts_voice_model") === RUNTIME_OVERVIEW_READINESS.MISSING || !firstPresent(configuredTts(context), effectiveTts(context))) {
    diagnoses.push("TTS missing");
  }
  if (voiceRuntimeGroupReadiness(context, "stt_transcription") === RUNTIME_OVERVIEW_READINESS.MISSING || !firstPresent(configuredStt(context), effectiveStt(context))) {
    diagnoses.push("STT missing");
  }
  const stuck = queueStuckWarning(context);
  if (stuck) {
    diagnoses.push("queue stuck");
  }
  if (voiceRuntimeGroupReadiness(context, "queue_barge_in") === RUNTIME_OVERVIEW_READINESS.MISSING) {
    diagnoses.push("cancellation path unavailable");
  }
  if (runtimeOverviewWarningsSummary(context).toLowerCase().includes("ptt")) {
    diagnoses.push("PTT source invalid warning");
  }
  return [...new Set(diagnoses)];
}

function voiceDoctorMeaning(context) {
  const diagnoses = voiceDoctorDiagnoses(context);
  if (diagnoses.includes("backend offline")) {
    return "Jarvis is not reachable; voice state cannot be trusted yet.";
  }
  if (diagnoses.includes("voice disabled")) {
    return "Voice is off; text/status can still be tested.";
  }
  if (diagnoses.some((item) => item.includes("TTS") || item.includes("STT") || item.includes("broker"))) {
    return "Voice path is incomplete; check the missing layer before live PTT.";
  }
  if (diagnoses.includes("queue stuck")) {
    return "Speech was queued or speaking too long; inspect voice queue and cancellation.";
  }
  return "Voice looks usable enough for the POC; run a manual PTT and queue check.";
}

function providerDoctorRows(context) {
  return [
    ["active provider/adapter", firstPresent(projectionValue(context.brainRuntime.current_adapter), context.activeAdapter)],
    ["active model", firstPresent(providerCurrentModel(context), configuredBrainModel(context))],
    ["command status", providerCommandStatus(context)],
    ["credentials status", providerCredentialsStatus(context)],
    ["effort support", providerAllowedEffort(context)],
    ["fast support", providerFastSupport(context)],
    ["context budget/window", firstPresent(projectionValue(safeObject(currentProviderCapability(context)).context_window_chars), configuredSetting(context, ["brain.context_budget_chars", "context.window"]))],
    ["streaming support", providerSupportValue(context, "streaming_support")],
    ["tools support", providerSupportValue(context, "tools_support")],
    ["local runtime status", localRuntimeStatus(context)],
    ["latest provider error", providerLatestError(context)],
  ];
}

function providerDoctorDiagnoses(context) {
  const diagnoses = [];
  const provider = safeObject(currentProviderCapability(context));
  const providerName = firstPresent(provider.name, projectionValue(context.brainRuntime.current_adapter), context.activeAdapter);
  const commandStatus = providerProjectionReadiness(provider, "provider_command_status");
  const credentialsStatus = providerProjectionReadiness(provider, "provider_credentials_status");
  const modelStatus = providerProjectionReadiness(provider, "current_model");
  if (commandStatus === RUNTIME_OVERVIEW_READINESS.MISSING) {
    diagnoses.push("provider command missing");
  }
  if (provider.configured === true && provider.available === false) {
    diagnoses.push("provider configured but unavailable");
  }
  if (!firstPresent(providerCurrentModel(context), configuredBrainModel(context)) || modelStatus === RUNTIME_OVERVIEW_READINESS.MISSING) {
    diagnoses.push("model missing/unknown");
  }
  if (providerProjectionReadiness(provider, "effort") === RUNTIME_OVERVIEW_READINESS.UNSUPPORTED || providerProjectionReadiness(provider, "effort") === RUNTIME_OVERVIEW_READINESS.INVALID) {
    diagnoses.push("effort unsupported");
  }
  if (providerSupportValue(context, "fast_supported") === "no" || providerProjectionReadiness(provider, "fast") === RUNTIME_OVERVIEW_READINESS.INVALID) {
    diagnoses.push("fast unsupported");
  }
  if (/local|ollama|mlx|llama|bielik|mistral/i.test(String(providerName || "")) && modelStatus !== RUNTIME_OVERVIEW_READINESS.OK) {
    diagnoses.push("local model missing");
  }
  if (providerName === "mock" || provider.kind === "Developer/Test") {
    diagnoses.push("mock/dev selected");
  }
  if (credentialsStatus === RUNTIME_OVERVIEW_READINESS.MISSING || credentialsStatus === RUNTIME_OVERVIEW_READINESS.UNKNOWN) {
    diagnoses.push("credentials unknown/missing");
  }
  return [...new Set(diagnoses)];
}

function providerDoctorMeaning(context) {
  const diagnoses = providerDoctorDiagnoses(context);
  if (diagnoses.includes("provider command missing")) {
    return "The selected adapter cannot be executed from the safe runtime view.";
  }
  if (diagnoses.includes("mock/dev selected")) {
    return "Mock/dev is fine for smoke tests, but not proof of a real provider.";
  }
  if (diagnoses.includes("credentials unknown/missing")) {
    return "Credentials are not exposed here; run a manual provider smoke if needed.";
  }
  return "Provider status is known enough for a POC text turn.";
}

function operatorSummaryFromSnapshot(snapshot) {
  const context = runtimeOverviewContext(snapshot || {});
  const backendConnected = operatorBackendConnected(context);
  const runtimeLoaded = operatorRuntimeProjectionLoaded(context);
  const blockers = operatorTopBlockers(context);
  const warnings = operatorTopWarnings(context);
  const degradingWarnings = operatorDegradingWarnings(context);
  let status = "ready";
  if (!backendConnected) {
    status = "offline";
  } else if (!runtimeLoaded) {
    status = "unknown";
  } else if (blockers.length > 0) {
    status = "blocked";
  } else if (degradingWarnings.length > 0) {
    status = "degraded";
  }
  return {
    status,
    statusLine: operatorStatusLine(status, blockers, warnings, context),
    blockers: blockers.slice(0, 3),
    warnings: warnings.slice(0, 3),
    nextAction: operatorNextAction(status, blockers, warnings),
    backendConnected,
    lastRefreshTime: cockpit.missionControl.lastRefreshAt
      ? formatFullDate(cockpit.missionControl.lastRefreshAt)
      : "unknown",
    lastImportantEvent: lastImportantEventSummary(context),
    safetyGuarantee:
      "POC mode - not production; no config writes; no settings save; no provider switch execution; no model loading; no microphone activation; no external API/provider calls; no paid calls; no raw secret rendering",
  };
}

function operatorBackendConnected(context) {
  return runtimeOverviewSourceAvailable(context, "health") && Boolean(
    firstPresent(context.health.service, context.health.state, context.state.state),
  );
}

function operatorRuntimeProjectionLoaded(context) {
  return (
    runtimeOverviewSourceAvailable(context, "runtimeSettings") &&
    Object.keys(context.runtimeSettings).length > 0
  );
}

function operatorTopBlockers(context) {
  const blockers = [];
  if (!operatorBackendConnected(context)) {
    blockers.push("backend offline");
    return blockers;
  }
  if (!operatorRuntimeProjectionLoaded(context)) {
    blockers.push("runtime projection missing");
  }
  const topBlockers = readinessValue(context, "top_blockers");
  if (Array.isArray(topBlockers)) {
    blockers.push(...topBlockers.map(overviewValue));
  } else if (firstPresent(topBlockers) && topBlockers !== "none") {
    blockers.push(overviewValue(topBlockers));
  }
  for (const [key, label] of [
    ["brain_provider_command", "brain provider command"],
    ["tts_provider", "TTS provider"],
    ["stt_provider", "STT provider"],
    ["panel_backend_connected", "panel backend connected"],
  ]) {
    const status = readinessStatus(context, key);
    if (status === RUNTIME_OVERVIEW_READINESS.MISSING || status === RUNTIME_OVERVIEW_READINESS.INVALID) {
      blockers.push(`${label}: ${overviewValue(readinessValue(context, key))}`);
    }
  }
  const providerStatus = providerCapabilityReadiness(context, context.activeAdapter);
  if (providerStatus === RUNTIME_OVERVIEW_READINESS.MISSING || providerStatus === RUNTIME_OVERVIEW_READINESS.INVALID) {
    blockers.push("provider configured but unavailable");
  }
  return uniqueNonEmpty(blockers).slice(0, 6);
}

function operatorTopWarnings(context) {
  const warnings = [];
  const readinessWarningsValue = readinessValue(context, "warnings");
  if (Array.isArray(readinessWarningsValue)) {
    warnings.push(...readinessWarningsValue.map(overviewValue));
  } else if (firstPresent(readinessWarningsValue)) {
    warnings.push(overviewValue(readinessWarningsValue));
  }
  warnings.push(...runtimeOverviewCompatibilityWarnings(context));
  warnings.push(...voiceRuntimeWarnings(context));
  warnings.push(...providerCompatibilityWarnings(context));
  const mockWarning = providerMockDevWarning(context);
  if (mockWarning !== "none") {
    warnings.push(mockWarning);
  }
  const credentials = providerCredentialsStatus(context);
  if (credentials.includes("unknown") || credentials.includes("missing")) {
    warnings.push("credentials unknown/missing");
  }
  const latestError = traceLatestSafeError(context);
  if (latestError && latestError !== "none") {
    warnings.push(`latest safe error: ${latestError}`);
  }
  const stuck = queueStuckWarning(context);
  if (stuck) {
    warnings.push(stuck);
  }
  for (const failure of context.failures) {
    warnings.push(`source unavailable: ${overviewValue(failure)}`);
  }
  return uniqueNonEmpty(warnings);
}

function operatorDegradingWarnings(context) {
  return operatorTopWarnings(context).filter((warning) =>
    /latest safe error|queue stuck|source unavailable|compat:|invalid|failed|unavailable/i.test(warning),
  );
}

function operatorStatusLine(status, blockers, warnings, context) {
  if (status === "offline") {
    return `Offline: ${blockers[0] || "backend offline"}`;
  }
  if (status === "unknown") {
    return "Unknown: runtime projection missing";
  }
  if (status === "blocked") {
    return `Blocked: ${blockers.slice(0, 2).join(", ")}`;
  }
  if (status === "degraded") {
    return `Degraded: ${warnings.slice(0, 2).join(", ") || "warnings remain"}`;
  }
  return `Ready enough: ${operatorReadySignals(context).slice(0, 3).join(", ")}${warnings.length > 0 ? ", warnings remain" : ""}`;
}

function operatorReadySignals(context) {
  const signals = [];
  if (operatorBackendConnected(context)) signals.push("status available");
  if (operatorRuntimeProjectionLoaded(context)) signals.push("runtime projection loaded");
  if (traceValue(context, "turn_id")) signals.push("latest turn trace visible");
  if (runtimeOverviewSourceAvailable(context, "voiceQueue")) signals.push("voice queue visible");
  if (runtimeOverviewSourceAvailable(context, "events")) signals.push("safe events visible");
  return signals.length > 0 ? signals : ["read-only cockpit loaded"];
}

function operatorNextAction(status, blockers, warnings) {
  if (status === "offline") {
    return "Start or inspect the daemon with scripts/jarvis status, then refresh.";
  }
  if (status === "unknown") {
    return "Refresh Mission Control after /runtime/settings is available.";
  }
  if (status === "blocked") {
    return `Fix first blocker: ${blockers[0] || "runtime blocker"}.`;
  }
  if (status === "degraded") {
    return `Test next safe path, then inspect warning: ${warnings[0] || "latest warning"}.`;
  }
  return "Test next: send text turn, hold PTT manually, approve memory, verify latest trace.";
}

function lastImportantEventSummary(context) {
  const parts = [
    importantEventPart(context.events, "error", (event) => eventMatchesIssue(event, ["runtime", "turn", "voice", "brain", "provider", "approval", "memory", "tool"])),
    importantEventPart(context.events, "voice", (event) => eventFamily(event && event.type) === "voice"),
    importantEventPart(context.events, "approval", (event) => eventFamily(event && event.type) === "approval"),
    importantEventPart(context.events, "turn", (event) => eventFamily(event && event.type) === "turn"),
  ].filter(Boolean);
  return parts.join(" | ");
}

function importantEventPart(events, label, predicate) {
  const event = newestFirstEvents(events).find(predicate);
  if (!event) {
    return "";
  }
  const item = safeEventTimelineItem(event);
  return `${label}: ${item.type}${item.summary ? ` - ${item.summary}` : ""}`;
}

function moduleStatusFromReadiness(readiness) {
  const normalized = normalizeRuntimeReadiness(readiness);
  if (normalized === RUNTIME_OVERVIEW_READINESS.OK) return "ready";
  if (
    normalized === RUNTIME_OVERVIEW_READINESS.INVALID ||
    normalized === RUNTIME_OVERVIEW_READINESS.MISSING ||
    normalized === RUNTIME_OVERVIEW_READINESS.UNSUPPORTED
  ) return "blocked";
  return "degraded";
}

function moduleStatusFromSources(context, sources) {
  return sources.every((source) => runtimeOverviewSourceAvailable(context, source))
    ? "ready"
    : "degraded";
}

function runtimeDirLogSummary(context) {
  return firstPresent(
    readinessValue(context, "runtime_dir"),
    readinessValue(context, "log_dir"),
    readinessValue(context, "database_path"),
    configuredSetting(context, ["runtime.dir", "runtime_dir", "logs.dir", "log_dir"]),
    RUNTIME_OVERVIEW_NOT_EXPOSED,
  );
}

function sourceStatusSummary(context, keys) {
  return keys
    .map((key) => `${key}: ${runtimeOverviewSourceAvailable(context, key) ? "ok" : "missing"}`)
    .join(", ");
}

function scriptsJarvisStatusHint(context) {
  const observations = Array.isArray(safeObject(context.runtimeProcesses).observations)
    ? context.runtimeProcesses.observations
    : [];
  if (observations.length > 0) {
    return `${observations.length} runtime observations loaded`;
  }
  return "not exposed; use scripts/jarvis status manually";
}

function providerMockDevWarning(context) {
  const provider = safeObject(currentProviderCapability(context));
  if (provider.name === "mock" || provider.kind === "Developer/Test") {
    return "mock/dev selected";
  }
  return "none";
}

function missionPttSummary(context) {
  return firstPresent(
    voiceRuntimeConfiguredValue(context, "endpointing_vad_ptt", ["ptt_mode", "ptt_source", "ptt_hotkey"]),
    configuredSetting(context, ["voice.ptt_mode", "voice.ptt_hotkey", "ptt.mode"]),
  );
}

function memoryEnabledSummary(context) {
  const value = configuredSetting(context, ["memory.enabled", "memory_enabled"]);
  if (value === false || String(value).toLowerCase() === "false") {
    return "disabled";
  }
  if (value === true || String(value).toLowerCase() === "true") {
    return "enabled";
  }
  return context.memoryItems.length > 0 || context.memoryBlocks.length > 0 ? "visible" : "unknown";
}

function latestMemoryStatus(context) {
  const event = latestEvent(context.events, ["memory.updated", "memory.candidate.created", "memory.candidate.promoted", "memory.disabled"]);
  if (event) {
    return eventPayloadSummary(event.payload || {}) || eventLabel(event.type);
  }
  if (context.memoryItems.length > 0) {
    return `${context.memoryItems.length} Memory OS items visible`;
  }
  if (context.memoryBlocks.length > 0) {
    return `${context.memoryBlocks.length} legacy blocks visible`;
  }
  return "none/unknown";
}

function pendingApprovalSummary(context) {
  const rows = context.approvals;
  const pending = rows.filter((item) => String(item.status || "pending") === "pending").length;
  return `${pending} pending · ${rows.length} loaded`;
}

function approvalBlocker(context) {
  if (!runtimeOverviewSourceAvailable(context, "approvals")) {
    return "approval source unavailable";
  }
  return "none";
}

function latestTurnBrief(context) {
  const turn = traceValue(context, "turn_id");
  if (!turn) {
    return "unknown";
  }
  return `${shortId(turn)} · ${overviewValue(traceValue(context, "source"))}`;
}

function latestEventTime(events) {
  const event = newestFirstEvents(events)[0];
  return event && event.created_at ? formatRelative(event.created_at) : RUNTIME_OVERVIEW_UNKNOWN;
}

function pttChecklistStatus(context) {
  if (!operatorBackendConnected(context)) {
    return "fail";
  }
  const readiness = voiceRuntimeGroupReadiness(context, "endpointing_vad_ptt");
  if (readiness === RUNTIME_OVERVIEW_READINESS.OK && firstPresent(missionPttSummary(context))) {
    return "pass";
  }
  return readiness === RUNTIME_OVERVIEW_READINESS.MISSING ? "fail" : "unknown";
}

function pttChecklistWhy(context) {
  const value = missionPttSummary(context);
  return firstPresent(value)
    ? `PTT state/config visible: ${overviewValue(value)}`
    : "hotkey/PTT state missing";
}

function bargeInChecklistStatus(context) {
  const readiness = voiceRuntimeGroupReadiness(context, "queue_barge_in");
  if (readiness === RUNTIME_OVERVIEW_READINESS.OK) {
    return "pass";
  }
  if (readiness === RUNTIME_OVERVIEW_READINESS.MISSING) {
    return "fail";
  }
  return "unknown";
}

function bargeInChecklistWhy(context) {
  return firstPresent(
    voiceRuntimeGroupDependency(context, "queue_barge_in"),
    latestBargeInSummary(context.events),
    "cancel state not observed yet",
  );
}

function currentSpeakingItem(context) {
  const row = context.queueRows.find((item) => String(item.status || "").toLowerCase() === "speaking");
  if (!row) {
    return "none";
  }
  return `${shortId(row.id)} · ${overviewValue(row.kind)} · ${overviewValue(row.status)}`;
}

function queueStuckWarning(context) {
  const now = Date.now();
  for (const row of context.queueRows) {
    const status = String(row && row.status || "").toLowerCase();
    if (!["queued", "speaking", "started"].includes(status)) {
      continue;
    }
    const timestamp = Date.parse(row.spoken_at || row.created_at || "");
    if (Number.isFinite(timestamp) && now - timestamp > 10 * 60 * 1000) {
      return `queue stuck: ${shortId(row.id)} ${status}`;
    }
  }
  return "";
}

function localRuntimeStatus(context) {
  const graph = safeObject(safeObject(context.runtimeSettings).capability_graph);
  const runtimes = safeObject(safeObject(graph).local_capabilities).runtimes;
  if (!Array.isArray(runtimes) || runtimes.length === 0) {
    return "absent/unknown";
  }
  return runtimes
    .map((runtime) => `${overviewValue(runtime.id || runtime.name)}: ${overviewValue(runtime.status || runtime.available)}`)
    .join(", ");
}

function uniqueNonEmpty(values) {
  return [...new Set(values.map(overviewValue).filter((value) => value && value !== RUNTIME_OVERVIEW_UNKNOWN && value !== "none"))];
}

const RUNTIME_OVERVIEW_SECTIONS = [
  {
    title: "Runtime",
    fields: [
      field("overview mode", "contract", (ctx) => ctx.readOnlyMode),
      field("service", "health", (ctx) => ctx.health.service),
      field("state", "state", (ctx) => firstPresent(ctx.state.state, ctx.health.state)),
      field("started", "health", (ctx) => ctx.health.started),
      field("schema version", "health", (ctx) => ctx.health.schema_version),
      field("pending approval count", "state", (ctx) =>
        firstPresent(ctx.state.pending_approval_count, ctx.health.pending_approval_count),
      ),
      field("voice enabled", "state", (ctx) => ctx.voiceEnabled),
    ],
  },
  {
    title: "Turn State",
    fields: [
      field("current_turn_id", "runtimeSettings", (ctx) => turnStateValue(ctx, "current_turn_id"), {
        readiness: (ctx) => turnStateReadiness(ctx, "current_turn_id"),
        warnings: (ctx) => turnStateWarnings(ctx, ["current_turn_id"]),
      }),
      field("current_conversation_id", "runtimeSettings", (ctx) =>
        turnStateValue(ctx, "current_conversation_id"),
        {
          readiness: (ctx) => turnStateReadiness(ctx, "current_conversation_id"),
          warnings: (ctx) => turnStateWarnings(ctx, ["current_conversation_id"]),
        },
      ),
      field("current_turn_source", "runtimeSettings", (ctx) => turnStateValue(ctx, "current_turn_source"), {
        readiness: (ctx) => turnStateReadiness(ctx, "current_turn_source"),
      }),
      field("generation_state", "runtimeSettings", (ctx) => turnStateValue(ctx, "generation_state"), {
        readiness: (ctx) => turnStateReadiness(ctx, "generation_state"),
      }),
      field("current_speech_id", "runtimeSettings", (ctx) => turnStateValue(ctx, "current_speech_id"), {
        readiness: (ctx) => turnStateReadiness(ctx, "current_speech_id"),
      }),
      field("interrupted_previous_response", "runtimeSettings", (ctx) =>
        turnStateValue(ctx, "interrupted_previous_response"),
        {
          readiness: (ctx) => turnStateReadiness(ctx, "interrupted_previous_response"),
        },
      ),
      field("interrupted_turn_id", "runtimeSettings", (ctx) => turnStateValue(ctx, "interrupted_turn_id"), {
        readiness: (ctx) => turnStateReadiness(ctx, "interrupted_turn_id"),
      }),
      field("interruption_reason", "runtimeSettings", (ctx) => turnStateValue(ctx, "interruption_reason"), {
        readiness: (ctx) => turnStateReadiness(ctx, "interruption_reason"),
      }),
      field("cancelled_speech_id", "runtimeSettings", (ctx) => turnStateValue(ctx, "cancelled_speech_id"), {
        readiness: (ctx) => turnStateReadiness(ctx, "cancelled_speech_id"),
      }),
    ],
  },
  {
    title: "Readiness / Blockers",
    fields: [
      field("OK / Missing / Invalid / Unknown / Warning", "runtimeSettings", (ctx) =>
        readinessSummary(ctx),
        {
          readiness: (ctx) => readinessStatus(ctx, "summary"),
          warnings: (ctx) => readinessWarnings(ctx, ["summary", "warnings", "top_blockers"]),
        },
      ),
      field("top blockers", "runtimeSettings", (ctx) => readinessValue(ctx, "top_blockers"), {
        readiness: (ctx) => readinessStatus(ctx, "top_blockers"),
        warnings: (ctx) => readinessWarnings(ctx, ["top_blockers"]),
      }),
      field("warnings", "runtimeSettings", (ctx) => readinessValue(ctx, "warnings"), {
        readiness: (ctx) => readinessStatus(ctx, "warnings"),
      }),
      field("daemon config", "runtimeSettings", (ctx) => readinessValue(ctx, "daemon_config"), {
        readiness: (ctx) => readinessStatus(ctx, "daemon_config"),
        warnings: (ctx) => readinessWarnings(ctx, ["daemon_config"]),
      }),
      field("database path", "runtimeSettings", (ctx) => readinessValue(ctx, "database_path"), {
        readiness: (ctx) => readinessStatus(ctx, "database_path"),
        warnings: (ctx) => readinessWarnings(ctx, ["database_path"]),
      }),
      field("panel backend connected", "runtimeSettings", (ctx) =>
        readinessValue(ctx, "panel_backend_connected"),
        {
          readiness: (ctx) => readinessStatus(ctx, "panel_backend_connected"),
        },
      ),
      field("brain provider command", "runtimeSettings", (ctx) =>
        readinessValue(ctx, "brain_provider_command"),
        {
          readiness: (ctx) => readinessStatus(ctx, "brain_provider_command"),
          warnings: (ctx) => readinessWarnings(ctx, ["brain_provider_command"]),
        },
      ),
      field("TTS provider", "runtimeSettings", (ctx) => readinessValue(ctx, "tts_provider"), {
        readiness: (ctx) => readinessStatus(ctx, "tts_provider"),
        warnings: (ctx) => readinessWarnings(ctx, ["tts_provider"]),
      }),
      field("STT provider", "runtimeSettings", (ctx) => readinessValue(ctx, "stt_provider"), {
        readiness: (ctx) => readinessStatus(ctx, "stt_provider"),
        warnings: (ctx) => readinessWarnings(ctx, ["stt_provider"]),
      }),
      field("recorder/playback command", "runtimeSettings", (ctx) =>
        `${overviewValue(readinessValue(ctx, "recorder_command"))} / ${overviewValue(readinessValue(ctx, "playback_command"))}`,
        {
          readiness: (ctx) =>
            runtimeReadinessCompare(
              readinessStatus(ctx, "recorder_command"),
              readinessStatus(ctx, "playback_command"),
            ) <= 0
              ? readinessStatus(ctx, "recorder_command")
              : readinessStatus(ctx, "playback_command"),
          warnings: (ctx) => readinessWarnings(ctx, ["recorder_command", "playback_command"]),
        },
      ),
      field("network/tools capability", "runtimeSettings", (ctx) =>
        readinessValue(ctx, "network_tools_capability"),
        {
          readiness: (ctx) => readinessStatus(ctx, "network_tools_capability"),
          warnings: (ctx) => readinessWarnings(ctx, ["network_tools_capability"]),
        },
      ),
    ],
  },
  {
    title: "Brain/Provider",
    fields: [
      field("active provider/adapter", "runtimeSettings", (ctx) =>
        firstPresent(projectionValue(ctx.brainRuntime.current_adapter), ctx.activeAdapter),
        {
          readiness: (ctx, value) => providerCapabilityReadiness(ctx, value),
          dependency: (ctx, value) => providerCapabilityDependency(ctx, value),
          warnings: (ctx) => providerCompatibilityWarnings(ctx),
        },
      ),
      field("active model", "runtimeSettings", (ctx) =>
        firstPresent(providerCurrentModel(ctx), configuredBrainModel(ctx), adapterModels(ctx.adapters, ctx.activeAdapter)),
        {
          readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "current_model"),
          warnings: (ctx) => providerCompatibilityWarnings(ctx),
        },
      ),
      field("configured provider list", "runtimeSettings", (ctx) => providerCapabilityList(ctx), {
        readiness: (ctx) =>
          normalProviderCapabilities(ctx).length > 0
            ? RUNTIME_OVERVIEW_READINESS.OK
            : RUNTIME_OVERVIEW_READINESS.UNKNOWN,
      }),
      field("provider availability/configured status", "runtimeSettings", (ctx) => providerAvailabilitySummary(ctx), {
        readiness: (ctx) => providerCapabilityReadiness(ctx, ctx.activeAdapter),
        warnings: (ctx) => providerCompatibilityWarnings(ctx),
      }),
      field("command status", "runtimeSettings", (ctx) => providerCommandStatus(ctx), {
        readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "provider_command_status"),
      }),
      field("credentials status", "runtimeSettings", (ctx) => providerCredentialsStatus(ctx), {
        readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "provider_credentials_status"),
      }),
      field("context budget/window", "runtimeSettings", (ctx) =>
        firstPresent(
          projectionValue(safeObject(currentProviderCapability(ctx)).context_window_chars),
          configuredSetting(ctx, [
            "brain.context_budget_chars",
            "brain.context_budget",
            "brain.context_window",
            "context.budget",
            "context.window",
            "memory.context_budget",
          ]),
        ),
      ),
      field("streaming support", "runtimeSettings", (ctx) => providerSupportValue(ctx, "streaming_support")),
      field("tools support", "runtimeSettings", (ctx) => providerSupportValue(ctx, "tools_support")),
      field("effort allowed values", "runtimeSettings", (ctx) => providerAllowedEffort(ctx), {
        readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "allowed_effort_values"),
        warnings: (ctx) => providerCompatibilityWarnings(ctx),
      }),
      field("effort current/status", "runtimeSettings", (ctx) => providerEffortStatus(ctx), {
        readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "effort"),
        warnings: (ctx) => providerCompatibilityWarnings(ctx),
      }),
      field("fast support", "runtimeSettings", (ctx) => providerFastSupport(ctx), {
        readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "fast_supported"),
        warnings: (ctx) => providerCompatibilityWarnings(ctx),
      }),
      field("latest provider used by last turn", "runtimeSettings", (ctx) => latestTurnProviderSummary(ctx)),
      field("latest provider error", "runtimeSettings", (ctx) => providerLatestError(ctx), {
        readiness: (ctx) => providerProjectionReadiness(currentProviderCapability(ctx), "latest_error"),
      }),
      field("provider sessions are memory", "contract", () => "no; daemon owns memory"),
      field("Claude config", "brain", (ctx) =>
        adapterRegistration(ctx.adapters, ["claude_cli", "claude_cli_warm"]),
      ),
      field("Codex config", "brain", (ctx) => adapterRegistration(ctx.adapters, ["codex_cli"])),
    ],
  },
  {
    title: "Latest turn trace",
    fields: [
      field("turn_id", "runtimeSettings", (ctx) => traceValue(ctx, "turn_id"), {
        readiness: (ctx) => traceReadiness(ctx, "turn_id"),
        warnings: (ctx) => traceWarnings(ctx, ["turn_id"]),
      }),
      field("conversation_id", "runtimeSettings", (ctx) => traceValue(ctx, "conversation_id"), {
        readiness: (ctx) => traceReadiness(ctx, "conversation_id"),
      }),
      field("source", "runtimeSettings", (ctx) => traceValue(ctx, "source"), {
        readiness: (ctx) => traceReadiness(ctx, "source"),
      }),
      field("provider/adapter/model used", "runtimeSettings", (ctx) =>
        traceProviderModelSummary(ctx),
      ),
      field("effort/fast", "runtimeSettings", (ctx) =>
        `${overviewValue(traceValue(ctx, "effort"))} / ${overviewValue(traceValue(ctx, "fast"))}`,
      ),
      field("memory included count", "runtimeSettings", (ctx) => traceValue(ctx, "memory_included_count"), {
        readiness: (ctx) => traceReadiness(ctx, "memory_included_count"),
        warnings: (ctx) => traceWarnings(ctx, ["memory_included_count"]),
      }),
      field("memory excluded count", "runtimeSettings", (ctx) => traceValue(ctx, "memory_excluded_count"), {
        readiness: (ctx) => traceReadiness(ctx, "memory_excluded_count"),
        warnings: (ctx) => traceWarnings(ctx, ["memory_excluded_count"]),
      }),
      field("approvals requested/executed count", "runtimeSettings", (ctx) =>
        `${overviewValue(traceValue(ctx, "approvals_requested_count"))} / ${overviewValue(traceValue(ctx, "approvals_executed_count"))}`,
      ),
      field("tools attempted count", "runtimeSettings", (ctx) => traceValue(ctx, "tools_attempted_count")),
      field("voice rows created filler/final/error", "runtimeSettings", (ctx) => traceVoiceRowsCreated(ctx)),
      field("speech cancellation/interruption reason", "runtimeSettings", (ctx) =>
        traceCancellationSummary(ctx),
      ),
      field("latest safe error", "runtimeSettings", (ctx) => traceLatestSafeError(ctx), {
        readiness: (ctx, value) =>
          value === "none" ? RUNTIME_OVERVIEW_READINESS.OK : traceReadiness(ctx, "latest_safe_error"),
      }),
    ],
  },
  {
    title: "Debug timeline",
    fields: [
      field("user input received", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["user_input_received", "input_received_at", "created_at"]),
      ),
      field("STT done", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["stt_done_at", "transcription_done_at"]),
      ),
      field("generation started", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["generation_started_at", "started_at"]),
      ),
      field("generation done", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["generation_done_at", "completion_at", "updated_at"]),
      ),
      field("TTS queued", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["tts_queued_at", "speech_queued_at"]),
      ),
      field("playback started", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["playback_started_at", "spoken_at"]),
      ),
      field("playback finished", "runtimeSettings", (ctx) =>
        traceTimestamp(ctx, ["playback_finished_at", "speech_done_at"]),
      ),
      field("newest-first safe events", "events", (ctx) => debugTimelineSummary(ctx.events), {
        readiness: (ctx) =>
          Array.isArray(ctx.events) && ctx.events.length > 0
            ? RUNTIME_OVERVIEW_READINESS.OK
            : RUNTIME_OVERVIEW_READINESS.UNKNOWN,
      }),
    ],
  },
  {
    title: "Voice Settings: Capture/Input",
    fields: [
      field("backend projection", "voiceRuntime", (ctx) => voiceRuntimeProjectionSummary(ctx), {
        readiness: (ctx) => voiceRuntimeOverallReadiness(ctx),
        dependency: (ctx) => voiceRuntimeCannotProbeSummary(ctx),
        warnings: (ctx) => voiceRuntimeWarnings(ctx),
      }),
      field("input policy", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "capture_input", ["input_policy"]),
          ctx.audio.input_policy,
          configuredSetting(ctx, ["voice.input_policy", "audio.input_policy"]),
        ),
        {
          readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "capture_input"),
          dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "capture_input"),
          warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "capture_input"),
        },
      ),
      field("preferred input", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "capture_input", ["preferred_input"]),
          voiceRuntimeEffectiveValue(ctx, "capture_input", ["input_device", "input_transport"]),
          ctx.audio.preferred_input,
          ctx.audio.input_device,
        ),
      ),
      field("bluetooth mic allowed", "audio", (ctx) => ctx.audio.allow_bluetooth_microphone),
      field("recorder backend/command", "voiceRuntime", (ctx) => recorderEngine(ctx), {
        readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "capture_input"),
        dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "capture_input"),
        warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "capture_input"),
      }),
      field("mic permission/status", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeEffectiveValue(ctx, "capture_input", [
            "microphone_permission",
            "mic_permission",
            "permission",
          ]),
          ctx.audio.microphone_permission,
          ctx.audio.mic_permission,
          RUNTIME_OVERVIEW_NOT_EXPOSED,
        ),
        {
          readiness: () => RUNTIME_OVERVIEW_READINESS.UNKNOWN,
          dependency: () => "reported only if existing safe audio state exposes it",
        },
      ),
      field("active capture/listening", "voice", (ctx) =>
        firstPresent(
          voiceRuntimeEffectiveValue(ctx, "capture_input", ["listening"]),
          ctx.voice.listening,
        ),
      ),
    ],
  },
  {
    title: "Voice Settings: STT/Transcription",
    fields: [
      field("STT provider", "voiceRuntime", (ctx) => firstPresent(configuredStt(ctx), effectiveStt(ctx)), {
        readiness: (ctx, value) =>
          voiceRuntimeGroupReadiness(ctx, "stt_transcription", voiceConfiguredReadiness(ctx, value)),
        dependency: (ctx, value) =>
          voiceRuntimeGroupDependency(ctx, "stt_transcription", configuredRuntimeDependency(ctx, value)),
        warnings: (ctx, value) =>
          voiceRuntimeGroupWarnings(ctx, "stt_transcription", voiceConfiguredWarnings("STT")(ctx, value)),
      }),
      field("STT model/path", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "stt_transcription", ["model", "model_path", "path"]),
          configuredSetting(ctx, ["voice.stt_model", "stt.model", "stt.path"]),
        ),
      ),
      field("STT language", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "stt_transcription", ["language"]),
          configuredSetting(ctx, ["voice.stt_language", "stt.language"]),
        ),
      ),
      field("STT readiness", "voiceRuntime", (ctx) => voiceRuntimeGroupReadiness(ctx, "stt_transcription"), {
        readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "stt_transcription"),
        dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "stt_transcription"),
      }),
      field("latest STT error", "voiceRuntime", (ctx) =>
        latestVoiceLayerError(ctx, "stt_transcription", ["stt", "transcription"]),
        {
          readiness: (ctx, value) =>
            value === "none in recent events" ? RUNTIME_OVERVIEW_READINESS.OK : RUNTIME_OVERVIEW_READINESS.INVALID,
          warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "stt_transcription"),
        },
      ),
    ],
  },
  {
    title: "Voice Settings: Endpointing/VAD/PTT",
    fields: [
      field("PTT mode", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "endpointing_vad_ptt", ["ptt_mode"]),
          configuredSetting(ctx, ["voice.ptt_mode", "ptt.mode"]),
        ),
        {
          readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "endpointing_vad_ptt"),
          dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "endpointing_vad_ptt"),
          warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "endpointing_vad_ptt"),
        },
      ),
      field("hotkey", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "endpointing_vad_ptt", ["ptt_hotkey"]),
          configuredSetting(ctx, ["voice.ptt_hotkey", "voice.ptt.hotkey", "ptt.hotkey"]),
        ),
      ),
      field("merge window", "settings", (ctx) =>
        configuredSetting(ctx, [
          "voice.ptt_merge_window_ms",
          "voice.merge_window_ms",
          "ptt.merge_window_ms",
          "endpointing.merge_window_ms",
        ]),
      ),
      field("silence threshold/duration", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "endpointing_vad_ptt", [
            "stt_min_rms",
            "stt_min_voiced_seconds",
            "stt_min_voiced_ratio",
          ]),
          configuredSetting(ctx, [
            "voice.stt_min_rms",
            "voice.stt_min_voiced_seconds",
            "voice.stt_min_voiced_ratio",
            "vad.silence_threshold",
            "vad.silence_duration_ms",
          ]),
        ),
      ),
      field("interrupt policy", "voiceRuntime", (ctx) =>
        firstPresent(
          configuredSetting(ctx, ["voice.interrupt_policy", "voice.barge_in_policy"]),
          latestQueueValue(ctx.queueRows, ["interrupt_policy"]),
          voiceRuntimeEffectiveValue(ctx, "queue_barge_in", ["cancellation_reason"]),
          RUNTIME_OVERVIEW_UNKNOWN,
        ),
      ),
      field("listen lease state", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeEffectiveValue(ctx, "endpointing_vad_ptt", [
            "active_leases",
            "lease_modes",
            "lease_sources",
          ]),
          ctx.voice.listening,
        ),
      ),
    ],
  },
  {
    title: "Voice Settings: TTS/Voice Model",
    fields: [
      field("TTS provider", "voiceRuntime", (ctx) => firstPresent(configuredTts(ctx), effectiveTts(ctx)), {
        readiness: (ctx, value) =>
          voiceRuntimeGroupReadiness(ctx, "tts_voice_model", voiceConfiguredReadiness(ctx, value)),
        dependency: (ctx, value) =>
          voiceRuntimeGroupDependency(ctx, "tts_voice_model", configuredRuntimeDependency(ctx, value)),
        warnings: (ctx, value) =>
          voiceRuntimeGroupWarnings(ctx, "tts_voice_model", voiceConfiguredWarnings("TTS")(ctx, value)),
      }),
      field("TTS model_id", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "tts_voice_model", ["model_id", "voice_model"]),
          configuredSetting(ctx, ["voice.tts_model_id", "tts.model_id"]),
        ),
      ),
      field("voice id/profile/model", "voiceRuntime", (ctx) => configuredVoiceIdentity(ctx), {
        readiness: (ctx, value) =>
          voiceRuntimeGroupReadiness(ctx, "tts_voice_model", voiceIdentityReadiness(ctx, value)),
        dependency: (ctx, value) =>
          voiceRuntimeGroupDependency(ctx, "tts_voice_model", configuredRuntimeDependency(ctx, value)),
        warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "tts_voice_model"),
      }),
      field("speed/rate/tempo", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "tts_voice_model", ["speed", "rate", "tempo"]),
          configuredSetting(ctx, [
            "voice.supertonic_speed",
            "voice.speed",
            "voice.rate",
            "tts.speed",
            "tts.rate",
          ]),
        ),
      ),
      field("provider-specific voice settings", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "tts_voice_model", ["language", "steps", "speak_responses"]),
          configuredSetting(ctx, ["voice.supertonic_steps", "voice.supertonic_lang"]),
        ),
      ),
      field("latest TTS error", "voiceRuntime", (ctx) =>
        latestVoiceLayerError(ctx, "tts_voice_model", ["tts", "speech", "voice.speak"]),
        {
          readiness: (ctx, value) =>
            value === "none in recent events" ? RUNTIME_OVERVIEW_READINESS.OK : RUNTIME_OVERVIEW_READINESS.INVALID,
          warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "tts_voice_model"),
        },
      ),
    ],
  },
  {
    title: "Voice Settings: Playback",
    fields: [
      field("output policy", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeConfiguredValue(ctx, "playback", ["output_policy"]),
          ctx.audio.output_policy,
          configuredSetting(ctx, ["voice.output_policy", "audio.output_policy"]),
        ),
        {
          readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "playback"),
          dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "playback"),
          warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "playback"),
        },
      ),
      field("playback engine/command", "voiceRuntime", (ctx) => playbackEngine(ctx), {
        readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "playback"),
        dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "playback"),
        warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "playback"),
      }),
      field("active playback state", "voiceQueue", (ctx) =>
        firstPresent(
          latestQueueValue(ctx.queueRows, ["status"]),
          voiceRuntimeEffectiveValue(ctx, "playback", ["broker", "output_device"]),
          ctx.voice.listening === true ? "listening only" : undefined,
        ),
      ),
      field("latest playback error", "voiceRuntime", (ctx) =>
        latestVoiceLayerError(ctx, "playback", ["playback", "audio", "voice.speak"]),
        {
          readiness: (ctx, value) =>
            value === "none in recent events" ? RUNTIME_OVERVIEW_READINESS.OK : RUNTIME_OVERVIEW_READINESS.INVALID,
          warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "playback"),
        },
      ),
    ],
  },
  {
    title: "Voice Settings: Queue/Barge-in",
    fields: [
      field("queue counts", "voiceQueue", (ctx) =>
        firstPresent(
          voiceRuntimeEffectiveValue(ctx, "queue_barge_in", ["queue_counts"]),
          voiceQueueCounts(ctx.queueRows),
        ),
      ),
      field("speaking/final/filler", "voiceQueue", (ctx) => voiceQueueKindSummary(ctx.queueRows)),
      field("cancelled reason", "voiceRuntime", (ctx) =>
        firstPresent(
          voiceRuntimeEffectiveValue(ctx, "queue_barge_in", ["cancellation_reason"]),
          latestQueueValue(ctx.queueRows, ["cancellation_reason", "cancel_reason", "interruption_reason"]),
          latestBargeInSummary(ctx.events),
        ),
      ),
      field("interrupted previous response", "voiceQueue", (ctx) =>
        firstPresent(
          latestQueueValue(ctx.queueRows, ["interrupted_previous_response"]),
          latestEventPayloadValue(ctx.events, ["interrupted_previous_response"]),
          RUNTIME_OVERVIEW_UNKNOWN,
        ),
      ),
      field("voice queue", "voiceQueue", (ctx) => voiceQueueSummary(ctx.queueRows, ctx.voiceQueue), {
        readiness: (ctx) => voiceRuntimeGroupReadiness(ctx, "queue_barge_in"),
        dependency: (ctx) => voiceRuntimeGroupDependency(ctx, "queue_barge_in"),
        warnings: (ctx) => voiceRuntimeGroupWarnings(ctx, "queue_barge_in"),
      }),
    ],
  },
  {
    title: "Tools/Internet",
    fields: [
      field("registered tools", "tools", (ctx) =>
        ctx.tools.length > 0 ? `${ctx.tools.length} registered` : "none registered",
      ),
      field("visible risk classes", "tools", (ctx) => toolRiskSummary(ctx.tools)),
      field("approval-required tools", "tools", () => "policy not exposed; risk classes visible"),
      field("internet/network capability", "tools", (ctx) => networkToolSummary(ctx.tools), {
        readiness: (ctx) =>
          networkToolCandidates(ctx.tools).length > 0
            ? RUNTIME_OVERVIEW_READINESS.OK
            : networkPolicyRequiresTool(ctx)
              ? RUNTIME_OVERVIEW_READINESS.MISSING
              : RUNTIME_OVERVIEW_READINESS.UNKNOWN,
        warnings: (ctx) =>
          networkPolicyRequiresTool(ctx) && networkToolCandidates(ctx.tools).length === 0
            ? ["network/internet policy exists but no network tool exists"]
            : [],
      }),
      field("missing credentials/config status", "settings", () => RUNTIME_OVERVIEW_NOT_EXPOSED),
    ],
  },
  {
    title: "Logs/Trace",
    fields: [
      field("latest runtime error", "events", (ctx) =>
        latestEventIssue(ctx.events, ["runtime", "state", "daemon"]),
      ),
      field("latest voice error", "events", (ctx) =>
        latestEventIssue(ctx.events, ["voice", "audio", "speech", "stt", "tts", "listening"]),
      ),
      field("latest provider error", "events", (ctx) =>
        latestEventIssue(ctx.events, ["brain", "provider", "adapter"]),
      ),
      field("latest approval/tool error", "events", (ctx) =>
        latestEventIssue(ctx.events, ["approval", "tool"]),
      ),
      field("events window", "events", () => "latest 50 events"),
      field("last failure source", "events", (ctx) => runtimeOverviewSourceFailures(ctx.failures)),
      field("backend data gaps", "contract", (ctx) => backendDataGapsSummary(ctx)),
      field("warnings summary", "contract", (ctx) => runtimeOverviewWarningsSummary(ctx)),
      field("test/debug status", "events", () => RUNTIME_OVERVIEW_NOT_EXPOSED),
    ],
  },
  {
    title: "Developer/Test",
    fields: [
      field("active persona/profile", "settings", (ctx) => overviewPersona(ctx.settings)),
      field("runtime change without restart", "contract", () =>
        "persona.profile is read per turn when set",
      ),
      field("style config", "settings", (ctx) => configuredSetting(ctx, [
        "persona.style",
        "style.profile",
        "voice.style",
      ])),
      field("mock adapter/provider", "brain", (ctx) =>
        adapterRegistration(ctx.adapters, ["mock"], "Developer/Test registered"),
      ),
    ],
  },
];

function runtimeOverviewSections(snapshot) {
  const context = runtimeOverviewContext(snapshot);
  return RUNTIME_OVERVIEW_SECTIONS.map((section) => ({
    title: section.title,
    rows: runtimeOverviewFieldRows(section.fields, context),
  }));
}

function runtimeOverviewContext(snapshot) {
  const settings = overviewSettings(snapshot.settings);
  const runtimeSettings = safeObject(snapshot.runtimeSettings);
  const brainRuntime = safeObject(runtimeSettings.brain);
  const runtimeReadiness = safeObject(runtimeSettings.runtime_readiness);
  const currentTurnState = safeObject(runtimeSettings.current_turn_state);
  const latestTurnTrace = safeObject(runtimeSettings.latest_turn_trace);
  const brain = safeObject(snapshot.brain);
  const health = safeObject(snapshot.health);
  const state = safeObject(snapshot.state);
  const audio = safeObject(snapshot.audio).audio || {};
  const voice = safeObject(snapshot.voice);
  const voiceRuntime = safeObject(safeObject(snapshot.voiceRuntime).voice_runtime);
  const queueRows = Array.isArray(safeObject(snapshot.voiceQueue).voice_queue)
    ? snapshot.voiceQueue.voice_queue
    : [];
  const tools = Array.isArray(safeObject(snapshot.tools).tools) ? snapshot.tools.tools : [];
  const approvals = Array.isArray(safeObject(snapshot.approvals).approvals)
    ? snapshot.approvals.approvals
    : [];
  const memoryBlocks = Array.isArray(safeObject(snapshot.memory).memory)
    ? snapshot.memory.memory
    : [];
  const memoryItems = Array.isArray(safeObject(snapshot.memoryItems).items)
    ? snapshot.memoryItems.items
    : [];
  const events = Array.isArray(safeObject(snapshot.events).events) ? snapshot.events.events : [];
  const adapters = Array.isArray(brain.adapters) ? brain.adapters : [];
  const activeAdapter = firstPresent(brain.current, state.brain_adapter, health.brain_adapter);
  const failures = Array.isArray(snapshot.failures) ? snapshot.failures : [];
  const sourceStatus = safeObject(snapshot.sourceStatus);
  const voiceEnabled = firstPresent(
    voiceRuntime.voice_enabled,
    voice.voice_enabled,
    state.voice_enabled,
    health.voice_enabled,
  );

  return {
    readOnlyMode: RUNTIME_OVERVIEW_READ_ONLY,
    settings,
    runtimeSettings,
    brainRuntime,
    runtimeReadiness,
    currentTurnState,
    latestTurnTrace,
    brain,
    health,
    state,
    audio,
    voice,
    voiceRuntime,
    voiceQueue: safeObject(snapshot.voiceQueue),
    queueRows,
    tools,
    approvals,
    memoryBlocks,
    memoryItems,
    events,
    runtimeProcesses: safeObject(snapshot.runtimeProcesses),
    adapters,
    activeAdapter,
    failures,
    sourceStatus,
    voiceEnabled,
  };
}

function field(label, source, value, options = {}) {
  return { label, source, value, ...options };
}

function runtimeOverviewFieldRows(fields, context) {
  return fields.map((entry) => [entry.label, runtimeOverviewFieldSummary(entry, context)]);
}

function runtimeOverviewFieldSummary(fieldConfig, context) {
  const rawValue = fieldConfig.value(context);
  const value = runtimeOverviewDisplayValue(rawValue);
  const readiness = runtimeOverviewReadiness(fieldConfig, context, rawValue, value);
  const source = runtimeOverviewSourceLabel(fieldConfig.source);
  const parts = [value, `readiness: ${readiness}`, `source: ${source}`];
  const dependency =
    typeof fieldConfig.dependency === "function"
      ? runtimeOverviewDisplayValue(fieldConfig.dependency(context, rawValue))
      : "";
  if (dependency && dependency !== RUNTIME_OVERVIEW_UNKNOWN) {
    parts.push(`dependency: ${dependency}`);
  }
  const warnings = runtimeOverviewFieldWarnings(fieldConfig, context, rawValue);
  if (warnings.length > 0) {
    parts.push(`warnings: ${warnings.join("; ")}`);
  }
  return parts.join(" · ");
}

function runtimeOverviewReadiness(fieldConfig, context, rawValue, value) {
  if (!runtimeOverviewSourceAvailable(context, fieldConfig.source)) {
    return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
  }
  if (typeof fieldConfig.readiness === "function") {
    return normalizeRuntimeReadiness(fieldConfig.readiness(context, rawValue, value));
  }
  if (value === RUNTIME_OVERVIEW_NOT_EXPOSED || value === RUNTIME_OVERVIEW_UNKNOWN) {
    return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
  }
  if (value === "not registered" || value === "none registered" || value === "none") {
    return RUNTIME_OVERVIEW_READINESS.MISSING;
  }
  if (value === "object not displayed") {
    return RUNTIME_OVERVIEW_READINESS.INVALID;
  }
  return RUNTIME_OVERVIEW_READINESS.OK;
}

function normalizeRuntimeReadiness(value) {
  const normalized = String(value || "").toLowerCase();
  return RUNTIME_OVERVIEW_FIELD_STATUS_ORDER.includes(normalized)
    ? normalized
    : RUNTIME_OVERVIEW_READINESS.UNKNOWN;
}

function runtimeOverviewSourceLabel(source) {
  return RUNTIME_OVERVIEW_FIELD_SOURCES[source] || overviewValue(source);
}

function runtimeOverviewSourceAvailable(context, source) {
  if (source === "contract") {
    return true;
  }
  const status = safeObject(context.sourceStatus)[source];
  return !status || status.ok !== false;
}

function runtimeOverviewFieldWarnings(fieldConfig, context, rawValue) {
  if (!runtimeOverviewSourceAvailable(context, fieldConfig.source)) {
    const status = safeObject(context.sourceStatus)[fieldConfig.source];
    const path = status && status.path ? ` (${status.path})` : "";
    return [`${runtimeOverviewSourceLabel(fieldConfig.source)} source unavailable${path}`];
  }
  return typeof fieldConfig.warnings === "function"
    ? fieldConfig.warnings(context, rawValue).filter(Boolean)
    : [];
}

function runtimeOverviewDisplayValue(value) {
  if (typeof value === "boolean") {
    return yesNoUnknown(value);
  }
  return overviewValue(value);
}

function overviewSettings(payload) {
  const settings = safeObject(payload).settings;
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) {
    return {};
  }
  return settings;
}

function safeObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value;
}

function voiceRuntimeGroups(context) {
  return safeObject(safeObject(context.voiceRuntime).groups);
}

function voiceRuntimeGroup(context, key) {
  return safeObject(voiceRuntimeGroups(context)[key]);
}

function voiceRuntimeProjectionSummary(context) {
  const groups = voiceRuntimeGroups(context);
  const names = Object.keys(groups);
  if (names.length === 0) {
    return RUNTIME_OVERVIEW_NOT_EXPOSED;
  }
  return `${names.length} backend-owned groups · read-only`;
}

function voiceRuntimeOverallReadiness(context) {
  const groups = Object.values(voiceRuntimeGroups(context));
  if (groups.length === 0) {
    return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
  }
  return groups
    .map((group) => normalizeRuntimeReadiness(safeObject(group).readiness))
    .sort(runtimeReadinessCompare)[0];
}

function runtimeReadinessCompare(left, right) {
  return runtimeReadinessRank(left) - runtimeReadinessRank(right);
}

function runtimeReadinessRank(value) {
  if (value === RUNTIME_OVERVIEW_READINESS.INVALID) {
    return 0;
  }
  if (value === RUNTIME_OVERVIEW_READINESS.MISSING || value === RUNTIME_OVERVIEW_READINESS.UNSUPPORTED) {
    return 1;
  }
  if (value === RUNTIME_OVERVIEW_READINESS.UNKNOWN) {
    return 2;
  }
  return 3;
}

function voiceRuntimeCannotProbeSummary(context) {
  const cannotProbe = safeObject(context.voiceRuntime).cannot_probe_safely;
  if (!Array.isArray(cannotProbe) || cannotProbe.length === 0) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return cannotProbe.map(overviewValue).join("; ");
}

function voiceRuntimeWarnings(context) {
  const warnings = safeObject(context.voiceRuntime).warnings;
  return Array.isArray(warnings) ? warnings.map(overviewValue).filter(Boolean) : [];
}

function voiceRuntimeGroupReadiness(context, groupKey, fallback = RUNTIME_OVERVIEW_READINESS.UNKNOWN) {
  const readiness = voiceRuntimeGroup(context, groupKey).readiness;
  return readiness ? normalizeRuntimeReadiness(readiness) : normalizeRuntimeReadiness(fallback);
}

function voiceRuntimeGroupDependency(context, groupKey, fallback) {
  return firstPresent(voiceRuntimeGroup(context, groupKey).dependency_status, fallback);
}

function voiceRuntimeGroupWarnings(context, groupKey, fallback = []) {
  const groupWarnings = voiceRuntimeGroup(context, groupKey).warnings;
  const warnings = Array.isArray(fallback) ? [...fallback] : [];
  if (Array.isArray(groupWarnings)) {
    warnings.push(...groupWarnings.map(overviewValue));
  }
  return [...new Set(warnings.filter(Boolean))];
}

function voiceRuntimeConfiguredValue(context, groupKey, keys) {
  return voiceRuntimeValueAt(voiceRuntimeGroup(context, groupKey).configured, keys);
}

function voiceRuntimeEffectiveValue(context, groupKey, keys) {
  return voiceRuntimeValueAt(voiceRuntimeGroup(context, groupKey).effective, keys);
}

function voiceRuntimeValueAt(source, keys) {
  const object = safeObject(source);
  for (const key of keys) {
    const value = valueAtPath(object, key);
    if (value !== undefined && value !== null && value !== "") {
      return overviewScalarOrObjectSummary(value);
    }
  }
  return undefined;
}

function overviewScalarOrObjectSummary(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return value;
  }
  const entries = Object.entries(value)
    .filter(([, item]) => item !== undefined && item !== null && item !== "")
    .map(([key, item]) => `${key}: ${overviewValue(item)}`);
  return entries.length > 0 ? entries.join(", ") : undefined;
}

function overviewPersona(settings) {
  if (Object.prototype.hasOwnProperty.call(settings, "persona.profile")) {
    return overviewValue(settings["persona.profile"]);
  }
  return "default profile (persona.profile not set)";
}

function configuredSetting(context, keys) {
  return firstConfiguredValue(context.settings, keys);
}

function firstConfiguredValue(settings, keys) {
  for (const key of keys) {
    const value = valueAtPath(settings, key);
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }
  return undefined;
}

function valueAtPath(object, path) {
  const source = safeObject(object);
  if (Object.prototype.hasOwnProperty.call(source, path)) {
    return source[path];
  }
  return String(path)
    .split(".")
    .reduce((current, part) => {
      if (!current || typeof current !== "object" || Array.isArray(current)) {
        return undefined;
      }
      return current[part];
    }, source);
}

function configuredTts(context) {
  return firstPresent(
    voiceRuntimeConfiguredValue(context, "tts_voice_model", ["default_tts", "tts", "engine"]),
    configuredSetting(context, [
      "voice.default_tts",
      "voice.tts",
      "voice.tts.engine",
      "voice.tts_provider",
      "tts.engine",
      "tts.provider",
      "default_tts",
    ]),
  );
}

function configuredStt(context) {
  return firstPresent(
    voiceRuntimeConfiguredValue(context, "stt_transcription", ["default_stt", "stt", "engine"]),
    configuredSetting(context, [
      "voice.default_stt",
      "voice.stt",
      "voice.stt.engine",
      "voice.stt_provider",
      "stt.engine",
      "stt.provider",
      "default_stt",
    ]),
  );
}

function effectiveTts(context) {
  return firstPresent(
    voiceRuntimeEffectiveValue(context, "tts_voice_model", ["engine", "default_tts", "tts"]),
    latestEventPayloadValue(context.events, ["tts_engine", "tts_provider", "default_tts"]),
  );
}

function effectiveStt(context) {
  return firstPresent(
    voiceRuntimeEffectiveValue(context, "stt_transcription", ["engine", "default_stt", "stt"]),
    latestEventPayloadValue(context.events, ["stt_engine", "stt_provider", "default_stt"]),
  );
}

function configuredVoiceIdentity(context) {
  return firstPresent(
    voiceRuntimeConfiguredValue(context, "tts_voice_model", [
      "voice_id",
      "voice_model",
      "voice_profile",
    ]),
    configuredSetting(context, [
      "voice.supertonic_voice",
      "voice.voice_id",
      "voice.voice_model",
      "voice.voice_profile",
      "voice.model",
      "voice.profile",
      "tts.voice_id",
      "tts.model",
      "voice_id",
    ]),
  );
}

function playbackEngine(context) {
  return firstPresent(
    voiceRuntimeConfiguredValue(context, "playback", ["playback_binary", "engine"]),
    voiceRuntimeEffectiveValue(context, "playback", ["broker", "output_device"]),
    context.audio.playback_engine,
    context.audio.output_engine,
    configuredSetting(context, [
      "voice.playback_binary",
      "voice.playback_engine",
      "audio.playback_engine",
      "playback.command",
      "playback.engine",
    ]),
  );
}

function recorderEngine(context) {
  return firstPresent(
    voiceRuntimeConfiguredValue(context, "capture_input", ["recorder", "recorder_binary"]),
    voiceRuntimeEffectiveValue(context, "capture_input", ["recorder", "input_device"]),
    context.audio.recorder_engine,
    context.audio.input_engine,
    configuredSetting(context, [
      "voice.recorder",
      "voice.recorder_binary",
      "voice.recorder_engine",
      "audio.recorder_engine",
      "recorder.command",
      "recorder.engine",
    ]),
  );
}

function voiceConfiguredReadiness(context, value) {
  if (context.voiceEnabled === true && !firstPresent(value)) {
    return RUNTIME_OVERVIEW_READINESS.MISSING;
  }
  return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
}

function voiceEffectiveReadiness(context, value) {
  return firstPresent(value)
    ? RUNTIME_OVERVIEW_READINESS.OK
    : RUNTIME_OVERVIEW_READINESS.UNKNOWN;
}

function voiceConfiguredWarnings(label) {
  return (context, value) => {
    if (context.voiceEnabled === true && !firstPresent(value)) {
      return [`voice enabled but ${label} is not exposed/configured`];
    }
    return [];
  };
}

function voiceIdentityReadiness(context, value) {
  if (ttsRequiresVoiceIdentity(context) && !firstPresent(value)) {
    return RUNTIME_OVERVIEW_READINESS.MISSING;
  }
  return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
}

function configuredRuntimeDependency(context, value) {
  return firstPresent(value) ? "configured only; dependency not probed" : undefined;
}

function ttsRequiresVoiceIdentity(context) {
  const selected = String(firstPresent(configuredTts(context), effectiveTts(context)) || "").toLowerCase();
  return selected && selected !== "mock";
}

function backendDataGapsSummary(context) {
  const gaps = [];
  const voiceRuntimeAvailable = runtimeOverviewSourceAvailable(context, "voiceRuntime");
  if (!voiceRuntimeAvailable) {
    gaps.push("voice_runtime projection unavailable");
  }
  if (context.voiceEnabled === false) {
    return gaps.length > 0
      ? gaps.join("; ")
      : "voice runtime not required while voice disabled";
  }
  if (!runtimeOverviewSourceAvailable(context, "events")) {
    gaps.push("latest runtime events unavailable");
  } else if (!voiceRuntimeAvailable) {
    if (!firstPresent(effectiveTts(context))) {
      gaps.push("effective_tts not exposed by runtime");
    }
    if (!firstPresent(effectiveStt(context))) {
      gaps.push("effective_stt not exposed by runtime");
    }
  }
  if (!runtimeOverviewSourceAvailable(context, "settings")) {
    gaps.push("settings unavailable");
  } else {
    if (!firstPresent(configuredTts(context))) {
      gaps.push("configured_tts missing/not exposed");
    }
    if (!firstPresent(configuredStt(context))) {
      gaps.push("configured_stt missing/not exposed");
    }
    if (ttsRequiresVoiceIdentity(context) && !firstPresent(configuredVoiceIdentity(context))) {
      gaps.push("voice identity missing/not exposed");
    }
  }
  if (!firstPresent(playbackEngine(context))) {
    gaps.push("playback engine not exposed");
  }
  if (!firstPresent(recorderEngine(context))) {
    gaps.push("recorder engine not exposed");
  }
  return gaps.length > 0 ? gaps.join("; ") : "none";
}

function runtimeOverviewWarningsSummary(context) {
  const warnings = [];
  for (const failure of context.failures) {
    warnings.push(`source: unavailable ${overviewValue(failure)}`);
  }
  for (const warning of voiceRuntimeWarnings(context)) {
    warnings.push(`voice: ${warning}`);
  }
  if (context.voiceEnabled === true && !firstPresent(configuredTts(context))) {
    warnings.push("missing: voice enabled but configured TTS missing/not exposed");
  }
  if (context.voiceEnabled === true && !firstPresent(configuredStt(context))) {
    warnings.push("missing: voice enabled but configured STT missing/not exposed");
  }
  if (context.voiceEnabled === true && configuredTts(context) && !firstPresent(effectiveTts(context))) {
    warnings.push("unknown: effective TTS not reported by runtime");
  }
  if (context.voiceEnabled === true && configuredStt(context) && !firstPresent(effectiveStt(context))) {
    warnings.push("unknown: effective STT not reported by runtime");
  }
  if (ttsRequiresVoiceIdentity(context) && !firstPresent(configuredVoiceIdentity(context))) {
    warnings.push("missing: TTS requires voice identity/model but none is exposed");
  }
  if (networkPolicyRequiresTool(context) && networkToolCandidates(context.tools).length === 0) {
    warnings.push("compat: network/internet policy exists but no network tool exists");
  }
  warnings.push(...runtimeOverviewCompatibilityWarnings(context));
  return warnings.length > 0 ? [...new Set(warnings)].join("; ") : "none";
}

function overviewValue(value) {
  if (value === undefined || value === null || value === "") {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  if (Array.isArray(value)) {
    const shown = value.map(overviewValue).filter((item) => item !== RUNTIME_OVERVIEW_UNKNOWN);
    return shown.length > 0 ? shown.join(", ") : "none";
  }
  if (typeof value === "string") {
    return redactEventSummaryText(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "object not displayed";
}

function yesNoUnknown(value) {
  if (value === true) {
    return "yes";
  }
  if (value === false) {
    return "no";
  }
  return RUNTIME_OVERVIEW_UNKNOWN;
}

function firstPresent(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }
  return undefined;
}

function adapterNames(adapters) {
  if (!Array.isArray(adapters) || adapters.length === 0) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return adapters.map((adapter) => overviewValue(adapter.name)).join(", ");
}

function adapterRegistration(adapters, names, presentLabel = "registered") {
  const found = names.filter((name) => adapterByName(adapters, name));
  if (found.length === 0) {
    return "not registered";
  }
  return found.length === 1 ? presentLabel : `${presentLabel}: ${found.join(", ")}`;
}

function adapterModels(adapters, name) {
  const adapter = adapterByName(adapters, name);
  const models = adapter && Array.isArray(adapter.models) ? adapter.models : [];
  return models.length > 0 ? models.map(overviewValue).join(", ") : RUNTIME_OVERVIEW_UNKNOWN;
}

function adapterReadiness(adapters, name) {
  if (!name) {
    return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
  }
  return adapterByName(adapters, name)
    ? RUNTIME_OVERVIEW_READINESS.OK
    : RUNTIME_OVERVIEW_READINESS.MISSING;
}

function adapterDependency(adapters, name) {
  if (!name) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return adapterByName(adapters, name) ? "registered" : "not registered";
}

function adapterByName(adapters, name) {
  if (!name || !Array.isArray(adapters)) {
    return null;
  }
  return adapters.find((adapter) => adapter && adapter.name === name) || null;
}

function projectionValue(projection) {
  const object = safeObject(projection);
  if (Object.prototype.hasOwnProperty.call(object, "effective_value")) {
    return object.effective_value;
  }
  if (Object.prototype.hasOwnProperty.call(object, "value")) {
    return object.value;
  }
  return undefined;
}

function projectionStatus(projection) {
  return normalizeRuntimeReadiness(safeObject(projection).status);
}

function projectionWarning(projection) {
  return safeObject(projection).warning;
}

function projectionList(projection) {
  const value = projectionValue(projection);
  return Array.isArray(value) ? value : [];
}

function providerCapabilities(context) {
  return projectionList(safeObject(context.brainRuntime).providers);
}

function normalProviderCapabilities(context) {
  return providerCapabilities(context).filter((provider) => {
    const source = safeObject(provider);
    return source.name !== "mock" && source.kind !== "Developer/Test";
  });
}

function currentProviderCapability(context) {
  const providers = providerCapabilities(context);
  return (
    providers.find((provider) => safeObject(provider).current === true) ||
    providers.find((provider) => safeObject(provider).name === context.activeAdapter) ||
    null
  );
}

function configuredBrainModel(context) {
  return firstPresent(
    projectionValue(safeObject(context.brainRuntime).default_model),
    configuredSetting(context, [
      "brain.default_model",
      "brain.model",
      "provider.model",
      "model",
      "llm.model",
    ]),
  );
}

function providerCapabilityList(context) {
  const providers = normalProviderCapabilities(context);
  if (providers.length === 0) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return providers.map((provider) => overviewValue(safeObject(provider).name)).join(", ");
}

function providerAvailabilitySummary(context) {
  const providers = normalProviderCapabilities(context);
  if (providers.length === 0) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return providers
    .map((provider) => {
      const source = safeObject(provider);
      return `${overviewValue(source.name)}: status=${overviewValue(source.status)}, configured=${yesNoUnknown(source.configured)}, available=${yesNoUnknown(source.available)}`;
    })
    .join(" · ");
}

function providerCapabilityReadiness(context, adapterName) {
  const provider =
    currentProviderCapability(context) ||
    providerCapabilities(context).find((item) => safeObject(item).name === adapterName);
  if (!provider) {
    return adapterReadiness(context.adapters, adapterName);
  }
  const status = String(safeObject(provider).status || "").toLowerCase();
  if (status === "ok") {
    return RUNTIME_OVERVIEW_READINESS.OK;
  }
  if (status === "missing") {
    return RUNTIME_OVERVIEW_READINESS.MISSING;
  }
  if (status === "invalid") {
    return RUNTIME_OVERVIEW_READINESS.INVALID;
  }
  return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
}

function providerCapabilityDependency(context, adapterName) {
  const provider =
    currentProviderCapability(context) ||
    providerCapabilities(context).find((item) => safeObject(item).name === adapterName);
  if (!provider) {
    return adapterDependency(context.adapters, adapterName);
  }
  const source = safeObject(provider);
  return `configured=${yesNoUnknown(source.configured)}, available=${yesNoUnknown(source.available)}`;
}

function providerProjectionReadiness(provider, key) {
  if (!provider) {
    return RUNTIME_OVERVIEW_READINESS.UNKNOWN;
  }
  return projectionStatus(safeObject(provider)[key]);
}

function providerCurrentModel(context) {
  return projectionValue(safeObject(currentProviderCapability(context)).current_model);
}

function providerCommandStatus(context) {
  const provider = currentProviderCapability(context);
  const command = safeObject(provider).provider_command_status;
  const value = projectionValue(command);
  if (!firstPresent(value)) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return `${overviewValue(value)} (${projectionStatus(command)})`;
}

function providerCredentialsStatus(context) {
  const provider = currentProviderCapability(context);
  const credentials = safeObject(provider).provider_credentials_status;
  const value = projectionValue(credentials);
  if (!firstPresent(value)) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return `${overviewValue(value)} (${projectionStatus(credentials)})`;
}

function providerSupportValue(context, key) {
  const projection = safeObject(currentProviderCapability(context))[key];
  const value = projectionValue(projection);
  if (!firstPresent(value)) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return overviewValue(value);
}

function providerAllowedEffort(context) {
  const value = projectionValue(safeObject(currentProviderCapability(context)).allowed_effort_values);
  return Array.isArray(value) && value.length > 0 ? value.map(overviewValue).join(", ") : "none";
}

function providerEffortStatus(context) {
  const effort = safeObject(currentProviderCapability(context)).effort;
  const value = projectionValue(effort);
  return `${overviewValue(value)} (${projectionStatus(effort)})`;
}

function providerFastSupport(context) {
  const provider = safeObject(currentProviderCapability(context));
  const supported = projectionValue(provider.fast_supported);
  const current = projectionValue(provider.fast);
  if (supported === "no") {
    return "unsupported";
  }
  return `${overviewValue(supported)} · current=${overviewValue(current)}`;
}

function latestTurnProviderSummary(context) {
  const adapter = projectionValue(safeObject(context.latestTurnTrace).provider_adapter);
  const model = projectionValue(safeObject(context.latestTurnTrace).provider_model);
  if (!firstPresent(adapter, model)) {
    return RUNTIME_OVERVIEW_UNKNOWN;
  }
  return [adapter, model].filter((value) => firstPresent(value)).map(overviewValue).join(" / ");
}

function providerLatestError(context) {
  const provider = currentProviderCapability(context);
  const latestError = safeObject(provider).latest_error;
  return firstPresent(projectionValue(latestError), latestEventIssue(context.events, ["brain", "provider", "adapter"]));
}

function readinessProjection(context, key) {
  return safeObject(context.runtimeReadiness)[key];
}

function readinessValue(context, key) {
  return projectionValue(readinessProjection(context, key));
}

function readinessStatus(context, key) {
  return projectionStatus(readinessProjection(context, key));
}

function readinessWarnings(context, keys) {
  const warnings = [];
  for (const key of keys) {
    const projection = readinessProjection(context, key);
    const warning = projectionWarning(projection);
    if (warning) {
      warnings.push(overviewValue(warning));
    }
    const value = projectionValue(projection);
    if (Array.isArray(value) && (key === "warnings" || key === "top_blockers")) {
      warnings.push(...value.map(overviewValue));
    }
  }
  return [...new Set(warnings.filter(Boolean))];
}

function readinessSummary(context) {
  const summary = readinessValue(context, "summary");
  const object = safeObject(summary);
  const labels = ["OK", "Missing", "Invalid", "Unknown", "Warning"];
  const parts = labels.map((label) => `${label}: ${overviewValue(object[label])}`);
  return parts.join(", ");
}

function turnStateProjection(context, key) {
  return safeObject(context.currentTurnState)[key];
}

function turnStateValue(context, key) {
  return projectionValue(turnStateProjection(context, key));
}

function turnStateReadiness(context, key) {
  return projectionStatus(turnStateProjection(context, key));
}

function turnStateWarnings(context, keys) {
  return keys
    .map((key) => projectionWarning(turnStateProjection(context, key)))
    .filter(Boolean)
    .map(overviewValue);
}

function traceProjection(context, key) {
  return safeObject(context.latestTurnTrace)[key];
}

function traceValue(context, key) {
  return projectionValue(traceProjection(context, key));
}

function traceReadiness(context, key) {
  return projectionStatus(traceProjection(context, key));
}

function traceWarnings(context, keys) {
  return keys
    .map((key) => projectionWarning(traceProjection(context, key)))
    .filter(Boolean)
    .map(overviewValue);
}

function traceProviderModelSummary(context) {
  return [traceValue(context, "provider_adapter"), traceValue(context, "provider_model")]
    .filter((value) => firstPresent(value))
    .map(overviewValue)
    .join(" / ") || RUNTIME_OVERVIEW_UNKNOWN;
}

function traceVoiceRowsCreated(context) {
  const rows = traceValue(context, "voice_rows_created");
  return firstPresent(overviewScalarOrObjectSummary(rows), "filler: 0, final: 0, error: 0");
}

function traceCancellationSummary(context) {
  const parts = [
    ["reason", traceValue(context, "cancellation_reason")],
    ["interrupted", traceValue(context, "interrupted_previous_response")],
    ["cancelled_speech_id", traceValue(context, "cancelled_speech_id")],
    ["previous_turn_id", traceValue(context, "previous_turn_id")],
    ["new_turn_source", traceValue(context, "new_turn_source")],
  ]
    .filter(([, value]) => firstPresent(value))
    .map(([key, value]) => `${key}: ${overviewValue(value)}`);
  return parts.length > 0 ? parts.join(", ") : RUNTIME_OVERVIEW_UNKNOWN;
}

function traceTimestamps(context) {
  return safeObject(traceValue(context, "timestamps"));
}

function traceTimestamp(context, keys) {
  const timestamps = traceTimestamps(context);
  for (const key of keys) {
    const value = timestamps[key];
    if (firstPresent(value)) {
      return value;
    }
  }
  return RUNTIME_OVERVIEW_UNKNOWN;
}

function traceLatestSafeError(context) {
  return firstPresent(traceValue(context, "latest_safe_error"), "none");
}

function debugTimelineSummary(events) {
  const rows = newestFirstEvents(events);
  const safeRows = rows.slice(0, 8).map((event) => {
    const item = safeEventTimelineItem(event);
    const parts = [
      `#${overviewValue(event && event.id)}`,
      item.timestamp ? formatRelative(item.timestamp) : null,
      `family: ${item.family}`,
      item.type,
      item.status ? `status: ${item.status}` : null,
      item.severity && item.severity !== item.status ? `severity: ${item.severity}` : null,
      item.summary || null,
    ].filter(Boolean);
    return parts.join(" · ");
  });
  return safeRows.length > 0 ? safeRows.join(" | ") : "no recent safe events";
}

function providerCompatibilityWarnings(context) {
  const warnings = [];
  const provider = safeObject(currentProviderCapability(context));
  const name = String(provider.name || context.activeAdapter || "");
  const model = safeObject(provider.current_model);
  const effort = safeObject(provider.effort);
  const fast = safeObject(provider.fast);
  const fastSupported = safeObject(provider.fast_supported);

  if (projectionStatus(effort) === RUNTIME_OVERVIEW_READINESS.INVALID) {
    warnings.push("unsupported by current provider/model");
  }
  if (
    projectionValue(fastSupported) === "no" ||
    projectionStatus(fast) === RUNTIME_OVERVIEW_READINESS.INVALID
  ) {
    warnings.push("fast disabled/unsupported");
  }
  if (
    name === "local" &&
    [RUNTIME_OVERVIEW_READINESS.MISSING, RUNTIME_OVERVIEW_READINESS.INVALID].includes(
      projectionStatus(model),
    )
  ) {
    warnings.push("missing local model");
  }
  for (const projection of [model, effort, fast, fastSupported, safeObject(provider.latest_error)]) {
    const warning = projectionWarning(projection);
    if (warning) {
      warnings.push(overviewValue(warning));
    }
  }
  return [...new Set(warnings.filter(Boolean))];
}

function voiceQueueSummary(rows, payload) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return "empty";
  }
  const counts = {};
  for (const row of rows) {
    const status = overviewValue(row && row.status);
    counts[status] = (counts[status] || 0) + 1;
  }
  const statusSummary = Object.entries(counts)
    .map(([status, count]) => `${status}: ${count}`)
    .join(", ");
  const limit = safeObject(payload).limit;
  return `${rows.length}${limit ? ` of ${limit}` : ""} rows (${statusSummary})`;
}

function voiceQueueCounts(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return "empty";
  }
  const counts = {};
  for (const row of rows) {
    const kind = overviewValue(row && row.kind);
    const status = overviewValue(row && row.status);
    const key = kind === RUNTIME_OVERVIEW_UNKNOWN ? status : `${kind}/${status}`;
    counts[key] = (counts[key] || 0) + 1;
  }
  return Object.entries(counts)
    .map(([key, count]) => `${key}: ${count}`)
    .join(", ");
}

function voiceQueueKindSummary(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return "empty";
  }
  const counts = { speaking: 0, final: 0, filler: 0 };
  for (const row of rows) {
    const status = String((row && row.status) || "").toLowerCase();
    const kind = String((row && row.kind) || "").toLowerCase();
    if (status === "speaking") {
      counts.speaking += 1;
    }
    if (kind === "final") {
      counts.final += 1;
    }
    if (kind === "filler") {
      counts.filler += 1;
    }
  }
  return Object.entries(counts)
    .map(([key, count]) => `${key}: ${count}`)
    .join(", ");
}

function latestQueueValue(rows, keys) {
  if (!Array.isArray(rows)) {
    return undefined;
  }
  for (const row of rows) {
    for (const key of keys) {
      const value = row && row[key];
      if (value !== undefined && value !== null && value !== "") {
        return value;
      }
    }
  }
  return undefined;
}

function latestVoiceLayerError(context, groupKey, eventFamilies) {
  const groupError = voiceRuntimeGroup(context, groupKey).latest_safe_error;
  return firstPresent(groupError, latestEventIssue(context.events, eventFamilies));
}

function toolRiskSummary(tools) {
  if (!Array.isArray(tools) || tools.length === 0) {
    return "none";
  }
  const risks = [...new Set(tools.map((tool) => overviewValue(tool.risk)))].sort();
  return risks.join(", ");
}

function networkToolSummary(tools) {
  const networkTools = networkToolCandidates(tools);
  if (networkTools.length === 0) {
    return "no network-capable tool detected";
  }
  return networkTools.map((tool) => overviewValue(tool.name)).join(", ");
}

function networkToolCandidates(tools) {
  return Array.isArray(tools) ? tools.filter(toolSupportsNetwork) : [];
}

function toolSupportsNetwork(tool) {
  const haystack = [
    tool && tool.name,
    tool && tool.risk,
    tool && tool.description,
    tool && tool.class,
    tool && tool.capability,
  ]
    .map((value) => String(value || "").toLowerCase())
    .join(" ");
  return /\b(network|internet|http|https|url|web|browser|fetch)\b/.test(haystack);
}

function networkPolicyRequiresTool(context) {
  const value = configuredSetting(context, [
    "security.require_approval_for_network",
    "tools.internet_enabled",
    "internet.enabled",
    "network.enabled",
    "provider.network_enabled",
  ]);
  return value === true || ["true", "yes", "on", "1", "required", "enabled"].includes(
    String(value || "").toLowerCase(),
  );
}

function runtimeOverviewCompatibilityWarnings(context) {
  const warnings = [];
  const adapter = adapterByName(context.adapters, context.activeAdapter);
  const providerKnown = providerCapabilities(context).some(
    (provider) => safeObject(provider).name === context.activeAdapter,
  );
  if (context.activeAdapter && !adapter && !providerKnown) {
    warnings.push(`compat: active adapter ${overviewValue(context.activeAdapter)} is not registered`);
  }

  const effort = configuredSetting(context, [
    "brain.effort",
    "provider.effort",
    "reasoning.effort",
    "effort",
  ]);
  if (firstPresent(effort) && adapterCapability(adapter, ["supports_effort", "effort_supported"]) === false) {
    warnings.push("compat: effort configured but active provider declares no effort support");
  } else if (firstPresent(effort) && adapter && adapterCapability(adapter, ["supports_effort", "effort_supported"]) === undefined) {
    warnings.push("unknown: effort configured but provider effort capability is not exposed");
  }

  const fastMode = configuredSetting(context, [
    "brain.fast_mode",
    "provider.fast_mode",
    "fast_mode",
  ]);
  if (fastMode === true && adapterCapability(adapter, ["supports_fast_mode", "fast_mode_supported"]) === false) {
    warnings.push("compat: fast mode enabled but active provider declares no fast-mode support");
  } else if (fastMode === true && adapter && adapterCapability(adapter, ["supports_fast_mode", "fast_mode_supported"]) === undefined) {
    warnings.push("unknown: fast mode enabled but provider fast-mode capability is not exposed");
  }

  if (context.tools.length > 0 && adapterCapability(adapter, ["supports_tools", "tools_supported"]) === false) {
    warnings.push("compat: tools are registered but active provider declares no tool support");
  }

  const persona = configuredSetting(context, ["persona.profile"]);
  if (firstPresent(persona) && !configuredSetting(context, ["persona.effective_profile"])) {
    warnings.push("unknown: persona profile requested but effective profile/fallback is not exposed");
  }

  return warnings;
}

function adapterCapability(adapter, keys) {
  const source = safeObject(adapter);
  for (const key of keys) {
    const value = valueAtPath(source, key);
    if (value === true || value === false) {
      return value;
    }
  }
  return undefined;
}

function runtimeOverviewSourceFailures(failures) {
  if (!Array.isArray(failures) || failures.length === 0) {
    return "none";
  }
  return failures.map(overviewValue).join(", ");
}

function latestBargeInSummary(events) {
  const event = latestEvent(events, ["voice.speak.cancelled", "voice.speak.interrupted"]);
  if (!event) {
    return RUNTIME_OVERVIEW_NOT_EXPOSED;
  }
  return eventPayloadSummary(event.payload || {}) || eventLabel(event.type);
}

function latestEventIssue(events, families) {
  const candidates = Array.isArray(events) ? [...events] : [];
  candidates.sort((left, right) => Number(right.id || 0) - Number(left.id || 0));
  const event = candidates.find((item) => eventMatchesIssue(item, families));
  if (!event) {
    return "none in recent events";
  }
  const summary = eventPayloadSummary(event.payload || {});
  return summary ? `${eventLabel(event.type)} · ${summary}` : eventLabel(event.type);
}

function latestEvent(events, types) {
  const candidates = Array.isArray(events) ? [...events] : [];
  candidates.sort((left, right) => Number(right.id || 0) - Number(left.id || 0));
  return (
    candidates.find((item) => types.includes(String(item && item.type ? item.type : ""))) || null
  );
}

function latestEventPayloadValue(events, keys) {
  const candidates = Array.isArray(events) ? [...events] : [];
  candidates.sort((left, right) => Number(right.id || 0) - Number(left.id || 0));
  for (const event of candidates) {
    const payload = safeObject(event.payload);
    for (const key of keys) {
      const value = payload[key];
      if (value !== undefined && value !== null && value !== "") {
        return value;
      }
    }
  }
  return undefined;
}

function eventMatchesIssue(event, families) {
  const type = String(event && event.type ? event.type : "").toLowerCase();
  if (!families.some((family) => type.includes(family))) {
    return false;
  }
  const payload = safeObject(event.payload);
  const status = String(payload.status || "").toLowerCase();
  return (
    type.includes("error") ||
    type.includes("failed") ||
    type.includes("failure") ||
    status === "failed" ||
    Boolean(payload.error)
  );
}

async function sendTextInput(event) {
  event.preventDefault();
  clearError(el.inputError);

  const text = el.textInput.value.trim();
  if (!text || !cockpit.online) {
    return;
  }

  const body = { text, source: "panel" };
  if (cockpit.selectedConversationId) {
    body.conversation_id = cockpit.selectedConversationId;
  }

  // Optymistyczny dymek: wysłana wiadomość od razu ląduje w czacie i pole
  // się czyści (jak w komunikatorze), a nie dopiero po odpowiedzi daemona.
  appendPendingUserBubble(text);
  el.textInput.value = "";
  el.textInput.style.height = "";

  setBusy(el.sendButton, true);
  try {
    const payload = await requestJson("/input/text", {
      method: "POST",
      body,
    });
    cockpit.selectedConversationId = payload.conversation_id || cockpit.selectedConversationId;
    cockpit.composingNew = false;
    await Promise.all([refreshHistory(), refreshEvents(), refreshToolsAndApprovals()]);
  } catch (error) {
    renderError(el.inputError, error);
  } finally {
    setBusy(el.sendButton, false);
  }
}

// Wysokość pola śledzi treść aż do limitu z CSS (max-height), potem scroll.
// Reset (pusta wartość) wraca do bazowych dwóch rzędów.
function autoGrowComposer() {
  const field = el.textInput;
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}

function appendPendingUserBubble(text) {
  const emptyRow = el.turnList.querySelector(".empty-row");
  if (emptyRow) {
    emptyRow.remove();
  }
  el.turnList.appendChild(
    chatTurn({ input_text: text, status: "received", source: "panel" }),
  );
  scrollChatToBottom();
}

async function refreshHistory() {
  clearError(el.historyError);

  try {
    const payload = await requestJson("/conversations?limit=12");
    const conversations = Array.isArray(payload.conversations) ? payload.conversations : [];
    renderConversations(conversations);

    const hasSelected = conversations.some((conversation) => {
      return conversation.id === cockpit.selectedConversationId;
    });
    if (
      !cockpit.composingNew &&
      (!cockpit.selectedConversationId || !hasSelected) &&
      conversations.length > 0
    ) {
      cockpit.selectedConversationId = conversations[0].id;
    }
    if (cockpit.selectedConversationId) {
      await refreshTurns(cockpit.selectedConversationId);
      renderConversations(conversations);
    } else if (!cockpit.composingNew) {
      renderEmpty(el.turnList, "Napisz coś poniżej albo przytrzymaj Push To Talk.");
    }
  } catch (error) {
    clearNode(el.conversationSelect);
    clearNode(el.turnList);
    renderError(el.historyError, error);
  }
}

async function refreshTurns(conversationId) {
  // Najnowsza wymiana ma być widoczna od razu na górze; oldest-first z limitem
  // ucinało świeże tury w długich rozmowach i chowało resztę na dole listy.
  const query = `/turns?conversation_id=${encodeURIComponent(conversationId)}&limit=20&newest_first=true`;
  const payload = await requestJson(query);
  const turns = Array.isArray(payload.turns) ? payload.turns : [];
  renderTurns(turns);
}

// Rozmowy żyją w dropdownie przy czacie (nie w osobnej sekcji): wybór
// przełącza przebieg, "+" zaczyna nową rozmowę.
function renderConversations(conversations) {
  clearNode(el.conversationSelect);

  if (cockpit.composingNew || conversations.length === 0) {
    const fresh = document.createElement("option");
    fresh.value = "";
    setText(fresh, conversations.length === 0 ? "nowa rozmowa (brak historii)" : "nowa rozmowa…");
    el.conversationSelect.appendChild(fresh);
  }

  for (const conversation of conversations) {
    const option = document.createElement("option");
    option.value = conversation.id;
    const cachedTitle = conversation.title || cockpit.conversationTitles.get(conversation.id);
    const label = cachedTitle || `Rozmowa ${formatClock(conversation.latest_turn_at || conversation.created_at)}`;
    setText(option, `${label} · ${formatRelative(conversation.latest_turn_at || conversation.created_at)}`);
    if (!cachedTitle) {
      ensureConversationTitle(conversation.id, option);
    }
    el.conversationSelect.appendChild(option);
  }

  el.conversationSelect.value = cockpit.composingNew
    ? ""
    : cockpit.selectedConversationId || "";
}

function ensureNewConversationOption() {
  const existing = el.conversationSelect.querySelector('option[value=""]');
  if (!existing) {
    const fresh = document.createElement("option");
    fresh.value = "";
    setText(fresh, "nowa rozmowa…");
    el.conversationSelect.insertBefore(fresh, el.conversationSelect.firstChild);
  }
  el.conversationSelect.value = "";
}

// Kafelek bez tytułu dostaje początek pierwszego input_text rozmowy. Jedna
// tania prośba (limit=1, oldest-first) na rozmowę, potem cache — pierwsza
// tura jest niezmienna, więc nic tu nie musi się odświeżać.
async function ensureConversationTitle(conversationId, node) {
  if (cockpit.conversationTitles.has(conversationId)) {
    return;
  }
  cockpit.conversationTitles.set(conversationId, "");
  try {
    const payload = await requestJson(
      `/turns?conversation_id=${encodeURIComponent(conversationId)}&limit=1`,
    );
    const turns = Array.isArray(payload.turns) ? payload.turns : [];
    const title = titleFromInput(turns.length > 0 ? turns[0].input_text : "");
    if (title) {
      cockpit.conversationTitles.set(conversationId, title);
      setText(node, title);
    }
  } catch (error) {
    // Fallbackowa etykieta z zegarem już stoi; spróbujemy przy kolejnym renderze.
    cockpit.conversationTitles.delete(conversationId);
  }
}

function titleFromInput(inputText) {
  if (typeof inputText !== "string") {
    return "";
  }
  const flat = inputText.replace(/\s+/g, " ").trim();
  if (!flat) {
    return "";
  }
  return flat.length > 60 ? `${flat.slice(0, 60)}…` : flat;
}

function renderTurns(turns) {
  clearNode(el.turnList);

  if (turns.length === 0) {
    renderEmpty(el.turnList, "Pusta rozmowa — napisz coś poniżej.");
    return;
  }

  // Fetch przychodzi newest-first (świeże tury nie giną przy limicie);
  // czat czyta się chronologicznie, więc odwracamy i dowozimy scroll na dół.
  const chronological = [...turns].reverse();
  for (const turn of chronological) {
    el.turnList.appendChild(chatTurn(turn));
  }
  scrollChatToBottom();
}

function scrollChatToBottom() {
  el.turnList.scrollTop = el.turnList.scrollHeight;
  // Wysokość logu potrafi się zmienić już po renderze (np. karta zgód
  // urośnie w kolumnie ops) — dosuwamy jeszcze raz po przeliczeniu layoutu.
  window.requestAnimationFrame(() => {
    el.turnList.scrollTop = el.turnList.scrollHeight;
  });
}

function chatTurn(turn) {
  const wrap = document.createElement("article");
  wrap.className = "chat-turn";
  const status = turn.status || "unknown";
  if (status === "failed") {
    wrap.classList.add("failed");
  }

  if (turn.input_text) {
    const user = document.createElement("div");
    user.className = "chat-bubble user";
    setText(user, turn.input_text);
    wrap.appendChild(user);
  }

  const jarvis = document.createElement("div");
  jarvis.className = "chat-bubble jarvis";
  if (turn.final_text) {
    setText(jarvis, turn.final_text);
  } else {
    jarvis.classList.add("placeholder");
    setText(jarvis, turnPlaceholder(status));
  }
  wrap.appendChild(jarvis);

  const meta = document.createElement("p");
  meta.className = "chat-meta";
  const metaText = document.createElement("span");
  const statusLabel = status === "finished" ? "" : ` · ${status}`;
  setText(metaText, `${turn.source || "unknown"}${statusLabel} · `);
  meta.append(metaText, timeNode(turn.created_at));
  wrap.appendChild(meta);

  return wrap;
}

function turnPlaceholder(status) {
  if (status === "failed") {
    return "tura nieudana";
  }
  if (status === "cancelled") {
    return "przerwana";
  }
  if (status === "finished") {
    return "(bez odpowiedzi)";
  }
  return "…";
}

async function refreshMemory() {
  clearError(el.memoryError);

  try {
    const [payload, itemsPayload] = await Promise.all([
      requestJson("/memory?active_only=true&limit=25"),
      requestJson("/memory/items"),
    ]);
    const blocks = Array.isArray(payload.memory) ? payload.memory : [];
    const legacyBlocks = blocks.map((block) => ({ ...block, panel_source: "legacy_block" }));
    const items = Array.isArray(itemsPayload.items) ? itemsPayload.items : [];
    const activeItems = items
      .filter((item) => String(item.status || "").toLowerCase() === "active")
      .map(memoryItemToPanelRow);
    renderMemory([...activeItems, ...legacyBlocks]);
  } catch (error) {
    clearNode(el.memoryList);
    renderError(el.memoryError, error);
  }
}

function memoryItemToPanelRow(item) {
  return {
    id: item.id,
    kind: item.kind,
    title: item.title || item.canonical_key || shortId(item.id),
    body: item.content || item.claim || "",
    priority: null,
    active: item.status === "active",
    panel_source: "memory_os_item",
    status: item.status || "unknown",
    metadata: {
      memory_os_status: item.status || "unknown",
      namespace: item.namespace || "",
      scope: item.scope || "",
      promoted_by: item.source_policy || "approval",
    },
  };
}

async function createMemoryBlock(event) {
  event.preventDefault();
  clearError(el.memoryError);

  const priority = Number.parseInt(el.memoryPriority.value, 10);
  const body = {
    kind: el.memoryKind.value.trim(),
    title: el.memoryTitle.value.trim(),
    body: el.memoryBody.value.trim(),
    priority: Number.isFinite(priority) ? priority : 0,
    active: true,
  };

  setBusy(el.createMemoryButton, true);
  try {
    await requestJson("/memory", {
      method: "POST",
      body,
    });
    el.memoryTitle.value = "";
    el.memoryBody.value = "";
    await Promise.all([refreshMemory(), refreshEvents()]);
  } catch (error) {
    renderError(el.memoryError, error);
  } finally {
    setBusy(el.createMemoryButton, false);
  }
}

function renderMemory(blocks) {
  clearNode(el.memoryList);

  if (blocks.length === 0) {
    renderEmpty(el.memoryList, "Brak aktywnej pamięci");
    return;
  }

  for (const block of blocks) {
    const row = document.createElement("article");
    row.className = "list-row";

    appendLine(row, block.title || shortId(block.id), "input-line");
    if (block.body) {
      appendLine(row, block.body, "final-line");
    }

    // Chipy: rodzaj po ludzku + priorytet — na jedno spojrzenie widać wagę.
    const chips = document.createElement("div");
    chips.className = "mem-chips";
    const kindChip = document.createElement("span");
    kindChip.className = "mem-chip";
    setText(kindChip, memoryKindLabel(block.kind));
    const priorityChip = document.createElement("span");
    priorityChip.className = "mem-chip";
    setText(
      priorityChip,
      block.panel_source === "memory_os_item" ? "Memory OS" : `priorytet ${block.priority ?? 0}`,
    );
    chips.append(kindChip, priorityChip);
    if (block.status) {
      const statusChip = document.createElement("span");
      statusChip.className = "mem-chip";
      setText(statusChip, block.status);
      chips.appendChild(statusChip);
    }
    row.appendChild(chips);

    // Pochodzenie bloku po ludzku: kto zaproponował i kto zatwierdził
    // (auto-pamięć przez zgody) — bez tego nie widać, które notatki wpisał model.
    const metadata = block.metadata || {};
    if (metadata.proposed_by || metadata.promoted_by) {
      appendLine(row, memorySourceLine(metadata), "muted");
    }

    if (block.panel_source === "memory_os_item") {
      appendLine(row, "Memory OS item — widoczny w panelu; dobór do promptu zależy od polityki pamięci.", "muted");
      el.memoryList.appendChild(row);
      continue;
    }

    const actions = document.createElement("div");
    actions.className = "row-actions";

    const priorityInput = document.createElement("input");
    priorityInput.type = "number";
    priorityInput.className = "priority-input";
    priorityInput.value = String(block.priority ?? 0);
    priorityInput.setAttribute("aria-label", "Nowy priorytet bloku");
    const priorityButton = smallButton("Zapisz priorytet");
    priorityButton.addEventListener("click", async () => {
      const priority = Number.parseInt(priorityInput.value, 10);
      if (!Number.isFinite(priority)) {
        return;
      }
      clearError(el.memoryError);
      setBusy(priorityButton, true);
      try {
        await requestJson(`/memory/${encodeURIComponent(block.id)}`, {
          method: "PATCH",
          body: { priority },
        });
        await Promise.all([refreshMemory(), refreshEvents()]);
      } catch (error) {
        renderError(el.memoryError, error);
      } finally {
        setBusy(priorityButton, false);
      }
    });

    const disableButton = smallButton("Wyłącz");
    disableButton.classList.add("danger");
    disableButton.addEventListener("click", async () => {
      clearError(el.memoryError);
      setBusy(disableButton, true);
      try {
        await requestJson(`/memory/${encodeURIComponent(block.id)}`, { method: "DELETE" });
        await Promise.all([refreshMemory(), refreshEvents()]);
      } catch (error) {
        renderError(el.memoryError, error);
      } finally {
        setBusy(disableButton, false);
      }
    });

    actions.append(priorityInput, priorityButton, disableButton);
    row.appendChild(actions);
    el.memoryList.appendChild(row);
  }
}

// Pochodzenie notatki po ludzku: „zaproponował: model · zatwierdził:
// zatwierdzenie w panelu” zamiast surowego proposed_by/promoted_by.
function memorySourceLine(metadata) {
  const parts = [];
  if (metadata.proposed_by) {
    parts.push(`zaproponował: ${requesterLabel(metadata.proposed_by)}`);
  }
  if (metadata.promoted_by) {
    const who =
      metadata.promoted_by === "approval"
        ? "zgoda w panelu"
        : requesterLabel(metadata.promoted_by);
    parts.push(`zatwierdził: ${who}`);
  }
  return parts.join(" · ");
}

async function refreshToolsAndApprovals() {
  await Promise.all([refreshTools(), refreshApprovals()]);
}

async function refreshTools() {
  clearError(el.toolsError);
  try {
    const toolsPayload = await requestJson("/tools");
    renderTools(Array.isArray(toolsPayload.tools) ? toolsPayload.tools : []);
  } catch (error) {
    clearNode(el.toolList);
    renderError(el.toolsError, error);
  }
}

async function refreshApprovals() {
  clearError(el.approvalsError);
  try {
    const approvalsPayload = await requestJson("/approvals?limit=25");
    renderApprovals(Array.isArray(approvalsPayload.approvals) ? approvalsPayload.approvals : []);
  } catch (error) {
    clearNode(el.approvalList);
    renderError(el.approvalsError, error);
  }
}

// Heartbeat /health niesie pending_approval_count — to fallback dla eventów
// approval.* ze streamu: gdy licznik się rozjedzie z tym, co panel pokazuje,
// dociągamy karty zgód nawet bez działającego WebSocketa.
function syncPendingApprovals(rawCount) {
  const count = Number(rawCount);
  if (!Number.isFinite(count)) {
    return;
  }
  if (count !== cockpit.pendingApprovalCount) {
    scheduleApprovalsRefresh();
  }
  setPendingBadge(count);
}

// Żywa ramka stanu: jeden atrybut body[data-state] steruje neonem na krawędzi
// karty. Priorytet od najważniejszego: brak łącza (offline) > czekająca zgoda
// (pending) > daemon pracuje (busy: myśli/mówi/słucha) > spoczynek (online).
// Ruch (obieganie) tylko przy busy/pending — czyli gdy coś naprawdę trwa.
function applyStateFrame() {
  let state = "offline";
  if (cockpit.online) {
    if (cockpit.pendingApprovalCount > 0) {
      state = "pending";
    } else if (isDaemonBusy()) {
      state = "busy";
    } else {
      state = "online";
    }
  }
  document.body.dataset.state = state;
}

// Daemon "pracuje", gdy jego RuntimeState wyszedł ze spoczynku (myśli, mówi,
// słucha) albo mikrofon właśnie zbiera dźwięk. IDLE i wszystko nieznane =
// spoczynek, żeby ramka nie kręciła się bez powodu.
function isDaemonBusy() {
  const state = String(cockpit.runtimeState || "").toUpperCase();
  if (state === "THINKING" || state === "SPEAKING" || state === "LISTENING") {
    return true;
  }
  return cockpit.voice.listening === true;
}

// Jeden widok naraz: zakładki przełączają panele, stan widoku żyje tylko
// w DOM (thin client — żadnej persystencji poza tokenem API).
function switchView(view) {
  document.body.dataset.view = view;
  for (const tab of document.querySelectorAll(".tab-button")) {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const panel of document.querySelectorAll(".view")) {
    panel.classList.toggle("active", panel.id === `view-${view}`);
  }
  if (view === "chat") {
    scrollChatToBottom();
  }
  updateApprovalSignals();
}

function setPendingBadge(count) {
  cockpit.pendingApprovalCount = count;
  document.body.classList.toggle("has-pending", count > 0);
  applyStateFrame();
  updateApprovalSignals();
}

// Dwa sygnały zgód: licznik na zakładce (zawsze) i bursztynowy przerywnik
// w czacie (tylko poza widokiem zgód) — przerwanie ma być nie do przegapienia.
function updateApprovalSignals() {
  const count = cockpit.pendingApprovalCount;
  if (el.approvalsBadge) {
    el.approvalsBadge.hidden = count === 0;
    setText(el.approvalsBadge, String(count));
  }
  if (el.approvalNudge) {
    const onApprovalsView = document.body.dataset.view === "approvals";
    el.approvalNudge.hidden = count === 0 || onApprovalsView;
    const verb = count === 1 ? "czeka" : approvalLabel(count) === "zgody" ? "czekają" : "czeka";
    setText(el.approvalNudge, `${count} ${approvalLabel(count)} ${verb} na decyzję — pokaż`);
  }
}

function approvalLabel(count) {
  if (count === 1) {
    return "zgoda";
  }
  const lastDigit = count % 10;
  const lastTwo = count % 100;
  if (lastDigit >= 2 && lastDigit <= 4 && (lastTwo < 12 || lastTwo > 14)) {
    return "zgody";
  }
  return "zgód";
}

function renderTools(tools) {
  clearNode(el.toolList);

  if (tools.length === 0) {
    renderEmpty(el.toolList, "Brak narzędzi");
    return;
  }

  for (const tool of tools) {
    const row = document.createElement("div");
    row.className = "list-row";

    // Ludzka nazwa + chip polityki zgód po polsku (nigdy „file_read -
    // file_read”): CO to umie i z jaką wagą pyta o zgodę.
    const head = document.createElement("div");
    head.className = "approval-head";
    const name = document.createElement("span");
    name.className = "approval-tool";
    setText(name, toolLabel(tool.name));
    const chip = document.createElement("span");
    chip.className = `risk-chip ${riskTier(tool.risk)}`;
    setText(chip, riskLabel(tool.risk));
    head.append(name, chip);
    row.appendChild(head);

    if (tool.description) {
      appendLine(row, tool.description, "muted");
    }
    el.toolList.appendChild(row);
  }
}

function renderApprovals(pendingApprovals) {
  clearNode(el.approvalList);
  setPendingBadge(pendingApprovals.length);

  const approved = Array.from(cockpit.approvedApprovals.values());
  if (pendingApprovals.length === 0 && approved.length === 0) {
    renderApprovalsEmpty();
    return;
  }

  for (const approval of pendingApprovals) {
    el.approvalList.appendChild(approvalCard(approval, "pending"));
  }
  for (const approval of approved) {
    el.approvalList.appendChild(approvalCard(approval, "approved"));
  }
}

// Pusty stan zgód: spokojny, wycentrowany znak ✓ + jedno zdanie, co się tu
// pojawi — a nie wiersz, który wygląda jak wyszarzony formularz.
function renderApprovalsEmpty() {
  clearNode(el.approvalList);
  const box = document.createElement("div");
  box.className = "empty-state";

  const mark = document.createElement("div");
  mark.className = "empty-state-mark";
  mark.setAttribute("aria-hidden", "true");

  const title = document.createElement("p");
  title.className = "empty-state-title";
  setText(title, "Nic nie czeka");

  const hint = document.createElement("p");
  hint.className = "empty-state-hint muted";
  setText(hint, "Gdy Jarvis poprosi o użycie narzędzia, decyzja pojawi się tutaj.");

  const note = document.createElement("p");
  note.className = "empty-state-note muted";
  setText(note, "Zatwierdzenie nie wykonuje — wykonanie to osobny klik.");

  box.append(mark, title, hint, note);
  el.approvalList.appendChild(box);
}

// Karta zgody czytelna na rzut oka: CO (ludzka nazwa narzędzia) + JAK
// ryzykowne (chip barwiony wagą) na górze, JAKIE argumenty (tabelka
// klucz→wartość), meta (id · kto prosi · kiedy) najmniej ważną linijką na
// dole, i jednoznaczne przyciski w prawym dolnym rogu.
function approvalCard(approval, mode) {
  const card = document.createElement("article");
  card.className = `approval-card ${mode}`;
  const payload = approval.payload || {};
  const status = approval.status || mode;

  // Eyebrow: jednoznaczny kontekst, że to prośba czekająca na Twoją decyzję
  // (albo już zatwierdzona, gotowa do wykonania) — żeby wiadomo było, co się
  // dzieje, zanim spojrzysz na przyciski.
  const eyebrow = document.createElement("p");
  eyebrow.className = "approval-eyebrow";
  setText(
    eyebrow,
    mode === "pending" ? "Jarvis prosi o zgodę na:" : "Zatwierdzone — gotowe do wykonania:",
  );
  card.appendChild(eyebrow);

  const head = document.createElement("div");
  head.className = "approval-head";
  const name = document.createElement("span");
  name.className = "approval-tool";
  setText(name, toolLabel(payload.tool_name || approval.action_type));
  const chip = document.createElement("span");
  chip.className = `risk-chip ${riskTier(approval.risk)}`;
  setText(chip, riskLabel(approval.risk));
  head.append(name, chip);
  card.appendChild(head);

  const args = Object.entries(payload.arguments || {});
  if (args.length > 0) {
    const table = document.createElement("dl");
    table.className = "approval-args";
    for (const [key, value] of args) {
      const dt = document.createElement("dt");
      setText(dt, key);
      const dd = document.createElement("dd");
      dd.className = "approval-arg";
      setText(dd, argumentPreview(value));
      table.append(dt, dd);
    }
    card.appendChild(table);
  }

  const summary = approvalSummary(approval);
  if (summary) {
    appendLine(card, summary, "payload-line");
  }

  const details = document.createElement("dl");
  details.className = "approval-args";
  for (const [key, value] of [
    ["id", approval.id],
    ["status", status],
    ["typ", approval.action_type],
    ["narzędzie", payload.tool_name],
    ["źródło", approval.requested_by],
  ]) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    const dt = document.createElement("dt");
    setText(dt, key);
    const dd = document.createElement("dd");
    dd.className = "approval-arg";
    setText(dd, argumentPreview(value));
    details.append(dt, dd);
  }
  if (details.children.length > 0) {
    card.appendChild(details);
  }

  const meta = document.createElement("p");
  meta.className = "approval-meta";
  const modeLabel = mode === "pending" ? "czeka na decyzję" : "zatwierdzona";
  const who = requesterLabel(approval.requested_by);
  const metaText = document.createElement("span");
  setText(metaText, `${shortId(approval.id)} · ${who} · ${modeLabel} · `);
  meta.append(metaText, timeNode(approval.created_at || approval.requested_at));
  card.appendChild(meta);

  const actions = document.createElement("div");
  actions.className = "row-actions";
  if (mode === "pending") {
    const approveButton = smallButton(isMemoryApproval(approval) ? "Zatwierdź i zapisz" : "Zatwierdź");
    approveButton.classList.add("strong");
    approveButton.addEventListener("click", () => {
      if (isMemoryApproval(approval)) {
        approveAndExecuteApproval(approval.id, approveButton);
      } else {
        decideApproval(approval.id, "approve", approveButton);
      }
    });
    const rejectButton = smallButton("Odrzuć");
    rejectButton.classList.add("reject");
    rejectButton.addEventListener("click", () => decideApproval(approval.id, "reject", rejectButton));
    actions.append(approveButton, rejectButton);
  } else {
    const executeButton = smallButton("Wykonaj zatwierdzone");
    executeButton.classList.add("strong");
    executeButton.addEventListener("click", () => executeApproval(approval.id, executeButton));
    actions.appendChild(executeButton);
  }
  card.appendChild(actions);
  return card;
}

function approvalSummary(approval) {
  const payload = approval.payload || {};
  const args = payload.arguments || {};
  if (typeof payload.summary === "string" && payload.summary.trim()) {
    return payload.summary.trim();
  }
  if (isMemoryApproval(approval)) {
    const title = typeof args.title === "string" ? args.title.trim() : "";
    const body = typeof args.body === "string" ? args.body.trim() : "";
    return [title, body].filter(Boolean).join(" — ").slice(0, 220);
  }
  if (typeof args.command === "string") {
    return args.command.slice(0, 220);
  }
  if (typeof args.path === "string") {
    return args.path.slice(0, 220);
  }
  return "";
}

function isMemoryApproval(approval) {
  const payload = approval.payload || {};
  return payload.tool_name === "memory_save" || approval.action_type === "tool:memory_save";
}

// Kto prosi o zgodę — po ludzku, bez surowego identyfikatora źródła.
function requesterLabel(requestedBy) {
  const map = {
    brain: "model",
    model: "model",
    panel: "panel",
    voice: "głos",
    worker: "worker",
  };
  const key = typeof requestedBy === "string" ? requestedBy : "";
  return map[key] || key || "nieznane źródło";
}

async function decideApproval(approvalId, action, button) {
  clearError(el.approvalsError);
  setApprovalCardBusy(button, true);
  try {
    const payload = await requestJson(`/approvals/${encodeURIComponent(approvalId)}/${action}`, {
      method: "POST",
      body: { reason: "panel click" },
    });
    if (action === "approve" && payload.approval) {
      cockpit.approvedApprovals.set(approvalId, payload.approval);
    }
    if (action === "reject") {
      cockpit.approvedApprovals.delete(approvalId);
    }
    await Promise.all([refreshApprovals(), refreshEvents()]);
  } catch (error) {
    renderError(el.approvalsError, error);
  } finally {
    setApprovalCardBusy(button, false);
  }
}

async function approveAndExecuteApproval(approvalId, button) {
  clearError(el.approvalsError);
  setApprovalCardBusy(button, true);
  let approveSucceeded = false;
  try {
    const payload = await requestJson(`/approvals/${encodeURIComponent(approvalId)}/approve`, {
      method: "POST",
      body: { reason: "panel approve and save" },
    });
    approveSucceeded = true;
    if (payload.approval) {
      cockpit.approvedApprovals.set(approvalId, payload.approval);
    }
    await executeApprovalRequest(approvalId);
    cockpit.approvedApprovals.delete(approvalId);
    await refreshAfterApprovalExecution();
  } catch (error) {
    if (isAlreadyExecutedConflict(error)) {
      cockpit.approvedApprovals.delete(approvalId);
      await refreshAfterApprovalExecution();
      return;
    }
    if (approveSucceeded) {
      await refreshApprovals();
    }
    renderError(el.approvalsError, error);
  } finally {
    setApprovalCardBusy(button, false);
  }
}

async function executeApproval(approvalId, button) {
  clearError(el.approvalsError);
  setApprovalCardBusy(button, true);
  try {
    await executeApprovalRequest(approvalId);
    cockpit.approvedApprovals.delete(approvalId);
    await refreshAfterApprovalExecution();
  } catch (error) {
    if (isAlreadyExecutedConflict(error)) {
      cockpit.approvedApprovals.delete(approvalId);
      await refreshAfterApprovalExecution();
      return;
    }
    renderError(el.approvalsError, error);
  } finally {
    setApprovalCardBusy(button, false);
  }
}

async function executeApprovalRequest(approvalId) {
  return requestJson(`/approvals/${encodeURIComponent(approvalId)}/execute`, {
    method: "POST",
  });
}

async function refreshAfterApprovalExecution() {
  await Promise.all([
    refreshApprovals(),
    refreshEvents(),
    refreshMemory(),
    refreshHistory(),
    refreshHealthAndState(),
  ]);
}

function isAlreadyExecutedConflict(error) {
  const detail = error && error.detail ? error.detail : {};
  const payload = detail.payload || {};
  const text = `${error?.message || ""} ${payload.error || ""}`.toLowerCase();
  return detail.status === 409 && text.includes("already executed");
}

function setApprovalCardBusy(button, busy) {
  const card = button.closest(".approval-card");
  if (card) {
    for (const item of card.querySelectorAll("button")) {
      item.disabled = busy;
      item.classList.toggle("busy", busy);
    }
  } else {
    setBusy(button, busy);
  }
}

// --- Settings (GET /settings + POST /settings, brain switch; ADR-002) ---
// The cockpit never keeps a settings copy: every render reads the daemon
// and every mutation POSTs, then re-fetches daemon truth.

async function refreshSettings() {
  clearError(el.settingsError);

  try {
    const [settingsPayload, brainPayload] = await Promise.all([
      requestJson("/settings"),
      requestJson("/brain/adapters"),
    ]);
    renderBrainAdapters(brainPayload);
    renderSettings(settingsPayload.settings || {});
  } catch (error) {
    clearNode(el.settingsList);
    clearNode(el.brainAdapterSelect);
    setText(el.brainAdapterLabel, "");
    renderError(el.settingsError, error);
  }
}

function renderBrainAdapters(payload) {
  clearNode(el.brainAdapterSelect);

  const adapters = Array.isArray(payload.adapters) ? payload.adapters : [];
  for (const adapter of adapters) {
    const option = document.createElement("option");
    option.value = adapter.name;
    setText(option, adapter.name);
    el.brainAdapterSelect.appendChild(option);
  }
  if (payload.current) {
    el.brainAdapterSelect.value = payload.current;
  }
  setText(
    el.brainAdapterLabel,
    `aktywny: ${payload.current || "n/a"} · domyślny: ${payload.default || "n/a"}`,
  );
}

function renderSettings(settings) {
  clearNode(el.settingsList);

  const entries = Object.entries(settings);
  if (entries.length === 0) {
    renderEmpty(el.settingsList, "Brak ustawień");
    return;
  }

  for (const [key, value] of entries) {
    const row = document.createElement("article");
    row.className = "list-row";
    appendLine(row, key, "input-line");
    appendLine(row, JSON.stringify(value), "final-line");

    const actions = document.createElement("div");
    actions.className = "row-actions";
    const editButton = smallButton("Edit");
    editButton.addEventListener("click", () => {
      el.settingKey.value = key;
      el.settingValue.value = JSON.stringify(value);
      el.settingValue.focus();
    });
    actions.appendChild(editButton);
    row.appendChild(actions);
    el.settingsList.appendChild(row);
  }
}

async function saveSetting(event) {
  event.preventDefault();
  clearError(el.settingsError);

  if (POC_NO_PERSISTENCE_GUARD) {
    renderError(
      el.settingsError,
      makeRequestError("Mission Control POC is read-only; settings save is disabled.", {
        route: "/settings",
      }),
    );
    return;
  }

  const key = el.settingKey.value.trim();
  if (!key || !cockpit.online) {
    return;
  }

  let value;
  try {
    value = JSON.parse(el.settingValue.value);
  } catch (error) {
    renderError(
      el.settingsError,
      makeRequestError('Setting value must be valid JSON, e.g. true, 3 or "text"', {
        value: el.settingValue.value,
      }),
    );
    return;
  }

  setBusy(el.saveSettingButton, true);
  try {
    await requestJson("/settings", {
      method: "POST",
      body: { key, value },
    });
    el.settingKey.value = "";
    el.settingValue.value = "";
    await refreshSettings();
  } catch (error) {
    renderError(el.settingsError, error);
  } finally {
    setBusy(el.saveSettingButton, false);
  }
}

async function switchBrain() {
  clearError(el.settingsError);

  if (POC_NO_PERSISTENCE_GUARD) {
    renderError(
      el.settingsError,
      makeRequestError("Mission Control POC is read-only; provider switch execution is disabled.", {
        route: "/brain/switch",
      }),
    );
    return;
  }

  const adapter = el.brainAdapterSelect.value;
  if (!adapter || !cockpit.online) {
    return;
  }

  setBusy(el.switchBrainButton, true);
  try {
    await requestJson("/brain/switch", {
      method: "POST",
      body: { adapter },
    });
    await Promise.all([refreshSettings(), refreshHealthAndState(), refreshEvents()]);
  } catch (error) {
    renderError(el.settingsError, error);
  } finally {
    setBusy(el.switchBrainButton, false);
  }
}

async function refreshEvents() {
  clearError(el.eventsError);

  try {
    const payload = await requestJson("/events?latest=true&limit=50");
    const events = Array.isArray(payload.events) ? payload.events : [];
    renderEvents(events);
    const latestId = Number(payload.latest_event_id);
    if (Number.isFinite(latestId) && latestId > cockpit.stream.lastEventId) {
      cockpit.stream.lastEventId = latestId;
    }
  } catch (error) {
    clearNode(el.eventList);
    renderError(el.eventsError, error);
  }
}

function eventNumericId(event) {
  const value = Number(event && event.id);
  return Number.isFinite(value) ? value : null;
}

function newestFirstEvents(events) {
  const rows = Array.isArray(events) ? events : [];
  const indexed = rows.map((event, index) => ({
    event,
    index,
    id: eventNumericId(event),
  }));
  indexed.sort((left, right) => {
    if (left.id !== null && right.id !== null && left.id !== right.id) {
      return right.id - left.id;
    }
    if (left.id !== null && right.id === null) {
      return -1;
    }
    if (left.id === null && right.id !== null) {
      return 1;
    }
    return left.index - right.index;
  });

  const seenIds = new Set();
  const deduped = [];
  for (const row of indexed) {
    if (row.id !== null) {
      const key = String(row.id);
      if (seenIds.has(key)) {
        continue;
      }
      seenIds.add(key);
    }
    deduped.push(row.event);
  }
  return deduped;
}

function eventCacheRows(events) {
  return newestFirstEvents(events).slice(0, MAX_LIVE_EVENT_ROWS * 2);
}

function renderEvents(events) {
  const rows = eventCacheRows(events);
  cockpit.lastEvents = rows;
  clearNode(el.eventList);

  const filter = cockpit.logFilter || "all";
  const shown = rows
    .filter((event) => eventMatchesFilter(event.type, filter))
    .slice(0, MAX_LIVE_EVENT_ROWS);
  if (shown.length === 0) {
    renderEmpty(
      el.eventList,
      filter === "all" ? "Brak zdarzeń" : "Brak zdarzeń w tym filtrze",
    );
    return;
  }

  for (const event of shown) {
    el.eventList.appendChild(eventRow(event));
  }
}

// Wiersz dziennika po ludzku: ludzka etykieta zdarzenia + meta
// #id · źródło · czas względny (mono, najmniej ważna linijka).
function eventRow(event) {
  const row = document.createElement("div");
  row.className = "list-row";
  const item = safeEventTimelineItem(event);
  appendLine(row, `${eventLabel(event.type)} · ${item.family}`, "input-line");
  const compact = [
    item.summary,
    item.status ? `status: ${item.status}` : null,
    item.severity && item.severity !== item.status ? `severity: ${item.severity}` : null,
  ].filter(Boolean).join(" · ");
  if (compact) {
    appendLine(row, compact, "payload-line");
  }

  const meta = document.createElement("p");
  meta.className = "event-meta";
  const metaText = document.createElement("span");
  setText(metaText, `#${event.id} · ${event.source || "system"} · `);
  meta.append(metaText, timeNode(event.created_at));
  row.appendChild(meta);
  return row;
}

const EVENT_PAYLOAD_SUMMARY_KEYS = [
  "turn_id",
  "conversation_id",
  "request_id",
  "approval_id",
  "tool_name",
  "status",
  "kind",
  "seq",
  "reason",
  "error",
  "duration_seconds",
  "voiced_seconds",
  "rms",
];
const EVENT_PAYLOAD_REDACTION = "[REDACTED]";

function safeEventTimelineItem(event) {
  const payload = event && event.payload && typeof event.payload === "object" ? event.payload : {};
  const summary = eventPayloadSummary(payload);
  const status = eventStatus(payload, event && event.type);
  return {
    timestamp: event && event.created_at ? event.created_at : "",
    family: eventFamily(event && event.type),
    type: event && event.type ? String(event.type) : "unknown",
    summary,
    status,
    severity: eventSeverity(event && event.type, status, payload),
  };
}

function eventFamily(type) {
  const value = String(type || "").toLowerCase();
  if (!value) {
    return "unknown";
  }
  if (value.startsWith("daemon.") || value.startsWith("state.") || value.startsWith("runtime.")) {
    return "runtime";
  }
  if (value.startsWith("turn.") || value.startsWith("input.")) {
    return "turn";
  }
  if (value.startsWith("voice.") || value.startsWith("audio.") || value.startsWith("listening.")) {
    return "voice";
  }
  if (value.startsWith("brain.") || value.startsWith("provider.")) {
    return "provider";
  }
  if (value.startsWith("approval.")) {
    return "approval";
  }
  if (value.startsWith("memory.")) {
    return "memory";
  }
  if (value.startsWith("tool.")) {
    return "tool";
  }
  if (value.startsWith("panel.")) {
    return "panel";
  }
  if (value.includes("failed") || value.includes("error")) {
    return "error";
  }
  return "unknown";
}

function eventStatus(payload, type) {
  const source = payload && typeof payload === "object" ? payload : {};
  const raw = firstPresent(source.status, source.reason, source.kind);
  if (raw !== undefined && raw !== null && raw !== "") {
    return overviewValue(eventPayloadSummaryValue(raw));
  }
  const value = String(type || "").toLowerCase();
  if (value.includes("failed")) {
    return "failed";
  }
  if (value.includes("cancelled")) {
    return "cancelled";
  }
  if (value.includes("finished") || value.includes("responded")) {
    return "ok";
  }
  return "";
}

function eventSeverity(type, status, payload) {
  const value = `${String(type || "").toLowerCase()} ${String(status || "").toLowerCase()}`;
  if (value.includes("failed") || value.includes("error") || eventPayloadSummaryValue(payload && payload.error)) {
    return "error";
  }
  if (value.includes("cancelled") || value.includes("warning") || value.includes("invalid")) {
    return "warning";
  }
  if (status) {
    return status;
  }
  return "info";
}

function eventPayloadSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return "";
  }
  const parts = [];
  for (const key of EVENT_PAYLOAD_SUMMARY_KEYS) {
    const value = eventPayloadSummaryValue(payload[key]);
    if (value === "") {
      continue;
    }
    parts.push(`${key}: ${argumentPreview(value)}`);
  }
  return parts.join(" · ");
}

function eventPayloadSummaryValue(value) {
  if (value === undefined || value === null || value === "") {
    return "";
  }
  if (typeof value === "string") {
    return redactEventSummaryText(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  return "";
}

function redactEventSummaryText(value) {
  return value
    .replace(/(\bAuthorization\s*[:=]\s*Bearer\s+)[^\s,;"']+/gi, `$1${EVENT_PAYLOAD_REDACTION}`)
    .replace(/(\bBearer\s+)[A-Za-z0-9._~+/=-]+/gi, `$1${EVENT_PAYLOAD_REDACTION}`)
    .replace(/(\bAuthorization\s*[:=]\s*Basic\s+)[A-Za-z0-9+/=._-]+/gi, `$1${EVENT_PAYLOAD_REDACTION}`)
    .replace(/(\bBasic\s+)[A-Za-z0-9+/=._-]{8,}/gi, `$1${EVENT_PAYLOAD_REDACTION}`)
    .replace(/\bgithub_pat_[A-Za-z0-9_]{8,}/g, EVENT_PAYLOAD_REDACTION)
    .replace(/\bgh[oprsu]_[A-Za-z0-9_]{8,}/g, EVENT_PAYLOAD_REDACTION)
    .replace(/\bsk-[A-Za-z0-9][A-Za-z0-9._-]*/g, EVENT_PAYLOAD_REDACTION)
    .replace(/\bxox[abps]-[A-Za-z0-9-]{8,}/g, EVENT_PAYLOAD_REDACTION)
    .replace(/\bAKIA[0-9A-Z]{16}\b/g, EVENT_PAYLOAD_REDACTION)
    .replace(
      /\b(password|passwd|secret|api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token|token|private[_-]?key|client[_-]?secret|credentials?)\b\s*[:=]\s*["']?[^\s"']{4,}["']?/gi,
      `$1=${EVENT_PAYLOAD_REDACTION}`,
    );
}

// --- Live event stream (GET /stream WebSocket, read-only; ADR-019) ---

function streamUrl() {
  const base = apiBase().replace(/^http/, "ws");
  if (cockpit.stream.lastEventId > 0) {
    return `${base}/stream?after_id=${cockpit.stream.lastEventId}`;
  }
  return `${base}/stream`;
}

function connectStream() {
  const stream = cockpit.stream;
  if (
    stream.socket &&
    stream.base === apiBase() &&
    (stream.socket.readyState === WebSocket.OPEN ||
      stream.socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  disconnectStream("reconnecting");

  // The browser cannot set X-Jarvis-Token on a WebSocket handshake, so the
  // token rides along as a jarvis-token.<token> subprotocol entry.
  const protocols = [STREAM_SUBPROTOCOL];
  const token = apiToken();
  if (token) {
    protocols.push(`${STREAM_TOKEN_SUBPROTOCOL_PREFIX}${token}`);
  }

  let socket;
  try {
    socket = new WebSocket(streamUrl(), protocols);
  } catch (error) {
    setStreamStatus("stream off");
    scheduleStreamReconnect();
    return;
  }

  stream.socket = socket;
  stream.base = apiBase();
  setStreamStatus("stream connecting");

  socket.addEventListener("open", () => {
    stream.retryMs = 2000;
    stream.connected = true;
    setStreamStatus("live");
  });
  socket.addEventListener("message", (message) => {
    handleStreamMessage(message.data);
  });
  socket.addEventListener("close", () => {
    if (stream.socket === socket) {
      stream.socket = null;
      stream.connected = false;
      setStreamStatus(apiToken() ? "stream off" : "stream off (token?)");
      scheduleStreamReconnect();
    }
  });
}

function disconnectStream(reason) {
  const stream = cockpit.stream;
  if (stream.reconnectTimer !== null) {
    clearTimeout(stream.reconnectTimer);
    stream.reconnectTimer = null;
  }
  if (stream.socket) {
    const socket = stream.socket;
    stream.socket = null;
    stream.connected = false;
    try {
      socket.close(1000, reason || "cockpit disconnect");
    } catch (error) {
      // already closed
    }
  }
  setStreamStatus("stream off");
}

function scheduleStreamReconnect() {
  const stream = cockpit.stream;
  if (stream.reconnectTimer !== null || !cockpit.online) {
    return;
  }
  stream.reconnectTimer = setTimeout(() => {
    stream.reconnectTimer = null;
    if (cockpit.online) {
      connectStream();
    }
  }, stream.retryMs);
  stream.retryMs = Math.min(stream.retryMs * 2, STREAM_MAX_RETRY_MS);
}

function handleStreamMessage(raw) {
  let frame;
  try {
    frame = JSON.parse(raw);
  } catch (error) {
    return;
  }

  if (frame.type === "stream.hello") {
    // Hello reports server state only; the reconnect cursor advances when an
    // actual event frame is accepted.
    return;
  }
  if (frame.type !== "event" || !frame.event) {
    return;
  }

  const event = frame.event;
  const eventId = Number(event.id);
  if (Number.isFinite(eventId) && eventId > cockpit.stream.lastEventId) {
    cockpit.stream.lastEventId = eventId;
  }
  prependLiveEvent(event);

  const type = String(event.type || "");
  if (type === "state.changed" && event.payload && event.payload.new_state) {
    setText(el.stateLabel, event.payload.new_state);
    // Stan pracy prosto ze strumienia — ramka reaguje natychmiast, bez
    // czekania na następny heartbeat /state.
    cockpit.runtimeState = event.payload.new_state;
    applyStateFrame();
  }
  if (type.startsWith("input.") || type.startsWith("turn.")) {
    scheduleHistoryRefresh();
  }
  if (type.startsWith("memory.") || type === "tool.finished") {
    scheduleMemoryRefresh();
  }
  if (type.startsWith("approval.") || type.startsWith("tool.")) {
    scheduleApprovalsRefresh();
  }
  if (type.startsWith("brain.")) {
    scheduleSettingsRefresh();
  }
  if (type.startsWith("listening.") || type.startsWith("voice.")) {
    scheduleVoiceRefresh();
  }
  if (type.startsWith("voice.")) {
    scheduleVoiceQueueRefresh();
  }
  if (type.startsWith("daemon.") || type === "state.changed") {
    scheduleRuntimeRefresh();
  }
  if (runtimeOverviewEventType(type)) {
    scheduleRuntimeOverviewRefresh();
  }
}

function runtimeOverviewEventType(type) {
  return (
    type === "state.changed" ||
    type.startsWith("input.") ||
    type.startsWith("turn.") ||
    type.startsWith("voice.") ||
    type.startsWith("brain.") ||
    type.startsWith("daemon.") ||
    type.startsWith("approval.") ||
    type.startsWith("memory.") ||
    type.startsWith("tool.")
  );
}

function scheduleHistoryRefresh() {
  const stream = cockpit.stream;
  if (stream.historyTimer !== null) {
    return;
  }
  stream.historyTimer = setTimeout(async () => {
    stream.historyTimer = null;
    try {
      await refreshHistory();
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function scheduleMemoryRefresh() {
  const stream = cockpit.stream;
  if (stream.memoryTimer !== null) {
    return;
  }
  stream.memoryTimer = setTimeout(async () => {
    stream.memoryTimer = null;
    try {
      await refreshMemory();
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function scheduleVoiceRefresh() {
  const stream = cockpit.stream;
  if (stream.voiceTimer !== null) {
    return;
  }
  stream.voiceTimer = setTimeout(async () => {
    stream.voiceTimer = null;
    try {
      await refreshVoice();
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function scheduleVoiceQueueRefresh() {
  const stream = cockpit.stream;
  if (stream.voiceQueueTimer !== null) {
    return;
  }
  stream.voiceQueueTimer = setTimeout(async () => {
    stream.voiceQueueTimer = null;
    try {
      await refreshVoiceQueue();
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function prependLiveEvent(event) {
  const rows = Array.isArray(cockpit.lastEvents) ? cockpit.lastEvents : [];
  renderEvents([event, ...rows]);
}

function scheduleApprovalsRefresh() {
  const stream = cockpit.stream;
  if (stream.approvalsTimer !== null) {
    return;
  }
  stream.approvalsTimer = setTimeout(async () => {
    stream.approvalsTimer = null;
    try {
      await refreshToolsAndApprovals();
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function scheduleSettingsRefresh() {
  const stream = cockpit.stream;
  if (stream.settingsTimer !== null) {
    return;
  }
  stream.settingsTimer = setTimeout(async () => {
    stream.settingsTimer = null;
    try {
      await Promise.all([refreshSettings(), refreshHealthAndState()]);
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function scheduleRuntimeRefresh() {
  const stream = cockpit.stream;
  if (stream.runtimeTimer !== null) {
    return;
  }
  stream.runtimeTimer = setTimeout(async () => {
    stream.runtimeTimer = null;
    try {
      await Promise.all([refreshHealthAndState(), refreshRuntime()]);
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function scheduleRuntimeOverviewRefresh() {
  const stream = cockpit.stream;
  if (stream.runtimeOverviewTimer !== null) {
    return;
  }
  stream.runtimeOverviewTimer = setTimeout(async () => {
    stream.runtimeOverviewTimer = null;
    try {
      await refreshRuntimeOverview();
    } catch (error) {
      // section renders its own errors
    }
  }, 300);
}

function setStreamStatus(label) {
  if (el.streamStatus) {
    setText(el.streamStatus, label);
    el.streamStatus.classList.toggle("live", label === "live");
  }
}

async function refreshRuntime() {
  clearError(el.runtimeError);

  try {
    const payload = await requestJson("/runtime/processes");
    renderKeyValues(el.runtimeList, [
      ["conflict_count", payload.conflict_count],
      ["report_only", payload.report_only],
      ["cleanup_automated", payload.cleanup_automated],
    ]);
    renderRuntimeObservations(Array.isArray(payload.observations) ? payload.observations : []);
  } catch (error) {
    clearNode(el.runtimeList);
    clearNode(el.runtimeObservationList);
    renderError(el.runtimeError, error);
  }
}

function renderRuntimeObservations(observations) {
  clearNode(el.runtimeObservationList);

  if (observations.length === 0) {
    renderEmpty(el.runtimeObservationList, "Brak obserwacji");
    return;
  }

  for (const observation of observations) {
    const row = document.createElement("div");
    row.className = "list-row";
    appendLine(row, `${observation.label || "process"} - ${observation.risk || "unknown"}`, "input-line");
    appendLine(row, observation.command || observation.process_name || "", "muted");
    el.runtimeObservationList.appendChild(row);
  }
}

const API_TOKEN_STORAGE_KEY = "jarvis-api-token";

function apiToken() {
  try {
    return window.localStorage.getItem(API_TOKEN_STORAGE_KEY) || "";
  } catch (error) {
    return "";
  }
}

function promptForApiToken() {
  const entered = window.prompt(
    "Jarvis API token required (see ~/.jarvis/runtime/api-token):",
    "",
  );
  if (entered === null) {
    return "";
  }
  const token = entered.trim();
  try {
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, token);
  } catch (error) {
    // storage unavailable - token works for this call only
  }
  return token;
}

async function requestJson(path, options = {}) {
  const method = options.method || "GET";
  const init = {
    method,
    headers: {},
  };

  // Every request carries the token now: private-data reads (conversations,
  // memory, settings) require it too, not just mutations (FIX-06 follow-up).
  const token = apiToken() || promptForApiToken();
  if (token) {
    init.headers["X-Jarvis-Token"] = token;
  }

  if (Object.prototype.hasOwnProperty.call(options, "body")) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(`${apiBase()}${path}`, init);
  } catch (error) {
    throw makeRequestError("Daemon unreachable", { route: path, detail: String(error) });
  }

  const payload = await readResponsePayload(response);
  if (response.status === 401) {
    try {
      window.localStorage.removeItem(API_TOKEN_STORAGE_KEY);
    } catch (error) {
      // ignore storage errors
    }
    throw makeRequestError("Unauthorized - set the Jarvis API token and retry", {
      route: path,
      status: response.status,
      payload,
    });
  }
  if (!response.ok) {
    throw makeRequestError(payload.error || `HTTP ${response.status}`, {
      route: path,
      status: response.status,
      payload,
    });
  }
  return payload;
}

async function readResponsePayload(response) {
  const text = await response.text();
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    return {
      error: "Non-JSON response",
      status: response.status,
      body: text.slice(0, 300),
    };
  }
}

function makeRequestError(message, detail) {
  const error = new Error(message);
  error.detail = detail;
  return error;
}

function apiBase() {
  return cockpit.apiBase.replace(/\/+$/, "");
}

function setOnline(online) {
  cockpit.online = online;
  // Żywa ramka karty jest wskaźnikiem online/offline (teal/czerwień) — nie ma
  // osobnej sekcji statusu; body.offline dodatkowo wygasza kompozytor.
  document.body.classList.toggle("offline", !online);
  applyStateFrame();
  setInteractiveEnabled(online);
  if (!online) {
    cockpit.voice.enabled = false;
    cockpit.voice.listening = false;
    cockpit.voice.leases = [];
    renderVoice();
  }
}

function setInteractiveEnabled(enabled) {
  const controls = [
    el.textInput,
    el.sendButton,
    el.memoryKind,
    el.memoryTitle,
    el.memoryPriority,
    el.memoryBody,
    el.createMemoryButton,
    el.brainAdapterSelect,
    el.switchBrainButton,
    el.refreshSettingsPreviewButton,
  ];

  for (const control of controls) {
    control.disabled = !enabled;
  }
  for (const control of [
    el.brainAdapterSelect,
    el.switchBrainButton,
    el.settingKey,
    el.settingValue,
    el.saveSettingButton,
  ]) {
    control.disabled = !enabled || POC_NO_PERSISTENCE_GUARD;
  }
  if (el.settingsPreviewSaveButton) {
    el.settingsPreviewSaveButton.disabled = true;
  }
}

function clearDynamicSections() {
  setPendingBadge(0);
  // Nieaktualne błędy sekcji nie mogą wisieć pod świeżym stanem offline —
  // jedyną diagnozą pozostaje healthError w Zaawansowane → Stan daemona.
  for (const box of [
    el.historyError,
    el.inputError,
    el.voiceError,
    el.approvalsError,
    el.memoryError,
    el.toolsError,
    el.settingsPreviewError,
    el.settingsError,
    el.runtimeOverviewError,
    el.eventsError,
    el.runtimeError,
  ]) {
    clearError(box);
  }
  setText(el.missionControlRefreshStatus, "");
  clearNode(el.conversationSelect);
  clearNode(el.turnList);
  clearNode(el.memoryList);
  clearNode(el.healthHumanList);
  clearNode(el.toolList);
  clearNode(el.approvalList);
  clearNode(el.missionControlModules);
  clearNode(el.missionControlChecklist);
  clearNode(el.voiceDoctorList);
  clearNode(el.providerDoctorList);
  clearNode(el.settingsPreviewList);
  clearNode(el.settingsList);
  clearNode(el.runtimeOverviewList);
  clearNode(el.brainAdapterSelect);
  clearNode(el.eventList);
  clearNode(el.runtimeList);
  clearNode(el.runtimeObservationList);
  if (el.voiceQueueList) {
    clearNode(el.voiceQueueList);
  }
  setText(el.brainAdapterLabel, "");
  cockpit.voice.listening = false;
  cockpit.voice.leases = [];
  renderVoice();
  const offlineOption = document.createElement("option");
  offlineOption.value = "";
  setText(offlineOption, "daemon offline");
  el.conversationSelect.appendChild(offlineOption);
  // Jeden mocny komunikat offline z akcją zamiast szarego "Daemon offline"
  // powtórzonego w każdej sekcji — czerwona ramka i pill niosą resztę.
  renderOfflineHero();
  renderEmpty(el.approvalList, "Podgląd zgód niedostępny, dopóki daemon nie wstanie.");
  renderMissionControl(missionControlOfflineSnapshot());
}

function renderOfflineHero() {
  clearNode(el.turnList);
  const hero = document.createElement("div");
  hero.className = "offline-hero";

  const title = document.createElement("p");
  title.className = "offline-title";
  setText(title, "Daemon nie odpowiada");

  const hint = document.createElement("p");
  hint.className = "offline-hint muted";
  setText(hint, "Uruchom go w terminalu: jarvis start — panel połączy się sam.");

  const retry = document.createElement("button");
  retry.type = "button";
  setText(retry, "Spróbuj teraz");
  retry.addEventListener("click", refreshAll);

  hero.append(title, hint, retry);
  el.turnList.appendChild(hero);
}

function renderKeyValues(node, rows) {
  clearNode(node);
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    setText(dt, label);
    setText(dd, displayValue(value));
    node.append(dt, dd);
  }
}

function renderError(node, error) {
  const payload = {
    error: error.message || "Request failed",
    detail: error.detail || null,
  };
  node.hidden = false;
  setText(node, compactJson(payload));
}

function clearError(node) {
  node.hidden = true;
  node.textContent = "";
}

function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function renderEmpty(node, message) {
  clearNode(node);
  const row = document.createElement("div");
  row.className = "empty-row";
  setText(row, message);
  node.appendChild(row);
}

function appendLine(parent, value, className) {
  const node = document.createElement("p");
  node.className = className;
  setText(node, value);
  parent.appendChild(node);
}

function argumentPreview(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (typeof text !== "string") {
    return String(value);
  }
  return text.length > 220 ? `${text.slice(0, 220)}…` : text;
}

function smallButton(label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "small-button";
  setText(button, label);
  return button;
}

function setBusy(button, busy) {
  button.disabled = busy || !cockpit.online;
  button.classList.toggle("busy", busy);
}

function setText(node, value) {
  // A missing/commented-out element (getElementById -> null) must NOT throw and
  // abort the whole status refresh — that once left the state pill stuck on
  // "unknown" while the daemon was healthy. Optional UI stays optional.
  if (!node) {
    return;
  }
  node.textContent = displayValue(value);
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  if (typeof value === "object") {
    return compactJson(value);
  }
  return String(value);
}

function compactJson(value) {
  return JSON.stringify(value, null, 2);
}

function shortId(value) {
  if (!value) {
    return "n/a";
  }
  const text = String(value);
  if (text.length <= 12) {
    return text;
  }
  return `${text.slice(0, 8)}...${text.slice(-4)}`;
}

// Węzeł czasu względnego: etykieta "2 min temu", pełna data w tooltipie,
// ISO w dataset.timestamp, żeby ticker mógł odświeżać etykiety w miejscu.
function timeNode(iso) {
  const node = document.createElement("time");
  if (iso) {
    node.dataset.timestamp = iso;
    node.title = formatFullDate(iso);
  }
  setText(node, formatRelative(iso));
  return node;
}

function refreshRelativeTimes() {
  for (const node of document.querySelectorAll("[data-timestamp]")) {
    setText(node, formatRelative(node.dataset.timestamp));
  }
}

function formatRelative(iso) {
  if (!iso) {
    return "?";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "?";
  }
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) {
    return formatClock(iso);
  }
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) {
    return "przed chwilą";
  }
  if (minutes < 60) {
    return `${minutes} min temu`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return hours === 1 ? "godzinę temu" : `${hours} godz. temu`;
  }
  return formatClock(iso);
}

function formatFullDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return String(iso);
  }
  return date.toLocaleString("pl-PL");
}

function formatClock(iso) {
  if (!iso) {
    return "?";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "?";
  }
  const sameDay = date.toDateString() === new Date().toDateString();
  const clock = date.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) {
    return clock;
  }
  const day = date.toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit" });
  return `${day} ${clock}`;
}
