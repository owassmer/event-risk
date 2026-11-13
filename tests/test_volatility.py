"""The portfolio's volatility to the horizon's end (MATH.md 3.2): one figure, the channels' part and the holdings' own
parts taken together over completed moves, a holding listed within the window joining those before it as it has moved
with them, leaning while its history is short.

The rule's arithmetic (estimate.joined) is proven by hand at no memory. The derivation is checked through production's
fits on synthetic histories with known parts: with every holding on the whole window it is the joint estimate, its
sum taken jointly with the channels, and a recent listing, two young
holdings in one industry, and a holding younger than the estimator's memory each join as the rule says, the
established holdings keeping their whole window.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from harness.derive import default_registry, estimate, history
from harness.derive.channels import model
from harness.store import Store
from massive_data import ASOF, KNOWLEDGE, SESSIONS, closes_from, put_bars, put_reference

DAYS = SESSIONS[1:]


# ---------------------------------------------------------------- the rule, by hand


def test_a_part_joins_the_total_as_it_moved_with_it_and_by_its_own_leaning_as_its_fit_does():
    """At no memory the covariance of completed moves is the same session's: its centring keeps 1 - 1/n of it
    (Bessel's correction) and it has n - 1 degrees of freedom. The total moves 1, -1, 1, -1 and the part 0.5, -0.5,
    0.5, -0.5: over 4 sessions, variance 1, 0.25 and covariance 0.5 as the estimator keeps them, 4/3, 1/3 and 2/3 as
    the sessions say them, on 3 degrees of freedom. With its co-movement held at none (kappa infinite), its own risk
    is the part's whole variance leaning one session toward 0.2, (0.2 + 3 * 1/3) / 4 = 0.3, and the total's 2 becomes
    2.3. At kappa 4, the co-movement's prior is 4 * 2 / 0.2 = 40 at the total's variance over its window, and the fit
    is the fixed point: precision 3 * 4/3 / sigma^2 + 40, beta its share of 3 * 2/3 / sigma^2, and sigma^2 the part's
    residual and the co-movement's uncertainty on its 3 degrees of freedom, leaning one session toward 0.2; the part
    adds sigma^2 and that uncertainty on the total's 2."""
    total = np.array([1.0, -1.0, 1.0, -1.0, np.nan])
    part = np.array([0.5, -0.5, 0.5, -0.5, 3.0])  # the last session the total has no move for: not one both have
    held = estimate.joined(2.0, total, part, 0, 0.2, 1.0, float("inf"))
    assert held == {"variance": pytest.approx(2.3), "beta": 0.0, "own": pytest.approx(0.3), "sessions": 4}
    got = estimate.joined(2.0, total, part, 0, 0.2, 1.0, 4.0)
    a, c, d, beta = 4 / 3, 2 / 3, 1 / 3, got["beta"]
    precision = 40.0 + 3 * a / (sigma2 := (0.2 + 3 * (d - 2 * beta * c + beta ** 2 * a + a / (3 * a / 0.3 + 40))) / 4)
    for _ in range(200):  # the fixed point, by its own equations
        precision = 3 * a / sigma2 + 40.0
        b = (3 * c / sigma2) / precision
        sigma2 = (0.2 + 3 * (d - 2 * b * c + b * b * a + a / precision)) / 4
    assert beta == pytest.approx(b) and 0.0 < beta < 0.5
    assert got["own"] == pytest.approx(sigma2 + 2.0 / precision)
    assert got["variance"] == pytest.approx((1 + beta) ** 2 * 2.0 + got["own"]) and got["sessions"] == 4


def test_a_part_with_no_session_beside_the_total_or_one_is_its_prior_alone():
    got = estimate.joined(2.0, np.array([1.0, -1.0]), np.full(2, np.nan), 5, 0.2, 1.0, 4.0)
    assert got == {"variance": pytest.approx(2.2), "beta": 0.0, "own": pytest.approx(0.2), "sessions": 0}
    one = estimate.joined(2.0, np.array([1.0, -1.0]), np.array([np.nan, 0.7]), 5, 0.2, 1.0, 4.0)
    assert one == {"variance": pytest.approx(2.2), "beta": 0.0, "own": pytest.approx(0.2), "sessions": 1}
    # a total that neither moved over the part's sessions nor has a variance, as the own parts' total begins: nothing
    # to fit the part on, so its own risk is what its sessions say, leaning toward its prior
    part = np.array([0.5, -0.5, 0.25, -0.25])
    first = estimate.joined(0.0, np.zeros(4), part, 0, 0.2, 1.0, 4.0)
    said = float(np.var(part, ddof=1))
    assert first == {"variance": pytest.approx((3 * said + 0.2) / 4), "beta": 0.0,
                     "own": pytest.approx((3 * said + 0.2) / 4), "sessions": 4}


def test_the_centring_keeps_what_a_few_sessions_hold_of_a_variance_and_dividing_by_it_takes_them_as_they_say():
    """centring by hand: over three sessions at five of memory, (1 - 1/3) - (2/9) * (5/6 * 2 + 4/6 * 1) = 4/27; at
    no memory, 1 - 1/n; under two sessions, none. freedom: n - 1 at no memory, Bessel's; 1.6 over three sessions at
    five. And over independent moves, with sessions missing inside the window, the covariance of completed moves
    divided by what the centring keeps is the variance in expectation, and it varies as a variance on freedom's
    degrees of freedom does, 2 sigma^4 / nu."""
    assert estimate.centring(np.ones(3, bool), 5) == pytest.approx(4 / 27)
    assert estimate.centring(np.ones(4, bool), 0) == pytest.approx(0.75)
    assert estimate.centring(np.ones(1, bool), 5) == 0.0 == estimate.freedom(np.ones(1, bool), 5)
    assert estimate.freedom(np.ones(4, bool), 0) == pytest.approx(3.0)
    assert estimate.freedom(np.ones(3, bool), 5) == pytest.approx(1.6)
    rng = np.random.default_rng(4401)
    for n in (5, 12, 30):
        ok = np.ones(n, bool)
        ok[[1, n // 2]] = False
        kept, nu = estimate.centring(ok, 5), estimate.freedom(ok, 5)
        # 20,000 independent series, 500 to a call: each one's own variance is its column's, as it is alone
        got = np.concatenate([np.diag(estimate.covariances(rng.standard_normal((n, 500)), ok, 5)[2])
                              for _ in range(40)]) / kept
        assert np.mean(got) == pytest.approx(1.0, abs=0.02)
        assert np.var(got) == pytest.approx(2.0 / nu, rel=0.06)


def test_a_listing_that_doubled_on_its_second_session_takes_its_own_moves_and_almost_no_co_movement():
    """Three sessions, the market quiet, the listing's own moves 1%, 100%, -2%: what three sessions measure of its
    co-movement with the market is that one doubling over a 0.3% market move, and its own variability is hundreds of
    times the market's, so its co-movement leans as a response would, kappa sessions of a series as variable as the
    market being thousands of its own: next to none of it is taken, and its figure is its own variability."""
    x = np.zeros((6, 1))
    x[:, 0] = [0.004, -0.003, 0.002, 0.001, 0.003, -0.002]
    ok = np.ones(6, bool)
    omega = np.array([[0.0001]])
    session = np.array([[1.0], [0.0]])
    e = np.array([np.nan, np.nan, np.nan, 0.01, 1.0, -0.02])
    got = estimate.figure(x, ok, omega, {"h": (1.0, session, e)}, memory=5, lean=1.0, kappa=4.0)
    [part] = got["parts"]
    assert abs(part["beta"]) < 0.01 and part["with"] < 0.01 * part["own"]
    young = np.array([False, False, False, True, True, True])
    sessions = estimate.covariances(e[3:, None], np.ones(3, bool), 5)[2][0, 0] / estimate.centring(young, 5)
    nu, prior = estimate.freedom(young, 5), 0.0001 / estimate.centring(ok, 5)
    assert part["own"] == pytest.approx((nu * sessions + prior) / (nu + 1), rel=0.02)
    assert 0.1 < got["daily_sd"] < 0.6  # a listing that doubles on its second day: wild, not a 200% daily move


# ---------------------------------------------------------------- the derivation, through production's fits


def _store(tmp_path, holdings: dict[str, tuple[float, dict[str, float]]], market: dict[str, float]) -> Store:
    """A store of the market and these holdings, each its dollars and its returns by session."""
    store = Store.create(tmp_path / "scratch-volatility", registry=default_registry())
    store.load_initial(KNOWLEDGE)
    put_bars(store, "SPY", closes_from(market))
    for t, (_, returns) in holdings.items():
        put_reference(store, t, "CS")
        put_bars(store, t, closes_from(returns))
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": t, "dollars": v} for t, (v, _) in holdings.items()]})
    return store


def _case(seed: int, young: dict[str, int], industry: tuple[str, ...] = (), full: int = 4) -> tuple:
    """The market, `full` holdings with the whole window, and each young one listed this many sessions before the
    as-of: each a beta of 1 on the market plus its own move, the `industry` ones sharing most of theirs."""
    rng = np.random.default_rng(seed)
    n = len(DAYS)
    market = rng.normal(0, 0.01, n)
    shared = rng.normal(0, 0.015, n)
    out = {}
    for i in range(full):
        out[f"H{i}"] = (10_000.0, dict(zip(DAYS, market + rng.normal(0, 0.015, n))))
    for t, sessions in young.items():
        own = 0.8 * shared + 0.6 * rng.normal(0, 0.015, n) if t in industry else rng.normal(0, 0.015, n)
        out[t] = (10_000.0, dict(zip(DAYS[-sessions:], (market + own)[-sessions:])))
    return out, dict(zip(DAYS, market))


def _residuals(store) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """What the derivation reads, rebuilt here as the evaluation built its state (worlds.state): the channels'
    returns, the holdings' full responses in dollars, and each holding's residual on each session it covers alone."""
    engine = store.engine
    mdl = model(engine.value("factors", "market").output)
    fits = engine.value("betas", "portfolio").output["fits"]
    holds = engine.value("holdings", "portfolio").output["holds"]
    cid = engine.value("volatility", "portfolio").basis
    inputs = json.loads(store.db.execute("SELECT inputs FROM computation WHERE id = ?", (cid,)).fetchone()[0])
    s, e = np.zeros(mdl.k), {}
    for hid, row in holds.items():
        session = np.array(fits[row["name"]]["session"]).reshape(2, mdl.k)
        s += row["dollars"] * session.sum(axis=0)
        r = np.full(mdl.x.shape[0], np.nan)
        for i, j, ret in history.returns(inputs["series"][row["name"]], engine.load, mdl.sessions)["dollar"]:
            if j == i + 1 and i >= 1 and mdl.ok[i] and mdl.ok[i - 1]:
                r[i] = ret - session[0] @ mdl.x[i] - session[1] @ mdl.x[i - 1]
        e[row["name"]] = row["dollars"] * r
    return mdl.x, s, mdl, e


def test_with_every_holding_on_the_whole_window_it_is_the_joint_estimate_as_its_sessions_say_it(tmp_path):
    """Four holdings with the whole window: the channels' part over all of it, s' Omega s, and the holdings' own part
    and its co-movement with the channels from one call of the estimator on [s . x, sum_h V_h e_h] over the sessions
    the residuals cover, A + B + 2C (the evaluation's form, 47m), each covariance taken as the sessions say it, divided
    by what the estimator's centring keeps of it over a full window (0.988). The rule takes them as one part joining
    the channels, its co-movement leaning as a fit's response does on the degrees of freedom completed moves carry
    (about a quarter of the sessions) and adding its uncertainty: within a part in a hundred of the joint estimate,
    two to three parts in a hundred above the estimator as it keeps them; wider than the holdings' own parts taken as
    independent."""
    held, market = _case(1, {})
    store = _store(tmp_path, held, market)
    out = store.engine.value("volatility", "portfolio").output
    x, s, mdl, e = _residuals(store)
    own = sum(e.values())
    both = ~np.isnan(own)
    _, _, kept = estimate.covariances(np.column_stack([np.nan_to_num(x) @ s, own]), both, 5)
    window, sessions = estimate.centring(mdl.ok, 5), estimate.centring(both, 5)
    m, omega = kept / sessions, mdl.omega / window
    assert 1.0 / window == pytest.approx(1.012, abs=0.001) and 1.0 / sessions == pytest.approx(1.012, abs=0.001)
    joint = float(s @ omega @ s) + m[1, 1] + 2 * m[0, 1]
    assert out["daily_sd"] ** 2 == pytest.approx(joint, rel=0.01)
    as_kept = float(s @ mdl.omega @ s) + kept[1, 1] + 2 * kept[0, 1]
    assert 1.01 < out["daily_sd"] ** 2 / as_kept < 1.03
    assert out["variance"]["channels"] == pytest.approx(float(s @ omega @ s))
    # the three terms of MATH.md 3.2: the channels' part, the own parts together, what the two share
    nu = estimate.freedom(both, 5)
    prior = float(omega[0, 0]) * 4 * 10_000.0 ** 2
    assert out["variance"]["own"] == pytest.approx((nu * m[1, 1] + prior) / (nu + 1))
    # what the two share: the co-movement as the sessions measure it, leaning toward none as a response would on the
    # degrees of freedom completed moves carry, with that co-movement's uncertainty on the channels' variance
    [part] = out["parts"]
    assert np.sign(out["variance"]["shared"]) == np.sign(m[0, 1])
    assert 0.7 * abs(2 * m[0, 1]) < abs(out["variance"]["shared"]) < abs(2 * m[0, 1])
    assert sum(out["variance"].values()) == pytest.approx(out["daily_sd"] ** 2)
    assert part["sessions"] == int(both.sum()) == int(mdl.ok.sum()) - 1  # the first has no session before it: 503
    separate = float(s @ omega @ s) + sum(
        estimate.covariances(v[:, None], ~np.isnan(v), 5)[2][0, 0] for v in e.values()) / sessions
    assert out["daily_sd"] ** 2 != pytest.approx(separate, rel=1e-3)


def test_a_recent_listing_joins_and_takes_nothing_from_the_history_the_others_have(tmp_path):
    """Four holdings with the whole window and one listed fifteen sessions before the as-of: the four come in over
    the whole window, and the new one joins over its fifteen, where the sessions every holding has would leave the
    four fifteen as well."""
    held, market = _case(2, {"NEW": 15})
    store = _store(tmp_path, held, market)
    out = store.engine.value("volatility", "portfolio").output
    first, recent = out["parts"]
    x, s, mdl, e = _residuals(store)
    assert first["holdings"] == sorted(h for h, b in out["by_holding"].items() if b["name"] != "name:NEW")
    assert first["sessions"] == int(mdl.ok.sum()) - 1 and recent["sessions"] == 15
    assert [out["by_holding"][h]["name"] for h in recent["holdings"]] == ["name:NEW"]
    assert recent["from"] == DAYS[-15]
    # the four's part is what it is without the new listing's residuals: the same total, the same sessions
    four = sum(v for n, v in e.items() if n != "name:NEW")
    omega = mdl.omega / estimate.centring(mdl.ok, 5)
    alone = estimate.joined(float(s @ omega @ s), np.where(mdl.ok, np.nan_to_num(x) @ s, np.nan), four, 5,
                            float(omega[0, 0]) * 4 * 10_000.0 ** 2, 1.0, 4.0)
    assert first["own"] == pytest.approx(alone["own"]) and first["beta"] == pytest.approx(alone["beta"])
    assert sum(out["variance"].values()) == pytest.approx(out["daily_sd"] ** 2)


def test_two_young_holdings_in_one_industry_join_together_and_keep_their_correlation(tmp_path):
    """Two holdings listed the same day, sixty sessions before the as-of, sharing most of their own moves: one part,
    their sum's own variability, which their correlation makes larger than the two taken apart."""
    held, market = _case(3, {"Y1": 60, "Y2": 60}, industry=("Y1", "Y2"))
    store = _store(tmp_path, held, market)
    out = store.engine.value("volatility", "portfolio").output
    first, young = out["parts"]
    assert sorted(out["by_holding"][h]["name"] for h in young["holdings"]) == ["name:Y1", "name:Y2"]
    assert young["sessions"] == 60
    x, s, mdl, e = _residuals(store)
    apart = sum(estimate.covariances(e[n][:, None], ~np.isnan(e[n]), 5)[2][0, 0] for n in ("name:Y1", "name:Y2"))
    assert young["own"] > 1.3 * apart * 60 / 61  # correlation 0.64 of their own moves: about 1.6 times, apart


def test_a_holding_younger_than_the_memory_joins_leaning_on_its_prior(tmp_path):
    """A holding listed three sessions before the as-of, fewer than the estimator's five sessions of memory: it joins
    over its three, its own variability leaning one session toward the market's on its dollars, so at least a third
    of it (two degrees of freedom and one session's lean)."""
    held, market = _case(4, {"NEW": 3})
    store = _store(tmp_path, held, market)
    out = store.engine.value("volatility", "portfolio").output
    first, recent = out["parts"]
    assert recent["sessions"] == 3
    x, s, mdl, e = _residuals(store)
    assert recent["own"] >= float(mdl.omega[0, 0]) / estimate.centring(mdl.ok, 5) * 10_000.0 ** 2 / 3 - 1e-9
    assert np.isfinite(out["volatility"]) and out["volatility"] > 0


def test_a_holding_with_no_residual_row_or_one_joins_on_its_prior_and_two_beginning_together_are_one_part():
    """estimate.figure at its edges, by hand at no memory: no channels' response (session coefficients of none), the
    market's variance of completed moves 0.0004 as the estimator keeps it over six sessions, so 0.0004 * 6/5 = 0.00048
    as they say it. A holding of $1 listed after the window's last row has no residual, and joins on its prior alone;
    so does one with a single residual row, one session saying nothing of a variance about its own mean; two whose
    residuals begin on the same row are one part."""
    x = np.zeros((6, 1))
    x[:, 0] = [0.01, -0.02, 0.015, 0.0, -0.01, 0.02]
    ok = np.ones(6, bool)
    omega = np.array([[0.0004]])
    none = np.zeros((2, 1))
    alone = estimate.figure(x, ok, omega, {"h": (1.0, none, np.full(6, np.nan))}, memory=0, lean=1.0, kappa=4.0)
    assert alone["variance"] == {"channels": 0.0, "own": pytest.approx(0.00048), "shared": pytest.approx(0.0)}
    assert alone["parts"] == [{"start": 6, "holdings": ["h"], "sessions": 0, "beta": 0.0, "with": 0.0,
                               "own": pytest.approx(0.00048)}]
    one = np.full(6, np.nan)
    one[5] = 0.03
    assert estimate.figure(x, ok, omega, {"h": (1.0, none, one)}, memory=0, lean=1.0, kappa=4.0)["daily_sd"] ** 2 == \
        pytest.approx(0.00048)
    e = np.array([np.nan, np.nan, 0.01, -0.01, 0.02, 0.0])
    together = estimate.figure(x, ok, omega, {"a": (1.0, none, e), "b": (2.0, none, e)}, memory=0, lean=1.0,
                               kappa=4.0)
    [part] = together["parts"]
    assert part["holdings"] == ["a", "b"] and part["sessions"] == 4


def test_residuals_are_the_return_less_what_the_session_coefficients_give_on_one_session_spans():
    x = np.array([[0.01], [0.02], [np.nan], [0.03], [0.01]])
    ok = ~np.isnan(x).any(axis=1)
    session = np.array([[1.0], [0.5]])
    spans = [(0, 1, 0.5), (1, 2, 0.04), (2, 3, 0.1), (3, 5, 0.2), (4, 5, 0.02)]
    e = estimate.residuals(x, ok, session, spans)
    # row 0 has no session before it; row 2 is out of the common history, and so is row 3's session before; the
    # span (3, 5] covers two sessions; row 1: 0.04 - 0.02 - 0.5 * 0.01; row 4: 0.02 - 0.01 - 0.5 * 0.03
    assert np.isnan(e[[0, 2, 3]]).all()
    assert e[1] == pytest.approx(0.015) and e[4] == pytest.approx(-0.005)
