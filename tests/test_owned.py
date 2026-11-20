"""Where the portfolio's weight is (MATH.md 3.1; harness.derive.owned): by company, by industry and by country, each
summing to the portfolio's value, every row of every fund counted; a company's industry from its row in
FinanceDatabase's file; its home its ISIN's country where it trades there, else the file's, else its profile's; what is
not a company under its kind. The sources' answers are put in the store in each one's kept shape; nothing reaches the network."""

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


EXCHANGE = {"us": "NYQ", "tw": "TAI", "hk": "HKG", "gb": "LSE", "ca": "TOR"}  # Yahoo's code for an exchange there


def equities(store, rows):
    """The rows FinanceDatabase's file holds, each (symbol, name, sector, industry, isin, country): what it answers a
    listing's symbol and an ISIN by (harness.venues.equities), none for any no test lists."""
    held = store.__dict__.setdefault("equities_held", [])
    held += [{"symbol": sym, "name": n, "sector": sec, "industry": ind, "isin": isin, "country": country,
              "exchange": EXCHANGE.get(region_of(sym)), "delisted": False} for sym, n, sec, ind, isin, country in rows]

    def answer(subject, covering):
        how, _, value = subject.partition(":")
        found = [r for r in held if r[how] == value]
        return {"payload": {"status": "OK" if found else "NOT_FOUND", "subject": subject, "rows": found},
                "request": {"method": "GET", "url": "equities"}, "retrieved_at": FETCHED, "range": (ASOF, ASOF)}

    answer.many = lambda subjects, covering: {x: answer(x, covering) for x in subjects}
    store.fetchers["equity"] = answer


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
    equities(store, [("AAPL", "Apple Inc.", "Information Technology", "Computers & Hardware", "US0378331005",
                      "United States"),
                     ("XYZ", "Xyz Corp", "Industrials", "Rail & Road Transport", "US9999999999", "United States"),
                     ("2330.TW", "Taiwan Semiconductor", "Information Technology", "Semiconductors", "TW0002330008",
                      "Taiwan"),
                     # registered in the Cayman Islands, it trades in Hong Kong: its own record's
                     ("0700.HK", "Tencent Holdings", "Communication Services", "Internet Media & Services",
                      "KYG875721634", "China")])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    w = store.engine.value("weights", "portfolio").output
    for listed in ("by_company", "by_industry", "by_country"):
        assert sum(c["dollars"] for c in w[listed]) == pytest.approx(10000.0)
    countries = {c.get("country") or c.get("kind"): c["dollars"] for c in w["by_country"]}
    assert countries == pytest.approx({"US": 5010.0, "TW": 3000.0, "CN": 1500.0, "cash": 490.0})
    industries = {c.get("industry") or c.get("kind"): c["dollars"] for c in w["by_industry"]}
    assert industries["Semiconductors"] == pytest.approx(3000.0)
    assert industries["Rail & Road Transport"] == pytest.approx(10.0)
    xyz = next(c for c in w["by_company"] if c.get("company") == "Xyz Corp")
    assert xyz["name"] is None and xyz["industry"] == "Rail & Road Transport"  # counted, though no name stands for it
    assert w["unclassified"] == {"industry": 0, "country": 0}


def test_a_listings_symbol_is_built_by_its_exchanges_rule_and_a_country_named_as_iso_names_it():
    assert symbols_for("AAL LN", "CH", "GB00BTK05J60") == ["AAL.SW", "AAL.L"]  # a fund naming the wrong country
    assert symbols_for("ADDT B", "SE") == ["ADDT-B.ST"] and symbols_for("ADVANC-F", "TH") == ["ADVANC.BK"]
    assert symbols_for("700", "HK") == ["0700.HK"] and symbols_for("000001", None, "CNE000000040") == ["000001.SZ"]
    assert symbols_for("BRK.B", "US") == ["BRK-B"] and region_of("BRK-B") == "us" and region_of("600519.SS") == "cn"
    assert [country_code(n) for n in ("South Korea", "Turkey", "Macau", "Curacao", "Cayman Islands")] == \
        ["KR", "TR", "MO", "CW", "KY"]
    assert [country_code(n) for n in ("Macedonia", "Sint Maarten", "Falkland Islands", "Taiwan")] == \
        ["MK", "SX", "FK", "TW"]  # FinanceDatabase's names
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
    by its ISIN, or the one its US CUSIP makes; a company its fund names as trading in the US is found where its ISIN
    says it is registered."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    fund(store, "F", [
        {"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
        {"constituent_ticker": "OKE.UN", "isin": "US6826801036", "weight": 0.001},  # each below the floor: no name
        {"constituent_ticker": "GPS", "isin": "US3647601083", "weight": 0.001},  # the ticker since changed
        {"constituent_ticker": "HONIV", "us_code": "438516205", "weight": 0.001},  # when-issued, its CUSIP alone
        {"constituent_ticker": "8210", "isin": "TW0008210006", "weight": 0.001},  # named as trading in the US
        {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.496}])
    equities(store, [("AAPL", "Apple Inc.", "Information Technology", "Computers & Hardware", "US0378331005",
                      "United States"),
                     ("OKE", "ONEOK", "Energy", "Oil, Gas & Fuels", "US6826801036", "United States"),
                     ("GAP", "Gap", "Consumer Discretionary", "Specialty Retail", "US3647601083", "United States"),
                     ("HON", "Honeywell", "Industrials", "Conglomerates", "US4385161066", "United States"),
                     ("HON", "Honeywell", "Industrials", "Conglomerates", "US4385162056", "United States"),
                     ("8210.TW", "Chenbro Micom", "Information Technology", "Computers & Hardware", "TW0008210006",
                      "Taiwan")])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 100000.0}]})
    w = store.engine.value("weights", "portfolio").output
    industries = {c["company"]: (c["industry"], c["country"]) for c in w["by_company"] if "company" in c}
    assert industries == {"Apple Inc.": ("Computers & Hardware", "US"),
                          "ONEOK": ("Oil, Gas & Fuels", "US"), "Gap": ("Specialty Retail", "US"),
                          "Honeywell": ("Conglomerates", "US"), "Chenbro Micom": ("Computers & Hardware", "TW")}
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
    equities(store, [("AAPL", "Apple Inc.", "Information Technology", "Computers & Hardware", "US0378331005",
                      "United States")])
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


def test_a_company_the_file_holds_by_none_of_its_listings_has_the_home_its_profile_gives_and_no_industry(store):
    """Where FinanceDatabase's file holds a company by none of its symbols or its ISIN, its home is its ISIN's where
    its fund names that it trades there, and else the first profile that answers of the listings it has: its ISIN's
    search, then its own ticker where it trades; else it has none. Its industry is the file's classification's, and it
    stands under none: one classification for every market."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    fund(store, "F", [{"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
                      {"constituent_ticker": "QRVO", "constituent_name": "QORVO INC", "isin": "US74736K1016",
                       "weight": 0.001},
                      {"constituent_ticker": "ICT", "constituent_name": "INTL CONTAINER TERMINAL",
                       "isin": "PHY411571011", "country_of_exchange": "PH", "weight": 0.002},
                      {"constituent_ticker": "KZX", "constituent_name": "KAZAX CORP", "isin": "KZ0000000001",
                       "country_of_exchange": "US", "weight": 0.002},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.495}])
    equities(store, [("AAPL", "Apple Inc.", "Information Technology", "Computers & Hardware", "US0378331005",
                      "United States")])  # Qorvo, ICT and Kazax left out
    symbol(store, "KZ0000000001:US", None)
    store.put_data("profile", f"KZX@{MONTH}", {"status": "NOT_FOUND", "symbol": "KZX"},
                   {"method": "GET", "url": "profile"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    store.put_data("figi", "KZ0000000001", {"status": "OK", "isin": "KZ0000000001", "composites": [],
                                            "listings": [{"ticker": "KZXGF", "exchCode": "US"}]},
                   {"method": "POST", "url": "openfigi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    profile(store, "KZXGF", "Kazakhstan", "Energy", "Oil & Gas", "Kazax Corporation")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    w = store.engine.value("weights", "portfolio").output
    got = {c["company"]: (c["industry"], c["country"]) for c in w["by_company"] if "company" in c}
    assert got["QORVO INC"] == (None, "US")  # its fund's holdings say it trades in the US: no profile read
    assert got["INTL CONTAINER TERMINAL"] == (None, "PH")  # registered there and traded there: no profile read
    assert got["KAZAX CORP"] == (None, None)  # no profile of its listings gives a country
    assert w["unclassified"] == {"industry": 3, "country": 1}


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
    equities(store, [("TSM", "Taiwan Semiconductor Manufacturing Company Limited", "Information Technology",
                      "Semiconductors", "US8740391003", "Taiwan"),
                     ("AAPL", "Apple Inc.", "Information Technology", "Computers & Hardware", "US0378331005",
                      "United States")])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 5000.0},
                                                       {"ticker": "TSM", "dollars": 5000.0}]})
    w = store.engine.value("weights", "portfolio").output
    homes = {c["company"]: c["country"] for c in w["by_company"]}
    assert homes == {"Taiwan Semiconductor Manufacturing Company Limited": "TW", "Apple Inc.": "US"}
    held = next(h for h in holdings(store)["holdings"] if h["ticker"] == "TSM")
    assert held["company"] == "Taiwan Semiconductor Manufacturing Company Limited"  # not the receipt's name
    tsm = next(c for c in w["by_company"] if c["name"] == "name:TSM")  # nor its SIC: the one classification's
    assert (tsm["industry_id"], tsm["country_id"]) == ("industry:semiconductors", "country:TW")
    from harness.ledger import group
    groups = {g["id"]: group(store, g["id"])["companies"] for g in holdings(store)["by_industry"] if g.get("id")}
    assert groups == {"industry:semiconductors": 1, "industry:computers-hardware": 1}  # each group by its id


def test_a_company_is_classified_by_its_own_securities_before_a_symbol_built_from_a_rows_ticker(store):
    """A company's industry follows the identifiers it is known by. AngloGold Ashanti's row names its Johannesburg
    ticker, ANG, under a US exchange, beside its British ISIN; its share class's US listing is AU, by which its
    identifiers confirm the company. ANG on London is Angling Direct, which the screener holds: the company is
    AngloGold, classified by AU, its own listing, never by a symbol built from the row's ticker and a country. Its
    home, where it trades in the country of its ISIN neither by a listing of it nor by its fund's word, is its
    record's."""
    ref(store, "F", "ETF")
    ref(store, "AU", "CS", cik="0001973832")
    au = [{"ticker": "AU", "exchCode": "US", "compositeFIGI": "FIGIAU", "shareClassFIGI": "SCAU"}]
    store.put_data("figi", "GB00BRXH2664", {"status": "OK", "isin": "GB00BRXH2664", "listings": au,
                                           "composites": ["FIGIAU"]},
                   {"method": "POST", "url": "figi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    fund(store, "F", [{"constituent_name": "ANGLOGOLD ASHANTI PLC", "constituent_ticker": "ANG", "isin": "GB00BRXH2664",
                       "country_of_exchange": "US", "weight": 0.5},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.5}])
    equities(store, [("AU", "AngloGold Ashanti plc", "Materials", "Mining & Metals", "GB00BRXH2664", "United Kingdom"),
                     ("ANG.L", "Angling Direct PLC", "Consumer Discretionary", "Specialty Retail", "GB00BD3VFW73",
                      "United Kingdom")])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 1000.0}]})
    w = store.engine.value("weights", "portfolio").output
    [company] = [c for c in w["by_company"] if c.get("name")]
    assert (company["name"], company["company"], company["industry"], company["country"]) == \
        ("name:AU", "AngloGold Ashanti plc", "Mining & Metals", "GB")


def test_a_company_trading_where_its_isin_is_registered_by_its_funds_word_has_that_home():
    """MATH.md 3.1: a company's home is the country it is registered in where it also trades there. Met through a fund's
    row on an exchange in the country of its ISIN, and classified by its own US listing, it has that home from its
    fund's word that it trades there; met by a row on an exchange elsewhere, with no listing of it there in the file,
    its home is its own record's, the file's country."""
    from harness.derive.owned import classify
    shop = [{"symbol": "SHOP", "name": "Shopify Inc.", "sector": "Information Technology", "industry": "Software",
             "isin": "CA82509L1076", "country": "Canada", "exchange": "NYQ", "delisted": False}]
    held = {"symbol:SHOP": shop, "isin:CA82509L1076": shop}
    readers = (lambda subject: held.get(subject, []), lambda listing: "", lambda s: None, lambda isin: [])
    at_home = {"row": {"constituent_ticker": "SHOP", "isin": "CA82509L1076", "country_of_exchange": "CA"}}
    elsewhere = {"row": {"constituent_ticker": "SHOP", "isin": "CA82509L1076", "country_of_exchange": "DE"}}
    got = classify({"name:SHOP": at_home, "name:OTHER": elsewhere},
                   {"name:SHOP": {"SHOP": 1.0}, "name:OTHER": {"SHOP": 1.0}}, *readers)
    assert [got["name:SHOP"][k] for k in ("industry", "home", "home_named", "wants")] == ["Software", "CA", None, []]
    assert [got["name:OTHER"][k] for k in ("home", "home_named", "wants")] == ["CA", "Canada", []]


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
    by which the file's rows classify it, where its own fields give neither."""
    from harness.derive.owned import built_symbols, traded
    row = {"isin": "KYG875721634", "constituent_name": "TENCENT", "listing": {"ticker": "700", "country": "HK"}}
    assert traded(row) == ("700", "HK")
    assert built_symbols({"row": row}) == ["0700.HK"]
    stated = {**row, "constituent_ticker": "700", "country_of_exchange": "HK"}
    assert traded({**stated, "listing": {"ticker": "X", "country": "US"}}) == ("700", "HK")  # the fund's own first


def test_a_company_below_the_floor_the_file_lacks_is_read_no_further_and_stands_under_no_country():
    """MATH.md 3.1: every row is counted. A company below the look-through's floor that FinanceDatabase's file holds by
    none of its symbols or its ISIN, and whose fund names no exchange in its ISIN's country, stands under no country;
    no search or profile is read for it. At or above the floor, its profile is read."""
    from harness.derive.owned import classify
    nothing = (lambda subject: [], lambda listing: None, lambda symbol: None, lambda isin: [])
    row = {"row": {"constituent_ticker": "ZZZ", "isin": "KZ0000000001"}, "dollars": 4.0}
    small = classify({"row:a": row}, {}, *nothing, floor=5.0)["row:a"]
    assert (small["home"], small["wants"]) == (None, [])
    large = classify({"row:a": {**row, "dollars": 6.0}}, {}, *nothing, floor=5.0)["row:a"]
    assert large["wants"] == [("symbol", "KZ0000000001:KZ")]
