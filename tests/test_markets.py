"""Markets: the price each venue displays, the best bid and ask, depth, whether a market is open,
the venues' books and histories, a market's terms, the one search across the venues, a family read whole, a market
entering the store, and fetch_source and a page's stated dated. The venues run through Synpath's adapters on a fake
transport, and the web on a fake opener; nothing touches the network."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import datetime

import pytest

from harness.derive.markets import LISTING, depth, past_price, priced
import httpx

from harness.venues import kalshi, polymarket, polymarket_us
from harness.venues.http import Http
from harness.web import Web
from harness.store import Store, StoreError
from conftest import FIXTURES

NOW = "2026-11-05T02:00:00+00:00"


class Response(io.BytesIO):
    def __init__(self, status, body, url=""):
        super().__init__(json.dumps(body).encode() if not isinstance(body, bytes) else body)
        self.status, self.headers, self._url = status, {}, url

    def geturl(self):
        return self._url


class Opener:
    """Answers each request from a routing function; records method, path, query, and body."""

    def __init__(self, route):
        self.route = route
        self.seen: list[tuple[str, str, dict, object]] = []

    def __call__(self, request, timeout=None):
        parts = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(parts.query)
        body = json.loads(request.data) if request.data else None
        self.seen.append((request.get_method(), parts.netloc + parts.path, query, body))
        status, answer = self.route(request.get_method(), parts.netloc + parts.path, query, body)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(json.dumps(answer).encode()))
        return Response(status, answer, request.full_url)


def http(route):
    opener = Opener(route)
    return Http(opener=opener, pause=0), opener


class Transport:
    """Answers each request a venue adapter makes from a routing function; records method, path, query, and body."""

    def __init__(self, route):
        self.route = route
        self.seen: list[tuple[str, str, dict, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.url.query.decode() if isinstance(request.url.query, bytes)
                                      else str(request.url.query))
        body = json.loads(request.content) if request.content else None
        path = request.url.host + request.url.path
        self.seen.append((request.method, path, query, body))
        status, answer = self.route(request.method, path, query, body)
        return httpx.Response(status, json=answer)


def whole(route):
    """Polymarket's events that its site search ranks, read whole by id: the events the route's search answers."""
    def answer(method, path, query, body):
        if path == "gamma-api.polymarket.com/public-search":  # the documented search's exact matches: none here
            return 200, {"events": [], "pagination": {"hasMore": False, "totalResults": 0}}
        if path == "gamma-api.polymarket.com/events" and "id" in query:
            _, found = route("GET", "gamma-api.polymarket.com/search-v2", {}, None)
            return 200, [e for e in found.get("events") or [] if str(e["id"]) in query["id"]]
        return route(method, path, query, body)
    return answer


def adapter(venue: str, route):
    """A venue's adapter answered by `route`, unpaced, with what it was asked."""
    transport = Transport(whole(route) if venue == "polymarket" else route)
    client = httpx.Client(transport=httpx.MockTransport(transport))
    found = kalshi.Kalshi(client=client, limiter=None, search_limiter=None) if venue == "kalshi" \
        else polymarket_us.PolymarketUS(client=client, limiter=None) if venue == "polymarket_us" \
        else polymarket.Polymarket(client=client, limiter=None)
    return found, transport


# ---------------------------------------------------------------- books: the same book at two venues

PM_BOOK = {"asset_id": "Y1", "bids": [{"price": "0.30", "size": "1000"}, {"price": "0.31", "size": "2000"},
                                      {"price": "0.32", "size": "500"}],  # the venue's order: worst first
           "asks": [{"price": "0.40", "size": "100"}, {"price": "0.36", "size": "3000"}, {"price": "0.35", "size": "1000"}]}
KS_BOOK = {"orderbook_fp": {"yes_dollars": [["0.3000", "1000.00"], ["0.3100", "2000.00"], ["0.3200", "500.00"]],
                            "no_dollars": [["0.6000", "100.00"], ["0.6400", "3000.00"], ["0.6500", "1000.00"]]}}


def test_a_price_is_the_midpoint_where_both_sides_quote_and_the_last_trade_is_history_in_yes_terms():
    book = polymarket.levels(PM_BOOK)  # best bid 0.32, best ask 0.35
    record = {"market": {"clobTokenIds": json.dumps(["Y1", "N1"]), "outcomes": json.dumps(["Yes", "No"])}}
    no_trade = {"book": PM_BOOK, "trade": {"data": [{"token_id": "N1", "price": 0.47, "timestamp": 1794255840}]}}
    assert priced("polymarket", book, no_trade, record) == \
        {"bid": 0.32, "ask": 0.35, "price": pytest.approx(0.335), "depth": depth(book),
         "last_trade": {"price": 0.53, "time": "2026-11-09T20:24:00Z"}}  # a NO trade at 0.47 is YES at 0.53
    yes_trade = {"book": PM_BOOK, "trade": {"data": [{"token_id": "Y1", "price": 0.29, "timestamp": 1794255840}]}}
    assert priced("polymarket", book, yes_trade, record)["last_trade"]["price"] == 0.29
    assert polymarket.levels(no_trade) == book == polymarket.levels(PM_BOOK)  # a reading with its trade, or a book alone
    wide = {"bids": [(0.04, 100.0)], "asks": [(0.19, 100.0)]}  # however wide, both sides quote
    assert priced("polymarket", wide, {}, {})["price"] == pytest.approx(0.115)
    for one_side in ({"bids": [(0.30, 100.0)], "asks": []}, {"bids": [], "asks": [(0.40, 100.0)]}):
        shown = priced("polymarket", one_side, yes_trade, record)
        assert shown["price"] is None and shown["depth"] == 0.0  # no quote on a side: no price, whatever traded
        assert shown["last_trade"]["price"] == 0.29
    trade = {"trades": [{"created_time": "2026-11-09T20:37:57Z", "yes_price_dollars": "0.2000", "count_fp": "5.00"}]}
    shown = priced("kalshi", kalshi.levels(KS_BOOK), {**KS_BOOK, "trade": trade}, {})
    assert shown["price"] == pytest.approx(0.335)
    assert shown["last_trade"] == {"price": 0.2, "time": "2026-11-09T20:37:57Z"}
    assert priced("kalshi", kalshi.levels(KS_BOOK), KS_BOOK, {})["last_trade"] is None  # a reading with no trade
    us_book = {"marketData": {"bids": [{"px": {"value": "0.21"}, "qty": "156.8"}],
                              "offers": [{"px": {"value": "0.22"}, "qty": "9558.95"}],
                              "stats": {"lastTradePx": {"value": "0.22"},
                                        "lastTradeSetTime": "2026-11-09T20:20:37.498906026Z"}}}
    shown = priced("polymarket_us", polymarket_us.levels(us_book), us_book, {})
    assert (shown["bid"], shown["ask"], shown["price"]) == (0.21, 0.22, pytest.approx(0.215))
    assert shown["last_trade"] == {"price": 0.22, "time": "2026-11-09T20:20:37Z"}


def test_a_markets_price_is_unchanged_by_the_size_of_an_order():
    """A recorded book: the price is what the market shows, whatever any order would pay walking it."""
    found = json.loads((FIXTURES / "blockade_book.json").read_text())
    book = polymarket.levels(found["book"])
    shown = priced("polymarket", book, found["book"], {})
    assert (shown["bid"], shown["ask"]) == (0.02, 0.026)
    assert shown["price"] == pytest.approx(0.023)


def test_depth_is_the_smaller_sides_cost_over_the_top_five_levels():
    bids = [(0.5, 100.0)] * 6
    asks = [(0.6, 100.0)] * 6
    assert depth({"bids": bids, "asks": asks}) == pytest.approx(min(5 * 0.6 * 100, 5 * 0.5 * 100))  # the sixth is fleeting
    assert depth({"bids": [], "asks": asks}) == 0.0


def test_whether_a_market_is_open_is_the_venues_word_today_and_its_dates_for_a_past_as_of():
    pm = {"market": {"closed": False, "startDate": "2026-01-01T00:00:00Z", "endDate": "2027-01-01T04:59:00Z"}}
    assert polymarket.is_open(pm, "2026-11-05", False) is True
    assert polymarket.is_open({"market": {"closed": True}}, "2026-11-05", False) is False
    closed = {"market": {"closed": True, "startDate": "2026-01-01T00:00:00Z", "closedTime": "2026-10-02 18:00:00+00",
                         "endDate": "2027-01-01T04:59:00Z"}}
    assert polymarket.is_open(closed, "2026-10-01", True) is True and polymarket.is_open(closed, "2026-10-03", True) is False
    assert kalshi.is_open({"market": {"status": "active"}}, "2026-11-05", False) is True
    assert kalshi.is_open({"market": {"status": "finalized"}}, "2026-11-05", False) is False
    ks = {"market": {"open_time": "2026-01-01T00:00:00Z", "close_time": "2026-12-09T18:55:00Z"}}
    assert kalshi.is_open(ks, "2026-12-09", True) is True and kalshi.is_open(ks, "2026-12-10", True) is False


def test_the_venues_books_read_as_yes_bids_and_asks_best_first_kalshis_asks_from_its_no_bids():
    pm, ks = polymarket.levels(PM_BOOK), kalshi.levels(KS_BOOK)
    assert pm["bids"][0] == (0.32, 500.0) and pm["asks"][0] == (0.35, 1000.0)
    assert ks == pm  # a NO bid at 0.65 is a YES ask at 0.35


def test_history_is_a_daily_price_at_each_venue():
    utc = lambda s: int(datetime.fromisoformat(s).timestamp())
    pm = polymarket.daily({"data": [{"timestamp": utc("2026-11-03T12:00:00+00:00"), "price": 0.20},
                                    {"timestamp": utc("2026-11-04T03:00:00+00:00"), "price": 0.25},
                                    {"timestamp": utc("2026-11-05T03:00:00+00:00"), "price": 0.31},
                                    {"timestamp": utc("2026-11-04T06:00:00+00:00"), "price": 0.30}]})
    # the last point inside each New York day: 03:00 UTC is 22:00 the day before in New York, 06:00 UTC is 01:00
    assert pm == {"2026-11-03": 0.25, "2026-11-04": 0.31}
    ks = kalshi.daily({"candlesticks": [
        {"end_period_ts": utc("2026-11-04T05:00:00+00:00"), "yes_bid": {"close_dollars": "0.2000"},
         "yes_ask": {"close_dollars": "0.2400"}},
        {"end_period_ts": utc("2026-11-05T05:00:00+00:00"), "yes_bid": {"close": "0.3000"},
         "yes_ask": {"close": "0.3400"}}]})  # the historical endpoint's shape
    assert ks == {"2026-11-03": pytest.approx(0.22), "2026-11-04": pytest.approx(0.32)}  # the mid of the closing bid and ask


def test_a_past_as_of_reads_the_market_as_it_stood_at_that_days_end_and_never_an_invented_quote():
    """Kalshi's candle gives the day's closing bid and ask, Polymarket US's sample its best bid and ask (its NO price
    one less the bid), Polymarket's history a price alone, which is no quote; a book the store kept by the as-of's end
    stands where it was read after that close, its depth with it."""
    utc = lambda s: int(datetime.fromisoformat(s).timestamp())
    candles = {"candlesticks": [
        {"end_period_ts": utc("2026-10-01T04:00:00+00:00"), "yes_bid": {"close_dollars": "0.3000"},
         "yes_ask": {"close_dollars": "0.3400"}},
        {"end_period_ts": utc("2026-10-02T04:00:00+00:00"), "yes_bid": {"close_dollars": "0.4000"},
         "yes_ask": {"close_dollars": "0.4400"}},
        {"end_period_ts": utc("2026-10-03T04:00:00+00:00"), "yes_bid": {"close_dollars": "0.5000"},
         "yes_ask": {"close_dollars": "0.5400"}}]}
    q = past_price("kalshi", candles, "2026-10-01")  # the candle ending at midnight in New York: October 1's close
    assert (q["bid"], q["ask"], q["price"], q["depth"], q["price_date"]) == (0.40, 0.44, pytest.approx(0.42), None,
                                                                             "2026-10-01")
    sample = {"history": [{"timestamp": utc("2026-10-02T04:00:00+00:00"), "longPrice": "0.45", "shortPrice": "0.61"}]}
    q = past_price("polymarket_us", sample, "2026-10-01")
    assert (q["bid"], q["ask"], q["price"]) == (pytest.approx(0.39), 0.45, pytest.approx(0.42))
    q = past_price("polymarket", {"data": [{"timestamp": utc("2026-10-01T20:00:00+00:00"), "price": 0.42}]},
                   "2026-10-01")
    assert (q["bid"], q["ask"], q["price"], q["history_price"]) == (None, None, None, 0.42)  # no quote, no price
    book = {"bids": [{"price": "0.41", "size": "100"}], "asks": [{"price": "0.43", "size": "50"}]}
    later = past_price("polymarket", {"data": []}, "2026-10-01", ("2026-10-01T22:00:00+00:00", book))
    assert (later["bid"], later["ask"], later["from"]) == (0.41, 0.43, "reading") and later["depth"] is not None
    earlier = past_price("kalshi", candles, "2026-10-01", ("2026-10-01T15:00:00+00:00", {"orderbook": {}}))
    assert (earlier["bid"], earlier["from"]) == (0.40, "history")  # the close came after it: the close stands


# ---------------------------------------------------------------- a market's terms: the venue's own fields


def test_a_markets_terms_are_each_venues_own_fields_and_no_date_is_read_from_the_text():
    question = "Will the US effective tariff rate be above 20% on March 31, 2027?"  # a date the code does not read
    pm = {"market": {"question": question, "description": "Resolves on April 15, 2027.",
                     "endDate": "2027-04-01T03:59:00Z", "resolutionSource": "https://budgetlab.yale.edu"},
          "event": {"id": "9", "title": "Tariffs", "endDate": "2027-06-30T12:00:00Z"}}
    listing = polymarket.listing(pm)
    assert listing["close_time"] == "2027-03-31"  # the market's endDate, in New York
    assert listing["resolution_source"] == "https://budgetlab.yale.edu"
    assert listing["family"] == {"id": "family:polymarket:9", "title": "Tariffs"}
    assert listing["strike"] is None
    event_only = polymarket.listing({"market": {"question": question},
                                     "event": {"endDate": "2027-06-30T12:00:00Z", "resolutionSource": "https://x.org"}})
    assert event_only["close_time"] == "2027-06-30" and event_only["resolution_source"] == "https://x.org"  # its event's
    ks = {"market": {**KS_MARKET}, "event": {"event_ticker": "KXFED-26DEC", "title": "Fed funds rate after Dec 2026?",
                                             "sub_title": "On Dec 9, 2026", "settlement_sources": []},
          "series": {"settlement_sources": [{"name": "Federal Reserve", "url": "https://federalreserve.gov"}],
                     "fee_type": "quadratic", "fee_multiplier": 1}}
    listing = kalshi.listing(ks)
    assert listing["close_time"] == "2026-12-09" and set(listing) == set(LISTING.fields)
    assert listing["strike"] == {"type": "greater", "floor": 4.5}  # the strike as Kalshi states it, as data
    assert listing["resolution_source"] == "Federal Reserve (https://federalreserve.gov)"  # the series' list
    assert listing["family"] == {"id": "family:kalshi:KXFED-26DEC", "title": "Fed funds rate after Dec 2026?"}  # its title
    assert listing["fees"]["fee_type"] == "quadratic" and listing["fees"]["multiplier"] == 1
    ks["event"]["settlement_sources"] = [{"name": "The Fed", "url": None}]
    assert kalshi.listing(ks)["resolution_source"] == "The Fed"  # the event's own list, where it has one


def test_a_markets_strike_date_is_the_date_its_venue_states_its_question_is_based_on_and_no_other_time():
    """MATH.md 3.3: the date a question is based on, where the venue states one as data, as the venue's moment in one form;
    a market's other times (its close, when its outcome is expected known, when its event occurred) are not it."""
    times = {"close_time": "2027-11-03T03:59:00Z", "expected_expiration_time": "2027-11-03T15:00:00Z",
             "occurrence_datetime": "2026-11-03T15:00:00Z"}
    dated = {"market": {**KS_MARKET, **times}, "event": {"event_ticker": "KXFED-26DEC",
                                                         "strike_date": "2026-12-09T14:00:00-05:00"}}
    assert kalshi.listing(dated)["strike_date"] == "2026-12-09T19:00:00Z"  # the event's date strike, in UTC
    period = {"market": {**KS_MARKET, **times}, "event": {"event_ticker": "KXGDP-27APR29", "strike_period": "Q1 2027"}}
    assert kalshi.listing(period)["strike_date"] is None  # a period strike is in the question's words
    pm = polymarket.listing({"market": {"question": "q", "endDate": "2027-01-01T04:59:00Z"}, "event": {"id": "9"}})
    us = polymarket_us.listing({"market": {"title": "q", "endDate": "2027-07-15T03:00:00Z"}})
    assert pm["strike_date"] is None and us["strike_date"] is None  # neither states one as data
    assert set(pm) == set(us) == set(kalshi.listing(dated)) == set(LISTING.fields)  # one form, whichever venue


def test_a_kalshi_markets_fee_is_the_one_in_effect_its_events_override_over_its_series():
    series = {"ticker": "KXFED", "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}
    change = lambda ts, kind, m: {"series_ticker": "KXFED", "fee_type": kind, "fee_multiplier": m, "scheduled_ts": ts}
    record = {"market": KS_MARKET, "event": KS_EVENT, "series": series,
              "fee_changes": {"series": [change("2025-10-04T07:00:00Z", "quadratic", 0.5),
                                         change("2026-09-03T18:35:23Z", "quadratic_with_maker_fees", 1)],
                              "read_at": "2026-11-11T15:00:00Z"}}
    now = "2026-11-11T15:00:00Z"
    assert (kalshi.fees(record, now)["fee_type"], kalshi.fees(record, now)["multiplier"]) == \
        ("quadratic_with_maker_fees", 1)  # no change after now: the series' own
    then = kalshi.fees(record, "2026-01-02T05:00:00Z")  # between the two changes: the first
    assert (then["fee_type"], then["multiplier"], "override" in then) == ("quadratic", 0.5, False)
    assert kalshi.fees(record, "2025-06-01T04:00:00Z") is None  # before its only stated changes: none stated
    record["fee_changes"]["series"].append(change("2026-12-01T05:00:00Z", "quadratic", 2))  # scheduled after now
    assert kalshi.fees(record, now)["multiplier"] == 1  # not yet in effect
    record["fee_changes"]["series"] = []  # a series whose fee never changed: its record's, at any moment
    assert kalshi.fees(record, "2025-06-01T04:00:00Z")["multiplier"] == 1
    override = lambda ts, kind, m: {"event_ticker": "KXFED-26DEC", "fee_type_override": kind,
                                    "fee_multiplier_override": m, "scheduled_ts": ts}
    record["fee_changes"]["event"] = [override("2026-11-01T00:00:00Z", None, 0.25)]  # the multiplier alone
    found = kalshi.fees(record, now)
    assert (found["fee_type"], found["multiplier"], found["override"]) == ("quadratic_with_maker_fees", 0.25, "event")
    record["fee_changes"]["event"].append(override("2026-11-10T00:00:00Z", None, None))  # cleared: the series' again
    assert "override" not in kalshi.fees(record, now) and kalshi.fees(record, now)["multiplier"] == 1
    assert kalshi.fees(record, "2026-11-05T00:00:00Z")["multiplier"] == 0.25  # while it stood
    assert kalshi.fees(record, "2026-11-05T00:00:00Z")["taker_rate"] == pytest.approx(0.07 * 0.25)  # its schedule's
    assert kalshi.fees(record, now)["taker_rate"] == pytest.approx(0.07)


def test_what_buying_a_contract_costs_is_its_side_of_the_book_walked_and_the_fee_its_venue_states():
    """MATH.md 1.9, 3.3: Yes up the asks, No against the Yes bids at one less each, each fill at its own price, and the
    taker's fee as its venue states it, in one form whichever venue (harness.derive.markets.order_cost)."""
    from harness.derive.engine import Unavailable
    from harness.derive.markets import order_cost
    book = {"asks": [(0.45, 10), (0.40, 10)], "bids": [(0.30, 5), (0.35, 20)]}
    kalshi_fee = {"fee_type": "quadratic", "multiplier": 1, "rounding": "up_to_cent", "taker_rate": 0.07}
    got = order_cost(book, "yes", 15, kalshi_fee)  # 10 at 0.40 and 5 at 0.45: 6.25, and a fee of 0.254625, up to 0.26
    assert got == {"cost": pytest.approx(6.51), "fees": pytest.approx(0.26),
                   "fills": [{"price": 0.40, "quantity": 10.0}, {"price": 0.45, "quantity": 5.0}]}
    no = order_cost(book, "no", 22, {"fee_type": "quadratic_theta", "taker_rate": 0.04, "exponent": 2})
    assert no["fills"] == [{"price": pytest.approx(0.65), "quantity": 20.0}, {"price": pytest.approx(0.70), "quantity": 2.0}]
    assert no["fees"] == pytest.approx(0.04 * (20 * (0.65 * 0.35) ** 2 + 2 * (0.70 * 0.30) ** 2))
    free = order_cost(book, "yes", 1, {"fee_type": "none", "taker_rate": 0.0})
    assert free == {"cost": pytest.approx(0.40), "fees": 0.0, "fills": [{"price": 0.40, "quantity": 1.0}]}
    with pytest.raises(ValueError, match="the book offers 20 contracts on yes; the order asks for 25"):
        order_cost(book, "yes", 25, kalshi_fee)
    with pytest.raises(Unavailable, match="fee the market charges at the as-of is not stated"):
        order_cost(book, "yes", 1, None)
    with pytest.raises(Unavailable, match="not known level by level"):
        order_cost(None, "yes", 1, kalshi_fee)


# ---------------------------------------------------------------- the venues behind the store

PM_MARKET = {"id": "516710", "question": "Will the US effective tariff rate be above 20% on March 31, 2027?",
             "description": "Resolves Yes if the Yale Budget Lab's effective tariff rate is above 20% on March 31.",
             "resolutionSource": "https://budgetlab.yale.edu", "endDate": "2027-04-01T03:59:00Z",
             "closed": False, "active": True,
             "clobTokenIds": json.dumps(["Y1", "N1"]), "outcomes": json.dumps(["Yes", "No"])}
PM_EVENT = {"id": "9", "title": "Tariffs", "endDate": "2027-04-01T03:59:00Z", "tags": [{"label": "Tariffs"}]}


def polymarket_route(method, path, query, body):
    if path == "gamma-api.polymarket.com/search-v2":
        return 200, {"events": [{**PM_EVENT, "markets": [PM_MARKET]}],
                     "pagination": {"hasMore": False, "totalResults": 1}}
    if path == "gamma-api.polymarket.com/markets" and "id" in query:  # a market's record, the list form
        return 200, [{**PM_MARKET, "events": [PM_EVENT]}] if query["id"] == ["516710"] else []
    if path == "gamma-api.polymarket.com/markets/516710/tags":
        return 200, [{"id": "1", "label": "Tariffs", "slug": "tariffs"}]
    if path == "gamma-api.polymarket.com/events/9":
        return 200, {**PM_EVENT, "markets": [PM_MARKET]}
    if path.startswith("gamma-api.polymarket.com/"):
        return 404, {"error": "not found"}
    if path == "clob.polymarket.com/books":
        return 200, [dict(PM_BOOK, asset_id=b["token_id"], asks=PM_BOOK["asks"] + [{"price": "0.50", "size": "2000"}])
                     for b in body]
    if path == "data-api.polymarket.com/v2/prices-history":
        stamp = int(datetime.fromisoformat("2026-10-01T12:00:00+00:00").timestamp())
        return 200, {"data": [{"timestamp": stamp, "price": 0.42, "resolution_seconds": 43200}],
                     "pagination": {"next_cursor": None}}
    raise AssertionError(f"unexpected {method} {path}")


KS = "external-api.kalshi.com/trade-api/v2"
KS_MARKET = {"ticker": "KXFED-26DEC-T4.50", "event_ticker": "KXFED-26DEC", "strike_type": "greater", "floor_strike": 4.5,
             "title": "Will the upper bound of the federal funds rate be above 4.50% following the Fed's Dec 9, 2026 "
                      "meeting?", "rules_primary": "...published on the Federal Reserve's official website...",
             "close_time": "2026-12-09T18:55:00Z", "open_time": "2026-01-01T00:00:00Z", "status": "active",
             "expected_expiration_time": "2026-12-09T19:05:00Z", "market_type": "binary",
             "yes_sub_title": "Above 4.50%", "last_price_dollars": "0.3300", "volume_fp": "5000.00"}
KS_EVENT = {"event_ticker": "KXFED-26DEC", "series_ticker": "KXFED", "title": "Fed funds rate after Dec 2026 meeting?",
            "sub_title": "On Dec 9, 2026", "category": "Economics", "mutually_exclusive": True, "settlement_sources": []}
KS_SERIES = {"ticker": "KXFED", "title": "Fed funds rate", "fee_type": "quadratic", "fee_multiplier": 1,
             "settlement_sources": [{"name": "Federal Reserve", "url": "https://www.federalreserve.gov"}]}


KS_EVENT_27 = {**KS_EVENT, "event_ticker": "KXFED-27JAN", "title": "Fed funds rate after Jan 2027 meeting?"}
KS_MARKET_27 = {**KS_MARKET, "ticker": "KXFED-27JAN-T4.50", "event_ticker": "KXFED-27JAN", "close_time": "2027-01-27T18:55:00Z"}
SERIES_EVENTS = {"KXFED": [(KS_EVENT, KS_MARKET), (KS_EVENT_27, KS_MARKET_27)]}


def kalshi_route(method, path, query, body):
    if path == "api.elections.kalshi.com/v1/search/series":
        return 200, {"current_page": [{"event_ticker": "KXFED-26DEC", "series_ticker": "KXFED",
                                       "series_title": "Fed funds rate", "markets": [{"ticker": KS_MARKET["ticker"]}]}],
                     "next_cursor": None}
    if path == f"{KS}/events" and "series_ticker" in query:  # a series' events, open ones by default
        nested = query.get("with_nested_markets") == ["true"]
        return 200, {"events": [{**e, **({"markets": [m]} if nested else {})} for e, m in SERIES_EVENTS.get(
            query["series_ticker"][0], [])], "cursor": ""}
    if path == f"{KS}/events" and "tickers" in query:
        return 200, {"events": [{**KS_EVENT, "markets": [KS_MARKET]}], "cursor": ""}
    if path == f"{KS}/events/KXFED-26DEC":
        return 200, {"event": {**KS_EVENT, **({"markets": [KS_MARKET]} if query.get("with_nested_markets") else {})},
                     "markets": []}
    if path == f"{KS}/series/KXFED":
        return 200, {"series": KS_SERIES}
    if path == f"{KS}/series/fee_changes":
        return 200, {"series_fee_change_arr": []}
    if path == f"{KS}/events/fee_changes":
        return 200, {"event_fee_changes": [], "cursor": ""}
    if path == f"{KS}/markets/trades":
        return 200, {"trades": [], "cursor": ""}
    if path == f"{KS}/markets/orderbooks":
        return 200, {"orderbooks": [{"ticker": t, **KS_BOOK} for t in query["tickers"]]}
    if path == f"{KS}/markets" and "tickers" in query:
        return 200, {"markets": [KS_MARKET], "cursor": ""}
    if path == f"{KS}/markets/KXFED-26DEC-T4.50":
        return 200, {"market": KS_MARKET}
    if path == f"{KS}/series/KXFED/markets/KXFED-26DEC-T4.50/candlesticks":
        end = int(datetime.fromisoformat("2026-10-02T04:00:00+00:00").timestamp())  # midnight in New York
        return 200, {"candlesticks": [{"end_period_ts": end, "yes_bid": {"close_dollars": "0.3000"},
                                       "yes_ask": {"close_dollars": "0.3400"}}]}
    raise AssertionError(f"unexpected {method} {path}")


def flat(found):
    """The markets of a search's or a family's rows, in order, each row read by its columns."""
    rows = found["markets"] if "markets" in found and "families" not in found else \
        [row for f in found["families"] for row in f.get("markets", [])]
    return [dict(zip(found["columns"], row)) for row in rows]


def proposition(store):
    return store.add("Proposition", {"title": "T", "text": "The effective tariff rate reaches 25%", "comparison": "occurs",
                                     "window_end": "2027-03-31", "kind": "hitting"})


def test_search_markets_asks_each_venues_own_search_records_the_search_and_keeps_no_result(world):
    t = proposition(world.store)
    found = world.search_markets("Fed December", t["id"])
    asked = [(p, q) for _, p, q, _ in world.seen["kalshi"].seen if p.endswith("/v1/search/series")]
    assert asked == [("api.elections.kalshi.com/v1/search/series", {"query": ["Fed December"]})]
    assert [(p, q.get("q")) for _, p, q, _ in world.seen["polymarket"].seen if p.endswith("/search-v2")] == \
        [("gamma-api.polymarket.com/search-v2", ["Fed December"])]
    assert [f["family"] for f in found["families"]] == ["family:polymarket:9", "family:kalshi:KXFED-26DEC"]
    kalshi_family = found["families"][1]
    assert kalshi_family["title"] == "Fed funds rate after Dec 2026 meeting?" and "subtitle" not in kalshi_family
    market = flat({"columns": found["columns"], "families": [kalshi_family]})[0]
    assert market["id"] == "market:kalshi:KXFED-26DEC-T4.50" and market["closes"] == "2026-12-09"
    assert market["price"] == pytest.approx(0.335)  # the midpoint of 0.32 and 0.35, from its book
    assert found["returned"] == 2 and "kept" not in found
    shown = world.store.get(t["id"])["searches"][0]  # its words, how many it returned; its results by its id
    assert shown["query"] == "Fed December" and shown["returned"] == 2 and "results" not in shown
    assert world.store.get(shown["id"])["results"] == ["market:polymarket:516710", "market:kalshi:KXFED-26DEC-T4.50"]
    assert world.store.db.execute("SELECT count(*) FROM market").fetchone()[0] == 0
    assert world.store.db.execute("SELECT count(*) FROM data").fetchone()[0] == 0
    with pytest.raises(StoreError, match="your own words"):
        world.search_markets("  ", t["id"])


def test_a_family_is_read_whole_from_its_venue_and_nothing_is_kept(world):
    found = world.read_family("family:kalshi:KXFED-26DEC")
    assert (found["type"], found["title"], found["closes"]) == \
        ("Family", "Fed funds rate after Dec 2026 meeting?", "2026-12-09")
    assert flat(found)[0]["id"] == "market:kalshi:KXFED-26DEC-T4.50" and flat(found)[0]["price"] == pytest.approx(0.335)
    assert world.read_family("family:polymarket:9")["markets"][0][0] == "market:polymarket:516710"
    assert world.store.db.execute("SELECT count(*) FROM data").fetchone()[0] == 0
    with pytest.raises(StoreError, match="lists no family"):
        world.read_family("family:polymarket:404")


def test_a_linked_market_enters_with_its_record_and_its_book_is_fetched_on_first_read(world):
    t = proposition(world.store)
    link = world.store.link(t["id"], "asks", "market:polymarket:516710")
    market = world.store.get("market:polymarket:516710")
    assert [d["kind"] for d in market["data"]] == ["market", "book", "history"]  # both fetched on its first read
    derived = market["derived"]
    assert derived["question"]["value"] == PM_MARKET["question"]
    assert derived["family"]["value"] == {"id": "family:polymarket:9", "title": "Tariffs"}  # its event, from the record
    assert derived["resolution_source"]["value"] == "https://budgetlab.yale.edu"
    assert derived["close_time"]["value"] == "2027-03-31"  # its endDate
    assert (derived["bid"]["value"], derived["ask"]["value"]) == (0.32, 0.35)  # the best on each side
    assert derived["price"]["value"] == pytest.approx(0.335) and derived["open"]["value"] is True
    world.store.get("market:polymarket:516710")
    fetched = [p for m, p, q, b in world.seen["polymarket"].seen]
    assert sum(p.endswith("/books") for p in fetched) == 1 and sum(p.endswith("/prices-history") for p in fetched) == 1
    assert not {"metric", "comparison", "value"} & set(derived)  # the code reads no terms out of the question
    assert link["to"] == "market:polymarket:516710"


def _two_days(tmp_path):
    """A world whose Kalshi market has a daily candle for Oct 1 and for Oct 2, 2026, each ending at New York's
    midnight."""
    from conftest import paid
    from harness.derive import default_registry
    from harness.venues import Venues
    from harness.web import Web
    from harness.world import World

    def route(method, path, query, body):
        if path == f"{KS}/series/KXFED/markets/KXFED-26DEC-T4.50/candlesticks":
            end = lambda s: int(datetime.fromisoformat(s).timestamp())
            return 200, {"candlesticks": [
                {"end_period_ts": end("2026-10-02T04:00:00+00:00"), "yes_bid": {"close_dollars": "0.3000"},
                 "yes_ask": {"close_dollars": "0.3400"}},
                {"end_period_ts": end("2026-10-03T04:00:00+00:00"), "yes_bid": {"close_dollars": "0.4000"},
                 "yes_ask": {"close_dollars": "0.4400"}}]}
        return kalshi_route(method, path, query, body)
    store = Store.create(tmp_path / "scratch-two-days", registry=default_registry())
    (pm, _), (ks, seen) = adapter("polymarket", polymarket_route), adapter("kalshi", route)
    w = World(store, Venues({"polymarket": pm, "kalshi": ks}, paid()), Web(key="test"))
    return w, seen


MID = "market:kalshi:KXFED-26DEC-T4.50"


def test_a_market_read_with_get_comes_with_its_history_to_the_as_of(tmp_path):
    """MATH.md 3.3: a market read whole with get comes with its history, its price on each past day; at a past as-of it
    ends there, and the market stands as that day's close gives it, from the one history read."""
    w, seen = _two_days(tmp_path)
    today = w.read_market(MID)
    assert today["history"] == {"2026-10-01": pytest.approx(0.32), "2026-10-02": pytest.approx(0.42)}
    assert today["price"] == pytest.approx(0.335)  # today from its book
    w.store.add("Portfolio", {"asof": "2026-10-01"})
    seen.seen.clear()
    then = w.read_market(MID)
    assert then["history"] == {"2026-10-01": pytest.approx(0.32)}  # nothing after the as-of
    assert (then["bid"], then["ask"], then["price"]) == (0.30, 0.34, pytest.approx(0.32))  # Oct 1's close
    assert sum(p.endswith("/candlesticks") for _, p, _, _ in seen.seen) == 1
    w.store.close()


def test_a_kept_markets_history_at_a_past_as_of_ends_there(tmp_path):
    w, _ = _two_days(tmp_path)
    w.store.add("Portfolio", {"asof": "2026-10-01"})
    w.store.link(proposition(w.store)["id"], "asks", MID)
    assert w.store.get(MID)["derived"]["history"]["value"] == {"2026-10-01": pytest.approx(0.32)}
    w.store.close()


def test_a_markets_settlement_is_its_venues_word_and_at_a_past_as_of_what_it_was_by_then(tmp_path):
    """ONTOLOGY.md 2.1; MATH.md 3.3: a market's settlement as its venue reports it, kept or read with get: Kalshi's
    finalized market with its result; at an as-of before it settled, not settled; an open market, not settled."""
    from conftest import paid
    from harness.derive import default_registry
    from harness.venues import Venues
    from harness.web import Web
    from harness.world import World
    settled = {**KS_MARKET, "status": "finalized", "result": "yes", "settlement_ts": "2026-10-10T19:05:00.20348Z"}

    def route(method, path, query, body):
        if path == f"{KS}/markets" and "tickers" in query:
            return 200, {"markets": [settled], "cursor": ""}
        if path == f"{KS}/markets/KXFED-26DEC-T4.50":
            return 200, {"market": settled}
        if path == f"{KS}/events" and "tickers" in query:
            return 200, {"events": [{**KS_EVENT, "markets": [settled]}], "cursor": ""}
        return kalshi_route(method, path, query, body)
    store = Store.create(tmp_path / "scratch-settled", registry=default_registry())
    (pm, _), (ks, _) = adapter("polymarket", polymarket_route), adapter("kalshi", route)
    w = World(store, Venues({"polymarket": pm, "kalshi": ks}, paid()), Web(key="test"))
    said = {"settled": True, "result": 1, "at": "2026-10-10T19:05:00Z"}  # a UTC time to the second
    assert w.read_market(MID)["settlement"] == said
    w.store.link(proposition(w.store)["id"], "asks", MID)
    assert w.store.get(MID)["derived"]["settlement"]["value"] == said
    assert w.read_market("market:polymarket:516710")["settlement"] == {"settled": False, "result": None}
    w.store.add("Portfolio", {"asof": "2026-10-09"})  # the day before it settled
    assert w.store.get(MID)["derived"]["settlement"]["value"] == {"settled": False, "result": None}
    assert w.read_market(MID)["settlement"] == {"settled": False, "result": None}
    w.store.close()


def test_a_past_as_of_store_quotes_a_kept_market_from_its_history(world):
    world.store.add("Portfolio", {"asof": "2026-10-01"})
    t = proposition(world.store)
    world.store.link(t["id"], "asks", "market:polymarket:516710")
    derived = world.store.get("market:polymarket:516710")["derived"]
    # Polymarket's history is a price a day: no quote, and so no price, at a past as-of with no book kept by then
    assert (derived["bid"]["value"], derived["ask"]["value"], derived["price"]["value"], derived["depth"]["value"]) == \
        (None, None, None, None)
    assert [d["kind"] for d in world.store.get("market:polymarket:516710")["data"]] == ["market", "history"]


def fetch_route(page_text):
    def route(method, path, query, body):
        assert path == "api.anthropic.com/v1/messages" and body["tools"][0]["type"] == "web_fetch_20260309"
        return 200, {"stop_reason": "end_turn", "content": [
            {"type": "web_fetch_tool_result", "content": {"type": "web_fetch_result", "url": "https://example.org/rate",
                                                          "retrieved_at": "2026-11-04T20:00:00Z",
                                                          "content": {"type": "document", "title": "Rate",
                                                                      "source": {"type": "text", "media_type": "text/plain",
                                                                                 "data": page_text}}}},
            {"type": "text", "text": "OK"}]}
    return route


def test_a_fetched_pages_excerpt_is_its_body_after_the_front_matter_it_opens_with(world):
    """A fetched page opens with its front matter, its URL, title and the site's descriptions of it, which the fetch
    puts first; the excerpt fetch_source returns is the page's body, the text passages are found in, so a passage can be
    quoted from it as it is."""
    front = ("---\ncanonical: https://example.org/rate\nmeta-article:published_time: 2026-10-30T10:00:00+00:00\n"
             "meta-description: The rate, and what it means for tariffs on imports in the year ahead and after\n---\n")
    world.web = Web(key="test", opener=Opener(fetch_route(front + "\nPublished October 30, 2026\n\nThe rate is 22.")))
    page = world.fetch_source("https://example.org/rate")
    assert page["excerpt"] == "Published October 30, 2026\n\nThe rate is 22."
    assert page["length"] == len(world.store.source_row(page["id"])["text"])  # the page whole, as get reads it


def test_a_page_fetched_with_the_words_a_search_quoted_returns_the_passages_that_hold_them_to_cite(world):
    """fetch_source given the words a search's quoted passage gave returns, with the page, the passages that contain
    them, as get(id, text) finds them: each the page's own words, which a statement cites as its basis's passages in
    the next call. Quotes, case and spacing as the search gave them do not matter to the finding."""
    from test_store import add_proposition
    opener = Opener(fetch_route("Published October 30, 2026\n\n## The rate\n\nThe effective tariff rate is 22 "
                                "percent, the highest since 1935.\n\nIt was 2.4 percent a year ago."))
    world.web = Web(key="test", opener=opener)
    page = world.fetch_source("https://example.org/rate", "the effective  tariff rate is 22 percent")
    assert page["found"] == 1 and page["excerpt"].startswith("Published")
    assert page["passages"] == [{"at": page["passages"][0]["at"], "under": "The rate",
                                 "passage": "The effective tariff rate is 22 percent, the highest since 1935."}]
    got = world.store.get(page["id"], "the effective  tariff rate is 22 percent")
    assert {k: got[k] for k in ("found", "passages")} == {k: page[k] for k in ("found", "passages")}
    t = add_proposition(world.store)
    world.store.link(t["id"], "evidence", page["id"], {"passages": [page["passages"][0]["passage"]]})
    assert world.store.get(page["id"])["kept"] is True and len(opener.seen) == 1
    assert "passages" not in world.fetch_source("https://example.org/rate")  # no words: the page and its excerpt


def test_fetch_source_holds_the_page_and_keeps_nothing_until_it_is_used(world):
    opener = Opener(fetch_route("Published October 30, 2026\n\nThe effective tariff rate is 22 percent."))
    world.web = Web(key="test", opener=opener)
    page = world.fetch_source("https://example.org/rate")
    assert page["kept"] is False and page["dated"] is None and page["excerpt"].startswith("Published")
    assert world.store.db.execute("SELECT count(*) FROM source WHERE url IS NOT NULL").fetchone()[0] == 0
    assert world.fetch_source("https://example.org/rate")["id"] == page["id"] and len(opener.seen) == 1
    world.store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct", "kind": "level",
                               "level": {"value": 22, "source_id": page["id"]}})
    assert world.store.get(page["id"])["kept"] is True


def test_a_pages_dated_is_stated_by_edit_citing_the_page_itself(world):
    world.web = Web(key="test", opener=Opener(fetch_route("Published October 30, 2026\n\nThe rate is 22 percent.")))
    sid = world.fetch_source("https://example.org/rate")["id"]
    other = world.store.put_page("https://example.org/other", "Another page.", retrieved_at=NOW)
    with pytest.raises(StoreError, match="cites the page itself"):
        world.store.edit(sid, "dated", "2026-10-30")  # without a source
    with pytest.raises(StoreError, match="cites the page itself"):
        world.store.edit(sid, "dated", "2026-10-30", other)  # citing another page
    with pytest.raises(StoreError, match="as fetched"):
        world.store.edit(sid, "text", "Something else.", sid)
    held = world.store.edit(sid, "dated", "2026-10-30", sid)
    assert held["kept"] is False and held["dated"] == "2026-10-30"  # a held page states it before it is used
    world.store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct", "kind": "level",
                               "level": {"value": 22, "source_id": sid}})
    assert world.store.get(sid)["kept"] is True and world.store.get(sid)["dated"] == "2026-10-30"
    kept = world.store.edit(sid, "dated", "2026-10-31", sid)  # and a kept Source too
    assert kept["dated"] == "2026-10-31"
    assert world.store.db.execute("SELECT dated FROM source WHERE id = ?", (sid,)).fetchone()[0] == "2026-10-31"
    with pytest.raises(StoreError, match="cites the page itself"):
        world.store.edit(sid, "dated", "2026-11-01")
    assert world.store.get(sid)["dated"] == "2026-10-31"  # the refused edit wrote nothing


def test_a_past_store_reads_its_markets_histories_side_by_side(world):
    """A search from a store whose as-of is past quotes each market open then from its history at the as-of, the
    histories read several at a time, as the first read reads the bars."""
    import threading
    import time as clock
    world.store.add("Portfolio", {"asof": "2026-10-01"})
    many = [{**PM_MARKET, "id": str(900 + i), "question": f"Question {i}?", "startDate": "2026-01-01T00:00:00Z",
             "clobTokenIds": json.dumps([f"Y{i}", f"N{i}"])} for i in range(6)]
    state = {"now": 0, "most": 0}
    lock = threading.Lock()

    def route(method, path, query, body):
        if path == "gamma-api.polymarket.com/search-v2":
            assert "events_status" not in query  # a past as-of: every status, those open then kept
            return 200, {"events": [{"id": "9", "title": "Many", "tags": [], "markets": many}],
                         "pagination": {"hasMore": False, "totalResults": 6}}
        if path == "data-api.polymarket.com/v2/prices-history":
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            clock.sleep(0.05)
            with lock:
                state["now"] -= 1
        return polymarket_route(method, path, query, body)

    world.venues.clients["polymarket"], opener = adapter("polymarket", route)
    world.venues.clients.pop("kalshi")
    found = world.search_markets("tariffs", proposition(world.store)["id"])
    prices = flat(found)
    assert len(prices) == 6 and found["prices_as_of"] == "2026-10-01"
    assert all(m["price"] is None for m in prices)  # Polymarket's history a price alone: no quote, so no price
    assert sum(p.endswith("/books") for _, p, _, _ in opener.seen) == 0  # no book: the as-of is past
    assert state["most"] > 1  # side by side


def test_requests_from_several_threads_keep_the_venues_interval_between_them():
    """Kalshi reads twenty a second: requests made side by side still start an interval apart."""
    import time as clock
    from concurrent.futures import ThreadPoolExecutor
    starts = []
    client = Http(opener=Opener(lambda method, path, query, body: (starts.append(clock.monotonic()) or 200, {})),
                  pause=0, interval=0.05)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: client.call("GET", f"https://api.example.org/x/{i}"), range(4)))
    assert len(starts) == 4 and max(starts) - min(starts) >= 0.10  # 0.15 reserved; a late wakeup takes at most one


def test_search_markets_returns_each_venues_families_with_their_open_markets_and_a_large_one_as_what_each_asks(world):
    closed = {**PM_MARKET, "id": "800", "question": "Closed?", "closed": True, "clobTokenIds": json.dumps(["Y8", "N8"])}
    other = {**PM_MARKET, "id": "801", "question": "Other?", "clobTokenIds": json.dumps(["Y9", "N9"])}
    ladder = [{**PM_MARKET, "id": str(700 + i), "question": f"Rung {i}?", "groupItemTitle": f"Rung {i}",
               "endDate": f"2027-0{1 + i % 9}-01T04:59:00Z", "clobTokenIds": json.dumps([f"L{i}", f"M{i}"])}
              for i in range(14)]

    def route(method, path, query, body):
        if path == "gamma-api.polymarket.com/search-v2":
            return 200, {"events": [{"id": "9", "title": "Tariffs", "tags": [], "markets": [PM_MARKET, closed]},
                                    {"id": "10", "title": "Other", "tags": [], "markets": [other]},
                                    {"id": "11", "title": "Ladder", "tags": [], "markets": ladder},
                                    {"id": "12", "title": "Over", "tags": [], "markets": [closed]}],
                         "pagination": {"hasMore": False, "totalResults": 4}}
        if path == "clob.polymarket.com/books":
            return 200, []
        return polymarket_route(method, path, query, body)

    world.venues.clients["polymarket"], _ = adapter("polymarket", route)
    world.venues.clients.pop("kalshi")
    found = world.search_markets("tariff", proposition(world.store)["id"])
    assert [f["family"] for f in found["families"]] == ["family:polymarket:9", "family:polymarket:10",
                                                        "family:polymarket:11"]  # the venue's order; one with none open goes
    assert [row[0] for row in found["families"][0]["markets"]] == ["market:polymarket:516710"]  # only those open
    assert found["families"][0]["markets"][0][1] == PM_MARKET["question"]
    large = found["families"][2]
    assert "markets" not in large and large["open_markets"] == 14
    assert large["questions"] == [f"Rung {i}" for i in range(14)] and "ladders" not in large  # no strike as data
    assert large["span"] == {"closes": ["2026-12-31", "2027-09-01"]}
    assert found["returned"] == 2 and found["columns"] == ["id", "question", "closes", "bid", "ask", "price",
                                                           "depth ($)"]  # its unit at the column, in dollars


def test_a_searchs_row_shows_the_books_bid_and_ask_beside_its_price(world):
    """MATH.md 3.3: a market's price is the midpoint of its best bid and ask, which says nothing of how far apart they
    are; so each row a search returns shows the bid and the ask beside it, and a wide book reads as wide."""
    def route(method, path, query, body):
        if path == "clob.polymarket.com/books":
            return 200, [{"asset_id": b["token_id"], "bids": [{"price": "0.05", "size": "100"}],
                          "asks": [{"price": "0.79", "size": "100"}]} for b in body]
        return polymarket_route(method, path, query, body)

    world.venues.clients["polymarket"], _ = adapter("polymarket", route)
    world.venues.clients.pop("kalshi")
    row = flat(world.search_markets("tariffs", proposition(world.store)["id"]))[0]
    assert (row["bid"], row["ask"], row["price"]) == (0.05, 0.79, 0.42)


def test_the_venues_families_are_interleaved_rank_by_rank():
    from harness.world import interleave
    found = {"polymarket": ["p1", "p2", "p3"], "kalshi": ["k1"]}
    assert interleave(found) == [("polymarket", "p1"), ("kalshi", "k1"), ("polymarket", "p2"), ("polymarket", "p3")]
    assert [f for _, f in interleave({"a": ["a1", "a2"], "b": ["b1", "b2"]})] == ["a1", "b1", "b2", "a2"]


def test_a_venue_that_does_not_answer_makes_the_search_fail_to_be_asked_again(world, monkeypatch):
    import harness.venues.markets as markets
    monkeypatch.setattr(markets, "WAIT", 0.0)

    def down(method, path, query, body):
        return 503, {"error": "unavailable"}

    world.venues.clients["kalshi"], seen = adapter("kalshi", down)
    with pytest.raises(StoreError, match="Kalshi did not answer just now; try again shortly"):
        world.search_markets("Fed December", proposition(world.store)["id"])
    assert len(seen.seen) == markets.ATTEMPTS  # each read tried again, as every read is


# ---------------------------------------------------------------- Polymarket US

US_MARKET = {"id": 1003057, "slug": "rdc-usfed-fomc-2026-12-09-hike25", "question": "Fed Decision in December",
             "title": "25 bps Increase", "status": "MARKET_STATUS_OPEN", "active": True, "closed": False,
             "startDate": "2026-09-22T18:11:21Z", "endDate": "2026-12-24T04:00:00Z", "feeCoefficient": 0.095,
             "description": "This market will settle to Yes if the Federal Reserve increases the upper bound of the target "
                            "federal funds rate by 25 basis points at the December 2026 FOMC meeting. Outcome sourced "
                            "from the Federal Reserve.",
             "bestBidQuote": {"value": "0.78", "currency": "USD"}, "bestAskQuote": {"value": "0.79", "currency": "USD"},
             "marketSides": [{"long": True, "description": "Yes"}, {"long": False, "description": "No"}],
             "outcomes": '["Yes","No"]', "assetPriceTerms": None}
US_EVENT = {"id": "122355", "slug": "usfed-fomc-2026-12-09", "title": "Fed Decision in December", "category": "macro",
            "endDate": "2026-12-10T04:59:00Z", "markets": [US_MARKET]}


def test_polymarket_us_reads_a_market_with_its_event_a_family_whole_and_its_settlement():
    resolved = {**US_MARKET, "slug": "dipcc-usiran-nucl-2026-10-31", "status": "MARKET_STATUS_RESOLVED",
                "question": "US-Iran Nuclear Deal?", "title": "By October 31, 2026"}

    def route(method, path, query, body):
        if path.endswith("/v1/market/slug/rdc-usfed-fomc-2026-12-09-hike25"):
            return 200, {"market": US_MARKET}
        if path.endswith("/v1/market/slug/dipcc-usiran-nucl-2026-10-31"):
            return 200, {"market": resolved}
        if path.endswith("/v1/search"):  # the market's event, found by its question and its slug among the event's
            other = {**US_EVENT, "id": "1", "slug": "other", "markets": [{**US_MARKET, "slug": "other-market"}]}
            return 200, {"events": [other, US_EVENT] if query["query"] == ["Fed Decision in December"] else []}
        if path.endswith("/v1/events/slug/usfed-fomc-2026-12-09"):
            return 200, {"event": US_EVENT}
        if path.endswith("/v1/markets/dipcc-usiran-nucl-2026-10-31/settlement"):
            return 200, {"slug": "dipcc-usiran-nucl-2026-10-31", "settlement": 0}
        return 404, {}
    venue, seen = adapter("polymarket_us", route)
    record = venue.market("rdc-usfed-fomc-2026-12-09-hike25")["payload"]
    assert record["event"]["slug"] == "usfed-fomc-2026-12-09"
    listing = polymarket_us.listing(record)
    assert listing["question"] == "25 bps Increase"  # the market's own words, which its family's title completes
    assert listing["family"] == {"id": "family:polymarket_us:usfed-fomc-2026-12-09", "title": "Fed Decision in December"}
    assert listing["resolution_source"] is None and "Outcome sourced from the Federal Reserve" in listing["rules"]
    assert (listing["close_time"], listing["strike"]) == ("2026-12-23", None)
    assert polymarket_us.paid_on(record) is False and polymarket_us.PAID["label"] is None  # nothing on a position
    assert listing["fees"] == {"fee_type": "quadratic_theta", "taker_rate": 0.095}
    whole = venue.family("usfed-fomc-2026-12-09")
    assert [mid for mid, _ in polymarket_us.records_of(whole)] == ["market:polymarket_us:rdc-usfed-fomc-2026-12-09-hike25"]
    assert polymarket_us.family_of(whole) == {"title": "Fed Decision in December", "closes": "2026-12-23"}
    assert venue.settlement({"market": resolved})["payload"]["settled"] is True
    assert venue.settlement({"market": resolved})["payload"]["result"] == 0
    kept = venue.market("dipcc-usiran-nucl-2026-10-31")["payload"]  # a resolved market's record carries its price
    assert kept["settlement"] == 0 and polymarket_us.settlement(kept)["result"] == 0
    assert "settlement" not in record  # an open one's has none to carry
    assert venue.settlement({"market": US_MARKET})["payload"] == \
        {"slug": US_MARKET["slug"], "status": "MARKET_STATUS_OPEN", "settlement": None, "settled": False, "result": None}
    ranges = {**US_MARKET, "assetPriceTerms": {"marketType": "ASSET_PRICE_MARKET_TYPE_RANGE",
                                               "rangeLower": None, "rangeUpper": {"value": "55999.99"}}}
    assert polymarket_us.strike(ranges) == {"type": "less_or_equal", "cap": 55999.99}  # a bottom tail, inclusive


def test_a_large_familys_markets_that_differ_only_by_their_strikes_are_a_ladder_by_its_ends_and_its_strikes():
    """MATH.md 3.3: past LARGE open markets a family gives each market's question in its venue's words, and where the
    questions differ only by the strikes the venue states as data, the ladder's first and last question and its
    strikes: the first, last and step where evenly spaced, otherwise each. Kalshi's rungs are one ladder where they share
    a strike type and what the strike is of (its custom strike: one ladder per player); a band's strike is its floor and
    cap, and the open bands at its ends, of another type each, are questions of their own."""
    from harness.world import entry

    def market(ticker, label, kind, floor=None, cap=None, custom=None):
        m = {"ticker": ticker, "title": f"{label}?", "yes_sub_title": label, "close_time": "2026-11-16T05:00:00Z",
             "strike_type": kind, "floor_strike": floor, "cap_strike": cap, "custom_strike": custom}
        return f"market:kalshi:{ticker}", {"market": m, "event": {"event_ticker": "KXE", "title": "E"}}

    index = [market(f"N{i}", f"{26300 + 10 * i:,} or above", "greater_or_equal", floor=26300 + 10 * i,
                    custom={"Index": "Nasdaq-100"}) for i in range(14)]
    got = entry("kalshi", "family:kalshi:KXE", "Nasdaq-100 price?", "2026-11-16", index, {}, many=True)
    assert got["open_markets"] == 14 and "questions" not in got
    assert got["ladders"] == [{"first": "26,300 or above", "last": "26,430 or above", "rungs": 14,
                               "strikes": {"first": 26300, "last": 26430, "step": 10}}]
    players = [market(f"{who}{y}", f"{who}: {y}+", "greater", floor=y - 0.5, custom={"football_player": who})
               for who in ("A", "B") for y in range(7)]
    got = entry("kalshi", "family:kalshi:KXE", "Passing yards", "2026-11-16", players, {}, many=True)
    assert [(l["first"], l["last"], l["rungs"], l["strikes"]) for l in got["ladders"]] == [
        ("A: 0+", "A: 6+", 7, {"first": -0.5, "last": 5.5, "step": 1.0}),
        ("B: 0+", "B: 6+", 7, {"first": -0.5, "last": 5.5, "step": 1.0})]
    bands = [market("LO", "$42.7499 or below", "less", cap=42.75)] + \
        [market(f"S{i}", f"${42.75 + i / 4} to {42.9999 + i / 4}", "between", floor=42.75 + i / 4,
                cap=round(42.9999 + i / 4, 4)) for i in range(12)] + [market("HI", "$45.75 or above", "greater", floor=45.7499)]
    got = entry("kalshi", "family:kalshi:KXE", "SOL price range?", "2026-11-16", bands, {}, many=True)
    assert got["questions"] == ["$42.7499 or below", "$45.75 or above"]
    assert got["ladders"][0]["rungs"] == 12 and got["ladders"][0]["strikes"] == {
        "first": [42.75, 42.9999], "last": [45.5, 45.7499], "step": 0.25}
    uneven = [market(f"U{i}", f"{x} or above", "greater_or_equal", floor=x) for i, x in
              enumerate([0, 1, 50, 100] + list(range(200, 1200, 100)))]
    got = entry("kalshi", "family:kalshi:KXE", "U", "2026-11-16", uneven, {}, many=True)
    assert got["ladders"][0]["strikes"] == [0, 1, 50, 100] + list(range(200, 1200, 100))
