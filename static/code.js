// Mike — the Code area, in the manner of Claude Code: project, conversation with expandable steps and diffs,
// changes + files panel, a collapsible terminal drawer, slash commands and @ file mentions.
let api = { send() {}, ask() {}, stop() {}, newChat() {} };
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const pref = {
  get(k, d) { try { const v = localStorage.getItem(`mike.cx.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`mike.cx.${k}`, JSON.stringify(v)); } catch {} },
};
const C = {
  project: null, projects: [], terms: {}, active: "", changes: [], files: [], brain: "", tab: "changes",
  panelOpen: pref.get("panel", false), termOpen: pref.get("term", false), termH: pref.get("termH", 240), plan: false,
  turn: null, working: false, workStart: 0, workTimer: null, diff: null, view: null, openDirs: new Set(), stick: true,
  pop: null, popItems: [], popIdx: 0, refresh: null, toastT: null, steps: 0,
};

const ICON = {
  run: '<path d="M5 7l4 4-4 4M11 16h7"/>', file: '<path d="M7 4.5h6.5L18 9v10.5H7zM13.5 4.5V9H18"/>', read: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  edit: '<path d="M4.5 19.5l1-4L15.8 5.2a1.8 1.8 0 0 1 2.5 0l.5.5a1.8 1.8 0 0 1 0 2.5L8.5 18.5z"/>', term: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M7.5 10l3 2.5-3 2.5"/>',
  folder: '<path d="M4.5 7.5a2 2 0 0 1 2-2h3.3l2 2.2h5.7a2 2 0 0 1 2 2v7.3a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2z"/>', check: '<path d="M5.5 12.5l4 4 9-9"/>',
  chev: '<path d="M9 6l6 6-6 6"/>', plus: '<path d="M12 5v14M5 12h14"/>', globe: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c3 3 3 13 0 16M12 4c-3 3-3 13 0 16"/>',
};
const TOOL = {
  Bash: ["Ran", "run"], Write: ["Created", "edit"], Edit: ["Edited", "edit"], MultiEdit: ["Edited", "edit"], NotebookEdit: ["Edited", "edit"],
  Read: ["Read", "file"], Glob: ["Searched files", "read"], Grep: ["Searched", "read"], search_code: ["Searched", "read"], WebSearch: ["Searched the web", "globe"], WebFetch: ["Fetched", "globe"],
  terminal_start: ["Started terminal", "term"], terminal_read: ["Read terminal", "term"], terminal_send: ["Typed in terminal", "term"], terminal_interrupt: ["Pressed Ctrl-C", "term"],
  terminal_stop: ["Stopped terminal", "term"], terminal_show: ["Opened terminal window", "term"], project_use: ["Opened project", "folder"], project_find: ["Looked for a project", "folder"],
  project_info: ["Checked the project", "folder"], self_check: ["Checked Mike's code", "check"], mike_restart: ["Restarting Mike", "run"], find_skill: ["Looked for a skill", "read"], use_skill: ["Loaded a skill", "read"],
  run_command: ["Ran", "run"], read_file: ["Read", "file"], write_file: ["Created", "edit"], edit_file: ["Edited", "edit"],
};
const SLASH = [
  { cmd: "/run", desc: "Run the project", cmdKey: "run" }, { cmd: "/test", desc: "Run the tests", cmdKey: "test" }, { cmd: "/build", desc: "Build the project", cmdKey: "build" },
  { cmd: "/diff", desc: "Review changes" }, { cmd: "/commit", desc: "Commit the changes" }, { cmd: "/terminal", desc: "Show or hide the terminal" },
  { cmd: "/new", desc: "New project" }, { cmd: "/plan", desc: "Toggle plan mode" }, { cmd: "/clear", desc: "Clear this conversation view" }, { cmd: "/stop", desc: "Stop what Mike is doing" },
];

function h(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
function icon(name, cls) { const s = document.createElementNS(SVG, "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.innerHTML = ICON[name] || ICON.run; if (cls) s.setAttribute("class", cls); return s; }
const near = (n) => n.scrollHeight - n.scrollTop - n.clientHeight < 80;
function toast(text, error) {
  const t = $("cxToast"); t.textContent = text; t.classList.toggle("err", !!error); t.classList.remove("hidden");
  clearTimeout(C.toastT); C.toastT = setTimeout(() => t.classList.add("hidden"), 3200);
}
// Inline `code` and **bold**, built from text nodes (never innerHTML).
function rich(parent, text) {
  text.split(/(`[^`\n]+`)/g).forEach((part) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) parent.append(h("code", "", part.slice(1, -1)));
    else part.split(/(\*\*[^*\n]+\*\*)/g).forEach((p2) => {
      if (p2.startsWith("**") && p2.endsWith("**") && p2.length > 4) parent.append(h("strong", "", p2.slice(2, -2)));
      else if (p2) parent.append(document.createTextNode(p2));
    });
  });
}

// ---------------------------------------------------------------- top bar
function renderBar() {
  const p = C.project;
  $("cxProjName").textContent = p ? p.name : "Choose a project";
  const br = $("cxBranch"); br.classList.toggle("hidden", !p?.branch);
  if (p?.branch) $("cxBranchName").textContent = p.branch + (C.changes.length ? ` · ${C.changes.length} changed` : " · clean");
  const cmds = $("cxCmds"); cmds.replaceChildren();
  for (const [k, v] of Object.entries(p?.commands || {}).slice(0, 4)) {
    const b = h("button", "cx-chip", k[0].toUpperCase() + k.slice(1)); b.type = "button"; b.title = v;
    b.onclick = () => runCmd(k, v);
    cmds.append(b);
  }
  const live = Object.values(C.terms).filter((t) => t.running).length;
  const tb = $("cxTermBadge"); tb.textContent = live; tb.classList.toggle("hidden", !live);
  const pb = $("cxPanelBadge"); pb.textContent = C.changes.length; pb.classList.toggle("hidden", !C.changes.length);
  $("cxTermBtn").classList.toggle("on", C.termOpen); $("cxPanelBtn").classList.toggle("on", C.panelOpen);
  $("cxBrain").textContent = C.brain;
  $("cxHint").textContent = p ? p.path.replace(/^\/Users\/[^/]+/, "~") : "";
  renderEmpty();
}
function runCmd(k, v) { setTerm(true); api.send({ type: "term_start", name: k, command: v, cwd: C.project?.path || "" }); }

// ---------------------------------------------------------------- project menu + new project
function closePop() { document.querySelectorAll(".cx-menu").forEach((n) => n.remove()); }
function openProjectMenu() {
  closePop();
  const m = h("div", "cx-menu");
  const q = h("input", "field"); q.placeholder = "Find a project"; q.spellcheck = false; m.append(q);
  const list = h("div", "cx-menu-list"); m.append(list);
  const draw = () => {
    list.replaceChildren();
    const f = q.value.trim().toLowerCase();
    for (const p of C.projects.filter((x) => !f || x.name.toLowerCase().includes(f)).slice(0, 14)) {
      const r = h("button", "cx-menu-item" + (C.project && p.path === C.project.path ? " on" : "")); r.type = "button";
      r.append(icon("folder"), h("span", "", p.name), h("small", "", p.path.replace(/^\/Users\/[^/]+/, "~")));
      r.onclick = () => { closePop(); api.send({ type: "project_use", path: p.path }); };
      list.append(r);
    }
  };
  q.oninput = draw; draw();
  const sep = h("div", "cx-menu-sep"); m.append(sep);
  const nw = h("button", "cx-menu-item strong"); nw.type = "button"; nw.append(icon("plus"), h("span", "", "New project…")); nw.onclick = () => { closePop(); openNew(); };
  const op = h("button", "cx-menu-item"); op.type = "button"; op.append(icon("folder"), h("span", "", "Open a folder…"));
  op.onclick = () => {
    closePop(); const p = prompt("Folder path to open (for example ~/code/my-app)"); if (p && p.trim()) api.send({ type: "project_open", path: p.trim() });
  };
  m.append(nw, op);
  $("view-code").append(m);
  q.focus();
  setTimeout(() => document.addEventListener("pointerdown", function once(e) { if (!m.contains(e.target) && e.target.closest("#cxProj") == null) { closePop(); } if (!document.body.contains(m)) document.removeEventListener("pointerdown", once); }), 0);
}
function openNew() {
  $("cxNew").classList.remove("hidden"); $("cxNewName").value = ""; setTimeout(() => $("cxNewName").focus(), 30);
}
function closeNew() { $("cxNew").classList.add("hidden"); }

// ---------------------------------------------------------------- empty state
function renderEmpty() {
  const p = C.project;
  $("cxEmptyTitle").textContent = p ? `Work on ${p.name}` : "What should we build?";
  $("cxEmptySub").textContent = p ? "Tell Mike what to build, fix or explain. He reads the code, makes the changes and runs them." : "Pick a project, or start a new one.";
  const s = $("cxSugg"); s.replaceChildren();
  const ideas = p ? ["Explain how this project works", "Run the tests and fix what fails", "Find bugs and fix them", "Add a README"] : [];
  ideas.forEach((t) => { const b = h("button", "cx-sugg-btn", t); b.type = "button"; b.onclick = () => submit(t); s.append(b); });
  if (!p) { const b = h("button", "cx-sugg-btn primary", "New project"); b.type = "button"; b.onclick = openNew; s.append(b); const c = h("button", "cx-sugg-btn", "Choose a project"); c.type = "button"; c.onclick = openProjectMenu; s.append(c); }
  $("cxEmpty").classList.toggle("hidden", $("cxFeed").children.length > 1);
}

// ---------------------------------------------------------------- feed
function feedAdd(node) {
  const f = $("cxFeed"), stick = near(f) || C.stick;
  const w = $("cxWorking"); if (w) w.remove();
  f.append(node); if (C.working) f.append(working());
  $("cxEmpty").classList.add("hidden");
  while (f.children.length > 160) f.children[1].remove();
  if (stick) f.scrollTop = f.scrollHeight;
}
function working() {
  let n = $("cxWorking");
  if (!n) { n = h("div", "cx-working"); n.id = "cxWorking"; n.append(h("span", "cx-dots"), h("span", "t", "Working…"), h("small", "")); }
  return n;
}
function tickWorking() {
  const n = $("cxWorking"); if (!n) return;
  const s = Math.floor((Date.now() - C.workStart) / 1000);
  n.querySelector("small").textContent = s >= 3 ? `${s}s` : "";
}
function diffBlock(oldT, newT) {
  const box = h("div", "cx-diff");
  const add = (cls, t) => { box.append(h("div", "ln " + cls, (cls === "del" ? "− " : "+ ") + t)); };
  String(oldT || "").split("\n").slice(0, 14).forEach((l) => add("del", l));
  String(newT || "").split("\n").slice(0, 14).forEach((l) => add("add", l));
  return box;
}
function stepNode(m) {
  const a = m.args || {};
  const [verb, ic] = TOOL[m.name] || [m.name.replace(/_/g, " "), "run"];
  const isEdit = ic === "edit";
  const file = (a.file || "").split("/").pop();
  const label = isEdit || ic === "file" ? file : (a.command || m.detail || a.pattern || "").slice(0, 120);
  const body = isEdit ? (a.old || a.new ? diffBlock(a.old, a.new) : (a.content ? (() => { const d = h("div", "cx-diff"); a.content.split("\n").slice(0, 16).forEach((l) => d.append(h("div", "ln add", "+ " + l))); return d; })() : null))
    : (a.command && a.command.length > 70 ? h("pre", "cx-pre", a.command) : null);
  const row = h("div", "cx-step");
  const head = h("button", "cx-step-head"); head.type = "button";
  head.append(icon(ic, "ic"), h("b", "", verb), h("code", "", label));
  if (body) { head.append(icon("chev", "chev")); head.onclick = () => row.classList.toggle("open"); } else head.classList.add("flat");
  row.append(head);
  if (body) { const wrap = h("div", "cx-step-body"); wrap.append(body); row.append(wrap); }
  if (a.file) row.title = a.file;
  return row;
}
function todoNode(todos) {
  const box = h("div", "cx-todos");
  box.append(h("div", "cx-todos-h", "Plan"));
  todos.forEach((t) => {
    const r = h("div", "cx-todo " + t.status);
    r.append(h("i", "box"), h("span", "", t.text));
    box.append(r);
  });
  return box;
}
function feedEvent(m) {
  switch (m.type) {
    case "user": C.turn = null; feedAdd(h("div", "cx-user", m.text)); break;
    case "assistant_delta": {
      if (!C.turn) { C.turn = h("div", "cx-say"); C.turn._t = ""; feedAdd(C.turn); }
      C.turn._t += m.text;
      C.turn.replaceChildren(); rich(C.turn, C.turn._t.replace(/^\s+/, ""));
      const f = $("cxFeed"); if (near(f)) f.scrollTop = f.scrollHeight;
      break;
    }
    case "assistant_done":
      if (C.turn && !C.turn._t.trim()) C.turn.remove();
      C.turn = null;
      if (m.error) feedAdd(h("div", "cx-note err", "Something went wrong."));
      else if (m.interrupted) feedAdd(h("div", "cx-note", "Stopped."));
      clearTimeout(C.refresh); C.refresh = setTimeout(() => api.send({ type: "code_get" }), 500);
      break;
    case "tool": {
      if (m.name === "ToolSearch") break;
      C.turn = null;
      if (m.name === "TodoWrite" && m.args?.todos) {
        const old = $("cxFeed").querySelector(".cx-todos.live"); if (old) old.remove();
        const t = todoNode(m.args.todos); t.classList.add("live"); feedAdd(t); break;
      }
      if (m.name === "TodoWrite") break;
      feedAdd(stepNode(m));
      break;
    }
  }
}

// ---------------------------------------------------------------- right panel: changes + files
function setPanel(on) { C.panelOpen = on; pref.set("panel", on); $("cxPanel").classList.toggle("hidden", !on); renderBar(); if (on) refreshPanel(); }
function refreshPanel() {
  document.querySelectorAll(".cx-panel-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === C.tab));
  $("cxChgCount").textContent = C.changes.length || "";
  $("cxCommit").classList.toggle("hidden", !(C.tab === "changes" && C.changes.length && !C.diff));
  const body = $("cxPanelBody"); body.replaceChildren();
  if (!C.project) { body.append(h("div", "cx-p-empty", "Choose a project first.")); return; }
  if (C.diff) return renderDiff(body);
  if (C.view) return renderFileView(body);
  if (C.tab === "changes") return renderChanges(body);
  return renderFiles(body);
}
function renderChanges(body) {
  if (!C.changes.length) { body.append(h("div", "cx-p-empty", "No changes yet. Everything Mike edits shows up here.")); return; }
  const add = C.changes.reduce((n, c) => n + c.add, 0), del = C.changes.reduce((n, c) => n + c.del, 0);
  const sum = h("div", "cx-sum"); sum.append(h("span", "", `${C.changes.length} file${C.changes.length > 1 ? "s" : ""}`), h("span", "add", `+${add}`), h("span", "del", `−${del}`));
  body.append(sum);
  C.changes.forEach((c) => {
    const r = h("button", "cx-file"); r.type = "button";
    r.append(h("i", "st " + c.status, c.status), h("span", "name", c.file.split("/").pop()), h("small", "", c.file.includes("/") ? c.file.replace(/\/[^/]+$/, "") : ""),
             h("span", "cnt", ""));
    const cnt = r.querySelector(".cnt"); cnt.append(h("em", "add", `+${c.add}`), h("em", "del", `−${c.del}`));
    r.onclick = () => { C.diff = { file: c.file, text: null }; refreshPanel(); api.send({ type: "diff_get", file: c.file }); };
    body.append(r);
  });
}
function back(body, title) {
  const r = h("div", "cx-back"); const b = h("button", "cx-mini"); b.type = "button"; b.append(icon("chev", "flip"));
  b.onclick = () => { C.diff = null; C.view = null; refreshPanel(); };
  r.append(b, h("b", "", title)); body.append(r);
}
function renderDiff(body) {
  back(body, C.diff.file);
  const box = h("div", "cx-code");
  if (C.diff.text == null) box.append(h("div", "cx-p-empty", "Loading…"));
  else (C.diff.text || "No textual changes.").split("\n").forEach((l) => {
    if (l.startsWith("diff ") || l.startsWith("index ") || l.startsWith("--- ") || l.startsWith("+++ ")) return;
    box.append(h("div", "ln " + (l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""), l || " "));
  });
  body.append(box);
}
function renderFileView(body) {
  back(body, C.view.file);
  const box = h("div", "cx-code");
  if (C.view.text == null) box.append(h("div", "cx-p-empty", "Loading…"));
  else C.view.text.split("\n").slice(0, 1500).forEach((l, i) => { const r = h("div", "ln"); r.append(h("span", "no", String(i + 1)), document.createTextNode(l || " ")); box.append(r); });
  body.append(box);
}
function renderFiles(body) {
  if (!C.files.length) { body.append(h("div", "cx-p-empty", "No files.")); return; }
  const tree = {};
  C.files.forEach((f) => { let n = tree; f.split("/").forEach((part, i, a) => { n = n[part] ??= i === a.length - 1 ? null : {}; }); });
  const draw = (node, prefix, depth) => {
    Object.keys(node).sort((a, b) => (node[b] === null) - (node[a] === null) || a.localeCompare(b)).forEach((k) => {
      const path = prefix + k;
      if (node[k] === null) {
        const r = h("button", "cx-tree file"); r.type = "button"; r.style.paddingLeft = 10 + depth * 14 + "px";
        r.append(icon("file"), h("span", "", k)); r.onclick = () => { C.view = { file: path, text: null }; refreshPanel(); api.send({ type: "file_read", file: path }); };
        body.append(r);
      } else {
        const open = C.openDirs.has(path);
        const r = h("button", "cx-tree dir" + (open ? " open" : "")); r.type = "button"; r.style.paddingLeft = 10 + depth * 14 + "px";
        r.append(icon("chev", "chev"), icon("folder"), h("span", "", k));
        r.onclick = () => { open ? C.openDirs.delete(path) : C.openDirs.add(path); refreshPanel(); };
        body.append(r);
        if (open) draw(node[k], path + "/", depth + 1);
      }
    });
  };
  draw(tree, "", 0);
}

// ---------------------------------------------------------------- terminal drawer
function setTerm(on) {
  C.termOpen = on; pref.set("term", on);
  const t = $("cxTerm"); t.classList.toggle("open", on); t.style.height = on ? C.termH + "px" : "";
  renderBar(); if (on) renderTerm();
}
function renderTabs() {
  const tabs = $("cxTabs"); tabs.replaceChildren();
  const names = Object.keys(C.terms);
  if (!names.includes(C.active)) C.active = names[0] || "";
  names.forEach((n) => {
    const t = C.terms[n], b = h("button", "cx-tab" + (n === C.active ? " on" : "")); b.type = "button";
    b.append(h("i", "dot " + (t.running ? "live" : "done")), h("span", "", n));
    b.onclick = () => { C.active = n; renderTabs(); renderTerm(); }; tabs.append(b);
  });
  const plus = h("button", "cx-tab plus", "+"); plus.type = "button"; plus.title = "New shell";
  plus.onclick = () => { setTerm(true); api.send({ type: "term_start", name: "shell" + (names.length + 1), command: "", cwd: C.project?.path || "" }); };
  tabs.append(plus);
  $("cxTEmpty").classList.toggle("hidden", names.length > 0); $("cxTForm").classList.toggle("hidden", !names.length);
}
function renderTerm() {
  const out = $("cxOut"), t = C.terms[C.active], stick = near(out);
  out.textContent = t ? t.text + (!t.running ? `\n[ended${t.exit != null ? ` · exit ${t.exit}` : ""}]` : "") : "";
  if (stick || C.stick) out.scrollTop = out.scrollHeight;
}

// ---------------------------------------------------------------- composer
function submit(text) {
  text = text.trim(); if (!text) return;
  closePopover();
  if (text.startsWith("/")) { const c = SLASH.find((s) => s.cmd === text.split(/\s/)[0]); if (c) { slash(c, text.slice(c.cmd.length).trim()); return; } }
  if (!C.project) { toast("Choose or create a project first.", true); openProjectMenu(); return; }
  api.ask((C.plan ? "Plan first: outline what you'd change in a few sentences and wait for my OK before touching any files. " : "") + text);
  C.stick = true;
}
function slash(c, rest) {
  switch (c.cmd) {
    case "/run": case "/test": case "/build": { const v = C.project?.commands?.[c.cmdKey]; v ? runCmd(c.cmdKey, v) : toast(`This project has no ${c.cmdKey} command.`, true); break; }
    case "/diff": C.tab = "changes"; C.diff = null; setPanel(true); break;
    case "/commit": C.tab = "changes"; C.diff = null; setPanel(true); setTimeout(() => $("cxCommitMsg").focus(), 50); break;
    case "/terminal": setTerm(!C.termOpen); break;
    case "/new": openNew(); break;
    case "/plan": setPlan(!C.plan); break;
    case "/clear": { const f = $("cxFeed"); [...f.children].forEach((n) => n.id !== "cxEmpty" && n.remove()); renderEmpty(); break; }
    case "/stop": api.stop(); break;
  }
  $("cxInput").value = ""; grow();
}
function setPlan(on) { C.plan = on; $("cxPlan").classList.toggle("on", on); $("cxInput").placeholder = on ? "Describe what you want. Mike will plan first and wait for your OK…" : "Ask Mike to build, fix or explain…  /  for commands,  @  for files"; }
function grow() { const t = $("cxInput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; }
function closePopover() { C.pop = null; $("cxPop").classList.add("hidden"); }
function showPopover(items, render) {
  C.popItems = items; C.popIdx = 0; C.pop = render;
  const pop = $("cxPop"); pop.replaceChildren();
  items.forEach((it, i) => {
    const r = h("button", "cx-pop-item" + (i === 0 ? " on" : "")); r.type = "button"; render(r, it);
    r.onmousedown = (e) => { e.preventDefault(); pickPopover(i); };
    pop.append(r);
  });
  pop.classList.toggle("hidden", !items.length);
}
function pickPopover(i) {
  const it = C.popItems[i]; if (!it) return;
  const ta = $("cxInput");
  if (it.cmd) { closePopover(); ta.value = ""; slash(it, ""); return; }
  // @ mention: replace the token before the cursor
  const pos = ta.selectionStart, before = ta.value.slice(0, pos).replace(/@[^\s@]*$/, "@" + it + " ");
  ta.value = before + ta.value.slice(pos); ta.selectionStart = ta.selectionEnd = before.length; closePopover(); ta.focus();
}
function movePopover(d) {
  C.popIdx = (C.popIdx + d + C.popItems.length) % C.popItems.length;
  [...$("cxPop").children].forEach((n, i) => n.classList.toggle("on", i === C.popIdx));
  $("cxPop").children[C.popIdx]?.scrollIntoView({ block: "nearest" });
}
function onInput() {
  grow();
  const ta = $("cxInput"), v = ta.value, pos = ta.selectionStart;
  if (/^\/\S*$/.test(v)) {
    const items = SLASH.filter((s) => s.cmd.startsWith(v.toLowerCase()));
    showPopover(items, (r, s) => { r.append(h("b", "", s.cmd), h("span", "", s.desc)); });
    return;
  }
  const m = v.slice(0, pos).match(/@([^\s@]*)$/);
  if (m) {
    if (!C.files.length) api.send({ type: "files_get" });
    const q = m[1].toLowerCase();
    const items = C.files.filter((f) => f.toLowerCase().includes(q)).slice(0, 8);
    showPopover(items, (r, f) => { r.append(icon("file"), h("span", "", f)); });
    return;
  }
  closePopover();
}

// ---------------------------------------------------------------- wiring
export const Code = {
  init(a) {
    api = a;
    $("cxProj").onclick = (e) => { e.stopPropagation(); document.querySelector(".cx-menu") ? closePop() : openProjectMenu(); };
    $("cxTermBtn").onclick = () => setTerm(!C.termOpen);
    $("cxPanelBtn").onclick = () => setPanel(!C.panelOpen);
    $("cxTClose").onclick = () => setTerm(false);
    $("cxPlan").onclick = () => setPlan(!C.plan);
    $("cxStop").onclick = () => api.stop();
    $("cxForm").onsubmit = (e) => { e.preventDefault(); const t = $("cxInput"); const v = t.value; if (v.trim() && !(C.popItems.length && C.pop && v.startsWith("/") && !SLASH.find((s) => s.cmd === v.trim()))) { t.value = ""; grow(); submit(v); } };
    $("cxInput").oninput = onInput;
    $("cxInput").onkeydown = (e) => {
      if (C.pop && C.popItems.length) {
        if (e.key === "ArrowDown") { e.preventDefault(); movePopover(1); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); movePopover(-1); return; }
        if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickPopover(C.popIdx); return; }
        if (e.key === "Escape") { e.preventDefault(); closePopover(); return; }
      }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("cxForm").requestSubmit(); }
    };
    $("cxInput").onblur = () => setTimeout(closePopover, 120);
    document.querySelectorAll(".cx-panel-tabs button").forEach((b) => (b.onclick = () => { C.tab = b.dataset.tab; C.diff = C.view = null; if (C.tab === "files") api.send({ type: "files_get" }); refreshPanel(); }));
    $("cxCommit").onsubmit = (e) => { e.preventDefault(); const m = $("cxCommitMsg"); if (m.value.trim()) { api.send({ type: "git_commit", message: m.value.trim() }); m.value = ""; } };
    $("cxTForm").onsubmit = (e) => { e.preventDefault(); const i = $("cxTInput"); if (C.active) { api.send({ type: "term_send", name: C.active, text: i.value, enter: true }); i.value = ""; } };
    $("cxTCtrlC").onclick = () => C.active && api.send({ type: "term_key", name: C.active, key: "ctrl-c" });
    $("cxTStop").onclick = () => C.active && api.send({ type: "term_stop", name: C.active });
    $("cxTShow").onclick = () => C.active && api.send({ type: "term_show", name: C.active });
    $("cxOut").addEventListener("scroll", () => { C.stick = near($("cxOut")); });
    $("cxFeed").addEventListener("scroll", () => { C.stick = near($("cxFeed")); });
    // drag the terminal's top edge to resize
    $("cxGrip").onpointerdown = (e) => {
      e.preventDefault(); const startY = e.clientY, startH = $("cxTerm").offsetHeight; $("cxGrip").setPointerCapture(e.pointerId); $("cxTerm").classList.add("dragging");
      const move = (ev) => { C.termH = Math.max(120, Math.min(innerHeight * 0.7, startH + (startY - ev.clientY))); $("cxTerm").style.height = C.termH + "px"; };
      const up = () => { $("cxTerm").classList.remove("dragging"); pref.set("termH", Math.round(C.termH)); $("cxGrip").removeEventListener("pointermove", move); $("cxGrip").removeEventListener("pointerup", up); };
      $("cxGrip").addEventListener("pointermove", move); $("cxGrip").addEventListener("pointerup", up);
    };
    // new project dialog
    document.querySelectorAll("#cxNewTpl button").forEach((b) => (b.onclick = () => { document.querySelectorAll("#cxNewTpl button").forEach((x) => x.classList.toggle("on", x === b)); }));
    $("cxNewCancel").onclick = closeNew;
    $("cxNew").addEventListener("pointerdown", (e) => { if (e.target === $("cxNew")) closeNew(); });
    $("cxNewForm").onsubmit = (e) => {
      e.preventDefault(); const name = $("cxNewName").value.trim(); if (!name) { $("cxNewName").focus(); return; }
      api.send({ type: "project_new", name, template: document.querySelector("#cxNewTpl button.on")?.dataset.v || "empty", parent: $("cxNewParent").value.trim() });
      closeNew();
    };
    document.addEventListener("keydown", (e) => {
      if (!$("view-code") || $("view-code").classList.contains("hidden")) return;
      if (e.ctrlKey && e.key === "`") { e.preventDefault(); setTerm(!C.termOpen); }
      if (e.key === "Escape" && !$("cxNew").classList.contains("hidden")) closeNew();
      if (e.key === "Escape") closePop();
    });
    setInterval(tickWorking, 1000);
    setTerm(C.termOpen); setPanel(C.panelOpen); renderBar(); renderTabs();
  },
  opened() { api.send({ type: "code_get" }); },
  event(m) { feedEvent(m); },
  handle(m) {
    switch (m.type) {
      case "code_state":
        C.project = m.project && m.project.name ? m.project : null; C.projects = m.projects || []; C.changes = m.changes || []; C.brain = m.brain || "";
        C.terms = Object.fromEntries((m.terminals || []).map((t) => [t.name, t]));
        renderBar(); renderTabs(); renderTerm(); if (C.panelOpen) refreshPanel();
        break;
      case "term": C.terms[m.name] = m; if (!C.active) C.active = m.name; renderBar(); (m.name === C.active ? renderTerm : renderTabs)(); break;
      case "files": C.files = m.files || []; if (C.panelOpen && C.tab === "files") refreshPanel(); break;
      case "diff": if (C.diff && C.diff.file === m.file) { C.diff.text = m.text; refreshPanel(); } break;
      case "file": if (C.view && C.view.file === m.file) { C.view.text = m.text; refreshPanel(); } break;
      case "code_toast": toast(m.text, m.error); break;
    }
  },
  busy(on) {
    $("cxStop").classList.toggle("hidden", !on); $("cxSend").classList.toggle("hidden", on);
    if (on && !C.working) { C.working = true; C.workStart = Date.now(); const f = $("cxFeed"); if ($("cxEmpty").classList.contains("hidden")) f.append(working()); }
    if (!on) { C.working = false; $("cxWorking")?.remove(); }
  },
};
