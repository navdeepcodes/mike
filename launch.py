"""Start Mike and open his window (Windows and Linux; on a Mac, Mike.app does this).

    python launch.py              start the backend if needed, then open Mike's window
    python launch.py --background start the backend only (used at login)
    python launch.py --stop       stop the backend

The window is Edge or Chrome in "app" mode (no tabs or address bar). Keep it open: it is Mike's
microphone and speaker. Closing it is fine; Mike keeps reminders running and reopens when you start him.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

URL = f"http://{config.HOST}:{config.PORT}/"
WIN = os.name == "nt"


def up() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=1.5):
            return True
    except Exception:
        return False


def start_backend():
    log_path = config.DATA / "server.log"
    config.DATA.mkdir(exist_ok=True)
    if log_path.exists() and log_path.stat().st_size > 2_000_000:
        log_path.replace(log_path.with_suffix(".log.1"))
    py = Path(sys.executable)
    if WIN and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"           # no console window
    log = open(log_path, "ab")
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if WIN else 0
    # UTF-8 mode: Windows would otherwise read and write text files as cp1252 and choke on emoji or accents.
    subprocess.Popen([str(py), "-X", "utf8", str(ROOT / "server.py")], cwd=str(ROOT), stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1"),
                     creationflags=flags, start_new_session=not WIN)


def _browser() -> str | None:
    if WIN:
        for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("LOCALAPPDATA", "")):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                p = Path(base) / rel
                if base and p.exists():
                    return str(p)
        return None
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def open_window():
    exe = _browser()
    if exe:
        profile = config.DATA / "window-profile"     # its own profile: remembers the mic permission, no extensions
        subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={profile}", "--window-size=460,760",
                          "--autoplay-policy=no-user-gesture-required", "--no-first-run"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(URL)


def stop_backend():
    import signal
    try:
        import psutil
    except ImportError:
        print("psutil missing; close Mike from Task Manager (python).")
        return
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == config.PORT and c.status == "LISTEN" and c.pid:
            psutil.Process(c.pid).send_signal(signal.SIGTERM)
            print("Mike stopped.")
            return
    print("Mike wasn't running.")


def main():
    if "--stop" in sys.argv:
        return stop_backend()
    if not up():
        print("Starting Mike (the first start loads the speech models, up to a minute)...", flush=True)
        start_backend()
        for _ in range(240):
            if up():
                break
            time.sleep(0.5)
        else:
            print(f"Mike didn't start. See {config.DATA / 'server.log'}")
            sys.exit(1)
    if "--background" not in sys.argv:
        open_window()


if __name__ == "__main__":
    main()
