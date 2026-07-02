"use strict";

const DEFAULT_API_BASE = "http://127.0.0.1:41741";

const cockpit = {
  apiBase: DEFAULT_API_BASE,
  online: false,
  selectedConversationId: null,
  approvedApprovals: new Map(),
  stream: {
    socket: null,
    base: null,
    lastEventId: 0,
    retryMs: 2000,
    reconnectTimer: null,
    approvalsTimer: null,
    settingsTimer: null,
  },
};

const STREAM_SUBPROTOCOL = "jarvis.v1";
const STREAM_TOKEN_SUBPROTOCOL_PREFIX = "jarvis-token.";
const STREAM_MAX_RETRY_MS = 15000;
const MAX_LIVE_EVENT_ROWS = 50;

const el = {};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  el.apiBaseInput.value = DEFAULT_API_BASE;
  bindEvents();
  refreshAll();
});

function bindElements() {
  const ids = [
    "onlineDot",
    "onlineLabel",
    "stateLabel",
    "refreshAllButton",
    "apiBaseInput",
    "healthStateList",
    "healthError",
    "textForm",
    "textInput",
    "sendButton",
    "inputResponse",
    "inputError",
    "refreshHistoryButton",
    "conversationList",
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
  ];

  for (const id of ids) {
    el[id] = document.getElementById(id);
  }
}

function bindEvents() {
  el.refreshAllButton.addEventListener("click", refreshAll);
  el.refreshHistoryButton.addEventListener("click", refreshHistory);
  el.refreshMemoryButton.addEventListener("click", refreshMemory);
  el.refreshToolsButton.addEventListener("click", refreshToolsAndApprovals);
  el.refreshSettingsButton.addEventListener("click", refreshSettings);
  el.switchBrainButton.addEventListener("click", switchBrain);
  el.settingsForm.addEventListener("submit", saveSetting);
  el.refreshEventsButton.addEventListener("click", refreshEvents);
  el.refreshRuntimeButton.addEventListener("click", refreshRuntime);
  el.textForm.addEventListener("submit", sendTextInput);
  el.memoryForm.addEventListener("submit", createMemoryBlock);
  el.apiBaseInput.addEventListener("change", () => {
    const nextBase = el.apiBaseInput.value.trim();
    cockpit.apiBase = nextBase || DEFAULT_API_BASE;
    el.apiBaseInput.value = cockpit.apiBase;
    cockpit.selectedConversationId = null;
    cockpit.approvedApprovals.clear();
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
    return;
  }

  await Promise.all([
    refreshHistory(),
    refreshMemory(),
    refreshToolsAndApprovals(),
    refreshSettings(),
    refreshEvents(),
    refreshRuntime(),
  ]);
  connectStream();
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
  el.inputResponse.textContent = "";

  const text = el.textInput.value.trim();
  if (!text || !cockpit.online) {
    return;
  }

  const body = { text, source: "panel" };
  if (cockpit.selectedConversationId) {
    body.conversation_id = cockpit.selectedConversationId;
  }

  setBusy(el.sendButton, true);
  try {
    const payload = await requestJson("/input/text", {
      method: "POST",
      body,
    });
    setText(el.inputResponse, payload.final_text || compactJson(payload));
    el.textInput.value = "";
    cockpit.selectedConversationId = payload.conversation_id || cockpit.selectedConversationId;
    await Promise.all([refreshHistory(), refreshEvents(), refreshToolsAndApprovals()]);
  } catch (error) {
    renderError(el.inputError, error);
  } finally {
    setBusy(el.sendButton, false);
  }
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
    if ((!cockpit.selectedConversationId || !hasSelected) && conversations.length > 0) {
      cockpit.selectedConversationId = conversations[0].id;
    }
    if (cockpit.selectedConversationId) {
      await refreshTurns(cockpit.selectedConversationId);
    } else {
      renderEmpty(el.turnList, "No turns");
    }
  } catch (error) {
    clearNode(el.conversationList);
    clearNode(el.turnList);
    renderError(el.historyError, error);
  }
}

async function refreshTurns(conversationId) {
  const query = `/turns?conversation_id=${encodeURIComponent(conversationId)}&limit=20`;
  const payload = await requestJson(query);
  const turns = Array.isArray(payload.turns) ? payload.turns : [];
  renderTurns(turns);
}

function renderConversations(conversations) {
  clearNode(el.conversationList);

  if (conversations.length === 0) {
    renderEmpty(el.conversationList, "No conversations");
    return;
  }

  for (const conversation of conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list-row conversation-row";
    button.setAttribute("role", "option");
    if (conversation.id === cockpit.selectedConversationId) {
      button.classList.add("selected");
      button.setAttribute("aria-selected", "true");
    }
    button.addEventListener("click", async () => {
      cockpit.selectedConversationId = conversation.id;
      renderConversations(conversations);
      clearError(el.historyError);
      try {
        await refreshTurns(conversation.id);
      } catch (error) {
        clearNode(el.turnList);
        renderError(el.historyError, error);
      }
    });

    const title = document.createElement("strong");
    setText(title, shortId(conversation.id));
    const meta = document.createElement("span");
    setText(meta, `${conversation.status || "unknown"} - ${conversation.turn_count || 0} turns`);

    button.append(title, meta);
    el.conversationList.appendChild(button);
  }
}

function renderTurns(turns) {
  clearNode(el.turnList);

  if (turns.length === 0) {
    renderEmpty(el.turnList, "No turns");
    return;
  }

  for (const turn of turns) {
    const row = document.createElement("article");
    row.className = "list-row";
    appendLine(row, `${turn.source || "unknown"} - ${turn.status || "unknown"}`, "muted");
    appendLine(row, turn.input_text || "", "input-line");
    appendLine(row, turn.final_text || "", "final-line");
    el.turnList.appendChild(row);
  }
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
    renderEmpty(el.memoryList, "No active memory");
    return;
  }

  for (const block of blocks) {
    const row = document.createElement("article");
    row.className = "list-row";
    appendLine(row, `${block.kind || "memory"} - priority ${block.priority ?? 0}`, "muted");
    appendLine(row, block.title || shortId(block.id), "input-line");
    appendLine(row, block.body || "", "final-line");

    const actions = document.createElement("div");
    actions.className = "row-actions";
    const disableButton = smallButton("Disable");
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
    actions.appendChild(disableButton);
    row.appendChild(actions);
    el.memoryList.appendChild(row);
  }
}

async function refreshToolsAndApprovals() {
  clearError(el.toolsError);

  try {
    const toolsPayload = await requestJson("/tools");
    const approvalsPayload = await requestJson("/approvals?limit=25");
    renderTools(Array.isArray(toolsPayload.tools) ? toolsPayload.tools : []);
    renderApprovals(Array.isArray(approvalsPayload.approvals) ? approvalsPayload.approvals : []);
  } catch (error) {
    clearNode(el.toolList);
    clearNode(el.approvalList);
    renderError(el.toolsError, error);
  }
}

function renderTools(tools) {
  clearNode(el.toolList);

  if (tools.length === 0) {
    renderEmpty(el.toolList, "No tools");
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

  const approved = Array.from(cockpit.approvedApprovals.values());
  if (pendingApprovals.length === 0 && approved.length === 0) {
    renderEmpty(el.approvalList, "No pending approvals");
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
  appendLine(row, `${title} - ${approval.risk || "unknown"} - ${mode}`, "input-line");
  appendLine(row, `id ${shortId(approval.id)} - ${approval.requested_by || "unknown"}`, "muted");

  const actions = document.createElement("div");
  actions.className = "row-actions";

  if (mode === "pending") {
    const approveButton = smallButton("Approve");
    approveButton.addEventListener("click", () => decideApproval(approval.id, "approve", approveButton));
    const rejectButton = smallButton("Reject");
    rejectButton.classList.add("danger");
    rejectButton.addEventListener("click", () => decideApproval(approval.id, "reject", rejectButton));
    actions.append(approveButton, rejectButton);
  } else {
    const executeButton = smallButton("Execute approved");
    executeButton.classList.add("strong");
    executeButton.addEventListener("click", () => executeApproval(approval.id, executeButton));
    actions.appendChild(executeButton);
  }

  row.appendChild(actions);
  return row;
}

async function decideApproval(approvalId, action, button) {
  clearError(el.toolsError);
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
    await Promise.all([refreshToolsAndApprovals(), refreshEvents()]);
  } catch (error) {
    renderError(el.toolsError, error);
  } finally {
    setBusy(button, false);
  }
}

async function executeApproval(approvalId, button) {
  clearError(el.toolsError);
  setBusy(button, true);
  try {
    await requestJson(`/approvals/${encodeURIComponent(approvalId)}/execute`, {
      method: "POST",
    });
    cockpit.approvedApprovals.delete(approvalId);
    await Promise.all([refreshToolsAndApprovals(), refreshEvents()]);
  } catch (error) {
    renderError(el.toolsError, error);
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
    renderEmpty(el.settingsList, "No settings");
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
    renderEmpty(el.eventList, "No events");
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
    renderEmpty(el.runtimeObservationList, "No observations");
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
const MUTATING_METHODS = new Set(["POST", "PATCH", "DELETE"]);

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

  if (MUTATING_METHODS.has(method)) {
    const token = apiToken() || promptForApiToken();
    if (token) {
      init.headers["X-Jarvis-Token"] = token;
    }
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
  if (response.status === 401 && MUTATING_METHODS.has(method)) {
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
  el.onlineDot.classList.toggle("online", online);
  el.onlineDot.classList.toggle("offline", !online);
  setText(el.onlineLabel, online ? "online" : "offline");
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
  clearNode(el.conversationList);
  clearNode(el.turnList);
  clearNode(el.memoryList);
  clearNode(el.toolList);
  clearNode(el.approvalList);
  clearNode(el.settingsList);
  clearNode(el.brainAdapterSelect);
  clearNode(el.eventList);
  clearNode(el.runtimeList);
  clearNode(el.runtimeObservationList);
  el.inputResponse.textContent = "";
  setText(el.brainAdapterLabel, "");
  renderEmpty(el.conversationList, "Daemon offline");
  renderEmpty(el.memoryList, "Daemon offline");
  renderEmpty(el.toolList, "Daemon offline");
  renderEmpty(el.settingsList, "Daemon offline");
  renderEmpty(el.eventList, "Daemon offline");
  renderEmpty(el.runtimeObservationList, "Daemon offline");
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
