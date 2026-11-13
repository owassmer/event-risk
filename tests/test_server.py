"""The server answers only its own page: a request naming another host, coming from another origin or site,
or posting anything but JSON is refused, and a store is opened only from among the stores."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from harness import server


@pytest.fixture
def served(tmp_path):
    page = server.Page(root=tmp_path / "stores")
    handler = type("PageHandler", (server.Handler,), {"page": page})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    port = httpd.server_address[1]

    def ask(method: str, path: str, body=None, **headers) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        conn.request(method, path, body=data, headers={k.replace("_", "-"): v for k, v in headers.items()})
        response = conn.getresponse()
        out = response.status, response.read().decode()
        conn.close()
        return out

    yield ask, port, page
    httpd.shutdown()
    httpd.server_close()


JSON = {"Content_Type": "application/json"}


def test_the_page_and_its_own_requests_are_answered(served):
    ask, port, _ = served
    assert ask("GET", "/")[0] == 200  # a request that names no origin, as a navigation does
    assert ask("GET", "/api/state", Origin=f"http://127.0.0.1:{port}", Sec_Fetch_Site="same-origin")[0] == 200
    assert ask("GET", "/", Host=f"localhost:{port}", Sec_Fetch_Site="none")[0] == 200
    status, body = ask("POST", "/api/stop", {}, Origin=f"http://127.0.0.1:{port}", Sec_Fetch_Site="same-origin", **JSON)
    assert (status, json.loads(body)) == (200, {"ok": True})


@pytest.mark.parametrize("headers", [
    {"Host": "attacker.example:8765"},  # a name that resolves to this machine
    {"Host": "attacker.example"},
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:1"},  # another port of this machine
    {"Sec_Fetch_Site": "cross-site"},
    {"Sec_Fetch_Site": "same-site"},
])
@pytest.mark.parametrize("method,path", [("GET", "/api/conversation"), ("GET", "/api/events"), ("GET", "/"),
                                         ("POST", "/api/send")])
def test_a_request_from_anywhere_else_is_refused(served, headers, method, path):
    ask, _, page = served
    status, body = ask(method, path, {"text": "hello", "files": []} if method == "POST" else None,
                       **headers, **(JSON if method == "POST" else {}))
    assert status == 403 and body == server.REFUSED
    assert page.waiting == [] and not page.busy


@pytest.mark.parametrize("kind", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None])
def test_a_post_that_is_not_json_is_refused(served, kind):
    ask, _, page = served
    status, _ = ask("POST", "/api/send", b'{"text": "hello", "files": []}', **({"Content_Type": kind} if kind else {}))
    assert status == 415
    assert page.waiting == [] and not page.busy


@pytest.mark.parametrize("raw", [b"[1, 2]", b"not json", b'"text"'])
def test_a_post_whose_body_is_not_a_json_object_is_refused(served, raw):
    ask, *_ = served
    assert ask("POST", "/api/stop", raw, **JSON)[0] == 400


@pytest.mark.parametrize("name", ["../outside", "..", "a/b", "/etc", "", "./", "stores/../../outside"])
def test_a_store_is_opened_only_from_among_the_stores(served, tmp_path, name):
    ask, *_ = served
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "store.sqlite").write_bytes(b"")
    status, body = ask("POST", "/api/open", {"name": name}, **JSON)
    assert status == 409 and "no saved conversation" in json.loads(body)["error"]


def test_a_store_beside_the_others_is_found_by_its_name(tmp_path):
    root = tmp_path / "stores"
    (root / "2026-01-02").mkdir(parents=True)
    assert server._among_stores(root, "2026-01-02") == (root / "2026-01-02").resolve()


def test_between_replies_a_render_computes_what_moved_and_during_one_shows_what_is_computed(tmp_path):
    """A minute's refresh between replies moves values the panels must compute (the look-through's dollars, every
    impact): a render then reads with the clients. While a reply is under way it reads only what is computed, so the
    page never computes in the analyst's place."""
    page = server.Page(root=tmp_path / "stores", read_only=True)
    finals = []
    page._render = lambda *, final: finals.append(final)
    page.follow()  # a browser follows the page
    page.busy = True
    page._round()
    page.busy = False
    page._round()
    assert finals == [False, True]


def test_the_panels_render_for_a_browser_that_follows_the_page_and_wait_unseen_with_none(tmp_path):
    """With no browser following the page, as in a scheduled run, a write or a minute's readings render nothing: the
    panels wait unseen, and the first browser to follow is rendered them once."""
    page = server.Page(root=tmp_path / "stores", read_only=True)
    finals = []
    page._render = lambda *, final: finals.append(final)
    page._round()
    page._round()
    assert finals == [] and page.unseen and not page.dirty.is_set()
    told = page.follow()
    assert page.dirty.is_set()  # the render loop renders them for the browser that followed
    page.dirty.clear()
    page._round()
    assert finals == [True] and not page.unseen
    page.hub.unsubscribe(told)
    page._round()
    assert finals == [True] and page.unseen


def test_a_value_being_made_apart_renders_the_panels_again_while_a_browser_follows(tmp_path, monkeypatch):
    """A value made apart is never waited for holding the store's lock; the panels show it as last worked out,
    and the loop renders them again shortly, until a render finds nothing being made, and only for a browser that
    follows the page."""
    monkeypatch.setattr(server, "LATER_RENDER", 0.05)
    page = server.Page(root=tmp_path / "stores", read_only=True)
    rendered = []

    def render(*, final):  # the first render finds a value being made; every later one finds none
        rendered.append(final)
        page.later = len(rendered) == 1

    page._render = render
    told = page.follow()
    page.dirty.clear()
    page._round()  # a value being made: rendered again shortly
    assert page.dirty.wait(2)
    page.dirty.clear()
    page._round()  # nothing being made: no render more
    assert not page.dirty.wait(0.3)
    page.hub.unsubscribe(told)


def test_figures_the_store_could_not_keep_or_render_are_said_to_be_as_of_their_time(tmp_path, monkeypatch):
    """Where the store cannot be written, or the panels cannot be rendered, the page keeps its last
    figures and says they are as of the time they were rendered (Page.state's stale), until a write commits and the
    panels render again; the browser is told when that changes."""
    from harness import render
    from harness.store import Store
    Store.create(tmp_path / "stores" / "s").close()
    page = server.Page(root=tmp_path / "stores", read_only=True)
    told = page.follow()  # a browser follows the page
    page.open(tmp_path / "stores" / "s")
    assert page.state()["stale"] is None and page.figures_at
    while not told.empty():  # what the opening told it
        told.get_nowait()
    rendered = page.figures_at

    def broken(reader):
        raise RuntimeError("a defect")
    monkeypatch.setattr(render, "portfolio_panel", broken)
    page._render(final=True)
    stale = page.state()["stale"]
    assert stale["why"] == "render" and stale["figures_at"] == rendered and stale["since"]
    assert told.get_nowait()["stale"] == stale
    monkeypatch.undo()
    page._render(final=True)
    assert page.state()["stale"] is None and told.get_nowait()["stale"] is None

    store = page.store
    pages = store.db.execute("PRAGMA page_count").fetchone()[0]
    store.db.execute(f"PRAGMA max_page_count = {pages}")
    with pytest.raises(Exception):
        store.put_data("bars", "AAA", {"x": "y" * 200000}, {"url": "u"}, retrieved_at="2026-11-12T15:00:00+00:00")
    page._render(final=True)  # the panels render from what the store holds; what it could not keep is not there
    stale = page.state()["stale"]
    assert stale["why"] == "full" and stale["since"] == store.unwritable["since"] and stale["figures_at"]
    store.db.execute("PRAGMA max_page_count = 1073741823")
    store.note("written again")
    page._render(final=True)
    assert page.state()["stale"] is None


def test_the_readings_interval_is_set_at_start_up_and_nothing_else_changes(monkeypatch):
    """event-risk --readings-every SECONDS: how often, while the as-of is today, the page reads its marks and books
    again between calls; the default is readings.INTERVAL."""
    from harness import readings, server
    served = []
    monkeypatch.setattr(readings, "INTERVAL", 60.0)
    monkeypatch.setattr(server, "load_env", lambda: None)
    monkeypatch.setattr(server, "Page", lambda: "page")
    monkeypatch.setattr(server, "_serve", lambda page, prog, port, browser: served.append((page, port, browser)))
    assert server.main(["--no-browser"]) == 0 and readings.INTERVAL == 60.0
    assert server.main(["--readings-every", "300", "--no-browser"]) == 0 and readings.INTERVAL == 300.0
    assert served == [("page", server.PORT, False)] * 2
    with pytest.raises(SystemExit):
        server.main(["--readings-every", "0"])


def test_the_example_opens_read_only_from_its_packed_store_and_the_shipped_one_stays_as_shipped(tmp_path, monkeypatch):
    """`example` opens a copy of example/ with its store uncompressed (server.PACKED), read only and with no keys, and
    the shipped files are as they were when it ends."""
    import gzip

    from harness import server
    from harness.store import Store
    from massive_data import KNOWLEDGE
    made = Store.create(tmp_path / "run", initial=KNOWLEDGE)
    made.close()
    shipped = tmp_path / "example"
    shipped.mkdir()
    with open(tmp_path / "run" / "store.sqlite", "rb") as raw, gzip.open(shipped / server.PACKED, "wb") as out:
        out.write(raw.read())
    (shipped / "trace.jsonl").write_text("")
    before = {f.name: f.read_bytes() for f in shipped.iterdir()}
    seen = []

    def serve(page, prog, port, browser):
        seen.append((page.read_only, page.store.query("Factor") != [], sorted(p.name for p in page.store.dir.iterdir())))

    monkeypatch.setattr(server, "EXAMPLE", shipped)
    monkeypatch.setattr(server, "_serve", serve)
    assert server.example(["--no-browser"]) == 0
    assert seen and seen[0][0] and seen[0][1] and "store.sqlite" in seen[0][2] and server.PACKED not in seen[0][2]
    assert {f.name: f.read_bytes() for f in shipped.iterdir()} == before
