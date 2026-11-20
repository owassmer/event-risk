"""The Massive client against a stub opener: the key in the header and nowhere else, paging,
refusals recorded as answers, and the two-step constituents fetch (knowledge/DATA.md)."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import urllib.error
import urllib.parse
import urllib.request

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


def test_a_funds_holdings_over_a_window_are_each_days_latest_processing_its_net_assets_and_its_option_rows():
    """MATH.md 1.7: each day's rows in their latest processing made by the as-of, a row reported twice counted once; kept,
    the day's net assets over all its rows, and the rows labelled as options or as nothing; asked sorted, since
    unsorted pages over a window can end early."""
    flex = {"constituent_ticker": "4SPY  261231C00006820", "constituent_name": "SPY 12/31/2026 6.82 C",
            "asset_class": None, "market_value": 1000.0, "shares_held": 10.0}
    share = {"constituent_ticker": "AAPL", "asset_class": "Equity", "market_value": 500.0, "shares_held": 2.0}
    cash = {"constituent_ticker": "Cash&Other", "constituent_name": "Cash & Other", "asset_class": "Cash",
            "market_value": 50.0}
    rows = [{**flex, "effective_date": "2026-11-05", "processed_date": "2026-11-05"},
            {**share, "effective_date": "2026-11-05", "processed_date": "2026-11-05"},
            {**flex, "market_value": 990.0, "effective_date": "2026-11-05", "processed_date": "2026-11-06"},  # again
            {**flex, "market_value": 990.0, "effective_date": "2026-11-05", "processed_date": "2026-11-06"},  # twice
            {**share, "effective_date": "2026-11-05", "processed_date": "2026-11-06"},
            {**cash, "effective_date": "2026-11-05", "processed_date": "2026-11-06"},
            {**flex, "constituent_ticker": "", "market_value": 1010.0, "effective_date": "2026-11-06",
             "processed_date": "2026-11-06"}]

    def routes(path, query):
        assert path == "/etf-global/v1/constituents" and query["composite_ticker"] == "PJAN"
        assert (query["effective_date.gte"], query["effective_date.lte"], query["processed_date.lte"]) == \
            ("2026-11-01", "2026-11-06", "2026-11-06") and query["sort"] == "processed_date.asc"
        return 200, {"status": "OK", "results": rows}

    massive, _ = client(routes)
    got = massive.holdings_history("PJAN", "2026-11-01", "2026-11-06")
    days = got["payload"]["days"]
    assert sorted(days) == ["2026-11-05", "2026-11-06"] and got["range"] == ("2026-11-01", "2026-11-06")
    first = days["2026-11-05"]
    assert first["processed_date"] == "2026-11-06" and first["net_assets"] == 990.0 + 500.0 + 50.0
    assert [r["market_value"] for r in first["rows"]] == [990.0]  # the share and the cash in the net assets alone
    from harness.derive.rows import contracts_by_day
    held = contracts_by_day(got["payload"])
    assert held["2026-11-05"]["contracts"] == [{"contract": "O:SPY261231C00006820", "value": 990.0, "quantity": 10.0}]
    # the next day's row names it by its name alone: the same contract, by its terms
    assert held["2026-11-06"]["contracts"] == [{"contract": "O:SPY261231C00006820", "value": 1010.0, "quantity": 10.0}]


def test_dividends_are_asked_by_ticker_and_ex_date_range_and_every_page_is_read():
    def routes(path, query):
        if path.endswith("/events"):  # its ticker events: SHY has not changed its ticker
            return 404, {"status": "NOT_FOUND"}
        assert path == "/stocks/v1/dividends"
        if query.get("cursor") == "p2":
            return 200, {"status": "OK", "results": [{"id": "E2", "ex_dividend_date": "2026-11-02",
                                                      "split_adjusted_cash_amount": 0.254426}]}
        return 200, {"status": "OK", "results": [{"id": "E1", "ex_dividend_date": "2026-10-01",
                                                  "split_adjusted_cash_amount": 0.241001}],
                     "next_url": "https://api.massive.com/stocks/v1/dividends?cursor=p2"}

    massive, opener = client(routes)
    got = massive.fetchers()["dividends"]("SHY", ("2024-09-25", "2026-11-04"))
    first = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(opener.seen[1][0]).query))
    assert first == {"ticker": "SHY", "ex_dividend_date.gte": "2024-09-25", "ex_dividend_date.lte": "2026-11-04",
                     "sort": "ex_dividend_date.asc", "limit": "5000"}
    assert [r["id"] for r in got["payload"]["results"]] == ["E1", "E2"]
    assert got["range"] == ("2024-09-25", "2026-11-04") and got["request"]["pages"] == 2


@pytest.mark.parametrize("dropped", [http.client.RemoteDisconnected("Remote end closed connection without response"),
                                     http.client.IncompleteRead(b'{"status": "O', 40), ConnectionResetError(54, "reset")])
def test_a_connection_dropped_or_cut_short_is_read_again_as_a_network_failure_is(dropped):
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise dropped
        return Response(200, {"status": "OK", "results": [{"ticker": "SPY"}]})

    massive = Massive(KEY, opener=opener, pause=0)
    status, body = massive.get("https://api.massive.com/v3/reference/tickers/SPY")
    assert (status, body["results"]) == (200, [{"ticker": "SPY"}]) and len(calls) == 2

    def always(request, timeout=None):
        raise dropped

    with pytest.raises(MassiveError):  # past its retries, the read fails as any network failure does
        Massive(KEY, opener=always, pause=0).get("https://api.massive.com/v3/reference/tickers/SPY")


def test_the_shared_client_reads_a_dropped_connection_again():
    from harness.venues.http import Http
    calls = []

    def opener(request, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return Response(200, {"ok": True})

    status, body = Http(opener=opener, pause=0).call("GET", "https://example.test/x")
    assert (status, body) == (200, {"ok": True}) and len(calls) == 2


def test_the_kept_open_connections_answer_as_urlopen_does_so_each_clients_handling_stands():
    """harness.venues.http.Pooled: a client's request sent with its own headers and body; an answer below 400 as
    urlopen gives it; one of 400 or more as urllib's HTTPError, with its code, headers and body, so a 429's wait and a
    404's answer are read as before; a timeout and the network failing as the failures a client retries."""
    import httpx
    from harness.venues.http import TRANSIENT, Pooled
    seen, answers = [], []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        kind = answers.pop(0)
        if kind == "timeout":
            raise httpx.ReadTimeout("no answer", request=request)
        if kind == "dropped":
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
        status, body, headers = kind
        return httpx.Response(status, json=body, headers=headers)

    pooled = Pooled(transport=httpx.MockTransport(handle))
    answers += [(429, {"status": "ERROR"}, {"Retry-After": "0"}), (200, {"status": "OK", "results": {"x": 1}}, {})]
    massive = Massive(KEY, opener=pooled, pause=0)
    assert massive.get("https://api.massive.com/v3/reference/tickers/SPY") == (200, {"status": "OK", "results": {"x": 1}})
    assert seen[-1].headers["Authorization"] == f"Bearer {KEY}" and seen[-1].headers["User-Agent"] == "event-risk"
    answers.append((404, {"status": "NOT_FOUND", "message": "Ticker not found."}, {}))
    assert massive.get("https://api.massive.com/v3/reference/tickers/ZZZ", names=True)[0] == 404  # an answer
    answers.append((503, {}, {"Retry-After": "7"}))
    with pytest.raises(urllib.error.HTTPError) as raised:
        pooled(urllib.request.Request("https://example.test/x", data=b'{"a": 1}', method="POST"), timeout=5)
    assert (raised.value.code, raised.value.headers.get("retry-after"), json.loads(raised.value.read())) == (503, "7", {})
    assert seen[-1].method == "POST" and seen[-1].content == b'{"a": 1}'
    for failure in ("timeout", "dropped"):  # each a failure the clients read again
        answers.append(failure)
        with pytest.raises(TRANSIENT):
            pooled(urllib.request.Request("https://example.test/x"), timeout=5)


def test_at_a_sources_pace_a_read_for_later_takes_its_turn_after_the_reads_a_call_waits_on(monkeypatch):
    """harness.venues.http.Pace: one request at a time, an interval apart; a read started for later (for_later), queued
    first, takes its turn only once no read a call waits on is waiting; and the mark carries to a client's own pool.
    The request holding the turn holds it through its interval until both others are waiting for it, whatever the
    pace of the threads, and each turn is recorded as it is held."""
    import threading
    import time
    import types
    from harness.venues import http as venues_http
    from harness.venues.http import Pace, carried, for_later
    pace, order, queued = Pace(60.0), [], []
    holding, release = threading.Event(), threading.Event()

    def interval(seconds):  # each request's interval, held in its turn: the turns in the order they are held
        order.append(threading.current_thread().name)
        if threading.current_thread().name == "later, holding the turn":
            holding.set()
            assert release.wait(10)
    monkeypatch.setattr(venues_http, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=interval))

    class Turn(threading.Condition):  # each request that waits for the turn, noted as it starts to
        def wait(self, timeout=None):
            queued.append(threading.current_thread().name)
            return super().wait(timeout)
    pace._turn = Turn()

    def take(later):
        with for_later() if later else contextlib.nullcontext():
            pace.take()

    def waiting_for_it(n):
        for _ in range(1000):
            if len(queued) >= n:
                return True
            threading.Event().wait(0.01)
        return False

    pace.take()  # the turn before: the next waits its interval
    threads = [threading.Thread(target=take, args=(later,), name=name) for name, later in
               (("later, holding the turn", True), ("later, queued first", True), ("waited on, queued after", False))]
    threads[0].start()
    assert holding.wait(10)  # it holds the turn, in its interval
    threads[1].start()
    assert waiting_for_it(1)
    threads[2].start()
    assert waiting_for_it(2)
    release.set()
    for t in threads:
        t.join(5)
    assert order == ["later, holding the turn", "waited on, queued after", "later, queued first"]
    with for_later():
        marked = carried(lambda: getattr(venues_http._reading, "later", False))
    seen: list = []
    other = threading.Thread(target=lambda: seen.append(marked()))
    other.start()
    other.join(5)
    assert seen == [True]  # run on a thread of a client's own pool, the read still carries its mark


# A stock's rows over a window its ticker changed in (`_joined`, `tenure`): Massive's events, references, bars and
# splits, stubbed by ticker and day.

def _ms(day: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(day + "T17:00:00+00:00").timestamp() * 1000)


def listing(events: dict, identity: dict, bars: dict, splits: dict | None = None):
    """Routes for Massive: `events` {ticker: [(date, ticker)]}, `identity` {(ticker, day): (cik, figi, list_date)},
    `bars` {ticker: [(day, close)]}, `splits` {ticker: [(execution_date, from, to)]}."""
    def routes(path, query):
        if path.endswith("/events"):
            t = path.split("/")[-2]
            if t not in events:
                return 404, {"status": "NOT_FOUND"}
            return 200, {"status": "OK", "results": {"events": [
                {"type": "ticker_change", "date": d, "ticker_change": {"ticker": n}} for d, n in events[t]]}}
        if path.startswith("/v3/reference/tickers/"):
            found = identity.get((path.rsplit("/", 1)[-1], query.get("date")))
            if not found:
                return 404, {"status": "NOT_FOUND"}
            cik, figi, listed = found
            return 200, {"status": "OK", "results": {"cik": cik, "composite_figi": figi, "list_date": listed}}
        if path.startswith("/v2/aggs/ticker/"):
            parts = path.split("/")
            t, start, end = parts[4], parts[8], parts[9]
            return 200, {"status": "OK", "results": [{"t": _ms(d), "c": c} for d, c in bars.get(t, []) if start <= d <= end]}
        if path == "/stocks/v1/splits":
            return 200, {"status": "OK", "results": [
                {"ticker": query["ticker"], "execution_date": d, "split_from": f, "split_to": t}
                for d, f, t in (splits or {}).get(query["ticker"], []) if d >= query.get("execution_date.gte", "")]}
        return 404, {"status": "NOT_FOUND"}
    return routes


DAYS = ["2026-06-01", "2026-06-12", "2026-06-16", "2026-07-01", "2026-07-02", "2026-08-03", "2026-11-13"]


def test_a_company_reorganized_under_a_holding_company_keeps_its_listings_history():
    # A new CIK and new FIGIs from the day of the change, the listing date kept: the days before are the listing's.
    massive, _ = client(listing(
        events={"ABC": [("2026-07-02", "ABC")]},
        identity={("ABC", "2026-07-01"): ("111", "FIGI-OLD", "1920-03-25"), ("ABC", "2026-11-13"): ("222", "FIGI-NEW", "1920-03-25")},
        bars={"ABC": [(d, 100.0 + i) for i, d in enumerate(DAYS)]}))
    got = massive.bars("ABC", "2026-06-01", "2026-11-13")["payload"]
    assert [r["c"] for r in got["results"]] == [100.0 + i for i in range(len(DAYS))]


def test_a_ticker_another_listing_held_keeps_that_listings_rows_out():
    # Another company's own CIK, FIGI and listing date: its rows under the ticker are no part of the listing's history.
    massive, _ = client(listing(
        events={"ABC": [("2026-07-02", "ABC")]},
        identity={("ABC", "2026-07-01"): ("999", "FIGI-FUND", "2001-07-27"), ("ABC", "2026-11-13"): ("222", "FIGI-NEW", "1969-12-04")},
        bars={"ABC": [(d, 100.0 + i) for i, d in enumerate(DAYS)]}))
    got = massive.bars("ABC", "2026-06-01", "2026-11-13")["payload"]
    assert got["tenure"] == [{"ticker": "ABC", "from": "2026-07-02", "to": "2026-11-13"}]
    assert [r["c"] for r in got["results"]] == [104.0, 105.0, 106.0]


def test_a_temporary_ticker_that_listed_nothing_before_leaves_the_current_tickers_own_earlier_rows_standing():
    # HWI from 2026-06-15 to 06-28, HW before and after, one company throughout; HWI names nothing before its event,
    # and every row is under HW, already on today's share basis (a later split under HW is not applied again).
    massive, _ = client(listing(
        events={"HW": [("2026-06-15", "HWI"), ("2026-06-29", "HW")]},
        identity={("HW", "2026-06-14"): ("333", "FIGI-HW", "1929-09-19"), ("HW", "2026-06-28"): ("333", "FIGI-HW", "1929-09-19"),
                  ("HW", "2026-11-13"): ("333", "FIGI-HW", "1929-09-19")},
        bars={"HW": [(d, 200.0 + i) for i, d in enumerate(DAYS)]},
        splits={"HW": [("2026-08-01", 1, 2)]}))
    got = massive.bars("HW", "2026-06-01", "2026-11-13")["payload"]
    assert got["tenure"][0] == {"ticker": "HW", "from": "2026-06-01", "to": "2026-06-14"}
    assert got["tenure"][1]["read"] == "HW"
    assert [r["c"] for r in got["results"]] == [200.0 + i for i in range(len(DAYS))]


def test_an_earlier_tickers_rows_are_put_on_todays_share_basis_by_the_splits_after_it():
    # OLD to NEW on 2026-07-02, a 2-for-1 split under NEW after it: OLD's rows halve, NEW's stand as Massive gives them.
    massive, _ = client(listing(
        events={"NEW": [("2026-01-02", "OLD"), ("2026-07-02", "NEW")]},
        identity={},
        bars={"OLD": [(d, 100.0) for d in DAYS if d < "2026-07-02"], "NEW": [(d, 50.0) for d in DAYS if d >= "2026-07-02"]},
        splits={"NEW": [("2026-08-01", 1, 2)]}))
    got = massive.bars("NEW", "2026-06-01", "2026-11-13")["payload"]
    assert [t["ticker"] for t in got["tenure"]] == ["OLD", "NEW"]
    assert [r["c"] for r in got["results"]] == [50.0] * len(DAYS)
