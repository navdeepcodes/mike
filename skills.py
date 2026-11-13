"""Skills: ready-made know-how (PDFs, documents, images, simulations, websites...) any model can use.

A skill is a folder with a SKILL.md (step-by-step instructions) and often helper scripts. They are read
where they already live on this Mac (~/.claude), never copied into Mike. Mike searches them by name and
description (find) and loads one only when it's needed (load), so nothing bloats every request.
Which skills Mike offers is config.SKILLS (names); empty means all of them.
"""
import importlib.util
import re
from pathlib import Path

import config

HOME = Path.home()
ROOTS = [HOME / ".claude" / "plugins" / "marketplaces", HOME / ".claude" / "skills"]
MAX_BODY = 12000
_cache: dict[str, dict] | None = None


def _parse(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta, key = {}, None
    for line in m.group(1).splitlines():
        if line[:1] in (" ", "\t") and key:           # continuation of a folded/multi-line value
            meta[key] = (meta[key] + " " + line.strip()).strip()
            continue
        k, _, v = line.partition(":")
        key = k.strip() if k.strip() in ("name", "description") else None
        if key:
            v = v.strip()
            meta[key] = "" if v in (">", "|", ">-", "|-") else v.strip("\"'")
    return meta, m.group(2)


def _scan() -> dict[str, dict]:
    candidates: dict[str, tuple] = {}
    wanted = {n.lower() for n in getattr(config, "SKILLS", [])}
    for root in ROOTS:
        if not root.is_dir():
            continue
        for p in root.rglob("SKILL.md"):
            # Skip dependencies and translated copies (folders like ja-JP, zh-CN): they're stubs, not the skill.
            if "node_modules" in p.parts or any(re.fullmatch(r"[a-z]{2}-[A-Za-z]{2,4}", part) for part in p.parts):
                continue
            try:
                text = p.read_text(errors="replace")
                meta, _ = _parse(text)
            except Exception:
                continue
            name = (meta.get("name") or p.parent.name).strip()
            if wanted and name.lower() not in wanted:
                continue
            tier = 0 if "anthropic-agent-skills" in p.parts else 1 if "marketplaces" in p.parts else 2
            rank = (tier, -len(text))             # official first, then the fullest copy
            if name.lower() not in candidates or rank < candidates[name.lower()][0]:
                candidates[name.lower()] = (rank, {"name": name, "description": meta.get("description", ""), "path": p})
    return {k: v[1] for k, v in candidates.items()}


def index() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = _scan()
    return _cache


def _stem(w: str) -> str:
    for end in ("ing", "ed", "es", "s"):
        if len(w) > len(end) + 3 and w.endswith(end):
            return w[: -len(end)]
    return w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def find(query: str, k: int = 12) -> str:
    """The skills, best match for what the user wants to make first, as 'name: what it's for' lines."""
    skills = index()
    if not skills:
        return "No skills are installed on this Mac."
    q = _words(query)

    def score(s):
        return 3 * len(q & _words(s["name"].replace("-", " "))) + len(q & _words(s["description"]))

    ranked = sorted(skills.values(), key=lambda s: -score(s))[:k]
    lines = [f"{s['name']}: {' '.join(s['description'].split())[:110]}" for s in ranked]
    return "\n".join(lines) + "\nPick the one that fits and call use_skill with its name."


def load(name: str) -> str:
    """The skill's instructions, ready to follow, with where its files are and how to use them."""
    skills = index()
    key = name.strip().lower()
    s = skills.get(key) or next((v for k, v in skills.items() if k.startswith(key) or key in k), None)
    if not s:
        return f"No skill called '{name}'. Call find_skill to see what's available."
    path: Path = s["path"]
    _, body = _parse(path.read_text(errors="replace"))
    note = ""
    if len(body) > MAX_BODY:
        body, note = body[:MAX_BODY], f"\n\n(Shortened. Read the rest with read_file {path} if you need it.)"
    others = sorted(p.name + ("/" if p.is_dir() else "") for p in path.parent.iterdir() if p.name != "SKILL.md")[:30]
    libs = [m for m in ("reportlab", "pypdf", "PIL", "matplotlib", "numpy", "docx", "openpyxl", "pptx", "markdown")
            if importlib.util.find_spec(m)]
    return (f"SKILL: {s['name']}\nSkill folder: {path.parent}\nFiles in it: {', '.join(others) or 'none'}\n"
            "How to use it: follow the steps below. Relative paths and scripts live in the skill folder; run scripts "
            "with run_command (use python3). Ignore mentions of tools you don't have (Skill, Agent, TodoWrite...). "
            f"Save what you make in {config.OUT_DIR} unless the user says where, check the file exists "
            "before saying it's done, then open it. "
            f"Python libraries ready to import: {', '.join(libs)}. Don't pip install. Now do it: write the code with "
            "write_file, run it with run_command, check the result. Keep helper scripts in /tmp/mike-work, not in a project. "
            "Use layout tools that wrap and flow content for you (paragraph or table objects, CSS) instead of placing text at "
            "fixed coordinates, so nothing runs off the page, and make it look finished: a title, spacing, good colors. "
            "Never put the user's personal details (name, city, contacts) into what you make unless they asked; use clear "
            "placeholders like [address] instead of inventing phone numbers or addresses.\n\n" + body.strip() + note)
