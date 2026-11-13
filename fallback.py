"""Mike's backup brain: free cloud models first, a local model last.

Used when Claude is unavailable (Pro limit reached, offline, Claude Code failing) or when you pick
"free models only" in Settings. Talks to OpenAI-compatible endpoints with streaming and tool calls,
and gives the model the same hands as Claude: app control, memory, reminders, shell (sensitive
commands still need your OK).

Keys live in data/secrets.json (readable only by you): {"groq": "<key>", "nvidia": "<key>", ...}
"""
import asyncio
import inspect
import re
import json
import logging
import os
import subprocess
import time
import typing
from typing import Awaitable, Callable

import httpx

import config
import memory
import mike_tools
import skills

log = logging.getLogger("mike.fallback")
SECRETS = config.DATA / "secrets.json"

# Tried in this order. Models are matched against what each provider actually lists right now.
PROVIDERS = [
    {"id": "groq", "name": "Groq", "base": "https://api.groq.com/openai/v1", "signup": "https://console.groq.com/keys",
     # Each model has its own per-minute limit on the free tier, so these act as separate lanes.
     "prefer": ["openai/gpt-oss-120b", "qwen/qwen3", "moonshotai/kimi-k2", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"]},
    {"id": "nvidia", "name": "NVIDIA", "base": "https://integrate.api.nvidia.com/v1", "signup": "https://build.nvidia.com",
     # NVIDIA lists many models that no longer answer; each pick is verified with a tiny test call.
     "prefer": ["nvidia/nemotron-3-ultra", "mistralai/mistral-large-4", "nvidia/nemotron-3.5-super", "deepseek-ai/deepseek-v3",
                "moonshotai/kimi-k2", "qwen/qwen3", "openai/gpt-oss-120b"]},
    {"id": "openrouter", "name": "OpenRouter", "base": "https://openrouter.ai/api/v1", "signup": "https://openrouter.ai/keys",
     # Only ":free" models (never a paid twin), strongest first.
     "only": ":free",
     "prefer": ["nvidia/nemotron-3-ultra", "google/gemma-4-31b", "nvidia/nemotron-3.5-super", "deepseek", "qwen/qwen3",
                "openai/gpt-oss-120b", "google/gemma-4-26b", "baidu/ernie-5.5"]},
    {"id": "gemini", "name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
     "signup": "https://aistudio.google.com/apikey", "prefer": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]},
    {"id": "ollama", "name": "Local (Ollama)", "base": "http://127.0.0.1:11434/v1", "signup": "", "local": True,
     "prefer": ["qwen3.5", "qwen3", "llama3", "gemma3", "mistral"]},
]


# Across providers, smartest-and-quick first. Groq's free tier allows only a few requests a minute per
# model, so when its best model is busy we go to another provider's big model, not Groq's smaller ones.
RANK = [("groq", "openai/gpt-oss-120b"), ("nvidia", "nvidia/nemotron-3-ultra"), ("nvidia", "mistralai/mistral-large-4"),
        ("openrouter", "nvidia/nemotron-3-ultra"), ("groq", "moonshotai/kimi-k2"), ("groq", "qwen/qwen3"),
        ("nvidia", "deepseek-ai/deepseek-v3"), ("openrouter", "google/gemma-4-31b"), ("gemini", "gemini-2.5-flash")]


def _size(model: str) -> float:
    """Parameter count in billions read from the model id ("gpt-oss-20b" -> 20); unknown sizes sort last."""
    m = re.search(r"(\d+(?:\.\d+)?)b\b", model.lower())
    return float(m.group(1)) if m else 1e9


def _rank(pid: str, model: str, fallback: int) -> int:
    return next((i for i, (rp, rm) in enumerate(RANK) if rp == pid and model.startswith(rm)), 100 + fallback)


def load_keys() -> dict:
    try:
        return json.loads(SECRETS.read_text())
    except Exception:
        return {}


def save_key(provider: str, key: str):
    keys = load_keys()
    if key:
        keys[provider] = key.strip()
    else:
        keys.pop(provider, None)
    SECRETS.write_text(json.dumps(keys, indent=2))
    os.chmod(SECRETS, 0o600)


# ---- tools ----------------------------------------------------------------------
_ENV = dict(os.environ, PATH=str(config.ROOT / ".venv" / "bin") + ":/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"))


def run_command(command: str) -> str:
    """Run a shell command on the user's Mac (zsh) and return its output. Use for opening apps
    (open -a "App"), files, AppleScript (osascript), system info, anything a terminal can do."""
    try:
        # Not a login shell: profile scripts print their own noise, which models read as "it failed".
        r = subprocess.run(["/bin/zsh", "-c", command], capture_output=True, text=True, timeout=60,
                           cwd=str(config.HOME), env=_ENV)
    except subprocess.TimeoutExpired:
        return "Timed out after 60 seconds."
    out, err = r.stdout.strip(), r.stderr.strip()
    if r.returncode == 0:
        return (out or "Done (it worked, no output).")[:6000]
    return f"Failed (exit code {r.returncode}): {(err or out)[:3000]}"


def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file with the content (makes folders as needed). Use this for code, HTML, CSS
    and documents instead of shell heredocs."""
    try:
        p = os.path.expanduser(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
        return f"Wrote {len(content)} characters to {path}."
    except Exception as e:
        return f"Couldn't write it: {e}"


def read_file(path: str) -> str:
    """Read a text file from the user's Mac (first 20,000 characters)."""
    try:
        with open(os.path.expanduser(path), errors="replace") as f:
            return f.read(20000)
    except Exception as e:
        return f"Couldn't read it: {e}"


# Mike's own tools, minus the image ones (most free models are text-only: read apps with `ui`).
# (The everyday set only: every tool description costs tokens against the free per-minute limits.)
TOOL_FUNCS = {f.__name__: f for f in [
    run_command, read_file, write_file, mike_tools.find_skill, mike_tools.use_skill,
    mike_tools.ui, mike_tools.press, mike_tools.set_text, mike_tools.menu, mike_tools.type_text,
    mike_tools.press_key, mike_tools.media,
    mike_tools.set_reminder, mike_tools.list_reminders, mike_tools.cancel_reminder,
    mike_tools.remember, mike_tools.recall,
]}


def _schema(fn) -> dict:
    hints = typing.get_type_hints(fn)
    props, required = {}, []
    for name, p in inspect.signature(fn).parameters.items():
        t = hints.get(name, str)
        args = typing.get_args(t)
        base = next((a for a in args if a is not type(None)), t) if args else t
        if typing.get_origin(base) is list or base is list:
            props[name] = {"type": "array", "items": {"type": "string"}}
        else:
            props[name] = {"type": {int: "integer", float: "number", bool: "boolean"}.get(base, "string")}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    doc = " ".join((fn.__doc__ or "").split())
    if len(doc) > 260:                       # free tiers count every token: keep descriptions short
        cut = doc.rfind(". ", 0, 260)
        doc = doc[: cut + 1] if cut > 80 else doc[:257] + "…"
    return {"type": "function", "function": {"name": fn.__name__, "description": doc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


TOOLS = [_schema(f) for f in TOOL_FUNCS.values()]

BACKUP_PERSONA = f"""You are {config.NAME}, the user's AI companion on their Mac, like FRIDAY for Iron Man. Your words
are spoken aloud. Talk like a sharp, warm friend their age: short (one or two sentences), casual, real
reactions, no markdown, lists, emoji or URLs. Never invent facts, plans or events about their life.
Don't tack on filler like "Let me know if you need anything else" or "What's next?"; just stop.

Be honest about actions. Nothing happens unless you call a tool. Never say something is done, playing,
set or opened unless a tool result in this turn shows it worked. If it failed, say so plainly.
Never repeat the same tool call; if a step didn't work, try a different way or tell them.
Only do what they asked: no extra reminders or actions they didn't ask for.
If what they said sounds like a mishearing or isn't meant for you, ask briefly.
Use the conversation so far: "it", "that", "the exam" refer to what was just discussed.

Tools: run_command (zsh), read_file, ui/press/set_text/menu/type_text/press_key to operate any app
(ui lists an app's buttons/fields with [ids]; press/set_text act on those ids), media keys, reminders,
memory (remember lasting facts they tell you; recall past conversations).
Recipes:
- Open an app: run_command open -a "App". Web search: open "https://www.google.com/search?q=..." (or youtube.com/results?search_query=...).
- Play music: run_command open "spotify:search:WHAT" then ui(app="Spotify", find="Play") and press a
  matching "Play ..." button (not the bottom bar). For a vibe ("something nice"), search a playlist like "chill vibes".
  Pause/next: ui(app="Spotify", find="Pause"/"Next") and press. media keys are only a fallback.
- Never change the Mac's microphone input volume or system mic settings, even if asked to mute or silence the mic: Mike's own mute handles that, and zeroing the system mic leaves him deaf. Speaker/output volume is fine.
- Act first, talk last: don't narrate steps or ask what looking would answer; do it, check it, then say one short
  sentence. Never state what you haven't verified from a tool result this step; if unsure, look again or say so.
- Finish what you start: for multi-step or ongoing tasks keep working (look, act, check) until it is really done.
- Making a PDF, document, slides, spreadsheet, image, simulation, animation or website: if a skill is already loaded
  for this request ("A skill applies" below), follow it; otherwise call find_skill first, then use_skill with the best
  match and follow it. Skills make the result far better. Write files with write_file (not shell heredocs), run scripts with run_command
  (python3), check the file exists, then open it. Save in ~/Documents/Mike unless told otherwise.
- Volume: run_command osascript -e "set volume output volume 40".
- Weather: run_command curl -s "wttr.in/?format=%l:+%C+%t". Battery: pmset -g batt.
- Files: mdfind -name "..." or mdfind "kind:pdf topic"; read PDFs with pdftotext FILE - | head -200.
- Reminders: set_reminder with the exact time they said; to change one, cancel_reminder then set_reminder.
The "Right now" note tells you the time, the app they're in and any selected text: use it silently,
never read it out. After acting, say what happened in one short line.
"""


def backup_system_prompt() -> str:
    """Small on purpose: free tiers limit tokens per minute."""
    parts = [BACKUP_PERSONA]
    known = memory.facts()
    if known:
        parts.append("What you know about the user:\n" + "\n".join(f"- {f}" for f in known[-25:]))
    last = memory.recent(6)
    if last:
        parts.append("Latest exchanges (oldest first):\n" + memory.format_entries(last, 220))
    parts.append(f"Today is {time.strftime('%A %d %B %Y')}.")
    return "\n\n".join(parts)


# Spoken requests that mean "do something" (the reply must come from a real tool result).
ACTION_RE = re.compile(r"\b(play|open|launch|start|set|remind|wake|timer|alarm|turn|switch|close|quit|pause|resume|"
                       r"skip|next|send|text|message|email|create|make|add|delete|remove|move|rename|volume|mute|"
                       r"change|cancel|write|type|put|save|download|install|run)\b", re.I)
# Replies that claim something was done.
CLAIM_RE = re.compile(r"\b(playing|set|done|opened|started|turned|sent|added|scheduled|created|paused|closed|"
                      r"launched|queued|cued|changed|moved|deleted|switched|saved|written|on it|open|on|up)\b", re.I)
NUDGE_ACT = ("(Check: you haven't called any tool this turn, so nothing has actually happened. Use the tools now to "
             "really do it, then reply. If you can't do it, say so honestly.)")
NUDGE_FILE = ("(Check: {name} doesn't exist yet, so the task isn't done and you must not say it is. Look at what your last "
              "command printed, fix the problem, then build it now: write the code with write_file, run it with run_command, "
              "and check the file exists.)")
ROUTER_SYSTEM = (
    "You route a voice assistant's requests. You get the user's request and a list of skills (ready-made know-how for "
    "making things). Reply with the single skill name that would clearly make the result better, or NONE if no skill is "
    "needed (chat, questions, simple commands, opening apps, music, reminders, settings). Reply with only the name or NONE.")
NUDGE_SAY = "(Now reply to the user in one short spoken sentence: what happened?)"


def _targets(request: str) -> list[str]:
    """Files or folders named in the request that don't exist yet: they're what has to exist when he's done."""
    out = []
    for raw in re.findall(r"(?:~|/)[^\s'\"<>|,;]*[\w/]", request):
        p = os.path.expanduser(raw)
        if p.count("/") >= 2 and not os.path.exists(p) and p not in out:
            out.append(p)
    return out


def _missing(paths: list[str]) -> list[str]:
    bad = []
    for p in paths:
        if (p.endswith("/") or "." not in os.path.basename(p)):        # a folder: must exist and hold something
            ok = os.path.isdir(p) and any(os.scandir(p))
        else:
            ok = os.path.isfile(p) and os.path.getsize(p) > 0
        if not ok:
            bad.append(p)
    return bad


class _ProviderDown(Exception):
    pass


class FallbackBrain:
    def __init__(self, on_delta: Callable[[str], Awaitable[None]], on_tool: Callable[[str, dict], Awaitable[None]],
                 on_done: Callable[[dict], Awaitable[None]], on_permission: Callable[[str, dict], Awaitable[bool]]):
        self.on_delta, self.on_tool, self.on_done, self.on_permission = on_delta, on_tool, on_done, on_permission
        self.history: list[dict] = []
        self.busy = False
        self.task: asyncio.Task | None = None
        self.models: dict[str, list[str]] = {}  # provider -> usable model ids, best first
        self.failed_until: dict[str, float] = {}   # "provider" or "provider/model" -> benched until
        self.last_used = ""                    # "Groq · openai/gpt-oss-120b"

    # ---- providers --------------------------------------------------------
    def available(self) -> list[dict]:
        keys = load_keys()
        now = time.time()
        return [p for p in PROVIDERS if (p.get("local") or keys.get(p["id"])) and self.failed_until.get(p["id"], 0) < now]

    def lanes(self, p: dict) -> list[str]:
        now = time.time()
        return [m for m in self.models.get(p["id"], []) if self.failed_until.get(f"{p['id']}/{m}", 0) < now]

    async def resolve_model(self, client: httpx.AsyncClient, p: dict) -> str | None:
        """Finds every preferred model the provider offers right now; returns the best one."""
        if p["id"] not in self.models:
            key = load_keys().get(p["id"], "")
            try:
                r = await client.get(f"{p['base']}/models", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=6)
                r.raise_for_status()
                ids = [m.get("id", "") for m in (r.json().get("data") or [])]
                if p.get("only"):
                    ids = [i for i in ids if i.endswith(p["only"])]
            except Exception as e:
                log.info("%s: can't list models (%s)", p["name"], str(e)[:120])
                return None
            picks = []
            for want in p["prefer"]:
                for i in ([i for i in ids if i == want] or [i for i in ids if want in i]):
                    i = i.split("/", 1)[1] if i.startswith("models/") else i
                    if i not in picks:
                        picks.append(i)
            if not p.get("local"):
                picks = await self._verify(client, p, key, picks[:6])
            if not picks:
                return None
            self.models[p["id"]] = picks[:4]
        return self.models[p["id"]][0]

    async def _verify(self, client: httpx.AsyncClient, p: dict, key: str, ids: list[str]) -> list[str]:
        """Keep only models that really answer a tool-enabled request (lists often include retired ones)."""
        tool = {"type": "function", "function": {"name": "noop", "description": "unused",
                                                 "parameters": {"type": "object", "properties": {}}}}
        async def ok(m):
            try:
                r = await client.post(f"{p['base']}/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=12,
                                      json={"model": m, "max_tokens": 4, "tools": [tool],
                                            "messages": [{"role": "user", "content": "Reply OK"}]})
                return r.status_code == 200 or r.status_code == 429   # 429 = real model, just busy right now
            except Exception:
                return False
        results = await asyncio.gather(*(ok(m) for m in ids))
        good = [m for m, g in zip(ids, results) if g]
        log.info("%s: usable models %s", p["name"], good)
        return good

    async def warm(self):
        """Find and check each provider's models in the background, so the first request is quick."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=6)) as client:
            await asyncio.gather(*(self.resolve_model(client, p) for p in self.available()), return_exceptions=True)

    def forget_models(self):
        self.models.clear()
        self.failed_until.clear()

    async def status(self) -> list[dict]:
        """For Settings: which providers are set up and which model each would use."""
        keys = load_keys()
        out = []
        async with httpx.AsyncClient() as client:
            for p in PROVIDERS:
                k = keys.get(p["id"], "")
                has = bool(p.get("local") or k)
                model = await self.resolve_model(client, p) if has else None
                out.append({"id": p["id"], "name": p["name"], "signup": p["signup"], "local": bool(p.get("local")),
                            "has_key": bool(k), "key_hint": f"••••{k[-4:]}" if len(k) > 8 else "",
                            "model": model, "ready": bool(model)})
        return out

    # ---- conversation -----------------------------------------------------
    def reset(self):
        self.history = []

    async def interrupt(self):
        if self.task and not self.task.done():
            self.task.cancel()

    async def send(self, text: str):
        self.busy = True
        self.history.append({"role": "user", "content": text})
        self.task = asyncio.create_task(self._turn())

    async def _turn(self):
        start = time.time()
        reply, error = "", None
        try:
            reply = await self._run()
        except asyncio.CancelledError:
            self.busy = False
            await self.on_done({"result": "", "interrupted": True, "duration_ms": int((time.time() - start) * 1000)})
            return
        except Exception as e:
            log.warning("backup brain failed: %s", e)
            error = str(e)[:200]
        self.busy = False
        self.history = self.history[-30:]
        await self.on_done({"result": reply, "error": error, "duration_ms": int((time.time() - start) * 1000)})

    async def _run(self) -> str:
        providers = self.available()
        if not providers:
            raise RuntimeError("No backup model is set up. Add a free key in Settings > Model.")
        system = backup_system_prompt()
        # The skill router runs alongside the answer, not before it: most requests need no skill, and
        # those shouldn't wait for it. If it picks one before anything was said or done, restart with it.
        self._acted = False
        mark = len(self.history)
        route = asyncio.create_task(self._skill_for(self._request_text()))
        answer = asyncio.create_task(self._answer(providers, system))
        try:
            await asyncio.wait({route, answer}, return_when=asyncio.FIRST_COMPLETED)
            if not answer.done():
                note = route.result() if route.done() and not route.exception() else ""
                if note and not self._acted:
                    answer.cancel()
                    await asyncio.gather(answer, return_exceptions=True)
                    del self.history[mark:]
                    answer = asyncio.create_task(self._answer(providers, system + "\n\n" + note))
            return await answer
        finally:
            for t in (route, answer):
                if not t.done():
                    t.cancel()

    async def _answer(self, providers: list[dict], system: str) -> str:
        last_err = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=6)) as client:
            lanes = []
            for p in providers:
                if await self.resolve_model(client, p):
                    lanes += [(p, m) for m in self.lanes(p)]
            lanes.sort(key=lambda pm: _rank(pm[0]["id"], pm[1], len(lanes)))   # stable: provider order breaks ties
            for p, model in lanes:
                if self.failed_until.get(p["id"], 0) > time.time():
                    continue
                try:
                    text = await self._loop(client, p, model, system)
                    self.last_used = f"{p['name']} · {model}"
                    log.info("backup answered with %s", self.last_used)
                    return text
                except _ProviderDown as e:
                    msg = str(e)
                    log.info("%s %s unavailable (%s); trying the next one", p["name"], model, msg[:90])
                    last_err = e
                    if "429" in msg:          # that model's per-minute limit: skip it until it frees up
                        m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", msg)
                        wait = (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 60
                        self.failed_until[f"{p['id']}/{model}"] = time.time() + min(max(wait, 5), 600)
                    else:                     # provider down or key rejected: skip it for a while
                        self.failed_until[p["id"]] = time.time() + 300
        raise RuntimeError(f"All backup models are unavailable right now ({last_err or 'none reachable'}).")

    async def ask(self, system: str, user: str, max_tokens: int = 300, small: bool = False) -> str | None:
        """One quick question (no tools) to the best free model that answers within seconds.
        Costs no Claude usage. None if no free model could answer."""
        providers = self.available()
        if not providers:
            return None
        async with httpx.AsyncClient(timeout=httpx.Timeout(9, connect=4)) as client:
            lanes = []
            for p in providers:
                if await self.resolve_model(client, p):
                    lanes += [(p, m) for m in self.lanes(p)]
            lanes.sort(key=lambda pm: _rank(pm[0]["id"], pm[1], len(lanes)))
            if small:
                # A short yes/no-style question: the smallest model answers fastest, and using it
                # leaves the big one's per-minute allowance for the real answer.
                lanes.sort(key=lambda pm: _size(pm[1]))
            for p, model in lanes[:3]:
                key = load_keys().get(p["id"], "")
                body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                if p["id"] == "groq" and "gpt-oss" in model:
                    body["reasoning_effort"] = "low"
                try:
                    r = await client.post(f"{p['base']}/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {key}"} if key else {})
                    r.raise_for_status()
                    text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                    if text.strip():
                        return text.strip()
                except Exception as e:
                    log.info("quick question to %s %s failed (%s)", p["name"], model, str(e)[:80])
        return None

    async def _skill_for(self, request: str) -> str:
        """If a skill would clearly help with this request, its instructions (chosen by a quick free model)."""
        idx = skills.index()
        if not idx or len(request.split()) < 3:
            return ""
        listing = "\n".join(f"{s['name']}: {' '.join(s['description'].split())[:100]}" for s in idx.values())
        try:
            out = await asyncio.wait_for(self.ask(ROUTER_SYSTEM, f"Skills:\n{listing}\n\nUser request: {request[:400]}", 200, small=True), 10)
        except Exception:
            return ""
        name = (out or "").strip().splitlines()[0].strip(" .`'\"*").lower() if (out or "").strip() else ""
        if name not in idx:
            return ""
        log.info("skill picked for this request: %s", name)
        return "A skill applies to this request. Follow it:\n" + skills.load(name)

    async def _loop(self, client, p, model, system) -> str:
        """Model ↔ tools until it answers in text (max 10 rounds)."""
        key = load_keys().get(p["id"], "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        seen: dict[str, str] = {}            # tool call -> result, to stop "open -a Spotify" x9 loops
        try:
            return await self._rounds(client, p, model, system, headers, self._request_text(), seen, {}, set())
        finally:
            self.history = [m for m in self.history if not m.get("nudge")]

    async def _rounds(self, client, p, model, system, headers, request, seen, per_tool, nudged) -> str:
        said, used_tool = "", False
        targets = _targets(request)
        for _ in range(16):
            # An action request with no tool used yet: hold the words back until we know they're true.
            hold = (not used_tool and bool(ACTION_RE.search(request)) and "act" not in nudged) or bool(_missing(targets))
            body = {"model": model, "stream": True, "tools": TOOLS, "temperature": 0.4, "max_tokens": 1200 if p["id"] == "groq" else 4000,   # reasoning models spend part of it thinking
                    
                    "messages": [{"role": "system", "content": system + self._now_note()}] + self._payload_history()}
            if p["id"] == "groq" and "gpt-oss" in model:
                body["reasoning_effort"] = "medium"     # think a little before acting: far fewer silly moves
            text, calls = "", {}
            head = {"buf": "", "done": False}

            async def emit(piece: str):
                """Stream text out, dropping an echoed '[context: …]' / '(Background …)' at the start."""
                nonlocal text
                if hold:
                    text += piece
                    return
                if not head["done"]:
                    head["buf"] += piece
                    b = head["buf"].lstrip()
                    if not b:
                        return
                    if b[0] in "[(":
                        close = b.find("]" if b[0] == "[" else ")")
                        if close == -1 and len(b) < 400:
                            return                          # wait for the bracket to close
                        if close != -1 and re.match(r"[\[(]\s*(context|background)", b, re.I):
                            b = b[close + 1:].lstrip()
                    head["done"] = True
                    piece = b
                    if not piece:
                        return
                text += piece
                self._acted = True
                await self.on_delta(piece)

            try:
                async with client.stream("POST", f"{p['base']}/chat/completions", json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        detail = (await r.aread()).decode(errors="replace")[:400]
                        if r.headers.get("retry-after"):
                            detail += f" (try again in {r.headers['retry-after']}s)"
                        raise _ProviderDown(f"HTTP {r.status_code} {detail}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for ch in chunk.get("choices", []):
                            d = ch.get("delta") or {}
                            if d.get("content"):
                                await emit(d["content"])
                            for tc in d.get("tool_calls") or []:
                                c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                                c["id"] = tc.get("id") or c["id"]
                                fn = tc.get("function") or {}
                                c["name"] += fn.get("name") or ""
                                c["args"] += fn.get("arguments") or ""
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
                if said or text:
                    raise RuntimeError(f"connection dropped mid-reply ({type(e).__name__})")
                raise _ProviderDown(type(e).__name__)
            if not head["done"] and head["buf"].strip():
                head["done"] = True
                await emit("")
            if not calls:
                log.info("backup reply with no tool call: %r", text[:160])
                if hold and text.strip() and (CLAIM_RE.search(text) or not text.rstrip().endswith("?")):
                    # Claimed it did something without touching a tool: make it actually do it.
                    nudged.add("act")
                    log.info("backup claimed an action without a tool (%r); nudging", text[:80])
                    self.history += [{"role": "assistant", "content": text, "nudge": True},
                                     {"role": "user", "content": NUDGE_ACT, "nudge": True}]
                    continue
                gone = _missing(targets)
                if gone:
                    tries = sum(1 for n in nudged if n.startswith("file"))
                    if tries < 2:
                        nudged.add(f"file{tries}")
                        log.info("backup stopped but %s isn't there; sending it back (%d)", gone[0], tries + 1)
                        if text.strip():
                            self.history.append({"role": "assistant", "content": text, "nudge": True})
                        self.history.append({"role": "user", "content": NUDGE_FILE.format(name=os.path.basename(gone[0])), "nudge": True})
                        continue
                    msg = f"I couldn't finish that one. {os.path.basename(gone[0])} isn't there."
                    await self.on_delta(msg)
                    self.history.append({"role": "assistant", "content": msg})
                    return said + msg
                if hold and text:
                    await self.on_delta(text)
                if not (said + text).strip() and "say" not in nudged:
                    nudged.add("say")             # went quiet after its tools: get a one-line answer
                    self.history.append({"role": "user", "content": NUDGE_SAY, "nudge": True})
                    continue
                said += text
                self.history.append({"role": "assistant", "content": text})
                return said
            if hold and text:
                await self.on_delta(text)
            said += text
            ordered = [calls[i] for i in sorted(calls)]
            self.history.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                for i, c in enumerate(ordered)]})
            used_tool = True
            for i, c in enumerate(ordered):
                sig = c["name"] + "|" + re.sub(r"\s+", "", c["args"] or "")
                per_tool[c["name"]] = per_tool.get(c["name"], 0) + 1
                if sig in seen:
                    result = (f"You already ran exactly this a moment ago; it returned: {seen[sig][:300]}. "
                              "Don't repeat it. Take a different next step, or tell the user what happened.")
                elif per_tool[c["name"]] > 8:
                    result = "That's too many tries with this tool. Stop and tell the user honestly where things stand."
                else:
                    result = await self._call(c["name"], c["args"])
                    seen[sig] = result
                self.history.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": result[:3000]})
        return said

    def _payload_history(self) -> list[dict]:
        """Recent turns only, with the [context] line moved to the end (models tend to echo a leading one)."""
        starts = [i for i, m in enumerate(self.history) if m["role"] == "user" and not m.get("nudge")]
        recent = self.history[starts[-4]:] if len(starts) > 4 else list(self.history)
        current = starts[-1] if starts else 0
        out = []
        for i, m in enumerate(recent, start=len(self.history) - len(recent)):
            # earlier turns: drop intermediate tool steps, keep what was said
            if i < current and (m["role"] == "tool" or (m["role"] == "assistant" and m.get("tool_calls"))):
                continue
            if m["role"] == "user" and m["content"].startswith("[context:"):
                m = {"role": "user", "content": m["content"].partition("\n")[2]}   # context goes in the system note
            out.append({k: v for k, v in m.items() if k != "nudge"})
        return out

    def _request_text(self) -> str:
        last = next((m["content"] for m in reversed(self.history) if m["role"] == "user" and not m.get("nudge")), "")
        return last.partition("\n")[2] if last.startswith("[context:") else last

    def _now_note(self) -> str:
        last = next((m["content"] for m in reversed(self.history) if m["role"] == "user" and not m.get("nudge")), "")
        if last.startswith("[context:"):
            ctx = last.partition("\n")[0].strip("[]").replace("context:", "").strip()
            extra = [l.strip("[]") for l in last.splitlines()[1:] if l.startswith("[selected text")]
            return "\n\nRight now: " + ctx + ("\n" + extra[0] if extra else "")
        return ""

    async def _call(self, name: str, raw: str) -> str:
        fn = TOOL_FUNCS.get(name)
        if not fn:
            return f"Unknown tool {name}."
        try:
            args = json.loads(raw or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            return "Arguments weren't valid JSON; try again."
        self._acted = True
        tool_name = {"run_command": "Bash", "write_file": "Write"}.get(name, name)
        perm_args = {"file_path": args.get("path", "")} if name == "write_file" else args
        await self.on_tool(tool_name, args)
        allowed = await self.on_permission(tool_name, perm_args)   # sensitive shell commands still ask you
        if allowed is not True:
            return allowed if isinstance(allowed, str) else "The user declined this action."
        try:
            out = await asyncio.get_running_loop().run_in_executor(None, lambda: fn(**args))
        except TypeError as e:
            return f"Bad arguments: {e}"
        except Exception as e:
            return f"Failed: {e}"
        return out if isinstance(out, str) else json.dumps(out)[:8000]
