"""Every kind Massive answers, read with no key (harness.venues.keyless): the process's clients read each kind without
MASSIVE_API_KEY, and a listing's prices and dividends come in Massive's shape from Yahoo's chart, each dividend marked
recurring by the schedule its payments keep."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from harness.venues.keyless import Keyless, schedule

NY = ZoneInfo("America/New_York")


def stamp(day: str, hour: int = 9) -> int:
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=30, tzinfo=NY).timestamp())


class Chart:
    """Yahoo's chart for one symbol: two sessions and a year of quarterly dividends, one paid twice in a quarter."""

    def _chart(self, symbol, params):
        days = ["2026-11-17", "2026-11-18"]
        paid = ["2025-11-10", "2026-02-09", "2026-05-11", "2026-05-29", "2026-08-10"]
        return {"meta": {"currency": "USD", "instrumentType": "EQUITY", "exchangeName": "NMS",
                         "regularMarketPrice": 101.0, "regularMarketTime": stamp("2026-11-18", 16)},
                "timestamp": [stamp(d) for d in days],
                "indicators": {"quote": [{"open": [99.0, 100.5], "high": [101.0, 102.0], "low": [98.0, 100.0],
                                          "close": [100.0, 101.0], "volume": [10, 20]}]},
                "events": {"dividends": {str(stamp(d)): {"amount": 0.25, "date": stamp(d)} for d in paid},
                           "capitalGains": {str(stamp("2025-12-15")): {"amount": 1.5, "date": stamp("2025-12-15")}}}}


def test_without_massives_key_every_kind_it_answers_is_read_by_a_keyless_client(monkeypatch, tmp_path):
    """MATH.md 3.1: a portfolio is priced and its funds looked through with no key but Claude's. Each kind Massive's client
    answers has a fetcher here without MASSIVE_API_KEY."""
    from harness import analyst, observations
    from harness.venues.massive import Massive
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    kept = observations.Observations(tmp_path / "observations.sqlite")
    monkeypatch.setattr(observations, "shared", lambda: kept)
    monkeypatch.setattr(analyst, "_clients", None)
    kinds = set(analyst.clients()["fetchers"])
    monkeypatch.setattr(analyst, "_clients", None)
    assert set(Massive("k").fetchers()) - {"news"} <= kinds  # `news`, Massive's own feed, which nothing reads
    assert {"tracker", "closed_at"} <= kinds


def test_a_listings_bars_and_dividends_come_in_massives_shape_each_dividend_recurring_by_its_schedule():
    """A bar is stamped at 00:00 New York of its session, as Massive stamps a daily bar. Quarterly payments are
    recurring at four a year; a second payment inside the same quarter, and a capital gains distribution, are not."""
    keyless = Keyless(Chart(), sec=None, openfigi=None, equities=None)
    bars = keyless.bars("ABC", "2026-11-01", "2026-11-18")["payload"]
    assert bars["status"] == "OK" and [b["c"] for b in bars["results"]] == [100.0, 101.0]
    assert datetime.fromtimestamp(bars["results"][0]["t"] / 1000, NY).isoformat() == "2026-11-17T00:00:00-05:00"
    rows = keyless.dividends("ABC", "2025-11-01", "2026-11-18")["payload"]["results"]
    kinds = {r["ex_dividend_date"]: (r["distribution_type"], r.get("frequency")) for r in rows}
    assert kinds == {"2025-11-10": ("recurring", 4), "2026-02-09": ("recurring", 4), "2026-05-11": ("recurring", 4),
                     "2026-05-29": ("special", None), "2026-08-10": ("recurring", 4),
                     "2025-12-15": ("special", None)}
    assert all(r["cash_amount"] == r["split_adjusted_cash_amount"] for r in rows)
    assert schedule(["2026-01-15", "2026-02-13", "2026-03-16"]) == 12 and schedule(["2026-01-15"]) is None


def test_openfigi_asks_made_at_once_share_requests_ten_jobs_to_one_without_a_key():
    """Without a key OpenFIGI answers 25 requests a minute of ten jobs each: asks waiting for the client's one slot go
    together, so nine asks of one job each made while a request is out are one request, not nine."""
    import json
    import threading
    import time

    from harness.venues.openfigi import OpenFIGI

    sent: list[int] = []

    class Answer:
        def __init__(self, n):
            self.body = json.dumps([{"data": [{"ticker": "X"}]}] * n).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def opener(request, timeout=None):
        n = len(json.loads(request.data))
        sent.append(n)
        time.sleep(0.2)
        return Answer(n)

    client = OpenFIGI(opener=opener)
    first = threading.Thread(target=client._post, args=([{"idType": "ID_ISIN", "idValue": "A"}],))
    first.start()
    time.sleep(0.05)  # the first holds the slot: nine more wait for it
    rest = [threading.Thread(target=client._post, args=([{"idType": "ID_ISIN", "idValue": str(k)}],))
            for k in range(9)]
    for t in rest:
        t.start()
    for t in [first, *rest]:
        t.join()
    assert sent == [1, 9]


def test_where_an_isin_trades_is_its_home_markets_listing_never_another_markets_line_and_else_unknown():
    """A report names a share by its ISIN alone. FinanceDatabase lists every line of its company, Milan's 1AAPL beside
    Apple's own: the share's venue is its listing on its ISIN's own market, and where the file has none there it is
    unknown, never the first line in some order."""
    class Rows:
        def rows(self, isin):
            return {"US0378331005": [{"symbol": "1AAPL.MI", "exchange": "MIL", "delisted": False},
                                     {"symbol": "APC.DE", "exchange": "GER", "delisted": False},
                                     {"symbol": "AAPL", "exchange": "NMS", "delisted": False}],
                    "GB0000000001": [{"symbol": "ABC", "exchange": "NYQ", "delisted": False}]}.get(isin, [])

    keyless = Keyless(Chart(), sec=None, openfigi=None, equities=Rows())
    got = keyless.isin_listings(["US0378331005@2026-11", "GB0000000001@2026-11"], "2026-11-19")
    assert got["US0378331005@2026-11"]["payload"]["listings"] == [{"ticker": "AAPL", "country": "US"}]
    assert got["GB0000000001@2026-11"]["payload"]["status"] == "NOT_FOUND"


def test_without_massives_key_a_companys_news_guidance_and_risk_factors_are_refused_and_its_other_kinds_have_none():
    """TOOLS.md 5 (search_company): no source here gives a company's news, guidance or risk factors without Massive's key,
    so each is answered NOT_AUTHORIZED, which search_company returns as "cannot be read here", and the analyst finds a
    company's news on the web. Its results, ratings and reports are answered with none, and search_company reads them
    from its listing's record and from EDGAR."""
    import json
    keyless = Keyless(Chart(), sec=None, openfigi=None, equities=None)

    def answered(kind: str) -> dict:
        return keyless.company(json.dumps({"kind": kind, "ticker": "NVDA", "params": {"limit": 5}}), "2026-11-18")["payload"]

    for kind in ("news", "guidance", "risk_factors"):
        assert answered(kind)["status"] == "NOT_AUTHORIZED" and "results" not in answered(kind)
    for kind in ("earnings", "ratings", "annual_report", "current_reports"):
        assert answered(kind)["status"] == "OK" and answered(kind)["results"] == []


def test_a_us_isin_is_listed_where_its_cusip_is_unless_openfigi_already_maps_its_ticker_to_other_figis():
    """A US ISIN's listing comes from the file by its CUSIP; a company reorganized since the file's snapshot (Ferguson's
    new CUSIP beside Ferguson plc's FIGIs) is read from OpenFIGI once its ticker's own mapping says otherwise, as the
    resolution reads again a mapping in conflict with its reference: never worse than OpenFIGI alone."""
    asked: list[list[str]] = []

    class File:
        def rows(self, **by):
            row = {"symbol": "FERG", "exchange": "NYQ", "delisted": False, "composite_figi": "BBGOLD",
                   "shareclass_figi": "BBGOLDCLASS", "name": "Ferguson"}
            return [row] if by.get("cusip") == "31488V107" or by.get("isin") == "US31488V1070" else []

    class FIGI:
        jobs = 10

        def _map(self, kind, subjects, day):
            asked.append(list(subjects))
            return {s: {"payload": {"status": "OK", "isin": s, "listings": [
                {"ticker": "FERG", "exchCode": "US", "compositeFIGI": "BBGNEW"}], "composites": ["BBGNEW"]}}
                for s in subjects}

    keyless = Keyless(Chart(), sec=None, openfigi=FIGI(), equities=File())
    first = keyless.isin_mappings(["US31488V1070"], "2026-11-19")["US31488V1070"]["payload"]
    assert first["listings"][0]["compositeFIGI"] == "BBGOLD" and asked == []  # the file's, while nothing says otherwise
    keyless._mapped["FERG"] = {"compositeFIGI": "BBGNEW"}  # the ticker's reference, as OpenFIGI maps it now
    again = keyless.isin_mappings(["US31488V1070"], "2026-11-19")["US31488V1070"]["payload"]
    assert again["listings"][0]["compositeFIGI"] == "BBGNEW" and asked == [["US31488V1070"]]
def test_a_carrier_whose_own_openfigi_ask_was_answered_does_not_fail_with_the_asks_it_carries():
    """An ask answered while it waited for the slot goes on to carry those still waiting. Where that request fails, each
    ask it carried fails, to be asked again where it is read again, and the carrier's own answer stands."""
    import threading
    import time

    from harness.venues.http import VenueError
    from harness.venues.openfigi import OpenFIGI

    def failing(jobs):
        raise VenueError("OpenFIGI did not answer")

    client = OpenFIGI(opener=lambda request, timeout=None: None)
    client._answer = failing  # every request sent from here on fails
    client._slots.acquire()  # a request is out, so the next ask waits for the slot
    got: dict = {}
    carrier = threading.Thread(
        target=lambda: got.setdefault("answers", client._post([{"idType": "ID_ISIN", "idValue": "MINE"}])))
    carrier.start()
    for _ in range(400):  # until its ask is waiting
        with client._queued:
            if client._waiting:
                break
        time.sleep(0.005)
    with client._queued:  # the request that was out answers the waiting ask, and another ask arrives behind it
        mine = client._waiting.pop(0)
        mine["answers"] = [{"data": [{"ticker": "X"}]}]
        mine["done"].set()
        other = {"jobs": [{"idType": "ID_ISIN", "idValue": "OTHER"}], "done": threading.Event(), "answers": None,
                 "error": None}
        client._waiting.append(other)
    client._slots.release()
    carrier.join(5)
    assert got.get("answers") == [{"data": [{"ticker": "X"}]}]
    assert other["done"].is_set() and isinstance(other["error"], VenueError)
