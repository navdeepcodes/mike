"""Coding sessions from the Code tab, kept so you can reopen them: what you asked, every step Mike took with its
output, what he said, and the project's changes after each turn. One JSON-lines file per session in
data/code_sessions/. Old sessions beyond KEEP are deleted.
"""
import json
import re
import threading
import time
import uuid
from pathlib import Path

import config

DIR = config.DATA / "code_sessions"
KEEP = 200                     # most recent sessions kept
MAX_DIFF = 300_000             # characters of diff stored per snapshot
RECORDED = {"user", "tool", "tool_result", "assistant_delta", "assistant_done", "confirm", "confirm_done"}
_lock = threading.Lock()
_SAFE_ID = re.compile(r"^[0-9a-f]{12}$")


def _path(sid: str) -> Path | None:
    return DIR / f"{sid}.jsonl" if _SAFE_ID.match(sid or "") else None


def new(project: dict | None) -> str:
    sid = uuid.uuid4().hex[:12]
    head = {"type": "session", "id": sid, "t": time.time(), "project": (project or {}).get("name", ""),
            "path": (project or {}).get("path", "")}
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        _path(sid).write_text(json.dumps(head) + "\n")
        _prune()
    return sid


def append(sid: str, event: dict):
    p = _path(sid)
    if not p or event.get("type") not in RECORDED | {"snapshot"}:
        return
    with _lock:
        try:
            with p.open("a") as f:
                f.write(json.dumps({**event, "t": time.time()}) + "\n")
        except OSError:
            pass


def snapshot(sid: str, changes: list[dict], diffs: dict[str, str]):
    """The project's changes after a turn, with the diff text, so the session can be reviewed later."""
    out, used = {}, 0
    for f, text in diffs.items():
        if used + len(text) > MAX_DIFF:
            out[f] = "(diff too large to keep)"
            continue
        out[f] = text
        used += len(text)
    append(sid, {"type": "snapshot", "changes": changes, "diffs": out})


def load(sid: str) -> list[dict]:
    """Every event of a session, with streamed text joined back into whole replies."""
    p = _path(sid)
    if not p or not p.exists():
        return []
    events = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant_delta" and events and events[-1].get("type") == "assistant_delta":
            events[-1]["text"] += e.get("text", "")
        else:
            events.append(e)
    return events


def listing(project_path: str = "", limit: int = 50) -> list[dict]:
    """Recent sessions (newest first): when, which project, the first request, turns, and files changed."""
    if not DIR.exists():
        return []
    files = sorted(DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for f in files:
        head, first, turns, changed = None, "", 0, 0
        try:
            for line in f.read_text(errors="replace").splitlines():
                e = json.loads(line)
                t = e.get("type")
                if t == "session":
                    head = e
                elif t == "user":
                    turns += 1
                    first = first or str(e.get("text", ""))[:120]
                elif t == "snapshot":
                    changed = len(e.get("changes") or [])
        except Exception:
            continue
        if not head or not turns or (project_path and head.get("path") != project_path):
            continue
        out.append({"id": head["id"], "t": head["t"], "updated": f.stat().st_mtime, "project": head.get("project", ""),
                    "first": first, "turns": turns, "changed": changed})
        if len(out) >= limit:
            break
    return out


def _prune():
    files = sorted(DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[KEEP:]:
        f.unlink(missing_ok=True)
