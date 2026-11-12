"""Mike's greetings: casual, different every time, never the same line twice in a row.

Remembers the last lines used in data/greet_state.json: {"recent": ["…", …], "last_at": 1794460000.0}
"""
import json
import random
import time

import config

STATE = config.DATA / "greet_state.json"

HELLO = {
    "morning": ["Morning{n}. What's the plan?", "Yo, good morning{n}.", "Morning. Let's get it.",
                "Up and at it. What's first?", "Morning{n}. Ready when you are.", "Hey, morning. Sleep okay?",
                "Good morning{n}. What are we doing today?"],
    "afternoon": ["Hey{n}, what's up?", "Yo. What are we doing?", "Afternoon. Hit me.", "Hey, I'm here. What's good?",
                  "What's up{n}? How's the day going?", "Afternoon{n}. What's the move?"],
    "evening": ["Evening{n}. What's the move tonight?", "Yo, evening. What's up?", "Hey. Long day?",
                "Evening. I'm all yours.", "Hey{n}, how'd today go?", "Evening{n}. What are we getting into?"],
    "late": ["Still up, huh? Same.", "Late one tonight? I'm here.", "Night owl mode. What do you need?",
             "Can't sleep either? What's up?", "It's late{n}. What's going on?"],
    "any": ["Yo{n}.", "Hey hey. What's up?", "What's good{n}?", "I'm here. Talk to me.", "Sup. What are we getting into?",
            "Ayy{n}. What's the plan?", "Hey{n}. Missed me?", "What's up{n}? I'm all ears."],
    "monday": ["Monday again. Let's ease into it.", "New week{n}. What's first?"],
    "friday": ["Friday, finally. What's the plan?", "It's Friday{n}. Let's make it a good one."],
    "weekend": ["Weekend vibes. What's up?", "Hey{n}, weekend mode. What are we doing?"],
}
BACK = ["There you are.", "Welcome back{n}.", "Yo, you're back.", "Hey, look who's back.", "Back at it? What's up?",
        "Hey{n}. Good to have you back.", "Oh hey, you're back."]


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"recent": [], "last_at": 0.0}


def _pick(lines: list[str]) -> str:
    st = _load()
    fresh = [l for l in lines if l not in st["recent"]] or lines
    line = random.choice(fresh)
    st["recent"] = (st["recent"] + [line])[-15:]
    st["last_at"] = time.time()
    STATE.write_text(json.dumps(st))
    name = config.USER_NAME.strip()
    # Use the name now and then, not every time.
    return line.replace("{n}", f", {name}" if name and random.random() < 0.4 else "")


def recently_greeted(minutes: float = 10) -> bool:
    return time.time() - _load().get("last_at", 0) < minutes * 60


def hello() -> str:
    t = time.localtime()
    h, wd = t.tm_hour, t.tm_wday
    part = "late" if (h >= 23 or h < 4) else "morning" if h < 12 else "afternoon" if h < 17 else "evening"
    pool = HELLO[part] + HELLO["any"]
    if wd == 0 and h < 14:
        pool += HELLO["monday"]
    elif wd == 4:
        pool += HELLO["friday"]
    elif wd >= 5:
        pool += HELLO["weekend"]
    return _pick(pool)


def welcome_back() -> str:
    return _pick(BACK)
