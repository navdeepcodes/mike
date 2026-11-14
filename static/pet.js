// Mike's desktop pet: follows Mike's state over /ws/pet and acts it out.
import { PETS, CANVAS, drawPet, drawNone } from "/static/pets.js";

const native = window.webkit?.messageHandlers?.pet;
const toNative = (m) => { try { native?.postMessage(m); } catch {} };
if (!native) document.body.classList.add("browser");

const $ = (id) => document.getElementById(id);
const stage = $("stage"), wrap = $("wrap"), canvas = $("sprite"), ctx = canvas.getContext("2d");
const bubble = $("bubble"), kicker = $("kicker"), text = $("text"), hint = $("hint");

const ACTIVE = new Set(["listening", "thinking", "speaking", "confirming"]);
const S = {
  ws: null, connected: false, state: "offline", pet: "mike", mode: "active", present: null, main: true,
  confirm: null, happyUntil: 0, lingerUntil: 0, summonUntil: 0, look: [0, 0], visible: false,
  blinkAt: 0, blinkUntil: 0, mouthOpen: false, mouthAt: 0, bubbleTimer: null,
};

// ---------------------------------------------------------------- mood + visibility
function mood(now) {
  if (!S.connected) return "off";
  switch (S.state) {
    case "confirming": return "ask";
    case "listening": return "listen";
    case "thinking": return "think";
    case "speaking": return "talk";
  }
  if (now < S.happyUntil) return "happy";
  if (S.present === false || S.state === "offline" || S.state === "elsewhere") return "sleep";
  return "idle";
}
function wantVisible(now) {
  if (S.dismissed) return false;     // sent away by voice; comes back the next time he's called
  if (S.mode === "off") return now < S.summonUntil;
  if (S.mode === "always") return true;
  return ACTIVE.has(S.state) || now < S.lingerUntil || now < S.summonUntil;
}
function setVisible(on) {
  if (on === S.visible) return;
  S.visible = on;
  if (on) {
    toNative({ type: "visible", on: true });
    stage.classList.remove("hidden");
    wrap.classList.remove("pop"); void wrap.offsetWidth; wrap.classList.add("pop");
  } else {
    stage.classList.add("hidden");
    hideBubble();
    setTimeout(() => { if (!S.visible) toNative({ type: "visible", on: false }); }, 260);
  }
}
function hop() { wrap.classList.remove("hop", "pop"); void wrap.offsetWidth; wrap.classList.add("hop"); }

// ---------------------------------------------------------------- speech bubble
function showBubble(kind, k, t, h = "", holdMs = 0) {
  bubble.className = `bubble show ${kind}`;
  kicker.textContent = k || ""; text.textContent = t || ""; hint.textContent = h || "";
  clearTimeout(S.bubbleTimer);
  if (holdMs) S.bubbleTimer = setTimeout(hideBubble, holdMs);
}
function hideBubble() { bubble.classList.remove("show"); clearTimeout(S.bubbleTimer); }

const TOOL_LABEL = {
  look: "Looking through the camera…", screenshot: "Looking at your screen…", notify: "Sending a notification…",
  Read: "Reading a file…", Write: "Writing a file…", Edit: "Editing a file…", Glob: "Searching your files…",
  Grep: "Searching your files…", WebSearch: "Searching the web…", WebFetch: "Reading a web page…", Bash: "Running a command…",
};

// ---------------------------------------------------------------- socket
function send(m) { if (S.ws?.readyState === 1) S.ws.send(JSON.stringify(m)); }
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws/pet`);
  S.ws = ws;
  ws.onopen = () => { S.connected = true; };
  ws.onclose = () => { S.connected = false; S.state = "offline"; setTimeout(connect, 2000); };
  ws.onmessage = (ev) => { try { handle(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
}
function handle(m) {
  const now = performance.now();
  switch (m.type) {
    case "hello":
      S.state = m.state || "idle"; S.present = m.present; S.main = m.main;
      setPetLocal(m.pet, m.mode);
      break;
    case "pet":
      setPetLocal(m.pet, m.mode);
      S.summonUntil = now + 4000; hop();
      break;
    case "state": {
      const prev = S.state;
      S.state = m.state;
      if (ACTIVE.has(prev) && !ACTIVE.has(m.state)) S.lingerUntil = now + 6000;
      if (m.state === "listening" && prev !== "listening") { hop(); showBubble("listen", "Listening…", ""); }
      if (m.state === "thinking" && prev !== "thinking" && !bubble.classList.contains("show")) showBubble("think", "Thinking…", "");
      if (m.state === "idle" || m.state === "offline") setTimeout(() => { if (!ACTIVE.has(S.state)) hideBubble(); }, 3500);
      if (m.state === "elsewhere") showBubble("", "", "Mike is open in another window.", "", 4000);
      break;
    }
    case "chime": hop(); break;
    case "user": showBubble("think", "You said", m.text); break;
    case "tool":
      if (m.name !== "ToolSearch" && m.name !== "TodoWrite") showBubble("think", "Working", TOOL_LABEL[m.name] || `${m.name}…`);
      break;
    case "speak": if (m.text) showBubble("talk", "", m.text); break;
    case "assistant_done":
      if (!m.error && !m.interrupted) S.happyUntil = now + 1800;
      break;
    case "confirm":
      S.confirm = m; toNative({ type: "confirm", on: true });
      showBubble("ask", "Needs your OK", `Should I ${m.summary}?`, "Say yes or no, or right-click me");
      hop();
      break;
    case "confirm_done":
      S.confirm = null; toNative({ type: "confirm", on: false });
      showBubble("", "", m.allowed ? "On it." : "Okay, I won't.", "", 2500);
      break;
    case "presence":
      S.present = m.present;
      break;
    case "pet_dismiss":
      S.dismissed = !!m.on;
      if (S.dismissed) { S.summonUntil = 0; S.lingerUntil = 0; hideBubble(); }
      else { S.summonUntil = now + 4000; hop(); }
      break;
  }
}
function setPetLocal(pet, mode) {
  if (pet && (pet === "none" || PETS[pet])) S.pet = pet;
  if (mode) S.mode = mode;
  toNative({ type: "pet", pet: S.pet, mode: S.mode });
}

// ---------------------------------------------------------------- render loop
// Visibility runs on a timer: animation frames are paused while the window is hidden,
// so a hidden pet could otherwise never decide to show itself.
let lastWander = null;
setInterval(() => {
  const now = performance.now();
  setVisible(wantVisible(now));
  const md = mood(now);
  // Float around while relaxed; hold still while talking or asking so the bubble is easy to read.
  const wander = S.visible && md !== "talk" && md !== "ask";
  if (wander !== lastWander) { lastWander = wander; toNative({ type: "wander", on: wander }); }
}, 100);

function frame(nowMs) {
  const t = nowMs / 1000;
  const md = mood(nowMs);

  if (nowMs > S.blinkAt) { S.blinkUntil = nowMs + 140; S.blinkAt = nowMs + 2500 + Math.random() * 3500; }
  if (md === "talk" && nowMs > S.mouthAt) { S.mouthOpen = !S.mouthOpen || Math.random() < 0.3; S.mouthAt = nowMs + 90 + Math.random() * 90; }
  if (md !== "talk") S.mouthOpen = false;

  if (S.visible) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (S.pet === "none") drawNone(ctx, canvas.width, canvas.height, md, t);
    else {
      const scale = canvas.width / CANVAS;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.imageSmoothingEnabled = false;
      drawPet(ctx, S.pet, md, { t, look: S.look, blink: nowMs < S.blinkUntil, mouthOpen: S.mouthOpen });
    }
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ---------------------------------------------------------------- input
function click() {
  hop();
  if (S.confirm) { showBubble("ask", "Needs your OK", `Should I ${S.confirm.summary}?`, "Say yes or no"); return; }
  send({ type: "wake" });
}
// In a browser tab there is no native window to do this for us.
if (!native) {
  canvas.addEventListener("click", click);
  addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    const dx = (e.clientX - (r.left + r.width / 2)) / 200, dy = (e.clientY - (r.top + r.height / 2)) / 200;
    S.look = [Math.max(-1, Math.min(1, dx)), Math.max(-1, Math.min(1, dy))];
  });
}

// Hooks for Mike.app's native pet window.
window.pet = {
  click,
  look(dx, dy) { S.look = [Math.max(-1, Math.min(1, dx)), Math.max(-1, Math.min(1, dy))]; },
  face(dir) { wrap.classList.toggle("flip", dir < 0); },
  answer(allow) { if (S.confirm) send({ type: "approve", allow }); },
  summon(ms = 8000) { S.summonUntil = performance.now() + ms; hop(); },
  setPet(id) { send({ type: "set_pet", pet: id }); },
  setMode(mode) { send({ type: "set_pet", mode }); },
  current: () => ({ pet: S.pet, mode: S.mode }),
};
connect();
