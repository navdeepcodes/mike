"""Terminal sessions Mike can start, watch, type into and stop: dev servers, test watchers, build runs, a shell.

Each session is a real pseudo-terminal, so programs behave as they would in Terminal.app (colors, prompts, Ctrl-C).
They live in Mike's server, so they keep running between questions. Output is kept (ANSI codes stripped) for
reading back, and mirrored to a log file so `show` can open it in a normal Terminal window with no macOS prompt.
Sessions end when Mike restarts.
"""
import fcntl
import os
import pty
import re
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import config

LOG_DIR = config.DATA / "terminals"
MAX_SESSIONS = 8
MAX_BUF = 300_000
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>MD78]")
_sessions: dict[str, "Session"] = {}
on_output = None     # set by the server: called (from a reader thread) with the session name whenever it prints or ends
_lock = threading.Lock()


def _clean(raw: str) -> str:
    text = _ANSI.sub("", raw).replace("\r\n", "\n")
    # A carriage return redraws the line (progress bars): keep what ends up on screen.
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


class Session:
    def __init__(self, name: str, command: str, cwd: str):
        self.name, self.command, self.cwd = name, command, cwd
        self.raw = ""
        self.started = time.time()
        self.exit_code: int | None = None
        self.log_path = LOG_DIR / f"{name}.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
        env = {**os.environ, "TERM": "xterm-256color", "PYTHONUNBUFFERED": "1"}
        env["PATH"] = ":".join(["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", str(Path.home() / ".local/bin"),
                                env.get("PATH", "/usr/bin:/bin")])
        argv = ["/bin/zsh", "-c", command] if command else ["/bin/zsh", "-i"]
        # The child gets its own session with this terminal as its controlling one, so Ctrl-C works.
        wrapper = "import os,sys,fcntl,termios;os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0);os.execvp(sys.argv[1],sys.argv[1:])"
        self.proc = subprocess.Popen([sys.executable, "-c", wrapper, *argv], stdin=slave, stdout=slave, stderr=slave,
                                     cwd=cwd or None, env=env, close_fds=True)
        os.close(slave)
        self.fd = master
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        with open(self.log_path, "ab", buffering=0) as log:
            while True:
                try:
                    data = os.read(self.fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                log.write(data)
                with _lock:
                    self.raw = (self.raw + data.decode("utf-8", "replace"))[-MAX_BUF:]
                if on_output:
                    on_output(self.name)
        self.exit_code = self.proc.wait()
        if on_output:
            on_output(self.name)
        try:
            os.close(self.fd)
        except OSError:
            pass

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def text(self) -> str:
        with _lock:
            return _clean(self.raw)

    def send(self, text: str, enter: bool = True):
        if not self.running:
            raise RuntimeError("that terminal has ended")
        os.write(self.fd, (text + ("\n" if enter else "")).encode())

    def stop(self):
        if self.running:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", (name or "main").strip())[:30] or "main"


def start(name: str, command: str = "", cwd: str = "") -> str:
    name = _safe(name)
    cwd = os.path.expanduser(cwd) if cwd else ""
    if cwd and not os.path.isdir(cwd):
        return f"Not started: {cwd} isn't a folder."
    old = _sessions.get(name)
    if old and old.running:
        return f"A terminal called '{name}' is already running ({old.command or 'shell'}). Read it, send to it, or stop it first."
    if sum(s.running for s in _sessions.values()) >= MAX_SESSIONS:
        return f"Not started: {MAX_SESSIONS} terminals are already running. Stop one first."
    _sessions[name] = Session(name, command, cwd)
    return f"Started terminal '{name}'" + (f" running: {command}" if command else " (a shell)") + ". Use terminal_read to see its output."


def read(name: str, lines: int = 40, wait: float = 0.0) -> str:
    s = _sessions.get(_safe(name))
    if not s:
        return f"No terminal called '{name}'. Running: {', '.join(_sessions) or 'none'}."
    if wait > 0:
        # Wait until it has said something and then gone quiet for a moment (or the wait runs out).
        end, before, last_change = time.time() + min(wait, 30), len(s.raw), time.time()
        seen = before
        while time.time() < end and s.running:
            time.sleep(0.15)
            if len(s.raw) != seen:
                seen, last_change = len(s.raw), time.time()
            elif seen != before and time.time() - last_change > 1.2:
                break
    out = s.text().rstrip().split("\n")[-max(1, min(lines, 300)):]
    status = "running" if s.running else f"ended with exit code {s.exit_code}"
    return f"[{s.name}: {status}]\n" + "\n".join(out)


def send(name: str, text: str, enter: bool = True) -> str:
    s = _sessions.get(_safe(name))
    if not s:
        return f"No terminal called '{name}'."
    try:
        s.send(text, enter)
    except Exception as e:
        return f"Not sent: {e}"
    return "Sent. Use terminal_read to see what happened."


def interrupt(name: str) -> str:
    s = _sessions.get(_safe(name))
    if not s or not s.running:
        return f"No running terminal called '{name}'."
    s.send("\x03", enter=False)
    return "Sent Ctrl-C."


def stop(name: str) -> str:
    s = _sessions.get(_safe(name))
    if not s:
        return f"No terminal called '{name}'."
    s.stop()
    return f"Stopped '{s.name}'."


def snapshot(lines: int = 150) -> list[dict]:
    """For the Code view: every terminal with its latest output."""
    return [{"name": s.name, "running": s.running, "exit": s.exit_code, "command": s.command, "cwd": s.cwd,
             "text": "\n".join(s.text().rstrip().split("\n")[-lines:])} for s in _sessions.values()]


def listing() -> str:
    if not _sessions:
        return "No terminals."
    rows = []
    for s in _sessions.values():
        st = "running" if s.running else f"ended ({s.exit_code})"
        rows.append(f"{s.name}: {st}, {int(time.time() - s.started)}s, in {s.cwd or '~'}: {s.command or 'shell'}")
    return "\n".join(rows)


def show(name: str) -> str:
    """Opens the terminal's output live in a normal Terminal window (no macOS permission prompt)."""
    s = _sessions.get(_safe(name))
    if not s:
        return f"No terminal called '{name}'."
    script = LOG_DIR / f"{s.name}.command"
    script.write_text(f"#!/bin/zsh\nprintf '\\033]0;Mike: {s.name}\\007'\ntail -n 300 -f '{s.log_path}'\n")
    script.chmod(0o755)
    subprocess.Popen(["open", "-a", "Terminal", str(script)])
    return f"Opened '{s.name}' in a Terminal window. It follows the output live (read only)."


def stop_all():
    for s in list(_sessions.values()):
        s.stop()
