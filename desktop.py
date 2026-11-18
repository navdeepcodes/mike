"""Computer control for Windows and Linux (on a Mac, Mike.app does this natively).

Same operations and answers as app/Control.swift, so the rest of Mike doesn't care which OS it runs on.
Uses pyautogui (mouse, keyboard, media keys) and mss (screenshots). Reading an app's buttons by name
(ui / press / set_text / menu) is Mac-only for now; elsewhere Mike uses screenshot + click instead.
"""
import ctypes
import io
import os
import subprocess
import sys
import webbrowser

WINDOWS = os.name == "nt"
MAC_ONLY = {"ui", "press", "set_text", "menu", "automation"}
_KEYS = {"return": "enter", "forwarddelete": "delete", "delete": "backspace", "escape": "esc",
         "pageup": "pageup", "pagedown": "pagedown"}
_MODS = {"command": "ctrl", "cmd": "ctrl", "control": "ctrl", "ctrl": "ctrl", "option": "alt", "alt": "alt",
         "shift": "shift", "win": "win", "windows": "win"}
_MEDIA = {"play_pause": "playpause", "play": "playpause", "pause": "playpause", "next": "nexttrack",
          "previous": "prevtrack", "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute"}


def _gui():
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.02
    return pyautogui


def _front_window() -> tuple[str, str]:
    """(app, window title) of the foreground window."""
    if WINDOWS:
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetForegroundWindow()
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                import psutil
                app = psutil.Process(pid.value).name().removesuffix(".exe")
            except Exception:
                app = title.rsplit(" - ", 1)[-1] if " - " in title else title
            return app, title
        except Exception:
            return "", ""
    try:
        title = subprocess.run(["xdotool", "getactivewindow", "getwindowname"], capture_output=True, text=True,
                               timeout=1).stdout.strip()
        return (title.rsplit(" - ", 1)[-1] if " - " in title else title), title
    except Exception:
        return "", ""


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input."""
    if WINDOWS:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return max(0.0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)
    return 0.0


def screenshot_jpeg() -> bytes | None:
    try:
        import mss
        from PIL import Image
        with mss.mss() as s:
            shot = s.grab(s.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        # Coordinates in the picture must equal click coordinates: keep the logical size.
        w, h = _gui().size()
        if img.size != (w, h):
            img = img.resize((w, h))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=70)
        return out.getvalue()
    except Exception:
        return None


def notify(title: str, message: str):
    """A desktop notification, best effort, no extra installs."""
    try:
        if WINDOWS:
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  f"$n.ShowBalloonTip(6000,{_ps(title)},{_ps(message)},'Info');Start-Sleep 7;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f"display notification {_as(message)} with title {_as(title)}"])
        else:
            subprocess.Popen(["notify-send", title, message])
    except Exception:
        pass


def _ps(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _as(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _open(target: str):
    if WINDOWS:
        os.startfile(target)  # noqa: S606 (opening the user's own file/app)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def handle(op: str, a: dict) -> dict:
    """Run one native operation. Returns the same shapes Mike.app returns."""
    try:
        if op == "context":
            app, title = _front_window()
            return {k: v for k, v in (("app", app), ("window", title[:120])) if v}
        if op == "perm_status":
            return {"microphone": "granted", "camera": "granted", "screen": "granted", "accessibility": "granted"}
        if op in ("open_pane", "login_item", "show_at_launch"):
            return {"enabled": False}
        if op == "app_info":
            import platform
            return {"version": "1.0", "build": "1", "os": platform.platform()}
        if op == "open_url":
            url = str(a.get("url", ""))
            if not url.startswith(("http://", "https://")):
                return {"error": "bad url"}
            webbrowser.open(url)
            return {"ok": True}
        if op == "open_path":
            path = os.path.expanduser(str(a.get("path", "")))
            _open(os.path.dirname(path) if a.get("reveal") else path)
            return {"ok": True}
        if op == "apps":
            app, _ = _front_window()
            names: list[str] = []
            try:
                import pygetwindow
                names = sorted({t.rsplit(" - ", 1)[-1] for t in pygetwindow.getAllTitles() if t.strip()})
            except Exception:
                pass
            return {"front": app, "running": names}
        if op == "activate":
            name = str(a.get("app", "")).lower()
            try:
                import pygetwindow
                for w in pygetwindow.getAllWindows():
                    if name and name in w.title.lower():
                        if w.isMinimized:
                            w.restore()
                        w.activate()
                        return {"ok": True, "app": w.title}
            except Exception as e:
                return {"error": f"Couldn't switch: {e}"}
            return {"error": f"No window for {a.get('app')}; start it first."}
        if op == "media":
            key = _MEDIA.get(str(a.get("action", "")))
            if not key:
                return {"error": "Unknown media action. Use: " + ", ".join(sorted(_MEDIA))}
            g = _gui()
            for _ in range(max(1, min(int(a.get("times") or 1), 16))):
                g.press(key)
            return {"ok": True}
        if op == "click":
            _gui().click(int(a.get("x", 0)), int(a.get("y", 0)), clicks=2 if a.get("double") else 1,
                         button="right" if a.get("right") else "left")
            return {"ok": True}
        if op == "type":
            text = str(a.get("text", ""))
            g = _gui()
            if text.isascii():
                for i, line in enumerate(text.split("\n")):
                    if i:
                        g.press("enter")
                    g.write(line, interval=0.005)
            else:   # pyautogui can't type non-ASCII: paste it instead
                _paste(text)
            return {"ok": True}
        if op == "key":
            k = str(a.get("key", "")).lower()
            k = _KEYS.get(k, k)
            mods = [_MODS.get(str(m).lower(), str(m).lower()) for m in a.get("modifiers") or []]
            _gui().hotkey(*mods, k)
            return {"ok": True}
        if op == "scroll":
            g = _gui()
            kw = {"x": int(a["x"]), "y": int(a["y"])} if a.get("x") is not None else {}
            if a.get("dy"):
                g.scroll(int(a["dy"]) * 40, **kw)
            if a.get("dx"):
                g.hscroll(int(a["dx"]) * 40, **kw)
            return {"ok": True}
        if op in MAC_ONLY:
            return {"error": "Reading app buttons by name only works on a Mac so far. Use screenshot, then click "
                             "at the coordinates you see, plus press_key and type_text."}
        return {"error": f"unknown op {op}"}
    except ImportError as e:
        return {"error": f"Computer control isn't installed ({e.name}). Run the installer again."}
    except Exception as e:
        return {"error": f"{op} failed: {e}"}


def _paste(text: str):
    if WINDOWS:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value {_ps(text)}"],
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
    else:
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), timeout=5)
    _gui().hotkey("ctrl", "v")
