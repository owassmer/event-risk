"""The Massive client against a stub opener: the key in the header and nowhere else, paging,
refusals recorded as answers, and the two-step constituents fetch (knowledge/DATA.md)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from harness.derive.portfolio import kind_of

from harness.store import Store
from harness.venues.massive import Massive, MassiveError

KEY = "test-key-0123456789abcdef"


class Response(io.BytesIO):
    def __init__(self, status: int, body: dict):
        super().__init__(json.dumps(body).encode())
        self.status = status


class Opener:
    """Answers by path and query; records each request's URL and headers."""

    def __init__(self, routes):
        self.routes = routes
        self.seen: list[tuple[str, dict]] = []

    def __call__(self, request, timeout=None):
        self.seen.append((request.full_url, dict(request.header_items())))
        parts = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        status, body = self.routes(parts.path, query)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))
        return Response(status, body)


def client(routes):
    opener = Opener(routes)
    return Massive(KEY, opener=opener, pause=0), opener


def test_the_key_travels_in_the_header_and_no_recorded_request_carries_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", KEY)

    def routes(path, query):
        if query.get("cursor") == "p2":
            return 200, {"status": "OK", "results": [{"t": 2, "c": 2.0}]}
        return 200, {"status": "OK", "results": [{"t": 1, "c": 1.0}],
                     "next_url": "https://api.massive.com/v2/aggs/ticker/SPY/range/1/day/a/b?cursor=p2"}

    massive, opener = client(routes)
    got = massive.fetchers()["bars"]("SPY", ("2024-09-25", "2026-11-04"))
    assert [r["c"] for r in got["payload"]["results"]] == [1.0, 2.0] and got["request"]["pages"] == 2
    assert all(headers["Authorization"] == f"Bearer {KEY}" for _, headers in opener.seen)
    assert all(KEY not in url for url, _ in opener.seen)
    store = Store.create(tmp_path / "scratch-massive")
    store.put_data("bars", "SPY", got["payload"], got["request"], retrieved_at=got["retrieved_at"], range=got["range"])
    assert KEY not in json.dumps([tuple(r) for r in store.db.execute("SELECT * FROM data")])
    store.close()


def test_a_refusal_a_missing_ticker_and_an_unreadable_one_are_answers_recorded_as_data():
    def routes(path, query):
        if path.endswith("/NOPE"):
            return 404, {"status": "NOT_FOUND", "message": "Ticker not found."}
        if path.endswith("/3750%20HK"):  # a fund's local listing, as Massive answers it
            return 400, {"status": "ERROR", "request_id": "x", "error": "Invalid ticker: 3750 HK"}
        return 403, {"status": "NOT_AUTHORIZED", "message": "You are not entitled to this data."}

    massive, _ = client(routes)
    missing = massive.ticker("NOPE", "2026-11-04")["payload"]
    invalid = massive.ticker("3750 HK", "2026-11-04")["payload"]
    refused = massive.bars("X:BTCUSD", "2026-11-03", "2026-11-04", timespan="hour")["payload"]
    assert missing["status"] == "NOT_FOUND" and refused["status"] == "NOT_AUTHORIZED"
    assert invalid == {"status": "INVALID", "http_status": 400, "error": "Invalid ticker: 3750 HK"}
    assert kind_of(invalid) == kind_of(missing) == "other"  # neither a name nor a fund: it maps to the residual
    assert refused["error"] == "You are not entitled to this data."


def test_throttling_is_waited_out_and_a_server_that_keeps_failing_raises():
    answers = iter([(429, {})] * 5 + [(200, {"status": "OK", "results": {"type": "CS"}})])
    massive, opener = client(lambda path, query: next(answers))
    assert massive.ticker("NVDA", "2026-11-04")["payload"]["results"]["type"] == "CS" and len(opener.seen) == 6
    failing, _ = client(lambda path, query: (502, {}))
    with pytest.raises(MassiveError):
        failing.ticker("NVDA", "2026-11-04")


def test_constituents_are_the_rows_of_the_latest_processing_at_the_latest_effective_date_on_or_before_asof():
    def row(effective, processed, weight):
        return {"constituent_ticker": "NVDA", "weight": weight, "effective_date": effective, "processed_date": processed}
    # one effective date processed again each day
    rows = [row("2026-11-03", "2026-11-04", 0.08), row("2026-11-03", "2026-11-03", 0.079),
            row("2026-10-30", "2026-11-04", 0.07)]

    def routes(path, query):
        assert path == "/etf-global/v1/constituents" and query["composite_ticker"] == "QQQ"
        if query.get("sort") == "processed_date.desc":
            assert query["effective_date.lte"] == "2026-11-04"
            return 200, {"status": "OK", "results": rows}
        return 200, {"status": "OK", "results": [r for r in rows if r["effective_date"] == query["effective_date"]
                                                 and r["processed_date"] == query.get("processed_date", r["processed_date"])]}

    massive, opener = client(routes)
    got = massive.constituents("QQQ", "2026-11-04")
    assert (got["payload"]["effective_date"], got["payload"]["processed_date"]) == ("2026-11-03", "2026-11-04")
    assert got["payload"]["results"] == [rows[0]] and got["range"] == ("2026-11-04", "2026-11-04")
    assert len(opener.seen) == 2 and got["request"]["pages"] == 2


def test_dividends_are_asked_by_ticker_and_ex_date_range_and_every_page_is_read():
    def routes(path, query):
        assert path == "/stocks/v1/dividends"
        if query.get("cursor") == "p2":
            return 200, {"status": "OK", "results": [{"id": "E2", "ex_dividend_date": "2026-11-02",
                                                      "split_adjusted_cash_amount": 0.254426}]}
        return 200, {"status": "OK", "results": [{"id": "E1", "ex_dividend_date": "2026-10-01",
                                                  "split_adjusted_cash_amount": 0.241001}],
                     "next_url": "https://api.massive.com/stocks/v1/dividends?cursor=p2"}

    massive, opener = client(routes)
    got = massive.fetchers()["dividends"]("SHY", ("2024-09-25", "2026-11-04"))
    first = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(opener.seen[0][0]).query))
    assert first == {"ticker": "SHY", "ex_dividend_date.gte": "2024-09-25", "ex_dividend_date.lte": "2026-11-04",
                     "sort": "ex_dividend_date.asc", "limit": "5000"}
    assert [r["id"] for r in got["payload"]["results"]] == ["E1", "E2"]
    assert got["range"] == ("2024-09-25", "2026-11-04") and got["request"]["pages"] == 2
