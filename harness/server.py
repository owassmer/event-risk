"""The page's server: one conversation over one store at a time, served to the browser.

`event-risk` starts it and opens the page on a new conversation; the store is created when the first message is
sent, named for its date, and renamed for its as-of once the analyst has priced the portfolio. `event-risk --store
stores/{name}` opens a store and its conversation, readable with no keys and continued with the Claude key.
`example` opens the finished conversation the repository ships, example/, read only: a copy of it in a temporary
directory, so the shipped store never changes, with no keys read and no judge, venue, or data client built, so
nothing is fetched or asked and every number reads from the store's saved data and its cache.

The analyst works in its own thread. Its events, the first read's stages, and each change to the store reach the
browser over server-sent events; the panels are rendered here, by render.py, under the store's lock, between the
analyst's calls. While it works they render from what the store holds; when a reply ends they render once more
with the judge and the data clients. A message sent while it works waits, and goes when the reply ends.

The server answers only its own page. It listens on 127.0.0.1 alone. Every request must name it by the address it is
served at, in `Host`, so a name that resolves to this machine reaches nothing. Where the browser says where a request
comes from, in `Origin` and `Sec-Fetch-Site`, it must be the page itself. Current browsers send `Sec-Fetch-Site` on
every request to this address, so a page on another site, or on another port of this machine, can neither drive the
analyst nor read a store. A POST must be JSON, `Content-Type: application/json`, a type no other site can send here
without a preflight, which this server never answers. A request that says nothing of where it comes from, as a
program on this machine sends, is answered: such a program can read the stores directly anyway. A store is opened
only from among the stores beside the others.
(https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Origin,
https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site,
https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS#simple_requests)
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness import claude, readings, render
from harness.analyst import ROOT, Analyst, FileRefused, keep_file, live, load_env, open_store
from harness.store import STORE_FILE, Store, StoreError
from harness.trace import Trace
from harness.venues import Venues
from harness.venues.polymarket import NY
from harness.world import World

WEB = ROOT / "web"
STORES = ROOT / "stores"
EXAMPLE = ROOT / "example"  # the one finished conversation the repository ships
READ_ONLY = "This is a finished example, read only. Start your own with uv run event-risk."
PORT = 8765
KEYS = {  # each key, and what the page cannot do without it: one plain line each
    "ANTHROPIC_API_KEY": "No Claude key (ANTHROPIC_API_KEY in .env): the analyst can't reply.",
    "BASETEN_API_KEY": "No Baseten key (BASETEN_API_KEY in .env): new readings go unchecked; every number is still "
                       "worked out.",
    "MASSIVE_API_KEY": "No Massive key (MASSIVE_API_KEY in .env): a new portfolio can't be priced, and the companies' "
                       "news and records can't be read.",
}
LABELLED = ("from_id", "to_id", "id", "proposition_id", "source_id")  # the ids a call's input names
WRITES = ("add", "link", "note", "edit", "remove", "search_markets", "search_web", "search_company")
PREVIEW = 600  # a call's result as the work line first shows it; all of it opens on request
SITES = ("same-origin", "none")  # Sec-Fetch-Site of the page's own requests, and of one the person makes by hand
REFUSED = "This server answers only its own page."


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= PREVIEW else text[:PREVIEW] + "…"


class Hub:
    """The browsers following the page: each gets every event."""

    def __init__(self):
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(q)

    def publish(self, event: dict) -> None:
        with self.lock:
            for q in list(self.clients):
                q.put(event)


class Page:
    """The conversation the page shows: its store, its analyst, and the messages waiting."""

    def __init__(self, root: Path = STORES, *, read_only: bool = False):
        self.root = Path(root)
        self.read_only = read_only  # a finished conversation, read with no keys: nothing is sent, fetched, or asked
        self.hub = Hub()
        self.lock = threading.RLock()
        self.store: Store | None = None
        self.analyst: Analyst | None = None
        self.session: str | None = None
        self.busy = False
        self.created = False  # the store is this page's, made for a new conversation, and named for its as-of
        self.waiting: list[dict] = []
        self.next_id = 1
        self.panels = {"portfolio": "", "ledger": ""}
        self.error: str | None = None
        self.dirty = threading.Event()
        self.ranking = threading.Event()  # readings taken between replies moved values: the events move into rank
        self.venues = None if read_only else Venues()  # the analyst's and the readings' one set of venue clients
        threading.Thread(target=self._render_loop, daemon=True).start()
        if not read_only:
            threading.Thread(target=self._live_loop, daemon=True).start()

    # ---------------------------------------------------------------- keys and state

    def missing_keys(self) -> list[str]:
        """A line for each missing key that stops something the person can do. Without Claude the conversation can
        only be read, so the Claude line is the only one; with it, the Baseten and Massive lines say what the
        analyst's work goes without. A finished example reads no keys, and its one line says how to start one's own."""
        if self.read_only:
            return [READ_ONLY]
        if claude.api_key() is None:
            return [KEYS[claude.KEY]]
        return [line for key, line in KEYS.items() if key != claude.KEY and not os.environ.get(key)]

    def state(self) -> dict:
        with self.lock:
            return {"store": self.store.dir.name if self.store else None, "busy": self.busy,
                    "waiting": [{"id": w["id"], "text": w["text"], "files": [f["name"] for f in w.get("files") or []]}
                                for w in self.waiting if w["op"] == "say"],
                    "missing": self.missing_keys(),
                    "can_reply": not self.read_only and claude.api_key() is not None,
                    "read_only": self.read_only,
                    "session": self.session, "error": self.error,
                    "has_portfolio": bool(self.panels["portfolio"]), "has_ledger": bool(self.panels["ledger"])}

    def _publish_state(self) -> None:
        self.hub.publish({"type": "state", **self.state()})

    # ---------------------------------------------------------------- stores

    def open(self, directory: Path) -> None:
        """Open a store and its conversation: the latest session in its trace."""
        directory = Path(directory)
        if not (directory / STORE_FILE).exists():
            raise StoreError(f"There is no saved conversation in {directory}.")
        with self.lock:
            if self.busy:
                raise StoreError("The analyst is working. Stop the reply first.")
            self._close()
            if self.read_only:  # no judge and no data client: every value reads from what the store holds
                self.store = open_store(directory.name, root=directory.parent, judge=None, fetchers={})
            else:
                self.store = open_store(directory.name, root=directory.parent)
            self.store.progress = self._progress
            sessions = [e["session"] for e in Trace(self.store.trace_path).entries() if e.get("type") == "session"]
            self.session = sessions[-1] if sessions else None
            self.analyst = None
        self._render(final=True)
        self.hub.publish({"type": "conversation"})
        self.hub.publish({"type": "rank"})
        self._publish_state()

    def new(self) -> None:
        if self.read_only:
            raise StoreError(READ_ONLY)
        with self.lock:
            if self.busy:
                raise StoreError("The analyst is working. Stop the reply first.")
            self._close()
            self.panels = {"portfolio": "", "ledger": ""}
        self.hub.publish({"type": "panel", "name": "portfolio", "html": ""})
        self.hub.publish({"type": "panel", "name": "ledger", "html": ""})
        self.hub.publish({"type": "conversation"})
        self._publish_state()

    def _close(self) -> None:
        if self.store is not None:
            with self.store.lock:
                self.store.close()
        self.store, self.analyst, self.session, self.waiting, self.error = None, None, None, [], None
        self.created = False

    def _create(self) -> None:
        """The store a new conversation writes, named for today's date in New York."""
        name = self._free(datetime.now(NY).date().isoformat())
        self.store = open_store(name, root=self.root)
        self.store.progress = self._progress
        self.created = True

    def _free(self, base: str) -> str:
        name, n = base, 1
        while (self.root / name).exists():
            n += 1
            name = f"{base}-{n}"
        return name

    def _named_for_asof(self) -> None:
        """Once the portfolio has its as-of, the store this page made for the conversation is named for it. A store
        opened by name keeps its name."""
        store = self.store
        asof = store.meta("asof") if store and self.created else None
        if not asof or store.dir.name.startswith(asof):
            return
        name = self._free(asof)
        with store.lock:
            store.close()
            store.dir.rename(self.root / name)
        self.store = open_store(name, root=self.root)
        self.store.progress = self._progress
        self.analyst = None  # it continues from the trace on the next message

    def _analyst(self) -> Analyst:
        if self.analyst is None:
            self.analyst = Analyst(World(self.store, self.venues), on_event=self._event, session=self.session)
            self.session = self.analyst.session
        return self.analyst

    # ---------------------------------------------------------------- messages

    def send(self, text: str, files: list[dict]) -> None:
        if self.read_only:
            raise StoreError(READ_ONLY)
        if claude.api_key() is None:
            raise StoreError(KEYS["ANTHROPIC_API_KEY"])
        if not text.strip() and not files:
            raise StoreError("Write a message or drop a file.")
        with self.lock:
            if self.store is None:
                self._create()
            kept = [keep_file(self.store.dir, f["name"], base64.b64decode(f["data"])) for f in files]
            self.waiting.append({"op": "say", "id": self.next_id, "text": text, "files": kept})
            self.next_id += 1
            self._start()
        self._publish_state()

    def edit(self, turn: int, text: str, keep: list[str] | None = None) -> None:
        """Turn `turn`'s message, changed. It keeps the files it was sent with that `keep` names, by their paths in
        files/: the edit box shows each, and the person may remove any."""
        if not text.strip() and not keep:
            raise StoreError("Write a message or keep a file.")
        self._operate({"op": "edit", "turn": turn, "text": text, "keep": list(keep or [])})

    def retry(self, turn: int) -> None:
        self._operate({"op": "retry", "turn": turn})

    def _operate(self, item: dict) -> None:
        if self.read_only:
            raise StoreError(READ_ONLY)
        if claude.api_key() is None:
            raise StoreError(KEYS["ANTHROPIC_API_KEY"])
        with self.lock:
            if self.busy or self.store is None:
                raise StoreError("Stop the reply first.")
            self.waiting.insert(0, {**item, "id": self.next_id})
            self.next_id += 1
            self._start()
        self._publish_state()

    def cancel(self, wid: int) -> None:
        with self.lock:
            self.waiting = [w for w in self.waiting if w["id"] != wid]
        self._publish_state()

    def stop(self) -> None:
        with self.lock:
            analyst = self.analyst if self.busy else None
        if analyst is not None:
            analyst.stop()

    def _start(self) -> None:
        if not self.busy and self.waiting:
            self.busy = True
            self.error = None
            threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        while True:
            with self.lock:
                if not self.waiting:
                    self.busy = False
                    break
                item = self.waiting.pop(0)
                analyst = self._analyst()
            self._publish_state()
            try:
                if item["op"] == "say":
                    analyst.say(item["text"], item["files"])
                elif item["op"] == "edit":
                    sent = analyst.message(item["turn"])["files"]
                    analyst.edit(item["turn"], item["text"], [f for f in sent if f["path"] in item["keep"]])
                else:
                    analyst.retry(item["turn"])
            except Exception as err:  # the reply failed: say so plainly, and keep what was written
                traceback.print_exc()
                with self.lock:
                    self.error = _plain_error(err)
            with self.lock:
                try:
                    self._named_for_asof()
                except Exception:
                    traceback.print_exc()
            if self.error:
                self.hub.publish({"type": "conversation"})
            self._render(final=True)
            self.ranking.clear()
            self.hub.publish({"type": "store"})
            self.hub.publish({"type": "rank"})  # the reply's values are in: the events move into rank
        self._publish_state()

    # ---------------------------------------------------------------- what the analyst does

    def _labels(self, *inputs: Any) -> dict[str, str]:
        """What a member calls each id a call names, for the line that says what the analyst is doing."""
        ids = []
        for given in inputs:
            if not isinstance(given, dict):
                continue
            ids += [given[k] for k in LABELLED if isinstance(given.get(k), str)]
            ids += [x for x in given.get("about_ids") or [] if isinstance(x, str)]
            metric = (given.get("fields") or {}).get("metric") if isinstance(given.get("fields"), dict) else None
            if isinstance(metric, str):
                ids.append(metric)
        store = self.store
        if not ids or store is None:
            return {}
        try:
            with store.lock:
                return render.labels(store, ids)
        except Exception:
            return {}

    def _event(self, event: dict) -> None:
        kind = event["type"]
        out = dict(event)
        if kind in ("call_start", "call"):
            out["labels"] = self._labels(event.get("input"))
        elif kind == "block_stop" and (event.get("block") or {}).get("type") == "tool_use":
            out["labels"] = self._labels(event["block"].get("input"))
        if kind == "call":
            out.pop("result", None)
            out["preview"] = _short(event.get("result"))
            if event["tool"] in WRITES:
                self.dirty.set()
        elif kind in ("user", "rewind"):
            self.dirty.set()
        elif kind == "answer":
            out = {k: event[k] for k in ("type", "session", "turn", "stopped") if k in event}
        if kind == "rewind":
            self.hub.publish({"type": "conversation"})
            return
        self.hub.publish({"type": "analyst", "event": out})

    def _progress(self, event: dict) -> None:
        """A stage of the first read. When a part is ready, the portfolio panel shows it at once."""
        self.hub.publish({"type": "progress", **event})
        if event.get("state") == "done" and event.get("derivation") in ("holdings", "lookthrough", "g"):
            store = self.store
            if store is None:
                return
            try:
                with store.lock, render.cache_only(store):
                    html = render.portfolio_panel(render.Reader(store, pending=True))
            except Exception:
                traceback.print_exc()
                return
            self._panel("portfolio", html)

    # ---------------------------------------------------------------- the panels

    def _panel(self, name: str, html: str) -> None:
        if self.panels.get(name) != html:
            self.panels[name] = html
            self.hub.publish({"type": "panel", "name": name, "html": html})

    def _render(self, *, final: bool) -> None:
        """The panels, rendered. What their values recompute is the page's own reading, after a call or a refresh of
        the prices, so it reports no stages: the work line says what the analyst is doing."""
        store = self.store
        if store is None:
            return
        try:
            with store.lock, _quiet(store):
                if final:
                    reader = render.Reader(store)
                    portfolio = render.portfolio_panel(reader)
                    ledger = render.ledger_panel(reader)
                else:
                    with render.cache_only(store):
                        reader = render.Reader(store, pending=True)
                        portfolio = render.portfolio_panel(reader)
                        ledger = render.ledger_panel(reader)
        except Exception:
            traceback.print_exc()
            return
        self._panel("portfolio", portfolio)
        self._panel("ledger", ledger)

    def _render_loop(self) -> None:
        while True:
            self.dirty.wait()
            time.sleep(0.2)  # the calls of one reply's batch, together
            self.dirty.clear()
            self._render(final=False)
            self.hub.publish({"type": "store"})
            if self.ranking.is_set() and not self.busy:  # between replies, rows move into rank as values move
                self.ranking.clear()
                self.hub.publish({"type": "rank"})

    def _live_loop(self) -> None:
        """While the open store's as-of is today, its latest marks and kept books are read again every minute
        (readings.refresh), during a reply as between replies, so the figures a reply quotes are current when it lands.
        What a reading moves renders; between replies the events move into rank. A store whose as-of has passed, and
        a finished example, which is read only and starts no such loop, are read no more."""
        due = time.monotonic() + readings.INTERVAL
        while True:
            time.sleep(max(0.0, due - time.monotonic()))
            due = time.monotonic() + readings.INTERVAL
            store = self.store
            if store is None:
                continue
            try:
                kept = readings.refresh(store, self.venues)
            except Exception:  # a store closed while its readings were taken, or a read that failed whole
                if store is self.store:
                    traceback.print_exc()
                continue
            if kept and store is self.store:
                self.ranking.set()
                self.dirty.set()

    def view(self, target: str) -> dict:
        store = self.store
        if store is None:
            return {"title": "", "html": ""}
        with store.lock:
            if self.busy:
                with render.cache_only(store):
                    return render.view(store, target, pending=True)
            return render.view(store, target)

    def chips(self, ids: list[str]) -> dict:
        store = self.store
        if store is None:
            return {}
        with store.lock, render.cache_only(store):
            return render.chips(store, ids)

    def download(self) -> tuple[str, str]:
        store = self.store
        if store is None:
            raise StoreError("There are no events yet.")
        with store.lock:
            if self.busy:
                with render.cache_only(store):
                    text = render.ledger_markdown(store)
            else:
                text = render.ledger_markdown(store)
        return f"ledger-{store.dir.name}.md", text

    # ---------------------------------------------------------------- the conversation, from the trace

    def conversation(self) -> dict:
        store = self.store
        if store is None:
            return {"turns": []}
        entries = Trace(store.trace_path).entries()
        shown = turns(entries, self.session)
        inputs = [c.get("input") for t in shown for item in t["items"] if item["kind"] == "work"
                  for c in item["calls"]]
        return {"turns": shown, "store": store.dir.name, "labels": self._labels(*inputs)}

    def call(self, n: int) -> dict:
        store = self.store
        if store is None:
            return {}
        for e in Trace(store.trace_path).entries():
            if e.get("type") == "call" and e.get("call") == n:
                return {k: e.get(k) for k in ("call", "tool", "input", "result", "error")}
        return {}


@contextmanager
def _quiet(store: Store):
    """The store reporting no stages while the page reads it."""
    saved, store.progress = store.progress, None
    try:
        yield
    finally:
        store.progress = saved


def _plain_error(err: Exception) -> str:
    """Why a reply stopped, in one line: the API's own message where it gave one."""
    import anthropic

    if isinstance(err, anthropic.AuthenticationError):
        return f"The Claude key was refused. Check {claude.KEY} in .env."
    if isinstance(err, anthropic.APIConnectionError):
        return "The analyst couldn't be reached. Check the connection and send the message again."
    if isinstance(err, anthropic.APIStatusError):
        body = err.body if isinstance(err.body, dict) else {}
        message = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
        if isinstance(err, (anthropic.RateLimitError, anthropic.InternalServerError)) and not message:
            return "The analyst's model is busy. Send the message again in a moment."
        return f"The analyst couldn't reply on {claude.KEY}: {message or err.message}"
    return f"The reply stopped with an error: {str(err)[:300] or type(err).__name__}"


def turns(entries: list[dict], current: str | None) -> list[dict]:
    """The conversation as the page shows it: each turn's message, then its stretches of work and its words, in
    order. The turns a rewind replaced stay in the trace and are not shown."""
    calls = {e["call"]: e for e in entries if e.get("type") == "call"}
    sessions = list(dict.fromkeys(e["session"] for e in entries if e.get("type") == "session"))
    out: list[dict] = []
    for session in sessions:
        turn: dict | None = None
        uses: dict[str, dict] = {}
        last_at = None
        for e in live(entries, session):
            kind = e["type"]
            if kind == "user":
                turn = {"session": session, "turn": e["turn"], "text": e["text"], "at": e["at"],
                        "files": [{k: f[k] for k in ("name", "path", "media_type")} for f in e.get("files") or []],
                        "items": [], "stopped": False, "editable": session == current}
                out.append(turn)
                last_at = e["at"]
            elif turn is None:
                continue
            elif kind == "assistant":
                for b in e["content"]:
                    if b.get("type") == "text":
                        if b.get("text"):
                            turn["items"].append({"kind": "text", "text": b["text"]})
                        continue
                    work = turn["items"][-1] if turn["items"] and turn["items"][-1]["kind"] == "work" else None
                    if work is None:
                        work = {"kind": "work", "thinking": [], "calls": [], "from": last_at, "to": e["at"]}
                        turn["items"].append(work)
                    work["to"] = e["at"]
                    if b.get("type") == "thinking" and b.get("thinking"):
                        work["thinking"].append(b["thinking"])
                        work["calls"].append({"thinking": len(work["thinking"]) - 1})
                    elif b.get("type") == "tool_use":
                        call = {"tool_use_id": b["id"], "tool": b["name"], "input": b.get("input")}
                        uses[b["id"]] = call
                        work["calls"].append(call)
                last_at = e["at"]
            elif kind == "results":
                for r in e["results"]:
                    call = uses.get(r["tool_use_id"])
                    if call is None:
                        continue
                    if "call" in r:
                        c = calls.get(r["call"]) or {}
                        call.update(call=r["call"], ok="result" in c,
                                    preview=_short(c.get("result", c.get("error"))))
                        if c.get("at"):
                            last_at = c["at"]
                    else:
                        call.update(ok=False, not_run=True, preview=_short(r.get("content")))
                for item in reversed(turn["items"]):
                    if item["kind"] == "work":
                        item["to"] = last_at
                        break
            elif kind == "stop":
                turn["stopped"] = True
                if e.get("partial"):
                    turn["items"].append({"kind": "text", "text": e["partial"], "partial": True})
    return out


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    page: Page
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # quiet: the page is the interface
        return

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict | None:
        """A POST's body, a JSON object; None when it is not one."""
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _refusal(self) -> str | None:
        """Why a request is not the page's own, or None when it is: it names another host than the one the page is
        served at, it says it comes from another origin, or the browser says it comes from another site."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return "host"
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower().rstrip("/") != f"http://{host}":
            return "origin"
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site.strip().lower() not in SITES:
            return "site"
        return None

    def _refuse(self) -> bool:
        """Refuse a request that is not the page's own, saying so: True when it was refused."""
        if self._refusal() is None:
            return False
        self.close_connection = True
        self._send(403, REFUSED.encode(), "text/plain; charset=utf-8")
        return True

    def do_GET(self):
        if self._refuse():
            return
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path
        page = self.page
        try:
            if path == "/":
                return self._static("index.html")
            if path.startswith("/web/"):
                return self._static(path[len("/web/"):])
            if path == "/api/events":
                return self._events()
            if path == "/api/state":
                return self._json(page.state())
            if path == "/api/conversation":
                return self._json(page.conversation())
            if path == "/api/panels":
                return self._json(page.panels)
            if path == "/api/view":
                return self._json(page.view(q.get("id", "")))
            if path == "/api/chips":
                ids = [x for x in q.get("ids", "").split(",") if x]
                return self._json(page.chips(ids))
            if path == "/api/call":
                return self._json(page.call(int(q.get("n", "0"))))
            if path == "/api/stores":
                return self._json(stores(page.root))
            if path == "/api/download":
                name, text = page.download()
                return self._send(200, text.encode(), "text/markdown; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            if path == "/api/file":
                return self._file(q.get("path", ""))
            return self._send(404, b"not found", "text/plain")
        except (StoreError, FileRefused) as err:
            return self._json({"error": getattr(err, "message", str(err))}, 409)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as err:
            traceback.print_exc()
            return self._json({"error": f"{type(err).__name__}: {err}"}, 500)

    def do_POST(self):
        if self._refuse():
            return
        path = urlparse(self.path).path
        page = self.page
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            self.close_connection = True
            return self._json({"error": "The page sends JSON."}, 415)
        try:
            body = self._body()
            if body is None:
                return self._json({"error": "The page sends a JSON object."}, 400)
            if path == "/api/send":
                page.send(body.get("text") or "", body.get("files") or [])
            elif path == "/api/stop":
                page.stop()
            elif path == "/api/edit":
                page.edit(int(body["turn"]), body.get("text") or "", [str(x) for x in body.get("files") or []])
            elif path == "/api/retry":
                page.retry(int(body["turn"]))
            elif path == "/api/cancel":
                page.cancel(int(body["id"]))
            elif path == "/api/new":
                page.new()
            elif path == "/api/open":
                if page.read_only:  # a finished example has no other conversation to open
                    raise StoreError(READ_ONLY)
                page.open(_among_stores(page.root, body.get("name")))
            else:
                return self._send(404, b"not found", "text/plain")
            return self._json({"ok": True})
        except (StoreError, FileRefused) as err:
            return self._json({"error": getattr(err, "message", str(err))}, 409)
        except Exception as err:
            traceback.print_exc()
            return self._json({"error": f"{type(err).__name__}: {err}"}, 500)

    def _static(self, rel: str) -> None:
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():  # a file under web/, and nothing else
            return self._send(404, b"not found", "text/plain")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".woff2":
            kind = "font/woff2"
        if kind.startswith("text/") or kind == "application/javascript":
            kind += "; charset=utf-8"
        self._send(200, target.read_bytes(), kind)

    def _file(self, rel: str) -> None:
        store = self.page.store
        if store is None or not re.fullmatch(r"files/[A-Za-z0-9._-]+", rel):
            return self._send(404, b"not found", "text/plain")
        target = store.dir / rel
        if not target.is_file():
            return self._send(404, b"not found", "text/plain")
        self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def _events(self) -> None:
        q = self.page.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {data}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": still here\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.page.hub.unsubscribe(q)
            self.close_connection = True


def _among_stores(root: Path, name: Any) -> Path:
    """A store beside the others, by its name: a directory directly inside the stores root, and nothing else."""
    name = str(name or "")
    target = (Path(root) / name).resolve()
    if not name or name != Path(name).name or target.parent != Path(root).resolve():
        raise StoreError(f"There is no saved conversation named {name!r}.")
    return target


def stores(root: Path) -> list[dict]:
    """The stores on this machine, newest first, each with its as-of and how many events it watches."""
    out = []
    found = Path(root).iterdir() if Path(root).exists() else []
    for d in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / STORE_FILE
        if not f.is_file():
            continue
        try:
            db = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            asof = db.execute("SELECT value FROM meta WHERE key = 'asof'").fetchone()
            events = db.execute("SELECT count(*) FROM link WHERE relation = 'watches'").fetchone()[0]
            db.close()
        except sqlite3.Error:
            continue
        out.append({"name": d.name, "asof": asof[0] if asof else None, "events": events})
    return out


def _arguments(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true", help="serve the page without opening it")
    return parser


def _serve(page: Page, prog: str, port: int, browser: bool) -> None:
    """Serve the page until interrupted, and open it in the browser unless told not to."""
    handler = type("PageHandler", (Handler,), {"page": page})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"{prog}: {url}" + (f" ({page.store.dir.name})" if page.store else ""), flush=True)
    for line in page.missing_keys():
        print(line, flush=True)
    if browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = _arguments("event-risk", "What could move a portfolio: the page.")
    parser.add_argument("--store", help="open an existing store, stores/{name}")
    args = parser.parse_args(argv)
    load_env()
    page = Page()
    if args.store:
        page.open(Path(args.store) if Path(args.store).exists() else STORES / args.store)
    _serve(page, "event-risk", args.port, not args.no_browser)
    return 0


def example(argv: list[str] | None = None) -> int:
    """The finished conversation the repository ships, read only and with no keys, on a copy in a temporary
    directory that goes when the page stops."""
    args = _arguments("example", "A finished conversation and its ledger, read only.").parse_args(argv)
    if not (EXAMPLE / STORE_FILE).is_file():
        print(f"example: there is no example at {EXAMPLE}", file=sys.stderr)
        return 1
    root = Path(tempfile.mkdtemp(prefix="event-risk-example-"))

    def stop(*_):  # an interrupt or a termination stops the page, and the copy goes with it
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        shutil.copytree(EXAMPLE, root / EXAMPLE.name)
        page = Page(root=root, read_only=True)
        page.open(root / EXAMPLE.name)
        _serve(page, "example", args.port, not args.no_browser)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
