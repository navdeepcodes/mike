"""Mike - a voice + camera assistant whose brain is Claude Code on your Pro plan.

Run:  ./run.sh   then open http://127.0.0.1:8765
"""
import asyncio
import difflib
import base64
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

import config
import greetings
import memory
import random
import reminders
from fallback import FallbackBrain, PROVIDERS, save_key
import subprocess
from brain import Brain
from ears import Ears
import voice
import voiceprint
from voice import SentenceSplitter, synth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", force=True)
log = logging.getLogger("mike")
for noisy in ("httpx", "httpcore", "huggingface_hub", "phonemizer", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.ERROR)   # keep data/server.log readable

FILLERS = ["hey", "ok", "okay", "yo", "hi", "hello", "so", "and", "um", "uh"]
_names = sorted({w for w in config.WAKE_WORDS if " " not in w}, key=len, reverse=True)
WAKE_RE = re.compile(
    r"^\W*(?:(?:%s)\W+)*(?:%s)\b\W*" % ("|".join(FILLERS), "|".join(map(re.escape, _names))),
    re.I,
)
YES = re.compile(r"\b(yes|yeah|yep|yup|sure|do it|go ahead|go for it|okay|ok|confirm|approved?|affirmative|please do)\b", re.I)
NO = re.compile(r"\b(no|nope|nah|don't|do not|stop|cancel|deny|negative|never mind|abort)\b", re.I)
# Simple device commands run right here: instant, and they cost no Claude usage at all.
_PLEASE = r"^\W*(?:(?:please|hey|can you|could you)\W+)*"
_END = r"(?:\W+(?:please|now|for me|a bit|a little|just a little))*\W*$"
LOCAL_MEDIA = [
    (re.compile(_PLEASE + r"(?:skip|next)(?:\W+(?:this|the|that))?\W+(?:song|track)" + _END, re.I), "next", 1, "Next."),
    (re.compile(_PLEASE + r"(?:previous|last|go\W+back)(?:\W+(?:to\W+)?(?:the|that))?(?:\W+(?:song|track))" + _END, re.I), "previous", 1, "Going back."),
    (re.compile(_PLEASE + r"pause(?:\W+(?:the|this|that))?\W+(?:music|song|track|video|playback)" + _END, re.I), "play_pause", 1, "Paused."),
    (re.compile(_PLEASE + r"(?:turn\W+(?:it|the\W+volume)\W+up|volume\W+up|turn\W+up\W+the\W+volume|louder|increase\W+the\W+volume)" + _END, re.I), "volume_up", 2, "Louder."),
    (re.compile(_PLEASE + r"(?:turn\W+(?:it|the\W+volume)\W+down|volume\W+down|turn\W+down\W+the\W+volume|quieter|lower\W+the\W+volume|decrease\W+the\W+volume)" + _END, re.I), "volume_down", 2, "Quieter."),
]
LOCAL_TIME = re.compile(_PLEASE + r"(?:what(?:'s|\W+is)\W+(?:the\W+)?time(?:\W+is\W+it)?|what\W+time\W+is\W+it|tell\W+me\W+the\W+time)" + _END, re.I)
LOCAL_DATE = re.compile(_PLEASE + r"(?:what(?:'s|\W+is)\W+(?:the\W+|today'?s\W+)?date(?:\W+today)?|what\W+day\W+is\W+it(?:\W+today)?)" + _END, re.I)

STOP = re.compile(r"^\W*(ok(ay)?\W+)?(stop|cancel|never ?mind|shut up|quiet|be quiet|that's enough|enough|wait|hold on|hang on|pause"
                  r"|one sec(ond)?|stop talking)(\W+(mike|please|a sec(ond)?|a minute))*\W*$", re.I)
# "Mike, quit" / "shut down" / "go to sleep" / "goodbye": the whole command, so "close Roblox" never matches.
QUIT = re.compile(r"^\W*(please\W+)?(quit|exit|shut\W*down|turn\W*off|power\W*off|good\W*bye|bye(\W*bye)?)"
                  r"(\W+(yourself|now|please|mike|the\W*app|mike\W*app|for\W*now|for\W*today))*\W*$", re.I)
# "Mike, stop listening" / "mute" / "go to sleep": the mic turns off (⌥⇧M or the mic button turns it back on).
MUTE = re.compile(r"^\W*(please\W+)?(go\W+)?(stop\W+listening|don'?t\W+listen|mute\W+(yourself|(the\W+|my\W+|your\W+)?mic(rophone)?)"
                  r"|(turn\W+)?(the\W+)?mic(rophone)?\W+off|turn\W+off\W+(the\W+)?mic(rophone)?|go\W*to\W*sleep|sleep)"
                  r"(\W+(to\W+me|now|please|mike|for\W*now|for\W*a\W*(bit|while)))*\W*$", re.I)
RESET = re.compile(r"\b(new conversation|start over|fresh start|forget (this|that|everything)|reset)\b", re.I)


# "What can you see, Mike?" — the name at the end, after a comma.
WAKE_END_RE = re.compile(
    r",\s*(?:(?:hey|ok|okay)\W+)?(?:%s)\W*$" % "|".join(map(re.escape, _names)), re.I)
# Nothing but greetings / the name again ("Mike. Hey, Mike.") means "I'm listening", not a question.
GREETING_ONLY = re.compile(
    r"^\W*(?:(?:hey|hi|hello|yo|ok|okay|you there|are you there|wake up|%s)\W*)*$"
    % "|".join(map(re.escape, _names)), re.I)


MODEL_IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
             "fable": "claude-fable-5-2"}
MODEL_RE = re.compile(r"\b(switch|change|use|go|move)\b.*\b(opus|sonnet|haiku|fable)\b", re.I)
SETTINGS_FILE = config.DATA / "settings.json"
PET_IDS = {"none", "mike", "mochi", "tapey", "bolt", "boo", "kit"}
PET_MODES = {"active", "always", "off"}   # show while Mike is active / always / never


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def save_setting(**kv):
    data = load_settings()
    data.update(kv)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


# Every user-facing setting: key -> (config attribute, type, restart brain when changed).
SETTINGS_SPEC = {
    "user_name": ("USER_NAME", str, False),
    "model": ("MODEL", str, True),
    "effort": ("EFFORT", str, True),
    "fresh_after_min": ("FRESH_AFTER_MIN", int, False),
    "voice_speed": ("KOKORO_SPEED", float, False),
    "wake_word": ("WAKE_WORD", bool, False),
    "followup_seconds": ("FOLLOWUP_SECONDS", int, False),
    "barge_in": ("BARGE_IN", bool, False),
    "voice_match": ("VOICE_MATCH", bool, False),
    "welcome_back": ("WELCOME_BACK", bool, False),
    "morning_briefing": ("MORNING_BRIEFING", bool, False),
    "break_nudge_min": ("BREAK_NUDGE_MIN", int, False),
    "memory_auto": ("MEMORY_AUTO", bool, False),
    "claude_memory": ("CLAUDE_MEMORY", bool, True),
    "brain_mode": ("BRAIN_MODE", str, False),
}
MODEL_CHOICES = [
    {"id": "claude-haiku-5-5", "name": "Haiku 5.5", "desc": "Fastest. Great for everyday voice use."},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "desc": "Smarter for harder tasks, a little slower."},
    {"id": "claude-opus-5-5", "name": "Opus 5.5", "desc": "Deep reasoning. Uses your limit fastest."},
    {"id": "claude-fable-5-2", "name": "Fable 5.2", "desc": "Most capable. Slowest and heaviest on usage."},
]


def apply_saved_settings():
    for key, val in load_settings().items():
        if key in SETTINGS_SPEC:
            attr, typ, _ = SETTINGS_SPEC[key]
            try:
                setattr(config, attr, typ(val) if val is not None else None)
            except (TypeError, ValueError):
                pass


def current_settings() -> dict:
    out = {k: getattr(config, attr) for k, (attr, _, _) in SETTINGS_SPEC.items()}
    out["model"] = config.MODEL or "claude-haiku-5-5"
    return out


def pet_settings() -> dict:
    s = load_settings()
    return {"pet": s.get("pet", "mike"), "mode": s.get("pet_mode", "active")}


def split_wake(text: str) -> tuple[bool, str]:
    woke, cmd = False, text.strip()
    while (m := WAKE_RE.match(cmd)):
        woke, cmd = True, cmd[m.end():].strip()
    if (m := WAKE_END_RE.search(cmd)):
        woke, cmd = True, cmd[:m.start()].strip()
    if woke and GREETING_ONLY.match(cmd):
        cmd = ""
    return woke, cmd


class Mike:
    def __init__(self):
        self.ws: WebSocket | None = None      # the main window: owns mic, camera and speaker
        self.ws_lock = asyncio.Lock()
        self.pets: set[WebSocket] = set()     # desktop pets: follow along, can wake/stop/answer
        self.state = "booting"
        self.gen = 0                      # bumps on every interrupt; stale audio is dropped
        self.speech_q: asyncio.Queue = asyncio.Queue()
        self.synthing = 0                 # sentences currently being turned into audio
        self.splitter = SentenceSplitter()
        self.followup_until = 0.0
        self.offer_followup = False
        self.pending_confirm: dict | None = None
        self.snapshots: dict[str, asyncio.Future] = {}
        self.natives: dict[str, asyncio.Future] = {}
        self.last_command_at = 0.0          # for starting a fresh, fast conversation after a break
        self.turn_user = ""                 # what the current turn was asked (for the journal)
        self.turn_reply: list[str] = []
        self.turn_no = 0
        self.session_turns = 0
        self.auto: dict | None = None      # a task Mike is carrying on with by himself
        self.round_tools = 0               # tools used in the current round
        self.auto_done_flag = False        # the brain said task_complete
        self.held: list[str] = []          # words held back while he works on his own
        self.last_auto_said = ""
        self.recent_said: list[str] = []
        self.script_ok: dict[str, bool] = {}     # apps macOS already lets Mike script
        self.ducked = False
        self.acked = False
        self.hearing = False
        self.present = None
        self.last_seen = time.time()
        self.usage: dict = {}
        self.brain = Brain(self.on_delta, self.on_tool, self.on_done, self.on_permission,
                           self.on_log, self.on_usage)
        self.ears = Ears(self.on_heard, self.on_level)
        self.voice_id = voiceprint.VoicePrint()
        # Backup brain (free cloud models, then local) for when Claude is unavailable.
        self.launched_hello = False
        self.backup = FallbackBrain(self.on_delta, self.on_tool, self.on_done, self.on_permission)
        self.active = self.brain              # whichever brain is handling the current request
        self.claude_blocked_until = 0.0       # Claude Pro limit reached until this time
        self.turn_prompt = ""                 # the request as sent (with context), for retrying on the backup
        self.retried = False
        self.ears.prefilter = self.voice_prefilter
        self.voice_score: float | None = None   # voice-match score of the clip being handled
        self.ears.attentive = lambda: bool(self.pending_confirm) or self.state == "speaking" or (
            self.state == "listening" and time.time() < self.followup_until + 1.5)

    # ---- window plumbing -------------------------------------------------
    # Event types the desktop pet follows (it never gets audio or camera requests).
    PET_EVENTS = {"state", "hearing", "chime", "transcript", "user", "assistant_delta", "assistant_done",
                  "tool", "confirm", "confirm_done", "reset", "presence", "mic"}

    async def send(self, obj: dict):
        ws = self.ws
        if ws:
            try:
                async with self.ws_lock:
                    await ws.send_text(json.dumps(obj))
            except Exception:
                pass
        t = obj.get("type")
        if self.pets and (t in self.PET_EVENTS or t == "audio"):
            # The pet shows subtitles, not sound: strip the audio payload.
            msg = {"type": "speak", "text": obj.get("text", "")} if t == "audio" else obj
            await self.send_pets(msg)

    async def set_pet(self, data: dict):
        pet, mode = data.get("pet"), data.get("mode")
        if pet in PET_IDS:
            save_setting(pet=pet)
        if mode in PET_MODES:
            save_setting(pet_mode=mode)
        msg = {"type": "pet", **pet_settings()}
        await self.send(msg)
        await self.send_pets(msg)

    async def send_pets(self, msg: dict):
        data = json.dumps(msg)
        for pw in list(self.pets):
            try:
                await pw.send_text(data)
            except Exception:
                self.pets.discard(pw)

    async def set_state(self, state: str):
        if state != self.state:
            self.state = state
            await self.send({"type": "state", "state": state})

    async def on_log(self, text: str):
        await self.send({"type": "log", "text": text})

    async def on_usage(self, info: dict):
        self.usage = info
        await self.send({"type": "usage", "info": info})
        if info.get("status") == "rejected":
            until = float(info.get("resetsAt") or time.time() + 3600)
            fresh = until > self.claude_blocked_until + 60
            self.claude_blocked_until = until
            when = time.strftime("%-I:%M %p", time.localtime(until))
            if config.BRAIN_MODE == "auto" and self.backup.available():
                if fresh:
                    self.say(f"Claude's limit is used up, so I'm on my backup brain until {when}.")
            else:
                self.say(f"You've hit your Claude usage limit. It resets at {when}.")

    # ---- speaking --------------------------------------------------------
    def say(self, text: str):
        self.recent_said = (self.recent_said + [text])[-6:]
        self.speech_q.put_nowait((self.gen, text))

    def is_echo(self, heard: str) -> bool:
        """Mike's own voice leaking from the speakers back into the mic?"""
        words = re.findall(r"[a-z']+", heard.lower())
        if not words:
            return True
        said = set(re.findall(r"[a-z']+", " ".join(self.recent_said).lower()))
        return sum(w in said for w in words) / len(words) >= 0.6

    async def duck(self, on: bool):
        if on != self.ducked:
            self.ducked = on
            await self.send({"type": "duck", "on": on})

    async def speech_worker(self):
        while True:
            gen, text = await self.speech_q.get()
            if gen != self.gen:
                continue
            self.synthing += 1
            try:
                wav = await synth(text)
            except Exception as e:
                log.warning("tts failed: %s", e)
                wav = None
            finally:
                self.synthing -= 1
            if not wav and gen == self.gen and self.speech_q.empty() and not self.brain.busy:
                await self.on_drained(gen)  # nothing to play after all; don't get stuck
                continue
            if gen != self.gen or not wav:
                continue
            await self.set_state("speaking")
            await self.send({"type": "audio", "gen": gen, "text": text,
                             "data": base64.b64encode(wav).decode()})
            if getattr(self, "t_heard", None):
                log.info("timing: heard → first sound %.2fs", time.time() - self.t_heard)
                self.t_heard = None

    async def interrupt(self):
        self.auto = None          # you spoke or said stop: whatever he was carrying on with ends
        self.held = []
        self.gen += 1
        self.splitter = SentenceSplitter()
        while not self.speech_q.empty():
            self.speech_q.get_nowait()
        await self.send({"type": "stop_audio", "gen": self.gen})
        if self.pending_confirm and not self.pending_confirm["future"].done():
            self.pending_confirm["future"].set_result(False)
        await self.brain.interrupt()
        await self.backup.interrupt()
        await self.set_state("idle")

    async def on_drained(self, gen: int):
        """The window finished playing everything it was given."""
        # More speech still coming (queued or being synthesized) => the reply isn't over yet.
        if gen != self.gen or not self.speech_q.empty() or self.synthing:
            return
        if self.pending_confirm:
            await self.set_state("confirming")
        elif self.brain.busy:
            await self.set_state("thinking")
        elif self.offer_followup:
            # Only after answering you: keep listening briefly without the wake word.
            self.offer_followup = False
            self.followup_until = time.time() + config.FOLLOWUP_SECONDS
            await self.set_state("listening")
            asyncio.create_task(self._expire_followup(self.followup_until))
        else:
            await self.set_state("idle")

    async def _expire_followup(self, until: float):
        await asyncio.sleep(max(0, until - time.time()) + 0.1)
        if self.state == "listening" and time.time() >= self.followup_until and not self.hearing:
            await self.set_state("idle")

    # ---- brain callbacks -------------------------------------------------
    async def on_delta(self, text: str):
        self.turn_reply.append(text)
        await self.send({"type": "assistant_delta", "text": text})
        if self.auto is not None or self.turn_user.startswith("("):
            self.held.append(text)          # working on his own: don't narrate every step aloud
            return
        for sentence in self.splitter.push(text):
            self.say(sentence)
        # The last sentence has no text after it, so don't wait for the whole turn to end:
        # if nothing more arrives for a moment, speak it.
        if self.splitter.ends_cleanly():
            sp, gen = self.splitter, self.gen
            buf = sp.buf
            async def flush_soon():
                await asyncio.sleep(0.18)
                if self.splitter is sp and gen == self.gen and sp.buf == buf:
                    for sentence in sp.flush():
                        self.say(sentence)
            asyncio.create_task(flush_soon())

    ACKS = ["On it.", "Sure, one sec.", "Yep, give me a sec.", "Okay, on it.", "Got it, hang on.", "One sec."]
    QUIET_TOOLS = {"remember", "update_memory", "forget", "keep_going", "task_complete", "dismiss_pet"}

    async def _ack_later(self, turn: int):
        """If a task runs a while with nothing said yet, say a quick 'on it' like a person would."""
        await asyncio.sleep(1.3)
        if turn == self.turn_no and not self.acked and not "".join(self.turn_reply).strip() and self.state == "thinking":
            self.acked = True
            self.say(random.choice(self.ACKS))

    async def on_tool(self, name: str, inp: dict):
        self.round_tools += 1
        # Text before a tool and text after it arrive glued together ("...at the top.Spotify's on").
        # Close off what was said so far as its own sentence.
        if self.turn_reply and not "".join(self.turn_reply).endswith((" ", "\n")):
            self.turn_reply.append(" ")
            await self.send({"type": "assistant_delta", "text": " "})
            for sentence in self.splitter.push("\n"):
                self.say(sentence)
        if name.replace("mcp__mike__", "") not in self.QUIET_TOOLS and not self.acked:
            asyncio.create_task(self._ack_later(self.turn_no))
        detail = (inp.get("description") or inp.get("command") or inp.get("file_path")
                  or inp.get("query") or inp.get("url") or inp.get("pattern") or "")
        pretty = name.replace("mcp__mike__", "").replace("mcp__", "")
        await self.send({"type": "tool", "name": pretty, "detail": str(detail)[:200]})
        log.info("tool %s: %s", pretty, str(detail)[:160])
        if pretty == "look":
            await self.send({"type": "flash", "what": "camera"})

    async def on_done(self, info: dict):
        claude_failed = (self.active is self.brain and not info.get("interrupted") and self.turn_prompt
                         and (info.get("error") or time.time() < self.claude_blocked_until))
        if (claude_failed and config.BRAIN_MODE == "auto" and not self.retried and self.backup.available()):
            # Claude couldn't answer (limit, offline, crash): hand the same request to the backup brain.
            self.retried = True
            log.info("Claude couldn't answer (%s); retrying on the backup brain", info.get("error") or "limit")
            self.splitter = SentenceSplitter()
            self.turn_reply = []
            self.active = self.backup
            await self.backup.send(self.turn_prompt)
            return
        if self.active is self.backup and self.backup.last_used and not info.get("error"):
            await self.send({"type": "log", "text": f"brain online · model {self.backup.last_used}"})
        for sentence in self.splitter.flush():
            self.say(sentence)
        self.splitter = SentenceSplitter()
        held, self.held = "".join(self.held).strip(), []
        if held and not info.get("interrupted"):
            # Only the concluding sentence, and never a near-repeat of what he said last round.
            last = [p for p in re.split(r"(?<=[.!?])\s+", held) if p.strip()][-1]
            if difflib.SequenceMatcher(None, last.lower(), self.last_auto_said.lower()).ratio() < 0.55:
                self.say(last)
                self.last_auto_said = last
        user_text = self.turn_user
        said = "".join(self.turn_reply).strip()
        if self.turn_user:
            reply = "".join(self.turn_reply).strip() or ("(interrupted)" if info.get("interrupted") else "")
            try:
                memory.log_turn(self.turn_user, reply)
            except Exception:
                log.exception("journal write failed")
        self.turn_user, self.turn_reply = "", []
        self.offer_followup = not info.get("interrupted")
        await self.send({"type": "assistant_done", **{k: info.get(k) for k in ("cost", "duration_ms", "error", "interrupted")}})
        if info.get("error") and not info.get("interrupted"):
            err = str(info["error"])
            if self.active is self.backup and "No backup model" in err:
                self.say("My backup brain isn't set up yet. Add a free key in Settings, under Model.")
            elif self.active is self.backup:
                self.say("Claude's out and my backup models are all busy right now. Try again in a minute.")
            else:
                self.say("Sorry, something went wrong on my end.")
            await self.on_log(f"error: {info['error']}")
        if await self._maybe_continue(info, user_text, said):
            return
        # If nothing is queued to speak, go straight to follow-up listening.
        await asyncio.sleep(0.05)
        if self.speech_q.empty() and self.state != "speaking":
            await self.on_drained(self.gen)

    JUDGE_SYSTEM = (
        "You check the work of a voice assistant that can operate the user's computer. You get the user's "
        "request and the assistant's last message after it did some actions. Decide whether the request is "
        "fully accomplished, or the assistant stopped with work still left that it could keep doing by itself: "
        "it did only one step of something with many steps or that keeps going, it said it will wait, watch, "
        "check back or continue, or the activity the user asked for isn't over. If it needs an answer or a "
        "decision from the user, or it cannot go on, that counts as finished. Reply with exactly one word: "
        "DONE or CONTINUE.")

    async def _is_finished(self, goal: str, said: str) -> bool | None:
        """Ask a quick free model whether the job is over. None if it couldn't be asked."""
        try:
            out = await asyncio.wait_for(self.backup.ask(
                self.JUDGE_SYSTEM, f"User request: {goal[:400]}\nAssistant's last message: {said[:500] or '(said nothing)'}"), 10)
        except Exception:
            return None
        if not out:
            return None
        word = out.strip().upper()
        log.info("job check: %s", word[:40])
        if "CONTINUE" in word and "DONE" not in word.split("CONTINUE")[0]:
            return False
        return True if "DONE" in word else None

    async def _maybe_continue(self, info: dict, user_text: str, said: str) -> bool:
        """He stopped. If he was mid-task (he said so, or he worked and went silent), send him back in."""
        tools, self.round_tools = self.round_tools, 0
        flagged_done, self.auto_done_flag = self.auto_done_flag, False
        if (not config.AUTOPILOT or info.get("interrupted") or info.get("error") or self.pending_confirm
                or flagged_done):
            self.auto = None
            return False
        if not tools or said.endswith("?"):      # he didn't act this round, or he's asking you something
            self.auto = None
            return False
        a = self.auto
        goal = a["goal"] if a else user_text
        if not goal or goal.startswith("("):
            return False
        finished = await self._is_finished(goal, said)
        if finished is None:
            # Nobody to ask: he keeps going only if he declared it, or worked and then said nothing.
            finished = not a and bool(said)
        if finished:
            self.auto = None
            return False
        if a is None:
            a = self.auto = {"goal": goal, "rounds": 0, "t0": time.time()}
        if a["rounds"] >= config.AUTOPILOT_MAX_ROUNDS or time.time() - a["t0"] > config.AUTOPILOT_MAX_MIN * 60:
            self.auto = None
            self.say("I've been at this a while, so I'll pause here. Tell me to keep going if you want more.")
            return False
        a["rounds"] += 1
        log.info("carrying on by himself (round %d, %d tools last round): %s", a["rounds"], tools, a["goal"][:80])
        prompt = (f"[Auto-continue {a['rounds']}/{config.AUTOPILOT_MAX_ROUNDS}] Continue the user's task: "
                  f"\"{a['goal'][:300]}\". Check the current state with your tools, do the next step, and keep "
                  "going. If you're waiting on something outside your control, run `sleep 8` before you look again. "
                  "Say nothing unless there is a result, a problem, or you need them. When it's finished or "
                  "you can't go on, call task_complete and say one short sentence.")
        self.turn_user, self.turn_reply = f"(auto-continue {a['rounds']}) {a['goal'][:60]}", []
        self.turn_no += 1
        self.acked, self.retried, self.turn_prompt = True, False, prompt
        await self.set_state("thinking")
        try:
            await (self.backup if self.active is self.backup else self.brain).send(prompt)
        except Exception:
            log.exception("auto-continue failed")
            self.auto = None
            return False
        return True

    async def on_permission(self, tool: str, inp: dict) -> bool | str:
        # AppleScript aimed at an app Mike isn't allowed to script yet would pop a macOS dialog and
        # hang until someone clicks it. Refuse it up front and point Claude at the native tools.
        if tool == "Bash" and "osascript" in str(inp.get("command", "")):
            blocked = await self.unscriptable_apps(str(inp["command"]))
            if blocked:
                log.info("blocked AppleScript for %s (no permission yet)", blocked)
                return (f"Not run: macOS hasn't allowed Mike to script {', '.join(blocked)} yet, and asking would pop "
                        "up a dialog. Do it with the native tools instead (ui, press, set_text, menu, type_text, "
                        "press_key, media), opening the app with `open -a` first if needed. Only if it truly can't be "
                        "done that way, tell the user in one sentence that they can allow it once while at the Mac.")
        # Just do the work. Only genuinely sensitive actions need a yes.
        if not is_sensitive(tool, inp):
            return True
        summary = describe_action(tool, inp)
        cid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.pending_confirm = {"id": cid, "future": fut}
        await self.send({"type": "confirm", "id": cid, "tool": tool, "summary": summary,
                         "detail": json.dumps(inp, indent=1)[:1500]})
        self.say(f"Quick check. Should I {summary}?")
        await self.set_state("confirming")
        try:
            ok = await asyncio.wait_for(fut, 90)
        except asyncio.TimeoutError:
            ok = False
            self.say("No answer, so I'll skip that.")
        self.pending_confirm = None
        await self.send({"type": "confirm_done", "id": cid, "allowed": ok})
        await self.set_state("thinking" if self.brain.busy else "idle")
        return ok

    async def unscriptable_apps(self, command: str) -> list[str]:
        names = set(re.findall(r'(?:tell|of)\s+(?:application|app)\s+(?:id\s+)?"([^"]+)"', command, re.I))
        names |= set(re.findall(r"(?:tell|of)\s+(?:application|app)\s+(?:id\s+)?'([^']+)'", command, re.I))
        names |= set(re.findall(r'Application\(["\']([^"\']+)["\']\)', command))   # JXA
        blocked = []
        for name in names:
            if self.script_ok.get(name.lower()):
                continue
            st = (await self.native("automation", {"app": name}, timeout=3)).get("status")
            if st == "granted":
                self.script_ok[name.lower()] = True
            elif st in ("ask", "denied", "not_running"):
                blocked.append(name)
        return blocked

    # ---- ears callbacks --------------------------------------------------
    async def on_level(self, level: float, hearing: bool):
        if hearing != self.hearing:
            self.hearing = hearing
            await self.send({"type": "hearing", "value": hearing})
            # Talking over Mike: if it's loud enough to be you (not his echo or the room),
            # drop his volume right away, like a person pausing when interrupted.
            if hearing and self.state == "speaking" and config.BARGE_IN:
                import math
                if self.ears.is_near(20 * math.log10(max(level / 6, 1e-6))):
                    if config.VOICE_MATCH and self.voice_id.ready:
                        asyncio.create_task(self._duck_if_you(self.gen))   # noise must not dip his volume
                    else:
                        await self.duck(True)
            elif not hearing and self.ducked:
                async def unduck_later(gen=self.gen):
                    await asyncio.sleep(3)
                    if self.ducked and gen == self.gen and not self.hearing:
                        await self.duck(False)
                asyncio.create_task(unduck_later())
            if self.state == "listening":
                if hearing:
                    # Someone started talking inside the window: hold it open until they finish.
                    self.deadline_before = self.followup_until
                    self.followup_until = max(self.followup_until, time.time() + 60)
                else:
                    # Give transcription a moment. Never cut the original window short: a cough
                    # or room noise right after "Hey Mike" must not end your chance to speak.
                    self.followup_until = max(getattr(self, "deadline_before", 0.0), time.time() + 4)
                    asyncio.create_task(self._expire_followup(self.followup_until))

    def voice_prefilter(self, pcm: bytes) -> bool:
        return (not config.VOICE_MATCH) or self.voice_id.maybe_user(pcm)

    async def _duck_if_you(self, gen: int):
        """Lower Mike's volume only once the first moments of speech sound like you."""
        await asyncio.sleep(0.7)
        if gen != self.gen or not self.hearing or self.state != "speaking":
            return
        pcm = b"".join(self.ears.speech)
        ok = await asyncio.get_running_loop().run_in_executor(None, self.voice_id.maybe_user, pcm)
        if ok and gen == self.gen and self.hearing and self.state == "speaking":
            await self.duck(True)

    async def on_heard(self, text: str):
        await self.on_utterance(text, mic=True)

    async def is_you(self) -> bool:
        """Does the clip just heard sound like you (not a TV, video or someone else)?"""
        if not config.VOICE_MATCH:
            return True
        ok, score = await asyncio.get_running_loop().run_in_executor(None, self.voice_id.is_user, self.ears.last_pcm)
        self.voice_score = score
        if score is not None:
            log.info("voice match %.2f vs %.2f: %s", score, self.voice_id.threshold(), "you" if ok else "not you")
            if score >= 0.8:      # clearly you: keep the voice print fresh (new mic, a cold, a different room)
                asyncio.get_running_loop().run_in_executor(None, self.voice_id.enroll, self.ears.last_pcm)
        return ok

    async def on_utterance(self, text: str, mic: bool = False):
        log.info("heard: %s", text)
        self.t_heard = time.time()      # for the "heard → first sound" timing in speech_worker
        self.heard_alt = (text, getattr(self.ears, "alt", "") if mic else "")
        self.voice_score = None
        woke, command = split_wake(text)
        if mic and woke and self.ears.is_near(self.ears.last_db):
            # You called him by name, close to the mic: learn your voice from it.
            asyncio.get_running_loop().run_in_executor(None, self.voice_id.enroll, self.ears.last_pcm)
        # Without the name, speech from the mic has to be in your voice (a video can't chat with Mike).
        if mic and not woke and (self.pending_confirm or self.state in ("speaking", "listening") or self.ducked):
            if not await self.is_you():
                log.info("ignored (not your voice): %s", text)
                if self.ducked:
                    await self.duck(False)
                return

        # Answering a yes/no confirmation doesn't need the wake word.
        if self.pending_confirm:
            await self.send({"type": "transcript", "text": text})
            body = command if woke else text
            if NO.search(body):
                self.pending_confirm["future"].set_result(False)
                self.say("Okay, I won't.")
            elif YES.search(body):
                self.pending_confirm["future"].set_result(True)
                self.say("On it.")
            else:
                self.say("Sorry, was that a yes or a no?")
            return

        # Barge-in: you spoke while Mike was talking (no name needed).
        if not woke and config.BARGE_IN and (self.state == "speaking" or self.ducked):
            clearly_you = (self.voice_score is not None and config.VOICE_MATCH
                           and self.voice_score >= self.voice_id.threshold() + 0.05)
            if not clearly_you and (self.is_echo(text) or not self.ears.is_near(self.ears.last_db)):
                log.info("ignored while speaking (%s): %s", "echo" if self.is_echo(text) else "quiet", text)
                await self.duck(False)
                return
            await self.send({"type": "transcript", "text": text})
            await self.duck(False)
            await self.interrupt()
            if STOP.match(text):
                return
            log.info("barge-in: %s", text)
            await self.handle_command(text, allow_quit=False)
            return

        if self.auto and not woke and STOP.match(text) and ((not mic) or await self.is_you()):
            await self.interrupt()
            self.say("Okay, stopping.")
            return

        in_followup = self.state == "listening" and time.time() < self.followup_until + 1.5
        if woke and not config.WAKE_WORD and not in_followup and self.state != "speaking":
            return   # wake word turned off in Settings: only ⌥Space, the pet or typing start Mike
        if not woke and not in_followup:
            return  # background chatter
        if not woke and not self.ears.is_near(self.ears.last_db):
            # Loudness is only a guess at "is this you". A clear voice match beats it, so a quiet
            # or turned-away follow-up in your voice isn't thrown out (it was: 0.83 match, dropped).
            clearly_you = self.voice_score is not None and self.voice_score >= self.voice_id.threshold() + 0.05
            if not clearly_you:
                log.info("ignored as background (too quiet to be you): %s", text)
                return

        if getattr(self, "pet_dismissed", False):
            await self.dismiss_pet(False)      # called again: he comes back
        await self.send({"type": "transcript", "text": text})
        if not woke:
            command = text

        # Just the name while Mike is working: keep working (it's usually someone saying the
        # name in passing). A real command, or "stop", replaces the current task.
        if not command and self.state == "thinking":
            log.info("name heard while working; carrying on")
            return
        if self.state in ("thinking", "speaking"):
            await self.interrupt()
        if not command:
            await self.send({"type": "chime"})
            self.followup_until = time.time() + config.WAKE_LISTEN_SECONDS
            await self.set_state("listening")
            asyncio.create_task(self._expire_followup(self.followup_until))
            return
        # Quitting by voice needs the name ("Mike, quit"), so "bye" from the TV can't shut Mike down.
        await self.handle_command(command, allow_quit=woke)

    def use_backup(self) -> bool:
        if config.BRAIN_MODE == "free":
            return True
        return (config.BRAIN_MODE == "auto" and time.time() < self.claude_blocked_until
                and bool(self.backup.available()))

    async def try_local(self, command: str) -> bool:
        """Handle a plain device command without Claude. False means: not one of those, ask the brain."""
        text = split_wake(command)[1] or command if split_wake(command)[0] else command
        reply = None
        if LOCAL_TIME.match(text):
            reply = f"It's {time.strftime('%-I:%M %p')}."
        elif LOCAL_DATE.match(text):
            reply = f"It's {time.strftime('%A, %-d %B')}."
        else:
            for rx, action, times, said in LOCAL_MEDIA:
                if rx.match(text):
                    res = await self.native("media", {"action": action, "times": times}, timeout=5)
                    if res.get("error"):
                        return False          # couldn't do it here: let the brain try
                    reply = said
                    break
        if reply is None:
            return False
        log.info("handled locally (no Claude): %s", text)
        await self.send({"type": "user", "text": command})
        await self.send({"type": "assistant_done", "cost": 0})
        self.followup_until = 0
        self.offer_followup = True
        self.say(reply)
        return True

    INTENT_SYSTEM = (
        "You listen for short spoken commands to a desktop assistant called Mike and decide whether the person is talking about "
        "Mike himself. Answer with exactly one word.\n"
        "HIDE: they want Mike to go away, be quiet, stop, stop talking, get out of the way, or be dismissed (close, dismiss, "
        "go away, enough, that's all, never mind, shut up, hide, leave me alone, stop it).\n"
        "QUIT: they want the whole Mike app to exit completely (quit, exit, shut down, turn Mike off).\n"
        "MUTE: they want the microphone to stop listening (stop listening, mute, don't listen).\n"
        "NONE: anything else: questions, tasks, chat, or a command about something other than Mike himself "
        "(close the window, stop the music, stop the timer, play something, what's the time).\n"
        "A command with no object ( just 'close', 'stop', 'quiet', 'enough') is aimed at Mike himself, so it is HIDE, whatever his state; "
        "the same word with a thing named (close the window, stop the music) is NONE.")

    async def understand(self, command: str) -> str | None:
        """What a short command means for Mike himself (HIDE / QUIT / MUTE / NONE), judged by a quick free model. None if unsure."""
        if len(command.split()) > 7 or not self.backup.available():
            return None
        try:
            out = await asyncio.wait_for(self.backup.ask(
                self.INTENT_SYSTEM, f"Mike is currently {self.state}. They said: {command[:120]}", 200, small=True), 4)
        except Exception:
            return None
        word = re.sub(r"[^A-Z]", "", (out or "").strip().split()[0].upper()) if (out or "").strip() else ""
        return word if word in ("HIDE", "QUIT", "MUTE", "NONE") else None

    async def dismiss_pet(self, on: bool):
        """Send the pet (and Mike's window) away, or bring them back. He keeps listening either way."""
        if on == getattr(self, "pet_dismissed", False):
            return
        self.pet_dismissed = on
        await self.send_pets({"type": "pet_dismiss", "on": on})
        if on:
            await self.send({"type": "hide"})

    async def handle_command(self, command: str, allow_quit: bool = True):
        if STOP.match(command):
            await self.interrupt()
            return
        if MUTE.match(command) or MUTE.match(split_wake(command)[1] or "-"):
            await self.interrupt()
            self.say("Okay, I'm not listening. Press Option Shift M when you need me.")
            for _ in range(60):                       # finish speaking, then let go of the mic
                await asyncio.sleep(0.1)
                if self.speech_q.empty() and not self.synthing and self.state != "speaking":
                    break
            log.info("mic muted on voice command")
            await self.send({"type": "mic_off"})
            return
        if allow_quit and (QUIT.match(command) or QUIT.match(split_wake(command)[1] or "-")):   # typed "Mike, quit" too
            await self.interrupt()
            self.say("Bye, see you later.")
            for _ in range(60):                       # let the goodbye finish, then quit
                await asyncio.sleep(0.1)
                if self.speech_q.empty() and not self.synthing and self.state != "speaking":
                    break
            await asyncio.sleep(0.4)
            log.info("quitting on voice command")
            await self.send({"type": "quit"})
            return
        if RESET.search(command) and len(command.split()) <= 6:
            await self.interrupt()
            await self.brain.reset()
            self.backup.reset()
            await self.send({"type": "reset"})
            self.say("Fresh start. What's next?")
            return
        m = MODEL_RE.search(command)
        if m and len(command.split()) <= 9:
            name = m.group(2).lower()
            config.MODEL = MODEL_IDS.get(name, name)     # exact model, not whatever the alias means today
            save_setting(model=config.MODEL)
            await self.interrupt()
            await self.brain.stop()          # restarts on the next request, same conversation
            await self.send({"type": "log", "text": f"brain online · model claude-{name}"})
            self.offer_followup = True
            self.say(f"Switched to {name.capitalize()}.")
            return
        if await self.try_local(command):
            return
        intent = await self.understand(command)
        if intent:
            log.info("understood %r as %s", command[:60], intent)
        if intent == "HIDE":
            await self.interrupt()
            await self.dismiss_pet(True)
            await self.set_state("idle")
            return
        if intent == "MUTE":
            await self.handle_command("stop listening")
            return
        if intent == "QUIT" and allow_quit:
            await self.handle_command("quit")
            return
        self.auto, self.round_tools, self.auto_done_flag = None, 0, False
        self.followup_until = 0
        await self.set_state("thinking")
        await self.send({"type": "user", "text": command})
        # After a break, start a fresh conversation: small context = fast and cheap. Memory
        # (facts + latest exchanges) is in the system prompt, so nothing important is lost.
        now = time.time()
        too_long = bool(getattr(config, "MAX_SESSION_TURNS", 0)) and self.session_turns >= config.MAX_SESSION_TURNS
        if (self.brain.session_id and not self.brain.busy
                and ((config.FRESH_AFTER_MIN and now - self.last_command_at > config.FRESH_AFTER_MIN * 60) or too_long)):
            log.info("fresh conversation after %s", "a long chat" if too_long else "a break")
            await self.brain.reset()
            self.backup.reset()
            self.session_turns = 0
        self.session_turns += 1
        self.last_command_at = now
        self.turn_user, self.turn_reply = command, []
        self.turn_no += 1
        self.acked = False
        self.retried = False
        self.turn_prompt = await self.with_context(command)
        try:
            if self.use_backup():
                self.active = self.backup
                await self.backup.send(self.turn_prompt)
                return
            self.active = self.brain
            await self.brain.send(self.turn_prompt)
        except Exception as e:
            log.exception("brain send failed")
            self.say("I couldn't reach Claude Code.")
            await self.on_log(f"brain error: {e}")
            await self.set_state("idle")

    # ---- presence --------------------------------------------------------
    async def on_presence(self, present: bool):
        now = time.time()
        was = self.present
        self.present = present
        if present != was:
            await self.send_pets({"type": "presence", "present": present})
        if present and was is False:
            away_min = (now - self.last_seen) / 60
            if config.WELCOME_BACK and away_min >= config.GREET_AFTER_AWAY_MIN and self.state == "idle":
                line = self.greeting(away_min)
                if (config.MORNING_BRIEFING and 4 <= time.localtime().tm_hour < 12
                        and time.localtime(self.last_seen).tm_yday != time.localtime().tm_yday):
                    w = await self.weather_line()
                    today = [r for r in reminders.load()
                             if time.localtime(r["due"]).tm_yday == time.localtime().tm_yday]
                    extra = f" You've got {len(today)} thing{'s' if len(today) != 1 else ''} lined up today." if len(today) > 1 else ""
                    line = " ".join(x for x in [line, w, extra] if x)
                self.say(line)
        if present:
            self.last_seen = now

    # ---- noticing you without a camera ------------------------------------
    @staticmethod
    async def idle_seconds() -> float:
        """Seconds since the last keyboard/mouse input (macOS HID idle time)."""
        try:
            proc = await asyncio.create_subprocess_exec("ioreg", "-c", "IOHIDSystem", stdout=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            m = re.search(rb'"HIDIdleTime" = (\d+)', out)
            return int(m.group(1)) / 1e9 if m else 0.0
        except Exception:
            return 0.0

    async def activity_loop(self):
        busy_since = time.time()
        nudged = False
        while True:
            await asyncio.sleep(5)
            idle = await self.idle_seconds()
            if idle < 60:
                if self.present is False or (self.present is None and idle < 10):
                    await self.on_presence(True)
                if idle < 10:
                    self.last_seen = time.time()
            elif idle > 600 and self.present:
                await self.on_presence(False)
            if idle > 300:
                busy_since, nudged = time.time(), False      # a real break resets the clock
            mins = (time.time() - busy_since) / 60
            if (config.BREAK_NUDGE_MIN and mins >= config.BREAK_NUDGE_MIN and not nudged
                    and self.state == "idle" and idle < 30):
                nudged = True
                hrs = round(mins / 60)
                self.say(random.choice([
                    f"Hey, you've been going for {'about ' + str(hrs) + ' hours' if hrs > 1 else 'a while'} straight. Quick stretch and some water?",
                    "You've been locked in for ages. Take five, your brain will thank you.",
                ]))

    async def memory_loop(self):
        """Every so often, once a conversation has wound down, save what's worth remembering."""
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(60)
            quiet_for = time.time() - self.last_command_at
            # After a long break, swap in a fresh, warm conversation now rather than on your next request.
            if (config.FRESH_AFTER_MIN and self.brain.session_id and not self.brain.busy
                    and quiet_for > config.FRESH_AFTER_MIN * 60 and self.state == "idle"):
                log.info("fresh conversation (pre-warmed after a break)")
                await self.brain.reset()
                self.backup.reset()
                await self.brain.start()
            if (not config.MEMORY_AUTO or self.state != "idle" or self.brain.busy
                    or quiet_for < config.MEMORY_AFTER_QUIET_MIN * 60):
                continue
            try:
                n = await loop.run_in_executor(None, memory.pending_count)
                if n < 4:
                    continue
                out = await loop.run_in_executor(None, memory.consolidate)
                log.info("memory consolidated from %d exchanges: %s", n, out)
                if out.get("add") or out.get("update"):
                    await self.on_log(f"remembered: {'; '.join(out.get('add', []) + out.get('update', []))[:300]}")
            except Exception:
                log.exception("memory consolidation failed")

    async def weather_line(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec("curl", "-s", "-m", "2", "wttr.in/?format=%C+%t",
                                                        stdout=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
            w = " ".join(out.decode().split())
            m = re.match(r"(.+?)\s+\+?(-?\d+)°C", w)
            if m and "unknown" not in w.lower():
                return f"It's {m.group(1).lower()} and {m.group(2)} degrees out."
        except Exception:
            pass
        return ""

    def greeting(self, away_min: float) -> str:
        """A short, human welcome that fits the time of day and what's coming up."""
        hour = time.localtime().tm_hour
        first_today = time.localtime(self.last_seen).tm_yday != time.localtime().tm_yday
        hello = greetings.hello() if (first_today or hour >= 23 or hour < 4) else greetings.welcome_back()
        soon = [r for r in reminders.load() if r["due"] - time.time() < 3 * 3600]
        if soon:
            r = soon[0]
            when = time.strftime("%-I:%M", time.localtime(r["due"]))
            return f"{hello} Heads up, {r['text']} at {when}."
        return hello

    # ---- settings & data for the app's Settings / Memory / Reminders / History views ----
    async def apply_setting(self, key: str, value):
        if key not in SETTINGS_SPEC:
            return
        attr, typ, restart = SETTINGS_SPEC[key]
        try:
            value = typ(value)
        except (TypeError, ValueError):
            return
        if getattr(config, attr) == value:
            return
        setattr(config, attr, value)
        save_setting(**{key: value})
        log.info("setting %s = %r", key, value)
        if restart:
            await self.brain.stop()          # restarts on the next request, same conversation
        await self.send({"type": "settings", "values": current_settings()})

    def settings_payload(self) -> dict:
        return {"type": "settings", "values": current_settings(), "models": MODEL_CHOICES,
                "voices": voice.voices(), "voice": voice.current_voice(), "pet": pet_settings(),
                "claude_bin": config.CLAUDE_BIN, "data_dir": str(config.DATA)}

    @staticmethod
    def memory_payload() -> dict:
        days = sorted(memory.JOURNAL_DIR.glob("*.jsonl")) if memory.JOURNAL_DIR.exists() else []
        return {"type": "memory", "facts": memory.facts(), "journal_days": len(days),
                "file": str(memory.MEMORY_FILE), "claude_notes": len(list(memory.CLAUDE_PROJECTS.glob("*/memory/*.md")))}

    @staticmethod
    def reminders_payload() -> dict:
        return {"type": "reminders", "items": reminders.load()}

    @staticmethod
    def history_payload(day: str = "", q: str = "") -> dict:
        days = []
        for date, entries in memory._entries(days=365):
            talk = [e for e in entries if not e.get("user", "").startswith("(")]
            if talk:
                days.append({"date": date, "count": len(talk), "first": talk[0]["user"][:80]})
        if q:
            entries = memory.search(q, limit=40)
        else:
            day = day or (days[0]["date"] if days else "")
            entries = []
            for date, es in memory._entries(days=365):
                if date == day:
                    entries = [e for e in es if not e.get("user", "").startswith("(")]
        return {"type": "history", "days": days, "day": day, "q": q, "entries": entries}

    # ---- reminders, timers, alarms ----------------------------------------
    async def reminder_loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                due = reminders.pop_due()
            except Exception:
                log.exception("reminder check failed")
                continue
            for r in due:
                await self.fire_reminder(r)

    async def fire_reminder(self, r: dict):
        text = r["text"].strip().rstrip(".")
        label = re.sub(r"\b(timer|is up|is done|done|reminder)\b", "", text, flags=re.I).strip(" ,:-").lower()
        short = 0 < len(label.split()) <= 3
        if r["kind"] == "timer":
            line = (random.choice([f"Hey, your {label} timer's done.", f"Time's up on the {label} timer.",
                                   f"Ding, {label} timer's done."]) if short
                    else f"Hey, time's up. {text}." if label else "Hey, your timer's done.")
        elif r["kind"] == "alarm":
            line = random.choice([f"Hey, it's time. {text}.", f"Alarm: {text}. Up you get."]) if text else "Hey, it's time."
        else:
            line = random.choice([f"Hey, quick reminder: {text}.", f"Don't forget, {text}.",
                                  f"Yo, you asked me to remind you: {text}."])
        log.info("reminder due: %s", r)
        try:
            subprocess.Popen(["osascript", "-e", f'display notification {json.dumps(r["text"] or "Time")} '
                              f'with title "Mike" subtitle {json.dumps(r["kind"].capitalize())} sound name "Glass"'])
        except Exception:
            pass
        await self.send({"type": "summon"})
        if self.state in ("idle", "listening"):
            self.offer_followup = True        # so "snooze it" / "thanks" works without the name
        self.say(line)
        try:
            memory.log_turn(f"({r['kind']} went off: {r['text']})", line)
        except Exception:
            pass

    # ---- screen (captured natively by Mike.app) ---------------------------
    async def screenshot(self) -> bytes | None:
        if not self.ws:
            return None
        sid = "s" + uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.snapshots[sid] = fut
        await self.send({"type": "screenshot_request", "id": sid})
        try:
            return await asyncio.wait_for(fut, 10)
        except asyncio.TimeoutError:
            return None
        finally:
            self.snapshots.pop(sid, None)

    # ---- computer control (Mike.app does it natively) ----------------------
    async def native(self, op: str, args: dict | None = None, timeout: float = 20) -> dict:
        if not self.ws:
            return {"error": "Mike's window isn't connected."}
        nid = "n" + uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.natives[nid] = fut
        await self.send({"type": "native_request", "id": nid, "op": op, "args": args or {}})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"error": "Mike.app didn't respond in time."}
        finally:
            self.natives.pop(nid, None)

    async def with_context(self, command: str) -> str:
        """Attach what the user is doing right now (time, front app, window, selected text)."""
        bits = [time.strftime("%a %d %b, %-I:%M %p")]
        try:
            ctx = await self.native("context", timeout=0.6)
        except Exception:
            ctx = {}
        if ctx.get("app"):
            bits.append(f"front app: {ctx['app']}")
        if ctx.get("window"):
            bits.append(f"window: {ctx['window']}")
        note = "[context: " + " | ".join(bits) + "]"
        said, alt = getattr(self, "heard_alt", ("", ""))
        if alt and command.strip() and command.strip() in said:
            note += (f"\n[speech recognition was unsure of this request; a second listen heard: \"{alt}\". "
                     "Use whichever makes sense as an everyday request.]")
        if ctx.get("selected"):
            note += f"\n[selected text in that app: {ctx['selected']}]"
        return f"{note}\n{command}"

    # ---- camera ----------------------------------------------------------
    async def snapshot(self) -> bytes | None:
        if not self.ws:
            return None
        sid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.snapshots[sid] = fut
        await self.send({"type": "snapshot_request", "id": sid})
        try:
            return await asyncio.wait_for(fut, 6)
        except asyncio.TimeoutError:
            return None
        finally:
            self.snapshots.pop(sid, None)


# Shell commands that delete, send, publish, install, or touch secrets/system settings.
SENSITIVE_CMD = re.compile(
    r"(^|[\s;&|(`])(sudo|rm|rmdir|unlink|shred|trash|dd|mkfs|diskutil|shutdown|reboot|halt|killall|pkill|kill"
    r"|launchctl|defaults\s+(write|delete)|security|chmod|chown|csrutil|spctl|tccutil|crontab|ssh|scp|rsync"
    r"|brew\s+(install|uninstall|upgrade)|pip3?\s+(install|uninstall)|npm\s+(i|install|uninstall|publish)"
    r"|git\s+(push|reset\s+--hard|clean|rebase|branch\s+-D)|gh\s+(pr|release|repo)\s+(create|merge|delete))(\s|$)"
    r"|\|\s*(ba|z)?sh\b|>\s*/(etc|System|usr|Library)\b|\.ssh\b|\.env\b|keychain|password|passwd|token|api[_-]?key"
    r"|osascript[^\n]*(Messages|Mail|WhatsApp|\bsend\b|\bdelete\b|empty trash)|mailto:|\bmv\b[^\n]*\s/(dev/null|Volumes)",
    re.I)
SENSITIVE_PATH = re.compile(r"/\.ssh/|/\.aws/|/\.gnupg/|\.env(\.|$)|/\.zshrc$|/\.zprofile$|/\.bash_profile$|/\.gitconfig$"
                            r"|/\.claude/|^/etc/|^/System/|^/usr/|^/Library/|/Library/(Keychains|LaunchAgents|Preferences)/", re.I)


def is_sensitive(tool: str, inp: dict) -> bool:
    if tool == "Bash":
        return bool(SENSITIVE_CMD.search(str(inp.get("command", ""))))
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = os.path.expanduser(str(inp.get("file_path") or inp.get("notebook_path") or ""))
        return bool(SENSITIVE_PATH.search(path))
    return False


def describe_action(tool: str, inp: dict) -> str:
    def base(p):
        return os.path.basename(str(p or "")) or "a file"
    if tool == "Bash":
        desc = (inp.get("description") or "").strip().rstrip(".")
        if desc:
            return desc[0].lower() + desc[1:]
        return f"run the command {str(inp.get('command', ''))[:80]}"
    if tool == "Write":
        return f"write the file {base(inp.get('file_path'))}"
    if tool in ("Edit", "MultiEdit"):
        return f"edit the file {base(inp.get('file_path'))}"
    if tool == "NotebookEdit":
        return f"edit the notebook {base(inp.get('notebook_path'))}"
    pretty = tool.replace("mcp__", "").replace("__", " ").replace("_", " ")
    return f"use {pretty}"


apply_saved_settings()   # model, voice speed, behaviour toggles you picked in Settings or by voice
mike = Mike()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA.mkdir(exist_ok=True)
    loop = asyncio.get_running_loop()
    await asyncio.gather(loop.run_in_executor(None, mike.ears.load),
                         loop.run_in_executor(None, voice.load))
    tasks = [asyncio.create_task(mike.ears.run()), asyncio.create_task(mike.speech_worker()),
             asyncio.create_task(mike.reminder_loop()), asyncio.create_task(mike.activity_loop()),
             asyncio.create_task(mike.memory_loop())]
    if mike.backup.available():
        tasks.append(asyncio.create_task(mike.backup.warm()))   # first backup answer doesn't wait on model checks
    # Pick up where the last exchange left off. If that was long ago, start the brain on a fresh
    # conversation right away, so the first request doesn't pay for a restart.
    try:
        last = memory.recent(1, days=60)
        mike.last_command_at = time.mktime(time.strptime(last[0]["t"], "%Y-%m-%dT%H:%M:%S")) if last else 0.0
    except Exception:
        mike.last_command_at = 0.0
    if (config.FRESH_AFTER_MIN and mike.brain.session_id
            and time.time() - mike.last_command_at > config.FRESH_AFTER_MIN * 60):
        mike.brain.session_id = None
        mike.brain._save_session()
    await mike.brain.start()
    mike.state = "idle"
    log.info("Mike is ready at http://%s:%s", config.HOST, config.PORT)
    yield
    for t in tasks:
        t.cancel()
    await mike.brain.stop()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/api/snapshot")
async def api_snapshot():
    jpg = await mike.snapshot()
    if not jpg:
        return PlainTextResponse("The HUD isn't open or the camera is off.", status_code=503)
    return Response(jpg, media_type="image/jpeg")


@app.get("/api/screenshot")
async def api_screenshot():
    jpg = await mike.screenshot()
    if not jpg:
        return PlainTextResponse("no screen access", status_code=503)
    return Response(jpg, media_type="image/jpeg")


@app.post("/api/reminders")
async def api_add_reminder(request: Request):
    b = await request.json()
    try:
        due = reminders.parse_when(b.get("in_minutes"), str(b.get("at") or ""))
    except (ValueError, TypeError) as e:
        return {"error": str(e)}
    r = reminders.add(str(b.get("text") or ""), due, str(b.get("repeat") or ""), str(b.get("kind") or "reminder"))
    return {"ok": True, "reminder": reminders.describe(r)}


@app.get("/api/reminders")
async def api_list_reminders():
    return {"reminders": [reminders.describe(r) for r in reminders.load()]}


@app.post("/api/reminders/cancel")
async def api_cancel_reminder(request: Request):
    b = await request.json()
    gone = reminders.cancel(str(b.get("what") or ""))
    return {"cancelled": [reminders.describe(r) for r in gone]}


@app.post("/api/show")
async def api_show():
    """Bring Mike's window to the front (used by the menu bar, scripts and tests)."""
    await mike.send({"type": "show"})
    return {"ok": True}


@app.post("/api/dismiss")
async def api_dismiss():
    """The user sent Mike away (close, go away, be quiet): hide the pet until he is called again."""
    await mike.interrupt()
    await mike.dismiss_pet(True)
    return {"ok": True}


@app.post("/api/hide")
async def api_hide():
    """Put Mike's window away (he keeps listening through the pet)."""
    await mike.send({"type": "hide"})
    return {"ok": True}


@app.post("/api/auto")
async def api_auto(request: Request):
    b = await request.json()
    if b.get("on"):
        if mike.auto is None and config.AUTOPILOT:
            goal = mike.turn_user if mike.turn_user and not mike.turn_user.startswith("(") else "the current task"
            mike.auto = {"goal": goal, "rounds": 0, "t0": time.time(), "idle": 0}
    else:
        mike.auto = None
        mike.auto_done_flag = True
    return {"ok": True}


@app.post("/api/native")
async def api_native(request: Request):
    body = await request.json()
    return await mike.native(str(body.get("op", "")), body.get("args") or {})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if mike.ws:
        try:
            # 4000 = replaced by a newer window; that page won't auto-reconnect.
            await mike.ws.close(code=4000, reason="Mike was opened in another window")
        except Exception:
            pass
    mike.ws = ws
    await mike.send({"type": "hello", "name": config.NAME, "state": mike.state,
                     "usage": mike.usage, "voices": voice.voices(), "voice": voice.current_voice(),
                     "pet": pet_settings()})
    # A short, different hello on launch (skipped if Mike just restarted).
    if not mike.launched_hello and not greetings.recently_greeted(10):
        mike.say(greetings.hello())
    mike.launched_hello = True
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await mike.ears.feed(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            t = data.get("type")
            if t == "text":
                text = data.get("text", "").strip()
                if not text:
                    continue
                if mike.pending_confirm:
                    await mike.on_utterance(text)
                else:
                    if mike.state in ("thinking", "speaking"):
                        await mike.interrupt()
                    await mike.handle_command(text)
            elif t == "drained":
                await mike.on_drained(int(data.get("gen", -1)))
            elif t == "approve" and mike.pending_confirm and mike.pending_confirm["id"] == data.get("id"):
                if not mike.pending_confirm["future"].done():
                    mike.pending_confirm["future"].set_result(bool(data.get("allow")))
            elif t == "interrupt":
                await mike.interrupt()
            elif t == "wake":
                await mike.on_utterance(config.NAME)
            elif t == "presence":
                await mike.on_presence(bool(data.get("present")))
            elif t in ("snapshot", "screenshot"):
                fut = mike.snapshots.get(data.get("id"))
                if fut and not fut.done():
                    raw = data.get("data")
                    fut.set_result(base64.b64decode(raw) if raw else None)
            elif t == "settings_get":
                await mike.send(mike.settings_payload())
            elif t == "set":
                await mike.apply_setting(str(data.get("key")), data.get("value"))
            elif t == "voice_reset":
                mike.voice_id.reset()
                log.info("voice print cleared; relearning from the next wake-ups")
            elif t == "memory_get":
                await mike.send(mike.memory_payload())
            elif t == "memory_add" and str(data.get("text", "")).strip():
                memory.remember(str(data["text"]))
                await mike.send(mike.memory_payload())
            elif t == "memory_delete":
                items = memory.facts()
                memory._write_facts([f for f in items if f != data.get("text")])
                await mike.send(mike.memory_payload())
            elif t == "reminders_get":
                await mike.send(mike.reminders_payload())
            elif t == "reminder_cancel":
                reminders.cancel(str(data.get("id", "")) or "-")
                await mike.send(mike.reminders_payload())
            elif t == "history_get":
                await mike.send(mike.history_payload(str(data.get("day") or ""), str(data.get("q") or "")))
            elif t == "backup_get":
                await mike.send({"type": "backup", "providers": await mike.backup.status(),
                                 "blocked_until": mike.claude_blocked_until})
            elif t == "backup_key" and data.get("provider") in {p["id"] for p in PROVIDERS}:
                save_key(str(data["provider"]), str(data.get("key") or ""))
                mike.backup.forget_models()
                await mike.send({"type": "backup", "providers": await mike.backup.status(),
                                 "blocked_until": mike.claude_blocked_until})
            elif t == "voice_preview":
                await mike.interrupt()
                mike.say(random.choice(["Hey, this is how I sound.", "Yo, it's Mike. This is my voice.",
                                        "Hi! This is me. Sound good?"]))
            elif t == "native":
                fut = mike.natives.get(data.get("id"))
                if fut and not fut.done():
                    fut.set_result(data.get("result") or {})
            elif t == "set_pet":
                await mike.set_pet(data)
            elif t == "set_voice":
                if voice.set_voice(str(data.get("voice", ""))):
                    await mike.send({"type": "voice", "voice": voice.current_voice()})
                    if mike.state in ("idle", "listening"):
                        await mike.interrupt()
                        mike.say(f"Hi, I'm {config.NAME}. This is how I'll sound from now on.")
            elif t == "reset":
                await mike.interrupt()
                await mike.brain.reset()
                mike.backup.reset()
                await mike.send({"type": "reset"})
    except WebSocketDisconnect:
        pass
    finally:
        if mike.ws is ws:
            mike.ws = None


@app.websocket("/ws/pet")
async def ws_pet(ws: WebSocket):
    """The desktop pet: mirrors Mike's state and can wake, stop, or answer yes/no."""
    await ws.accept()
    mike.pets.add(ws)
    await ws.send_text(json.dumps({
        "type": "hello", "name": config.NAME, "state": mike.state, "present": mike.present,
        "main": mike.ws is not None, **pet_settings(),
        "confirm": None if not mike.pending_confirm else {"id": mike.pending_confirm["id"]},
    }))
    try:
        while True:
            data = json.loads(await ws.receive_text() or "{}")
            t = data.get("type")
            if t == "wake":
                if mike.state in ("thinking", "speaking"):
                    await mike.interrupt()
                elif mike.ws is None:
                    await ws.send_text(json.dumps({"type": "speak", "text": "Open my main window so I can hear you."}))
                else:
                    await mike.on_utterance(config.NAME)  # same as saying "Mike"
            elif t == "interrupt":
                await mike.interrupt()
            elif t == "set_pet":
                await mike.set_pet(data)
            elif t == "text" and data.get("text", "").strip():
                if mike.pending_confirm:
                    await mike.on_utterance(data["text"])
                else:
                    if mike.state in ("thinking", "speaking"):
                        await mike.interrupt()
                    await mike.handle_command(data["text"].strip())
            elif t == "approve" and mike.pending_confirm and not mike.pending_confirm["future"].done():
                mike.pending_confirm["future"].set_result(bool(data.get("allow")))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        mike.pets.discard(ws)


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
