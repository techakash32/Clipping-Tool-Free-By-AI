const PROVIDER_KEY = {
  anthropic: { field: "ANTHROPIC_API_KEY", label: "Anthropic API key" },
  openrouter: { field: "OPENROUTER_API_KEY", label: "OpenRouter API key" },
  groq: { field: "GROQ_API_KEY", label: "Groq API key" },
  nvidia: { field: "NVIDIA_API_KEY", label: "NVIDIA API key" },
  gemini: { field: "GEMINI_API_KEY", label: "Gemini API key" },
  openai: { field: "OPENAI_API_KEY", label: "OpenAI-compatible API key" },
  ollama: null,
};

// ── tabs ──
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "history") loadHistory();
  });
});

// ── setup: provider + key ──
const providerSelect = document.getElementById("provider");
const keyFields = document.getElementById("key-fields");
let currentSetFlags = {};

function renderKeyField() {
  keyFields.innerHTML = "";
  const info = PROVIDER_KEY[providerSelect.value];
  if (!info) return;
  const row = document.createElement("div");
  row.className = "row";
  const already = currentSetFlags[info.field];
  row.innerHTML = `
    <label>${info.label}</label>
    <input type="password" id="key-input" placeholder="${already ? "already saved — paste to replace" : "paste your key"}" />
  `;
  keyFields.appendChild(row);
}
providerSelect.addEventListener("change", renderKeyField);

async function loadCredentials() {
  const res = await fetch("/api/credentials");
  const data = await res.json();
  currentSetFlags = data.set || {};
  providerSelect.value = data.provider || "anthropic";
  renderKeyField();

  const secretBadge = document.getElementById("yt-secret-status");
  const connectBadge = document.getElementById("yt-connect-status");
  secretBadge.textContent = data.youtube_secret_uploaded ? "client_secret.json uploaded" : "client_secret.json missing";
  secretBadge.className = "badge" + (data.youtube_secret_uploaded ? " ok" : "");
  connectBadge.textContent = data.youtube_connected ? "connected" : "not connected";
  connectBadge.className = "badge" + (data.youtube_connected ? " ok" : "");
}

document.getElementById("save-credentials").addEventListener("click", async () => {
  const provider = providerSelect.value;
  const info = PROVIDER_KEY[provider];
  const keys = {};
  if (info) {
    const val = document.getElementById("key-input").value.trim();
    if (val) keys[info.field] = val;
  }
  const res = await fetch("/api/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, keys }),
  });
  const msg = document.getElementById("save-msg");
  if (res.ok) {
    msg.textContent = "Saved.";
    loadCredentials();
  } else {
    msg.textContent = "Failed to save.";
  }
  setTimeout(() => (msg.textContent = ""), 3000);
});

// ── youtube ──
document.getElementById("yt-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/youtube/secret", { method: "POST", body: fd });
  const data = await res.json();
  if (res.ok && data.ok) {
    loadCredentials();
  } else {
    alert(data.error || "Upload failed.");
  }
});

document.getElementById("yt-connect").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Opening Google sign-in…";
  try {
    const res = await fetch("/api/youtube/connect", { method: "POST" });
    const data = await res.json();
    if (res.ok && data.ok) {
      alert("Connected as " + data.channel);
    } else {
      alert(data.error || "Could not connect.");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect YouTube";
    loadCredentials();
  }
});

document.getElementById("yt-disconnect").addEventListener("click", async () => {
  await fetch("/api/youtube/disconnect", { method: "POST" });
  loadCredentials();
});

// ── dashboard: stages ──
const STAGES = ["Find", "Review", "Edit", "Finishing review", "Upload", "Stats"];
const stageList = document.getElementById("stage-list");
function renderStages(activeStage, status) {
  stageList.innerHTML = "";
  const activeIdx = STAGES.indexOf(activeStage);
  STAGES.forEach((s, i) => {
    const li = document.createElement("li");
    li.textContent = s;
    if (status === "running" && i < activeIdx) li.classList.add("done");
    else if (status === "running" && i === activeIdx) li.classList.add("active");
    else if (status === "done") li.classList.add("done");
    stageList.appendChild(li);
  });
}
renderStages(null, "idle");

const runBtn = document.getElementById("run-btn");
const runStatus = document.getElementById("run-status");
const logView = document.getElementById("log-view");

function appendLog(line) {
  logView.textContent += line + "\n";
  logView.scrollTop = logView.scrollHeight;
}

function setRunStatus(status) {
  runStatus.textContent = status;
  runStatus.className = "badge" + (status === "running" ? " run" : status === "done" ? " ok" : status === "error" ? " err" : "");
  runBtn.disabled = status === "running";
}

runBtn.addEventListener("click", async () => {
  const url = document.getElementById("run-url").value.trim();
  const autoYes = document.getElementById("run-autoyes").checked;
  if (!url) { alert("Paste a podcast URL first."); return; }
  logView.textContent = "";
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "auto", url, auto_yes: autoYes }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || "Could not start run."); return; }
  setRunStatus("running");
  pollStatus();
});

// ── status polling, with a heartbeat note for silent stages
// (transcription/rendering produce no log lines while they run, so this
// tells the user it's still alive instead of looking frozen) ──
const heartbeat = document.getElementById("heartbeat");
let pollTimer = null;
async function pollStatus() {
  clearTimeout(pollTimer);
  const res = await fetch("/api/status");
  const data = await res.json();
  setRunStatus(data.status);
  renderStages(data.stage, data.status);
  if (data.status === "running") {
    const s = data.seconds_since_last_log;
    if (heartbeat) {
      if (s != null && s > 20) {
        heartbeat.textContent = `still working — no new log output for ${s}s (transcription and rendering are silent while they run, this is normal)`;
        heartbeat.style.display = "block";
      } else {
        heartbeat.style.display = "none";
      }
    }
    pollTimer = setTimeout(pollStatus, 2000);
  } else if (heartbeat) {
    heartbeat.style.display = "none";
  }
}
pollStatus();

// ── live logs via websocket ──
function connectLogSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/logs`);
  ws.onmessage = (evt) => appendLog(evt.data);
  ws.onclose = () => setTimeout(connectLogSocket, 3000);
}
connectLogSocket();

// ── history ──
async function loadHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  const tbody = document.querySelector("#history-table tbody");
  tbody.innerHTML = "";
  if (!data.ok) {
    tbody.innerHTML = `<tr><td colspan="5">${data.error || "No data yet."}</td></tr>`;
    return;
  }
  if (!data.clips.length) {
    tbody.innerHTML = `<tr><td colspan="5">No clips processed yet.</td></tr>`;
    return;
  }
  data.clips.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${c.title || ""}</td><td>${c.source_title || ""}</td><td>${c.score ?? ""}</td><td>${c.status || ""}</td><td>${(c.created_at || "").slice(0, 16).replace("T", " ")}</td>`;
    tbody.appendChild(tr);
  });
}

loadCredentials();