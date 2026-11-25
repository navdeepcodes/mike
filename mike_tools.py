"""MCP server that gives Claude Code Mike's senses: camera, screen, notifications.

Claude Code starts this automatically (see data/mcp.json). The camera frame
comes from the HUD in your browser via Mike's local server.
"""
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

import config
import memory
import projects
import skills
import self_coding

MIKE_URL = os.environ.get("MIKE_URL", f"http://127.0.0.1:{os.environ.get('MIKE_PORT', '8777')}")
TASK_STATE_ROOT = Path(os.environ.get("MIKE_TASK_STATE_DIR", "/tmp/mike_tasks"))
mcp = FastMCP("mike")


@mcp.tool()
def look():
    """Take a photo with the user's webcam and look at it. Use when the user asks you to
    look at, see, or read something, or asks about themselves or their surroundings."""
    try:
        with urllib.request.urlopen(f"{MIKE_URL}/api/snapshot", timeout=8) as r:
            return Image(data=r.read(), format="jpeg")
    except urllib.error.HTTPError as e:
        return f"Camera unavailable: {e.read().decode(errors='replace')}"
    except Exception as e:
        return f"Camera unavailable: {e}"


@mcp.tool()
def screenshot():
    """Capture the user's main screen and look at it. Use when they refer to what is on screen,
    and before/after clicking so you can see what you're doing. Pixel coordinates in the image
    are exactly the coordinates the click tool takes."""
    try:
        with urllib.request.urlopen(f"{MIKE_URL}/api/screenshot", timeout=14) as r:
            return Image(data=r.read(), format="jpeg")
    except Exception:
        if os.name != "nt" and __import__("sys").platform == "darwin":
            return ("I can't see the screen yet. Tell the user, in one sentence: turn on Mike under System Settings > "
                    "Privacy & Security > Screen & System Audio Recording, then quit and reopen Mike.")
        return "I can't see the screen right now (the screenshot failed)."


def _native(op: str, **args):
    """Ask Mike.app to act (Accessibility API + real mouse/keyboard events)."""
    req = urllib.request.Request(f"{MIKE_URL}/api/native", data=json.dumps({"op": op, "args": args}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            out = json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": f"Mike didn't answer ({e}). He must be running."}
    return out


def _say(out: dict, ok: str = "Done.") -> str:
    if out.get("error"):
        extra = f" Options: {', '.join(out['choices'][:40])}" if out.get("choices") else ""
        return out["error"] + extra
    return ok


@mcp.tool()
def ui(app: str = "", find: str = "") -> str:
    """Read the interface of an app's front window: every button, field, link, tab, row and label,
    each with an [id] and its center x,y. This is the primary way to operate ANY app (it is faster and
    more exact than screenshots). app: app name (default: the app the user is in). find: optional
    word to filter the list (e.g. "search", "send", "play"). Then use press / set_text with the ids."""
    out = _native("ui", app=app, query=find)
    if out.get("error"):
        return out["error"]
    head = f"{out.get('app')} - window \"{out.get('window', '')}\""
    return f"{head}\n{out.get('elements') or '(nothing matched)'}\n{out.get('note', '')}".strip()


@mcp.tool()
def press(element: int) -> str:
    """Press/click an element by its [id] from the latest ui call (buttons, links, tabs, rows, checkboxes;
    on a text field it focuses it)."""
    return _say(_native("press", id=element), "Pressed.")


@mcp.tool()
def set_text(element: int, text: str) -> str:
    """Put text into a text field by its [id] from the latest ui call (replaces what's there).
    Follow with press_key("return") to submit if needed."""
    return _say(_native("set_text", id=element, text=text), "Text entered.")


@mcp.tool()
def menu(path: list[str], app: str = "") -> str:
    """Click a menu-bar command, e.g. path=["File", "New Window"] or ["View", "Show Sidebar"].
    Works in nearly every Mac app and is the most reliable way to trigger app commands.
    If a step isn't found, the reply lists the available items."""
    return _say(_native("menu", path=path, app=app), "Clicked.")


@mcp.tool()
def switch_to(app: str) -> str:
    """Bring an already-running app to the front (use `open -a` to launch one that isn't running)."""
    return _say(_native("activate", app=app), f"{app} is in front.")


@mcp.tool()
def click(x: int, y: int, double: bool = False, right: bool = False) -> str:
    """Click at screen coordinates (from ui's @x,y or a screenshot). Prefer press(id) when the element is in ui."""
    return _say(_native("click", x=x, y=y, double=double, right=right), "Clicked.")


@mcp.tool()
def type_text(text: str) -> str:
    """Type text into whatever has keyboard focus (any language, emoji fine). Newlines press Return."""
    return _say(_native("type", text=text), "Typed.")


@mcp.tool()
def press_key(key: str, modifiers: list[str] | None = None) -> str:
    """Press a key or shortcut in the front app. key: a letter/digit/symbol, or return, tab, space, escape,
    delete, forwarddelete, up, down, left, right, home, end, pageup, pagedown, f1-f12.
    modifiers: any of command, option, shift, control. Example: key="t", modifiers=["command"] = new tab."""
    return _say(_native("key", key=key, modifiers=modifiers or []), "Pressed.")


@mcp.tool()
def scroll(direction: str = "down", amount: int = 5, x: int | None = None, y: int | None = None) -> str:
    """Scroll the front app (or at x,y). direction: up, down, left, right. amount: lines (5 = a bit, 20 = a lot)."""
    n = max(1, min(int(amount), 60))
    dy = n if direction == "up" else -n if direction == "down" else 0
    dx = n if direction == "left" else -n if direction == "right" else 0
    args = {"dx": dx, "dy": dy}
    if x is not None and y is not None:
        args.update(x=x, y=y)
    return _say(_native("scroll", **args), "Scrolled.")


@mcp.tool()
def media(action: str, times: int = 1) -> str:
    """Press a hardware media key, exactly like the keyboard's top row. Works for whatever is playing
    (Spotify, Music, YouTube in a browser...). action: play_pause, next, previous, volume_up,
    volume_down, mute, brightness_up, brightness_down. times: repeat (each volume step ~6%,
    each brightness step ~6%; brightness is Mac-only). On a Mac, an exact volume is osascript "set volume output volume N"."""
    return _say(_native("media", action=action, times=times), "Done.")


@mcp.tool()
def keep_going(why: str = "") -> str:
    """Call this right away when the user's request will take many steps or run for a while (playing a
    game, repeating an action, a long job, anything you'd otherwise stop partway through). Mike then
    keeps nudging you to continue after each of your messages until you call task_complete."""
    r = _api("/api/auto", {"on": True, "why": why})
    return "Okay, keep working step by step until it's done." if not r.get("error") else "Noted."


@mcp.tool()
def task_complete(summary: str = "") -> str:
    """Call when a keep_going task is finished (goal reached, game over) or you can't continue.
    Then give the user one short sentence about how it went."""
    _api("/api/auto", {"on": False})
    return "Okay. Tell the user briefly how it went."


# ---- building software: terminals, projects, and Mike building Mike ---------------------------------
@mcp.tool()
def terminal_start(name: str = "main", command: str = "", cwd: str = "") -> str:
    """Start a terminal session that keeps running between your messages: a dev server, a test watcher, a build,
    or (empty command) an interactive shell. It runs in the active project's folder unless cwd is given.
    Look at what it prints with terminal_read."""
    return _api("/api/term", {"op": "start", "name": name, "command": command, "cwd": cwd}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def terminal_read(name: str = "main", lines: int = 40, wait: float = 0) -> str:
    """Read the latest output of a terminal session. wait=N seconds waits for it to print and settle first
    (use 2 to 10 right after starting or sending something)."""
    return _api("/api/term", {"op": "read", "name": name, "lines": lines, "wait": wait}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def terminal_send(name: str = "main", text: str = "", enter: bool = True) -> str:
    """Type into a terminal session (a command into its shell, an answer to a prompt). Then terminal_read."""
    return _api("/api/term", {"op": "send", "name": name, "text": text, "enter": enter}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def terminal_interrupt(name: str = "main") -> str:
    """Press Ctrl-C in a terminal session."""
    return _api("/api/term", {"op": "interrupt", "name": name}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def terminal_stop(name: str = "main") -> str:
    """End a terminal session and everything running in it."""
    return _api("/api/term", {"op": "stop", "name": name}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def terminal_list() -> str:
    """List the terminal sessions and whether they're running."""
    return _api("/api/term", {"op": "list"}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def terminal_show(name: str = "main") -> str:
    """Open a terminal session's live output in a Terminal window, when the user wants to watch it."""
    return _api("/api/term", {"op": "show", "name": name}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def project_find(query: str = "") -> str:
    """Find a code project on this Mac by the name the user said ("mike", "the tv assistant"). Empty lists the recent ones."""
    return _api("/api/project", {"op": "find", "query": query}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def project_use(path: str) -> str:
    """Make a folder the active project (shown in the Code view, and where terminals start). Returns its stack,
    git state and the commands to run and test it."""
    return _api("/api/project", {"op": "use", "path": path}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def project_info() -> str:
    """The active project: stack, git branch and changes, run/test commands."""
    return _api("/api/project", {"op": "info"}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def self_check() -> str:
    """After changing Mike's own code (the mike folder), compile everything. 'OK' or exactly what's broken."""
    return _api("/api/self", {"op": "check"}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def mike_restart() -> str:
    """Restart Mike so changes to his own code take effect. It checks the code first and refuses if anything is
    broken. Tell the user first; Mike is gone for under a minute and this conversation continues after."""
    return _api("/api/self", {"op": "restart"}).get("text", "Couldn't reach Mike.")


@mcp.tool()
def dismiss_pet() -> str:
    """Call when the user tells you to go away, close, be quiet, stop or leave them alone and nothing else was asked.
    Hides the pet until they call you again. Say nothing afterwards."""
    _api("/api/dismiss", {})
    return "Done. Say nothing."


@mcp.tool()
def find_skill(query: str) -> str:
    """Find ready-made know-how (steps plus scripts) for making PDFs, documents, slides, spreadsheets, images,
    simulations, animations or websites. Say what you want to make; returns matching skill names."""
    return skills.find(query)


@mcp.tool()
def use_skill(name: str) -> str:
    """Load a skill's instructions by name (from find_skill), then follow them step by step."""
    return skills.load(name)


# ---- reminders / timers / alarms -----------------------------------------------
def _api(path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"{MIKE_URL}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def set_reminder(text: str, in_minutes: float | None = None, at: str = "", repeat: str = "", kind: str = "reminder") -> str:
    """Schedule something you will SAY OUT LOUD to the user when it's due (plus a notification and
    the pet popping up). Use for "remind me to...", "set a timer for 10 minutes", "wake me at 7",
    "every weekday at 9 tell me to...".
    text: a short label, not a sentence ("call mom", "pasta", "laundry"; never "timer is up"); in_minutes: delay (e.g. 0.5, 10, 90) OR at: clock time
    "18:30" / "7 AM" (next occurrence) or full "2026-12-02T09:00"; repeat: "", "daily", "weekdays", "weekly";
    kind: "reminder", "timer" or "alarm"."""
    out = _api("/api/reminders", {"text": text, "in_minutes": in_minutes, "at": at, "repeat": repeat, "kind": kind})
    return out.get("error") or f"Scheduled: {out.get('reminder')}"


@mcp.tool()
def list_reminders() -> str:
    """List upcoming reminders, timers and alarms."""
    items = _api("/api/reminders").get("reminders", [])
    return "\n".join(items) if items else "Nothing scheduled."


@mcp.tool()
def cancel_reminder(what: str) -> str:
    """Cancel reminders/timers by id (from list_reminders), by words in their text, or "all"."""
    gone = _api("/api/reminders/cancel", {"what": what}).get("cancelled", [])
    return ("Cancelled:\n" + "\n".join(gone)) if gone else "Nothing matched."


# ---- memory --------------------------------------------------------------------
@mcp.tool()
def remember(fact: str) -> str:
    """Save a lasting fact about the user so you know it in every future conversation: name, school,
    courses, exam dates, projects, goals, people in their life, likes and dislikes, routines, how they
    want you to behave. One short self-contained sentence, e.g. "Has an chemistry exam on 3 Dec"."""
    return memory.remember(fact)


@mcp.tool()
def update_memory(old: str, new: str) -> str:
    """Replace a saved fact that changed. old: words from the old fact. new: the corrected fact."""
    return memory.update(old, new)


@mcp.tool()
def forget(about: str) -> str:
    """Delete saved facts containing these words (when the user says forget it or it's no longer true)."""
    return memory.forget(about)


@mcp.tool()
def recall(query: str = "") -> str:
    """Search everything you and the user have said in past conversations (all days), plus saved facts.
    Use for "what did I say about...", "remember when...", "what was that thing yesterday". Empty = last week."""
    hits = memory.search(query)
    words = [w for w in query.lower().split() if len(w) > 2]
    facts = [f for f in memory.facts() if any(w in f.lower() for w in words)] if words else []
    out = []
    if facts:
        out.append("Saved facts:\n" + "\n".join(f"- {f}" for f in facts))
    if hits:
        out.append("Past conversation:\n" + memory.format_entries(hits, 400))
    return "\n\n".join(out) or "Nothing found about that."


# ---- Mike's own browser ------------------------------------------------------------------
def _browse(op: str, **args) -> str:
    out = _api("/api/browser", {"op": op, "args": args}, timeout=240)
    return out.get("text") or out.get("error") or "No answer from the browser."


@mcp.tool()
def browser_open(url: str) -> str:
    """Open a website (or search the web if it isn't a URL) in Mike's own browser window, which the user can
    watch. Returns the page text and its links, buttons and fields with [ids]. Use this to do things on the web."""
    return _browse("open", url=url)


@mcp.tool()
def browser_read(find: str = "") -> str:
    """Read the browser's current page again: text plus every link, button and field with an [id].
    find: only list elements whose label contains these words (e.g. "add to cart")."""
    return _browse("read", find=find)


@mcp.tool()
def browser_click(element: int) -> str:
    """Click a link or button by its [id] from browser_open/browser_read. Buying, paying, sending, posting
    and deleting ask the user first automatically."""
    return _browse("click", element=element)


@mcp.tool()
def browser_type(element: int, text: str, submit: bool = False) -> str:
    """Type into a field by [id] (or pick a dropdown option by its text). submit=true presses Enter.
    Never for passwords or card numbers: the user types those in the browser window."""
    return _browse("type", element=element, text=text, submit=submit)


@mcp.tool()
def browser_scroll(direction: str = "down") -> str:
    """Scroll the page: down, up, top, bottom. Returns what is visible now."""
    return _browse("scroll", direction=direction)


@mcp.tool()
def browser_key(key: str) -> str:
    """Press a key in the page: Enter, Escape, Tab, ArrowDown, or a shortcut like Control+A."""
    return _browse("key", key=key)


@mcp.tool()
def browser_back() -> str:
    """Go back to the previous page."""
    return _browse("back")


@mcp.tool()
def browser_tab(index: int = 0) -> str:
    """Switch to browser tab number index (1 = first). index=0 lists the open tabs."""
    return _browse("tab", index=index)


@mcp.tool()
def browser_screenshot():
    """See the browser page as an image (for layouts, pictures, maps). browser_read is faster for text."""
    try:
        with urllib.request.urlopen(f"{MIKE_URL}/api/browser/screenshot", timeout=30) as r:
            return Image(data=r.read(), format="jpeg")
    except Exception as e:
        return f"The browser isn't open ({e})."


@mcp.tool()
def browser_close() -> str:
    """Close Mike's browser window."""
    return _browse("close")


@mcp.tool()
def notify(title: str, message: str) -> str:
    """Show a desktop notification banner."""
    import desktop
    desktop.notify(title, message)
    return "Notification shown."


def _get_project_path() -> Path:
    """Get the current project path, falling back to Mike's root."""
    import projects
    current = projects.current()
    return Path(current) if current else config.ROOT


def _task_manager_for_project(project_path: Path):
    from self_coding import TaskManager
    project_key = hashlib.sha256(str(project_path.resolve()).encode()).hexdigest()[:16]
    return TaskManager(TASK_STATE_ROOT / project_key)


def _tool_output(result) -> str:
    return result.output if result.success else f"Failed: {result.error or result.output or 'tool execution failed'}"


@mcp.tool()
def code_read(path: str, start: int = 0, end: int = 0) -> str:
    """Read a file from the current project. Give start and end line numbers to read just that part."""
    from self_coding import ToolExecutor, DevSession, CodeIndexer
    project_path = _get_project_path()
    session = DevSession("read", "read file", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.read_file(path, start, end)
    return _tool_output(result)


@mcp.tool()
def code_write(path: str, content: str) -> str:
    """Create or overwrite a file in the current project. Use for code, configs, docs."""
    from self_coding import ToolExecutor, DevSession
    project_path = _get_project_path()
    session = DevSession("write", "write file", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.write_file(path, content)
    return _tool_output(result)


@mcp.tool()
def code_index() -> str:
    """Build and retrieve codebase index for intelligent file search.
    Returns indexed modules, imports, call graphs, and keyword associations."""
    from self_coding import CodeIndexer
    project_path = _get_project_path()
    indexer = CodeIndexer(project_path)
    context = indexer.index_project()

    lines = []
    lines.append(f"Language: {context.language}")
    lines.append(f"\nKeywords per file (sample):")
    for file_path, terms in list(context.keywords.items())[:5]:
        terms_str = ", ".join(list(terms)[:5])
        lines.append(f"  {file_path}: [{terms_str}]")

    lines.append(f"\nFile dependencies (sample):")
    for file_path, deps in list(context.file_deps.items())[:5]:
        deps_str = ", ".join(list(deps)[:3])
        lines.append(f"  {file_path} -> {deps_str}")

    lines.append(f"\nExports (sample):")
    for export_file, importers in list(context.exports.items())[:5]:
        importers_str = ", ".join(importers[:3])
        lines.append(f"  {export_file} <- {importers_str}")

    lines.append(f"\nCall graph sample (first function):")
    first_func = list(context.call_graph.keys())[0] if context.call_graph else "none"
    lines.append(f"  {first_func} -> {context.call_graph.get(first_func, [])[:3]}")

    lines.append(f"\nClass hierarchy sample (first class):")
    first_class = list(context.class_hierarchy.keys())[0] if context.class_hierarchy else "none"
    lines.append(f"  {first_class} -> {context.class_hierarchy.get(first_class, 'none')}")

    lines.append(f"\nRetrieve relevant files with: code_search")
    lines.append(f"Get dependents with: code_dependents")
    lines.append(f"Get dependencies with: code_deps")
    lines.append(f"Get callers with: code_callers")
    lines.append(f"Get callee with: code_callees")

    return "\n".join(lines)


@mcp.tool()
def code_dependents(path: str = "") -> str:
    """Get files that depend on the given file/path."""
    from self_coding import CodeIndexer
    project_path = _get_project_path()
    indexer = CodeIndexer(project_path)
    context = indexer.index_project()
    file_path = path if path else ""
    dependents = context.exports.get(file_path, [])
    return f"Files that depend on {file_path}: {dependents if dependents else 'none'}"


@mcp.tool()
def code_deps(path: str = "") -> str:
    """Get files that the given file depends on."""
    from self_coding import CodeIndexer
    project_path = _get_project_path()
    indexer = CodeIndexer(project_path)
    context = indexer.index_project()
    file_path = path if path else ""
    deps = context.file_deps.get(file_path, [])
    return f"Files {file_path} depends on: {deps if deps else 'none'}"


@mcp.tool()
def code_callers(func: str = "") -> str:
    """Get functions that call the given function."""
    from self_coding import CodeIndexer
    project_path = _get_project_path()
    indexer = CodeIndexer(project_path)
    context = indexer.index_project()
    callers = context.reverse_call_graph.get(func, [])
    return f"Functions that call {func}: {callers if callers else 'none'}"


@mcp.tool()
def code_callees(func: str = "") -> str:
    """Get functions called by the given function."""
    from self_coding import CodeIndexer
    project_path = _get_project_path()
    indexer = CodeIndexer(project_path)
    context = indexer.index_project()
    callee = context.call_graph.get(func, [])
    return f"Functions called by {func}: {callee if callee else 'none'}"


@mcp.tool()
def code_search_relevant(query: str, max_files: int = 10) -> str:
    """Find files most relevant to a query using semantic code indexing."""
    from self_coding import CodeIndexer
    project_path = _get_project_path()
    indexer = CodeIndexer(project_path)
    results = indexer.retrieve_relevant(query, max_files)
    if not results:
        return f"No files found matching '{query}'"
    lines = [f"Files relevant to '{query}':"]
    for file_path, score in results:
        lines.append(f"  {file_path} (score: {score})")
    return "\n".join(lines)


@mcp.tool()
def code_task_save(goal: str = "", step: int = 0, notes: str = "") -> str:
    """Save current task state for resume after restart."""
    project_path = _get_project_path()
    task_manager = _task_manager_for_project(project_path)
    if goal:
        task_manager.active_goal = goal
    if step > 0:
        task_manager.completed_steps = list(range(1, step + 1))
    task_manager.notes = notes
    task_manager.save_state()
    return f"Task state saved: {task_manager.active_goal} (through step {step})"


@mcp.tool()
def code_task_load() -> str:
    """Load saved task state."""
    project_path = _get_project_path()
    task_manager = _task_manager_for_project(project_path)
    if task_manager.active_goal:
        return (f"Resumed task: {task_manager.active_goal}\n"
                f"Completed steps: {task_manager.completed_steps}\n"
                f"Pending actions: {len(task_manager.pending_actions)}\n"
                f"Notes: {task_manager.notes}")
    return "No saved task state found."


@mcp.tool()
def code_subagent_spawn(task_description: str) -> str:
    """Start a background coding subagent for an independent task in the current project."""
    from subagents import spawn
    return spawn(_get_project_path(), task_description)


@mcp.tool()
def code_subagent_results(agent_id: str = "") -> str:
    """Get status and results for a subagent, optionally selecting its ID."""
    from subagents import results
    return results(agent_id)


@mcp.tool()
def code_edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """Change part of an existing file: replace the exact text old with new."""
    from self_coding import ToolExecutor, DevSession
    project_path = _get_project_path()
    session = DevSession("edit", "edit file", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.edit_file(path, old, new, replace_all)
    return _tool_output(result)


@mcp.tool()
def code_search(pattern: str, path: str = "") -> str:
    """Search the project's files for text (grep)."""
    from self_coding import ToolExecutor, DevSession
    project_path = _get_project_path()
    session = DevSession("search", "search code", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.search_code(pattern, path)
    return _tool_output(result)


@mcp.tool()
def code_bash(command: str, cwd: str = "", timeout: int = 120) -> str:
    """Run a shell command in the project directory."""
    from self_coding import ToolExecutor, DevSession
    project_path = _get_project_path()
    session = DevSession("bash", "run command", project_path)
    executor = ToolExecutor(project_path, session)
    work_dir = project_path / cwd if cwd else project_path
    result = executor._run_command(command, cwd=work_dir, timeout=timeout)
    return _tool_output(result)


@mcp.tool()
def code_test() -> str:
    """Run the project's test suite and return results."""
    from self_coding import ToolExecutor, DevSession, ProjectDetector
    project_path = _get_project_path()
    session = DevSession("test", "run tests", project_path)
    executor = ToolExecutor(project_path, session)
    detector = ProjectDetector(project_path)
    result = executor.run_tests(detector)
    return f"Tests: {result.passed}/{result.total} passed ({result.failed} failed, {result.errors} errors)\nFramework: {result.framework}\nOutput:\n{result.output[-3000:]}"


@mcp.tool()
def code_lint() -> str:
    """Run the project's linter."""
    from self_coding import ToolExecutor, DevSession, ProjectDetector
    project_path = _get_project_path()
    session = DevSession("lint", "run linter", project_path)
    executor = ToolExecutor(project_path, session)
    detector = ProjectDetector(project_path)
    result = executor.run_lint(detector)
    return result.output


@mcp.tool()
def code_type_check() -> str:
    """Run the project's type checker."""
    from self_coding import SelfCodingAgent, DevSession, ToolExecutor, ProjectDetector
    import projects
    current = projects.current()
    project_path = Path(current) if current else config.ROOT
    session = DevSession("type_check", "run type checker", project_path)
    executor = ToolExecutor(project_path, session)
    detector = ProjectDetector(project_path)
    result = executor.run_type_check(detector)
    return result.output


@mcp.tool()
def code_build() -> str:
    """Run the project's build command."""
    from self_coding import SelfCodingAgent, DevSession, ToolExecutor, ProjectDetector
    import projects
    current = projects.current()
    project_path = Path(current) if current else config.ROOT
    session = DevSession("build", "run build", project_path)
    executor = ToolExecutor(project_path, session)
    detector = ProjectDetector(project_path)
    result = executor.run_build(detector)
    return result.output


@mcp.tool()
def code_git_status() -> str:
    """Show git status."""
    from self_coding import SelfCodingAgent, DevSession, ToolExecutor, ProjectDetector
    import projects
    current = projects.current()
    project_path = Path(current) if current else config.ROOT
    session = DevSession("git", "git status", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.git_status()
    return result.output


@mcp.tool()
def code_git_diff(staged: bool = False) -> str:
    """Show git diff."""
    from self_coding import SelfCodingAgent, DevSession, ToolExecutor, ProjectDetector
    import projects
    current = projects.current()
    project_path = Path(current) if current else config.ROOT
    session = DevSession("git", "git diff", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.git_diff(staged)
    return result.output


@mcp.tool()
def code_git_checkpoint(label: str) -> str:
    """Create a git checkpoint (stash + branch) before risky changes."""
    from self_coding import SelfCodingAgent, DevSession, ToolExecutor, ProjectDetector
    import projects
    current = projects.current()
    project_path = Path(current) if current else config.ROOT
    session = DevSession("git", "git checkpoint", project_path)
    executor = ToolExecutor(project_path, session)
    result = executor.git_checkpoint(label)
    return result.output


@mcp.tool()
def code_self_check() -> str:
    """Enhanced self-check: compiles, runs tests, linter, type checker."""
    from self_coding import enhanced_self_check
    import projects
    current = projects.current()
    project_path = str(current) if current else str(config.ROOT)
    return enhanced_self_check(project_path)


if __name__ == "__main__":
    mcp.run()
