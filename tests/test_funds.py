"""A mutual fund's share class (MATH.md 3.1; harness.venues.funds): what it is, from the SEC's list of the funds' share
classes where no listing's reference gives it; its daily net asset value and distributions, from Yahoo's chart; each
in the shape Massive gives a listing's, so the holding is priced, fitted and looked through as any fund. Every client
is a fake; nothing reaches the network."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from harness.derive import series
from harness.venues.funds import MUTUAL, REFERENCE_FORM, MutualFunds

NY = ZoneInfo("America/New_York")
FUNDS = {"fields": ["cik", "seriesId", "classId", "symbol"],
         "data": [[36405, "S000002839", "C000007773", "VFIAX"], [36405, "S000002839", "C000092055", "VOO"],
                  [1689873, "S000081144", "C000243815", "TSDD"]]}


def stamp(day: str, at: time = time(9, 30)) -> int:
    return int(datetime.combine(datetime.fromisoformat(day).date(), at, NY).timestamp())


class Massive:
    def __init__(self):
        self.asked = []

    def ticker(self, ticker, day):
        self.asked.append((ticker, day))
        if ticker == "VOO" or ticker == "TSDD" and day >= "2023-08-21":  # TSDD listed on 2023-08-21
            return {"payload": {"status": "OK", "results": {"ticker": ticker, "type": "ETF"}}}
        return {"payload": {"status": "NOT_FOUND"}}


class SEC:
    def _list(self, url):
        return FUNDS


class Yahoo:
    def _chart(self, symbol, params):
        assert symbol == "VFIAX"
        return {"meta": {"instrumentType": "MUTUALFUND", "currency": "USD", "longName": "Vanguard 500 Index Admiral"},
                "timestamp": [stamp("2026-11-09"), stamp("2026-11-10"), stamp("2026-11-11")],
                "indicators": {"quote": [{"close": [690.0, 694.58, 703.67]}]},
                "events": {"dividends": {str(stamp("2026-09-29")): {"amount": 1.738, "date": stamp("2026-09-29")}}}}


def massive_kinds():
    def listing(kind):
        def one(subject, covering):
            return {"payload": {"from": "massive", "kind": kind, "subject": subject}}
        return one
    base = {k: listing(k) for k in ("ticker_ref", "ticker_on", "bars", "dividends", "splits", "snapshot", "news")}
    base["bars"].parse = 2
    return base


def test_a_mutual_funds_class_is_answered_from_its_own_facts_in_a_listings_shapes_and_a_listing_stays_massives():
    massive = Massive()
    got = MutualFunds(massive, SEC(), Yahoo()).fetchers(massive_kinds())
    assert set(got) == {"ticker_ref", "ticker_on", "bars", "dividends", "splits", "snapshot"}  # news stays Massive's
    ref = got["ticker_ref"]("VFIAX", ("2026-11-11", "2026-11-11"))["payload"]
    assert ref == {"status": "OK", "results": {"ticker": "VFIAX", "name": "Vanguard 500 Index Admiral", "type": MUTUAL,
                                               "market": "funds", "active": True, "currency_name": "usd",
                                               "cik": "0000036405", "series": "S000002839", "class": "C000007773",
                                               "exchange_traded": "VOO"}, "parse": REFERENCE_FORM}  # in this client's form
    assert got["ticker_on"]("VFIAX@2026-11-11", None)["payload"]["results"]["type"] == MUTUAL
    assert got["ticker_ref"].parse == REFERENCE_FORM and \
        got["ticker_ref"]("VOO", ("2026-11-11", "2026-11-11"))["payload"]["parse"] == REFERENCE_FORM
    # an ETF is on the SEC's list too, and a listing's reference gives it: it stays Massive's
    assert got["bars"]("VOO", ("2026-01-01", "2026-11-11"))["payload"]["from"] == "massive"
    bars = got["bars"]("VFIAX", ("2026-11-10", "2026-11-11"))
    assert bars["payload"]["parse"] == 2 and got["bars"].parse == 2  # the form Massive's bars are kept in
    assert series.daily_closes(bars["payload"], "2026-11-12T02:00:00+00:00") == {"2026-11-10": 694.58,
                                                                                 "2026-11-11": 703.67}
    paid = got["dividends"]("VFIAX", ("2025-11-11", "2026-11-11"))["payload"]["results"]
    assert paid == [{"ticker": "VFIAX", "ex_dividend_date": "2026-09-29", "cash_amount": 1.738, "currency": "USD"}]
    assert got["splits"]("VFIAX", ("2025-11-11", "2026-11-11"))["payload"] == {"status": "OK", "results": []}
    today = datetime.now(NY).date().isoformat()  # decided once a day, by the reference that day and today
    assert sorted(day for t, day in massive.asked if t == "VFIAX") == sorted({"2026-11-11", today})


def test_an_etf_read_over_a_span_before_it_listed_is_a_listing_with_no_prices_then_and_stays_massives():
    """The SEC lists every ETF's class, and no listing's reference gives an ETF on a day before it listed: it is a
    listing all the same, today's reference says so, and its prices over that span are Massive's, which has none."""
    massive = Massive()
    got = MutualFunds(massive, SEC(), Yahoo()).fetchers(massive_kinds())
    assert got["bars"]("TSDD", ("2022-01-03", "2022-10-12"))["payload"]["from"] == "massive"
    assert got["ticker_on"]("TSDD@2020-03-23", None)["payload"]["from"] == "massive"


def test_a_mutual_funds_latest_mark_is_its_last_net_asset_value_struck_at_new_yorks_close():
    got = MutualFunds(Massive(), SEC(), Yahoo()).fetchers(massive_kinds())["snapshot"]("VFIAX", ("2026-11-12", "2026-11-12"))
    mark = series.snapshot_mark(got["payload"])
    assert mark == {"price": 703.67, "time": "2026-11-11T16:00:00-05:00", "from": "net asset value"}


def test_a_listing_abroad_by_its_symbol_has_its_own_markets_reference_where_massive_has_none():
    """MATH.md 3.1: Massive's reference answers NOT_FOUND for a listing abroad named by its symbol; its reference is its
    own market's (harness.venues.yahoo.Yahoo.reference), in this client's form. A US listing stays Massive's."""
    class Abroad(Yahoo):
        def reference(self, symbol, day):
            return {"payload": {"status": "OK", "results": {"ticker": symbol, "type": "CS", "market": "home"}}}

    base = massive_kinds()
    base["ticker_ref"] = lambda subject, covering: {"payload": Massive().ticker(subject, covering[1])["payload"]}
    got = MutualFunds(Massive(), SEC(), Abroad()).fetchers(base)
    ref = got["ticker_ref"]("0700.HK", ("2026-11-12", "2026-11-12"))["payload"]
    assert ref == {"status": "OK", "results": {"ticker": "0700.HK", "type": "CS", "market": "home"},
                   "parse": REFERENCE_FORM}
    assert got["ticker_ref"]("VOO", ("2026-11-12", "2026-11-12"))["payload"]["results"]["type"] == "ETF"
    assert got["ticker_ref"]("ZZZQ", ("2026-11-12", "2026-11-12"))["payload"]["status"] == "NOT_FOUND"  # no symbol


def test_a_rows_kind_is_found_once_and_a_commodity_funds_kinds_find_their_own():
    """harness.derive.assets.Kinds: a row's kind is found once for a compute and given again, by the row itself; the
    kinds of a commodity fund's rows, made from the portfolio's, find their own, so a row nothing labels is a
    commodity there and other elsewhere."""
    from dataclasses import replace
    from harness.derive.assets import Kinds
    kinds = Kinds()
    unlabelled = {"security_type": "", "asset_class": ""}
    assert kinds.of(unlabelled) == "other" and kinds.of(unlabelled) == "other"
    assert replace(kinds, unlabelled="commodity").of(unlabelled) == "commodity"
    assert kinds.of(dict(unlabelled)) == "other"  # an equal row of its own is found anew, to the same kind
