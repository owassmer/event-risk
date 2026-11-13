"""A fund's row that states shares held and their value, as its issuer states a swap's or a contract for difference's
equity leg each day (MATH.md 3.1): its value per share is one listing's close on its date, which names the listing, and
the fund's own returns confirm it, the fund's rows at the exposures they state reproducing its daily returns. Every
test runs with no keys and no network."""

from __future__ import annotations

import pytest

from harness.derive.assets import Kinds
from harness.derive.rows import (CONFIRMING, HALF_CENT, established_by_price, price_subject, reached, reproduced,
                                 states_shares, with_exposure)

DAY = "2026-11-10"


def leg(shares, value, weight, **fields):
    """An ETF Global row of a contract for difference."""
    return {"constituent_ticker": "AXSB1", "constituent_name": "CFD A COMPANY", "asset_class": "SWAPS",
            "shares_held": shares, "market_value": value, "weight": weight, "effective_date": DAY, **fields}


def test_a_row_states_shares_where_its_value_per_share_can_be_a_close():
    assert states_shares(leg(-6975.0, -4473695.25, -2.0), "derivative")
    assert states_shares(leg(201251.0, 129080378.89, 0.03, asset_class=None), "other")  # a swap its labels leave bare
    # a row stated in dollars, its shares its value, names no listing: a financing leg, or a leg at its notional
    assert not states_shares(leg(5332609.31, 5332609.31, 2.38), "derivative")
    assert not states_shares(leg(-6975.0, -4473695.25, -2.0), "equity")  # a share is resolved by its identifiers
    assert not states_shares(leg(-6975.0, -4473695.25, -2.0, figi="BBG000000001"), "derivative")
    assert not states_shares(leg(-10.0, -30.0, 0.0, asset_class=None, constituent_ticker="UBER  261113C00078000",
                                 constituent_name="UBER US 11/13/26 C78"), "other")  # a contract it names
    assert not states_shares(leg(5.0, 40.0, 0.0, asset_class="Option"), "derivative")
    assert not states_shares(leg(-6975.0, -4473695.25, -2.0, effective_date=None), "derivative")
    assert not states_shares(leg(0.0, -4473695.25, -2.0), "derivative")
    # its subject: the prices its value per share can be, its value to the half cent over its shares
    day, low, high = price_subject(leg(-6975.0, -4473695.25, -2.0)).split("|")
    assert day == DAY and float(low) <= 641.39 <= float(high)
    assert float(high) - float(low) == pytest.approx(2 * HALF_CENT / 6975.0)


def _closes(returns, start=100.0):
    days = [f"2026-10-{d:02d}" for d in range(10, 10 + len(returns) + 1)]
    out, c = {days[0]: start}, start
    for d, r in zip(days[1:], returns):
        c *= 1 + r
        out[d] = c
    return out


def test_a_funds_rows_at_the_exposures_they_state_reproduce_its_returns():
    """Over the fund's last CONFIRMING sessions: 1 less the sum of squares of its return less the stated exposures
    times their listings' returns, over its sum of squares about its mean."""
    r = [0.01, -0.01] * (CONFIRMING // 2)
    stock, fund = _closes(r), _closes([-2 * x for x in r])
    asof = max(fund)
    assert reproduced((fund, {}), [(-2.0, (stock, {}))], asof) == pytest.approx(1.0)
    # at -1, the rest of each session's move is left: 10 * 0.0001 of 10 * 0.0004
    assert reproduced((fund, {}), [(-1.0, (stock, {}))], asof) == pytest.approx(0.75)
    other = _closes([0.01] * CONFIRMING)  # a listing that collided on the price: it moves otherwise
    assert reproduced((fund, {}), [(-2.0, (other, {}))], asof) < 0
    young = dict(list(fund.items())[:CONFIRMING])  # too few sessions to say
    assert reproduced((young, {}), [(-2.0, (stock, {}))], max(young)) is None


def test_each_row_names_the_listing_its_close_and_the_funds_returns_establish():
    good = {"X", "P"}

    def reproduces(chosen):
        if not all(v in good for v in chosen.values()):
            return 0.3
        return 0.95 if "c" in chosen else 0.6

    rows = [("a", ["X"]), ("b", ["X", "Y"]), ("c", ["P", "Q"]), ("d", ["P", "Q"])]
    # a sole match; another row's matches anchored on it, the legs on one reference being at one close; and legs left
    # with the same matches taking the one with which the fund's rows reproduce its returns
    assert established_by_price(rows, reproduces) == {"a": "X", "b": "X", "c": "P", "d": "P"}
    # the fund's rows reproduce its returns with either of two matches: the returns cannot say, and without those
    # legs the rows do not reproduce the fund, so none is established
    assert established_by_price(rows, lambda chosen: 0.95 if "c" in chosen else 0.6) == {}
    # rows that never reproduce the fund: prices that collided
    assert established_by_price(rows, lambda chosen: 0.5) == {}
    # a fund too young for its returns to say: a match that is the only one
    assert established_by_price(rows, lambda chosen: None) == {"a": "X"}


def test_a_row_its_price_names_is_its_exposure_and_the_report_row_of_the_same_position_is_not_read():
    cfd, financing = leg(-100.0, -5000.0, -2.0), leg(5500.0, 5500.0, 2.2)
    bare = leg(10.0, 500.0, 0.2, asset_class=None, constituent_ticker="02079K305 TRS GS")  # no kind from its labels
    payload = {"status": "OK", "effective_date": DAY, "results": [
        cfd, financing, bare, {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.6,
                               "market_value": 1500.0}]}
    named = {"ticker": "ACO", "company": {"constituent_ticker": "ACO", "figi": "BBG00CLASS01", "named_by": "price"},
             "session": DAY, "close": 50.0, "matches": ["ACO"], "reproduced": 0.99}
    priced = {"rows": {price_subject(cfd): named, price_subject(bare): {**named, "ticker": "GOO",
                                                                        "company": {"constituent_ticker": "GOO"}}},
              "listed": {"US0000000ACO": ["ACO"]}}

    def swap(reference, notional=1000.0):
        return {"constituent_name": "a swap", "weight": 0.1, "asset_category": "DE", "issuer_category": "OTHER",
                "market_value": 100.0, "derivative": {"category": "SWP", "reference": reference, "notional": notional,
                                                      "currency": "USD"}}

    report = {"status": "OK", "effective_date": "2026-06-30", "net_assets": 2000.0, "results": [
        swap({"security": {"ticker": "ACO", "title": "A Company"}}),
        swap({"security": {"isin": "US0000000ACO", "title": "A Company"}}),
        swap({"index": {"name": "A Company", "identifier": "ACO"}}),  # a fund named by its ticker as an index
        swap({"security": {"ticker": "OTH", "isin": "US0000000OTH", "title": "Another"}})]}
    out = with_exposure(payload, Kinds(), {}, report=report, priced=priced)["results"]
    mine = [r for r in out if r.get("priced")]
    # each at its value over the net assets the rows imply: 12,500 over weights of 5.0, 2,500
    assert [(r["priced"]["ticker"], round(r["exposure"], 9), r["payoff"]) for r in mine] == \
        [("ACO", -2.0, "linear"), ("GOO", 0.2, "linear")]
    assert mine[0]["to"] == {"company": named["company"]}
    # the leg stated in dollars names nothing, and gives way to the report as an unestablished derivative row does
    assert not any(r.get("shares_held") == 5500.0 for r in out)
    left = [r for r in out if r.get("derivative")]
    assert [((r["derivative"]["reference"].get("security") or {}).get("ticker")) for r in left] == ["OTH"]
    # an established row is among what the fund's derivatives reach, whatever its labels, for its company to resolve
    assert {t["constituent_ticker"] for t, _ in reached(out, 1.0)} >= {"ACO", "GOO"}


def test_a_leg_labelled_as_nothing_is_a_derivative_where_its_funds_report_states_the_same_position_as_one():
    """KORU's legs, read live, come as "EWY SWAP ASSET LEG" with no label, so their holdings source gives them no kind.
    Where the fund's report states a derivative on the listing a leg's price names, the leg is that position, and it is
    a derivative by the report's own code for it (Form N-PORT's derivative category). A leg the report states nothing
    on keeps the kind its labels give, whatever its name says."""
    on_report = leg(100.0, 5000.0, 2.0, asset_class=None, constituent_ticker=None, constituent_name="ACO SWAP ASSET LEG")
    alone = leg(10.0, 500.0, 0.2, asset_class=None, constituent_ticker=None, constituent_name="GOO SWAP ASSET LEG")
    payload = {"status": "OK", "effective_date": DAY, "results": [
        on_report, alone, {"constituent_ticker": "USD", "asset_class": "Cash", "weight": -1.2, "market_value": -3000.0}]}
    priced = {"rows": {price_subject(on_report): {"ticker": "ACO", "company": {"constituent_ticker": "ACO"},
                                                  "session": DAY, "close": 50.0},
                       price_subject(alone): {"ticker": "GOO", "company": {"constituent_ticker": "GOO"},
                                              "session": DAY, "close": 50.0}}, "listed": {}}
    report = {"status": "OK", "effective_date": "2026-08-31", "net_assets": 2500.0, "results": [
        {"constituent_name": "a swap", "weight": 2.0, "asset_category": "EC", "issuer_category": "CORP",
         "market_value": 10.0, "derivative": {"category": "SWP", "reference": {"security": {"ticker": "ACO"}},
                                              "notional": 5000.0, "currency": "USD"}}]}
    rows = with_exposure(payload, Kinds(), {}, report=report, priced=priced)["results"]
    by = {r["priced"]["ticker"]: r for r in rows if r.get("priced")}
    assert by["ACO"]["reported_as"] == {"kind": "derivative", "category": "SWP"}
    assert Kinds().of(by["ACO"]) == "derivative" and Kinds().of(by["GOO"]) == "other"
    assert (by["ACO"]["kind"], by["GOO"]["kind"]) == ("derivative", "other")  # the row says the kind it is read as
    assert not any(r.get("derivative") for r in rows)  # the report's row is the same position, not read again


def test_a_fund_whose_swaps_are_legs_its_price_names_is_measured_on_their_one_reference():
    """MATH.md 3.1: a fund on one reference is measured on it, its swaps' legs among its swaps where its price names
    them. KORU, read live: its holdings give EWY's own shares and "EWY SWAP ASSET LEG" rows that state shares and their
    value, unlabelled, and its report states the swaps on EWY (named as an index by its ticker). The legs are those
    swaps, by the report's code (reported_as), so the fund is measured on EWY: one row at its slope, the legs and the
    shares standing for it."""
    from harness.derive.rows import category, slope_reference
    day, close = "2026-11-11", 147.81

    def held(name, shares, value, weight, **fields):
        return {"constituent_ticker": None, "constituent_name": name, "asset_class": None, "security_type": None,
                "shares_held": shares, "market_value": value, "weight": weight, "effective_date": day, **fields}

    legs = [held("EWY SWAP ASSET LEG", 1834861.0, 271210804.41, 0.2498968318),
            held("EWY SWAP ASSET LEG", 3442217.0, 508794094.77, 0.4688088757)]
    payload = {"status": "OK", "effective_date": day, "results": [
        held("ISHARES MSCI SOUTH KOREA CAP", 3559041.0, 526061850.21, 0.4847195891, constituent_ticker="EWY",
             asset_class="Equity", security_type="ETP", isin="US4642867729"),
        *legs, held("EWY SWAP FINANCING LEG", -489624755.6, 0.0, 0.0),
        held("DREYFUS GOVT CASH MAN INS", 364100914.48, 364100914.48, 0.335486874),
        held("JPM COLLATERAL", -71897863.0, -71897863.0, -0.0662475384)]}
    ewy = {"constituent_ticker": "EWY", "isin": "US4642867729", "named_by": "price"}
    priced = {"rows": {price_subject(leg): {"ticker": "EWY", "company": ewy, "session": day, "close": close}
                       for leg in legs}, "listed": {}}

    def swap(value, notional):  # as the fund's report states it (Form N-PORT, read live)
        return {"constituent_name": "N/A", "title": "ISHARES MSCI SOUTH KOREA CAPPED ETF SWAP", "weight": 0.01,
                "asset_class": "Derivatives-other", "security_type": "Other - Swap", "asset_category": "DO",
                "issuer_category": "OTHER", "market_value": value, "currency": "USD",
                "derivative": {"category": "SWP", "reference": {"index": {"identifier": "EWY"}}, "notional": notional,
                               "currency": "USD", "termination": "2049-12-31"}}

    report = {"status": "OK", "effective_date": "2026-07-31", "net_assets": 1256390638.61,
              "results": [swap(-28798611.91, 515425933.8), swap(119842311.29, 1093812450.0)]}
    rows = with_exposure(payload, Kinds(), {}, report=report, priced=priced)["results"]
    named = [r for r in rows if r.get("priced")]
    assert [category(r) for r in named] == ["SWP", "SWP"] and not any(r.get("derivative") for r in rows)
    found = slope_reference(rows)
    assert found is not None and found["to"] == {"company": ewy} and found["named_by"] == "price"
    assert len(found["rows"]) == 2 and len(found["shares"]) == 1  # the two legs, and EWY's own shares
    slope = {"slope": 2.88, "to": "2026-11-11", "r2": 0.99}
    measured = with_exposure(payload, Kinds(), {}, report=report, priced=priced, slope=slope)["results"]
    one = [r for r in measured if r.get("measured")]
    assert len(one) == 1 and one[0]["exposure"] == 2.88 and one[0]["to"] == {"company": ewy}
    assert all(r.get("measured_by") and r.get("exposure") is None for r in measured
               if r.get("priced") or r.get("constituent_ticker") == "EWY")  # standing for the slope


def test_a_funds_rows_that_state_more_than_it_leave_its_derivatives_financing_and_it_divides_whole_by_kind():
    """A fund at a multiple states each swap's asset leg at its notional and its financing at no value, so its rows sum
    to more than the fund (read live: 314%). What they leave unstated, negative, is the derivatives' financing: a
    derivative, so the fund divides by kind to its whole, and the portfolio's line for it stands under derivative. A
    fund holding no derivative leaves other; a commodity pool, commodity."""
    from harness.derive.portfolio import unstated_kind, what_it_holds
    payload = {"status": "OK", "effective_date": DAY, "results": [
        {"constituent_name": "A REFERENCE ETF", "weight": 0.48, "asset_class": "Equity"},
        {"constituent_name": "THE SWAP ASSET LEG", "weight": 2.2, "asset_class": "Swaps"},
        {"constituent_name": "A MONEY MARKET FUND", "weight": 0.46}]}
    held = what_it_holds("LEV", payload, {}, 1000.0, set())
    kinds = {d["kind"]: round(d["weight"], 6) for d in held["by_kind"]}
    assert kinds == {"equity": 0.48, "other": 0.46, "derivative": 0.06} and held["weights_sum"] == pytest.approx(3.14)
    assert sum(d["dollars"] for d in held["by_kind"]) == pytest.approx(1000.0)  # the whole fund
    assert unstated_kind(None, {"equity"}, -0.02) == "other"  # no derivative: what nothing says
    assert unstated_kind(None, {"derivative"}, 0.05) == "other"  # left over, not financing
    pool = {"status": "OK", "results": {"sic_code": "6221"}}
    assert unstated_kind(pool, {"derivative"}, 0.9) == "commodity"
