"""Knows your voice, so a TV or video in the room can't talk to Mike.

Every time you call Mike by name (close to the mic), a short voice sample is added to your voice
print (data/voiceprint.npy, numbers only, no audio is kept). In a follow-up or while Mike is talking,
speech that doesn't sound like you is ignored. Until a few samples exist, nothing is filtered.
"""
import logging
import threading

import numpy as np

import config

log = logging.getLogger("mike.voice_id")
MODEL = config.DATA / "models" / "wespeaker_en_resnet34.onnx"
STORE = config.DATA / "voiceprint.npy"
RATE = 16000
MIN_SAMPLES = 3        # wake-ups needed before filtering starts
KEEP = 40              # newest samples kept
MATCH = 0.68           # cosine (WeSpeaker). Real speech tests: same person 0.74+, other people 0.72 or less (mean 0.53)
ENROLL_MIN = 0.45      # once a print exists, a new sample must roughly agree (stops a TV poisoning it)


class VoicePrint:
    def __init__(self):
        self.lock = threading.Lock()
        self.extractor = None
        try:
            import sherpa_onnx
            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=1)
            self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        except Exception as e:
            log.warning("voice recognition unavailable: %s", e)
        try:
            self.samples = np.load(STORE)
        except Exception:
            self.samples = np.zeros((0, 0), dtype=np.float32)

    @property
    def ready(self) -> bool:
        return self.extractor is not None and len(self.samples) >= MIN_SAMPLES and self.samples.shape[1] > 0

    def embed(self, pcm: bytes) -> np.ndarray | None:
        if self.extractor is None:
            return None
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < RATE * 0.6:
            return None
        with self.lock:
            s = self.extractor.create_stream()
            s.accept_waveform(RATE, audio)
            s.input_finished()
            if not self.extractor.is_ready(s):
                return None
            v = np.array(self.extractor.compute(s), dtype=np.float32)
        n = np.linalg.norm(v)
        return v / n if n else None

    def similarity(self, v: np.ndarray) -> float:
        centroid = self.samples.mean(axis=0)
        centroid /= np.linalg.norm(centroid) or 1
        best = float(np.max(self.samples @ v))
        return max(float(centroid @ v), best - 0.05)

    def enroll(self, pcm: bytes):
        v = self.embed(pcm)
        if v is None:
            return
        if self.ready and self.similarity(v) < ENROLL_MIN:
            log.info("wake word in a different voice; not added to your voice print")
            return
        base = self.samples if self.samples.shape[1:] == v.shape else np.zeros((0, len(v)), np.float32)
        self.samples = np.vstack([base, v[None]])[-KEEP:]
        np.save(STORE, self.samples)
        log.info("voice print updated (%d samples)", len(self.samples))

    def threshold(self) -> float:
        """MATCH, eased a little if your own samples vary a lot (noisy room, different mics)."""
        c = self.samples.mean(axis=0)
        c /= np.linalg.norm(c) or 1
        own = float(np.mean(self.samples @ c))
        return max(0.55, min(MATCH, own - 0.12))

    def is_user(self, pcm: bytes) -> tuple[bool, float | None]:
        """(is it you?, score). Unknown (not enough samples yet, clip too short) counts as you."""
        if not self.ready:
            return True, None
        v = self.embed(pcm)
        if v is None:
            return True, None
        score = self.similarity(v)
        return score >= self.threshold(), score

    def maybe_user(self, pcm: bytes) -> bool:
        """Lenient: False only when the clip clearly isn't you (TV, other people, noise).
        Used to skip noise before the slow speech model; the strict check still runs later."""
        if not self.ready:
            return True
        v = self.embed(pcm)
        if v is None:
            return True
        return self.similarity(v) >= self.threshold() - 0.10

    def reset(self):
        self.samples = np.zeros((0, 0), dtype=np.float32)
        STORE.unlink(missing_ok=True)
