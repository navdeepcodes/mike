"""MCP server that gives Claude Code Mike's senses: camera, screen, notifications.

Claude Code starts this automatically (see data/mcp.json). The camera frame
comes from the HUD in your browser via Mike's local server.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP, Image

import memory

MIKE_URL = os.environ.get("MIKE_URL", "http://127.0.0.1:8777")
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
        return ("I can't see the screen yet. Tell the user, in one sentence: turn on Mike under System Settings > "
                "Privacy & Security > Screen & System Audio Recording, then quit and reopen Mike.")


def _native(op: str, **args):
    """Ask Mike.app to act (Accessibility API + real mouse/keyboard events)."""
    req = urllib.request.Request(f"{MIKE_URL}/api/native", data=json.dumps({"op": op, "args": args}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            out = json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": f"Mike.app didn't answer ({e}). It must be running."}
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
    each brightness step ~6%). For an exact volume use osascript "set volume output volume N"."""
    return _say(_native("media", action=action, times=times), "Done.")


# ---- reminders / timers / alarms -----------------------------------------------
def _api(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"{MIKE_URL}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
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


@mcp.tool()
def notify(title: str, message: str) -> str:
    """Show a macOS notification banner."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e",
                    f'display notification "{esc(message)}" with title "{esc(title)}"'], timeout=10)
    return "Notification shown."


if __name__ == "__main__":
    mcp.run()
