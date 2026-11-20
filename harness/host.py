"""The product hosted for visitors: one process of the page (`event-risk`) for each visitor, behind this front.

A visitor with no session meets the gate: what this is, a field for their Anthropic API key, what a run costs, what
is kept, and the model the analyst runs on, of MODELS. On Start the front asks Anthropic whether the key is good and
can use that model, with one request that spends nothing (`GET /v1/models/{id}` with `x-api-key` and
`anthropic-version`: https://platform.claude.com/docs/en/api/models/retrieve; a key Anthropic refuses is answered 401
`authentication_error`, an organization it has disabled 400, and a model the key cannot use 404 `not_found_error` or
403 `permission_error`). A good key starts a child: `event-risk` on a free port of 127.0.0.1 with a folder of its own
for its conversations, given the key as ANTHROPIC_API_KEY, the model chosen as EVENT_RISK_MODEL, and
EVENT_RISK_HOSTED (by which the page names no key of the host's to the visitor), and,
of the front's own environment, only what PASSED names. A cookie names the session.
Every later request is passed to that session's child and its answer passed back as it arrives, the page's event
stream included. The front knows the product by its command (`--port`, `--no-browser`, `--stores`) and its HTTP
surface: the page at `/`, its files under `/web/`, its state at `/api/state`, its guard on its own origin.

Where the key goes. The gate's form posts it in the request's body, never in an address. `_start` reads it from the
body and `check_key` sends it to Anthropic in the `x-api-key` header; `Front.spawn` puts it into the child's
environment, and the front keeps no copy: a Session holds none, and no line the front prints or answers names it.
The child reads it from its environment for its requests to Anthropic, and its store refuses any value that carries
it (harness.credentials). It goes with the child.

Between visitors. A session is a random 256-bit token in an HttpOnly cookie, and nothing in a path or a header
chooses a child: a request reaches the child of the session its cookie names, or none. Each child listens on
127.0.0.1 alone and answers only its own origin (harness.server's guard). The front checks that a POST comes from
its own public origin (its `Origin` against `Host`, by `X-Forwarded-Proto` behind a proxy) and that a request other
than a navigation comes from its own site, then presents the child with the host, origin and site the child expects.

A session ends when its visitor ends it; when no page of it has been open and nothing asked of it for IDLE while the
analyst is not working; at LIFE whole; and when its child stops. Its child is stopped and its folder deleted, and the
visitor's next request meets the gate with a line saying why. At SESSIONS live sessions the gate starts nothing.

The host sets, by environment: PORT, and EVENT_RISK_SESSIONS (4), EVENT_RISK_IDLE_MINUTES (120), EVENT_RISK_HOURS
(12), EVENT_RISK_BODY_MB (50, the most a request may carry: a message with its files, which the page sends as
base64), EVENT_RISK_KEY_CHECKS (12, the keys checked with Anthropic in a minute, for the whole front),
EVENT_RISK_EXIT_IDLE_MINUTES (where it is set, the front exits, cleanly, once it has had no session and no request
for that long: a host that starts it on a request then stops the machine until the next visitor),
EVENT_RISK_SESSIONS_DIR (where each session's folder is made), EVENT_RISK_EXAMPLE (the finished example's
address), and the keys and settings PASSED names, each passed to every child where it is set.
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
ANTHROPIC = "https://api.anthropic.com"
VERSION = "2023-06-01"  # the anthropic-version header
KEYS_PAGE = "https://platform.claude.com/settings/keys"
EXAMPLE = "https://owassmer.github.io/event-risk/"
# The models a visitor chooses among, the first chosen unless they pick another, each with about what an analysis like
# the example's costs on it: that analysis's tokens (24.15M read from the cache, 0.54M written to it for an hour, and
# 0.10M of output) at each model's prices (https://platform.claude.com/docs/en/about-claude/pricing).
MODELS = (("claude-fable-5-2", "Claude Fable 5.2", 17), ("claude-opus-5-5", "Claude Opus 5.5", 11),
          ("claude-sonnet-5-5", "Claude Sonnet 5.5", 6), ("claude-haiku-5-5", "Claude Haiku 5.5", 2))
NAMES = {m: name for m, name, _ in MODELS}
SEARCH = "claude-haiku-5-5"  # the model the page's web search and page reading run on, whatever the analyst's
COOKIE = "session"
FOLDER = re.compile(r"[0-9a-f]{16}")  # a session's folder's name
# What of the front's environment each child is given, where it is set: what a process needs to run, then the keys
# and settings the host chooses for every session. The visitor's key is added as ANTHROPIC_API_KEY.
PASSED = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
          "SEC_CONTACT", "OPENFIGI_API_KEY", "MASSIVE_API_KEY", "BASETEN_API_KEY")
KEY_SHAPE = re.compile(r"[A-Za-z0-9_-]{20,300}")  # what an Anthropic key can be, so nothing else reaches a header
HOP = frozenset({"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade",
                 "proxy-authorization", "proxy-authenticate"})
DROPPED = HOP | {"host", "origin", "cookie", "content-length", "sec-fetch-site", "forwarded", "x-forwarded-for",
                 "x-forwarded-proto", "x-forwarded-host", "x-real-ip"}
START_WAIT = 90.0  # seconds a child has to answer before its start is given up
REFUSED = "This server answers only its own page."
GONE = "This session has ended. Reload the page to start a new one."

LINES = {  # what the gate says, each in one line
    "refused": "Anthropic did not accept that key.",
    "model": "Choose one of the models offered.",
    "unchecked": "Anthropic could not be reached to check the key. Try again in a moment.",
    "full": "Every session is in use just now. Try again in a little while.",
    "checks": "Too many keys have been checked here in the last minute. Try again in a minute.",
    "failed": "The session could not be started. Try again in a moment.",
    "ended": "Your session has ended, and its data is deleted.",
    "stopped": "Your session stopped unexpectedly, and its data was deleted.",
    "unknown": "Your session has ended.",
}


def _span(seconds: float) -> str:
    """A length of time in words: 2 hours, 1 hour, 90 minutes."""
    minutes = round(seconds / 60)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def check_key(key: str, model: str, anthropic: str = ANTHROPIC) -> str:
    """What Anthropic says of a key and the model it would run: "good"; "refused", the key; "model", the key cannot
    use the model; or "unchecked", where it could not be asked."""
    request = urllib.request.Request(f"{anthropic.rstrip('/')}/v1/models/{model}",
                                     headers={"x-api-key": key, "anthropic-version": VERSION})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return "good" if answer.status == 200 else "unchecked"
    except urllib.error.HTTPError as err:
        return "refused" if err.code in (400, 401) else "model" if err.code in (403, 404) else "unchecked"
    except (urllib.error.URLError, OSError):
        return "unchecked"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ask(port: int, path: str, timeout: float = 5.0) -> Any:
    """A child's JSON answer to a GET, as its own page asks it; None where it gives none."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        answer = conn.getresponse()
        return json.loads(answer.read()) if answer.status == 200 else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


@dataclass
class Session:
    token: str  # the cookie's value
    folder: Path
    port: int
    proc: subprocess.Popen
    started: float
    seen: float  # when a request last began or ended
    open: int = 0  # requests under way, the page's event stream among them
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class StartFailed(Exception):
    pass


class Front:
    """The sessions and their children."""

    def __init__(self, *, command: list[str] | None = None, root: Path | None = None,
                 env: Mapping[str, str] | None = None, sessions: int = 4, idle: float = 7200.0, life: float = 43200.0,
                 body: int = 50 << 20, checks: int = 12, exit_idle: float | None = None,
                 passed: tuple[str, ...] = PASSED, anthropic: str = ANTHROPIC, example: str = EXAMPLE):
        self.command = command or [_product()]
        self.root = Path(root) if root else Path(tempfile.gettempdir()) / "event-risk-sessions"
        self.env = dict(os.environ if env is None else env)
        self.most, self.idle, self.life, self.body, self.checks = sessions, idle, life, body, checks
        self.checked: deque[float] = deque()  # when each of the last minute's key checks was made
        self.exit_idle = exit_idle
        self.last = time.monotonic()  # when a request last came, or a session last ended
        self.passed, self.anthropic, self.example = passed, anthropic, example
        self.lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self.starting = 0
        self.ended: dict[str, tuple[str, float]] = {}  # a session's token, why it ended and when, kept for a day
        self.root.mkdir(parents=True, exist_ok=True)
        for left in self.root.iterdir():  # the session folders a front before this one left
            if FOLDER.fullmatch(left.name):
                shutil.rmtree(left, ignore_errors=True)

    # ---------------------------------------------------------------- a session's life

    def reserve(self) -> bool:
        """A place for a session about to start, where one is free."""
        with self.lock:
            if len(self.sessions) + self.starting >= self.most:
                return False
            self.starting += 1
            return True

    def may_check(self) -> bool:
        """Whether a key may be checked now, within CHECKS a minute for the whole front: a stranger cannot make this
        server ask Anthropic faster, or try a list of keys through it. Counted where it is granted."""
        now = time.monotonic()
        with self.lock:
            while self.checked and now - self.checked[0] >= 60:
                self.checked.popleft()
            if len(self.checked) >= self.checks:
                return False
            self.checked.append(now)
            return True

    def release(self) -> None:
        with self.lock:
            self.starting -= 1

    def touch(self) -> None:
        self.last = time.monotonic()

    def empty_for(self) -> float:
        """How long the front has had no session, no session starting, and no request."""
        with self.lock:
            return 0.0 if self.sessions or self.starting else time.monotonic() - self.last

    def spawn(self, key: str, model: str) -> Session:
        """A child on the visitor's key and the model they chose, answering, with its session."""
        folder = self.root / secrets.token_hex(8)
        (folder / "stores").mkdir(parents=True)
        port = _free_port()
        env = {k: self.env[k] for k in self.passed if self.env.get(k)}
        env["ANTHROPIC_API_KEY"] = key
        env["EVENT_RISK_HOSTED"] = "1"  # the product's own: it names no key of the host's to the visitor
        env["EVENT_RISK_MODEL"] = model  # one of MODELS, as the gate offered it
        try:
            proc = subprocess.Popen([*self.command, "--port", str(port), "--no-browser", "--stores",
                                     str(folder / "stores")], env=env, cwd=folder, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise StartFailed() from None
        finally:
            del env
        until = time.monotonic() + START_WAIT
        while _ask(port, "/api/state", timeout=2.0) is None:
            if proc.poll() is not None or time.monotonic() > until:
                self._stop_child(proc)
                shutil.rmtree(folder, ignore_errors=True)
                raise StartFailed()
            time.sleep(0.2)
        now = time.monotonic()
        session = Session(secrets.token_urlsafe(32), folder, port, proc, now, now)
        with self.lock:
            self.sessions[session.token] = session
        return session

    def session(self, token: str | None) -> tuple[Session | None, str | None]:
        """The live session a cookie names; else None, with why it ended where a cookie was sent."""
        if not token:
            return None, None
        with self.lock:
            found = self.sessions.get(token)
            if found is not None:
                return found, None
            why = self.ended.get(token)
        return None, why[0] if why else "unknown"

    def end(self, session: Session, why: str) -> None:
        """Stop a session's child and delete its folder."""
        with self.lock:
            if self.sessions.pop(session.token, None) is None:
                return
            now = time.monotonic()
            self.ended = {t: w for t, w in self.ended.items() if now - w[1] < 86400}
            self.ended[session.token] = (why, now)
        self._stop_child(session.proc)
        self.touch()
        shutil.rmtree(session.folder, ignore_errors=True)

    @staticmethod
    def _stop_child(proc: subprocess.Popen) -> None:
        for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def sweep(self) -> None:
        """End each session whose child stopped, that has run LIFE, or that has been idle for IDLE."""
        now = time.monotonic()
        with self.lock:
            live = list(self.sessions.values())
        for s in live:
            if s.proc.poll() is not None:
                self.end(s, "stopped")
            elif now - s.started >= self.life:
                self.end(s, "life")
            elif s.open == 0 and now - s.seen >= self.idle and not (_ask(s.port, "/api/state") or {}).get("busy"):
                self.end(s, "idle")

    def sweeping(self, every: float = 30.0, empty: Any = None) -> None:
        """Sweep every `every` seconds; and where the front exits when empty, call `empty` once it has been empty for
        EXIT_IDLE."""
        def run() -> None:
            while True:
                time.sleep(every)
                try:
                    self.sweep()
                except Exception as err:  # a sweep that fails is tried again at the next
                    print(f"event-risk-host: sweep: {type(err).__name__}", file=sys.stderr, flush=True)
                if self.exit_idle and empty is not None and self.empty_for() >= self.exit_idle:
                    empty()
                    return

        threading.Thread(target=run, daemon=True, name="sweep").start()

    def close(self) -> None:
        with self.lock:
            live = list(self.sessions.values())
        for s in live:
            self.end(s, "unknown")

    def line(self, why: str) -> str:
        if why == "idle":
            return f"Your session ended after {_span(self.idle)} with no page open, and its data was deleted."
        if why == "life":
            return f"Your session reached its {_span(self.life)} limit, and its data was deleted."
        return LINES.get(why, "")


def _product() -> str:
    """The product's command, installed beside the interpreter that runs the front."""
    beside = Path(sys.executable).with_name("event-risk")
    return str(beside) if beside.exists() else (shutil.which("event-risk") or "event-risk")


# ---------------------------------------------------------------- what the front shows

GATE_CSS = """
html, body { height: auto; overflow: auto; }  /* the page's own body holds still; the gate scrolls where it is long */
body { margin: 0; min-height: 100dvh; display: grid; place-items: center; background: var(--bg); color: var(--ink);
  font: 400 14px/1.55 var(--sans); }
#gate { width: min(520px, 100% - 40px); padding: 40px 0; }
#gate h1 { font: 600 28px/1.22 var(--sans); letter-spacing: -0.025em; margin: 0 0 10px; text-align: center; }
#gate .g-lead { color: var(--gray); margin: 0 0 26px; text-align: center; }
#gate .g-notice { margin: 0 0 16px; padding: 9px 12px; border: 1px solid var(--line2); border-radius: var(--r2);
  background: var(--surface); color: var(--ink); }
#gate .g-notice.bad { border-color: var(--rust); color: var(--rust); }
#gate form { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r3); padding: 20px;
  box-shadow: var(--shadow-sm); }
#gate .g-label { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
#gate label { font-weight: 600; }
#gate a { color: var(--accent); text-underline-offset: 3px; }
#gate input[type="password"] { box-sizing: border-box; width: 100%; font: 13.5px/1.4 var(--mono); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line2); border-radius: var(--r2); padding: 9px 11px; outline: none; }
#gate input[type="password"]:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
#gate .g-note { color: var(--gray); font-size: 13px; margin: 12px 0 0; }
#gate .g-models { border: 0; margin: 18px 0 0; padding: 0; min-width: 0; }
#gate .g-models legend { font-weight: 600; padding: 0; margin-bottom: 6px; }
#gate .g-model { display: flex; align-items: center; gap: 10px; padding: 8px 11px; border: 1px solid var(--line);
  border-top-width: 0; cursor: pointer; font-weight: 400; }
#gate .g-model:first-of-type { border-top-width: 1px; border-radius: var(--r2) var(--r2) 0 0; }
#gate .g-model:last-of-type { border-radius: 0 0 var(--r2) var(--r2); }
#gate .g-model:has(input:checked) { background: var(--accent-soft); }
#gate .g-model input { margin: 0; accent-color: var(--accent); }
#gate .g-name { flex: 1; }
#gate .g-cost { font: 13px/1 var(--mono); color: var(--gray); }
#gate .g-act { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 18px; margin-top: 20px; }
#gate button { font: 600 14px/1 var(--sans); color: #fff; background: var(--accent); border: 0; border-radius: var(--r2);
  padding: 11px 22px; cursor: pointer; }
#gate button:disabled { opacity: 0.6; cursor: default; }
"""

LINE_CSS = """
#host-line { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 16px; padding: 7px 16px;
  background: var(--surface); border-bottom: 1px solid var(--line); font: 13px/1.45 var(--sans); color: var(--gray); }
#host-line .hl-text { flex: 1 1 auto; min-width: 0; }
#host-line strong { color: var(--ink); font-weight: 600; }
#host-line form { margin: 0 0 0 auto; }
#host-line button { border: 0; background: none; padding: 0; font: 600 13px/1.45 var(--sans); color: var(--ink);
  text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
#host-line button:hover { color: var(--rust); }
"""

LINE_JS = """
(() => {
  const line = document.getElementById("host-line");
  const fit = () => document.documentElement.style.setProperty("--above", line.offsetHeight + "px");
  new ResizeObserver(fit).observe(line);
  fit();
  setInterval(async () => {
    try { if ((await fetch("/_host/alive", { cache: "no-store" })).status === 410) location.replace("/"); } catch (e) {}
  }, 15000);
})();
"""

LINE = ('<div id="host-line"><span class="hl-text"><strong>Live session</strong> · Your key and data leave this '
        'server when you end it.</span><form method="post" action="/_host/end" onsubmit="return confirm('
        "'End the session? Its conversation and ledger are deleted.')\"><button type=\"submit\">End session</button>"
        "</form></div>")


def access(model: str) -> str:
    """What the key needs access to, for the model chosen."""
    if model == SEARCH:
        return f"The key needs access to {NAMES[model]}."
    return f"The key needs access to {NAMES[model]}, and to {NAMES[SEARCH]} for web search."


def gate(front: Front, notice: str = "", bad: bool = False, chosen: str = MODELS[0][0]) -> str:
    said = f'<p class="g-notice{" bad" if bad else ""}" role="status">{html.escape(notice)}</p>' if notice else ""
    models = "".join(
        f'<label class="g-model"><input type="radio" name="model" value="{m}" data-access="{html.escape(access(m))}"'
        f'{" checked" if m == chosen else ""}><span class="g-name">{name}</span><span class="g-cost">${cost}</span>'
        "</label>" for m, name, cost in MODELS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Event risk</title><link rel="icon" href="data:,">
<link rel="stylesheet" href="/web/style.css"><style>{GATE_CSS}</style></head>
<body><main id="gate">
<h1>What could move your portfolio?</h1>
<p class="g-lead">Give the analyst your holdings. It finds the events that could move them, says how likely each is,
and shows what you gain or lose under each outcome. This page runs it live, on your own Anthropic API key.</p>
{said}<form method="post" action="/_host/start" id="g-form">
<div class="g-label"><label for="g-key">Anthropic API key</label>
<a href="{KEYS_PAGE}" target="_blank" rel="noopener">Get a key</a></div>
<input id="g-key" name="key" type="password" autocomplete="off" spellcheck="false" required>
<p class="g-note">Your key is used only to run your analysis. It stays in this session's memory, goes only to
Anthropic, and is gone when the session ends.</p>
<fieldset class="g-models"><legend>Model</legend>{models}</fieldset>
<p class="g-note">Beside each model, about what an analysis like the example's costs on it, billed to your key by
Anthropic. A full analysis takes about 30 minutes. <span id="g-access">{html.escape(access(chosen))}</span></p>
<p class="g-note">Your holdings and conversation are kept on this server for your session, and deleted when you end
it or {_span(front.idle)} after you leave.</p>
<div class="g-act"><button type="submit">Start</button>
<a href="{html.escape(front.example)}">See a finished example first</a></div>
</form></main>
<script>const form = document.getElementById("g-form");
form.addEventListener("change", (e) => {{
  if (e.target.name === "model") document.getElementById("g-access").textContent = e.target.dataset.access; }});
form.addEventListener("submit", (e) => {{
  const b = e.target.querySelector("button"); b.disabled = true; b.textContent = "Starting\\u2026"; }});</script>
</body></html>"""


def with_line(page: bytes) -> bytes:
    """The product's page with the session's line above it."""
    text = page.decode("utf-8")
    head, body = text.find("</head>"), re.search(r"<body[^>]*>", text)
    if head < 0 or body is None:
        return page
    text = (text[:head] + f"<style>{LINE_CSS}</style>" + text[head:body.end()] + LINE + f"<script>{LINE_JS}</script>"
            + text[body.end():])
    return text.encode("utf-8")


# ---------------------------------------------------------------- requests

class Handler(BaseHTTPRequestHandler):
    front: Front
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # quiet: a request's line is no one's business here, and names no key
        return

    def _send(self, status: int, body: bytes, kind: str, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, page: str, status: int = 200, headers: Mapping[str, str] | None = None) -> None:
        self._send(status, page.encode(), "text/html; charset=utf-8", headers)

    def _json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _see(self, where: str, headers: Mapping[str, str] | None = None) -> None:
        self._send(303, b"", "text/plain", {"Location": where, **(headers or {})})

    # who is asking

    def _origin(self) -> str:
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip().lower()
        return f"{proto}://{(self.headers.get('Host') or '').strip().lower()}"

    def _secure(self) -> bool:
        return self._origin().startswith("https://")

    def _own(self, method: str) -> bool:
        """A POST from the front's own public origin; a GET that is a navigation, or from the front's own site."""
        if method == "POST":
            return (self.headers.get("Origin") or "").strip().lower().rstrip("/") == self._origin()
        site = (self.headers.get("Sec-Fetch-Site") or "same-origin").strip().lower()
        return site in ("same-origin", "none") or self.headers.get("Sec-Fetch-Mode") == "navigate"

    def _token(self) -> str | None:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        found = cookie.get(COOKIE)
        return found.value if found else None

    def _cookie(self, token: str | None) -> dict[str, str]:
        secure = "; Secure" if self._secure() else ""
        if token is None:
            return {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"}
        return {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax{secure}"}

    # routes

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        self.front.touch()
        try:
            path = urlparse(self.path).path
            if not self._own(method):
                self.close_connection = True
                return self._send(403, REFUSED.encode(), "text/plain; charset=utf-8")
            if method == "GET" and path.startswith("/web/"):
                return self._static(path[len("/web/"):])
            if method == "POST" and path == "/_host/start":
                return self._start()
            session, why = self.front.session(self._token())
            if session is not None and session.proc.poll() is not None:  # its child stopped
                self.front.end(session, "stopped")
                session, why = None, "stopped"
            if method == "POST" and path == "/_host/end":
                if session is not None:
                    self.front.end(session, "ended")
                return self._see("/?ended", self._cookie(None))
            if path == "/_host/alive":
                return self._send(204 if session else 410, b"", "text/plain")
            if session is None:
                if method == "GET" and (path == "/" or self.headers.get("Sec-Fetch-Mode") == "navigate"):
                    headers = self._cookie(None) if why else {}
                    if why is None and urlparse(self.path).query == "ended":  # ended by its visitor just now
                        why = "ended"
                    return self._html(gate(self.front, self.front.line(why) if why else ""), headers=headers)
                self.close_connection = True
                return self._json(409, {"error": GONE})
            self._proxy(session, method, path)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as err:  # said by its kind and place alone: no message of a request's values reaches a log
            frame = err.__traceback__
            while frame and frame.tb_next:
                frame = frame.tb_next
            where = f"{Path(frame.tb_frame.f_code.co_filename).name}:{frame.tb_lineno}" if frame else ""
            print(f"event-risk-host: {type(err).__name__} at {where}", file=sys.stderr, flush=True)
            self.close_connection = True
            try:
                self._send(500, b"Something went wrong on this server.", "text/plain; charset=utf-8")
            except OSError:
                pass

    def _static(self, rel: str) -> None:
        """The page's own files, as the product serves them: a file under web/, and nothing else."""
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        kind = "font/woff2" if target.suffix == ".woff2" else mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind == "application/javascript":
            kind += "; charset=utf-8"
        self._send(200, target.read_bytes(), kind)

    def _start(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
            self.close_connection = True
            return self._html(gate(self.front, LINES["refused"], bad=True), 413)
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        key = (form.get("key") or [""])[0].strip()
        model = (form.get("model") or [MODELS[0][0]])[0]
        del form
        if model not in NAMES:  # nothing a visitor types becomes a child's environment
            return self._html(gate(self.front, LINES["model"], bad=True))
        said = lambda line, status=200: self._html(gate(self.front, line, bad=True, chosen=model), status)  # noqa: E731
        if not KEY_SHAPE.fullmatch(key):
            return said(LINES["refused"])
        if not self.front.reserve():
            return said(LINES["full"], 503)
        try:
            if not self.front.may_check():
                return said(LINES["checks"], 429)
            verdict = check_key(key, model, self.front.anthropic)
            if verdict == "model":
                return said(f"Your key can't use {NAMES[model]}. Choose another model, or a key that has access to it.")
            if verdict != "good":
                return said(LINES[verdict])
            try:
                session = self.front.spawn(key, model)
            except StartFailed:
                return said(LINES["failed"], 503)
        finally:
            del key
            self.front.release()
        self._see("/", self._cookie(session.token))

    def _proxy(self, session: Session, method: str, path: str) -> None:
        """The request passed to the session's child, as its own page would make it, and its answer passed back as it
        arrives."""
        n = int(self.headers.get("Content-Length") or 0)
        if n > self.front.body:  # read and set aside, up to twice the ceiling, so that the page reads why
            self.close_connection = True
            if n <= 2 * self.front.body:
                while n > 0 and (chunk := self.rfile.read(min(n, 1 << 20))):
                    n -= len(chunk)
            return self._json(413, {"error": f"A message with its files can be at most {self.front.body >> 20} MB."})
        body = self.rfile.read(n) if n else None
        port = session.port
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROPPED}
        headers.update({"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        if "Origin" in self.headers:
            headers["Origin"] = f"http://127.0.0.1:{port}"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        with session.lock:
            session.open += 1
            session.seen = time.monotonic()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=900)
        try:
            try:
                conn.request(method, self.path, body, headers)
                answer = conn.getresponse()
            except (OSError, http.client.HTTPException):
                self.close_connection = True
                if session.proc.poll() is None:
                    return self._json(502, {"error": "The session did not answer. Try again in a moment."})
                self.front.end(session, "stopped")
                if method == "GET" and (path == "/" or self.headers.get("Sec-Fetch-Mode") == "navigate"):
                    return self._html(gate(self.front, self.front.line("stopped")), headers=self._cookie(None))
                return self._json(409, {"error": GONE})
            kind = answer.getheader("Content-Type") or ""
            passed = [(k, v) for k, v in answer.getheaders() if k.lower() not in HOP | {"content-length"}]
            if method == "GET" and path == "/" and answer.status == 200 and kind.startswith("text/html"):
                page = with_line(answer.read())
                self.send_response(answer.status)
                for k, v in passed:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            length = answer.getheader("Content-Length")
            self.send_response(answer.status)
            for k, v in passed:
                self.send_header(k, v)
            if length is not None:
                self.send_header("Content-Length", length)
            else:  # a stream, delimited by the connection's end as the child's is
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            while chunk := answer.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            self.close_connection = True
        finally:
            conn.close()
            with session.lock:
                session.open -= 1
                session.seen = time.monotonic()


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name) or default)
    except ValueError:
        raise SystemExit(f"event-risk-host: {name} takes a number") from None
    if value <= 0:
        raise SystemExit(f"event-risk-host: {name} takes a number above 0")
    return value


def main(argv: list[str] | None = None) -> int:
    env = os.environ
    parser = argparse.ArgumentParser(prog="event-risk-host", description="The page hosted live: one process for each "
                                     "visitor, each on the visitor's own Anthropic API key.")
    parser.add_argument("--port", type=int, default=int(env.get("PORT") or 8000))
    parser.add_argument("--bind", default="127.0.0.1", help="the address to listen on (0.0.0.0 in a container)")
    args = parser.parse_args(argv)
    front = Front(root=Path(env["EVENT_RISK_SESSIONS_DIR"]) if env.get("EVENT_RISK_SESSIONS_DIR") else None,
                  sessions=int(_number(env, "EVENT_RISK_SESSIONS", 4)),
                  idle=_number(env, "EVENT_RISK_IDLE_MINUTES", 120) * 60,
                  life=_number(env, "EVENT_RISK_HOURS", 12) * 3600,
                  body=int(_number(env, "EVENT_RISK_BODY_MB", 50) * (1 << 20)),
                  checks=int(_number(env, "EVENT_RISK_KEY_CHECKS", 12)),
                  exit_idle=_number(env, "EVENT_RISK_EXIT_IDLE_MINUTES", 1) * 60
                  if env.get("EVENT_RISK_EXIT_IDLE_MINUTES") else None,
                  example=env.get("EVENT_RISK_EXAMPLE") or EXAMPLE)
    handler = type("FrontHandler", (Handler,), {"front": front})
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    server.daemon_threads = True

    def empty() -> None:
        print(f"event-risk-host: no session and no request for {_span(front.exit_idle)}; stopping", flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    front.sweeping(min(30.0, front.exit_idle / 4) if front.exit_idle else 30.0, empty)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"event-risk-host: http://{args.bind}:{args.port}/ ({front.most} sessions)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        front.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
