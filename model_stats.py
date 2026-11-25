"""How well each backup model actually does Mike's work, learned from its own steps (no hand-written ranking).

Every tool step a model takes is scored by its result: Mike's tools say plainly when a step failed ("Failed
(exit code 1)", "Not changed: that text isn't in…", "Arguments weren't valid JSON", a repeated call…). A model's
capability is its smoothed success rate. Before it has a record, the guess comes from its size (read from the
model id), and that guess fades as real results come in. Saved in data/model_stats.json.
"""
import json
import math
import re
import threading

import config

PATH = config.DATA / "model_stats.json"
PRIOR_WEIGHT = 6           # a new model's size-based guess counts as this many steps
_lock = threading.Lock()
_stats: dict[str, dict] | None = None

# How Mike's own tools report a step that didn't work (see fallback.py and mike_tools.py).
FAILED = re.compile(r"^(Failed|Couldn't|Not changed|Bad arguments|Arguments weren't valid JSON|Unknown tool|"
                    r"You already ran exactly this|That's too many tries|Timed out|.*\bisn't a folder)", re.I)


def _load() -> dict:
    global _stats
    if _stats is None:
        try:
            _stats = json.loads(PATH.read_text())
        except Exception:
            _stats = {}
    return _stats


def _save():
    try:
        PATH.parent.mkdir(exist_ok=True)
        PATH.write_text(json.dumps(_stats, indent=1))
    except Exception:
        pass


def step_ok(result: str) -> bool:
    return not FAILED.match((result or "").lstrip())


def record(lane: str, ok: bool):
    with _lock:
        s = _load().setdefault(lane, {"ok": 0, "bad": 0})
        s["ok" if ok else "bad"] += 1
        _save()


def prior(size_b: float | None) -> float:
    """A guess from size alone: bigger models tend to make fewer mistakes. Unknown size: middling."""
    if not size_b or size_b >= 1e8:
        return 0.6
    return max(0.3, min(0.9, 0.35 + 0.12 * math.log2(max(size_b, 1) / 4)))


def capability(lane: str, size_b: float | None = None) -> float:
    s = _load().get(lane, {})
    ok, bad = s.get("ok", 0), s.get("bad", 0)
    return (ok + PRIOR_WEIGHT * prior(size_b)) / (ok + bad + PRIOR_WEIGHT)


def set_limit(lane: str, tokens: int):
    """The provider told us this model's per-request/per-minute token limit (e.g. Groq's "Limit 8000")."""
    with _lock:
        s = _load().setdefault(lane, {"ok": 0, "bad": 0})
        if s.get("limit") != tokens:
            s["limit"] = tokens
            _save()


def limit(lane: str) -> int | None:
    return _load().get(lane, {}).get("limit")


def summary() -> dict:
    return {k: dict(v) for k, v in _load().items()}
