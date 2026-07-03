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
  // Tryb "nowa rozmowa": nie auto-wybieraj najnowszej rozmowy przy refreshu,
  // dopóki operator nie wyśle pierwszej wiadomości.
  composingNew: false,
  healthRetryTimer: null,
  voice: {
    enabled: false,
    listening: false,
    leases: [],
    pttActive: false,
  },
  stream: {
    socket: null,
    base: null,
    lastEventId: 0,
    retryMs: 2000,
    reconnectTimer: null,
    approvalsTimer: null,
    settingsTimer: null,
    voiceTimer: null,
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

function bindElements() {
  const ids = [
    "stateLabel",
    "refreshAllButton",
    "apiBaseInput",
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
    "approvalsCard",
    "approvalsBadge",
    "toolsError",
    "refreshSettingsButton",
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
    "streamStatus",
    "eventList",
    "eventsError",
    "refreshRuntimeButton",
    "runtimeList",
    "runtimeObservationList",
    "runtimeError",
    "pttButton",
    "listenToggle",
    "voiceStatus",
    "voiceError",
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
  el.switchBrainButton.addEventListener("click", switchBrain);
  el.settingsForm.addEventListener("submit", saveSetting);
  el.refreshEventsButton.addEventListener("click", refreshEvents);
  el.refreshRuntimeButton.addEventListener("click", refreshRuntime);
  el.textForm.addEventListener("submit", sendTextInput);
  el.textInput.addEventListener("keydown", (event) => {
    // Enter sends; Shift+Enter keeps inserting a newline.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el.textForm.requestSubmit();
    }
  });
  el.approvalsBadge.addEventListener("click", () => {
    // Badge to sygnał; klik prowadzi prosto do kart zgód.
    el.approvalsCard.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  el.newConversationButton.addEventListener("click", () => {
    cockpit.selectedConversationId = null;
    cockpit.composingNew = true;
    ensureNewConversationOption();
    renderEmpty(el.turnList, "Nowa rozmowa — napisz pierwszą wiadomość poniżej.");
    el.textInput.focus();
  });
  el.pttButton.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (el.pttButton.setPointerCapture && event.pointerId !== undefined) {
      el.pttButton.setPointerCapture(event.pointerId);
    }
    pttDown();
  });
  el.pttButton.addEventListener("pointerup", () => pttUp());
  el.pttButton.addEventListener("pointercancel", () => pttUp());
  el.pttButton.addEventListener("keydown", (event) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      pttDown();
    }
  });
  el.pttButton.addEventListener("keyup", (event) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      pttUp();
    }
  });
  el.listenToggle.addEventListener("click", toggleListenLock);
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
    refreshHistory(),
    refreshMemory(),
    refreshToolsAndApprovals(),
    refreshSettings(),
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

function renderVoice() {
  const usable = cockpit.online && cockpit.voice.enabled;
  el.pttButton.disabled = !usable;
  el.listenToggle.disabled = !usable;

  const locked = cockpit.voice.leases.some((lease) => lease.mode === "locked");
  setText(el.listenToggle, locked ? "Wyłącz nasłuch" : "Włącz nasłuch");
  el.listenToggle.classList.toggle("active", locked);

  let status = "mikrofon martwy";
  if (!cockpit.online) {
    status = "daemon offline";
  } else if (!cockpit.voice.enabled) {
    status = "głos wyłączony w configu";
  } else if (cockpit.voice.listening) {
    const holding = cockpit.voice.leases.some((lease) => lease.mode === "hold");
    status = holding ? "słucha (PTT)" : "nasłuch ciągły aktywny";
  }
  setText(el.voiceStatus, status);
  el.voiceStatus.classList.toggle("live", cockpit.voice.listening);
}

async function pttDown() {
  if (cockpit.voice.pttActive || !cockpit.online || !cockpit.voice.enabled) {
    return;
  }
  cockpit.voice.pttActive = true;
  el.pttButton.classList.add("talking");
  clearError(el.voiceError);
  try {
    await requestJson("/voice/ptt/down", { method: "POST", body: { source: "ptt" } });
  } catch (error) {
    cockpit.voice.pttActive = false;
    el.pttButton.classList.remove("talking");
    renderError(el.voiceError, error);
  }
  await refreshVoice();
}

async function pttUp() {
  if (!cockpit.voice.pttActive) {
    return;
  }
  cockpit.voice.pttActive = false;
  el.pttButton.classList.remove("talking");
  try {
    await requestJson("/voice/ptt/up", { method: "POST", body: { source: "ptt" } });
  } catch (error) {
    renderError(el.voiceError, error);
  }
  await refreshVoice();
}

async function toggleListenLock() {
  if (!cockpit.online || !cockpit.voice.enabled) {
    return;
  }
  const locked = cockpit.voice.leases.some((lease) => lease.mode === "locked");
  const path = locked ? "/voice/listen/unlock" : "/voice/listen/lock";
  setBusy(el.listenToggle, true);
  clearError(el.voiceError);
  try {
    await requestJson(path, { method: "POST", body: {} });
  } catch (error) {
    renderError(el.voiceError, error);
  } finally {
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
    syncPendingApprovals(merged.pending_approval_count);
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
    clearNode(el.healthStateList);
    renderError(el.healthError, error);
    return false;
  }
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

  // Optymistyczny dymek: wysłana wiadomość od razu ląduje w czacie, żeby
  // kompozytor zachowywał się jak czat, a nie formularz z osobnym wynikiem.
  appendPendingUserBubble(text);

  setBusy(el.sendButton, true);
  try {
    const payload = await requestJson("/input/text", {
      method: "POST",
      body,
    });
    el.textInput.value = "";
    cockpit.selectedConversationId = payload.conversation_id || cockpit.selectedConversationId;
    cockpit.composingNew = false;
    await Promise.all([refreshHistory(), refreshEvents(), refreshToolsAndApprovals()]);
  } catch (error) {
    renderError(el.inputError, error);
  } finally {
    setBusy(el.sendButton, false);
  }
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
    const payload = await requestJson("/memory?active_only=true&limit=25");
    const blocks = Array.isArray(payload.memory) ? payload.memory : [];
    renderMemory(blocks);
  } catch (error) {
    clearNode(el.memoryList);
    renderError(el.memoryError, error);
  }
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
    appendLine(row, `${block.kind || "memory"} - priorytet ${block.priority ?? 0}`, "muted");
    appendLine(row, block.title || shortId(block.id), "input-line");
    appendLine(row, block.body || "", "final-line");

    // Pochodzenie bloku: kto zaproponował i kto promował (auto-pamięć przez
    // approvals) — bez tego nie widać, które notatki wpisał model.
    const metadata = block.metadata || {};
    if (metadata.proposed_by || metadata.promoted_by) {
      appendLine(
        row,
        `proposed_by: ${metadata.proposed_by || "n/a"} · promoted_by: ${metadata.promoted_by || "n/a"}`,
        "muted",
      );
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

function setPendingBadge(count) {
  cockpit.pendingApprovalCount = count;
  document.body.classList.toggle("has-pending", count > 0);
  if (!el.approvalsBadge) {
    return;
  }
  el.approvalsBadge.hidden = count === 0;
  setText(el.approvalsBadge, `${count} ${approvalLabel(count)}`);
  el.approvalsBadge.title = `Czeka na zgodę: ${count} — kliknij, żeby przejść do kart zgód`;
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
    appendLine(row, `${tool.name || "tool"} - ${tool.risk || "unknown"}`, "input-line");
    appendLine(row, tool.description || "", "muted");
    el.toolList.appendChild(row);
  }
}

function renderApprovals(pendingApprovals) {
  clearNode(el.approvalList);
  setPendingBadge(pendingApprovals.length);

  const approved = Array.from(cockpit.approvedApprovals.values());
  if (pendingApprovals.length === 0 && approved.length === 0) {
    renderEmpty(el.approvalList, "Nic nie czeka na zgodę");
    return;
  }

  for (const approval of pendingApprovals) {
    el.approvalList.appendChild(approvalCard(approval, "pending"));
  }
  for (const approval of approved) {
    el.approvalList.appendChild(approvalCard(approval, "approved"));
  }
}

function approvalCard(approval, mode) {
  const row = document.createElement("article");
  row.className = "list-row approval-row";
  const payload = approval.payload || {};
  const title = payload.tool_name || approval.action_type || approval.id;
  const modeLabel = mode === "pending" ? "czeka na zgodę" : "zatwierdzona";
  appendLine(row, `${title} - ${approval.risk || "unknown"} - ${modeLabel}`, "input-line");
  appendLine(row, `id ${shortId(approval.id)} - ${approval.requested_by || "unknown"}`, "muted");
  for (const [key, value] of Object.entries(payload.arguments || {})) {
    appendLine(row, `${key}: ${argumentPreview(value)}`, "argument-line muted");
  }

  const actions = document.createElement("div");
  actions.className = "row-actions";

  if (mode === "pending") {
    const approveButton = smallButton("Zatwierdź");
    approveButton.classList.add("strong");
    approveButton.addEventListener("click", () => decideApproval(approval.id, "approve", approveButton));
    const rejectButton = smallButton("Odrzuć");
    rejectButton.classList.add("danger");
    rejectButton.addEventListener("click", () => decideApproval(approval.id, "reject", rejectButton));
    actions.append(approveButton, rejectButton);
  } else {
    const executeButton = smallButton("Wykonaj zatwierdzone");
    executeButton.classList.add("strong");
    executeButton.addEventListener("click", () => executeApproval(approval.id, executeButton));
    actions.appendChild(executeButton);
  }

  row.appendChild(actions);
  return row;
}

async function decideApproval(approvalId, action, button) {
  clearError(el.approvalsError);
  setBusy(button, true);
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
    setBusy(button, false);
  }
}

async function executeApproval(approvalId, button) {
  clearError(el.approvalsError);
  setBusy(button, true);
  try {
    await requestJson(`/approvals/${encodeURIComponent(approvalId)}/execute`, {
      method: "POST",
    });
    cockpit.approvedApprovals.delete(approvalId);
    await Promise.all([refreshApprovals(), refreshEvents()]);
  } catch (error) {
    renderError(el.approvalsError, error);
  } finally {
    setBusy(button, false);
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
    `current ${payload.current || "n/a"} - default ${payload.default || "n/a"}`,
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
  clearNode(el.eventList);

  if (events.length === 0) {
    renderEmpty(el.eventList, "Brak zdarzeń");
    return;
  }

  const latestFirst = [...events].reverse();
  for (const event of latestFirst) {
    el.eventList.appendChild(eventRow(event));
  }
}

function eventRow(event) {
  const row = document.createElement("div");
  row.className = "list-row";
  appendLine(row, `#${event.id} - ${event.type || "event"}`, "input-line");
  appendLine(row, event.source || event.created_at || "", "muted");
  return row;
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
    setStreamStatus("live");
  });
  socket.addEventListener("message", (message) => {
    handleStreamMessage(message.data);
  });
  socket.addEventListener("close", () => {
    if (stream.socket === socket) {
      stream.socket = null;
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
  }
  if (type.startsWith("approval.") || type.startsWith("tool.")) {
    scheduleApprovalsRefresh();
  }
  if (type.startsWith("brain.")) {
    scheduleSettingsRefresh();
  }
  if (type.startsWith("listening.")) {
    scheduleVoiceRefresh();
  }
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

function prependLiveEvent(event) {
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
  // Animowana ramka panelu czyta stan z <body> — to ona jest wskaźnikiem
  // online/offline (zielona/czerwona), nie osobna sekcja statusu.
  document.body.classList.toggle("offline", !online);
  setInteractiveEnabled(online);
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
    el.eventsError,
    el.runtimeError,
  ]) {
    clearError(box);
  }
  clearNode(el.conversationSelect);
  clearNode(el.turnList);
  clearNode(el.memoryList);
  clearNode(el.toolList);
  clearNode(el.approvalList);
  clearNode(el.settingsList);
  clearNode(el.brainAdapterSelect);
  clearNode(el.eventList);
  clearNode(el.runtimeList);
  clearNode(el.runtimeObservationList);
  setText(el.brainAdapterLabel, "");
  cockpit.voice.listening = false;
  cockpit.voice.leases = [];
  cockpit.voice.pttActive = false;
  el.pttButton.classList.remove("talking");
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

