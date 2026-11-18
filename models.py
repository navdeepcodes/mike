"""Downloads the local voice models into data/models on first run (about 150 MB, once).

Speech recognition models (Whisper) are fetched by their own libraries; these are the rest:
Kokoro (Mike's voice) and WeSpeaker (recognising your voice). Run `python models.py` to fetch them now.
"""
import logging
import sys
import urllib.request
from pathlib import Path

import config

log = logging.getLogger("mike.models")
_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {
    "kokoro-v1.0.int8.onnx": (_KOKORO + "kokoro-v1.0.int8.onnx", 92361271),
    "voices-v1.0.bin": (_KOKORO + "voices-v1.0.bin", 28214398),
    "wespeaker_en_resnet34.onnx": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                                   "speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx", 26534365),
}


def missing() -> list[str]:
    d = config.DATA / "models"
    return [n for n, (_, size) in FILES.items() if not (d / n).exists() or (d / n).stat().st_size != size]


def ensure(show: bool = False) -> bool:
    """Fetch whatever is missing. True when every model is in place."""
    d = config.DATA / "models"
    d.mkdir(parents=True, exist_ok=True)
    for name in missing():
        url, size = FILES[name]
        tmp = d / (name + ".part")
        msg = f"downloading {name} ({size // 1_000_000} MB)"
        print(msg, flush=True) if show else log.info(msg)
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            if tmp.stat().st_size != size:
                raise OSError(f"incomplete download ({tmp.stat().st_size} of {size} bytes)")
            tmp.replace(d / name)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            print(f"  failed: {e}", flush=True) if show else log.warning("model download failed: %s: %s", name, e)
            return False
    return True


if __name__ == "__main__":
    sys.exit(0 if ensure(show=True) else 1)
