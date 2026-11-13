"""The forecast is the valued joint state, with the mixture's actual horizon means."""

import math

import numpy as np
import pytest

from harness.derive import estimate
from harness.derive.distribution import (append_dated_residual, completed_covariance, factorization,
                                         state_covariance, summarize, weighted_quantiles)


def test_full_state_is_the_actual_completed_covariance_including_cross_lags():
    # The counterexample that defeats subtracting a same-observation rank-one
    # covariance from a Bartlett covariance.
    x = np.array([(1, 1), (-1, 1), (1, -1), (-1, -1)] * 2, dtype=float)
    names, covariance, cohorts = state_covariance(x[:, :1], {"name:A": x[:, 1]}, memory=5,
                                                 own_lean=1, kappa=4)
    expected = estimate.covariances(x, np.ones(8, dtype=bool), 5)[2] / estimate.centring(np.ones(8, bool), 5)
    assert names == ["name:A"]
    assert covariance == pytest.approx(expected, abs=1e-12)
    assert covariance[0, 1] != 0
    root = factorization(covariance)
    assert root @ root.T == pytest.approx(covariance, abs=1e-12)
    assert cohorts[0]["method"] == "joint completed covariance"


def test_new_listing_keeps_the_existing_states_history_and_positive_covariance():
    rng = np.random.default_rng(61)
    f = rng.normal(size=(180, 2)) * 0.01
    older = 0.4 * f[:, 0] + rng.normal(size=180) * 0.012
    recent = np.full(180, np.nan)
    recent[-15:] = 0.8 * older[-15:] + rng.normal(size=15) * 0.003
    _, before, _ = state_covariance(f, {"name:OLD": older}, memory=5, own_lean=1, kappa=4)
    names, after, details = state_covariance(f, {"name:OLD": older, "name:NEW": recent}, memory=5,
                                            own_lean=1, kappa=4)
    assert names == ["name:OLD", "name:NEW"]
    assert after[:3, :3] == pytest.approx(before, abs=1e-14)
    assert after[2, 3] > 0  # their shared own movement survives
    assert np.linalg.eigvalsh(after).min() >= -1e-12
    assert details[-1]["sessions"] == 15
    assert details[-1]["method"] == "conditional history extension"


def test_no_residual_history_has_the_existing_one_session_market_prior():
    f = np.random.default_rng(3).normal(size=(100, 1)) * 0.01
    _, covariance, details = state_covariance(f, {"name:NEW": np.full(100, np.nan)}, memory=5,
                                             own_lean=1, kappa=4)
    assert covariance[1, 1] == pytest.approx(covariance[0, 0])
    assert covariance[0, 1] == 0
    assert details[-1]["method"] == "market prior"


def test_covariance_reuse_is_exact_and_cannot_hide_history_or_setting_changes(monkeypatch):
    from harness.derive import distribution
    rng = np.random.default_rng(1981)
    factors = rng.normal(size=(70, 2)) * .01
    own = {"name:A": rng.normal(size=70) * .01, "name:B": rng.normal(size=70) * .02}
    own["name:B"][:40] = np.nan
    original = distribution._state_covariance
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(distribution, "_state_covariance", counted)
    settings = dict(memory=5, own_lean=1., kappa=4.)
    first = state_covariance(factors, own, **settings)
    expected = first[1].copy()
    first[0].append("mutated")
    first[1][:] = 0
    first[2][0]["method"] = "mutated"
    again = state_covariance(factors.copy(), dict(reversed(list(own.items()))), **settings)
    assert len(calls) == 1
    assert np.array_equal(again[1], expected)
    assert "mutated" not in again[0]
    assert again[2][0]["method"] != "mutated"
    own["name:B"][-1] += .01
    changed = state_covariance(factors, own, **settings)
    assert len(calls) == 2
    assert not np.array_equal(changed[1], expected)
    state_covariance(factors, own, **(settings | {"own_lean": 2.}))
    assert len(calls) == 3


def test_dated_company_residual_keeps_measured_factor_dependence_and_own_risk():
    historical = np.array([[4.0, 1.0], [1.0, 3.0]])
    same = append_dated_residual(historical[:1, :1], historical, 1)
    assert same == pytest.approx(historical)
    # The factor's variance now differs. Its residual loading is still 1/4,
    # and the residual's own variance is still 3 - 1/4, from its unaffected history.
    now = append_dated_residual(np.array([[8.0, 2.0], [2.0, 5.0]]), historical, 1)
    assert now[:2, :2] == pytest.approx(np.array([[8.0, 2.0], [2.0, 5.0]]))
    assert now[-1] == pytest.approx([2.0, 0.5, 3.25])
    assert np.linalg.eigvalsh(now).min() > 0


def test_covariance_after_cell_demeaning_is_measured_not_rank_one_subtracted():
    x = np.array([(1, 1), (-1, 1), (1, -1), (-1, -1)] * 2, dtype=float)
    residual = x.copy()
    for label in (-1, 1):
        mask = x[:, 1] == label
        residual[mask] -= x[mask].mean(axis=0)
    ordinary = completed_covariance(x, np.ones(8, bool), 5)
    within = completed_covariance(residual, np.ones(8, bool), 5)
    assert np.linalg.det(ordinary - within) < 0
    root = factorization(within)
    assert root @ root.T == pytest.approx(within)


def test_mixture_variance_uses_valued_horizon_means_and_keeps_each_width():
    samples = {"yes": np.array([5.0, 7.0, 9.0]), "no": np.array([-3.0, -2.0, -1.0])}
    worlds = {"yes": {"p": 0.3, "side": "yes", "A": 1.0},
              "no": {"p": 0.7, "side": "no", "A": -1.0}}
    got = summarize(samples, worlds)
    mean = 0.3 * 7 + 0.7 * -2
    between = 0.3 * (7 - mean) ** 2 + 0.7 * (-2 - mean) ** 2
    within = 0.3 * 8 / 3 + 0.7 * 2 / 3
    assert got["mean"] == pytest.approx(mean)
    assert got["variance"] == pytest.approx({"between": between, "within": within, "total": between + within})
    assert got["variance"]["total"] == pytest.approx(between + within)
    assert got["worlds"]["yes"]["centre"] == 1.0
    worlds["yes"]["p"], worlds["no"]["p"] = 0.8, 0.2
    changed = summarize(samples, worlds)
    assert changed["worlds"]["yes"]["variance"] == got["worlds"]["yes"]["variance"]
    assert changed["worlds"]["no"]["variance"] == got["worlds"]["no"]["variance"]
    assert sum(got["bins"]["mass"]) == pytest.approx(1)
    assert np.sum([w["mass"] for w in got["worlds"].values()], axis=0) == pytest.approx(got["bins"]["mass"])


def test_weighted_quantiles_count_a_point_payment_at_its_actual_mass():
    values = np.array([10, 10, 10, -5, 0, 5], dtype=float)
    weights = np.array([0.2, 0.2, 0.2, 0.4 / 3, 0.4 / 3, 0.4 / 3])
    got = weighted_quantiles(values, weights)
    assert got["0.5"] == 10
    assert got["0.1"] == -5


def test_store_exposes_an_actual_horizon_distribution_and_its_holding_means(tmp_path):
    from test_impact import world, event, channel
    from harness.derive import distribution
    w = world(tmp_path, g={"SPY": 10_000.0})
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    got = w.store.engine.value("distribution", e)
    assert got.output is not None, got.cause
    out = got.output
    assert out["horizon_end"] == w.store.horizon_end()
    assert out["sample_count"] == distribution.SAMPLES
    assert out["variance"]["total"] == pytest.approx(out["variance"]["between"] + out["variance"]["within"])
    for world_id, row in out["worlds"].items():
        assert row["variance"] > 0
        assert sum(out["holding_means"][world_id].values()) == pytest.approx(row["mean"])
    assert w.store.get(e)["derived"]["distribution"]["value"]["variance"] == out["variance"]
    assert "covariance" not in out  # the state's covariance is not copied into each event's distribution


def test_probability_only_update_mixes_the_same_worlds_at_fixed_pricing_weights(tmp_path, monkeypatch):
    from harness.derive import distribution
    from test_impact import world, event, channel
    w = world(tmp_path, g={"SPY": 10_000.0})
    e = event(w, p=0.3, priced_in={"value": 0.4})
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.15)
    before = w.store.engine.value("distribution", e).output
    original = distribution._state_uncached
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(distribution, "_state_uncached", counted)
    w.chances(e, {"value": 0.7}, keep=False)
    after = w.store.engine.value("distribution", e).output
    for wid in before["worlds"]:
        for key in ("mean", "variance", "centre", "quantiles"):
            assert after["worlds"][wid][key] == before["worlds"][wid][key]
    assert after["mean"] != before["mean"]
    assert not calls
    other = event(w, title="Another event", p=.4)
    w.link(other, "consequence", channel(w, "SPY"), shock=0.1)
    assert w.store.engine.value("distribution", other).output is not None
    assert not calls  # A new event reuses the state, with its own centres and values.


def test_share_consideration_and_direct_holding_use_one_company_draw(tmp_path):
    from test_impact import world, event
    from test_worth import holding, worth
    w = world(tmp_path)
    holding(w, "DEAL", 30, 3000)  # 100 target shares receive 40 buyer shares.
    holding(w, "BUYER", 100, -4000)
    e = event(w, p=.3)
    worth(w, e, "name:DEAL", "yes", {"cash": 5, "date": "2026-11-04",
                                      "shares": {"name": "name:BUYER", "quantity": .4}})
    worth(w, e, "name:DEAL", "no", {"amount": 25})
    got = w.store.engine.value("distribution", e)
    assert got.output is not None, got.cause
    # The buyer shares received and sold short cancel in every joint price state.
    # Only the known $500 payment remains, against today's net -$1,000 holding.
    yes = got.output["worlds"]["yes"]
    assert yes["mean"] == pytest.approx(1500)
    assert yes["variance"] == pytest.approx(0, abs=1e-9)
    assert list(yes["quantiles"].values()) == pytest.approx([1500] * len(yes["quantiles"]))
    assert got.output["worlds"]["no"]["variance"] > 0


def test_each_worlds_mean_at_the_horizon_is_its_centre_where_every_holding_moves_in_proportion_to_a_price(tmp_path):
    """MATH.md 1.8, zero drift: beyond a world's shocks every price's move to the horizon has a mean of zero in today's
    dollars, so where each holding's value is in proportion to a price, each world's mean at the horizon is its centre
    today. A share carrying the market, a deal that pays cash and another name's shares, that name shocked on its own,
    and in the other world the company on its own, carried from its close on a date by the factors' moves since: each
    world's mean is its centre to the cent, on the draws themselves."""
    from harness.derive import series
    from test_impact import world, event, channel
    from test_worth import holding, worth
    w = world(tmp_path, g={"SPY": 10_000.0})
    holding(w, "DEAL", 27.95, 2795)
    holding(w, "BUYER", 100, 1000)
    e = event(w, p=.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    w.store.link(e, "consequence", "name:BUYER", {"shock": {"value": -0.2, "note": "dilution"}})
    worth(w, e, "name:DEAL", "yes", {"cash": 5, "date": "2026-11-04", "shares": {"name": "name:BUYER", "quantity": .3}})
    for row in list(w.store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends')")):  # the history to the date
        w.store.put_data(row["kind"], row["subject"], w.store.data_payload(row["id"]), {"url": "test:history"},
                         retrieved_at="2026-11-05T02:00:00+00:00", range=(series.lookback("2026-08-03"), "2026-11-04"))
    worth(w, e, "name:DEAL", "no", {"date": "2026-08-03", "return": 0.0})
    got = w.store.engine.value("distribution", e)
    assert got.output is not None, got.cause
    for wid, world_ in got.output["worlds"].items():
        assert world_["variance"] > 0
        assert world_["mean"] == pytest.approx(world_["centre"], abs=0.005), wid


def test_a_listing_abroad_has_its_centre_as_its_mean_at_the_horizon(tmp_path):
    """A listing in won and a fund quoted in won, each moving by its local price and its currency together (1.6): their
    dollar price is drawn as one, so each world's mean at the horizon is its centre (1.8)."""
    from rows_fixture import holding
    from test_impact import _abroad_store, stated
    store, e, fund, abroad = _abroad_store(
        tmp_path, "scratch-drift", lambda f, a: [{"ticker": a, "dollars": 10000.0}, {"ticker": f, "dollars": 5000.0}],
        lambda f, a: {f: [holding(a, 0.6), holding("AAA", 0.4)]})
    won = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "C:KRWUSD")
    for target, move in ((f"name:{abroad}", 0.2), (won, -0.2)):
        store.link(e, "consequence", target, {"shock": stated(move)})
    got = store.engine.value("distribution", e)
    assert got.output is not None, got.cause
    for wid, world_ in got.output["worlds"].items():
        assert world_["variance"] > 0
        assert world_["mean"] == pytest.approx(world_["centre"], abs=0.005), wid
    store.close()


def test_held_contracts_share_their_logical_draw_and_keep_within_world_variance(tmp_path):
    from test_impact import world, event
    w = world(tmp_path)
    e = event(w, p=.5)
    narrow = w.prop("A related development", chances=[
        {"given": [e], "value": .25}, {"given_not": [e], "value": .10}])
    market = w.market("contract", "A related development?", .3, .4)
    w.link(narrow, "asks", market, taken={"value": False, "note": "The conditional forecast is our view."})
    for year, values in (("2025", []), ("2026", [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", year, {"results": values}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    held = w.store.link("portfolio", "holds", market, {"side": "yes", "quantity": 100})["id"]
    first = w.store.engine.value("distribution", e)
    assert first.output is not None, first.cause
    manifest = w.store.computation(first.basis)["inputs"]["valuation"]
    payout = 100 * next(c["discount"] for c in manifest["contracts"] if c["holding"] == held)
    assert first.output["worlds"]["yes"]["variance"] == pytest.approx(payout ** 2 * .25 * .75)
    assert first.output["worlds"]["no"]["variance"] == pytest.approx(payout ** 2 * .1 * .9)
    # Equal Yes and No positions pay exactly one dollar together in every atom.
    # Independent Bernoulli draws would incorrectly give them positive variance.
    w.store.link("portfolio", "holds", market, {"side": "no", "quantity": 100})
    both = w.store.engine.value("distribution", e)
    assert both.output is not None, both.cause
    assert both.output["variance"]["total"] == pytest.approx(0.0, abs=1e-9)
    assert both.output["variance"]["within"] == pytest.approx(0.0, abs=1e-12)


def test_unrelated_expiring_contract_has_its_stated_probability_at_the_horizon(tmp_path):
    from test_impact import world, event, channel
    from test_worth import government_curve
    w = world(tmp_path)
    e = event(w, p=.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    other = w.prop("Unrelated", chance=.8)
    market = w.market("other", "Will unrelated occur?", .3, .4)
    w.link(other, "asks", market, taken={"value": False, "note": "Use the stated forecast."})
    hid = w.store.link("portfolio", "holds", market, {"side": "yes", "quantity": 100})["id"]
    government_curve(w)
    row = w.store.engine.value("impact", e)
    assert row.output is not None, row.cause
    assert next(h for h in row.output["by_holding"] if h["holding"] == hid)["worlds"] == {"yes": 0, "no": 0}
    got = w.store.engine.value("distribution", e)
    assert got.output is not None, got.cause
    manifest = w.store.computation(got.basis)["inputs"]["valuation"]
    payout = 100 * next(c["discount"] for c in manifest["contracts"] if c["holding"] == hid)
    for side in ("yes", "no"):
        assert got.output["worlds"][side]["mean"] == pytest.approx(payout * .8 - 35)
        assert got.output["worlds"][side]["variance"] == pytest.approx(payout ** 2 * .8 * .2)
    assert got.output["variance"]["between"] == pytest.approx(0, abs=1e-12)
    assumed = got.output["contract_receipt"]["probability_assumptions"]
    assert next(a for a in assumed if a["of"] == other)["given"] == []
    assert next(a for a in assumed if a["of"] == other)["reading"] == "independent beyond these statements"


def test_unrelated_expiring_contract_without_probability_keeps_row_but_not_false_forecast(tmp_path):
    from test_impact import world, event, channel
    w = world(tmp_path)
    e = event(w, p=.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    other = w.prop("Unrelated")
    market = w.market("other", "Will unrelated occur?", .3, .4)
    w.link(other, "asks", market, taken={"value": False, "note": "The quote is not a forecast."})
    w.store.link("portfolio", "holds", market, {"side": "yes", "quantity": 100})
    row = w.store.engine.value("impact", e)
    assert row.output is not None, row.cause
    got = w.store.engine.value("distribution", e)
    assert got.output is None
    assert "probability" in str(got.cause).lower() or "chance" in str(got.cause).lower()


def test_a_names_residual_is_read_once_for_every_events_state_that_reads_it(tmp_path, monkeypatch):
    """MATH.md 1.8: the state's covariance is the portfolio's, over the factors and the holdings' own parts, and an
    event's state differs from another's only by what it adds of its own (a fund's remainder where the event's date
    decides which of its contracts outlive it). Each name's residual is kept by what it follows from, so an event's
    state worked out again reads no series twice, and gives the same state, bit for bit."""
    from test_impact import world, event, channel
    from harness.derive import distribution, history
    from harness.derive.engine import Inputs
    w = world(tmp_path, g={"SPY": 10_000.0, "TLT": 5_000.0})
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    with w.store.lock:
        m = distribution._inputs(Inputs(w.store.engine), e)
    load = w.store.engine.read
    read = []
    returns = history.returns
    monkeypatch.setattr(history, "returns", lambda *a, **k: read.append(1) or returns(*a, **k))
    distribution._RESIDUALS.clear()
    first = distribution._state_uncached(m, load)
    assert read  # each holding's series read once
    count = len(read)
    again = distribution._state_uncached(m, load)  # as an event whose state is worked out again
    assert len(read) == count
    assert np.array_equal(first[1], again[1]) and first[0] == again[0]


def test_a_states_rows_draw_each_price_as_its_loading_through_the_changes():
    """MATH.md 1.8: each price is drawn as its loading through the state's changes, its response to the factors and its own
    part, all prices at once; a company held under two ids is one row and one draw, and a factor's proxy loads on its
    factor alone. Each row's log-variance is its loading through the covariance, and the state is factorized once."""
    from harness.derive import distribution
    rng = np.random.default_rng(5)
    g = rng.normal(size=(5, 5))
    covariance = g @ g.T / 5
    company, fund, proxy = np.array([1.2, 0.3, 1.0, 0, 0]), np.array([0.8, -0.1, 0, 1.0, 0]), np.array([1.0, 0, 0, 0, 0])
    remainder = np.array([0.1, 0.2, 0, 0, 1.0])
    linear = {"name:ABC": company, "ABC": company, "name:FND": fund, "name:SPY": proxy, "remainder:name:FND": remainder}
    state = distribution.State({"channels": ["factor:1", "factor:2"]}, covariance, linear, {})
    assert state.rows["name:ABC"] == state.rows["ABC"] and len(set(state.rows.values())) == 4
    changes = rng.normal(size=(64, 5))
    moves = state.log_moves(changes)
    for n, a in linear.items():
        assert moves[state.rows[n]] == pytest.approx(changes @ a, abs=1e-12)
        assert state.log_variance[state.rows[n]] == pytest.approx(a @ covariance @ a, abs=1e-12)
    assert state.root @ state.root.T == pytest.approx(covariance, abs=1e-12)


def test_a_fund_valued_by_its_terms_with_a_statement_on_it_is_drawn_by_its_terms_times_what_it_states(tmp_path):
    """MATH.md 1.8, 1.6: each holding is valued at each draw by its one valuation. A fund on one reference stated -10%
    against what it follows if the event happens is worth there, at every draw, what its terms give at its reference's
    draw, times 0.9, as the row values its centre; where nothing is stated it is as unstated. The portfolio is the fund
    alone, and the draws are the same with the statement and without it."""
    import pathlib
    from breakdown import stated
    from harness import schema
    from rows_fixture import cash, on_reference
    from test_value_and_exposure import store_with
    means: dict = {}
    for says in (False, True):
        store = store_with(pathlib.Path(tmp_path) / str(says),
                           {"LEV": [on_reference(3.0, [("A", 0.4), ("B", 0.6)], "IDX"), cash(1.0)]},
                           [{"ticker": "LEV", "dollars": 10_000.0}], {"LEV": "ETF", "IDX": "ETF", "A": "CS", "B": "CS"},
                           seed=7)
        try:
            e = store.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                          "window_end": "2027-03-31", "chances": [stated(0.3)], "baseline": "no"})["id"]
            store.link("portfolio", "watches", e)
            spy = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "SPY")
            store.link(e, "consequence", spy, {"shock": stated(-0.2)})
            if says:
                store.link(e, "consequence", "name:LEV", {"shock": {"value": -0.1, "against": schema.FOLLOWS,
                                                                     "note": "Beyond its terms."}})
            worlds = store.engine.value("distribution", e).output["worlds"]
            means[says] = {w: (10_000.0 + worlds[w]["mean"], worlds[w]["variance"]) for w in ("yes", "no")}
        finally:
            store.close()
    (yes, yes_var), (no, no_var) = means[True]["yes"], means[True]["no"]
    assert yes == pytest.approx(0.9 * means[False]["yes"][0], rel=1e-9)
    assert yes_var == pytest.approx(0.81 * means[False]["yes"][1], rel=1e-9)  # every draw 0.9 of its terms', not
    assert (no, no_var) == pytest.approx(means[False]["no"], rel=1e-9)  # the fund's own draws around its centre
