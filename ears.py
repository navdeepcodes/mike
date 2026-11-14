"""Mike's ears: voice-activity detection + local Whisper transcription.

Audio arrives from the browser as 16 kHz mono int16 PCM. We cut it into
utterances with WebRTC VAD and transcribe each one locally (no quota used).
"""
import asyncio
import difflib
import logging
import re
import time
from typing import Awaitable, Callable

import numpy as np
import webrtcvad
from faster_whisper import WhisperModel

import config

log = logging.getLogger("mike.ears")

RATE = 16000
FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2  # int16
PREROLL_FRAMES = 17          # keep ~500 ms before speech starts, so a quick "Mike" isn't clipped
START_FRAMES = 4             # voiced frames (of the last 6) needed to start
MAX_SECONDS = 30

# Tells Whisper the name exists without making it think the name was already said
# (a prompt like "Hey Mike." makes it skip the name in "Hey Mike, open...").
WAKE_HINT = f"Glossary: {config.NAME} (the assistant's name)."
# For the accurate model: only the name, in the way it's called ("Hey Mike. Okay Mike."). It spells "Mike" instead of
# "My" / "Mic" and keeps a short call recognisable through room noise (5 of 6 calls kept at 0 dB, against 0 of 6 with no hint).
# A longer hint made noise come out as whole fake requests ("Mike, what's the capital of Peru?"), so it stays this plain.
HINT_SENTENCES = [f"Hey {config.NAME}.", f"Okay {config.NAME}."]
ADDRESS_HINT = " ".join(HINT_SENTENCES)



# Whisper invents these from noise; ignore them when they are the whole utterance.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching",
                  "bye", "the end", "so", "uh", "um", "hmm", "okay"}


def _dedupe(text: str) -> str:
    """Whisper loops on noise ("I'm sorry. I'm sorry. I'm sorry."). Drop sentences repeated 3+ times."""
    import re
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    counts = {}
    for p in parts:
        counts[p.lower()] = counts.get(p.lower(), 0) + 1
    kept = [p for p in parts if counts[p.lower()] < 3]
    if len(kept) != len(parts):
        log.info("dropped repeated speech from: %r", text[:120])
    return " ".join(kept)


def rhythm_db(pcm: bytes) -> float:
    """How much the loudness rises and falls (the beat of syllables), 10th to 90th percentile in dB.
    Speech, even in a noisy room, is 7 dB or more; steady noise (hum, fan, hiss) is 5 dB or less."""
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(a) // 320
    if n < 10:
        return 99.0                      # too short to judge
    db = 20 * np.log10(np.sqrt(np.mean(a[: n * 320].reshape(n, 320) ** 2, axis=1)) + 1e-9)
    return float(np.percentile(db, 90) - np.percentile(db, 10))


def loudness_db(pcm: bytes) -> float:
    """How loud the speech in a clip is (90th percentile of 30 ms frame levels, dBFS)."""
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(a) // 480
    if n == 0:
        return -90.0
    rms = np.sqrt(np.mean(a[: n * 480].reshape(n, 480) ** 2, axis=1)) + 1e-9
    return float(20 * np.log10(np.percentile(rms, 90)))


class Ears:
    def __init__(self, on_utterance: Callable[[str], Awaitable[None]],
                 on_level: Callable[[float, bool], Awaitable[None]]):
        self.on_utterance = on_utterance
        self.on_level = on_level
        self.vad = webrtcvad.Vad(3)  # most aggressive: ignore more background noise
        self.model: WhisperModel | None = None
        self.buf = b""
        self.preroll: list[bytes] = []
        self.recent: list[bool] = []
        self.speech: list[bytes] = []
        self.in_speech = False
        self.silence = 0
        self.queue: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue()
        self.enabled = True
        # Set by the server: True while Mike is waiting for you (after the name, follow-ups,
        # yes/no). Then every utterance gets the accurate model straight away.
        self.attentive: Callable[[], bool] = lambda: True
        # Set by the server: quick yes/no on a clip (is it plausibly your voice?). Noise and other
        # people are dropped before the slow speech model, so your real words are never stuck behind them.
        self.prefilter: Callable[[bytes], bool] | None = None
        # Loudness learning: talk that isn't for Mike (TV, people in the room) vs. how loud you
        # are when you say the name. Follow-ups quieter than that are treated as background.
        self.bg_levels: list[float] = []
        self.wake_level: float | None = None
        self.last_db = -90.0
        self.last_pcm = b""       # the clip behind the latest utterance (for the voice check)
        self.clip_is_you = False  # set by the voice prefilter: this clip clearly sounds like you
        # The quick screening model mishears the name in a few typical ways. A false hit only
        # costs one pass of the accurate model, which then decides for real.
        names = {w.split()[-1] for w in config.WAKE_WORDS} | {config.NAME.lower(), "michael", "mikes", "mics",
                 "mykey", "mikee", "nike", "mice", "mark", "mack", "mick", "mikel", "maik", "bike", "hike"}
        self.wake_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names))) + r")\b", re.I)
        # The quick model also turns "Mike" into these when other people are talking; they only
        # earn a second look from the accurate model, which decides.
        self.screen_re = re.compile(self.wake_re.pattern + r"|\bmight\b|^\W*(hey\W+|yo\W+)?my\W*$", re.I)

    def load(self):
        self.mlx = None
        if getattr(config, "STT_ENGINE", "") == "mlx":
            try:
                import mlx_whisper
                from huggingface_hub import snapshot_download
                # Resolve the models to local folders once. Passing a repo name makes mlx-whisper
                # ask huggingface.co for updates on EVERY utterance: slower, and broken offline.
                def local(repo):
                    try:
                        return snapshot_download(repo, local_files_only=True)
                    except Exception:
                        return snapshot_download(repo)
                self.full_path = local(config.MLX_WHISPER_MODEL)
                self.screen_path = local(config.WAKE_SCREEN_MODEL) if getattr(config, "WAKE_SCREEN_MODEL", None) else None
                # Downloads once (~1.6 GB), then runs on the Mac's GPU. Warm-up also compiles it.
                mlx_whisper.transcribe(np.zeros(RATE, dtype=np.float32), path_or_hf_repo=self.full_path, language="en")
                self.mlx = mlx_whisper
                self.screen_model = self.screen_path
                if self.screen_model:
                    mlx_whisper.transcribe(np.zeros(RATE, dtype=np.float32), path_or_hf_repo=self.screen_model, language="en")
                log.info("whisper ready (mlx %s, screening with %s)", config.MLX_WHISPER_MODEL, self.screen_model)
                return
            except Exception:
                log.exception("mlx whisper unavailable; falling back to faster-whisper")
        log.info("loading whisper %s", config.WHISPER_MODEL)
        self.model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        # Warm up so the first real command is fast.
        self.model.transcribe(np.zeros(RATE, dtype=np.float32), language="en")
        log.info("whisper ready")

    async def run(self):
        """Transcribe utterances one at a time, in order."""
        loop = asyncio.get_running_loop()
        while True:
            pcm, attentive = await self.queue.get()
            # Stay real-time: if background clips have piled up, skip stale ones (never ones
            # captured while Mike was waiting for you).
            if not attentive and self.queue.qsize() >= 2:
                log.info("skipped a stale background clip (backlog %d)", self.queue.qsize())
                continue
            self.last_db = loudness_db(pcm)
            self.last_pcm = pcm
            # Every clip, not only follow-ups: a TV or another person saying "Mike" shouldn't wake him,
            # and room talk shouldn't cost a speech-model pass. Lenient: only clips clearly not you.
            if self.prefilter is not None:
                try:
                    plausible = await loop.run_in_executor(None, self.prefilter, pcm)
                except Exception:
                    plausible = True
                if not plausible:
                    log.info("skipped a clip before transcribing (%.0f dB, not your voice)", self.last_db)
                    self.bg_levels = (self.bg_levels + [self.last_db])[-40:]
                    continue
            t0 = time.time()
            try:
                text = await loop.run_in_executor(None, self._transcribe, pcm, attentive)
            except Exception:
                log.exception("transcription failed")
                continue
            log.info("timing: transcribed %.1fs of speech in %.2fs", len(pcm) / (RATE * 2), time.time() - t0)
            if text and text.lower().strip(" .,!?") not in HALLUCINATIONS:
                if self.wake_re.search(text):
                    self.wake_level = self.last_db if self.wake_level is None else 0.7 * self.wake_level + 0.3 * self.last_db
                await self.on_utterance(text)

    def is_near(self, db: float) -> bool:
        """Loud enough to be you talking to Mike rather than the room?"""
        # Until the room has been measured, assume a normal speaking voice near the Mac.
        gate = float(np.median(self.bg_levels)) + 6 if len(self.bg_levels) >= 4 else -32.0
        if self.wake_level is not None:
            gate = min(gate, self.wake_level - 8)   # never stricter than how loud you are when you call him
        log.info("level %.1f dB vs gate %.1f dB", db, gate)
        return db >= gate

    def _transcribe(self, pcm: bytes, attentive: bool = True) -> str:
        qtext = ""
        self.alt = ""          # a second opinion when the two models disagree (the brain gets to weigh it)
        fast_text = ""
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        # Whisper tends to drop a word that starts right at sample 0; a little silence in front fixes it.
        audio = np.concatenate([np.zeros(RATE // 4, dtype=np.float32), audio])
        # Auto-gain is off in the mic (so you stand out from the room); level it here for Whisper.
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak < 0.002:      # no signal at all: with a name-prompt Whisper would invent one from nothing
            return ""
        if rhythm_db(pcm) < 6.0:   # steady noise: the name-prompt can turn that into a phantom "Hey Mike"
            log.info("skipped steady noise (no speech rhythm)")
            return ""
        if 0.0005 < peak < 0.5:
            audio = audio * (0.5 / peak)
        if self.mlx and not attentive and getattr(self, "screen_model", None):
            # Background speech: a quick pass to see if the name was said at all.
            t = time.time()
            quick = self.mlx.transcribe(audio, path_or_hf_repo=self.screen_model, language="en",
                                        temperature=0.0, condition_on_previous_text=False,
                                        initial_prompt=ADDRESS_HINT)      # same plain call as the accurate model: much better in a noisy room
            qtext = quick.get("text", "").strip()
            # Nothing understood in a short clip: could be a mumbled "Mike", let the accurate model judge.
            # Only a short clip loud enough to be you near the Mac is worth a second look.
            unsure = not qtext and len(audio) < RATE * 3 and loudness_db(pcm) >= -40
            if not unsure and not self.screen_re.search(qtext):
                log.info("ignored (no name, %.2fs, %.0f dB): %r", time.time() - t, loudness_db(pcm), qtext[:80])
                self.bg_levels = (self.bg_levels + [loudness_db(pcm)])[-40:]
                return ""
        if self.mlx:
            res = None
            thr = getattr(config, "FAST_ACCEPT_LOGPROB", None)
            if thr is not None and getattr(self, "screen_model", None):
                # The small model takes ~0.15 s, the big one ~1.2 s (it always encodes 30 s of audio).
                # With the name prompt the small one is nearly as accurate, and it knows when it's
                # unsure (measured: when this confident, 3.9% word errors vs 4.4% for the big one).
                fast = self.mlx.transcribe(
                    audio, path_or_hf_repo=self.screen_model, language="en", temperature=0.0,
                    condition_on_previous_text=False, no_speech_threshold=0.5, initial_prompt=ADDRESS_HINT)
                segs = fast.get("segments", [])
                fast_text = fast.get("text", "").strip()
                if segs and fast.get("text", "").strip() and all(
                        s.get("avg_logprob", -9) >= thr and s.get("no_speech_prob", 1) < 0.3
                        and s.get("compression_ratio", 9) <= 2.0 for s in segs):
                    res = fast
                    log.info("timing: quick model was sure enough, skipped the big one")
            if res is None:
                res = self.mlx.transcribe(
                    audio, path_or_hf_repo=self.full_path, language="en", temperature=0.0,
                    condition_on_previous_text=False, no_speech_threshold=0.5, initial_prompt=ADDRESS_HINT)
            kept, dropped = [], []
            for s in res.get("segments", []):
                words = re.findall(r"[a-z']+", s.get("text", "").lower())
                trusted = getattr(self, "clip_is_you", False)       # the voice check says it's you: ASR doubt counts for less
                # A short call of the name in a noisy room scores badly even when it's right; give it more room.
                call = 0 < len(words) <= 3 and bool(self.wake_re.search(s.get("text", "")))
                if (s.get("no_speech_prob", 0) > (0.65 if (call or trusted) else 0.55)
                        or s.get("avg_logprob", 0) < (-1.4 if trusted else -1.3 if call else -0.9)
                        or s.get("compression_ratio", 1) > 2.2):
                    dropped.append(s.get("text", "").strip())
                    log.info("low-confidence segment (logprob %.2f, no-speech %.2f, your voice: %s): %r", s.get("avg_logprob", 0), s.get("no_speech_prob", 0), trusted, s.get("text", "").strip()[:60])
                    continue
                kept.append(s.get("text", "").strip())
            if dropped:
                log.info("dropped low-confidence speech: %r", " / ".join(dict.fromkeys(dropped))[:160])
            text = _dedupe(" ".join(kept).strip())
            if fast_text and res is not fast and text and difflib.SequenceMatcher(None, fast_text.lower(), text.lower()).ratio() < 0.85:
                self.alt = fast_text
            # A lone "Mike" is so short the big model sometimes returns nothing for it.
            # If the quick pass heard just the name (optionally "hey/ok/yo"), trust it.
            if len(text.split()) <= 2 and not self.wake_re.search(text) and re.fullmatch(r"(hey|hi|ok|okay|yo)?[\s,]*" + self.wake_re.pattern + r"[\s.!?,]*", qtext, re.I):
                log.info("short name heard by quick pass: %r", qtext)
                return config.NAME
            return text
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=3, vad_filter=True,
            condition_on_previous_text=False,
        )
        kept = []
        for s in segments:
            # Drop what Whisper invents from noise, background talk or other languages:
            # likely-silence, low-confidence, or repetitive ("I'm gonna do it. I'm gonna do it.") segments.
            if s.no_speech_prob > 0.55 or s.avg_logprob < -0.85 or s.compression_ratio > 2.2:
                log.info("dropped low-confidence speech: %r (nsp=%.2f lp=%.2f cr=%.2f)",
                         s.text.strip(), s.no_speech_prob, s.avg_logprob, s.compression_ratio)
                continue
            kept.append(s.text.strip())
        return " ".join(kept).strip()

    async def feed(self, chunk: bytes):
        """Feed raw PCM from the browser."""
        self.buf += chunk
        peak = 0.0
        while len(self.buf) >= FRAME_BYTES:
            frame, self.buf = self.buf[:FRAME_BYTES], self.buf[FRAME_BYTES:]
            samples = np.frombuffer(frame, dtype=np.int16)
            peak = max(peak, float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0)
            voiced = self.enabled and self.vad.is_speech(frame, RATE)
            self._step(frame, voiced)
        await self.on_level(min(1.0, peak * 6), self.in_speech)

    def _step(self, frame: bytes, voiced: bool):
        if not self.in_speech:
            self.preroll.append(frame)
            self.preroll = self.preroll[-PREROLL_FRAMES:]
            self.recent.append(voiced)
            self.recent = self.recent[-6:]
            if sum(self.recent) >= START_FRAMES:
                self.in_speech = True
                self.speech = list(self.preroll)
                self.silence = 0
                self.recent = []
            return
        self.speech.append(frame)
        self.silence = 0 if voiced else self.silence + FRAME_MS
        too_long = len(self.speech) * FRAME_MS >= MAX_SECONDS * 1000
        if self.silence >= config.SILENCE_MS or too_long:
            pcm = b"".join(self.speech)
            self.in_speech = False
            self.speech = []
            self.preroll = []
            # Ignore blips shorter than ~0.4 s of actual sound.
            if len(pcm) > RATE * 2 * 0.4 + config.SILENCE_MS * RATE * 2 / 1000:
                log.info("utterance captured (%.1fs)", len(pcm) / (RATE * 2))
                self.queue.put_nowait((pcm, bool(self.attentive())))
