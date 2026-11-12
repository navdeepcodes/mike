"""Reminders, timers and alarms that Mike speaks out loud when they're due.

Stored in data/reminders.json as a list of
  {"id": "r1a2b", "text": "call mom", "due": 1763000000.0, "repeat": "", "kind": "reminder"}
with `due` in epoch seconds and `repeat` one of "", "daily", "weekdays", "weekly".
"""
import json
import time
import uuid
from datetime import datetime, timedelta

import config

FILE = config.DATA / "reminders.json"


def load() -> list[dict]:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return []


def save(items: list[dict]):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(sorted(items, key=lambda r: r["due"]), indent=2))


def parse_when(in_minutes: float | None, at: str) -> float:
    """Epoch seconds from either a delay or a clock time / ISO date-time."""
    now = datetime.now()
    if in_minutes is not None and float(in_minutes) > 0:
        return time.time() + float(in_minutes) * 60
    at = (at or "").strip()
    if not at:
        raise ValueError("Give in_minutes or at.")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(at, fmt).timestamp()
        except ValueError:
            pass
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            t = datetime.strptime(at.upper(), fmt).time()
        except ValueError:
            continue
        d = datetime.combine(now.date(), t)
        if d <= now:
            d += timedelta(days=1)    # "at 7" when it's already past 7 means tomorrow
        return d.timestamp()
    raise ValueError(f"Couldn't understand the time {at!r}; use HH:MM or YYYY-MM-DDTHH:MM.")


def add(text: str, due: float, repeat: str = "", kind: str = "reminder") -> dict:
    item = {"id": "r" + uuid.uuid4().hex[:4], "text": text.strip(), "due": due,
            "repeat": repeat if repeat in ("daily", "weekdays", "weekly") else "", "kind": kind}
    items = load()
    items.append(item)
    save(items)
    return item


def cancel(what: str) -> list[dict]:
    what = what.lower().strip()
    items = load()
    gone = [r for r in items if what in ("all", "everything") or r["id"] == what or (what and what in r["text"].lower())]
    save([r for r in items if r not in gone])
    return gone


def pop_due(now: float | None = None) -> list[dict]:
    """Returns reminders that are due and reschedules the repeating ones."""
    now = now or time.time()
    items = load()
    due = [r for r in items if r["due"] <= now]
    if not due:
        return []
    keep = [r for r in items if r["due"] > now]
    for r in due:
        nxt = _next(r, now)
        if nxt:
            keep.append({**r, "due": nxt})
    save(keep)
    return due


def _next(r: dict, now: float) -> float | None:
    if not r.get("repeat"):
        return None
    d = datetime.fromtimestamp(r["due"])
    step = timedelta(days=7 if r["repeat"] == "weekly" else 1)
    while d.timestamp() <= now or (r["repeat"] == "weekdays" and d.weekday() >= 5):
        d += step
    return d.timestamp()


def describe(r: dict) -> str:
    d = datetime.fromtimestamp(r["due"])
    days = (d.date() - datetime.now().date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else d.strftime("%a %d %b")
    rep = f", repeats {r['repeat']}" if r.get("repeat") else ""
    return f"[{r['id']}] {r['kind']} {day} at {d:%-I:%M %p}{rep}: {r['text']}"
