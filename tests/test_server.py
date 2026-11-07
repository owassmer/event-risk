"""The server answers only its own page (SPEC 8): a request naming another host, coming from another origin or site,
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
