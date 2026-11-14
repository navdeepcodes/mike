// Mike — interface logic. Talks to server.py over /ws; to Mike.app through window.webkit.
import { PETS, PET_ORDER, NONE, preview } from "/static/pets.js";
import { Code } from "/static/code.js";

// Element lookup by id, re-resolved if a node was replaced (e.g. the greeting after "New conversation").
const el = new Proxy({}, {
  get(cache, k) { const n = cache[k]; if (n && n.isConnected) return n; return (cache[k] = document.getElementById(k)); },
});

// Native app bridge (Mike.app). Harmless in a normal browser.
const native = window.webkit?.messageHandlers?.mike;
const toNative = (msg) => { try { native?.postMessage(msg); } catch {} };

const pref = {
  get(k, d) { try { const v = localStorage.getItem(`mike.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`mike.${k}`, JSON.stringify(v)); } catch {} },
};

const S = {
  ws: null, state: "offline", connected: false,
  micOn: true, micOk: false, micPending: false, micError: "", micStream: null, micNode: null, workletLoaded: false,
  camPresence: pref.get("camPresence", false), camStream: null, camTimer: null,
  audioCtx: null, analyser: null, hearing: false,
  gen: 0, speaking: "", lastTool: "", turn: null, confirmId: null, duck: false,
  settings: {}, models: [], pet: "mike", petMode: "active", view: "chat", userName: "", historyDay: "", dataDir: "",
};

// ------------------------------------------------------------------ theme
function applyTheme(t = pref.get("theme", "dark")) {
  const dark = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.querySelectorAll("#setTheme button").forEach((b) => b.classList.toggle("on", b.dataset.v === t));
  toNative({ type: "theme", dark });
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme());
applyTheme();

// ------------------------------------------------------------------ greeting
function greet() {
  if (!el.greeting) return;
  const h = new Date().getHours();
  const part = h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  el.greeting.textContent = S.userName ? `${part}, ${S.userName}` : part;
}
setInterval(greet, 60_000);

// ------------------------------------------------------------------ state
const STATUS = { offline: "Reconnecting", booting: "Starting", idle: "Ready", listening: "Listening", thinking: "Thinking",
  speaking: "Speaking", confirming: "Needs your OK", elsewhere: "Open in another window" };
function caption(text) { el.caption.textContent = text || ""; el.caption.classList.toggle("show", !!text); }
function renderStatus() {
  el.statusText.textContent = STATUS[S.state] || S.state;
  const busy = S.state === "thinking" || S.state === "speaking";
  el.stopBtn.classList.toggle("hidden", !busy);
  el.sendBtn.classList.toggle("hidden", busy);
  el.talkText.textContent = S.state === "listening" ? (S.hearing ? "Hearing you" : "Listening") : busy ? "Stop" : "Talk";
  switch (S.state) {
    case "listening": caption(S.micOk && S.micOn ? "" : "Mic is off. Type your follow-up."); break;
    case "thinking": caption(S.lastTool || ""); break;
    case "elsewhere": caption("Mike is open in another window. Click Talk to use it here."); break;
    default: caption("");
  }
  let ph = "Talk or type to Mike";
  if (S.micPending) ph = "Waiting for microphone permission…";
  else if (!S.micOk) ph = "Type to Mike (microphone unavailable)";
  else if (!S.micOn) ph = "Type to Mike (mic is off)";
  el.input.placeholder = ph;
}
function setState(s) {
  S.state = s;
  Code.busy(s === "thinking" || s === "speaking");
  if (s !== "thinking") S.lastTool = "";
  document.body.dataset.state = s;
  renderStatus();
  toNative({ type: "state", state: s });
}

// ------------------------------------------------------------------ views & sidebar
const TITLES = { chat: "Mike", code: "Code", history: "History", memory: "Memory", reminders: "Reminders" };
function showView(v) {
  S.view = v;
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("hidden", s.dataset.view !== v));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  el.topTitle.textContent = TITLES[v];
  if (v === "code") Code.opened();
  if (v === "history") send({ type: "history_get", day: S.historyDay, q: el.historySearch.value.trim() });
  if (v === "memory") send({ type: "memory_get" });
  if (v === "reminders") send({ type: "reminders_get" });
  if (v === "chat") setTimeout(() => el.input.focus(), 0);
  if (innerWidth < 760) setSide(false);
}
el.nav.onclick = (e) => { const b = e.target.closest("button[data-view]"); if (b) showView(b.dataset.view); };
function setSide(open) {
  el.app.classList.toggle("collapsed", !open);
  if (innerWidth >= 760) pref.set("sideOpen", open);
  setTimeout(reportDragHoles, 260);
}
el.sideToggle.onclick = () => setSide(false);
el.sideOpen.onclick = () => setSide(true);
// Decide the sidebar once the window has a real size (a hidden window reports width 0).
function initialSide() {
  if (!innerWidth) { setTimeout(initialSide, 200); return; }
  setSide(pref.get("sideOpen", true) && innerWidth >= 760);
}
initialSide();
function newConversation() { send({ type: "reset" }); showView("chat"); }
el.newBtn.onclick = newConversation;
el.meBtn.onclick = () => openSettings();

// ------------------------------------------------------------------ thread
const ICONS = {
  eye: '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  screen: '<rect x="3.5" y="4.5" width="17" height="11.5" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
  bell: '<path d="M6.5 9a5.5 5.5 0 0 1 11 0c0 5.5 2 7 2 7h-15s2-1.5 2-7M10.3 19.5a2 2 0 0 0 3.4 0"/>',
  term: '<path d="M5 16.5l5-4.5-5-4.5M12 18h7"/>',
  file: '<path d="M13.5 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><path d="M13.5 3.5V9H19"/>',
  pen: '<path d="M12 20h8M16 4.5a2 2 0 0 1 3 3L7.5 19l-3.5 1 1-3.5z"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5a13 13 0 0 1 0 17M12 3.5a13 13 0 0 0 0 17"/>',
  check: '<path d="M19 7L9.5 16.5 5 12"/>', x: '<path d="M17 7L7 17M7 7l10 10"/>', dot: '<circle cx="12" cy="12" r="2.5"/>',
  wave: '<path d="M5 10v4M9 7v10M13 4.5v15M17 7v10M21 10v4"/>',
  click: '<path d="M9 9l10 4-4 1.5L13.5 19z"/><path d="M5 5l1.5 1.5M10 3v2M3 10h2"/>',
  key: '<rect x="3.5" y="6.5" width="17" height="11" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M8 14h8"/>',
  brain: '<path d="M12 4.5c-3.6 0-6.5 2.6-6.5 6 0 2.1 1.1 3.7 2.6 4.8V19h7.8v-3.7c1.5-1.1 2.6-2.7 2.6-4.8 0-3.4-2.9-6-6.5-6z"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2.5"/>',
  music: '<path d="M9 17.5V6l10-2v11.5"/><circle cx="6.5" cy="17.5" r="2.5"/><circle cx="16.5" cy="15.5" r="2.5"/>',
  trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>',
};
const svg = (n) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ICONS.dot}</svg>`;
const HIDDEN_TOOLS = new Set(["ToolSearch", "TodoWrite"]);
function describeTool(name, detail) {
  const short = (s, n = 70) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
  const base = (p) => (p || "").split("/").pop();
  switch (name) {
    case "look": return { icon: "eye", text: "Looked through the camera", live: "Looking through the camera…" };
    case "screenshot": return { icon: "screen", text: "Looked at your screen", live: "Looking at your screen…" };
    case "notify": return { icon: "bell", text: "Sent a notification", live: "Sending a notification…" };
    case "Bash": return { icon: "term", text: short(detail) || "Ran a command", live: detail ? `${short(detail, 60)}…` : "Running a command…" };
    case "Read": return { icon: "file", text: `Read ${base(detail)}`, live: "Reading a file…" };
    case "Write": return { icon: "pen", text: `Wrote ${base(detail)}`, live: "Writing a file…" };
    case "Edit": case "MultiEdit": return { icon: "pen", text: `Edited ${base(detail)}`, live: "Editing a file…" };
    case "Glob": case "Grep": return { icon: "search", text: "Searched your files", live: "Searching your files…" };
    case "WebSearch": return { icon: "globe", text: `Searched the web for “${short(detail, 44)}”`, live: "Searching the web…" };
    case "WebFetch": return { icon: "globe", text: "Read a web page", live: "Reading a web page…" };
    case "ui": return { icon: "screen", text: "Read the app's controls", live: "Reading the app…" };
    case "press": case "click": return { icon: "click", text: "Clicked", live: "Clicking…" };
    case "set_text": case "type_text": return { icon: "key", text: "Typed", live: "Typing…" };
    case "press_key": return { icon: "key", text: "Pressed a key", live: "Pressing a key…" };
    case "menu": return { icon: "click", text: "Used a menu", live: "Using a menu…" };
    case "scroll": return { icon: "click", text: "Scrolled", live: "Scrolling…" };
    case "switch_to": return { icon: "screen", text: "Switched apps", live: "Switching apps…" };
    case "media": return { icon: "music", text: "Media control", live: "Controlling playback…" };
    case "remember": case "update_memory": return { icon: "brain", text: "Saved to memory", live: "Remembering…" };
    case "forget": return { icon: "brain", text: "Removed from memory", live: "Forgetting…" };
    case "recall": return { icon: "brain", text: "Searched memory", live: "Remembering…" };
    case "set_reminder": return { icon: "clock", text: "Set a reminder", live: "Setting a reminder…" };
    case "list_reminders": return { icon: "clock", text: "Checked reminders", live: "Checking reminders…" };
    case "cancel_reminder": return { icon: "clock", text: "Cancelled a reminder", live: "Cancelling…" };
    case "Task": case "Agent": return { icon: "dot", text: "Used a helper", live: "Working on it…" };
    default: return { icon: "dot", text: short(`${name}${detail ? `: ${detail}` : ""}`), live: "Working…" };
  }
}
const initial = () => (S.userName || "You").trim()[0].toUpperCase();
function setEmpty(empty) { el["view-chat"].classList.toggle("empty", empty); }
function newTurn(youText, via) {
  el.empty?.remove(); setEmpty(false);
  const turn = document.createElement("article");
  turn.className = "turn";
  if (youText) {
    const row = document.createElement("div"); row.className = "you-row";
    const av = document.createElement("span"); av.className = "avatar"; av.textContent = initial();
    const you = document.createElement("div"); you.className = "you"; you.textContent = youText;
    if (via === "voice") { const v = document.createElement("span"); v.className = "via"; v.innerHTML = svg("wave"); v.title = "Spoken"; you.append(v); }
    row.append(av, you); turn.append(row);
  }
  const actions = document.createElement("div"); actions.className = "actions";
  const reply = document.createElement("div"); reply.className = "reply live";
  turn.append(actions, reply);
  el.thread.append(turn);
  S.turn = { node: turn, reply, actions, text: "" };
  scrollDown(true);
  return S.turn;
}
function addAction(icon, text, cls = "") {
  const t = S.turn || newTurn(null);
  const a = document.createElement("div");
  a.className = `action ${cls}`;
  a.innerHTML = `${svg(icon)}<span></span>`;
  a.lastChild.textContent = text;
  t.actions.append(a);
  scrollDown();
}
function scrollDown(force = false) {
  const t = el.thread;
  if (force || t.scrollHeight - t.scrollTop - t.clientHeight < 300) t.scrollTop = t.scrollHeight;
}
function resetThread() {
  el.thread.innerHTML = `<div class="hero" id="empty"><span class="mark big" aria-hidden="true"><i></i><i></i><i></i></span>
    <h1 id="greeting"></h1><p class="hero-sub">Say “Mike”, press <kbd>⌥</kbd><kbd>Space</kbd>, or type below.</p></div>`;
  S.turn = null;
  greet(); setEmpty(true);
}
let pendingVia = null;

// ------------------------------------------------------------------ speech playback
// HTMLAudioElement output is covered by the browser's echo cancellation.
const player = { queue: [], current: null };
function playNext() {
  if (player.current) return;
  const item = player.queue.shift();
  if (!item) { S.speaking = ""; renderStatus(); send({ type: "drained", gen: S.gen }); return; }
  if (item.gen < S.gen) return playNext();
  const a = new Audio(item.url);
  a.volume = S.duck ? 0.2 : 1;   // quieter while you're talking over Mike
  player.current = a;
  S.speaking = item.text; renderStatus();
  const done = () => { URL.revokeObjectURL(item.url); if (player.current === a) player.current = null; playNext(); };
  a.onended = done; a.onerror = done;
  a.play().catch(() => { showGate(); done(); });
}
function enqueueAudio(gen, b64, text) {
  if (gen < S.gen) return;
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  player.queue.push({ gen, text, url: URL.createObjectURL(new Blob([bytes], { type: "audio/wav" })) });
  playNext();
}
function stopAudio(gen) {
  S.gen = gen; S.duck = false;
  player.queue.forEach((i) => URL.revokeObjectURL(i.url));
  player.queue = [];
  if (player.current) { player.current.pause(); player.current = null; }
  S.speaking = "";
}

// ------------------------------------------------------------------ socket
function send(obj) { if (S.ws?.readyState === 1) S.ws.send(JSON.stringify(obj)); }
// Tell the server when this window is in front and focused, so the desktop pet can keep quiet meanwhile.
let lastActive = null;
function reportActive(force) {
  const active = document.visibilityState === "visible" && document.hasFocus();
  if (!force && active === lastActive) return;
  lastActive = active; send({ type: "window", active });
}
window.addEventListener("focus", () => reportActive()); window.addEventListener("blur", () => reportActive());
document.addEventListener("visibilitychange", () => reportActive());
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  S.ws = ws;
  ws.onopen = () => {
    S.connected = true;
    send({ type: "settings_get" }); send({ type: "history_get" }); send({ type: "reminders_get" });
    reportActive(true);
    if (presence.present !== null) send({ type: "presence", present: presence.present });
  };
  ws.onclose = (ev) => {
    S.connected = false;
    stopAudio(S.gen + 1);
    if (ev.code === 4000) { setState("elsewhere"); return; }   // another Mike window took over
    setState("offline");
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => { try { handle(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
}

let refreshTimer = null;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { send({ type: "history_get", day: S.view === "history" ? S.historyDay : "" }); send({ type: "reminders_get" }); }, 1500);
}

function handle(m) {
  if (m.type === "user" || m.type === "assistant_delta" || m.type === "assistant_done" || m.type === "tool") Code.event(m);
  if (["code_state", "term", "files", "diff", "file", "code_toast"].includes(m.type)) { Code.handle(m); return; }
  switch (m.type) {
    case "hello":
      setState(m.state || "idle");
      if (m.usage) showUsage(m.usage);
      if (m.pet) showPet(m.pet.pet, m.pet.mode);
      break;
    case "settings": showSettings(m); break;
    case "memory": showMemory(m); break;
    case "reminders": showReminders(m.items || []); break;
    case "history": showHistory(m); break;
    case "backup": showBackup(m); break;
    case "pet": showPet(m.pet, m.mode); break;
    case "voice": el.setVoice.value = m.voice; break;
    case "state": setState(m.state); if (m.state === "idle") refreshSoon(); break;
    case "hearing": S.hearing = m.value; renderStatus(); break;
    case "chime": chime(true); break;
    case "mic_off": setMic(false); break;
    case "transcript": pendingVia = "voice"; break;
    case "user": newTurn(m.text, pendingVia); pendingVia = null; break;
    case "assistant_delta": {
      const t = S.turn || newTurn(null);
      t.text += m.text;
      t.reply.textContent = t.text.replace(/^\s+/, "");
      scrollDown();
      break;
    }
    case "assistant_done": {
      const t = S.turn;
      if (t) {
        t.reply.classList.remove("live");
        if (!t.text.trim()) t.reply.remove();
        if (m.error) addAction("x", "Something went wrong", "denied");
        const bits = [];
        if (m.interrupted) bits.push("Stopped");
        if (m.duration_ms) bits.push(`${(m.duration_ms / 1000).toFixed(1)}s`);
        if (bits.length) { const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = bits.join(" · "); t.node.append(meta); }
      }
      S.turn = null;
      break;
    }
    case "tool": {
      if (HIDDEN_TOOLS.has(m.name)) break;
      const d = describeTool(m.name, m.detail);
      addAction(d.icon, d.text);
      S.lastTool = d.live; renderStatus();
      break;
    }
    case "flash": flash(); break;
    case "audio": enqueueAudio(m.gen, m.data, m.text); break;
    case "stop_audio": stopAudio(m.gen); renderStatus(); break;
    case "confirm":
      S.confirmId = m.id;
      el.confirmTitle.textContent = `Allow Mike to ${m.summary}?`;
      el.confirmDetail.textContent = m.detail;
      el.confirm.querySelector("details").open = false;
      el.confirm.classList.remove("hidden");
      if (S.view !== "chat") showView("chat");
      toNative({ type: "attention" });
      break;
    case "confirm_done":
      if (m.id === S.confirmId) { el.confirm.classList.add("hidden"); S.confirmId = null; }
      addAction(m.allowed ? "check" : "x", m.allowed ? "You allowed it" : "You declined", m.allowed ? "" : "denied");
      break;
    case "screenshot_request":
      if (native) toNative({ type: "screenshot", id: m.id });
      else send({ type: "screenshot", id: m.id, data: null });
      break;
    case "quit": toNative({ type: "quit" }); break;
    case "show": toNative({ type: "show" }); break;
    case "hide": toNative({ type: "hide" }); break;
    case "summon": toNative({ type: "attention" }); send({ type: "reminders_get" }); break;
    case "duck": S.duck = !!m.on; if (player.current) player.current.volume = S.duck ? 0.2 : 1; break;
    case "native_request":
      if (native) toNative({ type: "native", id: m.id, op: m.op, args: m.args || {} });
      else send({ type: "native", id: m.id, result: { error: "Computer control needs the Mike app." } });
      break;
    case "snapshot_request": takeSnapshot().then((data) => send({ type: "snapshot", id: m.id, data })); break;
    case "usage": showUsage(m.info); break;
    case "log":
      if (m.text.startsWith("brain online")) showModel(m.text.split("model ").pop());
      else console.info("[mike]", m.text);
      break;
    case "reset": resetThread(); break;
  }
}

// ------------------------------------------------------------------ usage + model
function prettyModel(id) {
  const m = /claude-([a-z]+)-(\d+)(?:-(\d+))?/.exec(id || "");
  if (!m) return id || "Claude";
  return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? "." + m[3] : ""}`;
}
function showModel(id) { if (id && id !== "?") el.modelName.textContent = prettyModel(id); }
function showUsage(info) {
  if (!info || !info.status) return;
  const util = info.utilization ?? info.unifiedWindows?.five_hour?.utilization ?? info.unifiedWindows?.fiveHour?.utilization;
  const used = typeof util === "number" ? Math.round(util <= 1 ? util * 100 : util) : null;
  const limited = info.status !== "allowed" && info.status !== "allowed_warning";
  const text = limited ? "Limit reached" : used !== null ? `${used}% used` : "Available";
  el.usageText.textContent = text;
  el.usageBar.style.width = `${limited ? 100 : Math.max(2, used ?? 2)}%`;
  el.usageBar.classList.toggle("high", limited || used > 80);
  const reset = info.resetsAt ? `Resets at ${new Date(info.resetsAt * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "";
  el.resetText.textContent = reset;
  el.setUsage.textContent = [text, reset].filter(Boolean).join(" · ");
}
function renderModels() {
  const cur = S.settings.model;
  el.modelMenu.innerHTML = ""; el.setModels.innerHTML = "";
  for (const m of S.models) {
    const b = document.createElement("button");
    b.className = m.id === cur ? "on" : "";
    b.innerHTML = `<b></b>${svg("check")}<span></span>`;
    b.querySelector("b").textContent = m.name; b.querySelector("span").textContent = m.desc;
    b.onclick = () => { setSetting("model", m.id); el.modelMenu.classList.add("hidden"); };
    el.modelMenu.append(b);
    const c = document.createElement("button");
    c.className = `choice ${m.id === cur ? "on" : ""}`;
    c.innerHTML = `<span class="radio"></span><div><b></b><span></span></div>`;
    c.querySelector("b").textContent = m.name; c.querySelector("div span").textContent = m.desc;
    c.onclick = () => setSetting("model", m.id);
    el.setModels.append(c);
  }
  const found = S.models.find((m) => m.id === cur);
  el.modelName.textContent = found ? found.name : prettyModel(cur);
}
el.modelBtn.onclick = (e) => { e.stopPropagation(); el.modelMenu.classList.toggle("hidden"); };

// ------------------------------------------------------------------ settings
function setSetting(key, value) { S.settings[key] = value; send({ type: "set", key, value }); renderSettings(); }
const sw = (node, on) => node.setAttribute("aria-checked", String(!!on));
const seg = (node, v) => node.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === String(v)));
function showSettings(m) {
  S.settings = m.values || S.settings;
  if (m.models) S.models = m.models;
  if (m.voices) showVoices(m.voices, m.voice);
  if (m.pet) showPet(m.pet.pet, m.pet.mode);
  if (m.data_dir) { S.dataDir = m.data_dir; el.aboutData.textContent = m.data_dir.replace(/^\/Users\/[^/]+/, "~"); }
  if (m.claude_bin) el.aboutBrain.textContent = `Claude Code on your Claude Pro plan${m.claude_bin.includes("Application Support/Claude") ? ", using the copy inside the Claude app" : ""}`;
  S.userName = S.settings.user_name || "";
  el.meName.textContent = S.userName || "You"; el.avatar.textContent = initial();
  greet();
  renderSettings();
}
function renderSettings() {
  const s = S.settings;
  if (document.activeElement !== el.setName) el.setName.value = s.user_name || "";
  seg(el.setEffort, s.effort || "low");
  el.setFresh.value = String(s.fresh_after_min ?? 30);
  el.setSpeed.value = s.voice_speed ?? 1.05; el.setSpeedText.textContent = `${Number(s.voice_speed ?? 1.05).toFixed(2)}×`;
  sw(el.setWake, s.wake_word); sw(el.setBarge, s.barge_in); sw(el.setVoiceMatch, s.voice_match);
  el.setFollow.value = String(s.followup_seconds ?? 10);
  sw(el.setMemAuto, s.memory_auto); sw(el.setClaudeMem, s.claude_memory);
  sw(el.setWelcome, s.welcome_back); sw(el.setMorning, s.morning_briefing);
  el.setBreak.value = String(s.break_nudge_min ?? 120);
  sw(el.setChime, pref.get("chime", true));
  sw(el.setCamPresence, S.camPresence);
  sw(el.setMic, S.micOn && S.micOk);
  seg(el.setBrainMode, s.brain_mode || "auto");
  renderModels();
}
el.setName.onchange = () => setSetting("user_name", el.setName.value.trim());
el.setEffort.onclick = (e) => { const b = e.target.closest("button"); if (b) setSetting("effort", b.dataset.v); };
el.setFresh.onchange = () => setSetting("fresh_after_min", Number(el.setFresh.value));
el.setSpeed.oninput = () => { el.setSpeedText.textContent = `${Number(el.setSpeed.value).toFixed(2)}×`; };
el.setSpeed.onchange = () => setSetting("voice_speed", Number(el.setSpeed.value));
el.setWake.onclick = () => setSetting("wake_word", !S.settings.wake_word);
el.setBarge.onclick = () => setSetting("barge_in", !S.settings.barge_in);
el.setVoiceMatch.onclick = () => setSetting("voice_match", !S.settings.voice_match);
el.voiceReset.onclick = () => { send({ type: "voice_reset" }); el.voiceReset.textContent = "Say “Mike” a few times"; };
el.setFollow.onchange = () => setSetting("followup_seconds", Number(el.setFollow.value));
el.setMemAuto.onclick = () => setSetting("memory_auto", !S.settings.memory_auto);
el.setClaudeMem.onclick = () => setSetting("claude_memory", !S.settings.claude_memory);
el.setWelcome.onclick = () => setSetting("welcome_back", !S.settings.welcome_back);
el.setMorning.onclick = () => setSetting("morning_briefing", !S.settings.morning_briefing);
el.setBreak.onchange = () => setSetting("break_nudge_min", Number(el.setBreak.value));
el.setChime.onclick = () => { pref.set("chime", !pref.get("chime", true)); renderSettings(); };
el.setTheme.onclick = (e) => { const b = e.target.closest("button"); if (b) { pref.set("theme", b.dataset.v); applyTheme(b.dataset.v); } };
el.setReset.onclick = () => { closeSettings(); newConversation(); };
el.setMemOpen.onclick = () => { closeSettings(); showView("memory"); };
el.setMemFile.onclick = () => nativeCall("open_path", { path: `${S.dataDir || "~/mike/data"}/memory.md` });
el.aboutOpenData.onclick = () => nativeCall("open_path", { path: S.dataDir || "~/mike/data", reveal: true });
el.aboutQuit.onclick = () => toNative({ type: "quit" });
el.setVoicePreview.onclick = () => { ensureAudio().resume?.(); send({ type: "voice_preview" }); };
el.setLogin.onclick = async () => { const r = await nativeCall("login_item", { enable: el.setLogin.getAttribute("aria-checked") !== "true" }); sw(el.setLogin, r?.enabled); };
el.setShowLaunch.onclick = async () => { const r = await nativeCall("show_at_launch", { enable: el.setShowLaunch.getAttribute("aria-checked") !== "true" }); sw(el.setShowLaunch, r?.enabled); };

function openSection(name) {
  document.querySelectorAll(".sec").forEach((s) => s.classList.toggle("hidden", s.dataset.sec !== name));
  document.querySelectorAll("#setNav button").forEach((b) => b.classList.toggle("on", b.dataset.sec === name));
  if (name === "privacy") { refreshPerms(); listCameras(); }
  if (name === "pet") renderPetGrid(); else stopPetPreviews();
  if (name === "memory") send({ type: "memory_get" });
  if (name === "voice") listMics();
  if (name === "model") send({ type: "backup_get" });
}
el.setNav.onclick = (e) => { const b = e.target.closest("button[data-sec]"); if (b) openSection(b.dataset.sec); };
async function openSettings(section) {
  el.settings.classList.remove("hidden");
  send({ type: "settings_get" });
  openSection(section || document.querySelector("#setNav button.on")?.dataset.sec || "general");
  if (!native) { [el.setLogin, el.setShowLaunch].forEach((n) => (n.disabled = true)); return; }
  const [login, launch, info] = await Promise.all([nativeCall("login_item"), nativeCall("show_at_launch"), nativeCall("app_info")]);
  sw(el.setLogin, login?.enabled); sw(el.setShowLaunch, launch?.enabled);
  if (info?.version) el.aboutVersion.textContent = `Version ${info.version} · ${(info.macos || "").replace(/^Version /, "macOS ")}`;
}
function closeSettings() { el.settings.classList.add("hidden"); stopPetPreviews(); }
el.setClose.onclick = closeSettings;
el.settings.onclick = (e) => { if (e.target === el.settings) closeSettings(); };

// ------------------------------------------------------------------ backup brain (free models)
el.setBrainMode.onclick = (e) => { const b = e.target.closest("button[data-v]"); if (b) setSetting("brain_mode", b.dataset.v); };
function openUrl(url) { if (native) nativeCall("open_url", { url }); else window.open(url, "_blank"); }
function showBackup(m) {
  const ready = (m.providers || []).filter((p) => p.ready);
  const blocked = m.blocked_until && m.blocked_until * 1000 > Date.now();
  el.backupState.textContent = ready.length
    ? `${blocked ? "In use now. " : ""}Ready: ${ready.map((p) => p.name).join(", ")}.`
    : "Add a free key below to turn it on.";
  el.providers.innerHTML = "";
  for (const p of m.providers || []) {
    const row = document.createElement("div"); row.className = "perm";
    const state = p.ready ? `Ready · ${p.model}` : p.local ? "Not running, or no model downloaded" : p.has_key ? "Key didn't work" : "No key yet";
    row.innerHTML = `<div class="p-text"><b></b><span></span></div>`;
    row.querySelector("b").textContent = p.name;
    row.querySelector("span").textContent = p.local ? `${state}. For offline use, run “ollama pull qwen3:8b” in Terminal (about 5 GB) and keep Ollama open.` : state;
    if (!p.local) {
      const box = document.createElement("div"); box.className = "prov-key";
      box.innerHTML = `<input class="field" type="password" autocomplete="off" spellcheck="false"><button class="btn">Save</button><button class="link-btn">Get a free key</button>`;
      const input = box.querySelector("input");
      input.placeholder = p.key_hint || "Paste API key";
      box.querySelector(".btn").onclick = () => { send({ type: "backup_key", provider: p.id, key: input.value.trim() }); input.value = ""; };
      box.querySelector(".link-btn").onclick = () => openUrl(p.signup);
      row.append(box);
    }
    el.providers.append(row);
  }
}

// Native request/response: settings helpers that live in Mike.app.
const nativeWaits = {};
function nativeCall(op, args = {}) {
  if (!native) return Promise.resolve(null);
  const id = "u" + Math.random().toString(36).slice(2, 9);
  return new Promise((res) => {
    nativeWaits[id] = res;
    toNative({ type: "native", id, op, args });
    setTimeout(() => { if (nativeWaits[id]) { delete nativeWaits[id]; res(null); } }, 4000);
  });
}

const PERMS = [
  { key: "microphone", name: "Microphone", why: "Hear you say “Mike” and your requests.", icon: '<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"/>' },
  { key: "accessibility", name: "Accessibility", why: "Press buttons, type and use menus in your apps.", icon: '<circle cx="12" cy="5" r="1.8"/><path d="M5 8.5l7 1.5 7-1.5M12 10v4.5M9 21l3-6.5 3 6.5"/>' },
  { key: "screen", name: "Screen Recording", why: "See your screen when you ask what's on it.", icon: ICONS.screen },
  { key: "camera", name: "Camera", why: "Look through the camera when you ask him to.", icon: '<path d="M3.5 8.5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2zM15.5 11l5-3v8l-5-3"/>' },
];
async function refreshPerms() {
  const st = (await nativeCall("perm_status")) || {};
  el.perms.innerHTML = "";
  for (const p of PERMS) {
    const v = st[p.key], ok = v === "granted";
    const row = document.createElement("div"); row.className = "perm";
    row.innerHTML = `<span class="p-ico"><svg viewBox="0 0 24 24">${p.icon}</svg></span><div class="p-text"><b></b><span></span></div>
      <span class="p-state ${ok ? "ok" : v && v !== "not_asked" ? "bad" : ""}">${ok ? svg("check") + "Allowed" : v === "not_asked" ? "Not asked yet" : v ? "Not allowed" : "Mike app only"}</span>
      <button class="btn">Open Settings</button>`;
    row.querySelector("b").textContent = p.name; row.querySelector(".p-text span").textContent = p.why;
    row.querySelector("button").onclick = () => nativeCall("open_pane", { pane: p.key });
    el.perms.append(row);
  }
}

function showVoices(list, current) {
  el.setVoice.innerHTML = "";
  if (!list || !list.length) { el.setVoice.disabled = true; el.setVoice.append(new Option("macOS voice")); return; }
  el.setVoice.disabled = false;
  const groups = {};
  for (const v of list) {
    const g = v.id[1] === "m" ? "Male" : "Female";
    if (!groups[g]) { groups[g] = document.createElement("optgroup"); groups[g].label = g; }
    groups[g].append(new Option(v.label, v.id));
  }
  for (const g of ["Male", "Female"]) if (groups[g]) el.setVoice.append(groups[g]);
  el.setVoice.value = current;
}
el.setVoice.onchange = () => { ensureAudio().resume?.(); send({ type: "set_voice", voice: el.setVoice.value }); };

// desktop pet
const petUI = { stops: [] };
function showPet(pet, mode) {
  if (pet && (pet === "none" || PETS[pet])) S.pet = pet;
  if (mode) S.petMode = mode;
  document.querySelectorAll(".pet-card").forEach((c) => c.classList.toggle("selected", c.dataset.pet === S.pet));
  seg(el.setPetMode, S.petMode);
}
function renderPetGrid() {
  stopPetPreviews();
  el.petGrid.innerHTML = "";
  for (const id of PET_ORDER) {
    const info = PETS[id] || NONE;
    const card = document.createElement("button");
    card.className = "pet-card"; card.dataset.pet = id; card.title = info.tagline || "";
    const cv = document.createElement("canvas"); cv.width = 176; cv.height = 176;
    const name = document.createElement("b"); name.textContent = info.name;
    card.append(cv, name);
    card.onclick = () => { send({ type: "set_pet", pet: id }); showPet(id); };
    const stop = preview(cv, id, "idle");
    petUI.stops.push(() => stop?.());
    el.petGrid.append(card);
  }
  showPet();
}
function stopPetPreviews() { petUI.stops.forEach((s) => s()); petUI.stops = []; }
el.setPetMode.onclick = (e) => { const b = e.target.closest("button[data-v]"); if (b) { send({ type: "set_pet", mode: b.dataset.v }); showPet(null, b.dataset.v); } };

// ------------------------------------------------------------------ history, memory, reminders
const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function fmtDay(key) {
  const date = new Date(`${key}T12:00:00`);
  const diff = Math.round((new Date(dayKey(new Date()) + "T12:00:00") - date) / 864e5);
  return diff === 0 ? "Today" : diff === 1 ? "Yesterday" : date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}
const fmtTime = (t) => { try { return new Date(t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch { return ""; } };
function showHistory(m) {
  el.recentList.innerHTML = "";
  for (const d of (m.days || []).slice(0, 12)) {
    const b = document.createElement("button");
    b.innerHTML = "<b></b><span></span>";
    b.querySelector("b").textContent = fmtDay(d.date);
    b.querySelector("span").textContent = d.first;
    b.onclick = () => { S.historyDay = d.date; el.historySearch.value = ""; showView("history"); };
    el.recentList.append(b);
  }
  if (S.view !== "history") return;
  S.historyDay = m.day || S.historyDay;
  el.dayTabs.innerHTML = "";
  if (!m.q) for (const d of (m.days || []).slice(0, 14)) {
    const b = document.createElement("button");
    b.textContent = `${fmtDay(d.date)} · ${d.count}`;
    b.className = d.date === m.day ? "on" : "";
    b.onclick = () => { S.historyDay = d.date; send({ type: "history_get", day: d.date }); };
    el.dayTabs.append(b);
  }
  el.historyList.innerHTML = "";
  const entries = m.q ? m.entries : [...(m.entries || [])].reverse();
  if (!entries?.length) { el.historyList.innerHTML = `<div class="empty-note">${m.q ? "Nothing matched." : "No conversations yet."}</div>`; return; }
  for (const e of entries) {
    const it = document.createElement("div"); it.className = "h-item";
    it.innerHTML = `<div class="h-time"></div><div class="h-you"></div><div class="h-mike"></div>`;
    it.querySelector(".h-time").textContent = m.q ? `${fmtDay(e.t.slice(0, 10))}, ${fmtTime(e.t)}` : fmtTime(e.t);
    it.querySelector(".h-you").textContent = e.user;
    it.querySelector(".h-mike").textContent = e.mike;
    el.historyList.append(it);
  }
}
let searchTimer = null;
el.historySearch.oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => send({ type: "history_get", day: S.historyDay, q: el.historySearch.value.trim() }), 250); };

function showMemory(m) {
  el.memoryList.innerHTML = "";
  if (!m.facts.length) el.memoryList.innerHTML = '<li class="empty-note">Nothing yet. Mike learns as you talk.</li>';
  for (const f of [...m.facts].reverse()) {
    const li = document.createElement("li");
    li.innerHTML = `<span></span><button class="icon-btn" title="Forget this">${svg("trash")}</button>`;
    li.querySelector("span").textContent = f;
    li.querySelector("button").onclick = () => send({ type: "memory_delete", text: f });
    el.memoryList.append(li);
  }
  const n = m.facts.length;
  el.memoryFoot.textContent = `${n} saved ${n === 1 ? "fact" : "facts"} · ${m.journal_days} ${m.journal_days === 1 ? "day" : "days"} of conversation history · ${m.claude_notes} notes from Claude's memory`;
  el.setMemCount.textContent = `${n} facts saved, plus ${m.claude_notes} notes from Claude`;
  el.setClaudeMemText.textContent = `Mike reads the ${m.claude_notes} notes from your Claude Code sessions.`;
}
el.memoryForm.onsubmit = (e) => { e.preventDefault(); const t = el.memoryInput.value.trim(); if (t) send({ type: "memory_add", text: t }); el.memoryInput.value = ""; };

function showReminders(items) {
  el.remCount.textContent = items.length ? String(items.length) : "";
  el.reminderList.innerHTML = "";
  if (!items.length) { el.reminderList.innerHTML = '<li class="empty-note">Nothing scheduled.</li>'; return; }
  for (const r of items) {
    const d = new Date(r.due * 1000);
    const when = `${fmtDay(dayKey(d))}, ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
    const li = document.createElement("li");
    li.innerHTML = `<i class="r-ico">${svg(r.kind === "timer" ? "clock" : "bell")}</i><span><b></b><small></small></span><button class="icon-btn" title="Cancel">${svg("x")}</button>`;
    li.querySelector("b").textContent = r.text || (r.kind === "timer" ? "Timer" : "Reminder");
    li.querySelector("small").textContent = `${when}${r.repeat ? ` · repeats ${r.repeat}` : ""} · ${r.kind}`;
    li.querySelector("button").onclick = () => send({ type: "reminder_cancel", id: r.id });
    el.reminderList.append(li);
  }
}

// ------------------------------------------------------------------ mic
function ensureAudio() { if (!S.audioCtx) S.audioCtx = new AudioContext(); return S.audioCtx; }
async function startMic(deviceId = pref.get("micId", "")) {
  if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("unsupported"), { name: "NotSupportedError" });
  const stream = await navigator.mediaDevices.getUserMedia({
    // Auto-gain off: it would boost the TV / people across the room up to your level.
    audio: { deviceId: deviceId ? { exact: deviceId } : undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: false, channelCount: 1 },
  });
  S.micStream?.getTracks().forEach((t) => t.stop());
  try { S.micNode?.disconnect(); } catch {}
  const ctx = ensureAudio();
  if (!S.workletLoaded) { await ctx.audioWorklet.addModule("/static/mic-worklet.js"); S.workletLoaded = true; }
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "mic-worklet");
  node.port.onmessage = (e) => { S.audioAt = Date.now(); if (S.micOn && S.ws?.readyState === 1) S.ws.send(e.data); };
  S.audioAt = Date.now();
  stream.getAudioTracks().forEach((t) => { t.onended = () => { console.warn("[mic] track ended; reopening"); if (S.micOn) retryMic(); }; });
  S.analyser = ctx.createAnalyser(); S.analyser.fftSize = 512;
  src.connect(S.analyser); src.connect(node);
  const mute = ctx.createGain(); mute.gain.value = 0;
  node.connect(mute).connect(ctx.destination);
  S.micStream = stream; S.micNode = node; S.micOk = true;
}
// After the Mac sleeps (or the mic device changes) macOS ends the stream and the page keeps looking connected, but no
// audio arrives: he is deaf until restarted. Notice that and reopen the mic by himself.
S.micFails = 0;
function micCheck() {
  if (!S.micOn || !S.micOk || S.micPending || !S.micStream) return;          // muted on purpose, or already being reopened
  const ctx = S.audioCtx, tr = S.micStream.getAudioTracks()[0];
  if (ctx && ctx.state !== "running") ctx.resume().catch(() => {});
  const stale = Date.now() - (S.audioAt || 0) > 5000;
  if (tr && tr.readyState === "live" && !stale) { S.micFails = 0; return; }
  S.micFails += 1;
  console.warn(`[mic] no audio (${tr ? tr.readyState : "no track"}, stale ${stale}); reopening, try ${S.micFails}`);
  if (S.micFails >= 2 && ctx) {                                               // the audio engine itself is stuck: build a new one
    try { ctx.close(); } catch {}
    S.audioCtx = null; S.workletLoaded = false;
  }
  retryMic();
}
setInterval(micCheck, 4000);
["focus", "pageshow", "online"].forEach((ev) => window.addEventListener(ev, () => setTimeout(micCheck, 600)));
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") setTimeout(micCheck, 600); });
navigator.mediaDevices?.addEventListener?.("devicechange", () => setTimeout(micCheck, 600));
function stopMicStream() {
  // Release the mic completely, so macOS turns its mic light off.
  S.micStream?.getTracks().forEach((t) => t.stop());
  try { S.micNode?.disconnect(); } catch {}
  S.micStream = null; S.micNode = null;
}
function setMic(on) {
  if (on && S.micOk && !S.micStream) { S.micOn = true; retryMic(); return; }   // muted earlier: reopen the mic
  S.micOn = on;   // mute lasts only until you unmute or restart Mike, so a restart never leaves it deaf
  if (!on) stopMicStream();
  const live = on && S.micOk;
  el.micBtn.classList.toggle("off", !live);
  el.micBtn.title = !S.micOk ? (S.micError || "Microphone unavailable") : on ? "Mute microphone" : "Unmute microphone";
  sw(el.setMic, live);
  el.micHint.textContent = S.micOk ? (on ? "Mike listens on this device." : "Muted. Mike won't hear you.") : S.micError;
  renderStatus();
  toNative({ type: "mic", on: live });
}
async function retryMic(deviceId) {
  S.micPending = true; renderStatus();
  try { await startMic(deviceId); S.micError = ""; }
  catch (e) {
    let recovered = false;
    if (deviceId || pref.get("micId", "")) { pref.set("micId", ""); try { await startMic(""); S.micError = ""; recovered = true; } catch {} }
    if (!recovered) {
      S.micOk = false;
      S.micError = e.name === "NotAllowedError" ? "Microphone blocked. Allow Mike in Privacy & permissions."
        : e.name === "NotFoundError" ? "No microphone found." : "Microphone unavailable here.";
    }
  } finally { S.micPending = false; }
  setMic(S.micOn);
}
async function listMics() {
  try {
    const mics = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
    el.setMicDevice.innerHTML = "";
    for (const d of mics) el.setMicDevice.append(new Option(d.label || "Microphone", d.deviceId));
    const cur = S.micStream?.getAudioTracks()[0]?.getSettings().deviceId;
    if (cur) el.setMicDevice.value = cur;
  } catch {}
}
el.setMicDevice.onchange = () => { pref.set("micId", el.setMicDevice.value); retryMic(el.setMicDevice.value); };
el.micBtn.onclick = () => { if (S.micOk) setMic(!S.micOn); else retryMic(); };
el.setMic.onclick = () => { if (S.micOk) setMic(!S.micOn); else retryMic(); };

// ------------------------------------------------------------------ camera (off unless needed)
async function startCamera() {
  if (S.camStream) return;
  const id = pref.get("cameraId", "");
  try {
    S.camStream = await navigator.mediaDevices.getUserMedia({ video: { deviceId: id ? { exact: id } : undefined, width: { ideal: 1280 }, height: { ideal: 720 } } });
  } catch (e) {
    if (!id) throw e;
    pref.set("cameraId", "");
    S.camStream = await navigator.mediaDevices.getUserMedia({ video: true });
  }
  el.cam.srcObject = S.camStream;
  el.camBtn.classList.add("on");
  el.presence.textContent = "Camera on";
  if (S.camPresence) presence.start();
}
function stopCamera() {
  clearTimeout(S.camTimer);
  S.camStream?.getTracks().forEach((t) => t.stop());
  S.camStream = null; el.cam.srcObject = null;
  el.camBtn.classList.remove("on");
  el.presence.textContent = "Camera off";
  el.pip.classList.add("hidden");
  presence.update(null);
}
// After a one-off look, switch the camera off again unless presence or the preview needs it.
function camIdleLater(ms = 20000) {
  clearTimeout(S.camTimer);
  S.camTimer = setTimeout(() => { if (!S.camPresence && el.pip.classList.contains("hidden")) stopCamera(); }, ms);
}
async function listCameras() {
  try {
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    el.setCamDevice.innerHTML = "";
    for (const c of cams) el.setCamDevice.append(new Option(c.label || "Camera", c.deviceId));
    const cur = S.camStream?.getVideoTracks()[0]?.getSettings().deviceId || pref.get("cameraId", "");
    if (cur) el.setCamDevice.value = cur;
  } catch {}
}
el.setCamDevice.onchange = async () => { pref.set("cameraId", el.setCamDevice.value); const was = !!S.camStream; stopCamera(); if (was) await startCamera().catch(() => {}); };
el.setCamPresence.onclick = async () => {
  S.camPresence = !S.camPresence; pref.set("camPresence", S.camPresence);
  if (S.camPresence) { try { await startCamera(); presence.start(); } catch { S.camPresence = false; pref.set("camPresence", false); } }
  else { presence.stop(); camIdleLater(300); }
  renderSettings();
};
async function takeSnapshot() {
  try {
    const fresh = !S.camStream;
    await startCamera();
    const v = el.cam;
    for (let i = 0; i < 30 && !v.videoWidth; i++) await new Promise((r) => setTimeout(r, 100));
    if (fresh) await new Promise((r) => setTimeout(r, 700));   // let exposure settle
    if (!v.videoWidth) return null;
    const scale = Math.min(1, 1280 / v.videoWidth);
    const c = document.createElement("canvas");
    c.width = Math.round(v.videoWidth * scale); c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    flash();
    camIdleLater();
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
    const buf = new Uint8Array(await blob.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return btoa(bin);
  } catch (e) { console.warn("snapshot failed", e); return null; }
}
function flash() { el.camFlash.classList.remove("on"); void el.camFlash.offsetWidth; el.camFlash.classList.add("on"); }
el.camBtn.onclick = async (e) => {
  e.stopPropagation();
  if (!el.pip.classList.contains("hidden")) { el.pip.classList.add("hidden"); camIdleLater(300); return; }
  try { await startCamera(); }
  catch (err) { el.presence.textContent = err.name === "NotAllowedError" ? "Camera blocked. Allow it in Privacy & permissions." : "No camera available"; }
  el.pip.classList.remove("hidden");
};
el.pipClose.onclick = () => stopCamera();

// Optional on-device presence detection (frames never leave your Mac).
const presence = {
  detector: null, present: null, lastFace: 0, timer: null, loading: false,
  async start() {
    if (this.detector) { this.loop(); return; }
    if (this.loading) return;
    this.loading = true;
    try {
      const vision = await import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs");
      const files = await vision.FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm");
      this.detector = await vision.FaceDetector.createFromOptions(files, {
        baseOptions: { modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" },
        runningMode: "VIDEO", minDetectionConfidence: 0.5,
      });
      this.loop();
    } catch (e) { console.warn("presence detection unavailable", e); }
    finally { this.loading = false; }
  },
  stop() { clearInterval(this.timer); this.timer = null; },
  loop() {
    clearInterval(this.timer);
    this.timer = setInterval(() => {
      const v = el.cam;
      if (!S.camStream || !v.videoWidth || !this.detector) return;
      try { if (this.detector.detectForVideo(v, performance.now()).detections.length) this.lastFace = performance.now(); } catch {}
      this.update(performance.now() - this.lastFace < 5000);
    }, 500);
  },
  update(p) {
    if (p === this.present) return;
    this.present = p;
    if (p !== null && S.camStream) el.presence.textContent = p ? "Mike can see you" : "No one in view";
    if (p !== null) send({ type: "presence", present: p });
  },
};

// ------------------------------------------------------------------ chime
function chime(up = true) {
  if (!pref.get("chime", true)) return;
  const ctx = S.audioCtx;
  if (!ctx || ctx.state !== "running") return;
  const t = ctx.currentTime;
  [0, 0.07].forEach((d, i) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "sine"; o.frequency.value = up ? [660, 990][i] : [990, 660][i];
    g.gain.setValueAtTime(0.0001, t + d);
    g.gain.exponentialRampToValueAtTime(0.05, t + d + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + d + 0.3);
    o.connect(g).connect(ctx.destination);
    o.start(t + d); o.stop(t + d + 0.35);
  });
}

// ------------------------------------------------------------------ input
function autosize() {
  if (!el.input.offsetWidth) return;   // not laid out yet; measured again on resize / first input
  el.input.style.height = "auto";
  const h = el.input.scrollHeight;
  el.input.style.height = `${Math.min(180, h)}px`;
  el.input.classList.toggle("scroll", h > 180);
}
Code.init({ send, ask: (text) => { ensureAudio().resume?.(); send({ type: "text", text, source: "code" }); }, stop: () => interrupt() });
function sendText(text) { text = text.trim(); if (!text) return; ensureAudio().resume?.(); send({ type: "text", text }); if (S.view !== "chat") showView("chat"); }
const syncSend = () => { el.sendBtn.disabled = !el.input.value.trim(); autosize(); };
el.input.addEventListener("input", syncSend); syncSend();
el.input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); el.form.requestSubmit(); } });
el.form.onsubmit = (e) => { e.preventDefault(); sendText(el.input.value); el.input.value = ""; syncSend(); };
el.suggestions.addEventListener("click", (e) => { const b = e.target.closest("button[data-say]"); if (b) sendText(b.dataset.say); });
el.allowBtn.onclick = () => send({ type: "approve", id: S.confirmId, allow: true });
el.denyBtn.onclick = () => send({ type: "approve", id: S.confirmId, allow: false });
el.stopBtn.onclick = () => interrupt();
el.talkBtn.onclick = () => wake();

function wake() {
  ensureAudio().resume?.();
  if (S.state === "elsewhere") { connect(); return; }
  if (S.state === "thinking" || S.state === "speaking") { interrupt(); return; }
  if (!S.micOk || !S.micOn) { el.input.focus(); return; }
  chime(true);
  send({ type: "wake" });
}
function interrupt() { chime(false); send({ type: "interrupt" }); }

document.addEventListener("click", (e) => {
  if (!el.modelMenu.classList.contains("hidden") && !e.target.closest(".model-pick")) el.modelMenu.classList.add("hidden");
  if (!el.pip.classList.contains("hidden") && !el.pip.contains(e.target) && !el.camBtn.contains(e.target)) { el.pip.classList.add("hidden"); camIdleLater(300); }
});
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
  if (e.metaKey && e.key === ",") { e.preventDefault(); openSettings(); return; }
  if (e.metaKey && e.key.toLowerCase() === "n") { e.preventDefault(); newConversation(); return; }
  if (e.metaKey && e.key === "\\") { e.preventDefault(); setSide(el.app.classList.contains("collapsed")); return; }
  if (e.key === "Escape") {
    if (!el.settings.classList.contains("hidden")) { closeSettings(); return; }
    if (!el.modelMenu.classList.contains("hidden")) { el.modelMenu.classList.add("hidden"); return; }
    if (!el.pip.classList.contains("hidden")) { el.pip.classList.add("hidden"); camIdleLater(300); return; }
    if (typing) document.activeElement.blur(); else interrupt();
    return;
  }
  if (S.confirmId && !typing && e.key === "Enter") { el.allowBtn.click(); return; }
  if (e.code === "Space" && e.altKey) { e.preventDefault(); wake(); return; }
  if (!typing && S.view === "chat" && el.settings.classList.contains("hidden") && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) el.input.focus();
});

// Hooks for the native app (⌥Space hotkey, menu bar).
window.mike = {
  wake, interrupt, openSettings,
  screenshotResult(id, data) { send({ type: "screenshot", id, data }); },
  nativeResult(id, result) {
    if (nativeWaits[id]) { nativeWaits[id](result); delete nativeWaits[id]; return; }
    send({ type: "native", id, result });
  },
  toggleMic() { if (S.micOk) setMic(!S.micOn); else retryMic(); return S.micOn && S.micOk; },
  state: () => S.state,
};

// Tell Mike.app which parts of the title-bar strip are buttons (the rest drags the window).
function reportDragHoles() {
  if (!native) return;
  const rects = [...document.querySelectorAll(".side-top button, .top button, .top .nodrag")]
    .filter((b) => b.offsetParent !== null)
    .map((b) => { const r = b.getBoundingClientRect(); return [r.left - 2, r.top - 2, r.width + 4, r.height + 4]; })
    .filter((r) => r[1] < 52 && r[0] + r[2] > 0);
  toNative({ type: "dragHoles", rects, height: 52 });
}
addEventListener("resize", () => { reportDragHoles(); autosize(); if (innerWidth < 760 && !el.app.classList.contains("collapsed")) setSide(false); });

// ------------------------------------------------------------------ startup
function showGate() { if (S.audioCtx?.state !== "running") el.gate.classList.remove("hidden"); }
el.gateBtn.onclick = async () => { el.gate.classList.add("hidden"); await ensureAudio().resume().catch(() => {}); playNext(); };

async function boot() {
  setEmpty(true); greet();
  reportDragHoles();
  const params = new URLSearchParams(location.search);
  // ?preview renders the page with sample data, without taking over Mike's connection (design work).
  if (params.has("preview")) {
    S.micOk = true;
    showSettings({ values: { user_name: "Sam", model: "claude-haiku-5-5", effort: "low", fresh_after_min: 30, voice_speed: 1.05, wake_word: true, followup_seconds: 10, barge_in: true, welcome_back: true, morning_briefing: true, break_nudge_min: 120, memory_auto: true, claude_memory: true },
      models: [{ id: "claude-haiku-5-5", name: "Haiku 5.5", desc: "Fastest. Great for everyday voice use." }, { id: "claude-sonnet-5-5", name: "Sonnet 5.5", desc: "Smarter for harder tasks, a little slower." }, { id: "claude-opus-5-5", name: "Opus 5.5", desc: "Deep reasoning. Uses your limit fastest." }, { id: "claude-fable-5-2", name: "Fable 5.2", desc: "Most capable. Slowest and heaviest on usage." }],
      voices: [{ id: "am_michael", label: "Michael · American" }, { id: "af_heart", label: "Heart · American" }], voice: "af_heart", pet: { pet: "mike", mode: "active" } });
    showUsage({ status: "allowed", resetsAt: Date.now() / 1000 + 7200, utilization: 0.38 });
    const today = dayKey(new Date());
    showHistory({ days: [{ date: today, count: 12, first: "What's on my screen?" }, { date: "2026-11-11", count: 30, first: "Open Spotify and play lo-fi" }], entries: [] });
    showReminders([{ id: "r1", text: "Revise chemistry notes", due: Date.now() / 1000 + 86400, kind: "reminder", repeat: "" }]);
    setState(params.get("state") || "idle");
    if (params.has("demo")) demo();
    if (params.has("confirm")) handle({ type: "confirm", id: "x", summary: "delete the file notes.txt", detail: '{\n  "command": "rm ~/notes.txt"\n}' });
    if (params.has("view")) {
      showView(params.get("view"));
      if (params.get("view") === "memory") showMemory({ facts: ["Name is Sam", "Lives in Chicago", "Has a chemistry exam on 3 December"], journal_days: 2, claude_notes: 40 });
      if (params.get("view") === "history") showHistory({ days: [{ date: today, count: 2, first: "What's on my screen?" }], day: today, entries: [{ t: `${today}T09:17:57`, user: "Explain in simple words why rockets stage.", mike: "Rockets stage because carrying dead weight is expensive." }] });
    }
    if (params.has("settings")) openSettings(params.get("settings") || "general");
    if (params.get("settings") === "model") showBackup({ providers: [
      { id: "groq", name: "Groq", ready: true, model: "openai/gpt-oss-120b", has_key: true, key_hint: "••••8f2a", signup: "https://console.groq.com/keys" },
      { id: "nvidia", name: "NVIDIA", ready: false, has_key: false, signup: "https://build.nvidia.com" },
      { id: "openrouter", name: "OpenRouter", ready: false, has_key: false, signup: "https://openrouter.ai/keys" },
      { id: "gemini", name: "Google Gemini", ready: false, has_key: false, signup: "https://aistudio.google.com/apikey" },
      { id: "ollama", name: "Local (Ollama)", local: true, ready: false }] });
    return;
  }
  connect();
  await retryMic();
  if (S.camPresence) { try { await startCamera(); } catch {} }
  const ctx = ensureAudio();
  if (ctx.state !== "running") {
    await Promise.race([ctx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 300))]);
    if (ctx.state !== "running") showGate();
  }
  renderStatus();
}
function demo() {
  handle({ type: "transcript" }); handle({ type: "user", text: "Mike, what's on my screen?" });
  handle({ type: "tool", name: "screenshot", detail: "" });
  handle({ type: "assistant_delta", text: "You've got the CanSat telemetry site open in Opera, with the altitude graph climbing to about 420 metres and the battery at 7.9 volts. Looks like a clean test run." });
  handle({ type: "assistant_done", duration_ms: 2400 });
  handle({ type: "user", text: "Nice. Put on something to focus to on Spotify." });
  handle({ type: "tool", name: "ui", detail: "" }); handle({ type: "tool", name: "press", detail: "" });
  handle({ type: "assistant_delta", text: "Done, your lofi chill playlist is on." });
  handle({ type: "assistant_done", duration_ms: 4800 });
}
setState("offline");
boot();
