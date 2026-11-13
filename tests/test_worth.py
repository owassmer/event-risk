"""The stated consideration is a real holding value, through the store and its receipts."""
import pytest
from harness import schema
from harness.derive import impact, valuation
from harness.derive.engine import Inputs
from massive_data import put_reference, put_bars, closes_from, random_returns
from test_impact import world, event, output, channel
import numpy as np


def holding(w, ticker, price, dollars):
    put_reference(w.store, ticker, "CS")
    prices = closes_from(random_returns(np.random.default_rng(9), .01))
    last = prices[max(prices)]
    put_bars(w.store, ticker, {d: c * price / last for d, c in prices.items()})
    w.store.link("portfolio", "holds", schema.name_id(ticker), {"dollars": dollars})


def worth(w, e, n, side, value):
    return w.store.link(e, "worth", n, {"world": side, "value": {"value": value, "note": "the deal terms"}})["id"]


def test_deal_cash_example_keeps_values_and_implied_weight(tmp_path):
    w = world(tmp_path)
    holding(w, "DEAL", 27.95, 2795)
    e = event(w, p=.30)
    w.store.put_data("yield_curve", "2025", {"results": []}, {"url": "test"},
                     retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    w.store.put_data("yield_curve", "2026", {"results": [{"date": "2026-11-04", "curve": [[.5, .04]]}]},
                     {"url": "test"}, retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    lid = worth(w, e, "name:DEAL", "yes", {"cash": 35, "currency": "USD", "date": "2027-05-05"})
    worth(w, e, "name:DEAL", "no", {"amount": 23.84})
    found = output(w, e)
    y = 35 / (1 + .04 * 182 / 365)
    assert found["worth"]["name:DEAL"]["levels"] == pytest.approx({"yes": y, "no": 23.84})
    assert found["yes"] == pytest.approx(100 * (y - 27.95))
    assert found["no"] == pytest.approx(100 * (23.84 - 27.95))
    assert found["expected"] == pytest.approx(.3 * found["yes"] + .7 * found["no"])
    assert found["worth"]["name:DEAL"]["implied"] == pytest.approx((27.95 - 23.84) / (y - 23.84))
    assert w.store.engine.value("worth", lid).output["levels"] == pytest.approx({"yes": y, "no": 23.84})
    # MATH.md 1.6's illustration, at six months exactly and the rounding it shows: the curve's own convention, 35.00 / 1.02
    from harness.derive import rates
    yes = 35.00 * rates.discount([[.5, .04]], .5)
    ws = [{"id": "yes", "side": "yes", "p": .3}, {"id": "no", "side": "no", "p": .7}]
    implied = valuation.implied_from_values({"yes": yes, "no": 23.84}, 27.95, ws, {"yes": .3, "no": .7})
    assert (round(yes, 2), round(implied, 3), round(.3 * (yes - 27.95) + .7 * (23.84 - 27.95), 2)) == (34.31, .392, -.97)
    for side in ("yes", "no"):
        parts = found["parts"][side]
        assert sum(parts["factors"].values()) + sum(parts["own"].values()) + parts["curve"] == pytest.approx(found[side])


def test_cash_is_discounted_by_the_store_and_a_value_states_no_yield(tmp_path):
    """MATH.md 3.1: cash paid on a date is discounted at its currency's government yield, which the store reads; a value
    states the payment alone. The euro's curve is of spot rates, continuously compounded, so half a year at 3% is
    exp(-0.015); a currency whose curve no source here reads is not obtained, as any reading the store could not
    obtain."""
    import math
    from harness.derive.engine import Unavailable
    with pytest.raises(schema.Invalid):
        schema.worth("value", {"cash": 35, "currency": "USD", "date": "2027-05-05", "yield": 4})
    w = world(tmp_path)
    for year, rows in (("2025", []), ("2026", [{"date": "2026-11-04", "curve": [[.5, .03], [1, .03]]}])):
        w.store.put_data("yield_curve", f"EUR:{year}", {"status": "OK", "currency": "EUR", "year": year,
                                                        "basis": "spot", "results": rows},
                         {"url": "test"}, retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    readings = valuation.curve_inputs(Inputs(w.store.engine), "2026-11-04", "EUR")
    assert valuation.discount_at(readings, w.store.engine.load, "2026-11-04", .5) == pytest.approx(math.exp(-.015))
    with pytest.raises(Unavailable):
        valuation.curve_inputs(Inputs(w.store.engine), "2026-11-04", "CHF")


def test_the_company_on_its_own_is_carried_from_its_close_on_the_date(tmp_path):
    """MATH.md 1.6, 3.1: a price on an earlier date is that day's close, carried to today by the holding's betas fitted up
    to that date times the factors' moves since; today's price, which already holds those moves, is not the start."""
    import math
    w = world(tmp_path)
    holding(w, "DEAL", 27.95, 2795)
    e = event(w, p=.30)
    worth(w, e, "name:DEAL", "yes", {"amount": 35})
    from harness.derive import series
    for row in list(w.store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends')")):  # the history to the date
        w.store.put_data(row["kind"], row["subject"], w.store.data_payload(row["id"]), {"url": "test:history"},
                         retrieved_at="2026-11-05T02:00:00+00:00", range=(series.lookback("2026-08-03"), "2026-11-04"))
    worth(w, e, "name:DEAL", "no", {"date": "2026-08-03", "return": 0.0})
    anchor = output(w, e)["valuation_terms"]["worth"]["name:DEAL"]["no"]["anchor"]
    prices = closes_from(random_returns(np.random.default_rng(9), .01))
    then = {d: c * 27.95 / prices[max(prices)] for d, c in prices.items() if d <= "2026-08-03"}
    assert anchor["observed_price"] == pytest.approx(then[max(then)]) and abs(anchor["observed_price"] - 27.95) > .01
    moved = sum(anchor["raw_betas"][c] * m for c, m in anchor["factor_moves"].items())
    assert anchor["carried"] == pytest.approx(anchor["observed_price"] * math.exp(moved))


def test_received_shares_use_the_acquirers_world_price_without_placing_cash(tmp_path):
    w = world(tmp_path)
    holding(w, "DEAL", 30, 3000)
    holding(w, "BUYER", 100, 1000)
    e = event(w, p=.25)
    w.store.link(e, "consequence", "name:BUYER", {"shock": {"value": -0.2, "note": "dilution"}})
    worth(w, e, "name:DEAL", "yes", {"cash": 5, "date": "2026-11-04", "shares": {"name": "name:BUYER", "quantity": .4}})
    worth(w, e, "name:DEAL", "no", {"amount": 25})
    found = output(w, e)
    buyer_yes = 100 * .8 / .95
    assert found["worth"]["name:DEAL"]["levels"]["yes"] == pytest.approx(5 + .4 * buyer_yes)
    assert found["worth"]["name:DEAL"]["levels"]["no"] == 25
    deal = next(h for h in found["by_holding"] if h["name"] == "name:DEAL")
    assert deal["yes"] == pytest.approx(100 * (5 + .4 * buyer_yes - 30))


def test_a_worth_on_a_way_is_inherited_by_its_terminal_worlds(tmp_path):
    w = world(tmp_path)
    holding(w, "DEAL", 30, 3000)
    e = event(w, p=.5)
    first, second = w.prop("First"), w.prop("Second")
    w.link(e, "way", first, side="yes")
    w.chances(first, {"given": [e], "value": .6})
    w.link(e, "way", second, side="yes")
    worth(w, e, "name:DEAL", "yes", {"amount": 40})
    worth(w, e, "name:DEAL", "no", {"amount": 20})
    w.store.link(first, "worth", "name:DEAL", {"value": {"value": {"amount": 45}, "note": "first terms"}})
    found = output(w, e)
    assert found["worth"]["name:DEAL"]["levels"] == pytest.approx({first: 45, second: 40, "no": 20})


def government_curve(w, rate=.04):
    for year in ("2025", "2026"):
        w.store.put_data("yield_curve", year, {"results": [] if year == "2025" else [
            {"date": "2026-11-04", "curve": [[.5, rate]]}]}, {"url": "test"},
            retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))


def test_held_contracts_value_both_sides_in_actual_units_and_reconcile(tmp_path):
    w = world(tmp_path)
    government_curve(w)
    e = event(w, p=.3)
    mid = w.market("contract", "Will E occur?", .3, .4)
    w.link(e, "asks", mid)
    yes = w.store.link("portfolio", "holds", mid, {"side": "yes", "quantity": 100})["id"]
    no = w.store.link("portfolio", "holds", mid, {"side": "no", "quantity": 20})["id"]
    got = output(w, e)
    terms = {t["holding"]: t for t in got["valuation_terms"]["contracts"]}
    discount = terms[yes]["discount"]
    holdings = {h["holding"]: h for h in got["by_holding"]}
    assert holdings[yes]["worlds"] == pytest.approx({"yes": 100 * discount - 35, "no": -35})
    assert holdings[no]["worlds"] == pytest.approx({"yes": -13, "no": 20 * discount - 13})
    assert got["yes"] == pytest.approx(100 * discount - 48)
    assert got["no"] == pytest.approx(20 * discount - 48)
    for side in ("yes", "no"):
        parts = got["parts"][side]
        assert sum(parts["factors"].values()) + sum(parts["own"].values()) + parts["curve"] == pytest.approx(got[side])
    manifest = impact._impact_inputs(Inputs(w.store.engine), e)
    state = valuation.value_state(manifest, Inputs(w.store.engine).payload,
                                  got, "yes", {c: np.ones(2) for c in manifest["channels"]}, {}, {}, "2027-05-01")
    assert state[yes] == pytest.approx(np.full(2, 100 * discount))
    assert state[no] == pytest.approx(np.zeros(2))


def test_unrelated_held_contract_keeps_its_mark_in_an_event_row(tmp_path):
    w = world(tmp_path, g={"SPY": 100})
    e = event(w, p=.3)
    w.store.link(e, "consequence", channel(w, "SPY"), {"shock": {"value": -0.1, "note": "market response"}})
    other = w.prop("Unrelated", chance=.8)
    mid = w.market("other", "Will unrelated occur?", .3, .4)
    w.link(other, "asks", mid)
    hid = w.store.link("portfolio", "holds", mid, {"side": "yes", "quantity": 100})["id"]
    got = output(w, e)
    assert got["valuation_terms"]["contracts"] == []
    assert next(h for h in got["by_holding"] if h["holding"] == hid)["worlds"] == {"yes": 0, "no": 0}


def test_fixed_consideration_removes_the_same_company_noise_through_a_fund():
    looked = {"kinds": {"name:FUND": {"kind": "fund"}}, "funds": {"FUND": {"parts": {"name:CO": 1}}}}
    m = {"lookthrough": "looked", "fund_rows": "rows", "betas": "betas", "channels": [], "holds": {"h": "name:FUND"}, "values": {"h": 100}}
    centre = {"name_ratios": {"name:CO": {"yes": 1}}, "responding_ratios": {"name:CO": {"yes": 1}},
              "valuation_terms": {"worth": {"name:CO": {"yes": {"fixed": 100, "c": 100}}}}}
    got = valuation.value_state(m, lambda r: looked if r == "looked" else {}, centre, "yes", {},
                                 {"name:FUND": np.array([.8, 1.2]), "name:CO": np.array([.8, 1.2])}, {}, "2027-01-01")
    assert got["h"] == pytest.approx([100, 100])


def test_historical_sleeve_subtraction_preserves_short_expiry_and_trading_flows():
    from harness.derive.channels import option_remainder_returns
    long, short = "O:SPY270101C00100000", "O:SPY261110C00100000"
    dated = {"2026-11-01": {"net_assets": 100, "contracts": [
        {"contract": long, "quantity": 2, "value": 10}, {"contract": short, "quantity": 1, "value": 10}]},
        "2026-11-02": {"net_assets": 108, "contracts": [
        {"contract": long, "quantity": 3, "value": 18}, {"contract": short, "quantity": 1, "value": 14}]}}
    # Long position made 2*(6-5)=2, even though 3 are now held. The short-life
    # position's gain remains in the remainder, as it is not explicitly valued at the event.
    got = option_remainder_returns(dated, [(0, 1, .08)], list(dated), event_days=30)
    assert len(got) == 1
    assert got[0] == pytest.approx((0, 1, .06 / .9))


def test_expired_option_cash_uses_its_expiry_state_once():
    contract = {"kind": "call", "style": "european", "strike": 100, "expiry": "2027-01-01", "rate": 0,
                "yield": 0, "volatility": .2, "underlying_today": 100, "units_per_nav": .01,
                "reference": {"ticker": "CO", "name": "name:CO"}}
    m = {"lookthrough": "looked", "fund_rows": "rows", "betas": "betas", "channels": [], "holds": {"h": "name:FUND"}, "values": {"h": 100}}
    centre = {"valuation_terms": {"options": {"name:FUND": {"full": True, "sleeve_today": .1,
        "contracts": [contract], "asof": "2026-11-01", "shares": []}}}}
    state = valuation.value_state(m, lambda _: {}, centre, "yes", {}, {"name:CO": np.array([2., .5])}, {}, "2027-05-01",
        expiry_states={"2027-01-01": {"factors": {}, "names": {"name:CO": np.array([1.2, .8])}}})
    assert state["h"] == pytest.approx([110, 90])


def test_unaffected_company_handles_a_factor_at_zero_without_inventing_a_log_price():
    assert valuation.raw_response({"factor": 0}, {"factor": -1}) == 1
    assert valuation.raw_response({"factor": .25}, {"factor": -1}) == .75
    assert valuation.raw_response({"factor": 2}, {"factor": -1}) == 0
    m = {"lookthrough": "looked", "fund_rows": "rows", "betas": "betas", "channels": ["factor"],
         "holds": {"h": "name:CO"}, "values": {"h": 100}}
    centre = {"placed": {"yes": {"factor": -1}}, "names_set": ["name:CO"], "valuation_terms": {"worth": {
        "name:CO": {"yes": {"fixed": 0, "c": 100, "anchor": {
            "amount": 100, "raw_betas": {"factor": 0}, "state_key": "anchor:name:CO:2026-01-01"}}}}}}
    got = valuation.value_state(m, lambda _: {}, centre, "yes", {"factor": np.zeros(2)}, {}, {}, "2026-11-01")
    assert got["h"] == pytest.approx([100, 100])


def test_an_option_book_read_at_a_draw_on_the_as_of_is_each_worlds_centre():
    """One valuation, read twice (MATH.md 1.6): a book drawn at a world's centre on the as-of is worth what the world's
    row values it at, each contract at its own volatility."""
    from harness.derive import option_funds, options
    specs = {"name:BUTTERFLY": {"full": True, "asof": "2026-11-01", "cash": 1, "shares": [], "contracts": []}}
    for strike, units in ((110, .01), (115, -.02), (120, .01)):
        quote = options.value(kind="call", style="european", strike=strike, years=.5, rate=0,
                              dividend_yield=0, underlying=100, volatility=.3)
        specs["name:BUTTERFLY"]["contracts"].append({"kind": "call", "style": "european", "strike": strike,
            "years": .5, "rate": 0, "yield": 0, "underlying_today": 100, "value_today": quote,
            "units_per_nav": units, "expiry": "2027-05-01", "reference": {"ticker": "CO", "name": "name:CO"}})
    prices, terms = option_funds.compute(specs, lambda _: {}, {"low": .9, "high": .1}, ["low", "high"],
                                        lambda _: {"low": .95, "high": 1.45})
    m = {"lookthrough": "looked", "fund_rows": "rows", "betas": "betas", "channels": [], "holds": {"h": "name:BUTTERFLY"}, "values": {"h": 100}}
    centre = {"valuation_terms": {"options": terms}}
    for world, spot in (("low", .95), ("high", 1.45)):
        got = valuation.value_state(m, lambda _: {}, centre, world, {}, {"name:CO": np.full(2, spot)}, {}, "2026-11-01")
        assert got["h"] == pytest.approx(np.full(2, 100 * prices["name:BUTTERFLY"][world]), abs=1e-6)


def test_zero_consideration_removes_the_company_draw_through_a_fund():
    looked = {"kinds": {"name:FUND": {"kind": "fund"}}, "funds": {"FUND": {"parts": {"name:CO": 1}}}}
    m = {"lookthrough": "looked", "fund_rows": "rows", "betas": "betas", "channels": [], "holds": {"h": "name:FUND"}, "values": {"h": 100}}
    centre = {"name_ratios": {"name:CO": {"yes": 0}}, "responding_ratios": {"name:CO": {"yes": 1}},
              "valuation_terms": {"worth": {"name:CO": {"yes": {"fixed": 0, "c": 100}}}}}
    got = valuation.value_state(m, lambda _: looked, centre, "yes", {},
        {"name:FUND": np.array([.8, 1.2]), "name:CO": np.zeros(2), "response:name:CO": np.array([.8, 1.2])}, {}, "2027-01-01")
    assert got["h"] == pytest.approx([0, 0])


def test_an_expired_option_uses_the_targets_same_cash_consideration():
    contract = {"kind": "call", "style": "european", "strike": 100, "expiry": "2027-01-01", "rate": 0,
                "yield": 0, "volatility": .2, "underlying_today": 100, "units_per_nav": .01,
                "reference": {"ticker": "CO", "name": "name:CO"}}
    m = {"lookthrough": "looked", "fund_rows": "rows", "channels": [], "holds": {"h": "name:FUND"}, "values": {"h": 100}}
    centre = {"valuation_terms": {"options": {"name:FUND": {"full": True, "sleeve_today": .1,
        "contracts": [contract], "asof": "2026-11-01", "shares": []}},
        "worth": {"name:CO": {"yes": {"fixed": 120, "c": 100}}}}}
    got = valuation.value_state(m, lambda _: {}, centre, "yes", {}, {"name:CO": np.array([2., .5])}, {}, "2027-05-01",
        expiry_states={"2027-01-01": {"factors": {}, "names": {"name:CO": np.array([.7, 1.5])}}})
    assert got["h"] == pytest.approx([110, 110])


def test_multiple_reference_basket_keeps_unrelated_equity_outside_the_index(tmp_path):
    from rows_fixture import cash, holding as equity, on_reference
    from test_value_and_exposure import store_with, impact_of
    store = store_with(tmp_path, {"LEV": [on_reference(3, [("A", .4), ("B", .6)], "IDX"),
                                          equity("OTHER", .2), cash(1)]},
                       [{"ticker": "LEV", "dollars": 10_000}],
                       {"LEV": "ETF", "IDX": "ETF", "A": "CS", "B": "CS", "OTHER": "CS"})
    got = impact_of(store, .5, names={"OTHER": -.5})
    assert got["name_ratios"]["name:IDX"] == {"yes": 1, "no": 1}
    assert got["name_ratios"]["name:LEV"] == pytest.approx({"yes": 1 - .2 / 3, "no": 1 + .2 / 3})
    assert got["yes"] == pytest.approx(-2000 / 3)
    assert got["no"] == pytest.approx(2000 / 3)
    pid = store.links_of("portfolio", "watches")[0]["to"]
    ctx = Inputs(store.engine)
    manifest = impact._impact_inputs(ctx, pid)
    for world in ("yes", "no"):
        names = {n: np.full(2, by[world]) for n, by in got["name_ratios"].items()}
        names.update({"response:" + n: np.full(2, by[world]) for n, by in got["responding_ratios"].items()})
        values = valuation.value_state(manifest, ctx.payload, got, world,
                    {c: np.full(2, 1 + moved) for c, moved in got["placed"][world].items()}, names, {}, manifest["asof"])
        assert sum(values.values()) - got["V"] == pytest.approx(np.full(2, got[world]))
    store.close()


def test_a_fund_its_contracts_value_stated_whole_is_valued_from_its_terms_and_waits_on_their_reads(tmp_path,
                                                                                                     monkeypatch):
    """MATH.md 1.6: a fund whose contracts value it, stated whole, stands in that world at the value its terms give it in
    the baseline times 1 + s, and in every other world at what its terms give, so its option model is read; with
    the Treasury's curve not yet read, the row waits on it, and no world falls to its betas meanwhile."""
    from rows_fixture import cash, holding as equity
    from test_value_and_exposure import store_with, impact_of
    option = {"net_assets": 100, "market_value": -5, "report_date": "2026-11-03", "exposure": -.5,
              "kind": "derivative", "payoff": {"option": {
                  "right": "call", "style": "european", "side": "written", "strike": 100,
                  "expiry": "2027-05-01", "contracts": 1, "underlying_per_contract": 1,
                  "value": -5, "symbol": "O:AAA270501C00100000"}},
              "quote": {"underlying": "AAA", "price": 5, "underlying_price": 100,
                        "at": "2026-11-03T21:00:00+00:00", "delta": .5},
              "to": {"company": {"constituent_ticker": "AAA"}}}
    store = store_with(tmp_path, {"FUND": [option, equity("OTHER", .3), cash(.75)]},
                       [{"ticker": "FUND", "dollars": 1000}], {"FUND": "ETF", "AAA": "CS", "OTHER": "CS"})
    asked = []
    read = Inputs.data
    def recorded(self, kind, *args, **kwargs):
        asked.append(kind)
        return read(self, kind, *args, **kwargs)
    monkeypatch.setattr(Inputs, "data", recorded)
    got = impact_of(store, .5, names={"FUND": -.25})
    assert "yield_curve" in asked  # its terms read
    assert got is None  # its row waits on what its terms read, and no world stands at its betas meanwhile
    store.close()
