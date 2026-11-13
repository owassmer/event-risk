"""One name per company (MATH.md 3.1): whatever securities the portfolio holds a company by, it is one name, its id its first
US ticker or else its home listing, priced through the security the member holds or, where it is held only through
funds, its listing on its home market; each security's dollars kept beside it. On data in each source's own shapes."""

from __future__ import annotations

import json

import pytest

from harness.derive import default_registry
from harness.derive.identity import SEC_FILE
from harness.store import Store, StoreError
from massive_data import ASOF, BASE, FETCHED, flat_prices, put_home, put_rate

SEC = {"status": "OK", "fields": ["cik", "name", "ticker", "exchange"],
       "data": [[1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"], [1046179, "TAIWAN SEMICONDUCTOR", "TSM", "NYSE"],
                [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"], [1046179, "TAIWAN SEMICONDUCTOR", "TSMWF", "OTC"],
                [1560385, "Liberty Media Corp", "FWONA", "Nasdaq"], [1560385, "Liberty Media Corp", "FWONK", "Nasdaq"],
                [2078416, "Liberty Live Holdings, Inc.", "LLYVA", "Nasdaq"]]}


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-one-name", registry=default_registry())
    flat_prices(s)  # each holding priced, where the test's subject is what it holds
    s.put_data("sec_tickers", SEC_FILE, SEC, {"method": "GET", "url": "sec"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    yield s
    s.close()


def ref(store, ticker, kind, cik=None, name=None):
    payload = {"status": "OK", "results": {"ticker": ticker, "type": kind, "market": "stocks", "locale": "us",
                                           "composite_figi": f"FIGI{ticker}", **({"cik": cik} if cik else {}),
                                           **({"name": name} if name else {})}}
    store.put_data("ticker_ref", ticker, payload, {"method": "GET", "url": f"{BASE}/v3/reference/tickers/{ticker}"},
                   retrieved_at=FETCHED, range=(ASOF, ASOF))


def fund(store, ticker, rows):
    """rows: dicts of a constituent's own fields; each a share."""
    results = [{"composite_ticker": ticker, "asset_class": "Equity", "effective_date": "2026-11-03",
                "country_of_exchange": "US", **r} for r in rows]
    store.put_data("constituents", ticker, {"status": "OK", "composite_ticker": ticker, "effective_date": "2026-11-03",
                                            "results": results}, {"method": "GET", "url": "etf"},
                   retrieved_at=FETCHED, range=(ASOF, ASOF))


def test_a_mutual_fund_a_fund_of_funds_holds_is_a_fund_and_is_looked_through_once_more(store):
    """MATH.md 3.1: a fund held by a fund is expanded once more, at the product of the two exposures. A target-date fund's
    rows are its sponsor's other mutual funds, which no exchange lists: each row's identifiers name its share class
    (OpenFIGI), and the class is a fund (harness.venues.funds), read from its own Form N-PORT report."""
    def mutual(ticker):
        store.put_data("ticker_ref", ticker, {"status": "OK", "results": {"ticker": ticker, "type": "MF", "market": "funds",
                                                                         "active": True}},
                       {"method": "GET", "url": "chart"}, retrieved_at=FETCHED, range=(ASOF, ASOF))

    def report(fund, rows):
        store.put_data("nport", fund, {"status": "OK", "fund": fund, "effective_date": "2026-09-30", "net_assets": 1e9,
                                      "results": [{"asset_category": "EC", "issuer_category": "RF", "currency": "USD",
                                                   "effective_date": "2026-09-30", **r} for r in rows]},
                       {"method": "GET", "url": "nport"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    mutual("TDF")
    mutual("STOCKX")
    ref(store, "A", "CS")
    store.put_data("figi", "US9229087286", {"status": "OK", "isin": "US9229087286", "composites": [],
                                            "listings": [{"ticker": "STOCKX", "exchCode": "US", "compositeFIGI": "FIGISTOCKX"}]},
                   {"method": "POST", "url": "openfigi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    report("TDF", [{"constituent_name": "A total stock market index fund", "isin": "US9229087286", "weight": 0.6}])
    report("STOCKX", [{"constituent_ticker": "A", "figi": "FIGIA", "issuer_category": "CORP", "weight": 0.5}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "TDF", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"]["name:A"] == pytest.approx(1000.0 * 0.6 * 0.5)
    assert out["links"]["looks_through_to:name:TDF>name:STOCKX"]["fields"]["dollars"] == pytest.approx(600.0)


def test_a_fund_of_funds_divides_by_kind_as_the_funds_it_holds_do(store):
    """MATH.md 3.1: a fund is what it holds, each holding with its kind of asset. A fund of funds' rows are funds, each a
    share of a fund (a report's \"equity-common\"), whose own holdings say what its dollars are held in: a bond fund it
    holds is debt."""
    def mutual(ticker):
        store.put_data("ticker_ref", ticker, {"status": "OK", "results": {"ticker": ticker, "type": "MF", "market": "funds",
                                                                         "active": True}},
                       {"method": "GET", "url": "chart"}, retrieved_at=FETCHED, range=(ASOF, ASOF))

    def report(fund, rows):
        store.put_data("nport", fund, {"status": "OK", "fund": fund, "effective_date": "2026-09-30", "net_assets": 1e9,
                                      "results": [{"currency": "USD", "effective_date": "2026-09-30", **r} for r in rows]},
                       {"method": "GET", "url": "nport"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    for t in ("TDF", "STOCKX", "BONDX"):
        mutual(t)
    ref(store, "A", "CS")
    for isin, t in (("US9229087286", "STOCKX"), ("US9219378356", "BONDX")):
        store.put_data("figi", isin, {"status": "OK", "isin": isin, "composites": [],
                                      "listings": [{"ticker": t, "exchCode": "US", "compositeFIGI": f"FIGI{t}"}]},
                       {"method": "POST", "url": "openfigi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    share = {"asset_category": "EC", "issuer_category": "RF"}  # a share of a fund, as a report states it
    report("TDF", [{"constituent_name": "A stock index fund", "isin": "US9229087286", "weight": 0.6, **share},
                   {"constituent_name": "A bond index fund", "isin": "US9219378356", "weight": 0.4, **share}])
    report("STOCKX", [{"constituent_ticker": "A", "figi": "FIGIA", "asset_category": "EC", "issuer_category": "CORP",
                       "weight": 1.0}])
    report("BONDX", [{"constituent_name": "A Treasury note", "asset_category": "DBT", "issuer_category": "UST",
                      "weight": 1.0}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "TDF", "dollars": 1000.0}]})
    held = looked(store)["funds"]["TDF"]["holds"]
    assert {d["kind"]: d["weight"] for d in held["by_kind"]} == pytest.approx({"equity": 0.6, "debt": 0.4})
    assert [(r["priced_as"], r["kind"]) for r in held["named"]] == [("name:STOCKX", "equity"), ("name:BONDX", "debt")]
def test_a_report_row_that_resolves_reads_where_its_listing_is_and_its_ticker(store):
    """MATH.md 3.1: a fund is what it holds, each holding by name and where it is listed. A Form N-PORT report's rows
    state no exchange and often no ticker; where a row's identifiers resolve to a US listing, it is listed in the US
    under that listing's ticker."""
    store.put_data("ticker_ref", "RPT", {"status": "OK", "results": {"ticker": "RPT", "type": "FUND", "market": "stocks"}},
                   {"method": "GET", "url": "ref"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    ref(store, "A", "CS")
    store.put_data("figi", "US0000000AA1", {"status": "OK", "isin": "US0000000AA1", "composites": [],
                                            "listings": [{"ticker": "A", "exchCode": "US", "compositeFIGI": "FIGIA"}]},
                   {"method": "POST", "url": "openfigi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    store.put_data("nport", "RPT", {"status": "OK", "fund": "RPT", "effective_date": "2026-09-30", "net_assets": 1e9,
                                    "results": [{"constituent_name": "A Corp", "isin": "US0000000AA1", "weight": 1.0,
                                                 "asset_category": "EC", "issuer_category": "CORP", "currency": "USD",
                                                 "effective_date": "2026-09-30"}]},
                   {"method": "GET", "url": "nport"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "RPT", "dollars": 1000.0}]})
    held = looked(store)["funds"]["RPT"]["holds"]
    assert [(r["ticker"], r["listed"], r["priced_as"]) for r in held["named"]] == [("A", "United States", "name:A")]
    assert [(d["listed"], d["weight"]) for d in held["by_listing"]] == [("United States", 1.0)]


def taiwan(store):
    """TSMC: its ordinary on its home market, whose share class OpenFIGI lists in the US only over the counter, and its
    receipt on the NYSE, which the SEC lists under the same company."""
    store.put_data("figi", "TW0002330008", {"status": "OK", "isin": "TW0002330008", "composites": [],
                                            "listings": [{"ticker": "TSMWF", "exchCode": "US", "compositeFIGI": "FIGITSMWF"}]},
                   {"method": "POST", "url": "openfigi"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    ref(store, "TSMWF", "OS", cik="0001046179")
    store.put_data("company_listings", "0001046179", {"status": "OK", "results": [
        {"ticker": "TSM", "type": "ADRC", "market": "stocks", "cik": "0001046179"},
        {"ticker": "TSMWF", "type": "OS", "market": "otc", "cik": "0001046179"}]},
        {"method": "GET", "url": "listings"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    ref(store, "TSM", "ADRC", cik="0001046179", name="Taiwan Semiconductor Manufacturing")


def looked(store):
    v = store.engine.value("lookthrough", "portfolio")
    assert v.output is not None, v.cause
    return v.output


def test_two_classes_a_fund_holds_are_one_name_its_first_us_ticker_priced_through_it(store):
    ref(store, "F", "ETF")
    ref(store, "GOOG", "CS", cik="0001652044")
    ref(store, "GOOGL", "CS", cik="0001652044")
    fund(store, "F", [{"constituent_ticker": "GOOG", "figi": "FIGIGOOG", "weight": 0.4},
                      {"constituent_ticker": "GOOGL", "figi": "FIGIGOOGL", "weight": 0.6}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"]["name:GOOGL"] == pytest.approx(1000.0) and "name:GOOG" not in out["e"]
    assert out["securities"]["name:GOOGL"] == pytest.approx({"GOOG": 400.0, "GOOGL": 600.0})
    assert out["kinds"]["name:GOOGL"] == {"kind": "name", "beta": "GOOGL", "security": "GOOGL"}
    link = out["links"]["looks_through_to:name:F>name:GOOGL"]["fields"]
    assert (link["dollars"], link["weight"]) == (pytest.approx(1000.0), pytest.approx(1.0))


def test_a_listings_company_is_the_one_the_secs_list_gives_it_where_massives_record_names_another(store):
    """A CIK is the SEC's to issue, and Massive's copy can be out of date: here it files Formula One's A shares under
    the CIK of Liberty Live, since split off, and their C shares under Liberty Media. The SEC's list gives the A and C
    shares Liberty Media's and Liberty Live its own, so the fund holds two companies, each whole."""
    ref(store, "F", "ETF")
    ref(store, "FWONA", "CS", cik="0000869614")
    ref(store, "FWONK", "CS", cik="0001560385")
    ref(store, "LLYVA", "CS", cik="0000869614")
    fund(store, "F", [{"constituent_ticker": "FWONA", "figi": "FIGIFWONA", "weight": 0.3},
                      {"constituent_ticker": "FWONK", "figi": "FIGIFWONK", "weight": 0.3},
                      {"constituent_ticker": "LLYVA", "figi": "FIGILLYVA", "weight": 0.4}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 1000.0}]})
    out = looked(store)
    assert out["securities"]["name:FWONA"] == pytest.approx({"FWONA": 300.0, "FWONK": 300.0})
    assert out["securities"]["name:LLYVA"] == pytest.approx({"LLYVA": 400.0})
    assert "name:FWONK" not in out["e"]


def test_a_company_held_only_through_funds_is_priced_on_its_home_market_named_by_its_first_us_ticker(store):
    ref(store, "V", "ETF")
    taiwan(store)
    fund(store, "V", [{"constituent_ticker": "2330", "isin": "TW0002330008", "country_of_exchange": "TW",
                       "weight": 0.5}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "V", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"]["name:TSM"] == pytest.approx(500.0)
    assert out["kinds"]["name:TSM"]["security"] == "TW0002330008:TW"  # Taipei, in its own currency
    assert out["securities"]["name:TSM"] == pytest.approx({"TW0002330008:TW": 500.0})


def test_a_company_held_directly_by_its_listing_abroad_is_a_company(store):
    """MATH.md 3.1: a share of a company in any form is a name, its listing abroad as much as a US one, which no US
    reference gives; it is its company's one name, as a fund's row of it is."""
    taiwan(store)
    put_home(store, "TW0002330008:TW", {"2026-11-03": 1500.0, ASOF: 1510.0}, currency="TWD", mic="XTAI")
    put_rate(store, "C:TWDUSD", {"2026-11-03": 0.031, ASOF: 0.031}, base="TWD", quote="USD")
    store.put_data("ticker_ref", "TW0002330008:TW", {"status": "INVALID", "error": "not a US ticker"},
                   {"method": "GET", "url": "ref"}, retrieved_at=FETCHED, range=(ASOF, ASOF))  # as Massive answers it
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "TW0002330008:TW", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"] == pytest.approx({"name:TSM": 1000.0})
    assert out["kinds"]["name:TSM"]["kind"] == "name" and out["kinds"]["name:TSM"]["security"] == "TW0002330008:TW"
    assert "kind:other" not in out["owned"]


def test_held_directly_the_member_s_security_prices_it_and_every_dollar_of_the_company_is_one_name(store):
    ref(store, "V", "ETF")
    taiwan(store)
    fund(store, "V", [{"constituent_ticker": "2330", "isin": "TW0002330008", "country_of_exchange": "TW",
                       "weight": 0.5}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "TSM", "dollars": 300.0},
                                                       {"ticker": "V", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"]["name:TSM"] == pytest.approx(800.0)
    assert out["kinds"]["name:TSM"]["security"] == "TSM"  # the receipt the member holds
    assert out["securities"]["name:TSM"] == pytest.approx({"TSM": 300.0, "TW0002330008:TW": 500.0})
    held = [h for h in out["holdings"].values() if h["security"] == "TSM"]
    assert held == [{"name": "name:TSM", "security": "TSM"}]


def test_two_classes_held_directly_are_one_name_priced_through_its_first_us_ticker(store):
    ref(store, "GOOG", "CS", cik="0001652044")
    ref(store, "GOOGL", "CS", cik="0001652044")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "GOOG", "dollars": 100.0},
                                                       {"ticker": "GOOGL", "dollars": 200.0}]})
    out = looked(store)
    assert out["e"]["name:GOOGL"] == pytest.approx(300.0) and out["kinds"]["name:GOOGL"]["security"] == "GOOGL"
    assert sorted(h["security"] for h in out["holdings"].values()) == ["GOOG", "GOOGL"]
    assert {h["name"] for h in out["holdings"].values()} == {"name:GOOGL"}


def holds(store):
    return sorted((r["to_id"], json.loads(r["stated"]).get("ticker")) for r in
                  store.db.execute("SELECT to_id, stated FROM link WHERE relation = 'holds'"))


def test_holdings_given_with_the_portfolio_link_to_their_companys_one_name_each_with_its_security(store):
    ref(store, "GOOG", "CS", cik="0001652044", name="Alphabet Inc.")
    ref(store, "GOOGL", "CS", cik="0001652044", name="Alphabet Inc.")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "GOOG", "dollars": 100.0},
                                                       {"ticker": "GOOGL", "dollars": 200.0}]})
    assert holds(store) == [("name:GOOGL", "GOOG"), ("name:GOOGL", "GOOGL")]
    assert store.type_of("name:GOOG") is None or "held" not in (store.get("name:GOOG").get("because") or [])


def test_a_holding_linked_names_its_companys_name_and_is_held_once_by_each_security(store):
    ref(store, "GOOG", "CS", cik="0001652044", name="Alphabet Inc.")
    ref(store, "GOOGL", "CS", cik="0001652044", name="Alphabet Inc.")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "GOOGL", "dollars": 200.0}]})
    with pytest.raises(StoreError, match='GOOG is a security of the company whose name is name:GOOGL'):
        store.link("portfolio", "holds", "name:GOOG", {"dollars": 100.0})
    store.link("portfolio", "holds", "name:GOOGL", {"ticker": "GOOG", "dollars": 100.0})
    assert holds(store) == [("name:GOOGL", "GOOG"), ("name:GOOGL", "GOOGL")]
    with pytest.raises(StoreError, match="edit it instead"):
        store.link("portfolio", "holds", "name:GOOGL", {"ticker": "GOOG", "dollars": 50.0})


def test_a_holdings_change_that_would_reprice_a_name_a_shock_is_stated_on_is_refused_naming_the_shock(store):
    ref(store, "V", "ETF")
    taiwan(store)
    fund(store, "V", [{"constituent_ticker": "2330", "isin": "TW0002330008", "country_of_exchange": "TW",
                       "weight": 0.5}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "TSM", "dollars": 300.0},
                                                       {"ticker": "V", "dollars": 1000.0}]})
    held = store.db.execute("SELECT id FROM link WHERE relation = 'holds' AND to_id = 'name:TSM'").fetchone()[0]
    assert looked(store)["kinds"]["name:TSM"]["security"] == "TSM"
    with store.transaction():  # a shock on its price, as the analyst states one once a consequence runs to a name
        shock = store._insert_link("consequence", "prop:1", "name:TSM", {"shock": {"value": -0.2}}, "analyst", None)
    with pytest.raises(StoreError, match=f"name:TSM is priced through TSM, and this change would price it through "
                                         f"TW0002330008:TW, while {shock} states a shock on its price"):
        store.remove(held)  # held only through V, it would be priced in Taipei
    with store.transaction():
        store.db.execute("DELETE FROM link WHERE id = ?", (shock,))
    store.remove(held)  # with no shock on it, its pricing follows the holdings
    assert looked(store)["kinds"]["name:TSM"]["security"] == "TW0002330008:TW"


def test_a_company_a_consequence_names_is_reached_at_any_exposure_and_left_when_the_consequence_goes(store):
    """MATH.md 1.6, 3.1: a company an event names, held only below the floor through a fund, has every dollar of
    exposure to it booked to it once a consequence names it, a US company by its share classes' listings, one listed
    only abroad by its ISIN; and none once the consequence is removed."""
    ref(store, "F", "ETF")
    ref(store, "AAPL", "CS")
    ref(store, "XYZ", "CS")
    fund(store, "F", [{"constituent_ticker": "AAPL", "figi": "FIGIAAPL", "isin": "US0378331005", "weight": 0.5},
                      {"constituent_ticker": "XYZ", "figi": "FIGIXYZ", "weight": 0.001},
                      {"constituent_ticker": "6861", "isin": "JP3236200006", "country_of_exchange": "JP",
                       "weight": 0.002},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.497}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 10000.0}]})
    assert "name:XYZ" not in looked(store)["e"] and "name:JP3236200006:JP" not in looked(store)["e"]
    pid = store.add("Proposition", {"title": "T", "text": "T: the proposition", "comparison": "occurs",
                                    "window_end": "2027-03-31", "kind": "hitting"})["id"]
    with store.transaction():
        shocks = [store._insert_link("consequence", pid, nid, {"shock": {"value": -0.2}}, "analyst", None)
                  for nid in ("name:XYZ", "name:JP3236200006:JP")]
    out = looked(store)
    assert out["e"]["name:XYZ"] == pytest.approx(10.0) and out["e"]["name:JP3236200006:JP"] == pytest.approx(20.0)
    assert out["kinds"]["name:JP3236200006:JP"]["security"] == "JP3236200006:JP"  # priced on its home market
    assert "looks_through_to:name:F>name:XYZ" in out["links"]
    store.remove(shocks[0])
    assert "name:XYZ" not in looked(store)["e"] and "name:JP3236200006:JP" in looked(store)["e"]


def test_a_company_a_link_names_keeps_that_id_where_the_rule_would_now_choose_another(store):
    """MATH.md 3.1: a company's id is a listing chosen once by one rule and kept. A shock stated on name:GOOGL, the id the
    rule chose, and a holding of GOOG linked to it, keep the company named GOOGL once the SEC's list gives GOOG first:
    every dollar of it, held and through the fund, books under name:GOOGL, so the shock reaches all of it; and a shock
    on name:GOOG, which the rule would now choose, is refused, naming the company's id in this store."""
    ref(store, "F", "ETF")
    ref(store, "GOOG", "CS", cik="0001652044")
    ref(store, "GOOGL", "CS", cik="0001652044")
    fund(store, "F", [{"constituent_ticker": "GOOG", "figi": "FIGIGOOG", "weight": 0.4},
                      {"constituent_ticker": "GOOGL", "figi": "FIGIGOOGL", "weight": 0.6}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 1000.0},
                                                     {"ticker": "GOOG", "dollars": 500.0}]})
    assert [link["to"] for link in store.links_of("portfolio", "holds")] == ["name:F", "name:GOOGL"]
    pid = store.add("Proposition", {"title": "T", "text": "T: the proposition", "comparison": "occurs",
                                    "window_end": "2027-03-31", "kind": "hitting", "baseline": "no"})["id"]
    store.link(pid, "consequence", "name:GOOGL", {"shock": {"value": -0.1, "note": "a fine on the company"}})
    first = [r for r in SEC["data"] if r[2] == "GOOG"] + [r for r in SEC["data"] if r[2] != "GOOG"]
    store.put_data("sec_tickers", SEC_FILE, {**SEC, "data": first}, {"method": "GET", "url": "sec"},
                   retrieved_at="2026-11-05T03:00:00+00:00", range=(ASOF, ASOF))
    from harness.derive.identity import name_of
    assert name_of(store, "GOOG", ASOF)["name"] == "name:GOOG"  # what the rule would choose now
    out = looked(store)
    assert out["e"]["name:GOOGL"] == pytest.approx(1500.0) and "name:GOOG" not in out["e"]
    assert out["securities"]["name:GOOGL"] == pytest.approx({"GOOG": 900.0, "GOOGL": 600.0})
    with pytest.raises(StoreError) as refused:
        store.link(pid, "consequence", "name:GOOG", {"shock": {"value": -0.05, "note": "a fine"}})
    assert refused.value.as_dict()["dependents"] == ["name:GOOGL"]


def test_a_consequence_written_on_another_listing_of_a_company_is_refused_naming_the_company_s_own(store):
    """MATH.md 3.1: a company is one name whatever securities list it, so a shock on it is stated on its id, the listing
    one rule chooses (identity.name_of): GOOG is a class of the company whose name is GOOGL, and nothing is written."""
    ref(store, "GOOG", "CS", cik="0001652044")
    ref(store, "GOOGL", "CS", cik="0001652044")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"cash": 1000.0}]})
    pid = store.add("Proposition", {"title": "T", "text": "T: the proposition", "comparison": "occurs",
                                    "window_end": "2027-03-31", "kind": "hitting", "baseline": "no"})["id"]
    before = store._all("SELECT id FROM link ORDER BY rowid")
    with pytest.raises(StoreError) as refused:
        store.link(pid, "consequence", "name:GOOG", {"shock": {"value": -0.1, "note": "a fine on the company"}})
    assert "name:GOOGL" in str(refused.value) and refused.value.as_dict()["dependents"] == ["name:GOOGL"]
    assert store._all("SELECT id FROM link ORDER BY rowid") == before


# ---------------------------------------------------------------- a listing abroad named by its exchange's symbol


def put(store, kind, subject, payload):
    store.put_data(kind, subject, payload, {"method": "GET", "url": kind}, retrieved_at=FETCHED, range=(ASOF, ASOF))


def home_ref(store, symbol, name, mic, locale, currency, kind="CS"):
    """A listing abroad's reference, as its own market gives it (harness.venues.yahoo.Yahoo.reference)."""
    put(store, "ticker_ref", symbol, {"status": "OK", "results": {
        "ticker": symbol, "name": name, "type": kind, "market": "home", "locale": locale, "primary_exchange": mic,
        "currency_name": currency, "active": True}})


def tencent(store):
    """Tencent: its Hong Kong line, 0700.HK, whose share class OpenFIGI lists in the US only over the counter, which the
    SEC lists under no company; its ISIN, KYG875721634, as a fund's holdings give it beside its Hong Kong FIGI."""
    home_ref(store, "0700.HK", "Tencent Holdings Limited", "XHKG", "hk", "hkd")
    put(store, "figi_listing", "0700.HK@XHKG", {"status": "OK", "symbol": "0700.HK", "mic": "XHKG", "ticker": "700",
                                                "share_classes": ["BBG001S8C4T6"], "composites": ["BBG000BJ35F4"]})
    put(store, "figi_isin", "BBG000BJ35F4", {"status": "OK", "figi": "BBG000BJ35F4", "isin": "KYG875721634"})
    otc = [{"ticker": "TCTZF", "exchCode": "US", "compositeFIGI": "FIGITCTZF", "shareClassFIGI": "BBG001S8C4T6"}]
    put(store, "figi", "KYG875721634", {"status": "OK", "isin": "KYG875721634", "listings": otc,
                                        "composites": ["BBG000BJ35F4", "FIGITCTZF"]})
    put(store, "figi_class", "BBG001S8C4T6", {"status": "OK", "share_class": "BBG001S8C4T6", "listings": otc,
                                              "composites": ["BBG000BJ35F4", "FIGITCTZF"]})
    ref(store, "TCTZF", "OS")
    put(store, "isin_listings", f"KYG875721634@{ASOF[:7]}", {"status": "OK", "isin": "KYG875721634",
                                                             "listings": [{"ticker": "700", "country": "HK", "rows": 316}]})
    for country in ("HK", "KY"):  # a fund's holdings name its exchange's country; a report, its ISIN's
        put(store, "listing_symbol", f"KYG875721634:{country}@{ASOF[:7]}",
            {"status": "OK", "listing": f"KYG875721634:{country}", "symbol": "0700.HK"})


def test_a_listing_abroad_is_named_by_its_symbol_held_directly_at_the_floor_and_below_it_one_name(store):
    """ONTOLOGY.md 2; MATH.md 3.1: a company's id is its first US ticker, else its home listing, named by its exchange's
    own symbol. Tencent held as 0700.HK, through a fund's row at the floor (its ISIN and Hong Kong FIGI), and through a
    report's row below the floor that names it by its ISIN alone, is one name, name:0700.HK, priced through the symbol
    held; and `{ISIN}:{country}` given is the same company, named by its symbol."""
    from harness.derive.identity import name_of
    tencent(store)
    put_home(store, "0700.HK", {"2026-11-03": 400.0, ASOF: 410.0}, currency="HKD", mic="XHKG")
    put_rate(store, "C:HKDUSD", {"2026-11-03": 0.128, ASOF: 0.128}, base="HKD", quote="USD")
    ref(store, "F", "ETF")
    ref(store, "G", "ETF")
    fund(store, "F", [{"constituent_ticker": "700", "isin": "KYG875721634", "figi": "BBG000BJ35F4",
                       "country_of_exchange": "HK", "weight": 0.4}])
    fund(store, "G", [{"constituent_name": "TENCENT HOLDINGS LTD", "isin": "KYG875721634", "weight": 0.001,
                       "country_of_exchange": None}])  # as a report names it: no exchange, no ticker
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "0700.HK", "dollars": 1000.0},
                                                     {"ticker": "G", "dollars": 10000.0}]})
    assert looked(store)["e"]["name:0700.HK"] == pytest.approx(1000.0 + 10.0)  # G's row by the held symbol's ISIN
    store.link("portfolio", "holds", "name:F", {"dollars": 1000.0})
    assert [link["to"] for link in store.links_of("portfolio", "holds")] == ["name:0700.HK", "name:G", "name:F"]
    out = looked(store)
    assert out["e"]["name:0700.HK"] == pytest.approx(1000.0 + 400.0 + 10.0)  # held, F's row, and G's below the floor
    assert not [n for n in out["e"] if "KYG875721634" in n]
    assert out["kinds"]["name:0700.HK"]["security"] == "0700.HK"
    listed = {g.get("listed") or g.get("country"): g for g in out["funds"]["G"]["holds"]["by_listing"]}
    assert set(listed) == {"Hong Kong"}, listed  # G's row, named by its ISIN alone, placed where its symbol trades
    assert name_of(store, "KYG875721634:HK", ASOF)["name"] == "name:0700.HK"
    assert name_of(store, "0700.HK", ASOF)["company"]["title"] == "Tencent Holdings Limited"


def test_a_company_held_by_its_symbol_abroad_with_a_us_ticker_is_named_by_it_priced_through_the_symbol(store):
    """MATH.md 3.1: TSMC held as its Taipei line, 2330.TW, is the company whose first US ticker is TSM: its share class's
    line over the counter names its CIK, which the SEC lists TSM first under; it is priced through 2330.TW."""
    taiwan(store)
    home_ref(store, "2330.TW", "Taiwan Semiconductor Manufacturing", "XTAI", "tw", "twd")
    put(store, "figi_listing", "2330.TW@XTAI", {"status": "OK", "symbol": "2330.TW", "mic": "XTAI", "ticker": "2330",
                                                "share_classes": ["BBG001S6Q004"], "composites": ["BBG000BN2HR7"]})
    put(store, "figi_isin", "BBG000BN2HR7", {"status": "OK", "figi": "BBG000BN2HR7", "isin": "TW0002330008"})
    put_home(store, "2330.TW", {"2026-11-03": 1500.0, ASOF: 1510.0}, currency="TWD", mic="XTAI")
    put_rate(store, "C:TWDUSD", {"2026-11-03": 0.031, ASOF: 0.031}, base="TWD", quote="USD")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "2330.TW", "dollars": 1000.0}]})
    [held] = store.links_of("portfolio", "holds")
    assert (held["to"], held["stated"]["ticker"]) == ("name:TSM", "2330.TW")
    out = looked(store)
    assert out["e"]["name:TSM"] == pytest.approx(1000.0) and out["kinds"]["name:TSM"]["security"] == "2330.TW"


def test_a_fund_whose_reference_names_its_sponsors_cik_is_no_name_of_that_company(store):
    """MATH.md 3.1: a fund is its own name. Massive's reference can give a fund its sponsor's CIK (XLF, State Street's), and
    the fund can hold its sponsor: the sponsor's shares in it book to the sponsor's own name, never to the fund, which
    holds no link to itself."""
    ref(store, "XLF", "ETF", cik="0000093751")
    ref(store, "STT", "CS", cik="0000093751")
    ref(store, "JPM", "CS", cik="0000019617")
    store.put_data("sec_tickers", SEC_FILE, {**SEC, "data": SEC["data"] + [[93751, "STATE STREET CORP", "STT", "NYSE"]]},
                   {"method": "GET", "url": "sec"}, retrieved_at="2026-11-05T03:00:00+00:00", range=(ASOF, ASOF))
    fund(store, "XLF", [{"constituent_ticker": "STT", "figi": "FIGISTT", "weight": 0.0057},
                        {"constituent_ticker": "JPM", "figi": "FIGIJPM", "weight": 0.1}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "XLF", "dollars": 48000.0}]})
    out = looked(store)
    assert out["e"]["name:STT"] == pytest.approx(48000.0 * 0.0057)
    assert "looks_through_to:name:XLF>name:XLF" not in out["links"] and "name:XLF" not in out["funds"]["XLF"]["parts"]
