"""Value and exposure (MATH.md 1.2), hand-computed: a holding's value moves at its own betas, V_h b_h . u_E; a name's
exposure after look-through, signed, carries its own shock, e_i d_i, through a fund as directly; each name's share is
e_i (b_i . u_E) + e_i d_i, each fund looked through has one remainder, its own move beyond what its rows reach moving
as each does, and the names' shares with the funds' remainders sum to D. The funds' rows are stated in the rows'
contract (rows_fixture); the betas are the engine's own, over synthetic history."""

from __future__ import annotations

import math

import numpy as np
import pytest

from harness.derive.series import lookback
from harness.ledger import holdings as what_it_owns
from harness.store import Store, StoreError
from massive_data import ASOF, FETCHED, KNOWLEDGE, SESSIONS, closes_from, put_bars, put_reference
from rows_fixture import cash, holding, on_index, on_reference, rows_registry
from breakdown import stated

DAYS = SESSIONS[1:]


def store_with(tmp_path, rows: dict, holdings: list[dict], names: dict[str, str], *, seed: int = 0) -> Store:
    """A store whose funds hold `rows`, with the market and each name's synthetic history."""
    store = Store.create(tmp_path / "scratch-exposure", registry=rows_registry())
    store.load_initial(KNOWLEDGE)
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    for ticker, kind in names.items():
        put_reference(store, ticker, kind)
        put_bars(store, ticker, closes_from(dict(zip(DAYS, rng.uniform(0.5, 1.5) * m + rng.normal(0, 0.01, len(DAYS))))))
    store.fixture_rows = rows
    store.add("Portfolio", {"asof": ASOF, "holdings": holdings})
    return store


def impact_of(store, p: float, *, names: dict[str, float] | None = None, market: float | None = None) -> dict:
    """A watched event at p whose consequences shock each name, and the market, by the given fraction of its price in
    the baseline, the event not happening."""
    e = store.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                  "window_end": "2027-03-31", "chances": [stated(p)], "baseline": "no"})["id"]
    store.link("portfolio", "watches", e)
    for ticker, move in (names or {}).items():
        store.link(e, "consequence", f"name:{ticker}", {"shock": stated(move)})
    if market is not None:
        spy = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "SPY")
        store.link(e, "consequence", spy, {"shock": stated(market)})
    return store.engine.value("impact", e).output


def exposure(store) -> dict:
    return store.engine.value("lookthrough", "portfolio").output["e"]


def move(store, out, nid) -> float:
    """A name's or a fund's placed move between the two sides by its response (MATH.md 1.6): exp(b . z) - 1 against the
    baseline where the event happens, z the factors' compounded moves in the orthogonalized basis, placed two worlds
    apart as s / (1 + p s)."""
    b = store.engine.value("betas", "portfolio").output["objects"][nid]["fields"]["betas"]
    z: dict[str, float] = {}
    for c in sorted(out["against_baseline"]["yes"], key=lambda c: store.stated_of(c)["order"]):
        coef = store.engine.value("orthogonalize", c).output["coefficients"]
        z[c] = math.log1p(out["against_baseline"]["yes"][c]) - sum(beta * z[j] for j, beta in coef.items())
    s_r = math.exp(sum(b.get(c, 0.0) * z[c] for c in z)) - 1
    return s_r / (1 + out["p"] * s_r)


def adds_up(out) -> None:
    """The names' shares with the funds' remainders sum to D, and to the row's in each world; so do the holdings'."""
    for field, row in (("full_impact", "full_impact"), ("yes", "yes"), ("no", "no")):
        names = sum(n[field] for n in out["contributors"]) + sum(r[field] for r in out["remainders"])
        assert names == pytest.approx(out[row], abs=1e-9)
        assert sum(h[field] for h in out["by_holding"]) == pytest.approx(out[row], abs=1e-9)


def test_100_dollars_in_a_3x_fund_whose_index_is_20_percent_one_company_lose_6_on_its_own_shock_of_minus_10(tmp_path):
    """$100 in a fund exposed three times to an index that is 20% company A, held with its collateral in cash: $60 of
    exposure to A, so A's own shock of -10% against the baseline, placed at p = 0.25 (-0.10 / 0.975 apart), is
    -$6.15 of full impact, three quarters of it if it happens and a quarter the other way if not. Through a fund three
    times short of the same index, the opposite."""
    for exposure_, sign in ((3.0, -1.0), (-3.0, 1.0)):
        store = store_with(tmp_path / f"x{exposure_}", {"X3": [on_index(exposure_, [("A", 0.2), ("B", 0.8)]), cash(1.0)]},
                           [{"ticker": "X3", "dollars": 100.0}], {"X3": "ETF", "A": "CS", "B": "CS"})
        assert exposure(store) == pytest.approx({"name:A": exposure_ * 20.0, "name:B": exposure_ * 80.0})
        out = impact_of(store, 0.25, names={"A": -0.10})
        d = sign * 60.0 * 0.10 / (1 - 0.25 * 0.10)
        assert out["full_impact"] == pytest.approx(d, abs=1e-9) and round(abs(out["full_impact"]), 2) == 6.15
        (a,) = [n for n in out["contributors"] if n["name"] == "name:A"]
        assert (a["full_impact"], a["yes"], a["no"]) == pytest.approx((d, 0.75 * d, -0.25 * d), abs=1e-9)
        adds_up(out)
        store.close()


def test_a_future_on_an_index_reaches_a_company_the_fund_holds_only_where_the_company_is_in_the_index(tmp_path):
    """$1,000 in a fund holding company A at 30%, cash at 70%, and a future on an index at 100% of the fund: $300 of
    exposure to A where the index's companies are not established, and $350 where A is 5% of the index; A's own
    shock of -10% against the baseline, placed at p = 0.25, is then -0.10 / 0.975 of each."""
    for companies, a, b in ((None, 300.0, None), ([("A", 0.05), ("B", 0.95)], 350.0, 950.0)):
        store = store_with(tmp_path / f"c{a}", {"F": [holding("A", 0.3), cash(0.7), on_index(1.0, companies)]},
                           [{"ticker": "F", "dollars": 1000.0}], {"F": "ETF", "A": "CS", "B": "CS"})
        assert exposure(store) == pytest.approx({"name:A": a, **({"name:B": b} if b else {})})
        out = impact_of(store, 0.25, names={"A": -0.10})
        assert out["full_impact"] == pytest.approx(-0.10 / (1 - 0.25 * 0.10) * a, abs=1e-9)
        adds_up(out)
        store.close()


def test_d_is_the_names_shares_with_each_funds_remainder_through_a_fund_held_inside_a_fund(tmp_path):
    """$1,000 in F, which holds A at 50%, a fund G at 20% (A 40%, C 60%), cash at 30%, and a swap on an index (A 30%,
    C 70%) at 100%; $500 of A directly; $200 of cash. With the market -5% and A -10%: A's exposure is
    500 + 500 + 80 + 300 = $1,380 and C's 120 + 700 = $820; each name's share is its exposure at its own move and its
    own part, A's placed whole move -0.10 / 0.97 less its betas' move; F's remainder is its $1,000 at its own move less A's $500, G's $200, and the swap's A and C at theirs;
    G's is its $200 at its own move less A's $80 and C's $120 at theirs; and D is their sum."""
    rows = {"F": [holding("A", 0.5), holding("G", 0.2), cash(0.3), on_index(1.0, [("A", 0.3), ("C", 0.7)])],
            "G": [holding("A", 0.4), holding("C", 0.6)]}
    store = store_with(tmp_path, rows, [{"ticker": "F", "dollars": 1000.0}, {"ticker": "A", "dollars": 500.0},
                                        {"cash": 200.0}],
                       {"F": "ETF", "G": "ETF", "A": "CS", "C": "CS"}, seed=3)
    assert exposure(store) == pytest.approx({"name:A": 1380.0, "name:C": 820.0, "name:CASH:USD": 200.0})
    out = impact_of(store, 0.3, names={"A": -0.10}, market=-0.05)
    mv = {n: move(store, out, f"name:{n}") for n in ("F", "G", "A", "C")}
    d_a = -0.10 / (1 - 0.3 * 0.10) - mv["A"]  # A's own part against today
    names = {n["name"]: n["full_impact"] for n in out["contributors"]}
    assert names == pytest.approx({"name:A": 1380.0 * (mv["A"] + d_a), "name:C": 820.0 * mv["C"]}, abs=1e-9)
    remainders = {r["fund"]: r["full_impact"] for r in out["remainders"]}
    assert remainders == pytest.approx({
        "name:F": 1000.0 * mv["F"] - (500.0 + 300.0) * mv["A"] - 700.0 * mv["C"] - 200.0 * mv["G"],
        "name:G": 200.0 * mv["G"] - 80.0 * mv["A"] - 120.0 * mv["C"]}, abs=1e-9)
    assert out["full_impact"] == pytest.approx(1000.0 * mv["F"] + 500.0 * mv["A"] + 1380.0 * d_a, abs=1e-9)  # g . u_E + e d
    adds_up(out)
    (f,) = [h for h in out["by_holding"] if h["name"] == "name:F"]
    assert sum(n["full_impact"] for n in f["names"]) + sum(r["full_impact"] for r in f["remainders"]) == pytest.approx(f["full_impact"], abs=1e-9)
    store.close()


def test_a_name_with_an_exposure_and_no_price_moves_in_its_funds_remainder(tmp_path):
    """$1,000 in a fund holding A at 60% and a listing abroad at 40% whose price source answers it has no prices: the
    listing keeps its $400 of exposure and its place among the largest names; it has no betas and no price metric;
    and the fund's remainder, its $1,000 at its own move less A's $600 at A's, carries its dollars."""
    abroad = "AEA000000001:AE"
    store = Store.create(tmp_path / "scratch-unpriced", registry=rows_registry())
    store.load_initial(KNOWLEDGE)
    rng = np.random.default_rng(7)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    for ticker, kind in (("U", "ETF"), ("A", "CS"), (abroad, "CS")):
        put_reference(store, ticker, kind)
    for ticker in ("U", "A"):
        put_bars(store, ticker, closes_from(dict(zip(DAYS, m + rng.normal(0, 0.01, len(DAYS))))))
    store.put_data("home", abroad, {"status": "NOT_FOUND", "listing": abroad,
                                    "error": "Yahoo Finance lists no exchange for it"},
                   {"method": "GET", "url": f"home:{abroad}"}, retrieved_at=FETCHED, range=(lookback(ASOF), ASOF))
    store.fixture_rows = {"U": [holding("A", 0.6), holding(abroad, 0.4)]}
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "U", "dollars": 1000.0}]})
    assert exposure(store) == pytest.approx({"name:A": 600.0, f"name:{abroad}": 400.0})
    betas = store.engine.value("betas", "portfolio").output
    assert betas["fits"][f"name:{abroad}"] == {"rule": "no price"} and "b" not in betas["objects"][f"name:{abroad}"][
        "fields"]
    with pytest.raises(StoreError):  # no price, so no price metric
        store.get(f"metric:price:{abroad}")
    assert f"name:{abroad}" in {n.get("name") for n in what_it_owns(store)["by_company"]}
    out = impact_of(store, 0.4, market=-0.05)
    assert {n["name"] for n in out["contributors"]} == {"name:A"}
    (r,) = out["remainders"]
    assert r["full_impact"] == pytest.approx(1000.0 * move(store, out, "name:U") - 600.0 * move(store, out, "name:A"), abs=1e-9)
    adds_up(out)
    store.close()


def responds(store, out, nid: str, world: str) -> float:
    """A name's move by its response against the baseline in a world (MATH.md 1.6): exp(b . z_w) - 1, z_w the world's
    factors' compounded moves in the orthogonalized basis, b its betas as the store fitted them."""
    b = store.engine.value("betas", "portfolio").output["objects"][nid]["fields"]["betas"]
    z: dict[str, float] = {}
    for c in sorted(out["against_baseline"][world], key=lambda c: store.stated_of(c)["order"]):
        coef = store.engine.value("orthogonalize", c).output["coefficients"]
        z[c] = math.log1p(out["against_baseline"][world][c]) - sum(beta * z[j] for j, beta in coef.items())
    return math.exp(sum(b.get(c, 0.0) * z[c] for c in z)) - 1


def placed(s: float, p: float) -> dict[str, float]:
    """A price s against the baseline where the event happens and none where it does not, placed at q = p (1.5)."""
    return {"yes": (1 + s) / (1 + p * s) - 1, "no": 1 / (1 + p * s) - 1}


def test_a_fund_on_one_reference_moves_by_its_multiple_of_a_reference_the_portfolio_does_not_hold(tmp_path):
    """$10,000 in LEV, which carries three dollars of an index for each of its own (A 40%, B 60%), its collateral in
    cash; IDX, the fund that follows the index, is not held. Its reference's move is IDX's own response, fitted on IDX's
    own series and placed, so LEV stands in each world at 1 + 3 m_IDX, with B's own part on the $18,000 of B it carries,
    and at zero where that would take it below (MATH.md 1.6, 3.1). With the market -20% and B -20%, p = 0.3; with the
    market -80%, which moves IDX (beta 0.53 here) past a third of its price placed, LEV stands at zero where it happens,
    and its worlds come to more than its value today."""
    for market, b_shock in ((-0.20, -0.20), (-0.80, None)):
        store = store_with(tmp_path / f"m{-market}", {"LEV": [on_reference(3.0, [("A", 0.4), ("B", 0.6)], "IDX"),
                                                              cash(1.0)]},
                           [{"ticker": "LEV", "dollars": 10_000.0}], {"LEV": "ETF", "IDX": "ETF", "A": "CS", "B": "CS"},
                           seed=7)
        looked = store.engine.value("lookthrough", "portfolio").output
        assert looked["multiples"] == {"LEV": {"reference": "name:IDX", "multiple": 3.0}}
        assert exposure(store) == pytest.approx({"name:A": 12_000.0, "name:B": 18_000.0})
        out = impact_of(store, 0.3, names={"B": b_shock} if b_shock else None, market=market)
        assert "name:IDX" in store.engine.value("betas", "portfolio").output["objects"]  # fitted though not held
        m_idx = placed(responds(store, out, "name:IDX", "yes"), 0.3)
        own_b = {"yes": 0.0, "no": 0.0}
        if b_shock:
            whole, resp = placed(b_shock, 0.3), placed(responds(store, out, "name:B", "yes"), 0.3)
            own_b = {w: whole[w] - resp[w] for w in ("yes", "no")}
        lev = {w: 10_000.0 * max(3.0 * m_idx[w] + 1.8 * own_b[w], -1.0) for w in ("yes", "no")}
        (h,) = [h for h in out["by_holding"] if h["name"] == "name:LEV"]
        assert h["worlds"] == pytest.approx(lev, abs=1e-9)
        assert (out["yes"], out["no"]) == pytest.approx((lev["yes"], lev["no"]), abs=1e-9)
        if market == -0.80:
            assert lev["yes"] == -10_000.0 and out["expected"] == pytest.approx(0.3 * -10_000.0 + 0.7 * lev["no"])
            assert out["expected"] > 0
        adds_up(out)
        store.close()
