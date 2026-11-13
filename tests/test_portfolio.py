"""The portfolio: holdings, look-through, the orthogonalization, the
betas, g, and the proxy Metrics, on synthetic data in Massive's shapes."""

from __future__ import annotations

import json
import re

from datetime import datetime

import numpy as np
import pytest

from conftest import masked
from harness.derive import default_registry, series
from harness.derive.series import WINDOW
from harness.store import Store, StoreError
from massive_data import (flat_prices, ASOF, FETCHED, PROXIES, SESSIONS, closes_from, load_channels, put_bars,
                          put_constituents, put_hourly, put_market, put_reference, random_returns)



@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-portfolio", registry=default_registry())
    flat_prices(s)  # each holding priced, where the test's subject is what it holds
    yield s
    s.close()


def hold(store, *holdings, total=None):
    fields = {"asof": ASOF, "holdings": [dict(h) for h in holdings]}
    if total is not None:
        fields["total"] = total
    return store.add("Portfolio", fields)


def lookthrough(store) -> dict:
    v = store.engine.value("lookthrough", "portfolio")
    assert v.output is not None, v.cause
    return v.output


def e(store, ticker) -> float:
    return store.get(f"name:{ticker}")["derived"]["exposure"]["value"]


def link_dollars(out, fund, ticker) -> float:
    return out["links"][f"looks_through_to:name:{fund}>name:{ticker}"]["fields"]["dollars"]


def names(store, *pairs):
    for ticker, kind in pairs:
        put_reference(store, ticker, kind)


# ---------------------------------------------------------------- holdings


def test_holdings_give_the_other_two_amounts_at_the_as_of_close_and_v(store):
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    put_bars(store, "NVDA", {"2026-11-03": 190.0, ASOF: 200.0})
    put_bars(store, "XOM", {"2026-11-03": 110.0, ASOF: 100.0})
    hold(store, {"ticker": "NVDA", "quantity": 10}, {"ticker": "XOM", "dollars": 3000.0},
         {"cash": 1000.0}, total=10000.0)
    assert store.get("portfolio")["derived"]["value"]["value"] == pytest.approx(2000 + 3000 + 1000)
    amounts = {n["object"]["id"]: store.get(n["link"])["derived"] for n in store.neighbors("portfolio", "holds")}
    assert {k: v["value"] for k, v in amounts["name:NVDA"].items()} == {"dollars": 2000.0, "weight": pytest.approx(1 / 3)}
    assert amounts["name:XOM"]["quantity"]["value"] == pytest.approx(30.0)
    assert amounts["cash:USD"]["quantity"]["value"] == pytest.approx(1000.0)
    assert store.get("name:NVDA")["derived"]["prices"]["value"] == {
        "price": 200.0, "time": None, "session": ASOF, "from": "close", "previous_close": 190.0,
        "previous_session": "2026-11-03", "session_change": 10.0, "session_move": pytest.approx(200 / 190 - 1)}  # no snapshot here: the latest close


def test_a_bar_of_the_as_of_session_fetched_before_it_ended_is_not_a_close(store):
    names(store, ("NVDA", "CS"))
    put_bars(store, "NVDA", {"2026-11-02": 180.0, "2026-11-03": 190.0, ASOF: 200.0}, fetched="2026-11-04T18:00:00+00:00")
    hold(store, {"ticker": "NVDA", "quantity": 10})
    got = store.get("name:NVDA")["derived"]["prices"]["value"]
    assert (got["price"], got["session"], got["previous_close"]) == (190.0, "2026-11-03", 180.0)


def snapshot(store, ticker, *, trade=None, minute=None, prev=None, status="OK"):
    """A stock's snapshot in Massive's shape (DATA.md), read on the as-of date."""
    t = {"ticker": ticker}
    if trade:
        t["lastTrade"] = {"p": trade[0], "t": int(datetime.fromisoformat(trade[1]).timestamp() * 1e9), "s": 100}
    if minute:
        t["min"] = {"c": minute[0], "t": int(datetime.fromisoformat(minute[1]).timestamp() * 1000)}
    if prev:
        t["prevDay"] = {"c": prev}
    payload = {"status": status, "ticker": t} if status == "OK" else {"status": status, "http_status": 403}
    return store.put_data("snapshot", ticker, payload, {"method": "GET", "url": f"https://api.massive.com/v2/snapshot/"
                                                       f"locale/us/markets/stocks/tickers/{ticker}"},
                          retrieved_at="2026-11-04T19:47:00+00:00", range=(ASOF, ASOF))


def test_as_of_today_each_price_is_the_latest_mark_and_given_dollars_and_weights_are_kept(store):
    """Bars read during the as-of session: the as-of is today, and each price is the stock's last trade with its
    time. Dollars and weights the person gave are kept, their quantities implied at the marks; a quantity given is
    valued at its mark. Returns read completed sessions only."""
    names(store, ("NVDA", "CS"), ("XOM", "CS"), ("AAPL", "CS"))
    during = "2026-11-04T19:47:00+00:00"  # 14:47 in New York, the session not yet complete
    put_bars(store, "NVDA", {"2026-11-03": 200.0, ASOF: 181.0}, fetched=during)
    put_bars(store, "XOM", {"2026-11-03": 110.0, ASOF: 108.0}, fetched=during)
    put_bars(store, "AAPL", {"2026-11-03": 250.0, ASOF: 240.0}, fetched=during)
    snapshot(store, "NVDA", trade=(180.0, "2026-11-04T14:46:57-05:00"), minute=(180.5, "2026-11-04T14:46:00-05:00"))
    snapshot(store, "XOM", minute=(105.0, "2026-11-04T14:46:00-05:00"))  # no trade on this plan: the minute bar
    snapshot(store, "AAPL", status="NOT_AUTHORIZED")  # no mark: the latest completed close
    hold(store, {"ticker": "NVDA", "dollars": 9000.0}, {"ticker": "XOM", "weight": 0.21},
         {"ticker": "AAPL", "quantity": 10}, total=10000.0)
    nvda = store.get("name:NVDA")["derived"]["prices"]["value"]
    assert (nvda["price"], nvda["time"], nvda["from"]) == (180.0, "2026-11-04T14:46:57-05:00", "last trade")
    assert nvda["session_move"] == pytest.approx(180 / 200 - 1)  # against the last completed close
    xom = store.get("name:XOM")["derived"]["prices"]["value"]
    assert (xom["price"], xom["from"]) == (105.0, "latest minute bar")
    aapl = store.get("name:AAPL")["derived"]["prices"]["value"]
    assert (aapl["price"], aapl["session"], aapl["from"]) == (250.0, "2026-11-03", "close")
    amounts = {n["object"]["id"]: {k: v["value"] for k, v in store.get(n["link"])["derived"].items()}
               for n in store.neighbors("portfolio", "holds")}
    assert amounts["name:NVDA"]["quantity"] == pytest.approx(9000 / 180)  # the dollars given, kept
    assert amounts["name:XOM"]["dollars"] == pytest.approx(2100.0) and amounts["name:XOM"]["quantity"] == \
        pytest.approx(2100 / 105)
    assert amounts["name:AAPL"]["dollars"] == pytest.approx(2500.0)  # ten shares at the latest close
    assert store.get("portfolio")["derived"]["value"]["value"] == pytest.approx(9000 + 2100 + 2500)


# ---------------------------------------------------------------- look-through


def test_nested_funds_expand_once_more_with_the_floor_on_the_product(store):
    names(store, ("F1", "ETF"), ("F2", "ETF"), ("F3", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("D", "ADRC"),
          ("E", "CS"))
    put_constituents(store, "F1", [("A", 0.5, "Equity"), ("B", 0.3, "Equity"), ("F2", 0.2, "Equity")])
    put_constituents(store, "F2", [("C", 0.5, "Equity"), ("D", 0.39, "Equity"), ("F3", 0.1, "Equity"),
                                   ("E", 0.01, "Equity")])
    hold(store, {"ticker": "F1", "dollars": 10000.0})
    out = lookthrough(store)
    assert link_dollars(out, "F1", "F2") == pytest.approx(2000.0)
    assert link_dollars(out, "F2", "C") == pytest.approx(1000.0)
    assert link_dollars(out, "F2", "F3") == pytest.approx(200.0)  # a fund at the next depth is booked whole
    assert "looks_through_to:name:F2>name:E" not in out["links"]  # 0.2 * 0.01 is under the floor
    assert out["e"] == pytest.approx({"name:A": 5000.0, "name:B": 3000.0, "name:C": 1000.0, "name:D": 780.0,
                                      "name:F3": 200.0})  # E, under the floor, reaches no name
    assert e(store, "F1") == 0.0 and e(store, "F2") == 0.0  # an expanded fund has e = 0
    assert out["funds"]["F2"]["depth"] == 2 and "F3" not in out["funds"]
    assert store.get("name:C")["because"] == ["looked through"]


def test_a_fund_a_held_funds_derivatives_reach_is_read_as_a_fund_it_holds_and_the_reads_are_made_once_more(store,
                                                                                                           monkeypatch):
    """MATH.md 3.1: a fund that a held fund's derivatives reach, as an option on a fund does, is read as a fund held inside
    it, at the weight a derivative reaches it at, and at every weight where that is not yet known, as an option's delta
    is not until its terms are read (rows.reached): every one of its rows is resolved. What a derivative reaches is
    known only once the rows are read, so the reads are made once more with it, and then stand."""
    import math
    from harness.derive import portfolio
    names(store, ("F1", "ETF"), ("IDX", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"))
    put_constituents(store, "F1", [("A", 0.9, "Equity")])
    put_constituents(store, "IDX", [("B", 0.999, "Equity"), ("C", 0.0001, "Equity")])
    passes = []
    real = portfolio._rows_reads

    def reads(ctx, subject, through):  # F1 holds an option on IDX, whose delta its terms give later
        passes.append(dict(through))
        m = real(ctx, subject, through)
        return {**m, "through": {**m["through"], "IDX": math.inf}}

    monkeypatch.setattr(portfolio, "_rows_reads", reads)
    hold(store, {"ticker": "F1", "dollars": 10000.0})
    rows = store.engine.value("fund_rows", "portfolio")
    assert passes[-2:] == [{}, {"IDX": math.inf}]  # made once more, with IDX, and then stood
    read = store.computation(rows.basis)["inputs"]
    assert {"F1", "IDX"} <= set(read["constituents"])
    assert "C" in read["references"]  # 0.0001 of IDX: resolved, as every row is where the weight is not yet known


def test_a_funds_parts_are_its_rows_exposures_per_dollar_whatever_its_price(store):
    """MATH.md 3.1, 3.2: a fund's parts and rest are its rows' exposures per dollar of it, exactly, on every path that
    reaches it, so the fits that read them (channels.betas) do not change when only a price does."""
    names(store, ("F1", "ETF"), ("F2", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"))
    put_constituents(store, "F1", [("A", 0.3, "Equity"), ("B", 0.0147990112, "Equity"), ("F2", 0.6, "Equity")])
    put_constituents(store, "F2", [("C", 0.7, "Equity"), ("A", 0.3, "Equity")])
    hold(store, {"ticker": "F1", "quantity": 100.0}, {"ticker": "F2", "quantity": 37.0})
    first = lookthrough(store)
    assert first["funds"]["F1"]["parts"] == {"name:A": 0.3, "name:B": 0.0147990112, "name:F2": 0.6}
    assert first["funds"]["F2"]["parts"] == {"name:A": 0.3, "name:C": 0.7}  # held, and inside F1: one plan
    # a price at which dollars over dollars would move the last digits: 0.30000000000000004 for F2's A on its two paths
    put_bars(store, "F1", {"2026-11-03": 100.0, ASOF: 97.13}, fetched="2026-11-05T15:00:00+00:00")
    moved = lookthrough(store)
    assert moved["e"] != first["e"]  # the dollars move with the price
    for fund in ("F1", "F2"):
        assert {k: moved["funds"][fund][k] for k in ("parts", "rest")} == \
            {k: first["funds"][fund][k] for k in ("parts", "rest")}


def test_a_funds_rows_are_a_value_of_their_own_which_a_price_that_moves_leaves_as_it_was(store):
    """MATH.md 3.1: a fund's rows are what it holds, which a source's publication changes and a price does not. They are a
    value of their own (fund_rows), which the look-through names and does not copy, so a price that moves leaves them
    one Computation, and only the look-through's dollars are worked out again."""
    names(store, ("F1", "ETF"), ("F2", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"))
    put_constituents(store, "F1", [("A", 0.3, "Equity"), ("B", 0.1, "Equity"), ("F2", 0.6, "Equity")])
    put_constituents(store, "F2", [("C", 0.7, "Equity"), ("A", 0.3, "Equity")])
    hold(store, {"ticker": "F1", "quantity": 100.0}, {"ticker": "F2", "quantity": 37.0})
    first, rows = lookthrough(store), store.engine.value("fund_rows", "portfolio").basis
    assert "rows" not in first and set(store.computation(rows)["output"]["rows"]) == {"F1", "F2"}
    looked = store.engine.value("lookthrough", "portfolio").basis
    assert store.computation(looked)["inputs"]["fund_rows"]["basis"] == rows  # named, by its Computation
    put_bars(store, "F1", {"2026-11-03": 100.0, ASOF: 97.13}, fetched="2026-11-05T15:00:00+00:00")
    assert lookthrough(store)["e"] != first["e"]  # the dollars move with the price
    assert store.engine.value("fund_rows", "portfolio").basis == rows  # what the funds hold does not


def test_a_name_is_held_through_the_funds_the_portfolio_holds_not_the_funds_inside_them(store):
    """TOOLS.md 9: a contributor is named with the funds it is held through, the portfolio's own holdings whose look-through
    reaches it, largest first. C sits in F2, which sits in F1, and the portfolio holds F1 and, beside it, G holding C
    directly: C is held through F1 and G, never through F2, which the member does not hold."""
    from harness.ledger import _held_through
    names(store, ("F1", "ETF"), ("F2", "ETF"), ("G", "ETF"), ("A", "CS"), ("C", "CS"))
    put_constituents(store, "F1", [("A", 0.8, "Equity"), ("F2", 0.2, "Equity")])
    put_constituents(store, "F2", [("C", 1.0, "Equity")])
    put_constituents(store, "G", [("C", 0.1, "Equity"), ("A", 0.9, "Equity")])
    hold(store, {"ticker": "F1", "dollars": 10000.0}, {"ticker": "G", "dollars": 5000.0})
    out = lookthrough(store)
    assert link_dollars(out, "F2", "C") == pytest.approx(2000.0) and link_dollars(out, "G", "C") == pytest.approx(500.0)
    held = _held_through(store)
    assert held["name:C"] == ["name:F1", "name:G"]  # $2,000 through F1, by way of F2, and $500 through G
    assert held["name:A"] == ["name:F1", "name:G"]  # $8,000 and $4,500


def test_a_null_weight_and_a_sub_floor_weight_reach_no_name(store):
    names(store, ("G", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("D", "CS"))
    put_constituents(store, "G", [("A", 0.6, "Equity"), ("B", None, "Equity"), ("C", 0.002, "Equity"),
                                  ("D", 0.396, "Equity")])
    hold(store, {"ticker": "G", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 600.0, "name:D": 396.0})  # the fund's own response carries the rest
    assert set(out["links"]) == {"looks_through_to:name:G>name:A", "looks_through_to:name:G>name:D"}
    with pytest.raises(StoreError):  # a constituent that reaches no name is no Name of the portfolio
        store.get("name:B")


def test_cash_constituents_reach_no_name(store):
    names(store, ("H", "ETF"), ("A", "CS"))
    put_constituents(store, "H", [("A", 0.95, "Equity"), ("USD", 0.05, "Cash")])
    hold(store, {"ticker": "H", "dollars": 2000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 1900.0})
    assert {g["kind"]: g["weight"] for g in out["funds"]["H"]["holds"]["by_kind"]} == pytest.approx(
        {"equity": 0.95, "cash": 0.05})  # what the fund holds, by its rows' values


def test_a_treasury_bill_and_a_money_market_fund_are_the_funds_cash_whatever_words_its_holdings_give_them(store):
    """ETF Global gives a bill the class and type it gives a bond; the Treasury's bills say which it is, by CUSIP. A
    mutual fund held is cash where the SEC's filings show it to be a money market fund, and otherwise the residual."""
    names(store, ("H", "ETF"), ("A", "CS"))
    put_constituents(store, "H", [("A", 0.80, "Equity")])
    payload = store.data_payload(store.data("constituents", "H", covering=(ASOF, ASOF))["id"])
    held = [("TBILL", 0.10, "Fixed Income", "US GOVERNMENT", "912797WE3"),
            ("T", 0.05, "Fixed Income", "US GOVERNMENT", "91282CLK5"),
            ("GVMXX", 0.03, "Equity", "Open-End Fund", "857492888"), ("VFIAX", 0.02, "Equity", "Open-End Fund", None)]
    payload["results"] += [{"composite_ticker": "H", "constituent_ticker": t, "weight": w, "asset_class": c,
                            "security_type": k, "us_code": cusip, "effective_date": "2026-11-03"}
                           for t, w, c, k, cusip in held]
    store.put_data("constituents", "H", payload, {"method": "GET", "url": "https://api.massive.com/etf-global/v1/"
                   "constituents?composite_ticker=H"}, retrieved_at="2026-11-05T03:00:00+00:00", range=(ASOF, ASOF))
    asked = []

    def bills(subject, covering):
        asked.append(("bills", subject))
        return {"payload": {"status": "OK", "results": [{"cusip": "912797WE3", "term": "4-Week"}]},
                "request": {"method": "GET", "url": "https://www.treasurydirect.gov/TA_WS/securities/search"},
                "retrieved_at": "2026-11-04T19:00:00+00:00", "range": covering}

    def money_market(subject, covering):
        asked.append(("money_market", subject))
        return {"payload": {"status": "OK", "fund": subject, "money_market": subject == "GVMXX"},
                "request": {"method": "GET", "url": "https://www.sec.gov/cgi-bin/browse-edgar"},
                "retrieved_at": "2026-11-04T19:00:00+00:00", "range": covering}

    store.fetchers.update({"bills": bills, "money_market": money_market})
    hold(store, {"ticker": "H", "dollars": 2000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 1600.0})
    assert sorted(asked) == [("bills", "US Treasury"), ("money_market", "GVMXX"), ("money_market", "H"),
                             ("money_market", "VFIAX")]  # the fund held too, which could itself be one
    kinds = {g["kind"]: g["weight"] for g in out["funds"]["H"]["holds"]["by_kind"]}
    assert kinds == pytest.approx({"equity": 0.80, "cash": 0.13, "debt": 0.05, "other": 0.02})


def test_a_money_market_fund_held_directly_is_cash_and_moves_with_nothing(store):
    """MATH.md 3.1: a money market fund is cash; 3.2: cash responds to nothing. Held directly, as in a fund's rows, the
    fund the SEC's filings show to be one stands as cash, with no series of its own fitted, and is not looked through."""
    names(store, ("MMF", "MF"), ("SPY", "ETF"))
    put_constituents(store, "SPY", [("SPY", 1.0, "Equity")])

    def money_market(subject, covering):
        return {"payload": {"status": "OK", "fund": subject, "money_market": subject == "MMF"},
                "request": {"method": "GET", "url": "https://www.sec.gov/cgi-bin/browse-edgar"},
                "retrieved_at": FETCHED, "range": covering}

    def nport(subject, covering):
        raise AssertionError("a money market fund is not looked through")
    store.fetchers.update({"money_market": money_market, "nport": nport})
    hold(store, {"ticker": "MMF", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:MMF": 1000.0})
    assert out["kinds"]["name:MMF"] == {"kind": "cash", "beta": None}
    assert out["owned"]["kind:cash"]["dollars"] == pytest.approx(1000.0)


def test_levered_both_ways_book_each_rows_exposure_with_no_financing_line(store):
    names(store, ("L", "ETF"), ("S", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "L", [("A", 0.8, "Equity"), ("B", 0.5, "Equity")])  # 130 long: financed
    put_constituents(store, "S", [("A", 0.9, "Equity"), ("B", -0.2, "Equity")])  # long and short
    hold(store, {"ticker": "L", "dollars": 1000.0}, {"ticker": "S", "dollars": 1000.0})
    out = lookthrough(store)
    assert link_dollars(out, "L", "B") == pytest.approx(500.0)  # 130 long: exposures need not sum to value
    assert link_dollars(out, "S", "B") == pytest.approx(-200.0)  # short: negative exposure
    assert out["e"] == pytest.approx({"name:A": 800.0 + 900.0, "name:B": 500.0 - 200.0})  # and no financing line


def test_a_levered_and_an_inverse_fund_are_looked_through_at_their_exposures(store):
    names(store, ("X2", "ETF"), ("INV", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "X2", [("A", 1.2, "Equity"), ("B", 0.9, "Equity")])
    put_constituents(store, "INV", [("A", -1.0, "Equity")])
    hold(store, {"ticker": "X2", "dollars": 1000.0}, {"ticker": "INV", "dollars": 500.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 1200.0 - 500.0, "name:B": 900.0})
    assert out["funds"]["X2"]["expanded"] and out["funds"]["INV"]["expanded"]
    assert link_dollars(out, "INV", "A") == pytest.approx(-500.0)


def test_a_fund_that_maps_nothing_and_one_without_constituents_are_booked_whole(store):
    names(store, ("BND", "ETF"), ("GOLDT", "ETF"), ("NOTE1", None))
    put_constituents(store, "BND", [(None, 0.6, "Fixed Income"), ("NOTE1", 0.35, "Fixed Income"),
                                    ("USD", 0.05, "Cash")])
    put_constituents(store, "GOLDT", [], status="NOT_AUTHORIZED")
    store.put_data("nport", "GOLDT", {"status": "NOT_FOUND", "fund": "GOLDT", "error": "GOLDT filed no Form N-PORT"},
                   {"method": "GET", "url": "https://www.sec.gov/cgi-bin/browse-edgar"}, retrieved_at=FETCHED,
                   range=(ASOF, ASOF))  # a grantor trust files none
    hold(store, {"ticker": "BND", "dollars": 1000.0}, {"ticker": "GOLDT", "dollars": 500.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:BND": 1000.0, "name:GOLDT": 500.0})
    assert "US-listed share or fund" in out["funds"]["BND"]["reason"]
    assert out["funds"]["GOLDT"]["reason"] == f"no holdings are published for it on or before {ASOF}"


def test_a_mutual_fund_is_read_from_its_form_n_port_report_whole_and_its_holdings_source_is_not_asked(store):
    """MATH.md 3.1: a mutual fund is a fund, and ETF Global, which gives exchange-traded funds' holdings, gives none of
    its; its rows are its latest Form N-PORT report's, each as of the report's date (harness.venues.funds)."""
    names(store, ("VMF", "MF"))
    asked = []

    def constituents(subject, covering):
        asked.append(subject)
        raise AssertionError("a mutual fund's holdings are not ETF Global's to give")
    store.fetchers["constituents"] = constituents
    rows = [{"constituent_name": "A Corp", "asset_category": "EC", "issuer_category": "CORP", "weight": 0.9,
             "currency": "USD", "effective_date": "2026-09-30"},
            {"constituent_name": "A money market fund", "asset_category": "STIV", "issuer_category": "RF",
             "weight": 0.1, "currency": "USD", "effective_date": "2026-09-30"}]
    store.put_data("nport", "VMF", {"status": "OK", "fund": "VMF", "effective_date": "2026-09-30", "net_assets": 1e9,
                                   "results": rows},
                   {"method": "GET", "url": "https://www.sec.gov/cgi-bin/browse-edgar"}, retrieved_at=FETCHED,
                   range=(ASOF, ASOF))
    hold(store, {"ticker": "VMF", "dollars": 1000.0})
    out = lookthrough(store)
    assert asked == []
    held = out["funds"]["VMF"]["holds"]
    assert held["holdings_asof"] == "2026-09-30"
    assert {g["kind"]: g["weight"] for g in held["by_kind"]} == pytest.approx({"equity": 0.9, "cash": 0.1})


@pytest.mark.parametrize("held, filed, read", [("2025-08-15", "2026-06-30", "2026-06-30"),
                                               ("2026-11-03", "2026-06-30", "2026-11-03")])
def test_a_funds_holdings_are_those_published_as_of_the_latest_date_whichever_source_published_them(store, held, filed,
                                                                                                read):
    """MATH.md 3.1: a fund's holdings are those published for it as of the latest date on or before the as-of. A holdings
    source that no longer reads a fund keeps its last record, which its Form N-PORT report, of a later date, replaces;
    a record later than its report stands."""
    names(store, ("F", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "F", [("A", 1.0, "Equity")], effective=held)
    store.put_data("nport", "F", {"status": "OK", "fund": "F", "effective_date": filed, "net_assets": 1e9, "results": [
        {"constituent_ticker": "B", "figi": "FIGIB", "asset_category": "EC", "issuer_category": "CORP", "weight": 1.0,
         "currency": "USD", "effective_date": filed}]},
                   {"method": "GET", "url": "https://www.sec.gov/cgi-bin/browse-edgar"}, retrieved_at=FETCHED,
                   range=(ASOF, ASOF))
    hold(store, {"ticker": "F", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["funds"]["F"]["holds"]["holdings_asof"] == read
    assert out["e"] == pytest.approx({"name:B" if read == filed else "name:A": 1000.0})


def test_a_mutual_fund_whose_fund_an_exchange_also_lists_reads_that_classs_holdings_where_they_are_later(store):
    """MATH.md 3.1: a fund's holdings are the latest published for it. A mutual fund's share class and its fund's
    exchange-traded class hold one portfolio, whose holdings the exchange-traded class publishes each day: read beside
    the fund's report, the later stands (read live: VTSAX's report of June 30, VTI's holdings of September 30)."""
    store.put_data("ticker_ref", "MFX", {"status": "OK", "results": {"ticker": "MFX", "type": "MF", "market": "funds",
                                                                     "series": "S1", "exchange_traded": "ETFX"}},
                   {"method": "GET", "url": "chart"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    names(store, ("ETFX", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "ETFX", [("A", 1.0, "Equity")], effective="2026-11-03")
    store.put_data("nport", "MFX", {"status": "OK", "fund": "MFX", "effective_date": "2026-06-30", "net_assets": 1e9,
                                    "results": [{"constituent_ticker": "B", "figi": "FIGIB", "asset_category": "EC",
                                                 "issuer_category": "CORP", "weight": 1.0, "currency": "USD",
                                                 "effective_date": "2026-06-30"}]},
                   {"method": "GET", "url": "https://www.sec.gov/cgi-bin/browse-edgar"}, retrieved_at=FETCHED,
                   range=(ASOF, ASOF))
    hold(store, {"ticker": "MFX", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 1000.0})
    assert out["funds"]["MFX"]["holds"]["holdings_asof"] == "2026-11-03"


def test_a_fund_its_holdings_source_no_longer_reads_has_none_published_and_is_held_whole_or_read_from_its_report(store):
    """MATH.md 3.1: a fund's holdings are those published for it, as of the latest date on or before the as-of, by a source
    that still publishes them; a fund no source still publishes holdings for is held whole. ETF Global processes each
    fund it reads every day: a fund it last processed before the session before its own latest processing is one it no
    longer reads (read live: IAU's and SLV's last processing, 2019-07-03, with two banks among the rows)."""
    names(store, ("CUR", "ETF"), ("OLD", "ETF"), ("FILED", "ETF"), ("A", "CS"), ("B", "CS"), ("BANK", "CS"))
    for fund, rows, processed in (("CUR", [("A", 1.0, "Equity")], "2026-11-03"),
                                  ("OLD", [("BANK", 0.11, "Equity")], "2019-07-03"),
                                  ("FILED", [("BANK", 0.11, "Equity")], "2019-07-03")):
        effective = processed
        put_constituents(store, fund, rows, effective=effective)
        ref = store._find_data("constituents", fund, (ASOF, ASOF))
        payload = {**store.data_payload(ref["id"]), "processed_date": processed}
        store.put_data("constituents", fund, payload, {"method": "GET", "url": "etf"}, retrieved_at=FETCHED,
                       range=(ASOF, ASOF))
    store.put_data("holdings_processed", "ETF Global", {"status": "OK", "source": "ETF Global",
                                                        "processed_date": "2026-11-03"},
                   {"method": "GET", "url": "etf"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    for fund, payload in (("OLD", {"status": "NOT_FOUND", "fund": "OLD", "error": "files none"}),
                          ("FILED", {"status": "OK", "fund": "FILED", "effective_date": "2026-06-30", "net_assets": 1e9,
                                     "results": [{"constituent_ticker": "B", "figi": "FIGIB", "asset_category": "EC",
                                                  "issuer_category": "CORP", "weight": 1.0, "currency": "USD",
                                                  "effective_date": "2026-06-30"}]})):
        store.put_data("nport", fund, payload, {"method": "GET", "url": "nport"}, retrieved_at=FETCHED,
                       range=(ASOF, ASOF))
    store.put_data("nport", "CUR", {"status": "NOT_FOUND", "fund": "CUR", "error": "files none"},
                   {"method": "GET", "url": "nport"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    hold(store, {"ticker": "CUR", "dollars": 1000.0}, {"ticker": "OLD", "dollars": 1000.0},
         {"ticker": "FILED", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 1000.0, "name:OLD": 1000.0, "name:B": 1000.0})  # no bank anywhere
    assert out["funds"]["OLD"]["reason"] == "no source still publishes its holdings: the last were published on 2019-07-03"


def test_a_holding_whose_price_cannot_be_read_leaves_the_portfolio_unread_with_its_cause(store):
    """MATH.md 3.1; TOOLS.md 5: a holding given in dollars is the shares it bought then, worth what its price makes them
    now, so a holding with no price has no value to carry, and the portfolio is not read until it has one."""
    names(store, ("NOPX", "CS"))
    put_bars(store, "NOPX", {})
    hold(store, {"ticker": "NOPX", "dollars": 1000.0}, {"cash": 1000.0})
    v = store.engine.value("holdings", "portfolio")
    assert v.output is None and "NOPX" in v.cause


def test_a_name_held_directly_and_through_a_fund_sums_both(store):
    names(store, ("F", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "F", [("A", 0.5, "Equity"), ("B", 0.5, "Equity")])
    hold(store, {"ticker": "A", "dollars": 1000.0}, {"ticker": "F", "dollars": 2000.0})
    assert e(store, "A") == pytest.approx(2000.0)
    assert store.get("name:A")["because"] == ["held", "looked through"]


# ---------------------------------------------------------------- the channels, the betas, and g


def channel_series(store) -> list[dict]:
    """Each channel's orthogonalization, with its own part on each session, x_t = L u_t, from the factors."""
    channels = sorted(store.query("Factor"), key=lambda c: c["stated"]["order"])
    factors = store.engine.value("factors", "market").output
    own = np.linalg.solve(np.array(factors["lower"]), np.array(factors["x"], dtype=float).T).T
    days = factors["sessions"][1:]
    return [{**store.engine.value("orthogonalize", c["id"]).output,
             "series": {d: float(v) for d, v in zip(days, own[:, k])}} for k, c in enumerate(channels)]


def test_the_orthogonalization_is_the_channels_long_run_covariance_factored_in_channel_order(store):
    """Each channel's own part is uncorrelated with the earlier ones' in the channels' long-run covariance, Omega = L
    diag(variance) L': channel 1 is its proxy's return, and each later one its proxy's less its coefficients on the
    earlier parts, session by session."""
    load_channels(store)
    put_market(store, np.random.default_rng(1))
    hold(store, {"cash": 1.0})
    found = channel_series(store)
    days = sorted(found[0]["series"])
    assert len(days) == WINDOW and found[-1]["proxy"] == "IBIT"
    ids = [c["channel"] for c in found]
    lower = np.eye(len(found))
    for k, c in enumerate(found):
        for j, cid in enumerate(ids[:k]):
            lower[k, j] = c["coefficients"][cid]
        assert (k == 0) == (not c["coefficients"])  # each later channel on the earlier ones
    omega = np.array(store.engine.value("factors", "market").output["omega"])
    assert lower @ np.diag([c["variance"] for c in found]) @ lower.T == pytest.approx(omega, abs=1e-15)
    own = np.array([[c["series"][d] for c in found] for d in days])
    raw = np.array(store.engine.value("factors", "market").output["x"])
    assert own @ lower.T == pytest.approx(raw, abs=1e-12)


def test_betas_recover_known_coefficients_and_g_is_the_sum_of_e_times_b(store):
    load_channels(store)
    rng = np.random.default_rng(2)
    put_market(store, rng)
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    hold(store, {"cash": 1.0})
    found = channel_series(store)
    days = sorted(found[0]["series"])
    ids = [c["channel"] for c in found]
    truth = {"NVDA": [1.6, 0.2, -0.4, 0.1, 0.0, 1.2, 0.3, -0.1, 0.5, 0.05],
             "XOM": [0.8, -0.1, 0.2, -0.3, 0.9, 0.0, 0.1, 0.2, 0.3, 0.0]}
    for ticker, b in truth.items():
        r = {d: 0.0002 + sum(bj * c["series"].get(d, 0.0) for bj, c in zip(b, found)) + float(rng.normal(0, 1e-6))
             for d in days}
        put_bars(store, ticker, closes_from(r))
    store.remove("portfolio")
    hold(store, {"ticker": "NVDA", "dollars": 15000.0}, {"ticker": "XOM", "dollars": 10000.0},
         {"cash": 5000.0})
    for ticker, b in truth.items():
        got = store.get(f"name:{ticker}")["derived"]["betas"]["value"]
        assert [got[c] for c in ids] == pytest.approx(b, abs=1e-3)
    [cash] = [link["id"] for link in store.links_of("portfolio", "holds") if link["to"] == "cash:USD"]
    assert store.engine.value("g", "portfolio").output["by_holding"][cash]["b"] == dict.fromkeys(ids, 0.0)
    g = store.get("portfolio")["derived"]["dollar_beta"]["value"]
    expected = {c: sum(e(store, t) * store.get(f"name:{t}")["derived"]["betas"]["value"][c] for t in ("NVDA", "XOM"))
                for c in ids}
    assert g == pytest.approx(expected)
    loads = store.get(f"beta_to:name:NVDA>{ids[5]}")
    assert loads["derived"]["beta"]["value"] == pytest.approx(1.2, abs=1e-3)


def test_a_name_with_no_returns_takes_its_priors_centre_and_one_with_a_year_is_fitted_on_it(store):
    """With no population read here, a name's prior is the market's sensitivity: with no return of its own, its
    response is that centre, and with a year of returns, its own."""
    load_channels(store, ("TLT",))
    rng = np.random.default_rng(3)
    put_market(store, rng)
    names(store, ("NEW", "CS"), ("YEAR", "CS"))
    put_bars(store, "NEW", {ASOF: 10.0})  # listed today: no return
    put_bars(store, "YEAR", closes_from(random_returns(rng, days=SESSIONS[-250:])))  # 250 returns
    hold(store, {"ticker": "NEW", "dollars": 1.0}, {"ticker": "YEAR", "dollars": 1.0})
    ids = [c["channel"] for c in channel_series(store)]
    assert list(store.get("name:NEW")["derived"]["betas"]["value"].values()) == pytest.approx([1.0, 0.0],
                                                                                      abs=1e-12)
    fits = store.engine.value("betas", "portfolio").output["fits"]
    assert fits["name:NEW"]["n"] == 0 and fits["name:NEW"]["prior"]["from"] == "market"
    assert fits["name:YEAR"]["n"] == 250 and len(ids) == 2


def test_a_crypto_pair_set_as_a_proxy_is_read_at_16_00_new_york(store):
    load_channels(store, ("IBIT",))
    rng = np.random.default_rng(4)
    put_market(store, rng)
    prices = closes_from(random_returns(rng, 0.03))
    put_hourly(store, "X:BTCUSD", prices)
    hold(store, {"cash": 1.0})
    crypto = next(c for c in store.query("Factor") if c["stated"]["proxy"] == "IBIT")
    store.edit(crypto["id"], "proxy", "X:BTCUSD")
    assert store.engine.value("orthogonalize", crypto["id"]).output["proxy"] == "X:BTCUSD"
    assert store.get(crypto["id"])["derived"]["orthogonalization"]["value"]["n"] == WINDOW
    btc = store.get("metric:price:X:BTCUSD")["derived"]
    assert btc["level"]["value"] == pytest.approx(prices[ASOF] * 1.03)  # as of today, its latest hourly bar
    series = next(c for c in channel_series(store) if c["channel"] == crypto["id"])["series"]
    assert len(series) == WINDOW  # its returns read the 16:00 price of each completed day
    assert btc["resolution_source"]["value"] == "Coinbase Exchange"


def test_the_proxy_metric_is_the_proxys_price(store):
    load_channels(store, ("IBIT",))
    put_market(store, np.random.default_rng(5))
    hold(store, {"cash": 1.0})
    spy = store.get("metric:price:SPY")["derived"]
    assert spy["level"]["value"] > 0 and spy["asof"]["value"] == ASOF and spy["kind"]["value"] == "price"
    ibit = store.get("metric:price:IBIT")["derived"]
    assert "resolution_source" not in ibit and ibit["name"]["value"] == "IBIT's price"  # a listing's own price


def test_an_eleventh_channel_recomputes_every_beta_and_its_removal_returns_every_value(store):
    load_channels(store)
    rng = np.random.default_rng(6)
    put_market(store, rng)
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    for ticker in ("NVDA", "XOM", "BDRY"):
        put_bars(store, ticker, closes_from(random_returns(rng)))
    hold(store, {"ticker": "NVDA", "dollars": 15000.0}, {"ticker": "XOM", "dollars": 10000.0},
         {"cash": 5000.0})
    names(store, *((t, "ETF") for t in PROXIES + ["BDRY"]))  # what each proxy is: its group, here none read
    calls = []
    store.fetchers.update({kind: (lambda subject, covering, kind=kind: calls.append((kind, subject)))
                           for kind in ("bars", "hourly", "dividends", "ticker_ref", "constituents")})

    def snapshot():
        ids = ["portfolio", "name:NVDA", "name:XOM"] + [c["id"] for c in store.query("Factor")]
        return {i: store.get(i)["derived"] for i in ids}

    before = snapshot()
    rows = store.db.execute("SELECT count(*) FROM computation").fetchone()[0]
    ship = store.add("Factor", {"name": "dry bulk shipping", "proxy": "BDRY", "order": 11})
    during = snapshot()
    for name in ("name:NVDA", "name:XOM"):
        assert during[name]["betas"]["basis"] != before[name]["betas"]["basis"]
        assert ship["id"] in during[name]["betas"]["value"]
    assert during["portfolio"]["dollar_beta"]["basis"] != before["portfolio"]["dollar_beta"]["basis"]
    added = store.db.execute("SELECT count(*) FROM computation").fetchone()[0]
    store.remove(ship["id"])
    assert masked(snapshot()) == masked(before)  # every value, computed again from the data kept
    assert added > rows
    assert calls == []


def test_a_row_reported_twice_counts_once_and_rows_sharing_a_ticker_stay_distinct(store):
    names(store, ("DUP", "ETF"), ("A", "CS"), ("B", "CS"))
    rows = [("A", 0.5, "Equity"), ("A", 0.5, "Equity"), ("B", 0.3, "Equity"), ("B", 0.2, "Equity")]
    put_constituents(store, "DUP", rows)  # A's row twice; B's two rows differ in weight
    hold(store, {"ticker": "DUP", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["funds"]["DUP"]["holds"]["weights_sum"] == pytest.approx(1.0)  # not 1.5
    assert out["e"] == pytest.approx({"name:A": 500.0, "name:B": 500.0})


# ---------------------------------------------------------------- the Equity rule


def references_read(store) -> set[str]:
    """The tickers whose reference the look-through read."""
    basis = store.engine.value("lookthrough", "portfolio").basis
    inputs = json.loads(store.db.execute("SELECT inputs FROM computation WHERE id = ?", (basis,)).fetchone()[0])
    return set(inputs["references"])


def test_a_bond_fund_whose_tickers_collide_with_a_listed_company_maps_nothing_and_is_booked_whole(store):
    names(store, ("TLT", "ETF"), ("WIT", "ADRC"))  # WIT is Wipro's ADR, and the when-issued Treasuries' ticker
    put_constituents(store, "TLT", [("WIT", 0.031, "Fixed Income"), ("WIT", 0.027, "Fixed Income"),
                                    ("T 4.75 05/15/55", 0.937, "Fixed Income"), ("USD", 0.005, "Cash")])
    hold(store, {"ticker": "TLT", "dollars": 10000.0})
    out = lookthrough(store)
    assert out["e"] == {"name:TLT": 10000.0} and out["links"] == {}
    assert {k: v for k, v in out["funds"]["TLT"].items() if k != "holds"} == {"expanded": False,
                                   "reason": "none of its holdings is a US-listed share or fund",
                                   "rest": {"shares": 0.0, "undetermined": pytest.approx(0.995),
                                            "currencies": {}, "underlyings": {}}}  # its bonds, for its own history
    assert out["kinds"]["name:TLT"] == {"kind": "fund", "beta": "TLT"}  # with its own beta
    assert references_read(store) == {"TLT"}  # a bond's ticker is not looked up
    with pytest.raises(StoreError):
        store.get("name:WIT")


def test_a_coin_fund_whose_ticker_collides_with_a_listed_fund_is_booked_whole(store):
    names(store, ("IBIT", "ETF"), ("BTC", "ETV"))  # BTC is the Grayscale Bitcoin Mini Trust, and bitcoin's ticker
    put_constituents(store, "IBIT", [("BTC", 1.0, "Alternative")])
    put_constituents(store, "BTC", [("BTC", 1.0, "Alternative")])
    hold(store, {"ticker": "IBIT", "dollars": 5000.0})
    out = lookthrough(store)
    assert out["e"] == {"name:IBIT": 5000.0} and out["links"] == {}
    assert set(out["funds"]) == {"IBIT"} and "US-listed share or fund" in out["funds"]["IBIT"]["reason"]
    assert references_read(store) == {"IBIT"}


def test_equity_rows_resolve_and_other_classes_reach_no_name(store):
    names(store, ("MIX", "ETF"), ("A", "CS"), ("D", "ADRC"), ("WIT", "ADRC"), ("BTC", "ETV"))
    put_constituents(store, "MIX", [("A", 0.5, "Equity"), ("D", 0.2, "Equity"), ("WIT", 0.1, "Fixed Income"),
                                    ("BTC", 0.15, "Alternative"), ("NQZ6", 0.03, None), ("USD", 0.02, "Cash")])
    hold(store, {"ticker": "MIX", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 500.0, "name:D": 200.0})
    assert references_read(store) == {"MIX", "A", "D"}


def test_an_equity_classed_fund_constituent_still_expands_at_the_next_depth(store):
    names(store, ("TOP", "ETF"), ("SUB", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("WIT", "ADRC"))
    put_constituents(store, "TOP", [("A", 0.6, "Equity"), ("SUB", 0.4, "Equity")])
    put_constituents(store, "SUB", [("B", 0.5, "Equity"), ("C", 0.3, "Equity"), ("WIT", 0.2, "Fixed Income")])
    hold(store, {"ticker": "TOP", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["funds"]["SUB"]["depth"] == 2 and out["funds"]["SUB"]["expanded"] is True
    assert out["e"] == pytest.approx({"name:A": 600.0, "name:B": 200.0, "name:C": 120.0})
    assert e(store, "SUB") == 0.0


# ---------------------------------------------------------------- total returns


def test_a_dividend_on_its_ex_date_is_added_back_to_that_sessions_return():
    sessions = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]
    closes = {"2026-10-28": 100.0, "2026-10-29": 101.0, "2026-10-30": 99.5, "2026-11-02": 99.0, "2026-11-03": 99.2}
    paid = {"2026-10-30": 1.2, "2026-11-01": 0.3}  # 2026-11-01 is a Sunday: it goes ex on Monday
    got = series.returns(closes, sessions, paid)
    assert got == pytest.approx({"2026-10-29": 101.0 / 100.0 - 1, "2026-10-30": (99.5 + 1.2) / 101.0 - 1,
                                 "2026-11-02": (99.0 + 0.3) / 99.5 - 1, "2026-11-03": 99.2 / 99.0 - 1}, abs=1e-15)
    # A dividend that went ex on or before the window's first session has no return in it.
    assert series.returns(closes, sessions, {"2026-10-28": 5.0, "2026-10-01": 5.0}) == series.returns(closes, sessions)


def test_a_ticker_with_no_dividends_has_its_close_to_close_returns():
    sessions = ["2026-10-29", "2026-10-30", "2026-11-02"]
    closes = {"2026-10-29": 50.0, "2026-10-30": 51.0, "2026-11-02": 50.49}
    plain = {"2026-10-30": 51.0 / 50.0 - 1, "2026-11-02": 50.49 / 51.0 - 1}
    assert series.returns(closes, sessions) == series.returns(closes, sessions, {}) == pytest.approx(plain, abs=1e-15)
    assert series.dividends({"status": "OK", "results": []}) == {}


def test_massives_dividends_read_by_ex_date_split_adjusted_one_record_once():
    payload = {"status": "OK", "results": [
        {"id": "E1", "ex_dividend_date": "2026-10-01", "cash_amount": 1.0, "split_adjusted_cash_amount": 0.25},
        {"id": "E1", "ex_dividend_date": "2026-10-01", "cash_amount": 1.0, "split_adjusted_cash_amount": 0.25},
        {"id": "E2", "ex_dividend_date": "2026-10-01", "cash_amount": 0.4, "split_adjusted_cash_amount": 0.1},
        {"id": "E3", "ex_dividend_date": "2026-11-02", "cash_amount": 0.3, "split_adjusted_cash_amount": 0.3}]}
    assert series.dividends(payload) == pytest.approx({"2026-10-01": 0.35, "2026-11-02": 0.3})


def test_the_market_channel_is_the_proxys_total_return(store):
    load_channels(store, ())
    rng = np.random.default_rng(7)
    put_market(store, rng)
    spy = closes_from(random_returns(rng))
    ex = SESSIONS[-3]
    put_bars(store, "SPY", spy, dividends={ex: 1.75})
    hold(store, {"cash": 1.0})
    mkt = channel_series(store)[0]["series"]
    before = SESSIONS[SESSIONS.index(ex) - 1]
    assert mkt[ex] == pytest.approx((spy[ex] + 1.75) / spy[before] - 1, abs=1e-15)
    assert mkt[SESSIONS[-1]] == pytest.approx(spy[SESSIONS[-1]] / spy[SESSIONS[-2]] - 1, abs=1e-15)


def orthogonal(total: list[np.ndarray]) -> list[np.ndarray]:
    """The orthogonalization done by hand: channel 1 is its proxy's return, channel k the residual of proxy
    k on an intercept and channels 1 to k-1."""
    out = [total[0]]
    for y in total[1:]:
        design = np.column_stack([np.ones(len(y))] + out)
        coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
        out.append(y - design @ coefficients)
    return out


def paying(rng, total: dict[str, float], every: int, rate: float) -> tuple[dict[str, float], dict[str, float]]:
    """Closes and dividends whose total return is `total`: every `every` sessions a dividend
    of `rate` of the previous close goes ex, and the close falls by it."""
    days = sorted(total)
    level = 100.0
    closes, dividends = {SESSIONS[SESSIONS.index(days[0]) - 1]: level}, {}
    for i, d in enumerate(days):
        dividend = rate * level if i % every == every - 1 else 0.0
        level = level * (1.0 + total[d]) - dividend
        closes[d] = level
        if dividend:
            dividends[d] = dividend
    return closes, dividends


def test_betas_are_recovered_when_proxies_and_names_pay_dividends(store):
    """Every return is a total return: a proxy's and a name's dividends are added back on their ex-dates, so the
    channels' returns are the proxies' total returns and a name's response is recovered on them."""
    load_channels(store)
    rng = np.random.default_rng(8)
    days = SESSIONS[-WINDOW:]
    sd = {"SPY": 0.01, "SHY": 0.001, "TLT": 0.008, "UUP": 0.004, "USO": 0.02, "EFA": 0.009, "EEM": 0.012,
          "GLD": 0.009, "HYG": 0.004, "IBIT": 0.03}
    pays = {"SPY": (63, 0.004), "SHY": (21, 0.003), "TLT": (21, 0.003), "HYG": (21, 0.005), "EFA": (126, 0.012), "EEM": (126, 0.01)}
    total, raw, priced = [], [], {}
    for ticker in PROXIES:
        own = rng.normal(0.0, sd[ticker], len(days))
        mixed = own + sum(0.3 * r for r in raw[-2:]) if raw else own
        raw.append(mixed)
        total.append(mixed)
        returns = dict(zip(days, (float(x) for x in mixed)))
        if ticker in pays:
            closes, dividends = paying(rng, returns, *pays[ticker])
            put_bars(store, ticker, closes, dividends=dividends)
            priced[ticker] = closes
        else:
            put_bars(store, ticker, closes_from(returns))
    hold(store, {"cash": 1.0})
    found = channel_series(store)
    factors = store.engine.value("factors", "market").output
    x = np.array(factors["x"])
    assert x == pytest.approx(np.column_stack(total), abs=1e-12)  # each proxy's total return, session by session
    assert [found[0]["series"][d] for d in days] == pytest.approx(list(total[0]), abs=1e-12)
    truth = {"NVDA": [1.6, 0.2, -0.4, 0.1, 0.0, 1.2, 0.3, -0.1, 0.5, 0.05],
             "XOM": [0.8, -0.1, 0.2, -0.3, 0.9, 0.0, 0.1, 0.2, 0.3, 0.0]}
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    for ticker, b in truth.items():
        r = 0.0002 + np.array([sum(bj * c["series"][d] for bj, c in zip(b, found)) for d in days]) \
            + rng.normal(0, 1e-6, len(days))
        returns = dict(zip(days, (float(v) for v in r)))
        if ticker == "XOM":  # a name pays too
            closes, dividends = paying(rng, returns, 63, 0.009)
            put_bars(store, ticker, closes, dividends=dividends)
        else:
            put_bars(store, ticker, closes_from(returns))
    store.remove("portfolio")
    hold(store, {"ticker": "NVDA", "dollars": 15000.0}, {"ticker": "XOM", "dollars": 10000.0})
    ids = [c["channel"] for c in found]
    for ticker, b in truth.items():
        got = store.get(f"name:{ticker}")["derived"]["betas"]["value"]
        assert [got[c] for c in ids] == pytest.approx(b, abs=1e-3)
    # Read as price returns, SHY's payout is a fall of three tenths of a percent each month, three times its daily
    # move: the channel's return is its total return, not that.
    sessions = SESSIONS[-(WINDOW + 1):]
    shy_price = series.returns(priced["SHY"], sessions)
    assert max(abs(x[i, 1] - shy_price[d]) for i, d in enumerate(days)) > 2e-3


def test_a_windows_move_is_what_holding_earned_less_the_income_it_accrued_through_an_ex_date():
    """MATH.md 3.2: a window's move is what holding the proxy earned over it, less the income it accrued over it. A
    monthly payer of 0.30 going ex on Dec 31, Jan 31 and Mar 2, each accruing evenly over the days since the one before
    it: by Jan 30 it has accrued 30 / 31 of Jan 31's, and by Feb 3, 3 / 30 of Mar 2's. Its clean price stands at 100,
    100 and 101, so its closes are 100 + 0.30 * 30 / 31, 100.00 on the ex-date, and 101.03; the window moves by
    101 / 100 - 1 = 1%. The closes' ratio reads the distribution as a fall. A special distribution of 0.50 going ex on
    Feb 3 is no income: it stays in what holding earned, and the move is the same 1%."""
    paid = {"2024-12-31": 0.30, "2025-01-31": 0.30, "2025-03-02": 0.30}
    accrual = series.Accrual(paid, {d: 12 for d in paid})
    assert accrual("2025-01-30") == pytest.approx(0.30 + 0.30 * 30 / 31)
    assert accrual("2025-01-31") == pytest.approx(0.60)
    assert accrual("2025-02-03") == pytest.approx(0.60 + 0.30 * 3 / 30)
    closes = {"2025-01-30": 100.0 + 0.30 * 30 / 31, "2025-01-31": 100.00, "2025-02-03": 101.03}
    assert series.window_move(closes, "2025-01-30", "2025-02-03", paid, accrual) == pytest.approx(0.01, abs=1e-12)
    assert series.window_move(closes, "2025-01-30", "2025-02-03") == pytest.approx(
        closes["2025-02-03"] / closes["2025-01-30"] - 1)
    special = {**closes, "2025-02-03": 101.03 - 0.50}
    assert series.window_move(special, "2025-01-30", "2025-02-03", {**paid, "2025-02-03": 0.50},
                              accrual) == pytest.approx(0.01, abs=1e-12)
    assert series.window_move(closes, "2025-03-31", "2025-02-03", paid, accrual) is None  # no close at the start


def test_income_accrues_since_the_one_before_it_and_the_first_of_the_record_over_its_schedules_period():
    """series.Accrual: each distribution accrues evenly over the days since the one before it, as a fund's
    price carries its income from one ex-date to the next. A fund going ex on Nov 1, Dec 2 and Dec 18 and next on Feb 3
    accrues Dec 18's over the 16 days from Dec 2 and Feb 3's over the 47 days from Dec 18: the turn of the year accrues
    income, and Nov 18 to Dec 2 accrues Dec 2's alone, where a period of the schedule's length ending on each ex-date
    would accrue two distributions there and none over Dec 18 to Jan 3. The record is the distributions going ex on or
    after where the read begins, `since`, and the first of them accrues over the period its schedule gives, 365 / 12
    days for a monthly one, so a payload that reaches further back gives the same; read whole, the earlier one would
    shorten that period to the days since it. The days after the latest accrue at its rate for one more period of it
    and no further. Where no schedule is stated, the first accrues over the days to the next. A record with none
    accrues nothing."""
    uneven = {"2024-11-01": 0.10, "2024-12-02": 0.10, "2024-12-18": 0.06, "2025-02-03": 0.15}
    a = series.Accrual(uneven, {d: 12 for d in uneven})
    assert a("2025-01-03") - a("2024-12-18") == pytest.approx(0.15 * 16 / 47)
    assert a("2024-12-02") - a("2024-11-18") == pytest.approx(0.10 * 14 / 31)
    assert a("2024-12-10") == pytest.approx(0.20 + 0.06 * 8 / 16)
    period = 365 / 12
    alone = series.Accrual({"2025-03-01": 0.30}, {"2025-03-01": 12})
    assert alone("2025-02-14") == pytest.approx(0.30 * (1 - 15 / period))
    assert alone("2025-01-01") == 0.0  # before its period
    assert alone("2025-03-11") == pytest.approx(0.30 + 0.30 * 10 / period)
    assert alone("2025-06-01") == pytest.approx(0.60)  # one period after it, and no more
    later = {"2025-01-31": 0.30, "2025-03-02": 0.45}
    earlier = {"2024-11-29": 0.25, "2024-12-30": 0.40, **later}  # a payload reaching further back
    short = series.Accrual(later, {d: 12 for d in later}, "2025-01-01")
    long = series.Accrual(earlier, {d: 12 for d in earlier}, "2025-01-01")
    for x, y in (("2025-01-15", "2025-02-12"), ("2025-02-20", "2025-03-20")):
        assert long(y) - long(x) == pytest.approx(short(y) - short(x))
    assert short("2025-01-30") - short("2025-01-15") == pytest.approx(0.30 * 15 / period)
    whole = series.Accrual(earlier, {d: 12 for d in earlier})
    assert whole("2025-01-30") - whole("2025-01-15") == pytest.approx(0.30 * 15 / 32)  # since Dec 30
    unscheduled = series.Accrual({"2025-01-31": 0.30, "2025-03-02": 0.45})  # no schedule stated, as abroad
    assert unscheduled("2025-01-16") == pytest.approx(0.30 * (1 - 15 / 30))  # the days to the next: 30
    assert unscheduled("2025-03-17") == pytest.approx(0.75 + 0.45 * 15 / 30)  # the latest, since the one before
    assert series.Accrual()("2025-03-01") == 0.0


def test_after_missed_payments_income_goes_on_at_the_rate_before_them_and_the_next_accrues_over_its_period():
    """series.Accrual: where payments the schedule gives were missed, two or more, the distribution before
    them goes on accruing at its rate for one more period, as the latest does, and the one after accrues over its
    schedule's period alone; a drift of days, or one payment missed, reads as the days since the one before. A day
    never accrues twice: where the one before came after one missed payment itself, its period is long, and its going
    on stops where the next begins to accrue."""
    from datetime import date
    day = lambda d: date.fromisoformat(d).toordinal()  # noqa: E731
    # an annual payer that pays in December 2018 and 2019, and next in December 2022
    paid = {"2018-12-17": 0.40, "2019-12-16": 0.47, "2022-12-19": 0.70}
    annual = series.Accrual(paid, {d: 1 for d in paid})
    assert annual("2020-03-01") - annual("2020-02-01") == pytest.approx(0.47 * 29 / 364)  # its period: 364 days
    assert annual("2021-11-30") - annual("2021-01-01") == pytest.approx(0.0)  # past one more period, nothing
    begins = day("2022-12-19") - 365  # the 2022 payout over its schedule's year alone
    assert annual("2022-12-19") - annual("2022-06-19") == pytest.approx(0.70 * (day("2022-12-19") - day("2022-06-19"))
                                                                       / 365)
    assert annual("2021-12-19") == annual("2021-12-01") and begins == day("2021-12-19")  # nothing before its year
    assert annual("2021-12-20") - annual("2021-12-19") == pytest.approx(0.70 / 365)
    without = series.Accrual({k: v for k, v in paid.items() if k < "2021"}, {d: 1 for d in paid})  # a 2020 record
    assert without("2020-03-23") - without("2020-02-19") == pytest.approx(annual("2020-03-23") - annual("2020-02-19"))
    # an annual payer whose dates drift: 368 days is no payment missed
    drifting = {"2023-06-01": 0.50, "2024-06-03": 0.52}
    a = series.Accrual(drifting, {d: 1 for d in drifting})
    assert a("2023-09-09") - a("2023-06-01") == pytest.approx(0.52 * 100 / 368)
    # a quarterly payer that misses one payment reads as the days since the one before; missing two, it does not
    quarterly = {"2024-01-02": 0.20, "2024-04-01": 0.20, "2024-09-30": 0.25, "2025-06-07": 0.30}
    q = series.Accrual(quarterly, {d: 4 for d in quarterly})
    assert q("2024-07-01") - q("2024-04-01") == pytest.approx(0.25 * 91 / 182)  # one missed: since the one before
    # 250 days after it the next is two payments late: the one before goes on for its own period, 182 days, but
    # stops where the next begins, 365 / 4 days before its ex-date, 159 days on; no day accrues both
    stop = day("2025-06-07") - 365 / 4
    assert stop - day("2024-09-30") < 182
    assert q("2025-06-07") - q("2024-09-30") == pytest.approx(0.25 * (stop - day("2024-09-30")) / 182 + 0.30)
    assert q("2025-03-01") - q("2025-02-01") == pytest.approx(0.25 * 28 / 182)  # still going on
    assert q("2025-04-05") - q("2025-04-03") == pytest.approx(0.30 * 2 / (365 / 4))  # the next, over its quarter
    assert series.MISSED * 365 / 4 < day("2025-06-07") - day("2024-09-30") < 3 * 365 / 4


def test_a_proxys_income_accrues_from_the_record_its_derivation_reads_whatever_else_the_store_holds(make_store):
    """A factor's months, and a window within the history the factors are estimated on, read the history's
    distributions, from where it is read to the as-of; an earlier window reads them as far after its end as a stretch
    since the one before can run, MISSED years, to the as-of at the latest, and twice that before its start. A store that also holds a newer payload
    reaching further back, with a distribution before the history, gives every move the same, since the history's
    first distribution accrues over its schedule's period either way. Read whole, the newer payload would accrue that
    distribution over the days since the earlier one, and the moves across it would differ."""
    from datetime import date, timedelta
    from harness.derive import history
    from harness.derive.channels import window_record
    from harness.derive.engine import Inputs
    from massive_data import ASOF, put_bars, put_dividends
    store = make_store()
    begins = series.lookback(ASOF)
    days = [d for d in SESSIONS if d >= begins][:60]
    put_bars(store, "BND", {d: 100.0 + 0.01 * i for i, d in enumerate(days)}, dividends={days[10]: 0.25, days[31]: 0.25})

    def read():
        with store.lock:
            item = history.moves_inputs(Inputs(store.engine), "BND", (days[0], ASOF), record=(begins, ASOF))
        return item, history.moves(item, store.engine.read, days, since=begins, until=ASOF)
    held, moves = read()
    before = (date.fromisoformat(begins) - timedelta(days=20)).isoformat()
    put_dividends(store, "BND", {before: 0.25, days[10]: 0.25, days[31]: 0.25}, fetched="2026-11-05T03:00:00+00:00",
                  covering=((date.fromisoformat(before) - timedelta(days=365)).isoformat(), ASOF))
    store.engine.reset()
    wider, again = read()
    assert wider["dividends"] != held["dividends"]  # the newer payload is the one read
    for start, end in ((days[0], days[12]), (days[5], days[40]), (days[20], days[59])):
        assert again(start, end) == pytest.approx(moves(start, end), abs=1e-15)
    whole = history.moves(wider, store.engine.read, days)
    assert abs(whole(days[0], days[12]) - moves(days[0], days[12])) > 1e-5
    assert window_record(days[5], days[20], ASOF) == (begins, ASOF)  # within the history: the months' record
    assert window_record("2020-03-02", "2020-04-01", ASOF) == ("2015-03-03", "2022-10-01")  # 1,826 before, 913 after
    assert window_record("2024-06-03", "2024-08-05", ASOF) == ("2019-06-04", ASOF)  # to the as-of at the latest
    # evaluation's case: an annual payer one payment late, then missed payments, goes on for its own long stretch; a
    # record that cuts the one before it reads that stretch as a year, alone, and the window twice what it accrues
    paid = {"2013-12-16": 1.0, "2014-12-15": 1.0, "2016-12-15": 2.0, "2020-12-15": 1.0}
    since, until = window_record("2017-07-03", "2017-10-02", "2026-11-04")
    read = series.Accrual(paid, {d: 1 for d in paid}, since, until)
    whole = series.Accrual(paid, {d: 1 for d in paid})
    assert read("2017-10-02") - read("2017-07-03") == pytest.approx(whole("2017-10-02") - whole("2017-07-03"))
    cut = series.Accrual(paid, {d: 1 for d in paid}, "2015-01-01")
    assert cut("2017-10-02") - cut("2017-07-03") > 1.9 * (whole("2017-10-02") - whole("2017-07-03"))


def test_a_series_closes_are_kept_by_their_payloads_content_and_one_id_in_two_stores_is_two_series():
    """portfolio.closes, kept by what they follow from: the payload's content, when it was read and how the series is
    read. Two stores can give one data id to different payloads, and each reads its own closes; the same payload read
    again is not loaded again."""
    from harness.derive import portfolio
    day = lambda d, c: {"t": int(datetime(2026, 11, d, 12).timestamp() * 1000), "c": c}  # noqa: E731
    payloads = {"a": {"results": [day(2, 100.0), day(3, 101.0)]}, "b": {"results": [day(2, 50.0), day(3, 49.0)]}}
    loaded = []

    def load(ref):
        loaded.append(ref["sha256"])
        return payloads[ref["sha256"]]

    item = lambda sha: {"ticker": "SPY", "ref": {"data": 1, "sha256": sha,  # noqa: E731
                                                 "retrieved_at": "2026-11-04T22:00:00+00:00"}}
    a, b = portfolio.closes(item("a"), load), portfolio.closes(item("b"), load)
    assert a == {"2026-11-02": 100.0, "2026-11-03": 101.0} and b == {"2026-11-02": 50.0, "2026-11-03": 49.0}
    assert portfolio.closes(item("a"), load) is a and loaded == ["a", "b"]


def test_a_holding_whose_read_fails_leaves_the_look_through_waiting_and_maps_once_the_read_lands(store):
    """A read that fails past its retries is ours, so nothing the analyst reads says so: the look-through waits for
    it, and the next read maps what it confirms (MATH.md 3.1; TOOLS.md 5)."""
    names(store, ("MIX", "ETF"), ("ALL", "ETF"), ("A", "CS"))
    put_constituents(store, "MIX", [("A", 0.6, "Equity"), ("B", 0.4, "Equity")])
    put_constituents(store, "ALL", [("B", 0.5, "Equity"), ("C", 0.5, "Equity")])

    def down(subject, covering):
        raise RuntimeError("the service did not answer")
    store.fetchers["ticker_ref"] = down
    hold(store, {"ticker": "MIX", "dollars": 1000.0}, {"ticker": "ALL", "dollars": 500.0})
    v = store.engine.value("lookthrough", "portfolio")
    assert v.waiting and v.output is None  # nothing booked on a read that did not land
    names(store, ("B", "CS"), ("C", "CS"))
    out = lookthrough(store)  # the read lands: every fund maps what it confirms
    assert out["e"] == pytest.approx({"name:A": 600.0, "name:B": 650.0, "name:C": 250.0})


def test_a_known_dividend_waits_for_its_rate_and_its_splits_and_is_never_left_out():
    """A dividend paid in another currency needs its rate on the ex-date, and one Massive gives no split-adjusted
    amount needs the listing's splits: until both are read the returns wait, rather than lose the dividend as a false
    loss (overnight item 13)."""
    from harness.derive.engine import Waiting
    from harness.derive.portfolio import paid

    records = {"status": "OK", "results": [
        {"id": "1", "ticker": "TSM", "ex_dividend_date": "2026-09-15", "cash_amount": 5.0,
         "split_adjusted_cash_amount": 5.0, "currency": "TWD"},
        {"id": "2", "ticker": "TSM", "ex_dividend_date": "2026-06-10", "cash_amount": 1.0,
         "split_adjusted_cash_amount": None, "currency": "USD"}]}
    splits = {"status": "OK", "results": [{"execution_date": "2026-08-01", "split_from": 1, "split_to": 2}]}
    with pytest.raises(series.Incomplete, match="TWD"):
        series.dividends(records, {}, splits)  # its rate not read
    with pytest.raises(series.Incomplete, match="splits"):
        series.dividends(records, {"TWD": {"2026-09-14": 0.031}}, None)  # its splits not read
    assert series.dividends(records, {"TWD": {"2026-09-14": 0.031}}, splits) == \
        pytest.approx({"2026-09-15": 5.0 * 0.031, "2026-06-10": 0.5})  # the rate in force, the amount split-adjusted
    item = {"dividends": {"data": "d"}, "rates": {}, "splits": {"data": "s"}}
    with pytest.raises(Waiting, match="TWD"):  # the returns wait, and the next read asks again
        paid(item, lambda ref: {"d": records, "s": splits}[ref["data"]])


# ---------------------------------------------------------------- cash


def test_cash_is_an_amount_in_its_currency_worth_its_rate_in_dollars_and_cash_is_a_ticker_like_any_other(store):
    """ONTOLOGY.md 2.2; MATH.md 3.1: cash is held as an amount in its currency, cash:{CURRENCY}, the dollar's where none
    is given: dollar cash is its amount and moves with nothing; cash in another currency is worth its amount at that
    currency's rate, and moves with it, booked as the currency's pair and as cash. CASH is a ticker like any other,
    Pathward Financial's, and is priced as a share."""
    from massive_data import SESSIONS, put_rate
    put_rate(store, "C:EURUSD", {d: 1.10 for d in SESSIONS[-3:]}, base="EUR", quote="USD")
    names(store, ("NVDA", "CS"), ("CASH", "CS"))
    hold(store, {"ticker": "NVDA", "dollars": 1000.0}, {"cash": 500.0}, {"cash": 200.0, "currency": "eur"},
         {"ticker": "CASH", "dollars": 300.0})
    links = {link["to"]: link for link in store.links_of("portfolio", "holds")}
    assert set(links) == {"name:NVDA", "cash:USD", "cash:EUR", "name:CASH"}
    assert links["cash:EUR"]["stated"] == {"cash": 200.0} and links["name:CASH"]["stated"]["ticker"] == "CASH"
    rows = store.engine.value("holdings", "portfolio").output["holds"]
    usd, eur, pathward = (rows[links[t]["id"]] for t in ("cash:USD", "cash:EUR", "name:CASH"))
    assert (usd["dollars"], usd["price"], usd["session_move"], usd["name"]) == (500.0, 1.0, 0.0, "name:CASH:USD")
    assert (eur["dollars"], eur["quantity"], eur["name"]) == (pytest.approx(220.0), 200.0, "name:C:EURUSD")
    assert pathward["price"] == 100.0 and pathward["quantity"] == pytest.approx(3.0)  # a share, at its close
    looked = store.engine.value("lookthrough", "portfolio").output
    assert looked["e"]["name:C:EURUSD"] == pytest.approx(220.0) and looked["e"]["name:CASH:USD"] == 500.0
    assert looked["owned"]["kind:cash"]["dollars"] == pytest.approx(720.0)
    assert looked["kinds"]["name:CASH"]["kind"] == "name"
    assert {h["name"] for h in looked["holdings"].values()} >= {"cash:USD", "cash:EUR"}
    assert store.get("cash:EUR")["stated"] == {"currency": "EUR"}
    store.edit(links["cash:EUR"]["id"], "cash", 300.0)
    assert store.engine.value("holdings", "portfolio").output["holds"][links["cash:EUR"]["id"]]["dollars"] == \
        pytest.approx(330.0)
    refusals = [("cash:USD", {"cash": 1.0}, "edit it instead"), ("cash:usd", {"cash": 1.0}, "is not cash in a currency"),
                ("cash:GBP", {"dollars": 5.0}, "cash held gives its amount in its currency"),
                ("name:NVDA", {"cash": 5.0}, "cash is held as cash:{CURRENCY}")]
    for to, fields, said in refusals:
        with pytest.raises(StoreError, match=re.escape(said)):
            store.link("portfolio", "holds", to, fields)
    with pytest.raises(StoreError, match="cash in USD is held twice"):
        hold(store, {"cash": 1.0}, {"cash": 2.0, "currency": "USD"})


def test_cash_is_cash_in_its_currency_and_nothing_reads_its_currency_as_a_listing(store):
    """MATH.md 3.1: a cash holding is cash in its currency, booked as the security its currency moves as. A currency's code
    can be another security's ticker (USD is a fund's), so no reading of cash, in dollars or in another currency, reads
    a listing's reference."""
    from massive_data import SESSIONS, put_rate
    from harness.derive.portfolio import ticker_of
    from harness.ledger import holdings as what_it_owns
    put_rate(store, "C:EURUSD", {d: 1.10 for d in SESSIONS[-3:]}, base="EUR", quote="USD")
    names(store, ("NVDA", "CS"))
    asked = []

    def references(subject, *a, **k):  # the store's client for a listing's reference: each subject asked, written down
        asked.append(subject)
        raise LookupError(f"no listing {subject}")

    store.fetchers["ticker_ref"] = references
    hold(store, {"ticker": "NVDA", "dollars": 1000.0}, {"cash": 500.0}, {"cash": 200.0, "currency": "EUR"})
    assert [ticker_of(c) for c in ("cash:USD", "cash:EUR")] == ["CASH:USD", "C:EURUSD"]
    for cash in ("cash:USD", "cash:EUR"):
        assert store.engine.value("reference", cash).output == {"type": "cash", "company": None}
    what_it_owns(store)
    assert not {"USD", "EUR"} & set(asked)


def test_cash_in_another_currency_carries_its_rates_betas_and_dollar_cash_none(store):
    """MATH.md 1.6, 3.1: cash in dollars responds to nothing, and cash in another currency moves with its rate: its part of
    the dollar beta is its dollars at the pair's betas, fitted on the pair's own rates."""
    from massive_data import put_rate
    load_channels(store)
    put_market(store, np.random.default_rng(3))
    put_rate(store, "C:EURUSD", closes_from(random_returns(np.random.default_rng(4), 0.005), 1.10), base="EUR",
             quote="USD")
    hold(store, {"cash": 500.0}, {"cash": 200.0, "currency": "EUR"})
    g = store.engine.value("g", "portfolio").output
    pair = store.engine.value("betas", "portfolio").output["objects"]["name:C:EURUSD"]["fields"]["betas"]
    at = {link["to"]: g["by_holding"][link["id"]] for link in store.links_of("portfolio", "holds")}
    assert set(at["cash:USD"]["b"].values()) == {0.0}
    assert at["cash:EUR"]["b"] == pytest.approx(pair) and any(abs(b) > 1e-3 for b in pair.values())
    assert at["cash:EUR"]["g"] == pytest.approx({c: at["cash:EUR"]["dollars"] * b for c, b in pair.items()})


def test_a_listing_abroads_window_moves_in_dollars_by_what_holding_it_earned_less_the_income_it_accrued(make_store):
    """MATH.md 3.2: a factor's proxy listed abroad moves in dollars, each close at the rate struck with it, as its
    returns read it; a dividend goes back on the close it goes ex on, at that close's rate, and accrues as income
    since the one before it, each day's accrual at its close's rate. Closes of 103, 100.5 and 101 won at 0.00075,
    0.00076 and 0.00074 dollars; 3 won going ex on the second, the one before it 29 days earlier; so 3 / 29 won accrue
    each day, which goes back with the dividend:
    (100.5 + 3 - 3/29) * 0.00076 / (103 * 0.00075) * (101 - 3/29) * 0.00074 / (100.5 * 0.00076) - 1."""
    from harness.derive import history
    from harness.derive.engine import Inputs
    from massive_data import put_home, put_rate
    store = make_store()
    days = ["2026-10-27", "2026-10-28", "2026-10-29"]
    rates = dict(zip(days, (0.00075, 0.00076, 0.00074)))
    put_rate(store, "C:KRWUSD", rates, base="KRW", quote="USD")
    put_home(store, "005930.KS", dict(zip(days, (103.0, 100.5, 101.0))), dividends={"2026-09-29": 2.0, "2026-10-28": 3.0})
    with store.lock:
        item = history.moves_inputs(Inputs(store.engine), "005930.KS", ("2026-10-27", "2026-11-04"))
        moved = history.moves(item, store.engine.read, days)
    accrued = 3.0 / 29
    expected = ((100.5 + 3.0 - accrued) * 0.00076 / (103.0 * 0.00075)) * ((101.0 - accrued) * 0.00074
                                                                          / (100.5 * 0.00076)) - 1
    assert moved("2026-10-27", "2026-10-29") == pytest.approx(expected, rel=1e-12)
    assert moved.closes == pytest.approx({d: c * rates[d] for d, c in zip(days, (103.0, 100.5, 101.0))})
