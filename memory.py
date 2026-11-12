"""Mike's long-term memory.

Two stores, both plain files you can read and edit:
  data/memory.md         - lasting facts about you, one "- fact" per line (Mike adds these himself)
  data/journal/DATE.jsonl - every exchange: {"t": "2026-11-12T14:05:09", "user": "...", "mike": "..."}

The facts and your latest exchanges go into Mike's system prompt each time his brain starts,
so a fresh conversation still knows who you are and what you were just doing.
"""
import json
import re
import time
from datetime import datetime, timedelta

import config

MEMORY_FILE = config.DATA / "memory.md"
JOURNAL_DIR = config.DATA / "journal"
MAX_FACTS = 300


# ---- facts -------------------------------------------------------------------
def facts() -> list[str]:
    try:
        lines = MEMORY_FILE.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [l[2:].strip() for l in lines if l.startswith("- ") and l[2:].strip()]


def _write_facts(items: list[str]):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- {f}" for f in items[-MAX_FACTS:])
    MEMORY_FILE.write_text("# What Mike knows about you (edit freely)\n\n" + body + ("\n" if body else ""))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def remember(fact: str) -> str:
    fact = " ".join(fact.split()).strip(" -")
    if not fact:
        return "Nothing to remember."
    items = facts()
    if any(_norm(fact) == _norm(f) for f in items):
        return "Already knew that."
    items.append(fact)
    _write_facts(items)
    return "Saved."


def forget(about: str) -> str:
    """Remove every fact containing all the given words."""
    words = _norm(about).split()
    if not words:
        return "Forget what?"
    items = facts()
    keep = [f for f in items if not all(w in _norm(f) for w in words)]
    _write_facts(keep)
    removed = len(items) - len(keep)
    return f"Forgot {removed} thing{'s' if removed != 1 else ''}." if removed else "I didn't have anything like that saved."


def update(old: str, new: str) -> str:
    """Replace the fact matching `old` with `new` (e.g. a changed schedule)."""
    words = _norm(old).split()
    items = facts()
    for i, f in enumerate(items):
        if words and all(w in _norm(f) for w in words):
            items[i] = " ".join(new.split())
            _write_facts(items)
            return "Updated."
    return remember(new)


# ---- journal -----------------------------------------------------------------
def log_turn(user: str, mike: str):
    if not user.strip():
        return
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    rec = {"t": now.isoformat(timespec="seconds"), "user": user.strip(), "mike": mike.strip()}
    with open(JOURNAL_DIR / f"{now:%Y-%m-%d}.jsonl", "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _entries(days: int = 3650):
    """Yields (date, entries) newest day first; entries inside a day oldest first."""
    if not JOURNAL_DIR.exists():
        return
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for path in sorted(JOURNAL_DIR.glob("*.jsonl"), reverse=True):
        if path.stem < cutoff:
            break
        out = []
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        yield path.stem, out


def recent(n: int = 8, days: int = 2) -> list[dict]:
    got: list[dict] = []
    for _, entries in _entries(days):
        got = entries + got
        if len(got) >= n:
            break
    return got[-n:]


def search(query: str, limit: int = 12) -> list[dict]:
    words = [w for w in _norm(query).split() if len(w) > 2]
    if not words:
        return recent(limit, days=7)
    hits = []
    for _, entries in _entries():
        for e in entries:
            text = _norm(e.get("user", "") + " " + e.get("mike", ""))
            score = sum(w in text for w in words)
            if score >= max(1, (len(words) + 1) // 2):
                hits.append((score, e.get("t", ""), e))
        if len(hits) >= limit * 4:
            break
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)   # best match, then newest
    return [e for _, _, e in hits[:limit]]


def _when(t: str) -> str:
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return t
    days = (datetime.now().date() - d.date()).days
    day = "today" if days == 0 else "yesterday" if days == 1 else d.strftime("%a %d %b")
    return f"{day} {d:%H:%M}"


def format_entries(entries: list[dict], clip: int = 300) -> str:
    return "\n".join(f"[{_when(e.get('t', ''))}] them: {e.get('user', '')[:clip]} | you: {e.get('mike', '')[:clip]}"
                     for e in entries)


# ---- automatic consolidation ---------------------------------------------------
STATE_FILE = config.DATA / "memory_state.json"     # {"last_t": "2026-11-12T09:40:53"}

_SCHEMA = {"type": "object", "properties": {
    "add": {"type": "array", "items": {"type": "string"}},
    "update": {"type": "array", "items": {"type": "object", "properties": {
        "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"]}},
    "remove": {"type": "array", "items": {"type": "string"}}},
    "required": ["add", "update", "remove"]}

_INSTRUCTIONS = """You maintain the long-term memory of Mike, a voice companion for one user.
From the new conversation below, extract only things worth remembering for months:
who the user is, people in their life, dates and deadlines, exams, goals, plans and decisions,
projects and their status, preferences and dislikes (including how they want Mike to behave),
routines, places, commitments. Skip one-off requests, small talk, test messages, anything Mike
did on the computer unless it reveals a lasting fact, and anything already in the known facts.
Each fact: one short self-contained sentence about the user ("Has a CanSat launch on 14 Dec").
Use "update" when a known fact changed (old = words from the known fact), "remove" for facts
the user said are no longer true. Empty lists are fine and common."""


def _new_entries() -> list[dict]:
    try:
        last = json.loads(STATE_FILE.read_text()).get("last_t", "")
    except Exception:
        last = ""
    out = []
    for _, entries in _entries(days=14):
        out = [e for e in entries if e.get("t", "") > last] + out
    return out


def pending_count() -> int:
    return len([e for e in _new_entries() if not e.get("user", "").startswith("(")])


def consolidate() -> dict:
    """Ask Claude (Haiku, no tools) what's worth keeping from the conversation since last time,
    then save it. Blocking: call from a worker thread."""
    import os
    import subprocess
    entries = _new_entries()
    talk = [e for e in entries if not e.get("user", "").startswith("(")]   # skip reminder/timer events
    if not entries:
        return {"skipped": "nothing new"}
    last_t = entries[-1]["t"]
    result = {"add": [], "update": [], "remove": []}
    if talk:
        convo = "\n".join(f"[{e['t'][:16]}] User: {e['user'][:500]}\nMike: {e['mike'][:500]}" for e in talk[-60:])
        known = "\n".join(f"- {f}" for f in facts()) or "- (none yet)"
        prompt = f"Known facts:\n{known}\n\nNew conversation:\n{convo}"
        env = {k: v for k, v in os.environ.items() if not (k.startswith("ANTHROPIC_") or k.startswith("CLAUDE"))}
        args = [config.CLAUDE_BIN, "-p", "--output-format", "json", "--model", "claude-haiku-5-5",
                "--tools", "", "--strict-mcp-config", "--setting-sources", "project,local",
                "--settings", json.dumps({"disableAllHooks": True}), "--no-session-persistence",
                "--system-prompt", _INSTRUCTIONS, "--json-schema", json.dumps(_SCHEMA), prompt]
        config.WORKDIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(args, capture_output=True, text=True, timeout=120, cwd=str(config.WORKDIR), env=env)
        data = json.loads(r.stdout or "{}")
        if data.get("is_error") or r.returncode != 0:
            return {"error": (data.get("result") or r.stderr or "failed")[:300]}
        out = data.get("structured_output")
        if not isinstance(out, dict):
            text = data.get("result", "")
            m = re.search(r"\{.*\}", text, re.S)
            out = json.loads(m.group(0)) if m else {}
        for f in out.get("add", [])[:15]:
            if remember(f) == "Saved.":
                result["add"].append(f)
        for u in out.get("update", [])[:10]:
            if isinstance(u, dict) and u.get("old") and u.get("new"):
                update(u["old"], u["new"])
                result["update"].append(u["new"])
        for f in out.get("remove", [])[:10]:
            if forget(f).startswith("Forgot"):
                result["remove"].append(f)
    STATE_FILE.write_text(json.dumps({"last_t": last_t}))
    return result


# ---- Claude's own memory (all Claude Code sessions on this Mac) ---------------
CLAUDE_PROJECTS = config.HOME / ".claude" / "projects"
CLAUDE_MEMORY_FILE = config.DATA / "claude_memory.md"


def _where(folder: str) -> str:
    """Readable origin for a ~/.claude/projects/<encoded-path> folder."""
    m = re.search(r"scratch-(\d{4}-\d{2}-\d{2})", folder)
    if m:
        return f"a Claude chat on {m.group(1)}"
    home = "-" + str(config.HOME).strip("/").replace("/", "-")
    return "~" + folder[len(home):].replace("-", "/") if folder.startswith(home) else folder


def _frontmatter(text: str) -> tuple[dict, str]:
    meta = {}
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
        for line in head.splitlines():
            k, _, v = line.partition(":")
            if v.strip():
                meta[k.strip()] = v.strip().strip('"')
        return meta, body.strip()
    return meta, text.strip()


def sync_claude_memory() -> str:
    """Copies every Claude Code memory note into data/claude_memory.md and returns a short index."""
    index, full = [], ["# Claude's memory about the user (all Claude Code sessions on this Mac)\n"
                       "# Synced automatically each time Mike starts. Edit the originals, not this file.\n"]
    for path in sorted(CLAUDE_PROJECTS.glob("*/memory/*.md")):
        if path.name == "MEMORY.md":
            continue
        try:
            meta, body = _frontmatter(path.read_text())
        except Exception:
            continue
        name = meta.get("name", path.stem)
        where = _where(path.parent.parent.name)
        index.append(f"- {name} ({where}): {meta.get('description', '')[:110]}")
        full.append(f"\n## {name}\nFrom: {where}\n\n{body}\n")
    try:
        CLAUDE_MEMORY_FILE.write_text("\n".join(full))
    except Exception:
        pass
    return "\n".join(index)


# ---- prompt ------------------------------------------------------------------
def system_prompt(base: str) -> str:
    parts = [base]
    known = facts()
    parts.append("What you remember about the user (from earlier conversations):\n"
                 + ("\n".join(f"- {f}" for f in known) if known else "- nothing yet; learn as you go."))
    claude_index = sync_claude_memory() if getattr(config, "CLAUDE_MEMORY", True) else ""
    if claude_index:
        parts.append("Claude's memory about the user, from their Claude Code sessions (projects, preferences, "
                     "history). One line per note; the full notes are in " + str(CLAUDE_MEMORY_FILE) +
                     " (Grep/Read it for details when a topic comes up). Notes that are rules for coding "
                     "sessions describe how the user likes work done; take the spirit, not the coding rules:\n"
                     + claude_index)
    last = recent(8)
    if last:
        parts.append("Your latest exchanges with them, for continuity (older first):\n" + format_entries(last, 220))
    parts.append(f"Today is {time.strftime('%A %d %B %Y')}.")
    return "\n\n".join(parts)
