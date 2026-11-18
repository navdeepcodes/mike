"""Performance check: how fast Mike starts, speaks, and understands speech on this machine.

    python tests/perf.py            prints a table; writes data/perf.json
No microphone, speaker or API keys needed: Mike's own voice says test phrases, and his ears transcribe them.
"""
import asyncio
import io
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import numpy as np  # noqa: E402

import config  # noqa: E402

PHRASES = ["Hey Mike, what's the weather like today?", "Mike, set a timer for ten minutes.",
           "Open Spotify and play some music.", "Remind me to call mom at six thirty."]
R = {"os": platform.platform(), "cpu": platform.processor() or platform.machine(), "python": platform.python_version(),
     "stt_engine": config.STT_ENGINE}


def ms(t):
    return round((time.perf_counter() - t) * 1000)


def to_16k(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav)) as w:
        rate, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    n = int(len(pcm) * 16000 / rate)
    out = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm)
    return np.concatenate([np.zeros(4000), out, np.zeros(4000)]).astype(np.int16).tobytes()


NUMBERS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "thirty"}


def words(s):
    """Words to compare, without numbers ("ten" vs "10" is the same thing heard)."""
    import re
    return [w for w in re.findall(r"[a-z]+", s.lower()) if w not in NUMBERS]


def main():
    import models
    t = time.perf_counter(); models.ensure(show=True); R["models_download_ms"] = ms(t)

    import voice
    t = time.perf_counter(); voice.load(); R["voice_load_ms"] = ms(t)
    from ears import Ears

    async def nop(*a):
        return None
    ears = Ears(nop, nop)
    t = time.perf_counter(); ears.load(); R["ears_load_ms"] = ms(t)

    tts, stt, acc, rows = [], [], [], []
    for p in PHRASES:
        t = time.perf_counter(); wav = asyncio.run(voice.synth(p)); tts.append(ms(t))
        pcm = to_16k(wav)
        t = time.perf_counter(); heard = ears._transcribe(pcm, attentive=True); stt.append(ms(t))
        want, got = words(p), set(words(heard))
        acc.append(sum(w in got for w in want) / len(want))
        rows.append({"said": p, "heard": heard, "tts_ms": tts[-1], "stt_ms": stt[-1]})
    R.update(tts_ms_median=int(np.median(tts)), stt_ms_median=int(np.median(stt)),
             word_accuracy=round(float(np.mean(acc)), 3), phrases=rows)

    # The whole server: boot time, a local command over the websocket, memory.
    port = int(os.environ.get("MIKE_PERF_PORT", "8791"))
    env = dict(os.environ, MIKE_PORT=str(port), PYTHONUTF8="1")
    t = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "server.py"], cwd=ROOT, env=env,
                            stdout=open(config.DATA / "perf-server.log", "wb"), stderr=subprocess.STDOUT)
    try:
        for _ in range(600):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("server exited: see data/perf-server.log")
                time.sleep(0.25)
        R["server_boot_ms"] = ms(t)
        R["reply_ms"] = asyncio.run(_ask(port, "what time is it"))
        try:
            import psutil
            p = psutil.Process(proc.pid)
            R["server_ram_mb"] = round(sum(x.memory_info().rss for x in [p, *p.children(recursive=True)]) / 1e6)
        except Exception:
            pass
    finally:
        proc.terminate()
    if not config.IS_MAC:
        import desktop
        t = time.perf_counter(); shot = desktop.screenshot_jpeg(); R["screenshot_ms"] = ms(t) if shot else None
        t = time.perf_counter(); desktop.handle("context", {}); R["context_ms"] = ms(t)

    config.DATA.mkdir(exist_ok=True)
    (config.DATA / "perf.json").write_text(json.dumps(R, indent=1))
    report = _table()
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report + "\n")


async def _ask(port, text):
    import websockets
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as ws:
        t = time.perf_counter()
        await ws.send(json.dumps({"type": "text", "text": text}))
        seen_user = False
        while True:
            m = await asyncio.wait_for(ws.recv(), 60)
            if isinstance(m, bytes):
                continue
            kind = json.loads(m).get("type")
            seen_user = seen_user or kind == "user"
            if seen_user and kind == "audio":
                return ms(t)


def _table():
    lines = [f"### Mike performance: {R['os']}", "", "| Check | Result |", "|---|---|"]
    names = {"voice_load_ms": "Voice loads", "ears_load_ms": "Speech recognition loads", "server_boot_ms": "Server ready",
             "tts_ms_median": "Speaks a sentence (median)", "stt_ms_median": "Understands a sentence (median)",
             "word_accuracy": "Words understood", "reply_ms": "Answers 'what time is it' (first audio)",
             "server_ram_mb": "Memory (server)", "screenshot_ms": "Screenshot", "context_ms": "Reads the front window"}
    for k, n in names.items():
        v = R.get(k)
        if v is None:
            continue
        lines.append(f"| {n} | {f'{v:.0%}' if k == 'word_accuracy' else f'{v} MB' if k.endswith('_mb') else f'{v/1000:.2f} s'} |")
    lines += ["", "| Said | Heard | Speak | Understand |", "|---|---|---|---|"]
    lines += [f"| {r['said']} | {r['heard']} | {r['tts_ms']} ms | {r['stt_ms']} ms |" for r in R["phrases"]]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
