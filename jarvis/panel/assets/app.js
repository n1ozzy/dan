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
const RUNTIME_OVERVIEW_FIELD_STATUS_ORDER = ["ok", "missing", "invalid", "unknown"];
const RUNTIME_OVERVIEW_READINESS = Object.freeze({
  OK: "ok",
  MISSING: "missing",
  INVALID: "invalid",
  UNKNOWN: "unknown",
});
const RUNTIME_OVERVIEW_FIELD_SOURCES = Object.freeze({
  health: "Health",
  state: "State",
  settings: "Settings",
  brain: "Brain adapters",
  audio: "Audio devices",
  voice: "Voice listening",
  voiceQueue: "Voice queue",
  tools: "Tools registry",
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
  const usable = cockpit.online && cockpit.voice.enabled;
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

  const endpoints = [
    ["health", "/health"],
    ["state", "/state"],
    ["settings", "/settings"],
    ["brain", "/brain/adapters"],
    ["audio", "/audio/devices"],
    ["voice", "/voice/listening"],
    ["voiceQueue", "/voice/queue?limit=12"],
    ["tools", "/tools"],
    ["events", "/events?latest=true&limit=50"],
  ];
  const settled = await Promise.allSettled(
    endpoints.map((entry) => requestJson(entry[1])),
  );
  const snapshot = { failures: [], sourceStatus: {} };
  for (let index = 0; index < endpoints.length; index += 1) {
    const [key, path] = endpoints[index];
    const result = settled[index];
    if (result.status === "fulfilled") {
      snapshot[key] = result.value || {};
      snapshot.sourceStatus[key] = { ok: true, path };
    } else {
      snapshot.failures.push(path);
      snapshot.sourceStatus[key] = { ok: false, path };
    }
  }

  renderRuntimeOverview(snapshot);
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
    title: "Brain/Provider",
    fields: [
      field("configured adapter/provider", "settings", (ctx) => configuredSetting(ctx, [
        "brain.default_adapter",
        "brain.adapter",
        "brain.provider",
        "provider.adapter",
      ])),
      field("effective adapter/provider", "brain", (ctx) => ctx.activeAdapter, {
        readiness: (ctx, value) => adapterReadiness(ctx.adapters, value),
        dependency: (ctx, value) => adapterDependency(ctx.adapters, value),
      }),
      field("configured model", "settings", (ctx) => configuredSetting(ctx, [
        "brain.model",
        "provider.model",
        "model",
        "llm.model",
      ])),
      field("effective model(s)", "brain", (ctx) => adapterModels(ctx.adapters, ctx.activeAdapter)),
      field("registered adapters", "brain", (ctx) => adapterNames(ctx.adapters)),
      field("effort", "settings", (ctx) => configuredSetting(ctx, [
        "brain.effort",
        "provider.effort",
        "reasoning.effort",
        "effort",
      ])),
      field("fast mode", "settings", (ctx) => configuredSetting(ctx, [
        "brain.fast_mode",
        "provider.fast_mode",
        "fast_mode",
      ])),
      field("context budget/window", "settings", (ctx) => configuredSetting(ctx, [
        "brain.context_budget",
        "brain.context_window",
        "context.budget",
        "context.window",
        "memory.context_budget",
      ])),
      field("provider sessions are memory", "contract", () => "no; daemon owns memory"),
      field("Claude config", "brain", (ctx) =>
        adapterRegistration(ctx.adapters, ["claude_cli", "claude_cli_warm"]),
      ),
      field("Codex config", "brain", (ctx) => adapterRegistration(ctx.adapters, ["codex_cli"])),
      field("Grok/local/Bielik/Mistral/Ollama", "brain", (ctx) =>
        adapterRegistration(ctx.adapters, ["grok", "local", "bielik", "mistral", "ollama"]),
      ),
      field("mock", "brain", (ctx) =>
        adapterRegistration(ctx.adapters, ["mock"], "Developer/Test registered"),
      ),
    ],
  },
  {
    title: "Voice Runtime",
    fields: [
      field("listening now", "voice", (ctx) => ctx.voice.listening),
      field("audio backend", "audio", (ctx) => ctx.audio.backend),
      field("input device", "audio", (ctx) =>
        firstPresent(ctx.audio.input_device, ctx.audio.preferred_input),
      ),
      field("output device", "audio", (ctx) =>
        firstPresent(ctx.audio.output_device, ctx.audio.output_policy),
      ),
      field("input/output policy", "audio", (ctx) =>
        firstPresent(ctx.audio.input_policy, ctx.audio.output_policy, configuredSetting(ctx, [
          "voice.input_policy",
          "voice.output_policy",
          "audio.input_policy",
          "audio.output_policy",
        ])),
      ),
      field("bluetooth mic allowed", "audio", (ctx) => ctx.audio.allow_bluetooth_microphone),
      field("configured TTS", "settings", (ctx) => configuredTts(ctx), {
        readiness: voiceConfiguredReadiness,
        warnings: voiceConfiguredWarnings("TTS"),
      }),
      field("effective TTS", "events", (ctx) => effectiveTts(ctx), {
        readiness: voiceEffectiveReadiness,
      }),
      field("configured STT", "settings", (ctx) => configuredStt(ctx), {
        readiness: voiceConfiguredReadiness,
        warnings: voiceConfiguredWarnings("STT"),
      }),
      field("effective STT", "events", (ctx) => effectiveStt(ctx), {
        readiness: voiceEffectiveReadiness,
      }),
      field("voice/model/speaker", "settings", (ctx) => configuredVoiceIdentity(ctx), {
        readiness: voiceIdentityReadiness,
      }),
      field("playback engine", "audio", (ctx) => playbackEngine(ctx)),
      field("recorder/input engine", "audio", (ctx) => recorderEngine(ctx)),
      field("speech speed/rate", "settings", (ctx) => configuredSetting(ctx, [
        "voice.speed",
        "voice.rate",
        "tts.speed",
        "tts.rate",
      ])),
      field("pauses/timing/chunking", "settings", (ctx) => configuredSetting(ctx, [
        "voice.pause_ms",
        "voice.chunking",
        "tts.pause_ms",
        "tts.chunking",
      ])),
      field("PTT mode/hotkey", "settings", (ctx) => configuredSetting(ctx, [
        "voice.ptt_mode",
        "voice.ptt.hotkey",
        "ptt.mode",
        "ptt.hotkey",
      ])),
      field("mute mic on PTT", "settings", (ctx) => configuredSetting(ctx, [
        "voice.mute_mic_on_ptt",
        "ptt.mute_mic",
      ])),
      field("barge-in/cancel policy", "events", (ctx) => latestBargeInSummary(ctx.events)),
      field("broker enabled", "settings", (ctx) => configuredSetting(ctx, [
        "voice.broker_enabled",
        "voice_broker.enabled",
        "broker.enabled",
      ])),
      field("speak responses", "settings", (ctx) => configuredSetting(ctx, [
        "voice.speak_responses",
        "tts.speak_responses",
        "speak_responses",
      ])),
      field("queue counts", "voiceQueue", (ctx) => voiceQueueCounts(ctx.queueRows)),
      field("voice queue", "voiceQueue", (ctx) => voiceQueueSummary(ctx.queueRows, ctx.voiceQueue)),
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
            : RUNTIME_OVERVIEW_READINESS.MISSING,
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
      field("last failure source", "events", (ctx) => runtimeOverviewSourceFailures(ctx.failures)),
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
  const brain = safeObject(snapshot.brain);
  const health = safeObject(snapshot.health);
  const state = safeObject(snapshot.state);
  const audio = safeObject(snapshot.audio).audio || {};
  const voice = safeObject(snapshot.voice);
  const queueRows = Array.isArray(safeObject(snapshot.voiceQueue).voice_queue)
    ? snapshot.voiceQueue.voice_queue
    : [];
  const tools = Array.isArray(safeObject(snapshot.tools).tools) ? snapshot.tools.tools : [];
  const events = Array.isArray(safeObject(snapshot.events).events) ? snapshot.events.events : [];
  const adapters = Array.isArray(brain.adapters) ? brain.adapters : [];
  const activeAdapter = firstPresent(brain.current, state.brain_adapter, health.brain_adapter);
  const failures = Array.isArray(snapshot.failures) ? snapshot.failures : [];
  const sourceStatus = safeObject(snapshot.sourceStatus);
  const voiceEnabled = firstPresent(voice.voice_enabled, state.voice_enabled, health.voice_enabled);

  return {
    readOnlyMode: RUNTIME_OVERVIEW_READ_ONLY,
    settings,
    brain,
    health,
    state,
    audio,
    voice,
    voiceQueue: safeObject(snapshot.voiceQueue),
    queueRows,
    tools,
    events,
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
  return configuredSetting(context, [
    "voice.default_tts",
    "voice.tts",
    "voice.tts.engine",
    "voice.tts_provider",
    "tts.engine",
    "tts.provider",
    "default_tts",
  ]);
}

function configuredStt(context) {
  return configuredSetting(context, [
    "voice.default_stt",
    "voice.stt",
    "voice.stt.engine",
    "voice.stt_provider",
    "stt.engine",
    "stt.provider",
    "default_stt",
  ]);
}

function effectiveTts(context) {
  return latestEventPayloadValue(context.events, ["tts_engine", "tts_provider", "default_tts"]);
}

function effectiveStt(context) {
  return latestEventPayloadValue(context.events, ["stt_engine", "stt_provider", "default_stt"]);
}

function configuredVoiceIdentity(context) {
  return configuredSetting(context, [
    "voice.voice_id",
    "voice.voice_model",
    "voice.voice_profile",
    "voice.model",
    "voice.profile",
    "tts.voice_id",
    "tts.model",
    "voice_id",
  ]);
}

function playbackEngine(context) {
  return firstPresent(
    context.audio.playback_engine,
    context.audio.output_engine,
    configuredSetting(context, [
      "voice.playback_engine",
      "audio.playback_engine",
      "playback.command",
      "playback.engine",
    ]),
  );
}

function recorderEngine(context) {
  return firstPresent(
    context.audio.recorder_engine,
    context.audio.input_engine,
    configuredSetting(context, [
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
  return firstPresent(value)
    ? RUNTIME_OVERVIEW_READINESS.OK
    : RUNTIME_OVERVIEW_READINESS.UNKNOWN;
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
  const needsIdentity = Boolean(configuredTts(context) || effectiveTts(context));
  if (needsIdentity && !firstPresent(value)) {
    return RUNTIME_OVERVIEW_READINESS.MISSING;
  }
  return firstPresent(value)
    ? RUNTIME_OVERVIEW_READINESS.OK
    : RUNTIME_OVERVIEW_READINESS.UNKNOWN;
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
    const payload = await requestJson("/events?after_id=0&limit=50");
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

function renderEvents(events) {
  const rows = Array.isArray(events) ? events : [];
  cockpit.lastEvents = rows;
  clearNode(el.eventList);

  const filter = cockpit.logFilter || "all";
  const shown = rows.filter((event) => eventMatchesFilter(event.type, filter));
  if (shown.length === 0) {
    renderEmpty(
      el.eventList,
      filter === "all" ? "Brak zdarzeń" : "Brak zdarzeń w tym filtrze",
    );
    return;
  }

  const latestFirst = [...shown].reverse();
  for (const event of latestFirst) {
    el.eventList.appendChild(eventRow(event));
  }
}

// Wiersz dziennika po ludzku: ludzka etykieta zdarzenia + meta
// #id · źródło · czas względny (mono, najmniej ważna linijka).
function eventRow(event) {
  const row = document.createElement("div");
  row.className = "list-row";
  appendLine(row, eventLabel(event.type), "input-line");
  const summary = eventPayloadSummary(event.payload || {});
  if (summary) {
    appendLine(row, summary, "payload-line");
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
    const latestId = Number(frame.latest_event_id);
    if (Number.isFinite(latestId) && latestId > cockpit.stream.lastEventId) {
      cockpit.stream.lastEventId = latestId;
    }
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
  if (Array.isArray(cockpit.lastEvents)) {
    cockpit.lastEvents.push(event);
    if (cockpit.lastEvents.length > MAX_LIVE_EVENT_ROWS * 2) {
      cockpit.lastEvents.shift();
    }
  }
  // Aktywny filtr obowiązuje też live-append — inaczej dziennik „przecieka”.
  if (!eventMatchesFilter(event.type, cockpit.logFilter || "all")) {
    return;
  }
  const emptyRow = el.eventList.querySelector(".empty-row");
  if (emptyRow) {
    emptyRow.remove();
  }
  el.eventList.insertBefore(eventRow(event), el.eventList.firstChild);
  while (el.eventList.children.length > MAX_LIVE_EVENT_ROWS) {
    el.eventList.removeChild(el.eventList.lastChild);
  }
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
    el.settingKey,
    el.settingValue,
    el.saveSettingButton,
  ];

  for (const control of controls) {
    control.disabled = !enabled;
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
    el.settingsError,
    el.runtimeOverviewError,
    el.eventsError,
    el.runtimeError,
  ]) {
    clearError(box);
  }
  clearNode(el.conversationSelect);
  clearNode(el.turnList);
  clearNode(el.memoryList);
  clearNode(el.healthHumanList);
  clearNode(el.toolList);
  clearNode(el.approvalList);
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
