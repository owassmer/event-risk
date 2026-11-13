"""Yahoo Finance's reads, stubbed: a listing abroad's closes in its own currency, each with the moment its exchange
struck it, its dividends and splits; a currency pair's hourly rates, read at the session's close in New York; and each
kept in the store in the shape the derivations read (harness.venues.yahoo)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from harness.analyst import by_pair
from harness.derive import series
from harness.store import Store
from harness.venues.yahoo import Yahoo

LONDON = ZoneInfo("Europe/London")


def at(day: str, hour: int, minute: int = 0, zone=LONDON) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=zone).timestamp())


def stub(routes):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fc.yahoo.com":  # the site's cookie
            return httpx.Response(404, text="")
        if request.url.path == "/v1/test/getcrumb":  # and its crumb, which the site's own surfaces carry
            return httpx.Response(200, text="crumb1")
        params = {k: v for k, v in request.url.params.items() if k != "crumb"}
        seen.append((request.url.path, params))
        status, body = routes(request.url.path, params)
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return Yahoo(http=client, site=client, interval=0, pause=0), seen


SESSIONS = [[{"start": at(d, 8), "end": at(d, 16, 30)}] for d in ("2026-11-04", "2026-11-05")] + \
    [[{"start": at("2026-11-06", 8), "end": at("2026-11-06", 12, 30)}]]  # a shortened session


def london(path, params):
    if path == "/v1/finance/search":
        assert params["q"] == "GB0031348658"
        return 200, {"quotes": [{"symbol": "BCS", "exchange": "NYQ", "quoteType": "EQUITY"},
                                {"symbol": "BARC.IL", "exchange": "IOB", "quoteType": "EQUITY"},
                                {"symbol": "BARC.L", "exchange": "LSE", "quoteType": "EQUITY"}]}
    if path == "/v8/finance/chart/BARC.L" and params["interval"] == "60m":
        return 200, {"chart": {"result": [{"meta": {"exchangeTimezoneName": "Europe/London",
                                                    "tradingPeriods": SESSIONS}}]}}
    if path == "/v8/finance/chart/BARC.L":
        days = ("2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09")
        return 200, {"chart": {"result": [{
            "meta": {"symbol": "BARC.L", "currency": "GBp", "exchangeName": "LSE", "exchangeTimezoneName": "Europe/London",
                     "currentTradingPeriod": {"regular": {"start": at("2026-11-09", 8), "end": at("2026-11-09", 16, 30)}},
                     "regularMarketPrice": 431.5, "regularMarketTime": at("2026-11-09", 11)},
            "timestamp": [at(d, 8) for d in days],
            "indicators": {"quote": [{"close": [410.0, 420.25, None, 431.5]}]},
            "events": {"dividends": {"1": {"date": at("2026-11-05", 8), "amount": 5.5}},
                       "splits": {"2": {"date": at("2026-11-04", 8), "numerator": 2.0, "denominator": 1.0}}}}]}}
    raise AssertionError(path)


def test_a_listing_abroad_reads_its_closes_in_its_own_currency_each_with_the_moment_its_exchange_struck_it(tmp_path,
                                                                                                         monkeypatch):
    yahoo, seen = stub(london)
    monkeypatch.setattr("harness.venues.yahoo.now", lambda: "2026-11-09T12:00:00+00:00")  # the 9th's in progress
    got = yahoo.home("GB0031348658:GB", "2026-11-01", "2026-11-09")
    p = got["payload"]
    assert (p["status"], p["symbol"], p["mic"], p["currency"]) == ("OK", "BARC.L", "XLON", "GBP")  # London, in pounds
    assert p["results"] == [{"session": "2026-11-04", "moment": "2026-11-04T16:30:00Z", "c": 4.1},
                            {"session": "2026-11-05", "moment": "2026-11-05T16:30:00Z", "c": 4.2025}]
    # the 6th has no close; the 9th's session has not closed when read
    assert p["dividends"] == [{"ex_date": "2026-11-05", "amount": 0.055}]
    assert p["splits"] == [{"date": "2026-11-04", "from": 1.0, "to": 2.0}]
    mark = yahoo.home_mark("GB0031348658:GB", "2026-11-09")["payload"]
    assert (mark["price"], mark["time"], mark["currency"]) == (4.315, "2026-11-09T11:00:00Z", "GBP")
    assert sum(1 for path, _ in seen if path == "/v1/finance/search") == 1  # the listing is found once
    store = Store.create(tmp_path / "scratch-yahoo")
    store.put_data("home", "GB0031348658:GB", p, got["request"], retrieved_at=got["retrieved_at"], range=got["range"])
    kept = store.data("home", "GB0031348658:GB", covering=("2026-11-01", "2026-11-09"))
    assert store.data_payload(kept["id"]) == p
    store.close()


def listing(search, symbol, exchange, zone, currency, periods, closes):
    """A listing's routes: Yahoo's search for its ISIN, its sessions as Yahoo's hourly chart gives them, and its
    daily closes."""
    def routes(path, params):
        if path == "/v1/finance/search":
            assert params["q"] == search
            return 200, {"quotes": [{"symbol": symbol, "exchange": exchange, "quoteType": "EQUITY"}]}
        assert path == f"/v8/finance/chart/{symbol}"
        if params["interval"] == "60m":
            return 200, {"chart": {"result": [{"meta": {"exchangeTimezoneName": zone.key,
                                                        "tradingPeriods": [[p] for p in periods]}}]}}
        return 200, {"chart": {"result": [{"meta": {"symbol": symbol, "currency": currency, "exchangeName": exchange,
                                                    "exchangeTimezoneName": zone.key},
                                           "timestamp": [p["start"] for p in periods],
                                           "indicators": {"quote": [{"close": closes}]}}]}}
    return routes


def test_a_shortened_session_closes_when_its_exchange_closed_it():
    # London closed at 12:30 on Christmas Eve 2025, by its calendar and by Yahoo's session alike
    periods = [{"start": at("2025-12-23", 8), "end": at("2025-12-23", 16, 30)},
               {"start": at("2025-12-24", 8), "end": at("2025-12-24", 12, 30)}]
    yahoo, _ = stub(listing("GB0031348658", "BARC.L", "LSE", LONDON, "GBp", periods, [410.0, 425.0]))
    p = yahoo.home("GB0031348658:GB", "2025-12-22", "2025-12-24")["payload"]
    assert p["results"] == [{"session": "2025-12-23", "moment": "2025-12-23T16:30:00Z", "c": 4.1},
                            {"session": "2025-12-24", "moment": "2025-12-24T12:30:00Z", "c": 4.25}]


def test_a_close_is_struck_at_the_later_of_its_exchanges_official_close_and_the_end_of_its_trading():
    # Seoul's continuous trading ends at 15:20 and Yahoo's session at 15:00; the close is the 15:30 auction's
    seoul = ZoneInfo("Asia/Seoul")
    periods = [{"start": at("2025-12-29", 9, zone=seoul), "end": at("2025-12-29", 15, zone=seoul)}]
    yahoo, _ = stub(listing("KR7005930003", "005930.KS", "KSC", seoul, "KRW", periods, [119300.0]))
    p = yahoo.home("KR7005930003:KR", "2025-12-28", "2025-12-29")["payload"]
    assert p["results"] == [{"session": "2025-12-29", "moment": "2025-12-29T06:30:00Z", "c": 119300.0}]
    # Hong Kong's continuous trading ends at 16:00, its official close; its closing auction strikes the price by 16:10
    hk = ZoneInfo("Asia/Hong_Kong")
    periods = [{"start": at("2025-12-23", 9, 30, zone=hk), "end": at("2025-12-23", 16, 10, zone=hk)},
               {"start": at("2025-12-24", 9, 30, zone=hk), "end": at("2025-12-24", 12, 6, zone=hk)}]
    yahoo, _ = stub(listing("GB0005405286", "0005.HK", "HKG", hk, "HKD", periods, [97.5, 98.0]))
    p = yahoo.home("GB0005405286:HK", "2025-12-22", "2025-12-24")["payload"]
    assert p["results"] == [{"session": "2025-12-23", "moment": "2025-12-23T08:10:00Z", "c": 97.5},
                            {"session": "2025-12-24", "moment": "2025-12-24T04:06:00Z", "c": 98.0}]


def test_a_listing_yahoo_does_not_list_in_its_country_is_an_answer():
    yahoo, _ = stub(lambda path, params: (200, {"quotes": [{"symbol": "SAMSUNG.F", "exchange": "FRA",
                                                                    "quoteType": "EQUITY"}]}))
    got = yahoo.home("KR7005930003:KR", "2026-11-01", "2026-11-09")["payload"]
    assert got["status"] == "NOT_FOUND" and "KR7005930003" in got["error"]


NY = ZoneInfo("America/New_York")


def test_a_currency_pair_is_read_at_the_sessions_close_in_new_york_and_its_latest_rate_as_a_mark():
    hours = [at("2026-11-05", h, zone=NY) for h in (15, 16, 17)] + [at("2026-11-06", h, zone=NY) for h in (15, 17)]

    def routes(path, params):
        assert path == "/v8/finance/chart/EURUSD=X"
        if params["interval"] == "60m":
            return 200, {"chart": {"result": [{"meta": {"symbol": "EURUSD=X"}, "timestamp": hours,
                                               "indicators": {"quote": [{"open": [1.10, 1.11, 1.12, 1.20, 1.22],
                                                                         "high": [None] * 5, "low": [None] * 5,
                                                                         "close": [1.105, 1.115, 1.125, 1.21, 1.23]}]}}]}}
        return 200, {"chart": {"result": [{"meta": {"regularMarketPrice": 1.1234,
                                                    "regularMarketTime": at("2026-11-09", 10, zone=NY)}}]}}

    yahoo, _ = stub(routes)
    got = yahoo.hourly("C:EURUSD", "2026-11-05", "2026-11-06")
    closes = series.session_closes(got["payload"], "2026-11-07T00:00:00+00:00")
    # the 5th at its 16:00 bar's open; the 6th, with no 16:00 bar, at the close of the hour before
    assert closes == {"2026-11-05": 1.11, "2026-11-06": 1.21}
    mark = series.trade_mark(yahoo.mark("C:EURUSD", "2026-11-09")["payload"])
    assert mark == {"price": 1.1234, "time": "2026-11-09T10:00:00-05:00", "from": "latest rate"}


def test_a_pairs_latest_rate_is_its_minutes_bar_whole_not_the_figure_rounded_to_four_places():
    """Read live: JPYUSD=X's regularMarketPrice is 0.0063 (priceHint 4), where the bar of the minute it was struck in
    closed at 0.0062612, 0.6% apart; KRWUSD=X's 0.0007 against 0.00074975, 6.6%."""
    struck = at("2026-11-09", 10, zone=NY) + 25

    def routes(path, params):
        assert params["interval"] == "1m"
        return 200, {"chart": {"result": [{"meta": {"regularMarketPrice": 0.0063, "regularMarketTime": struck,
                                                    "priceHint": 4},
                                           "timestamp": [struck - 85, struck - 25],
                                           "indicators": {"quote": [{"close": [0.0062601, 0.0062612]}]}}]}}
    yahoo, _ = stub(routes)
    assert series.trade_mark(yahoo.mark("C:JPYUSD", "2026-11-09")["payload"])["price"] == 0.0062612


def test_a_pairs_reads_go_to_its_source_by_kind():
    crypto = lambda subject, covering: ("coinbase", subject)  # noqa: E731
    currency = lambda subject, covering: ("yahoo", subject)  # noqa: E731
    read = by_pair(crypto, currency)
    assert read("C:EURUSD", None) == ("yahoo", "C:EURUSD") and read("X:BTCUSD", None) == ("coinbase", "X:BTCUSD")


def test_the_share_class_is_taken_on_its_own_market_else_on_any_exchange_but_never_a_dealers_quote_of_it():
    from harness.venues.yahoo import pick
    quotes = [{"symbol": "STMPA.PA", "exchange": "PAR", "quoteType": "EQUITY"},
              {"symbol": "SGM.F", "exchange": "FRA", "quoteType": "EQUITY"}]
    assert pick(quotes, "IT") is None and pick(quotes, "NL") is None  # the fund's market, and the ISIN's
    assert pick(quotes, None) == "STMPA.PA"  # the same share class on another exchange
    assert pick(quotes[1:], None) is None and pick(quotes[1:], "DE") == "SGM.F"  # a dealer's only where its country's


def test_an_exchange_whose_calendar_cannot_be_built_has_none_and_reads_its_price_sources_sessions(monkeypatch):
    from harness import sessions

    def unbuilt(name, start=None, end=None):
        raise ValueError("Special dates are not sessions.")

    monkeypatch.setattr(sessions, "_built", {})
    monkeypatch.setattr(sessions.xc, "get_calendar", unbuilt)
    assert sessions.calendar("XTAE", date(2026, 11, 2)) is None
    assert sessions.closes("XTAE", "2026-11-01", "2026-11-06") == {}


def test_a_price_chart_refused_past_its_tries_holds_back_charts_and_not_the_sites_listings():
    """Each surface its own: the charts refused (429) leave the screener and a company's profile answering."""
    def routes(path, params):
        if path.startswith("/v8/finance/chart/"):
            return 429, {}
        if path.startswith("/v10/finance/quoteSummary/"):
            return 200, {"quoteSummary": {"result": [{"assetProfile": {"country": "Japan", "sector": "Industrials",
                                                                         "industry": "Conglomerates"},
                                                      "price": {"longName": "X Corp"}}]}}
        return 404, {}
    y, _ = stub(routes)
    import pytest
    from harness.venues.http import VenueError
    with pytest.raises(VenueError):
        y._get("/v8/finance/chart/6532.T", {})
    with pytest.raises(VenueError, match="did not answer a moment ago"):
        y._get("/v8/finance/chart/IMI.L", {})
    assert y.profile("6501.T", "2026-11-11")["payload"]["country"] == "Japan"


def test_a_listing_abroads_reference_is_its_own_markets_and_a_us_class_in_massives_form_charts_as_yahoos():
    """MATH.md 3.1: a holding's reference says what it is. A listing abroad named by its symbol is its own market's: its
    name, a share (EQUITY as Massive's CS), its exchange's MIC and country, its currency's own unit; a symbol Yahoo
    charts nothing under has none. McCormick's voting class, MKC.V in Massive's form, is charted under Yahoo's US form,
    MKC-V (read live)."""
    def routes(path, params):
        meta = {"/v8/finance/chart/0700.HK": {"exchangeName": "HKG", "instrumentType": "EQUITY", "currency": "HKD",
                                              "longName": "Tencent Holdings Limited"},
                "/v8/finance/chart/VOD.L": {"exchangeName": "LSE", "instrumentType": "EQUITY", "currency": "GBp",
                                            "longName": "Vodafone Group Public Limited Company"},
                "/v8/finance/chart/MKC-V": {"exchangeName": "NYQ", "instrumentType": "EQUITY", "currency": "USD"}}
        if path in meta:
            return 200, {"chart": {"result": [{"meta": meta[path], "timestamp": [], "indicators": {"quote": [{}]}}]}}
        return 404, {"chart": {"result": None, "error": {"code": "Not Found"}}}

    yahoo, _ = stub(routes)
    tencent = yahoo.reference("0700.HK", "2026-11-12")["payload"]
    assert tencent == {"status": "OK", "results": {"ticker": "0700.HK", "name": "Tencent Holdings Limited", "type": "CS",
                                                   "market": "home", "locale": "hk", "primary_exchange": "XHKG",
                                                   "currency_name": "hkd", "active": True}}
    assert yahoo.reference("VOD.L", "2026-11-12")["payload"]["results"]["currency_name"] == "gbp"  # pence as pounds
    assert yahoo.reference("ZZZZ.HK", "2026-11-12")["payload"]["status"] == "NOT_FOUND"
    assert yahoo.charted("MKC.V", {"interval": "1d"})[0] == "MKC-V"
    assert yahoo.charted("ZZZZ.L", {"interval": "1d"}) == ("ZZZZ.L", None)  # no US class under its US form either


def test_a_rows_ticker_as_etf_global_writes_it_is_yahoos_symbol_on_its_market():
    """Read on a total-international fund's rows placed by ETF Global's holdings: Korea's short code with its A
    before it, China's code with its leading zeros dropped, and Mexico's series mark, each Yahoo's symbol."""
    from harness.venues.yahoo import symbols_for
    assert symbols_for("A000100", "KR", "KR7000100008") == ["000100.KS", "000100.KQ"]
    assert symbols_for("688", "CN", "CNE0000003F5") == ["000688.SZ"]
    assert symbols_for("600519", "CN") == ["600519.SS"]
    assert symbols_for("AC *", "MX") == ["AC.MX"]


def test_a_listings_sector_is_read_by_its_symbol_as_many_at_once_as_the_screener_names_each_absent_one_answered():
    """Yahoo.sectors: the symbols asked, SCREEN_TICKERS to a query, each query one "or" over tickers; a listing the
    screener holds with a sector answered with it, one it holds with none, or does not hold, answered as not found."""
    from harness.venues import yahoo
    asked: list[list[str]] = []

    def screen(self, symbols):
        asked.append(list(symbols))
        return [{"ticker": s, "companyName": f"{s} Co", "sector": None if s.startswith("X") else "Technology",
                 "industry": "Semiconductors", "exchange": "NMS"} for s in symbols if not s.startswith("Z")]

    y = Yahoo(interval=0, pause=0)
    names = [f"A{k:03d}" for k in range(300)] + ["XNOSECTOR", "ZABSENT"]
    original = yahoo.Yahoo._screen
    yahoo.Yahoo._screen = screen
    try:
        got = y.sectors(names, "2026-11-12")
    finally:
        yahoo.Yahoo._screen = original
    assert sorted(len(q) for q in asked) == [52, yahoo.SCREEN_TICKERS] and sorted(sum(asked, [])) == sorted(names)
    assert got["A000"]["payload"] == {"status": "OK", "symbol": "A000", "name": "A000 Co", "sector": "Technology",
                                      "industry": "Semiconductors", "exchange": "NMS"}
    assert got["XNOSECTOR"]["payload"]["status"] == got["ZABSENT"]["payload"]["status"] == "NOT_FOUND"
    fetch = y.fetchers()["listing_sector"]  # by `{symbol}@{YYYY-MM}`, a month's reading, many at once
    yahoo.Yahoo._screen = screen
    try:
        many = fetch.many(["A001@2026-11", "ZABSENT@2026-11"], ("2026-11-12", "2026-11-12"))
    finally:
        yahoo.Yahoo._screen = original
    assert set(many) == {"A001@2026-11", "ZABSENT@2026-11"} and many["A001@2026-11"]["payload"]["status"] == "OK"


def test_a_span_before_a_symbol_first_traded_has_no_prices_and_any_other_refusal_stands():
    """Yahoo refuses a span that ends before a symbol first traded (400, "Data doesn't exist"); its chart's meta states
    when that was, so such a span is a chart with no prices. A refusal of a span that ends after it stands."""
    import pytest
    from harness.venues.http import VenueRefused
    first = int(datetime(2023, 8, 28, 13, 30, tzinfo=timezone.utc).timestamp())

    def routes(path, params):
        if path == "/v8/finance/chart/TSDD" and params.get("range") == "5d":
            return 200, {"chart": {"result": [{"meta": {"symbol": "TSDD", "instrumentType": "ETF",
                                                        "firstTradeDate": first}, "timestamp": [first + 86400 * 800]}]}}
        if path == "/v8/finance/chart/TSDD":
            return 400, {"chart": {"result": None, "error": {"code": "Bad Request", "description": "Data doesn't exist"}}}
        raise AssertionError(path)

    y, _ = stub(routes)
    day = lambda d: int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())  # noqa: E731
    before = y._chart("TSDD", {"period1": day("2022-01-03"), "period2": day("2022-10-13"), "interval": "1d"})
    assert before["timestamp"] == [] and before["meta"]["firstTradeDate"] == first
    with pytest.raises(VenueRefused):
        y._chart("TSDD", {"period1": day("2023-09-01"), "period2": day("2023-09-16"), "interval": "1d"})
