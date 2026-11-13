"""The web's reads survive a dropped connection (harness.web): a call to the API, and a page's direct download, are
read again when the network fails or the connection drops or is cut short mid-answer, as the venues' clients are
(harness.venues.http.TRANSIENT); a site's refusal is not read again."""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

from harness import web as web_module
from harness.web import Web, WebError


class Response(io.BytesIO):
    def __init__(self, body: bytes, kind: str = "application/json"):
        super().__init__(body)
        self.headers = {"content-type": kind}

    def geturl(self):
        return "https://example.com/page"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Cut(Response):
    """An answer whose body ends before its length: the read raises IncompleteRead."""

    def read(self, *args):
        raise http.client.IncompleteRead(b"{\"par", 120)


def opener(*answers):
    """Each call takes the next answer: an exception raised, or a response returned."""
    calls = []

    def open_(request, timeout=None):
        calls.append(request.full_url)
        answer = answers[len(calls) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer
    open_.calls = calls
    return open_


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(web_module.time, "sleep", lambda s: None)


OK = json.dumps({"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}).encode()


@pytest.mark.parametrize("dropped", [http.client.RemoteDisconnected("Remote end closed connection without response"),
                                     ConnectionResetError(54, "Connection reset by peer")])
def test_a_call_whose_connection_drops_is_made_again(dropped):
    o = opener(dropped, Response(OK))
    assert Web(key="k", opener=o)._call({"model": "m", "max_tokens": 1, "messages": []})["stop_reason"] == "end_turn"
    assert len(o.calls) == 2


def test_a_call_whose_answer_is_cut_short_is_made_again():
    o = opener(Cut(b""), Response(OK))
    assert Web(key="k", opener=o)._call({"model": "m", "max_tokens": 1, "messages": []})["stop_reason"] == "end_turn"
    assert len(o.calls) == 2


def test_a_call_that_keeps_dropping_fails_once_past_its_attempts():
    drop = http.client.RemoteDisconnected("Remote end closed connection without response")
    o = opener(drop, drop, drop)
    with pytest.raises(WebError, match="could not be reached"):
        Web(key="k", opener=o)._call({"model": "m", "max_tokens": 1, "messages": []})
    assert len(o.calls) == 3


def test_a_direct_download_whose_connection_drops_is_read_again():
    page = Response(b"<html><title>T</title><p>Body</p></html>", "text/html")
    o = opener(http.client.RemoteDisconnected("Remote end closed connection without response"), page)
    found = Web(key="k", opener=o).fetch("http://localhost/page")  # a private host: downloaded directly
    assert found["via"] == "direct" and "Body" in found["text"] and len(o.calls) == 2


def test_a_direct_download_refused_by_the_site_is_not_read_again():
    refused = urllib.error.HTTPError("http://localhost/page", 404, "Not Found", {}, None)
    o = opener(refused)
    with pytest.raises(WebError, match="no such page"):
        Web(key="k", opener=o).fetch("http://localhost/page")
    assert len(o.calls) == 1


def test_a_direct_download_that_drops_twice_fails():
    drop = ConnectionResetError(54, "Connection reset by peer")
    o = opener(drop, drop)
    with pytest.raises(WebError, match="could not be read"):
        Web(key="k", opener=o).fetch("http://localhost/page")
    assert len(o.calls) == 2


def test_a_search_results_date_is_a_date_an_age_taken_back_from_the_moment_the_search_answered():
    from datetime import datetime, timezone

    from harness.web import dated

    late = datetime(2026, 3, 31, 1, 30, tzinfo=timezone.utc)
    assert dated("65 days ago", late) == "2026-01-25"
    assert dated("3 hours ago", late) == "2026-03-30"  # an age that crosses midnight, in UTC
    assert dated("1 day ago", late) == "2026-03-30" and dated("2 weeks ago", late) == "2026-03-17"
    assert dated("1 month ago", late) == "2026-02-28"  # a month back by the calendar, to the month's last day
    assert dated("2 years ago", late) == "2024-03-31"
    assert dated("April 30, 2025", late) == "2025-04-30" and dated("2025-04-30", late) == "2025-04-30"
    assert dated(None, late) is None and dated("recently", late) is None and dated("", late) is None

    o = opener(Response(json.dumps({"stop_reason": "end_turn", "content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
            {"type": "web_search_result", "url": "https://a.org", "title": "A", "page_age": "0 days ago"},
            {"type": "web_search_result", "url": "https://b.org", "title": "B", "page_age": None}]}]}).encode()))
    found = Web(key="k", opener=o).search("q")["results"]
    today = datetime.now(timezone.utc).date().isoformat()
    assert [(r["url"], r["date"]) for r in found] == [("https://a.org", today), ("https://b.org", None)]
    assert all("page_age" not in r for r in found)
