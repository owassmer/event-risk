"""Whole current option books and partial sleeves keep their actual units and payoffs."""
from __future__ import annotations

import pytest

from harness.derive import option_funds, options

DAY = "2026-11-11"
PRICE = options.european(True, 100.0, 100.0, 0.5, 0.0, 0.0, 0.2)[0]


class Reads:
    def __init__(self):
        self.asked = []

    def data(self, kind, ticker, **kwargs):
        self.asked.append((kind, ticker, kwargs))
        return {"kind": kind, "subject": ticker, "data": "test"}

    def payload(self, ref):
        return {"status": "OK", "results": []}


def option(*, side="written", kind="call", underlying="SPY", expiry="2027-05-11", **extra):
    sign = -1 if side == "written" else 1
    return {"net_assets": 100.0, "report_date": DAY, "market_value": sign * PRICE,
            "payoff": {"option": {"right": kind, "style": "european", "side": side,
                                    "strike": 100.0, "expiry": expiry, "contracts": 1.0,
                                    "underlying_per_contract": 1.0, "value": sign * PRICE,
                                    "symbol": "O:SPY270511C00100000"}},
            "quote": {"underlying": underlying, "price": PRICE, "underlying_price": 100.0,
                      "at": "2026-11-11T21:00:00+00:00", "worked": {"rate": 0.0, "yield": 0.0}},
            "to": {"company": {"constituent_ticker": underlying}}, **extra}


def share(ticker="SPY", amount=100.0):
    return {"net_assets": 100.0, "market_value": amount, "payoff": "holding",
            "to": {"company": {"constituent_ticker": ticker}}}


def cash(amount):
    return {"net_assets": 100.0, "market_value": amount, "payoff": "cash"}


def book(*held):
    """The look-through, and each fund's rows (the fund_rows value)."""
    return {"asof": DAY}, {"FUND": {"net_assets": 100.0, "results": list(held)}}


def compute(specs, ratios=None):
    ratios = ratios or {"down": 0.5, "up": 1.5}
    return option_funds.compute(specs, lambda _: None, {"down": 0.5, "up": 0.5}, ["down", "up"],
                                lambda name: ratios)


def test_a_whole_covered_call_keeps_its_cap_even_when_the_fund_beta_would_pass_it():
    ctx = Reads()
    specs = option_funds.inputs(ctx, *book(option(), share(), cash(PRICE)), "2027-01-01")
    assert specs["name:FUND"]["full"] and ctx.asked == []
    got, terms = compute(specs)
    # One share and one written call are capped at strike plus the original premium/cash.
    assert got["name:FUND"] == pytest.approx({"down": 0.5 + PRICE / 100, "up": 1 + PRICE / 100})
    assert terms["name:FUND"]["contracts"][0]["units_per_nav"] == -0.01
    assert not terms["name:FUND"]["contracts"][0]["calibrated"]
    assert terms["name:FUND"]["sleeve_today"] == pytest.approx(-PRICE / 100)


def test_a_whole_protective_put_keeps_its_floor():
    specs = option_funds.inputs(Reads(), *book(option(side="purchased", kind="put"), share(), cash(-PRICE)),
                                 "2027-01-01")
    got, _ = compute(specs)
    assert got["name:FUND"] == pytest.approx({"down": 1 - PRICE / 100, "up": 1.5 - PRICE / 100})


def test_other_assets_make_a_partial_sleeve_whose_history_is_read():
    ctx = Reads()
    specs = option_funds.inputs(ctx, *book(option(), share("MSFT", 30.0), cash(70.0 + PRICE)), "2027-01-01")
    assert not specs["name:FUND"]["full"]
    assert ctx.asked[0][0:2] == ("holdings_history", "FUND")
    got, terms = compute(specs)
    # Only the option sleeve is returned. No whole-fund response or invented residual is inserted here.
    assert got["name:FUND"] == {"down": 0.0, "up": -0.5}
    assert terms["name:FUND"]["history"]["kind"] == "holdings_history"


def test_an_expiring_contract_stays_in_the_remainder_and_cannot_establish_a_whole_payoff():
    ctx = Reads()
    specs = option_funds.inputs(ctx, *book(option(), option(expiry="2026-12-01"), share()), "2027-01-01")
    assert not specs["name:FUND"]["full"]
    assert len(specs["name:FUND"]["contracts"]) == 1
    assert ctx.asked[0][0] == "holdings_history"


def test_multiple_references_and_unidentified_contracts_do_not_establish_a_whole_payoff():
    unknown = option()
    unknown["payoff"]["option"]["style"] = None
    for held in (option(underlying="QQQ"), unknown):
        specs = option_funds.inputs(Reads(), *book(option(), held, cash(100)), "2027-01-01")
        assert not specs["name:FUND"]["full"]


def test_index_options_use_the_index_world_not_an_unrelated_fund_beta():
    specs = option_funds.inputs(Reads(), *book(option(underlying="I:SPX"), cash(100 + PRICE)), "2027-01-01")
    got, _ = option_funds.compute(specs, lambda _: None, {"down": 0.5, "up": 0.5}, ["down", "up"],
                                  lambda name: pytest.fail("no equity price should be substituted for this index"),
                                  lambda ticker: {"down": 0.5, "up": 1.5} if ticker == "I:SPX" else None)
    assert got["name:FUND"] == pytest.approx({"down": 1 + PRICE / 100, "up": 0.5 + PRICE / 100})


def test_a_quoted_delta_does_not_supply_a_zero_rate_or_dividend_yield():
    row = option()
    row["quote"].pop("worked")
    row["quote"]["delta"] = 0.5
    ctx = Reads()
    specs = option_funds.inputs(ctx, *book(row, share(), cash(PRICE)), "2027-01-01")
    asked = [(kind, ticker) for kind, ticker, _ in ctx.asked]
    assert ("yield_curve", "2026") in asked and ("dividends", "SPY") in asked
    contract = specs["name:FUND"]["contracts"][0]
    assert "rate" not in contract and "yield" not in contract
    assert contract["payer"]["ticker"] == "SPY"


def test_asynchronous_strike_quotes_share_one_observed_underlying_and_clock():
    first, second = option(side="purchased"), option(side="purchased")
    second["payoff"]["option"]["strike"] = 110
    second["quote"].update(at="2026-11-11T21:01:00+00:00", underlying_price=100.5)
    specs = option_funds.inputs(Reads(), *book(first, second, cash(100 - 2 * PRICE)), "2027-01-01")
    _, terms = compute(specs, {"down": .95, "up": 1.05})
    a, b = terms["name:FUND"]["contracts"]
    assert a["underlying_today"] == b["underlying_today"] == 100.5
    assert a["years"] == b["years"]
    assert a["valuation_at"] == b["valuation_at"] == second["quote"]["at"]
    assert a["quote_at"] == first["quote"]["at"] and a["quoted_underlying"] == 100


def _butterfly_fund():
    held, cost = [], 0
    for strike, quantity in ((110, 1), (115, -2), (120, 1)):
        row = option(side="purchased" if quantity > 0 else "written")
        quote = options.european(True, 100., strike, .5, 0, 0, .3)[0]
        row["quote"]["price"] = quote
        row["market_value"] = quantity * quote
        row["payoff"]["option"].update(strike=strike, contracts=abs(quantity), value=quantity * quote)
        held.append(row)
        cost += quantity * quote
    specs = option_funds.inputs(Reads(), *book(*held, cash(100 - cost)), "2027-01-01")
    value, terms = option_funds.compute(specs, lambda _: None, {"down": .9, "up": .1}, ["down", "up"],
                                       lambda _: {"down": .95, "up": 1.45})
    return value, terms, cost


def test_each_contract_of_a_book_is_valued_at_its_own_volatility():
    """MATH.md 1.6: each contract's worlds average, at the weights, to its value today, at the one volatility found for
    it alone; the book is the contracts summed."""
    value, terms, cost = _butterfly_fund()
    q = {"down": .9, "up": .1}
    for c in terms["name:FUND"]["contracts"]:
        assert c["calibrated"] and c["bound"] is None
        assert abs(sum(q[w] * c["values"][w] for w in q) - c["value_today"]) < 1e-8
    book = terms["name:FUND"]
    for w in q:
        sleeve = sum(c["units_per_nav"] * c["values"][w] for c in book["contracts"])
        assert abs(value["name:FUND"][w] - (1 + sleeve - book["sleeve_today"])) < 1e-12


def test_prepared_books_do_not_read_security_prices_until_needed_and_share_calibration():
    looked, rows = book(option(side="purchased"), cash(100 - PRICE))
    rows["OTHER"] = {"net_assets": 100, "results": [option(side="purchased"), cash(100 - PRICE)]}
    specs = option_funds.inputs(Reads(), looked, rows, "2027-01-01")
    called = []
    def price(name):
        called.append(name)
        return {"down": .95, "up": 1.05}
    prepared = option_funds.prepare(specs, lambda _: None, {"down": .5, "up": .5}, ["down", "up"], price)
    assert called == [] and prepared.prices == prepared.terms == {}
    first, _ = prepared.value("name:FUND")
    assert called == ["name:SPY"] and set(prepared.terms) == {"name:FUND"}
    second, _ = prepared.value("name:OTHER")
    assert called == ["name:SPY"] and second == first
    assert prepared.value("name:FUND")[0] is first


def test_nested_option_funds_use_the_completed_reference_fund_price():
    looked, rows = book(option(side="purchased", underlying="BASE"), cash(100 - PRICE))
    rows["BASE"] = {"net_assets": 100, "results": [option(side="purchased"), cash(100 - PRICE)]}
    specs = option_funds.inputs(Reads(), looked, rows, "2027-01-01")
    def price(name):
        return prepared.value(name)[0] if name in specs else {"down": .5, "up": 1.5}
    prepared = option_funds.prepare(specs, lambda _: None, {"down": .5, "up": .5}, ["down", "up"], price)
    top, term = prepared.value("name:FUND")
    assert set(prepared.terms) == {"name:BASE", "name:FUND"}
    assert top["up"] > top["down"]
    base = prepared.prices["name:BASE"]
    c = term["contracts"][0]
    for w in ("down", "up"):
        assert c["values"][w] >= max(100 * base[w] - 100, 0)


def test_a_circular_option_fund_price_is_explained_without_recursive_evaluation():
    from harness.derive.engine import Unavailable
    specs = option_funds.inputs(Reads(), *book(option(side="purchased", underlying="FUND"), cash(100 - PRICE)),
                                "2027-01-01")
    prepared = option_funds.prepare(specs, lambda _: None, {"down": .5, "up": .5}, ["down", "up"],
                                    lambda name: prepared.value(name)[0])
    with pytest.raises(Unavailable, match="own price"):
        prepared.value("name:FUND")
