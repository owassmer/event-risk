"""A fund's rows as exposure (harness.derive.rows): each row what it is exposed to and by how much, per dollar of the
net assets of the report it comes from, with that report's date; no row the sources do not state, so exposures need not
sum to value; an index's companies only from what establishes them, never from what the fund itself holds; and a fund's
current holdings from ETF Global with its derivatives from its latest report."""

from __future__ import annotations

import pytest

from harness.derive.assets import Kinds
from harness.derive.rows import index_key, with_exposure

NA = 1000.0


def share(ticker, isin, weight):
    return {"constituent_name": ticker, "constituent_ticker": ticker, "isin": isin, "weight": weight,
            "asset_category": "EC", "issuer_category": "CORP", "currency": "USD"}


def derivative(weight, **d):
    return {"constituent_name": "a derivative", "weight": weight, "asset_category": "DE", "issuer_category": "OTHER",
            "market_value": weight * NA, "derivative": d}


def report(rows):
    return {"status": "OK", "effective_date": "2026-08-31", "net_assets": NA, "results": rows}


INDEX = {"index": {"name": "An Index", "identifier": "IDX"}}
IDENTIFIED = {"index": {**INDEX["index"], "figi": "BBG000000001"}}  # the index, by the FIGI OpenFIGI names it by
READINGS = {"IDX": {"index": {"figi": "BBG000000001"}}, "IDX-ISIN": {"index": {"figi": "BBG000000001"}},
            "TICK": {"index": None, "securities": [], "listed": True}}


def test_a_levered_funds_rows_are_its_holdings_and_its_swap_and_future_at_notional_with_no_financing_row():
    rows = [share("A", "US0000000001", 0.30), share("B", "US0000000002", 0.10),
            {"constituent_name": "bills", "weight": 0.55, "asset_category": "STIV", "issuer_category": "UST"},
            derivative(0.04, category="SWP", reference=INDEX, notional=2200.0, currency="USD"),
            derivative(0.01, category="FUT", reference={"index": {"name": "An Index future",
                                                                  "identifier": "IDX-ISIN"}},
                       notional=-400.0, currency="USD", payoff_profile="Long")]
    out = with_exposure(report(rows), Kinds(), READINGS)
    by = {r["constituent_name"]: r for r in out["results"]}
    assert (out["net_assets"], out["report_date"]) == (NA, "2026-08-31")
    assert all((r["report_date"], r["net_assets"]) == ("2026-08-31", NA) for r in out["results"])
    assert by["A"]["exposure"] == 0.30 and by["A"]["payoff"] == "holding" and by["A"]["to"]["company"]["isin"]
    assert by["bills"]["payoff"] == "cash" and by["bills"]["kind"] == "cash"
    swap, future = (r for r in out["results"] if r.get("payoff") == "linear")
    assert swap["exposure"] == pytest.approx(2.2) and future["exposure"] == pytest.approx(0.4)  # signed by its profile
    # the index is identified, and what the fund itself holds (A and B) is not taken for its companies
    assert swap["to"] == IDENTIFIED and "companies" not in future["to"]
    assert index_key(swap["to"]["index"], READINGS["IDX"]) == index_key(future["to"]["index"], READINGS["IDX-ISIN"])
    assert len(out["results"]) == len(rows) and not any(r.get("financing") for r in out["results"])
    assert sum(r["exposure"] for r in out["results"]) == pytest.approx(0.95 + 2.2 + 0.4)  # not its value, a dollar


def test_an_index_takes_the_companies_that_establish_it_and_a_conflicting_identifier_none():
    members = [{"isin": "US0000000001", "weight": 0.6}, {"isin": "US0000000002", "weight": 0.4}]
    rows = [{"constituent_name": "cash", "weight": 1.0, "asset_category": "STIV", "issuer_category": "RF"},
            derivative(0.0, category="SWP", reference=INDEX, notional=-3000.0, currency="USD"),
            derivative(0.0, category="SWP", reference={"index": {"identifier": "TICK"}}, notional=-10.0,
                       currency="USD")]
    basket = {"fund": "F", "holdings_asof": "2026-11-09", "tracking": 0.02, "sessions": ["2026-08-05", "2026-11-06"]}
    out = with_exposure(report(rows), Kinds(), READINGS,
                        {("figi", "BBG000000001"): {"values": "I:IDX", "companies": members, "basket": basket}})
    swap, conflict = (r for r in out["results"] if r.get("payoff") == "linear")
    assert swap["exposure"] == pytest.approx(-3.0) and swap["to"]["companies"] == members
    assert swap["to"]["basket"] == basket and swap["to"]["index"]["values"] == "I:IDX"
    assert index_key(conflict["to"]["index"], READINGS["TICK"]) == ("conflict", "TICK")
    assert "companies" not in conflict["to"]  # an identifier that names a listing confirms no index


def test_a_forwards_sides_are_two_currencies_summing_to_its_value_and_an_option_gives_its_terms_not_a_delta():
    rows = [share("A", "US0000000001", 0.98),
            {**derivative(0.01, category="FWD", sold={"currency": "EUR", "amount": -900.0},
                          bought={"currency": "USD", "amount": 1050.0}), "asset_category": "DFE"},
            {**derivative(-0.02, category="OPT", reference={"security": {"ticker": "A", "title": "A Inc"}},
                          right="call", side="written", underlying_per_contract=100.0, strike=50.0,
                          expiry="2026-09-18"), "shares": 3.0, "units": "NC"},
            {**derivative(0.001, category="WAR", reference={"security": {"cusip": "G06362100"}},
                          underlying_per_contract=1.0, strike=11.5, expiry="2027-06-02")}]
    out = with_exposure(report(rows), Kinds(), {})
    usd, eur = (r for r in out["results"] if (r.get("to") or {}).get("currency"))
    assert (usd["to"], usd["exposure"]) == ({"currency": "USD"}, pytest.approx(1.05))
    assert (eur["to"], eur["exposure"], eur["weight"]) == ({"currency": "EUR"}, pytest.approx(-1.04), None)
    option, warrant = (r for r in out["results"] if isinstance(r.get("payoff"), dict))
    assert option["exposure"] is None and option["payoff"]["option"] == {
        "right": "call", "side": "written", "underlying_per_contract": 100.0, "contracts": 3.0, "strike": 50.0,
        "expiry": "2026-09-18", "value": -20.0}
    assert option["to"] == {"company": {"constituent_ticker": "A", "constituent_name": "A Inc"}}
    assert warrant["payoff"]["option"]["right"] == "call" and warrant["payoff"]["option"]["side"] == "purchased"


def test_a_funds_current_holdings_are_etf_globals_and_its_derivatives_are_its_reports_each_per_its_own_net_assets():
    payload = {"status": "OK", "effective_date": "2026-11-06", "results": [
        {"constituent_ticker": "A", "isin": "US0000000001", "asset_class": "Equity", "weight": 0.9,
         "market_value": 900.0},
        {"constituent_ticker": "EUR", "asset_class": "Cash", "currency_traded": "EUR", "weight": 0.1,
         "market_value": 100.0},
        {"constituent_name": "an index future", "asset_class": "Futures", "weight": 0.0, "market_value": 0.0}]}
    out = with_exposure(payload, Kinds(), {})
    a, eur, future = out["results"]  # ETF Global's own row of a derivative states no exposure, and stands for none
    assert future["exposure"] is None
    assert (a["exposure"], a["payoff"], eur["payoff"], eur["to"]) == (0.9, "holding", "cash", {"currency": "EUR"})
    assert (a["report_date"], a["net_assets"]) == ("2026-11-06", pytest.approx(1000.0))
    assert out["net_assets"] == pytest.approx(1000.0) and "derivatives_date" not in out
    held = report([share("A", "US0000000001", 0.95),
                   derivative(0.001, category="FUT", reference=INDEX, notional=50.0, currency="USD",
                              payoff_profile="Long")])
    out = with_exposure(payload, Kinds(), READINGS, report=held)
    a, eur, future = out["results"]
    assert (a["report_date"], future["report_date"]) == ("2026-11-06", "2026-08-31")  # each row its own source's
    assert future["exposure"] == pytest.approx(0.05) and future["net_assets"] == NA and future["to"] == IDENTIFIED
    assert (out["report_date"], out["derivatives_date"], out["derivatives_net_assets"]) == ("2026-11-06",
                                                                                           "2026-08-31", NA)


def test_a_holding_of_another_currency_is_currency_in_it_and_a_notional_under_a_row_in_another_currency_has_no_value():
    held = {"constituent_name": "SWISS FRANC", "weight": 0.002, "asset_category": "OTHER", "issuer_category": "OTHER",
            "asset_class": "currency", "currency": "CHF", "exchange_rate": 0.80845, "market_value": 2.0}
    future = {**derivative(0.0, category="FUT", reference=INDEX, notional=1_368_600.0, currency="KRW",
                           payoff_profile="Long"), "currency": "KRW", "exchange_rate": 1368.6}
    chf, fut = with_exposure(report([held, future]), Kinds(), READINGS)["results"]
    assert (chf["kind"], chf["payoff"], chf["to"]) == ("currency", "cash", {"currency": "CHF"})
    # the report does not say whether the notional is in won or in dollars: no value, its reference and kind kept
    assert fut["exposure"] is None and fut["to"] == IDENTIFIED and fut["kind"] == "derivative"
