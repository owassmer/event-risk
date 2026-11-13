"""Where the portfolio's weight is (MATH.md 3.1; harness.derive.owned): by company, by industry and by country, each
summing to the portfolio's value, every row of every fund counted; a company's industry from its listing in its region's
listings, else its profile; its home its ISIN's country where it trades there, else its profile's; what is not a company
under its kind. The sources' answers are put in the store in each one's kept shape; nothing reaches the network."""

from __future__ import annotations

import pytest

from harness.derive import default_registry
from harness.derive.owned import country_code, country_name
from harness.store import Store
from harness.venues.yahoo import region_of, symbols_for
from massive_data import ASOF, FETCHED, flat_prices
from test_one_name import fund, looked, ref

MONTH = ASOF[:7]


def test_a_coin_is_one_kind_of_asset_however_its_source_writes_it():
    """MATH.md 3.1: a coin reads as a commodity, the kind every trust that holds one is classed under, whether its source
    names it by a code (a currency code, or Bloomberg's ticker for its dollar price) under a label of its own, or by a
    word alone in a commodity pool. Its pair stays what its value moves with."""
    from harness.derive.assets import Kinds
    from harness.derive.rows import etf_global_rows
    payload = {"effective_date": ASOF, "results": [
        {"constituent_name": "Bitcoin", "asset_class": "Alternative", "currency_traded": "BTC", "weight": 1},
        {"constituent_ticker": "XBTUSD", "asset_class": "Alternative", "weight": 1},
        {"constituent_name": "BITCOIN", "currency_traded": "USD", "weight": 1}]}
    pool = Kinds(unlabelled="commodity")  # a fund the SEC classes as a commodity trust or pool
    rows = etf_global_rows(payload, pool, pairs=frozenset({"X:BTCUSD"}))
    assert [pool.of(r) for r in rows] == ["commodity"] * 3
    assert [r.get("to") for r in rows] == [{"pair": "X:BTCUSD"}, {"pair": "X:BTCUSD"}, None]
    # in a fund of no such class, a coin its code confirms is a commodity all the same, and a word alone is not read
    other = etf_global_rows(payload, Kinds(), pairs=frozenset({"X:BTCUSD"}))
    assert [Kinds().of(r) for r in other] == ["commodity", "commodity", "other"]


def listings(store, region, rows):
    """The listings the screener holds in a region, each with its sector and industry: what it answers a symbol by
    (harness.venues.yahoo.sectors), absent for any symbol no test lists."""
    held = store.__dict__.setdefault("screened", {})
    held.update({s: {"name": n, "sector": sec, "industry": ind} for s, n, sec, ind in rows})

    def answer(subject, covering):
        symbol = subject.rsplit("@", 1)[0]
        r = held.get(symbol)
        payload = {"status": "OK", "symbol": symbol, "exchange": None, **r} if r else \
            {"status": "NOT_FOUND", "symbol": symbol}
        return {"payload": payload, "request": {"method": "POST", "url": "screener", "ticker": symbol},
                "retrieved_at": FETCHED, "range": (ASOF, ASOF)}

    answer.many = lambda subjects, covering: {s: answer(s, covering) for s in subjects}
    store.fetchers["listing_sector"] = answer


def profile(store, symbol, country, sector=None, industry=None, name=None):
    store.put_data("profile", f"{symbol}@{MONTH}", {"status": "OK", "symbol": symbol, "country": country,
                                                    "sector": sector, "industry": industry, "name": name},
                   {"method": "GET", "url": "profile"}, retrieved_at=FETCHED, range=(ASOF, ASOF))


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-owned", registry=default_registry())
    flat_prices(s)  # each holding priced, where the test's subject is what it holds
    yield s
    s.close()


def test_the_three_lists_sum_to_the_portfolio_every_row_counted_each_company_classified_by_its_sources(store):
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    fund(store, "F", [
        {"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
        {"constituent_ticker": "2330", "isin": "TW0002330008", "country_of_exchange": "TW", "weight": 0.3},
        {"constituent_ticker": "700", "isin": "KYG875721634", "country_of_exchange": "HK", "weight": 0.15},
        {"constituent_ticker": "XYZ", "isin": "US9999999999", "weight": 0.001},  # below the floor, no name
        {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.049}])
    listings(store, "us", [("AAPL", "Apple Inc.", "Technology", "Consumer Electronics"),
                           ("XYZ", "Xyz Corp", "Industrials", "Railroads")])
    listings(store, "tw", [("2330.TW", "Taiwan Semiconductor", "Technology", "Semiconductors")])
    listings(store, "hk", [("0700.HK", "Tencent Holdings", "Communication Services", "Internet Content & Information")])
    profile(store, "0700.HK", "China")  # registered in the Cayman Islands, it trades in Hong Kong: its own record's
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    w = store.engine.value("weights", "portfolio").output
    for listed in ("by_company", "by_industry", "by_country"):
        assert sum(c["dollars"] for c in w[listed]) == pytest.approx(10000.0)
    countries = {c.get("country") or c.get("kind"): c["dollars"] for c in w["by_country"]}
    assert countries == pytest.approx({"US": 5010.0, "TW": 3000.0, "CN": 1500.0, "cash": 490.0})
    industries = {c.get("industry") or c.get("kind"): c["dollars"] for c in w["by_industry"]}
    assert industries["Semiconductors"] == pytest.approx(3000.0) and industries["Railroads"] == pytest.approx(10.0)
    xyz = next(c for c in w["by_company"] if c.get("company") == "Xyz Corp")
    assert xyz["name"] is None and xyz["industry"] == "Railroads"  # counted, though no name stands for it
    assert w["unclassified"] == {"industry": 0, "country": 0}


def test_a_listings_symbol_is_built_by_its_exchanges_rule_and_a_country_named_as_iso_names_it():
    assert symbols_for("AAL LN", "CH", "GB00BTK05J60") == ["AAL.SW", "AAL.L"]  # a fund naming the wrong country
    assert symbols_for("ADDT B", "SE") == ["ADDT-B.ST"] and symbols_for("ADVANC-F", "TH") == ["ADVANC.BK"]
    assert symbols_for("700", "HK") == ["0700.HK"] and symbols_for("000001", None, "CNE000000040") == ["000001.SZ"]
    assert symbols_for("BRK.B", "US") == ["BRK-B"] and region_of("BRK-B") == "us" and region_of("600519.SS") == "cn"
    assert [country_code(n) for n in ("South Korea", "Turkey", "Macau", "Curacao", "Cayman Islands")] == \
        ["KR", "TR", "MO", "CW", "KY"]
    assert country_code("Atlantis") is None and country_name("TW") == "Taiwan"


def test_a_fund_booked_whole_stands_once_under_the_kinds_of_what_carries_its_value(store):
    ref(store, "B", "ETF")
    fund(store, "B", [{"constituent_ticker": "T 4 30", "asset_class": "Fixed Income", "weight": 0.97},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.03}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "B", "dollars": 2000.0},
                                                       {"cash": 500.0}]})
    w = store.engine.value("weights", "portfolio").output
    kinds = {c["kind"]: c["dollars"] for c in w["by_industry"]}
    assert kinds == pytest.approx({"debt": 1940.0, "cash": 560.0})  # its rows counted once, as the fund's
    assert sum(c["dollars"] for c in w["by_company"]) == pytest.approx(2500.0)


def symbol(store, listing, found):
    store.put_data("listing_symbol", f"{listing}@{MONTH}", {"status": "OK" if found else "NOT_FOUND", "listing": listing,
                                                             "symbol": found},
                   {"method": "GET", "url": "search"}, retrieved_at=FETCHED, range=(ASOF, ASOF))


def test_a_row_whose_own_ticker_matches_nothing_where_it_trades_is_found_by_its_identifiers(store):
    """A US exchange's code after a ticker is read as that code; a ticker since changed, or a when-issued line, is found
    by searching its ISIN, or the one its US CUSIP makes; a company its fund names as trading in the US is found where
    its ISIN says it is registered."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    fund(store, "F", [
        {"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
        {"constituent_ticker": "OKE.UN", "isin": "US6826801036", "weight": 0.001},  # each below the floor: no name
        {"constituent_ticker": "GPS", "isin": "US3647601083", "weight": 0.001},  # the ticker since changed
        {"constituent_ticker": "HONIV", "us_code": "438516205", "weight": 0.001},  # when-issued, its CUSIP alone
        {"constituent_ticker": "8210", "isin": "TW0008210006", "weight": 0.001},  # named as trading in the US
        {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.496}])
    listings(store, "us", [("AAPL", "Apple Inc.", "Technology", "Consumer Electronics"),
                           ("OKE", "ONEOK", "Energy", "Oil & Gas Midstream"),
                           ("GAP", "Gap", "Consumer Cyclical", "Apparel Retail"),
                           ("HON", "Honeywell", "Industrials", "Conglomerates")])
    listings(store, "tw", [("8210.TW", "Chenbro Micom", "Technology", "Computer Hardware")])
    symbol(store, "US3647601083:US", "GAP")
    symbol(store, "US4385162056:US", "HON")  # the ISIN its CUSIP makes, searched
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 100000.0}]})
    w = store.engine.value("weights", "portfolio").output
    industries = {c["company"]: (c["industry"], c["country"]) for c in w["by_company"] if "company" in c}
    assert industries == {"Apple Inc.": ("Consumer Electronics", "US"),
                          "ONEOK": ("Oil & Gas Midstream", "US"), "Gap": ("Apparel Retail", "US"),
                          "Honeywell": ("Conglomerates", "US"), "Chenbro Micom": ("Computer Hardware", "TW")}
    assert w["unclassified"] == {"industry": 0, "country": 0}


def test_securities_no_identifier_names_stand_apart_and_a_cusip_reaches_the_company_the_portfolio_names(store):
    """Two rows below the floor that name no ISIN, CUSIP, FIGI or ticker stand as two, each under its name, never as one
    company; and a row below the floor naming a company the portfolio names only by its CUSIP reaches that name."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    fund(store, "F", [
        {"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
        {"constituent_name": "ACME WARRANT", "weight": 0.001},
        {"constituent_name": "WIDGET CO CVR", "weight": 0.0005},
        {"constituent_name": "APPLE INC", "us_code": "037833100", "weight": 0.001},  # Apple, by its CUSIP alone
        {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.4975}])
    listings(store, "us", [("AAPL", "Apple Inc.", "Technology", "Consumer Electronics")])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    owned = looked(store)["owned"]
    assert owned["row::ACME WARRANT"]["dollars"] == pytest.approx(10.0)
    assert owned["row::WIDGET CO CVR"]["dollars"] == pytest.approx(5.0)
    assert looked(store)["e"]["name:AAPL"] == pytest.approx(5010.0)  # the row its CUSIP names reaches Apple


def test_a_row_below_the_floor_named_by_its_cusip_alone_reaches_the_company_the_portfolio_holds(store):
    """MATH.md 1.6, 3.1: a company held directly and reached in a fund only below the floor, by a row whose sole identifier
    is its CUSIP (a report's swap reference, a row with no FIGI), has those dollars booked to it: the CUSIP's listing
    gives the FIGI that names the company the portfolio holds."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    ref(store, "MSFT", "CS")
    fund(store, "F", [{"constituent_ticker": "MSFT", "figi": "FIGIMSFT", "isin": "US5949181045", "weight": 0.6},
                      {"constituent_name": "APPLE INC", "us_code": "037833100", "weight": 0.002},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.398}])
    store.put_data("cusip_listing", f"037833100@{ASOF}", {"status": "OK", "results": [
        {"ticker": "AAPL", "composite_figi": "FIGIAAPL", "share_class_figi": "BBGAAPLCLASS"}]},
        {"method": "GET", "url": "cusip"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "AAPL", "dollars": 1000.0},
                                                       {"ticker": "F", "dollars": 10000.0}]})
    out = looked(store)
    assert out["e"]["name:AAPL"] == pytest.approx(1020.0)  # its own 1,000 and the fund's 20
    assert "looks_through_to:name:F>name:AAPL" in out["links"]


def test_a_company_no_regions_listings_hold_is_classified_by_the_first_of_its_listings_whose_profile_answers(store):
    """Where a region's listings leave a company out (a region they do not reach, a listing they omit) and its ISIN's
    search finds nothing, its profile is read under the listings it has: its own ticker where it trades, then each US
    line OpenFIGI maps its ISIN to."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    fund(store, "F", [{"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
                      {"constituent_ticker": "QRVO", "isin": "US74736K1016", "weight": 0.001},
                      {"constituent_ticker": "ICT", "isin": "PHY411571011", "country_of_exchange": "PH",
                       "weight": 0.002},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.497}])
    listings(store, "us", [("AAPL", "Apple Inc.", "Technology", "Consumer Electronics")])  # Qorvo left out
    listings(store, "ph", [])  # a region the listings do not reach
    symbol(store, "US74736K1016:US", None)
    symbol(store, "PHY411571011:PH", None)
    profile(store, "QRVO", "United States", "Technology", "Semiconductors", "Qorvo, Inc.")
    store.put_data("profile", f"ICT.PS@{MONTH}", {"status": "NOT_FOUND", "symbol": "ICT.PS"},
                   {"method": "GET", "url": "profile"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    store.put_data("figi", "PHY411571011", {"status": "OK", "isin": "PHY411571011", "composites": [],
                                            "listings": [{"ticker": "ICTEF", "exchCode": "US"}]},
                   {"method": "POST", "url": "openfigi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    profile(store, "ICTEF", "Philippines", "Industrials", "Marine Shipping",
            "International Container Terminal Services, Inc.")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    w = store.engine.value("weights", "portfolio").output
    got = {c["company"]: (c["industry"], c["country"]) for c in w["by_company"] if "company" in c}
    assert got["Qorvo, Inc."] == ("Semiconductors", "US")
    assert got["International Container Terminal Services, Inc."] == ("Marine Shipping", "PH")  # registered, trades there
    assert w["unclassified"] == {"industry": 0, "country": 0}


def test_a_portfolio_naming_no_company_beyond_the_floors_reads_no_cusips_listing(store):
    """The listings of rows below the floor named by a CUSIP alone are read only where the portfolio names a company,
    held directly or by a consequence, that no row at the floor resolved to: a fund held alone needs none."""
    ref(store, "F", "ETF")
    ref(store, "MSFT", "CS")
    fund(store, "F", [{"constituent_ticker": "MSFT", "figi": "FIGIMSFT", "isin": "US5949181045", "weight": 0.6},
                      {"constituent_name": "APPLE INC", "us_code": "037833100", "weight": 0.002},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.398}])
    asked = []
    store.fetchers["cusip_listing"] = lambda subject, covering: asked.append(subject) or {
        "payload": {"status": "OK", "results": []}, "request": {"method": "GET", "url": "cusip"},
        "retrieved_at": FETCHED, "range": (ASOF, ASOF)}
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    looked(store)
    assert asked == []
    store.add("Proposition", {"title": "T", "text": "T: the proposition", "comparison": "occurs",
                              "window_end": "2027-03-31", "kind": "hitting"})
    with store.transaction():  # a company a consequence names that no row at the floor resolved to: now they are read
        store._insert_link("consequence", "prop:1", "name:AAPL", {"shock": {"value": -0.2}}, "analyst", None)
    ref(store, "AAPL", "CS")
    looked(store)
    assert asked == [f"037833100@{ASOF}"]


def test_a_company_met_by_its_receipt_has_the_home_its_own_record_gives_and_a_holding_reads_as_the_lists_do(store):
    """A depositary receipt's ISIN is the receipt's, issued in the US where it trades, and says nothing of where its
    company is registered: the company's home is its own record's. A company held directly is described in the
    holdings as the lists classify it: its name, its industry and its home from the one classification."""
    from harness.ledger import holdings
    ref(store, "F", "ETF")
    ref(store, "TSM", "ADRC", name="Taiwan Semiconductor Manufacturing Company Limited American Depositary Shares")
    ref(store, "AAPL", "CS", name="Apple Inc. Common Stock")
    fund(store, "F", [{"constituent_ticker": "TSM", "figi": "FIGITSM", "isin": "US8740391003", "weight": 0.6},
                      {"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.4}])
    listings(store, "us", [("TSM", "Taiwan Semiconductor Manufacturing Company Limited", "Technology",
                            "Semiconductors"), ("AAPL", "Apple Inc.", "Technology", "Consumer Electronics")])
    profile(store, "TSM", "Taiwan")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 5000.0},
                                                       {"ticker": "TSM", "dollars": 5000.0}]})
    w = store.engine.value("weights", "portfolio").output
    homes = {c["company"]: c["country"] for c in w["by_company"]}
    assert homes == {"Taiwan Semiconductor Manufacturing Company Limited": "TW", "Apple Inc.": "US"}
    held = next(h for h in holdings(store)["holdings"] if h["ticker"] == "TSM")
    assert held["company"] == {"name": "Taiwan Semiconductor Manufacturing Company Limited",
                               "industry": "Semiconductors", "country": "TW"}  # not the receipt's name, nor its SIC
    fund_row = next(h for h in holdings(store)["holdings"] if h["ticker"] == "F")  # its holdings in the same words
    assert {r["priced_as"]: r["industry"] for r in fund_row["fund"]["largest"]} == {
        "name:TSM": "Semiconductors", "name:AAPL": "Consumer Electronics"}


def test_a_row_no_label_names_in_a_fund_the_sec_classes_as_a_commodity_trust_is_a_commodity(store):
    """A commodity trust's holdings often state no kind, its bullion under a name alone; the fund's own record (its
    SIC, 6221) says what it holds. The same unlabelled row in any other fund stays other: nothing says what it is."""
    from test_one_name import BASE
    for ticker, sic in (("GLDX", "6221"), ("ODD", None)):
        results = {"ticker": ticker, "type": "ETV", "market": "stocks", "locale": "us",
                   "composite_figi": f"FIGI{ticker}", **({"sic_code": sic} if sic else {})}
        store.put_data("ticker_ref", ticker, {"status": "OK", "results": results},
                       {"method": "GET", "url": f"{BASE}/v3/reference/tickers/{ticker}"},
                       retrieved_at=FETCHED, range=(ASOF, ASOF))
        fund(store, ticker, [{"constituent_name": "GOLD", "asset_class": None, "country_of_exchange": None,
                              "weight": 1.0}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "GLDX", "dollars": 3000.0},
                                                       {"ticker": "ODD", "dollars": 1000.0}]})
    w = store.engine.value("weights", "portfolio").output
    kinds = {c["kind"]: c["dollars"] for c in w["by_country"] if "kind" in c}
    assert kinds == pytest.approx({"commodity": 3000.0, "other": 1000.0})


def test_a_commodity_trust_held_whole_with_no_holdings_published_stands_under_commodity(store):
    """MATH.md 3.1: what is not a company stands under its kind of asset. A trust whose holdings no source publishes, as
    a coin's trust's often are not, is held whole; its own record (SIC 6221) says its value is held in a commodity.
    Another fund with none published says nothing of it, and stands under other."""
    from test_one_name import BASE
    for ticker, sic in (("COINT", "6221"), ("ODD", None)):
        results = {"ticker": ticker, "type": "ETV", "market": "stocks", "locale": "us",
                   "composite_figi": f"FIGI{ticker}", **({"sic_code": sic} if sic else {})}
        store.put_data("ticker_ref", ticker, {"status": "OK", "results": results},
                       {"method": "GET", "url": f"{BASE}/v3/reference/tickers/{ticker}"},
                       retrieved_at=FETCHED, range=(ASOF, ASOF))
        fund(store, ticker, [])
        store.put_data("nport", ticker, {"status": "NOT_FOUND", "fund": ticker, "error": "files none"},
                       {"method": "GET", "url": "nport"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "COINT", "dollars": 4000.0},
                                                       {"ticker": "ODD", "dollars": 1000.0}]})
    w = store.engine.value("weights", "portfolio").output
    kinds = {c["kind"]: c["dollars"] for c in w["by_country"] if "kind" in c}
    assert kinds == pytest.approx({"commodity": 4000.0, "other": 1000.0})


def test_what_a_commodity_pools_rows_leave_unstated_is_its_commodity_exposure(store):
    """A futures pool's rows state its futures at their marked value and its collateral beside them, a tenth of its
    value; what they leave unstated backs its commodity exposure, and stands under commodity."""
    from test_one_name import BASE
    store.put_data("ticker_ref", "POOL", {"status": "OK", "results": {
        "ticker": "POOL", "type": "ETV", "market": "stocks", "locale": "us", "composite_figi": "FIGIPOOL",
        "sic_code": "6221"}}, {"method": "GET", "url": f"{BASE}/v3/reference/tickers/POOL"},
        retrieved_at=FETCHED, range=(ASOF, ASOF))
    ref(store, "TB", "ETF")
    fund(store, "TB", [{"constituent_name": "TREASURY BILL", "asset_class": "Fixed Income", "weight": 1.0}])
    fund(store, "POOL", [{"constituent_ticker": "TB", "figi": "FIGITB", "asset_class": "Equity", "security_type": "ETP",
                          "weight": 0.1},
                         {"constituent_name": "CORN FUTURE", "asset_class": None, "country_of_exchange": None,
                          "weight": 0.01}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "POOL", "dollars": 10000.0}]})
    w = store.engine.value("weights", "portfolio").output
    kinds = {c["kind"]: c["dollars"] for c in w["by_country"] if "kind" in c}
    assert kinds == pytest.approx({"commodity": 9000.0, "debt": 1000.0})


def test_a_report_row_named_by_its_isin_alone_is_matched_by_where_the_funds_holdings_say_it_trades():
    """MATH.md 3.1: every row of every fund is counted by its company's industry and home. A fund's report names a share
    by its ISIN alone; where the funds' holdings say it trades (its `listing`) gives its ticker and exchange's country,
    by which its region's listings classify it, where its own fields give neither."""
    from harness.derive.owned import candidates, traded
    row = {"isin": "KYG875721634", "constituent_name": "TENCENT", "listing": {"ticker": "700", "country": "HK"}}
    assert traded(row) == ("700", "HK")
    assert candidates("row:x", {"row": row}, {}) == ["0700.HK"]
    stated = {**row, "constituent_ticker": "700", "country_of_exchange": "HK"}
    assert traded({**stated, "listing": {"ticker": "X", "country": "US"}}) == ("700", "HK")  # the fund's own first
