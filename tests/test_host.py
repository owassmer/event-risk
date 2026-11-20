"""The product hosted (harness.host): one child for each visitor behind the front, on the visitor's own key.

Anthropic is stood in for by a local server that accepts one key (GOOD) on GET /v1/models, the request the front
asks a key with, and refuses every message, so the key check runs as written and nothing leaves this machine. The
session rules run against a small child that takes the product's command line and keeps its guard (CHILD); the last
test runs the product itself behind the front, sends it a message with a file, and reads everything the session wrote
and printed for the key.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

from harness import host

GOOD = "sk-ant-api03-" + "Q" * 40 + "-visitor"
WITHOUT = "claude-opus-5-5"  # a model GOOD has no access to
CHILD = r'''
import argparse, hashlib, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
a = argparse.ArgumentParser(); a.add_argument("--port", type=int); a.add_argument("--no-browser", action="store_true")
a.add_argument("--stores"); args = a.parse_args()
stores = Path(args.stores)

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *x): pass
    def send(self, status, body, kind="application/json"):
        self.send_response(status); self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def refused(self):  # the product's guard: its own host, and an origin, where one is given, of that host
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host != f"127.0.0.1:{args.port}" or (origin is not None and origin != f"http://{host}")
    def do_GET(self):
        if self.refused(): return self.send(403, b"refused", "text/plain")
        if self.path == "/":
            return self.send(200, b"<!doctype html><html><head><title>t</title></head><body><p>the page</p></body></html>",
                             "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self.send(200, json.dumps({"busy": (stores / "busy").exists()}).encode())
        if self.path == "/api/echo":
            h = self.headers
            return self.send(200, json.dumps({"child": str(stores), "host": h.get("Host"), "origin": h.get("Origin"),
                "cookie": h.get("Cookie"), "site": h.get("Sec-Fetch-Site"), "env": sorted(os.environ),
                "key": hashlib.sha256(os.environ.get("ANTHROPIC_API_KEY", "").encode()).hexdigest(),
        "model": os.environ.get("EVENT_RISK_MODEL")}).encode())
        if self.path == "/api/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(b"data: 1\n\n"); self.wfile.flush()
            time.sleep(3); self.wfile.write(b"data: 2\n\n"); self.wfile.flush()
            self.close_connection = True
            return
        if self.path == "/api/die":
            self.send(200, b"{}"); self.wfile.flush(); os._exit(1)
        self.send(404, b"{}")
    def do_POST(self):
        if self.refused(): return self.send(403, b"refused", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        self.send(200, json.dumps({"bytes": len(self.rfile.read(n)), "origin": self.headers.get("Origin")}).encode())

ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
'''


class Anthropic(BaseHTTPRequestHandler):
    """Anthropic as the front and a child meet it: a key is good where it is GOOD, and can use every model but
    WITHOUT; every message is refused."""
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.seen.append(("GET", self.path.split("?")[0], self.headers.get("x-api-key")))
        if self.path.startswith("/v1/models/") and self.headers.get("x-api-key") == GOOD:
            model = self.path.split("/")[-1]
            if model == WITHOUT:
                return self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": f"model: {model}"}})
            return self._send(200, {"type": "model", "id": model, "max_tokens": 128000})
        self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.seen.append(("POST", self.path.split("?")[0], None))
        self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})


def serve(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def anthropic():
    Anthropic.seen = []
    server = serve(Anthropic)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class Visitor:
    """A browser at the front: its cookie, and requests as the page makes them."""

    def __init__(self, port: int):
        self.port, self.cookie = port, None

    def ask(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = {"Host": f"127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin", **(headers or {})}
        if method == "POST" and "Origin" not in sent:
            sent["Origin"] = f"http://127.0.0.1:{self.port}"
        if self.cookie:
            sent["Cookie"] = f"{host.COOKIE}={self.cookie}"
        conn.request(method, path, body, {k: v for k, v in sent.items() if v is not None})
        answer = conn.getresponse()
        data = answer.read()
        conn.close()
        for k, v in answer.getheaders():
            if k.lower() == "set-cookie" and v.startswith(f"{host.COOKIE}="):
                value = v.split(";")[0].split("=", 1)[1]
                self.cookie = value or None
        return answer, data

    def start(self, key: str, headers: dict | None = None, model: str | None = None):
        form = {"key": key, **({"model": model} if model else {})}
        return self.ask("POST", "/_host/start", urlencode(form).encode(),
                        {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})

    def json(self, path: str):
        answer, data = self.ask("GET", path)
        return answer.status, json.loads(data)


@pytest.fixture
def front(tmp_path, anthropic):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    made = []

    def make(**kw):
        env = {"PATH": os.environ.get("PATH", ""), "MASSIVE_API_KEY": "m" * 24, "UNRELATED_SECRET": "u" * 24}
        f = host.Front(command=[sys.executable, str(script)], root=tmp_path / "sessions", env=env,
                       anthropic=anthropic, **kw)
        server = serve(type("H", (host.Handler,), {"front": f}))
        made.append((f, server))
        return f, server.server_address[1]

    yield make
    for f, server in made:
        server.shutdown()
        f.close()


def folders(f: host.Front) -> list[Path]:
    return sorted(p for p in f.root.iterdir() if host.FOLDER.fullmatch(p.name))


def test_the_gate_and_a_key_anthropic_refuses(front):
    f, port = front()
    v = Visitor(port)
    answer, page = v.ask("GET", "/")
    assert answer.status == 200 and b"What could move your portfolio?" in page and b"Anthropic API key" in page
    answer, page = v.start("sk-ant-api03-" + "z" * 40)
    assert b"Anthropic did not accept that key." in page and v.cookie is None and not folders(f)
    assert Anthropic.seen == [("GET", "/v1/models/claude-fable-5-2", "sk-ant-api03-" + "z" * 40)]
    answer, page = v.start("not a key\r\nX-Injected: 1")  # nothing of another shape is sent anywhere
    assert b"Anthropic did not accept that key." in page and len(Anthropic.seen) == 1
    assert b"zzzz" not in page  # the key is never written back


def test_a_good_key_starts_a_child_with_the_key_and_the_environment_the_front_chooses(front):
    f, port = front()
    v = Visitor(port)
    answer, _ = v.start(GOOD)
    assert answer.status == 303 and answer.getheader("Location") == "/" and v.cookie
    flags = answer.getheader("Set-Cookie")
    assert "HttpOnly" in flags and "SameSite=Lax" in flags and "Secure" not in flags
    [s] = f.sessions.values()
    status, echo = v.json("/api/echo")
    assert status == 200 and echo["key"] == hashlib.sha256(GOOD.encode()).hexdigest()
    own = {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}  # what the platform and the child's interpreter set themselves
    # the visitor's key, that the page is hosted, and what the host passes; not the host's UNRELATED_SECRET
    assert set(echo["env"]) - own == {"ANTHROPIC_API_KEY", "EVENT_RISK_HOSTED", "EVENT_RISK_MODEL", "PATH",
                                      "MASSIVE_API_KEY"}
    assert echo["model"] == "claude-fable-5-2"  # the gate's first, chosen where the visitor picks no other
    assert (echo["host"], echo["origin"], echo["cookie"], echo["site"]) == (f"127.0.0.1:{s.port}", None, None,
                                                                           "same-origin")
    answer, page = v.ask("GET", "/")
    assert b"<p>the page</p>" in page and b'id="host-line"' in page and b"End session" in page
    answer, body = v.ask("POST", "/api/send", b'{"text": "hi"}', {"Content-Type": "application/json"})
    assert json.loads(body) == {"bytes": 14, "origin": f"http://127.0.0.1:{s.port}"}  # the origin the child expects
    behind = Visitor(port)  # behind a proxy that ends HTTPS: the cookie is Secure, and the origin is https
    answer, _ = behind.start(GOOD, {"X-Forwarded-Proto": "https", "Origin": f"https://127.0.0.1:{port}"})
    assert answer.status == 303 and "Secure" in answer.getheader("Set-Cookie")


def test_a_request_from_elsewhere_is_refused(front):
    f, port = front()
    v = Visitor(port)
    v.start(GOOD)
    for headers in ({"Origin": "http://elsewhere.example"}, {"Origin": None}):
        answer, _ = v.ask("POST", "/api/send", b"{}", {"Content-Type": "application/json", **headers})
        assert answer.status == 403
    answer, _ = v.start(GOOD, {"Origin": "http://elsewhere.example"})
    assert answer.status == 403 and len(f.sessions) == 1
    answer, _ = v.ask("GET", "/api/echo", headers={"Sec-Fetch-Site": "cross-site"})
    assert answer.status == 403
    answer, _ = v.ask("GET", "/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})
    assert answer.status == 200  # a link from another site opens the page


def test_two_sessions_each_reach_only_their_own_child(front):
    f, port = front()
    a, b = Visitor(port), Visitor(port)
    a.start(GOOD)
    b.start(GOOD)
    assert a.cookie != b.cookie and len(folders(f)) == 2
    assert a.json("/api/echo")[1]["child"] != b.json("/api/echo")[1]["child"]
    stranger = Visitor(port)
    stranger.cookie = a.cookie[:-2] + ("AA" if not a.cookie.endswith("AA") else "BB")
    answer, body = stranger.ask("GET", "/api/echo")
    assert answer.status == 409 and json.loads(body) == {"error": host.GONE}
    answer, page = stranger.ask("GET", "/")
    assert b"Your session has ended." in page and stranger.cookie is None
    for path in (f"/{a.cookie}/api/echo", f"/api/echo?session={a.cookie}"):  # no path names a child
        answer, _ = Visitor(port).ask("GET", path)
        assert answer.status == 409


def test_the_model_the_visitor_chooses_is_checked_with_the_key_and_given_to_the_child_as_one_of_those_offered(front):
    f, port = front()
    v = Visitor(port)
    answer, page = v.ask("GET", "/")
    assert [m for m, _, _ in host.MODELS] == ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5",
                                              "claude-haiku-5-5"]
    assert b'value="claude-fable-5-2" data-access="The key needs access to Claude Fable 5.2, and to Claude Haiku 5.5 ' \
           b'for web search." checked>' in page
    answer, page = v.start(GOOD, model=WITHOUT)
    assert b"Your key can&#x27;t use Claude Opus 5.5. Choose another model, or a key that has access to it." in page
    assert b'value="claude-opus-5-5" data-access="The key needs access to Claude Opus 5.5, and to Claude Haiku 5.5 ' \
           b'for web search." checked>' in page and b"Claude Opus 5.5, and to" in page  # the choice kept, said again
    assert v.cookie is None and not folders(f)
    asked = len(Anthropic.seen)
    answer, page = v.start(GOOD, model="claude-fable-9; rm -rf /")
    assert b"Choose one of the models offered." in page and len(Anthropic.seen) == asked and not folders(f)
    answer, _ = v.start(GOOD, model="claude-sonnet-5-5")
    assert answer.status == 303 and Anthropic.seen[-1][:2] == ("GET", "/v1/models/claude-sonnet-5-5")
    assert v.json("/api/echo")[1]["model"] == "claude-sonnet-5-5"
    assert host.access("claude-haiku-5-5") == "The key needs access to Claude Haiku 5.5."


def test_a_front_that_exits_when_empty_counts_from_its_last_request_or_session(front):
    f, port = front(exit_idle=0.3)
    time.sleep(0.35)
    assert f.empty_for() >= 0.3
    v = Visitor(port)
    v.ask("GET", "/")
    assert f.empty_for() < 0.3  # a request starts it over
    v.start(GOOD)
    time.sleep(0.35)
    assert f.empty_for() == 0.0  # a session alive keeps the front up, whether or not a page of it is open
    v.ask("POST", "/_host/end")
    assert 0.0 < f.empty_for() < 0.3


def test_a_front_with_an_exit_idle_time_exits_cleanly_once_empty(tmp_path):
    import subprocess
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env.update(EVENT_RISK_EXIT_IDLE_MINUTES="0.01", EVENT_RISK_SESSIONS_DIR=str(tmp_path / "sessions"))
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "harness.host", "--port", "0"], env=env, capture_output=True,
                          text=True, timeout=30, cwd=Path(host.__file__).parent.parent)
    assert done.returncode == 0 and "no session and no request for" in done.stdout and "stopping" in done.stdout
    assert time.monotonic() - began < 20


def test_keys_are_checked_with_anthropic_at_most_so_many_a_minute_for_the_whole_front(front):
    f, port = front(checks=2)
    for n in range(2):
        assert b"Anthropic did not accept that key." in Visitor(port).start(f"sk-ant-api03-{n}" + "z" * 40)[1]
    answer, page = Visitor(port).start(GOOD)
    assert answer.status == 429 and b"Too many keys have been checked here in the last minute." in page
    assert len(Anthropic.seen) == 2 and not folders(f)  # the third was never asked
    f.checked[0] -= 60  # a minute on, the first check no longer counts
    assert Visitor(port).start(GOOD)[0].status == 303 and len(Anthropic.seen) == 3


def test_the_ceiling_starts_nothing(front):
    f, port = front(sessions=1)
    Visitor(port).start(GOOD)
    asked = len(Anthropic.seen)
    answer, page = Visitor(port).start(GOOD)
    assert answer.status == 503 and b"Every session is in use just now." in page
    assert len(folders(f)) == 1 and len(Anthropic.seen) == asked  # nothing asked of Anthropic either


def test_the_event_stream_flows_as_it_arrives(front):
    f, port = front()
    v = Visitor(port)
    v.start(GOOD)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{port}", "Cookie": f"{host.COOKIE}={v.cookie}"})
    began = time.monotonic()
    answer = conn.getresponse()
    assert answer.getheader("Content-Type") == "text/event-stream"
    assert answer.read1(100) == b"data: 1\n\n" and time.monotonic() - began < 2.5  # before the child's second, at 3 s
    conn.close()


def test_a_request_past_the_body_ceiling(front):
    f, port = front(body=1 << 20)
    v = Visitor(port)
    v.start(GOOD)
    answer, body = v.ask("POST", "/api/send", b"x" * ((1 << 20) + 1), {"Content-Type": "application/json"})
    assert answer.status == 413 and json.loads(body)["error"] == "A message with its files can be at most 1 MB."


def test_ending_a_session_stops_its_child_and_deletes_its_folder(front):
    f, port = front()
    v = Visitor(port)
    v.start(GOOD)
    [s] = f.sessions.values()
    cookie = v.cookie
    answer, _ = v.ask("POST", "/_host/end")
    assert answer.status == 303 and answer.getheader("Location") == "/?ended" and v.cookie is None
    assert s.proc.poll() is not None and not s.folder.exists() and not f.sessions
    assert b"Your session has ended, and its data is deleted." in v.ask("GET", "/?ended")[1]
    v.cookie = cookie
    answer, page = v.ask("GET", "/")
    assert b"Your session has ended, and its data is deleted." in page
    assert v.ask("GET", "/_host/alive")[0].status == 410


def test_a_child_that_stops_leaves_the_visitor_at_the_gate(front):
    f, port = front()
    v = Visitor(port)
    v.start(GOOD)
    [s] = f.sessions.values()
    v.ask("GET", "/api/die")
    s.proc.wait(10)
    cookie = v.cookie
    answer, body = v.ask("GET", "/api/state")
    assert answer.status == 409 and json.loads(body) == {"error": host.GONE} and not s.folder.exists()
    v.cookie = cookie
    answer, page = v.ask("GET", "/")
    assert answer.status == 200 and b"Your session stopped unexpectedly, and its data was deleted." in page
    assert b"Traceback" not in page


def test_idle_ends_a_session_only_while_no_page_is_open_and_the_analyst_is_not_working(front):
    f, port = front(idle=0.5)
    v = Visitor(port)
    v.start(GOOD)
    [s] = f.sessions.values()
    (s.folder / "stores" / "busy").touch()  # the analyst working
    time.sleep(0.6)
    f.sweep()
    assert f.sessions
    (s.folder / "stores" / "busy").unlink()
    s.open = 1  # a page of it open: its event stream under way
    f.sweep()
    assert f.sessions
    s.open = 0
    f.sweep()
    assert not f.sessions and not s.folder.exists()
    answer, page = v.ask("GET", "/")
    assert f.line("idle").encode() in page and b"with no page open, and its data was deleted." in page


def test_a_session_ends_at_its_life_whatever_it_is_doing(front):
    f, port = front(life=0.3)
    v = Visitor(port)
    v.start(GOOD)
    [s] = f.sessions.values()
    (s.folder / "stores" / "busy").touch()
    s.open = 1
    time.sleep(0.4)
    f.sweep()
    assert not f.sessions and not s.folder.exists()


def test_a_front_that_restarts_clears_what_the_last_left_and_sends_its_visitors_to_the_gate(front, tmp_path):
    f, port = front()
    v = Visitor(port)
    v.start(GOOD)
    [s] = f.sessions.values()
    other = tmp_path / "sessions" / "keep-me"
    other.mkdir()
    s.proc.kill()  # the front gone without its children's end
    s.proc.wait(10)
    g, port2 = front()
    assert not s.folder.exists() and other.exists()
    v.port = port2
    answer, page = v.ask("GET", "/")
    assert b"Your session has ended." in page and v.cookie is None


def test_the_product_behind_the_front_writes_and_prints_nothing_of_the_key(tmp_path, anthropic, capfd):
    """The product itself as the child: its guard accepts what the front presents, the page carries the session's line,
    and a message with a file reaches Anthropic (here, the stand-in, which refuses it). Then every file the session
    wrote, and everything the front and the child printed, is read for the key."""
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env["ANTHROPIC_BASE_URL"] = anthropic  # the child's client to the stand-in (the SDK reads it)
    f = host.Front(root=tmp_path / "sessions", env=env, anthropic=anthropic,
                   passed=host.PASSED + ("ANTHROPIC_BASE_URL",))
    server = serve(type("H", (host.Handler,), {"front": f}))
    try:
        v = Visitor(server.server_address[1])
        assert v.start(GOOD)[0].status == 303
        [s] = f.sessions.values()
        answer, page = v.ask("GET", "/")
        assert answer.status == 200 and b'id="composer"' in page and b'id="host-line"' in page
        status, state = v.json("/api/state")
        assert status == 200 and state["busy"] is False
        pdf = base64.b64encode(b"%PDF-1.4\n% holdings\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n").decode()
        message = json.dumps({"text": "AAPL 10 shares", "files": [{"name": "statement.pdf", "data": pdf}]}).encode()
        answer, body = v.ask("POST", "/api/send", message, {"Content-Type": "application/json"})
        assert answer.status == 200, body
        until = time.monotonic() + 60
        while time.monotonic() < until:
            status, state = v.json("/api/state")
            if not state["busy"] and not state["waiting"] and state.get("error"):
                break
            time.sleep(0.3)
        assert state.get("error") and any(m == "POST" and p == "/v1/messages" for m, p, _ in Anthropic.seen)
        written = [p for p in s.folder.rglob("*") if p.is_file()]
        assert any(p.name == "trace.jsonl" for p in written) and any(p.suffix == ".pdf" for p in written)
        secret = GOOD.encode()
        assert not [p for p in written if secret in p.read_bytes()]
        v.ask("POST", "/_host/end")
        assert not s.folder.exists() and s.proc.poll() is not None
    finally:
        server.shutdown()
        f.close()
    printed = capfd.readouterr()
    assert GOOD not in printed.out and GOOD not in printed.err
