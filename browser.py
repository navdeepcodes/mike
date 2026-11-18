"""Mike's own web browser: a real Chromium window he drives himself (Playwright).

He opens pages, reads them, clicks, types and scrolls like a person, in a window you can watch and
take over at any time. It has its own profile (data/browser-profile), so sites you log in to there stay
logged in, and it never touches your normal browser.

Safety (enforced in server.py, not left to the model): clicks that buy, pay, send, post or delete ask you
first, and Mike never types into password or payment-card fields; you type those yourself.

All Playwright calls run on one dedicated thread (its sync API isn't thread-safe).
"""
import logging
import queue
import re
import subprocess
import sys
import threading
from urllib.parse import quote_plus

import config

log = logging.getLogger("mike.browser")
PROFILE = config.DATA / "browser-profile"
MAX_ELEMENTS = 120
RISKY_CLICK = re.compile(r"\b(buy|purchase|place (your )?order|pay|checkout|check out|subscribe|donate|send|post|"
                         r"publish|tweet|reply|delete|remove|unsubscribe|cancel (my )?(order|subscription|account)|"
                         r"confirm|book|reserve|transfer|withdraw|submit|sign up|create account|accept)\b", re.I)
SECRET_FIELD = re.compile(r"password|passcode|card|cc-|cvc|cvv|csc|security code|expir|iban|routing|account number|"
                          r"ssn|social security|one-time|otp|2fa|verification code|pin\b", re.I)

# Marks every visible, usable element with data-mike-id and describes it.
_INDEX_JS = r"""
(max) => {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, summary, [contenteditable=""], [contenteditable=true],' +
              '[role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=switch],' +
              '[role=option], [role=combobox], [role=searchbox], [role=textbox], [onclick]';
  document.querySelectorAll('[data-mike-id]').forEach(e => e.removeAttribute('data-mike-id'));
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || el.disabled) continue;
    if (r.bottom < -window.innerHeight || r.top > window.innerHeight * 2) continue;   // near the visible part only
    let label = el.getAttribute('aria-label') || el.getAttribute('title') || '';
    if (!label && el.labels && el.labels[0]) label = el.labels[0].innerText;
    if (!label) label = (el.innerText || '').trim();
    if (!label) label = el.getAttribute('placeholder') || el.getAttribute('name') || el.getAttribute('alt') || '';
    if (!label && el.querySelector('img[alt]')) label = el.querySelector('img[alt]').alt;
    label = label.replace(/\s+/g, ' ').trim().slice(0, 80);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'dropdown' :
                 tag === 'textarea' || (tag === 'input' && !['button','submit','checkbox','radio','image','reset'].includes(type)) ||
                 el.isContentEditable ? 'field' : type === 'checkbox' ? 'checkbox' : 'button');
    const key = role + label + Math.round(r.top) + Math.round(r.left);
    if (seen.has(key) || (!label && role !== 'field')) continue;
    seen.add(key);
    const id = out.length + 1;
    el.setAttribute('data-mike-id', String(id));
    const hint = [type, el.getAttribute('autocomplete') || '', el.getAttribute('name') || '', el.id || ''].join(' ');
    out.push({id, role, label, value: (el.value !== undefined && role === 'field' && type !== 'password') ? String(el.value).slice(0, 60) : '',
              checked: el.checked === true, hint, inView: r.bottom > 0 && r.top < window.innerHeight});
  }
  return out;
}
"""

_TEXT_JS = r"""
() => {
  const root = document.querySelector('main, [role=main], article') || document.body;
  return (root.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
}
"""


class _Browser:
    def __init__(self):
        self.jobs: "queue.Queue" = queue.Queue()
        self.thread = None
        self.pw = self.ctx = self.page = None
        self.elements: dict[int, dict] = {}

    # ---- thread plumbing --------------------------------------------------
    def call(self, fn, *args, timeout: float = 90):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="mike-browser")
            self.thread.start()
        done: "queue.Queue" = queue.Queue()
        self.jobs.put((fn, args, done))
        ok, val = done.get(timeout=timeout)
        if not ok:
            raise val
        return val

    def _run(self):
        while True:
            fn, args, done = self.jobs.get()
            try:
                done.put((True, fn(*args)))
            except Exception as e:      # noqa: BLE001  (reported back to the caller)
                done.put((False, e))

    # ---- runs on the browser thread ------------------------------------------
    def _ensure(self):
        if self.ctx:
            try:
                if self.page and not self.page.is_closed():
                    return
                pages = [p for p in self.ctx.pages if not p.is_closed()]
                self.page = pages[-1] if pages else self.ctx.new_page()
                return
            except Exception:
                self.ctx = None
        from playwright.sync_api import sync_playwright
        if not self.pw:
            self.pw = sync_playwright().start()
        PROFILE.mkdir(parents=True, exist_ok=True)
        try:
            self._launch()
        except Exception as e:
            if "Executable doesn't exist" not in str(e):
                raise
            log.info("installing Chromium for Mike's browser (once)")
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True,
                           capture_output=True, timeout=900)
            self._launch()

    def _launch(self):
        self.ctx = self.pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport=None, no_viewport=True,
            args=["--window-size=1280,880", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"])
        self.ctx.set_default_timeout(15000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _settle(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass

    def _summary(self, find: str = "", text_chars: int = 3500) -> str:
        p = self.page
        els = p.evaluate(_INDEX_JS, MAX_ELEMENTS)
        self.elements = {e["id"]: e for e in els}
        if find:
            words = find.lower().split()
            els = [e for e in els if any(w in (e["label"] + " " + e["role"]).lower() for w in words)]
        lines = []
        for e in els:
            extra = f" = \"{e['value']}\"" if e["value"] else " (checked)" if e["checked"] else ""
            lines.append(f"[{e['id']}] {e['role']}: {e['label'] or '(no label)'}{extra}{'' if e['inView'] else ' (below)'}")
        text = "" if find else p.evaluate(_TEXT_JS)[:text_chars]
        tabs = [pg for pg in self.ctx.pages if not pg.is_closed()]
        head = f"Page: {p.title()[:100]}\nURL: {p.url}" + (f"\nTabs open: {len(tabs)}" if len(tabs) > 1 else "")
        body = ("\n\nWhat's on it:\n" + text) if text else ""
        return f"{head}{body}\n\nThings you can use (by [id]):\n" + ("\n".join(lines) or "(none found)")

    def _open(self, target: str) -> str:
        self._ensure()
        t = target.strip()
        if not re.match(r"^([a-z]+://|data:|about:)", t):
            t = ("https://" + t) if re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/\S*)?", t) else \
                "https://www.google.com/search?q=" + quote_plus(t)
        self.page.bring_to_front()
        self.page.goto(t, wait_until="domcontentloaded")
        self._settle()
        return self._summary(text_chars=2500)

    def _read(self, find: str = "") -> str:
        self._ensure()
        return self._summary(find)

    def _locator(self, element: int):
        if element not in self.elements:
            raise ValueError(f"No element [{element}] on this page now. Call browser_read to get fresh ids.")
        return self.page.locator(f'[data-mike-id="{element}"]').first

    def _describe(self, element: int) -> dict:
        self._ensure()
        from urllib.parse import urlparse
        host = urlparse(self.page.url).hostname or ""
        return {**(self.elements.get(element) or {}), "host": host.removeprefix("www.") or "this page"}

    def _click(self, element: int) -> str:
        self._ensure()
        before = len(self.ctx.pages)
        self._locator(element).click()
        self._settle()
        if len(self.ctx.pages) > before:              # it opened a new tab: follow it
            self.page = self.ctx.pages[-1]
            self._settle()
        return "Clicked.\n\n" + self._summary(text_chars=1500)

    def _type(self, element: int, text: str, submit: bool) -> str:
        self._ensure()
        loc = self._locator(element)
        if self.elements[element]["role"] == "dropdown":
            loc.select_option(label=text)
        else:
            loc.fill(text)
            if submit:
                loc.press("Enter")
                self._settle()
        return ("Typed and submitted.\n\n" + self._summary(text_chars=1500)) if submit else "Typed."

    def _scroll(self, direction: str) -> str:
        self._ensure()
        dy = {"up": -700, "down": 700, "top": -10**6, "bottom": 10**6}.get(direction, 700)
        self.page.mouse.wheel(0, dy)
        self.page.wait_for_timeout(400)
        return self._summary(text_chars=2500)

    def _key(self, key: str) -> str:
        self._ensure()
        self.page.keyboard.press(key)
        self._settle()
        return "Pressed.\n\n" + self._summary(text_chars=1200)

    def _back(self) -> str:
        self._ensure()
        self.page.go_back()
        self._settle()
        return self._summary(text_chars=2000)

    def _tab(self, index: int) -> str:
        self._ensure()
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        if not 1 <= index <= len(pages):
            return "Tabs: " + "; ".join(f"{i}. {p.title()[:50]}" for i, p in enumerate(pages, 1))
        self.page = pages[index - 1]
        self.page.bring_to_front()
        return self._summary(text_chars=2000)

    def _screenshot(self) -> bytes:
        self._ensure()
        return self.page.screenshot(type="jpeg", quality=70)

    def _close(self) -> str:
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
        self.ctx = self.page = None
        return "Closed the browser."


_b = _Browser()


def open(url_or_search: str) -> str:  # noqa: A001  (module API mirrors the tool names)
    return _b.call(_b._open, url_or_search)


def read(find: str = "") -> str:
    return _b.call(_b._read, find)


def describe(element: int) -> dict:
    return _b.call(_b._describe, int(element))


def click(element: int) -> str:
    return _b.call(_b._click, int(element))


def type_into(element: int, text: str, submit: bool = False) -> str:
    return _b.call(_b._type, int(element), str(text), bool(submit))


def scroll(direction: str = "down") -> str:
    return _b.call(_b._scroll, direction)


def key(name: str) -> str:
    return _b.call(_b._key, name)


def back() -> str:
    return _b.call(_b._back)


def tab(index: int = 0) -> str:
    return _b.call(_b._tab, int(index))


def screenshot() -> bytes:
    return _b.call(_b._screenshot)


def close() -> str:
    return _b.call(_b._close) if _b.thread else "The browser isn't open."


def check(op: str, args: dict) -> tuple[str, str]:
    """Safety check before an action. ('ok'|'ask'|'refuse', reason)."""
    if op not in ("click", "type"):
        return "ok", ""
    try:
        el = describe(int(args.get("element", 0)))
    except Exception:
        el = {}
    label = el.get("label", "")
    if op == "type" and (SECRET_FIELD.search(el.get("hint", "")) or SECRET_FIELD.search(label)):
        return "refuse", (f"That's a password or payment field ({label or 'secret field'}). Mike never types those: "
                          "ask the user to type it themselves in the browser window, then continue.")
    if op == "click" and RISKY_CLICK.search(label):
        return "ask", f'click "{label}" on {el.get("host", "the page")}'
    if op == "type" and args.get("submit") and RISKY_CLICK.search(label):
        return "ask", f'submit "{label}"'
    return "ok", ""
