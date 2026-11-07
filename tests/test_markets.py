"""Markets: the price each venue displays, the best bid and ask, depth, whether a market is open,
the venues' books and histories, close_time, search_markets, a market entering the store, and fetch_source and a page's
stated dated. The venue clients run against fake openers; nothing touches the network."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import datetime

import pytest

from harness.derive.markets import depth, past_price, priced
from harness.venues import hyperliquid, kalshi, polymarket
from harness.venues.http import Http
from harness.web import Web
from harness.store import StoreError
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


# ---------------------------------------------------------------- books: the same book at two venues

PM_BOOK = {"asset_id": "Y1", "bids": [{"price": "0.30", "size": "1000"}, {"price": "0.31", "size": "2000"},
                                      {"price": "0.32", "size": "500"}],  # the venue's order: worst first
           "asks": [{"price": "0.40", "size": "100"}, {"price": "0.36", "size": "3000"}, {"price": "0.35", "size": "1000"}]}
KS_BOOK = {"orderbook_fp": {"yes_dollars": [["0.3000", "1000.00"], ["0.3100", "2000.00"], ["0.3200", "500.00"]],
                            "no_dollars": [["0.6000", "100.00"], ["0.6400", "3000.00"], ["0.6500", "1000.00"]]}}


def test_polymarket_displays_the_midpoint_and_the_last_trade_where_the_spread_is_wider_than_ten_cents():
    book = polymarket.levels(PM_BOOK)  # best bid 0.32, best ask 0.35
    assert priced("polymarket", book, {**PM_BOOK, "last_trade_price": "0.33"}, {}) == \
        {"bid": 0.32, "ask": 0.35, "price": pytest.approx(0.335), "rule": "midpoint", "depth": depth(book)}
    ten = {"bids": [(0.30, 100.0)], "asks": [(0.40, 100.0)]}  # exactly ten cents is not wider
    assert priced("polymarket", ten, {"last_trade_price": "0.31"}, {})["price"] == pytest.approx(0.35)
    wide = {"bids": [(0.30, 100.0)], "asks": [(0.41, 100.0)]}
    assert priced("polymarket", wide, {"last_trade_price": "0.31"}, {})["price"] == 0.31
    assert priced("polymarket", wide, {"last_trade_price": "0.31"}, {})["rule"] == "last trade"
    one_side = {"bids": [(0.30, 100.0)], "asks": []}
    assert priced("polymarket", one_side, {"last_trade_price": "0.29"}, {})["price"] == 0.29
    assert priced("polymarket", wide, {}, {})["price"] is None  # wide, and never traded: no price


def test_kalshi_displays_the_midpoint_where_the_book_is_close_and_the_last_trade_otherwise():
    record = {"market": {"last_price_dollars": "0.2500", "volume_fp": "1200.00"}}
    assert priced("kalshi", kalshi.levels(KS_BOOK), KS_BOOK, record)["price"] == pytest.approx(0.335)
    wide = {"bids": [(0.10, 100.0)], "asks": [(0.40, 100.0)]}
    assert priced("kalshi", wide, {}, record)["price"] == 0.25  # the record's last trade
    assert priced("kalshi", wide, {"market": {"last_price_dollars": "0.2700", "volume_fp": "1.00"}}, record)[
        "price"] == 0.27  # the book's read of the market, made with it, before the record's
    assert priced("kalshi", wide, {}, {"market": {"last_price_dollars": "0.0000", "volume_fp": "0.00"}})[
        "price"] is None  # never traded


def test_hyperliquid_displays_the_mid_it_publishes():
    book = {"bids": [(0.13037, 10.0)], "asks": [(0.13038, 10.0)]}
    assert priced("hyperliquid", book, {"mid": "0.130375"}, {})["price"] == 0.130375
    assert priced("hyperliquid", {"bids": [], "asks": []}, {"mid": "0.5"}, {}) == \
        {"bid": None, "ask": None, "price": 0.5, "rule": "last trade", "depth": 0.0}  # an empty book: its last trade


def test_a_markets_price_is_unchanged_by_the_size_of_an_order():
    """A recorded book: the price is what the market shows, whatever any order would pay walking it."""
    found = json.loads((FIXTURES / "blockade_book.json").read_text())
    book = polymarket.levels(found["book"])
    shown = priced("polymarket", book, found["book"], {})
    assert (shown["bid"], shown["ask"], shown["rule"]) == (0.02, 0.026, "midpoint")
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
    settled = {"outcome": {"outcome": 7}, "question": {"settledNamedOutcomes": [7]}}
    assert hyperliquid.is_open(settled, "2026-11-05", False) is False
    assert hyperliquid.is_open({"outcome": {"outcome": 7}, "question": {}}, "2026-11-05", False) is True


def test_the_venues_books_read_as_yes_bids_and_asks_best_first_kalshis_asks_from_its_no_bids():
    pm, ks = polymarket.levels(PM_BOOK), kalshi.levels(KS_BOOK)
    assert pm["bids"][0] == (0.32, 500.0) and pm["asks"][0] == (0.35, 1000.0)
    assert ks == pm  # a NO bid at 0.65 is a YES ask at 0.35
    hl = hyperliquid.levels({"levels": [[{"px": "0.31", "sz": "2000", "n": 1}, {"px": "0.32", "sz": "500", "n": 1}],
                                        [{"px": "0.36", "sz": "3000", "n": 2}, {"px": "0.35", "sz": "1000", "n": 1}]]})
    assert hl["bids"][0] == (0.32, 500.0) and hl["asks"][0] == (0.35, 1000.0)


def test_history_is_a_daily_price_at_each_venue():
    utc = lambda s: int(datetime.fromisoformat(s).timestamp())
    pm = polymarket.daily({"data": [{"timestamp": utc("2026-11-03T00:00:00+00:00"), "price": 0.20},
                                    {"timestamp": utc("2026-11-03T12:00:00+00:00"), "price": 0.25},
                                    {"timestamp": utc("2026-11-04T18:30:00+00:00"), "price": 0.31},
                                    {"timestamp": utc("2026-11-04T00:00:00+00:00"), "price": 0.30}]})
    assert pm == {"2026-11-03": 0.25, "2026-11-04": 0.31}  # the last point inside each UTC day
    ks = kalshi.daily({"candlesticks": [
        {"end_period_ts": utc("2026-11-04T05:00:00+00:00"), "yes_bid": {"close_dollars": "0.2000"},
         "yes_ask": {"close_dollars": "0.2400"}},
        {"end_period_ts": utc("2026-11-05T05:00:00+00:00"), "yes_bid": {"close_dollars": "0.3000"},
         "yes_ask": {"close_dollars": "0.3400"}}]})
    assert ks == {"2026-11-03": pytest.approx(0.22), "2026-11-04": pytest.approx(0.32)}  # the mid of the closing bid and ask


def test_a_past_as_of_price_is_the_history_price_a_cent_either_side_and_its_depth_is_unknown():
    q = past_price({"2026-09-30": 0.40, "2026-10-01": 0.42, "2026-10-02": 0.45}, "2026-10-01")
    assert (q["bid"], q["ask"], q["price"], q["depth"], q["past"]) == (pytest.approx(0.41), pytest.approx(0.43), 0.42,
                                                                       None, True)


# ---------------------------------------------------------------- close_time: the venue's own field


def test_close_time_is_each_venues_own_end_date_and_no_date_is_read_from_the_text():
    question = "Will the US effective tariff rate be above 20% on March 31, 2027?"  # a date the code does not read
    pm = {"market": {"question": question, "description": "Resolves on April 15, 2027.",
                     "endDate": "2027-04-01T03:59:00Z"}, "event": {"endDate": "2027-06-30T12:00:00Z"}}
    assert polymarket.listing(pm)["close_time"] == "2027-03-31"  # the market's endDate, in New York
    assert polymarket.listing({"market": {"question": question}, "event": {"endDate": "2027-06-30T12:00:00Z"}})[
        "close_time"] == "2027-06-30"  # the event's, when the market states none
    ks = {"market": {"title": "Will the Fed cut by Dec 31, 2026?", "close_time": "2026-12-09T18:55:00Z"}}
    assert kalshi.listing(ks)["close_time"] == "2026-12-09"
    binary = {"outcome": {"name": "BTC above 100k", "description": "class:priceBinary|underlying:BTC|"
                                                                   "expiry:20261106-0200|targetPrice:100000"}}
    assert hyperliquid.listing(binary)["close_time"] == "2026-11-05"  # 02:00 UTC is the evening before in New York
    template = {"outcome": {"name": "template:policyRateDecrease", "description": ""},
                "question": {"name": "template:policyRateDecision",
                             "description": "decisionDeadline:20270127-1900|scheduledDecision:20261209-1900"}}
    assert hyperliquid.listing(template)["close_time"] is None  # a deployer's template states its own terms
    for listing in (polymarket.listing(pm), kalshi.listing(ks), hyperliquid.listing(binary)):
        assert "strike" not in listing


# ---------------------------------------------------------------- the venues behind the store

PM_MARKET = {"id": "516710", "question": "Will the US effective tariff rate be above 20% on March 31, 2027?",
             "description": "Resolves Yes if the Yale Budget Lab's effective tariff rate is above 20% on March 31.",
             "resolutionSource": "https://budgetlab.yale.edu", "endDate": "2027-04-01T03:59:00Z",
             "clobTokenIds": json.dumps(["Y1", "N1"]), "outcomes": json.dumps(["Yes", "No"])}


def polymarket_route(method, path, query, body):
    if path == "gamma-api.polymarket.com/public-search":
        return 200, {"events": [{"id": "9", "title": "Tariffs", "tags": [{"label": "Tariffs"}], "markets": [PM_MARKET]}],
                     "pagination": {"hasMore": False, "totalResults": 1}}
    if path == "gamma-api.polymarket.com/markets/516710":
        return 200, PM_MARKET
    if path == "gamma-api.polymarket.com/markets/516710/tags":
        return 200, [{"id": "1", "label": "Tariffs", "slug": "tariffs"}]
    if path.startswith("gamma-api.polymarket.com/markets/"):
        return 404, {"error": "not found"}
    if path == "clob.polymarket.com/books":
        return 200, [dict(PM_BOOK, asset_id=b["token_id"], asks=PM_BOOK["asks"] + [{"price": "0.50", "size": "2000"}])
                     for b in body]
    if path == "data-api.polymarket.com/v2/prices-history":
        stamp = int(datetime.fromisoformat("2026-10-01T12:00:00+00:00").timestamp())
        return 200, {"data": [{"timestamp": stamp, "price": 0.42, "resolution_seconds": 43200}],
                     "pagination": {"next_cursor": None}}
    raise AssertionError(f"unexpected {method} {path}")


KS_MARKET = {"ticker": "KXFED-26DEC-T4.50", "event_ticker": "KXFED-26DEC", "strike_type": "greater", "floor_strike": 4.5,
             "title": "Will the upper bound of the federal funds rate be above 4.50% following the Fed's Dec 9, 2026 "
                      "meeting?", "rules_primary": "...published on the Federal Reserve's official website...",
             "close_time": "2026-12-09T18:55:00Z", "open_time": "2026-01-01T00:00:00Z", "status": "active",
             "last_price_dollars": "0.3300", "volume_fp": "5000.00"}


def kalshi_route(method, path, query, body):
    if path == "api.elections.kalshi.com/trade-api/v2/events":
        return 200, {"events": [{"event_ticker": "KXFED-26DEC", "series_ticker": "KXFED", "category": "Economics",
                                 "markets": [KS_MARKET]}], "cursor": ""}
    if path == "api.elections.kalshi.com/trade-api/v2/markets/orderbooks":
        return 200, {"orderbooks": [{"ticker": t, **KS_BOOK} for t in query["tickers"]]}
    if path == "api.elections.kalshi.com/trade-api/v2/markets/KXFED-26DEC-T4.50/orderbook":
        return 200, KS_BOOK
    if path == "api.elections.kalshi.com/trade-api/v2/markets/KXFED-26DEC-T4.50":
        return 200, {"market": KS_MARKET}
    if path == "api.elections.kalshi.com/trade-api/v2/search/tags_by_categories":
        return 200, {"tags_by_categories": {"Economics": ["Fed", "Inflation"]}}
    raise AssertionError(f"unexpected {method} {path}")


def hyperliquid_route(method, path, query, body):
    if body and body.get("type") == "outcomeMeta":
        return 200, {"outcomes": [{"outcome": 7, "name": "template:policyRateDecrease", "description": "bank:Fed",
                                   "sideSpecs": [{"name": "Yes"}, {"name": "No"}]}], "questions": []}
    if body and body.get("type") == "l2Book":
        return 200, {"coin": body["coin"], "levels": [[{"px": "0.30", "sz": "10000", "n": 1}],
                                                      [{"px": "0.40", "sz": "10000", "n": 1}]]}
    if body and body.get("type") == "allMids":
        return 200, {"#70": "0.35", "BTC": "100000"}
    raise AssertionError(f"unexpected {method} {body}")


def flat(groups):
    """The markets of a search's events, in order."""
    return [m for g in groups for m in g["markets"]]


def proposition(store):
    return store.add("Proposition", {"title": "T", "text": "The effective tariff rate reaches 25%", "relation": "occurs",
                                     "window_end": "2027-03-31", "kind": "hitting"})


def test_search_markets_passes_the_venues_parameters_unchanged_records_the_search_and_keeps_no_result(world):
    t = proposition(world.store)
    params = {"series_ticker": "KXFED", "status": "open", "with_nested_markets": True, "limit": 2}
    found = world.search_markets("kalshi", "/events", params, t["id"])
    _, path, query, _ = world.seen["kalshi"].seen[0]
    assert path.endswith("/events") and query == {"series_ticker": ["KXFED"], "status": ["open"],
                                                  "with_nested_markets": ["true"], "limit": ["2"]}
    market = flat(found["events"])[0]
    assert market["id"] == "market:kalshi:KXFED-26DEC-T4.50" and market["close_time"] == "2026-12-09"
    assert "parsed" not in market
    assert (market["bid"], market["ask"], market["price"], market["price_rule"]) == (0.32, 0.35, pytest.approx(0.335),
                                                                                     "midpoint")
    assert market["open"] is True and market["kept"] is False
    assert world.store.get(t["id"])["searches"][0]["results"] == ["market:kalshi:KXFED-26DEC-T4.50"]
    assert world.store.db.execute("SELECT count(*) FROM market").fetchone()[0] == 0
    assert world.store.db.execute("SELECT count(*) FROM data").fetchone()[0] == 0
    hl = world.search_markets("hyperliquid", "outcomeMeta", {})
    assert flat(hl["events"])[0]["id"] == "market:hyperliquid:7" and flat(hl["events"])[0]["price"] == 0.35
    assert flat(hl["events"])[0]["depth"] == pytest.approx(4000)


def test_a_linked_market_enters_with_its_record_and_its_book_is_fetched_on_first_read(world):
    t = proposition(world.store)
    link = world.store.link(t["id"], "quoted_by", "market:polymarket:516710")
    market = world.store.get("market:polymarket:516710")
    assert [d["kind"] for d in market["data"]] == ["market", "book", "history"]  # both fetched on its first read
    derived = market["derived"]
    assert derived["question"]["value"] == PM_MARKET["question"] and derived["tags"]["value"] == ["Tariffs"]
    assert derived["yes_token"]["value"] == "Y1" and derived["close_time"]["value"] == "2027-03-31"  # its endDate
    assert (derived["bid"]["value"], derived["ask"]["value"]) == (0.32, 0.35)  # the best on each side
    assert derived["price"]["value"] == pytest.approx(0.335) and derived["open"]["value"] is None
    world.store.get("market:polymarket:516710")
    fetched = [p for m, p, q, b in world.seen["polymarket"].seen]
    assert sum(p.endswith("/books") for p in fetched) == 1 and sum(p.endswith("/prices-history") for p in fetched) == 1
    assert not {"metric", "relation", "value"} & set(derived)  # the code reads no terms out of the question
    assert link["to"] == "market:polymarket:516710"


def test_a_past_as_of_store_quotes_a_kept_market_from_its_history(world):
    world.store.add("Portfolio", {"asof": "2026-10-01"})
    t = proposition(world.store)
    world.store.link(t["id"], "quoted_by", "market:polymarket:516710")
    derived = world.store.get("market:polymarket:516710")["derived"]
    assert (derived["bid"]["value"], derived["ask"]["value"], derived["price"]["value"], derived["depth"]["value"]) == \
        (pytest.approx(0.41), pytest.approx(0.43), 0.42, None)
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


def test_fetch_source_holds_the_page_and_keeps_nothing_until_it_is_used(world):
    opener = Opener(fetch_route("Published October 30, 2026\n\nThe effective tariff rate is 22 percent."))
    world.web = Web(key="test", opener=opener)
    page = world.fetch_source("https://example.org/rate")
    assert page["kept"] is False and page["dated"] is None and page["excerpt"].startswith("Published")
    assert world.store.db.execute("SELECT count(*) FROM source WHERE url IS NOT NULL").fetchone()[0] == 0
    assert world.fetch_source("https://example.org/rate")["id"] == page["id"] and len(opener.seen) == 1
    world.store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct",
                               "current_value": {"value": 22, "source_id": page["id"]}})
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
    world.store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct",
                               "current_value": {"value": 22, "source_id": sid}})
    assert world.store.get(sid)["kept"] is True and world.store.get(sid)["dated"] == "2026-10-30"
    kept = world.store.edit(sid, "dated", "2026-10-31", sid)  # and a kept Source too
    assert kept["dated"] == "2026-10-31"
    assert world.store.db.execute("SELECT dated FROM source WHERE id = ?", (sid,)).fetchone()[0] == "2026-10-31"
    with pytest.raises(StoreError, match="cites the page itself"):
        world.store.edit(sid, "dated", "2026-11-01")
    assert world.store.get(sid)["dated"] == "2026-10-31"  # the refused edit wrote nothing


def test_a_past_store_reads_its_markets_histories_side_by_side(world):
    """A search from a store whose as-of is past quotes each market from its history at the as-of, the
    histories read several at a time, as the first read reads the bars."""
    import threading
    import time as clock
    world.store.add("Portfolio", {"asof": "2026-10-01"})
    many = [{**PM_MARKET, "id": str(900 + i), "question": f"Question {i}?",
             "clobTokenIds": json.dumps([f"Y{i}", f"N{i}"])} for i in range(6)]
    state = {"now": 0, "most": 0}
    lock = threading.Lock()

    def route(method, path, query, body):
        if path == "gamma-api.polymarket.com/public-search":
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

    pm, opener = http(route)
    world.venues.clients["polymarket"] = polymarket.Polymarket(pm)
    found = world.search_markets("polymarket", "/public-search", {"q": "tariffs"})
    prices = flat(found["events"])
    assert len(prices) == 6 and all(m["price_date"] == "2026-10-01" and m["bid"] == pytest.approx(0.41) for m in prices)
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


def test_search_markets_groups_the_markets_under_their_events_in_the_venues_order(world):
    closed = {**PM_MARKET, "id": "800", "question": "Closed?", "closed": True, "clobTokenIds": json.dumps(["Y8", "N8"])}
    other = {**PM_MARKET, "id": "801", "question": "Other?", "closed": False, "clobTokenIds": json.dumps(["Y9", "N9"])}

    def route(method, path, query, body):
        if path == "gamma-api.polymarket.com/public-search":
            return 200, {"events": [{"id": "9", "title": "Tariffs", "tags": [{"label": "Tariffs"}],
                                     "markets": [dict(PM_MARKET, closed=False), closed]},
                                    {"id": "10", "title": "Other", "tags": [{"label": "Trade"}], "markets": [other]}],
                         "pagination": {"hasMore": False, "totalResults": 2}}
        if path == "clob.polymarket.com/books":
            return 200, []
        return polymarket_route(method, path, query, body)

    pm, _ = http(route)
    world.venues.clients["polymarket"] = polymarket.Polymarket(pm)
    found = world.search_markets("polymarket", "/public-search", {"q": "tariff"})
    assert "markets" not in found and found["returned"] == 3
    assert [(g["event"], g["title"], g["tags"], [m["id"].split(":")[-1] for m in g["markets"]])
            for g in found["events"]] == [("9", "Tariffs", ["Tariffs"], ["516710", "800"]), ("10", "Other", ["Trade"], ["801"])]
    first = found["events"][0]["markets"]
    assert [m["open"] for m in first] == [True, False]  # the venue's own word, and nothing left out
    assert "tags" not in first[0] and "series" not in first[0]  # the event's tags once, no empty lists


def test_a_series_listing_returns_each_series_own_fields_and_paging_holds_no_list(world):
    def route(method, path, query, body):
        if path == "api.elections.kalshi.com/trade-api/v2/series":
            return 200, {"series": [{"ticker": "KXEFFTARIFF", "title": "US effective tariff rate", "category": "Economics",
                                     "tags": ["Tariffs"], "frequency": "quarterly", "contract_url": "https://x/y.pdf",
                                     "additional_prohibitions": ["a long paragraph"] * 5,
                                     "settlement_sources": [{"name": "Yale"}]}] * 3, "cursor": ""}
        return kalshi_route(method, path, query, body)

    ks, _ = http(route)
    world.venues.clients["kalshi"] = kalshi.Kalshi(ks)
    found = world.search_markets("kalshi", "/series", {"category": "Economics"})
    assert found["series"] == [{"ticker": "KXEFFTARIFF", "title": "US effective tariff rate", "category": "Economics",
                                "tags": ["Tariffs"], "frequency": "quarterly"}] * 3
    assert found["paging"] == {"cursor": ""} and found["returned"] == 0 and found["events"] == []
