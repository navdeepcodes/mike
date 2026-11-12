"""Mike's voice: macOS text-to-speech rendered to WAV and played in the HUD.

Playing it in the browser (not through `say` directly) lets the browser's echo
cancellation keep Mike from hearing himself.
"""
import asyncio
import io
import json
import logging
import os
import re
import tempfile
import threading
import wave

import numpy as np

import config

log = logging.getLogger("mike.voice")


def speakable(text: str) -> str:
    """Strip markdown and other things that sound bad when read aloud."""
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [label](url) -> label
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"^\s*[#>*\-•]+\s*", "", text, flags=re.M)        # headings, bullets, quotes
    text = re.sub(r"^\s*\d{1,2}[.)]\s+", "", text, flags=re.M)        # "1." list numbers
    text = re.sub(r"(?<=\d)\s*°\s*C\b", " degrees", text)
    text = re.sub(r"(?<=\d)\s*°\s*F\b", " degrees Fahrenheit", text)
    text = text.replace("°", " degrees")
    text = re.sub(r"(?<=\d)\s*%", " percent", text)
    text = re.sub(r"~\s*(?=\d)", "about ", text)
    text = re.sub(r"\bkm/h\b", "kilometers per hour", text)
    text = re.sub(r"\s&\s", " and ", text)
    text = re.sub(r"\be\.g\.,?", "for example", text, flags=re.I)
    text = re.sub(r"\bi\.e\.,?", "that is", text, flags=re.I)
    text = re.sub(r"\bvs\.?(?=\s)", "versus", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)   # emoji
    text = re.sub(r"[*_#|~]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---- Kokoro (neural, local) ------------------------------------------------
SETTINGS = config.DATA / "settings.json"
_kokoro = None
_kokoro_lock = threading.Lock()
_voice = config.KOKORO_VOICE

# Friendly names for the voice picker. Kokoro voice ids: a=American, b=British; f/m = female/male.
VOICE_LABELS = {
    "am_michael": "Michael · American", "am_fenrir": "Fenrir · American", "am_puck": "Puck · American",
    "am_adam": "Adam · American", "am_echo": "Echo · American", "am_eric": "Eric · American",
    "am_liam": "Liam · American", "am_onyx": "Onyx · American",
    "bm_george": "George · British", "bm_fable": "Fable · British", "bm_lewis": "Lewis · British",
    "bm_daniel": "Daniel · British",
    "af_heart": "Heart · American", "af_bella": "Bella · American", "af_nicole": "Nicole · American",
    "af_sarah": "Sarah · American", "af_nova": "Nova · American", "af_sky": "Sky · American",
    "bf_emma": "Emma · British", "bf_isabella": "Isabella · British", "bf_alice": "Alice · British",
}


def _load_settings():
    global _voice
    try:
        _voice = json.loads(SETTINGS.read_text()).get("voice", _voice)
    except Exception:
        pass


def _save_settings():
    try:
        data = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    except Exception:
        data = {}
    data["voice"] = _voice
    SETTINGS.write_text(json.dumps(data, indent=2))


def load():
    """Load Kokoro once (call from a worker thread at startup)."""
    global _kokoro
    _load_settings()
    if config.TTS_ENGINE != "kokoro":
        return
    if not (config.KOKORO_MODEL.exists() and config.KOKORO_VOICES.exists()):
        log.warning("Kokoro model files missing in %s; using macOS voice", config.KOKORO_MODEL.parent)
        return
    try:
        from kokoro_onnx import Kokoro
        k = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        k.create("Ready.", voice=_valid_voice(k, _voice), speed=config.KOKORO_SPEED, lang="en-us")  # warm up
        _kokoro = k
        log.info("Kokoro voice ready (%s)", _voice)
    except Exception:
        log.exception("Kokoro failed to load; using macOS voice")


def _valid_voice(k, v):
    names = set(k.get_voices())
    return v if v in names else ("am_michael" if "am_michael" in names else sorted(names)[0])


def voices() -> list[dict]:
    if not _kokoro:
        return []
    out = []
    for v in sorted(_kokoro.get_voices()):
        if v[:1] not in ("a", "b"):
            continue  # English voices only
        out.append({"id": v, "label": VOICE_LABELS.get(v, v)})
    out.sort(key=lambda x: (x["id"] not in VOICE_LABELS, x["id"][1] != "m", x["label"]))
    return out


def current_voice() -> str:
    return _voice if _kokoro else "macos"


def set_voice(v: str) -> bool:
    global _voice
    if not _kokoro or v not in _kokoro.get_voices():
        return False
    _voice = v
    _save_settings()
    return True


def _kokoro_wav(text: str) -> bytes:
    with _kokoro_lock:
        v = _valid_voice(_kokoro, _voice)
        samples, rate = _kokoro.create(text, voice=v, speed=config.KOKORO_SPEED,
                                       lang="en-gb" if v.startswith("b") else "en-us")
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def synth(text: str) -> bytes | None:
    text = speakable(text)
    if not text:
        return None
    if _kokoro:
        try:
            return await asyncio.get_running_loop().run_in_executor(None, _kokoro_wav, text)
        except Exception:
            log.exception("Kokoro synthesis failed; falling back to macOS voice")
    return await _say_wav(text)


async def _say_wav(text: str) -> bytes | None:
    fd, out = tempfile.mkstemp(prefix="mike_", suffix=".wav")
    os.close(fd)
    fd, src = tempfile.mkstemp(prefix="mike_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    args = ["say", "-f", src, "-o", out, "--file-format=WAVE", "--data-format=LEI16@22050",
            "-r", str(config.VOICE_RATE)]
    if config.VOICE:
        args += ["-v", config.VOICE]
    try:
        proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode())
        with open(out, "rb") as f:
            return f.read()
    finally:
        for p in (out, src):
            try:
                os.remove(p)
            except OSError:
                pass


_END = re.compile(r"([.!?…]+[\"')\]]?)(\s+)|\n+")


_CLAUSE = re.compile(r"[,;:]\s+|\s+[—–-]\s+")


class SentenceSplitter:
    """Collects streamed text and releases complete sentences for speaking."""

    def __init__(self):
        self.buf = ""
        self.released = 0

    def push(self, delta: str) -> list[str]:
        self.buf += delta
        out = []
        start = 0
        # Start talking early: the very first chunk of a reply goes out at the first clause
        # break (comma, dash, colon) instead of waiting for the whole sentence.
        if self.released == 0 and len(self.buf) >= 28 and not _END.search(self.buf):
            m = _CLAUSE.search(self.buf, 18)
            if m and len(self.buf) - m.end() >= 6:
                out.append(self.buf[:m.end()].strip())
                self.buf = self.buf[m.end():]
                self.released += 1
        while True:
            m = _END.search(self.buf, start)
            if not m:
                break
            sentence = self.buf[:m.end()].strip()
            # Don't release tiny fragments ("Sure." / "e.g.") on their own; merge with the next.
            if len(sentence) < 12 and not m.group(0).startswith("\n"):
                start = m.end()
                continue
            self.buf = self.buf[m.end():]
            start = 0
            if sentence:
                out.append(sentence)
                self.released += 1
        return out

    def ends_cleanly(self) -> bool:
        """True when the buffer looks like a finished sentence (the reply may be over)."""
        b = self.buf.rstrip()
        return len(b) >= 4 and b[-1] in ".!?…"

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []
