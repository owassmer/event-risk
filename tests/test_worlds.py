"""An event's worlds (MATH.md 1.1, 1.3 to 1.5) on hand-computed figures: the illustrations of MATH.md 1 to the cent, the
compounded fill, and the cases built to break the placing (a filled move past -100%, a price at zero in every world
today's prices carry)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from harness.derive import worlds

TWO = [{"id": "yes", "side": "yes", "p": 0.25}, {"id": "no", "side": "no", "p": 0.75}]
WAYS = [{"id": "first", "side": "yes", "p": 0.21}, {"id": "second", "side": "yes", "p": 0.09},
        {"id": "no", "side": "no", "p": 0.70}]


def test_a_price_at_70_with_a_shock_of_20_percent_stands_at_80_and_66_67_at_p_025():
    """MATH.md 1.5's level: L_base = 70 / (0.25 * 1.2 + 0.75) = 66.67, L_yes = 1.2 * 66.67 = 80.00; 20% apart, and 70
    at the weights."""
    q = worlds.weights(TWO, None)
    m = worlds.place({"yes": 1.20, "no": 1.0}, q)
    assert 70 * (1 + m["yes"]) == pytest.approx(80.00, abs=0.005)
    assert 70 * (1 + m["no"]) == pytest.approx(66.67, abs=0.005)
    assert 0.25 * 70 * (1 + m["yes"]) + 0.75 * 70 * (1 + m["no"]) == pytest.approx(70.0, abs=1e-12)
    # two worlds a shock s apart at weight q are s / (1 + q s) of today's price apart
    assert m["yes"] - m["no"] == pytest.approx(0.20 / (1 + 0.25 * 0.20), abs=1e-12)


def test_the_ways_illustration_to_the_cent():
    """MATH.md 1.5's ways: an event at 0.30, two ways, the first at 0.7 of it and the second at 0.3, the No side the baseline; a
    market fund at 600 shocked -5% and -20%; a dollar beta of $100,000 to it."""
    q = worlds.weights(WAYS, None)
    assert 0.21 * 0.95 + 0.09 * 0.80 + 0.70 == pytest.approx(0.9715)
    m = worlds.place({"first": 0.95, "second": 0.80, "no": 1.0}, q)
    levels = {w: 600 * (1 + x) for w, x in m.items()}
    assert round(levels["no"], 2) == 617.60
    assert round(levels["first"], 2) == 586.72
    assert round(levels["second"], 2) == 494.08
    A = {w: 100_000 * x for w, x in m.items()}
    assert {w: round(a, 2) for w, a in A.items()} == {"no": 2933.61, "first": -2213.07, "second": -17653.11}
    r = worlds.row(WAYS, A)
    assert round(r["yes"], 2) == -6845.08
    assert round(r["no"], 2) == 2933.61
    assert round(r["full_impact"], 2) == -9778.69
    assert round(r["worst_outcome"]["pnl"], 2) == -17653.11 and r["worst_outcome"]["world"] == "second"
    assert round(r["volatility"], 2) == 5924.50
    assert r["expected"] == pytest.approx(0.0, abs=1e-9)  # today's prices carry each world at our probability
    # the variance's two parts: between the sides, and within the Yes side
    p = 0.30
    spread = math.sqrt((0.21 * (A["first"] - r["yes"]) ** 2 + 0.09 * (A["second"] - r["yes"]) ** 2) / p)
    assert r["volatility"] ** 2 == pytest.approx(p * (1 - p) * r["full_impact"] ** 2 + p * spread ** 2)
    assert r["spread"] == {"yes": pytest.approx(spread), "no": 0.0}
    # one shock averaged over the ways, -9.5%: the same full impact, a narrower volatility
    one = [{"id": "yes", "side": "yes", "p": 0.30}, {"id": "no", "side": "no", "p": 0.70}]
    m1 = worlds.place({"yes": 1 - 0.095, "no": 1.0}, worlds.weights(one, None))
    r1 = worlds.row(one, {w: 100_000 * x for w, x in m1.items()})
    assert round(r1["full_impact"], 2) == -9778.69
    assert round(r1["volatility"], 2) == 4481.16


def test_the_two_world_illustration_and_its_identities():
    """MATH.md 1.5's two worlds: p = 0.62 * 0.305 and a full impact of -3,567.85 give Yes -2,893.17, No +674.68, and
    ±1,397.13; with a weight q stated apart from p, Yes = (1 - q) D, No = -q D, and mu = (p - q) D."""
    p = 0.62 * 0.305
    two = [{"id": "yes", "side": "yes", "p": p}, {"id": "no", "side": "no", "p": 1 - p}]
    D = -3567.85
    r = worlds.row(two, {"yes": (1 - p) * D, "no": -p * D})
    assert (round(r["yes"], 2), round(r["no"], 2), round(r["volatility"], 2)) == (-2893.17, 674.68, 1397.13)
    q = 0.40
    r = worlds.row(two, {"yes": (1 - q) * D, "no": -q * D})
    assert r["expected"] == pytest.approx((p - q) * D)
    assert r["yes"] == pytest.approx(r["expected"] + (1 - p) * r["full_impact"])
    assert r["no"] == pytest.approx(r["expected"] - p * r["full_impact"])
    assert r["volatility"] == pytest.approx(math.sqrt(p * (1 - p)) * abs(D))


def test_a_stated_weight_is_shared_within_each_side_as_the_probabilities_divide_it():
    ways = [{**w, "parent": w["side"] if w["id"] != "no" else None} for w in WAYS]
    q = worlds.weights(ways, {"yes": {"parent": None}}, {"yes": 0.5})
    assert q == pytest.approx({"first": 0.5 * 0.7, "second": 0.5 * 0.3, "no": 0.5})


def test_a_weight_stated_on_a_way_stands_and_the_other_worlds_share_what_it_leaves():
    """The event at 0.30, its ways at 0.20 and 0.10; today's prices put 0.35 on the first way, and nothing is stated
    for the event. The second way and the No side share 0.65 as 0.10 and 0.70 divide it: 0.08125 and 0.56875, so the
    event is carried at 0.43125. With 0.40 stated for the event as well, the second way takes 0.40 - 0.35, and the
    No side 0.60. With 0.30 for the event, the first way's 0.35 overruns it."""
    ws = [{"id": "first", "side": "yes", "p": 0.20, "parent": "yes"},
          {"id": "second", "side": "yes", "p": 0.10, "parent": "yes"}, {"id": "no", "side": "no", "p": 0.70}]
    parents = {"yes": {"parent": None}}
    q = worlds.weights(ws, parents, {"first": 0.35})
    assert q == pytest.approx({"first": 0.35, "second": 0.08125, "no": 0.56875})
    assert worlds.weights(ws, parents, {"first": 0.35, "yes": 0.40}) == pytest.approx(
        {"first": 0.35, "second": 0.05, "no": 0.60})
    with pytest.raises(worlds.Unholdable, match="come to 0.35"):
        worlds.weights(ws, parents, {"first": 0.35, "yes": 0.30})


def test_a_weight_stated_on_a_way_with_ways_is_shared_by_its_own_ways():
    """A way at 0.30 with ways at 0.10 and 0.20, a second way at 0.10, the No side at 0.60: 0.45 stated on the first
    way goes to its ways as 0.15 and 0.30; the second way and the No side share 0.55 as 0.10 and 0.60 divide it."""
    ws = [{"id": "a1", "side": "yes", "p": 0.10, "parent": "a"}, {"id": "a2", "side": "yes", "p": 0.20, "parent": "a"},
          {"id": "b", "side": "yes", "p": 0.10, "parent": "yes"}, {"id": "no", "side": "no", "p": 0.60}]
    parents = {"yes": {"parent": None}, "a": {"parent": "yes"}}
    q = worlds.weights(ws, parents, {"a": 0.45})
    assert q == pytest.approx({"a1": 0.15, "a2": 0.30, "b": 0.55 / 7, "no": 0.55 * 6 / 7})


def test_an_unstated_factor_moves_with_the_set_one_in_compounded_moves():
    """1.3: ln(1 + x_U) = cov(U,S) cov(S,S)^-1 ln(1 + x_S). A second factor whose regression on the first is 2.8,
    the first set at -45%: 0.55^2.8 - 1 = -81.2%, where the linear fill would give an impossible -126%."""
    var1 = 0.0001
    cov = np.array([[var1, 2.8 * var1], [2.8 * var1, 2.8 ** 2 * var1 + 0.00005]])
    logs = worlds.fill(["a", "b"], cov, {"a": math.log(0.55)})
    assert math.expm1(logs["b"]) == pytest.approx(0.55 ** 2.8 - 1, abs=1e-12)
    assert round(math.expm1(logs["b"]), 3) == -0.812
    # and the fill does not depend on the factors' order
    flipped = cov[np.ix_([1, 0], [1, 0])]
    assert worlds.fill(["b", "a"], flipped, {"a": math.log(0.55)})["b"] == pytest.approx(logs["b"], abs=1e-12)


def test_a_factor_at_zero_fills_nothing_and_the_others_fill_from_the_rest():
    cov = np.array([[1.0, 0.5, 0.2], [0.5, 1.0, 0.3], [0.2, 0.3, 1.0]]) * 1e-4
    got = worlds.fill(["a", "b", "c"], cov, {"a": -math.inf, "b": math.log(1.1)})
    assert got["a"] == -math.inf
    assert got["c"] == pytest.approx(0.3 * math.log(1.1), abs=1e-12)  # from b alone


def test_a_world_with_no_shocks_stands_where_the_baseline_does():
    cov = np.eye(2) * 1e-4
    assert worlds.fill(["a", "b"], cov, {}) == {"a": 0.0, "b": 0.0}


def test_a_price_whose_level_would_fall_below_zero_stands_at_zero_and_its_worlds_average_to_today():
    """The floor (1.6), every price: a holding at a response of -1.5 to a market placed -80% and +20% at p = 0.2 would
    stand at -20% of nothing; at zero there, its worlds are scaled together to average to today's price."""
    q = {"yes": 0.2, "no": 0.8}
    linear = {"yes": 1 - 1.5 * 0.8 * 1.0, "no": 1 + 1.5 * 0.2}  # 1 + b * m, the market's placed moves -80% and +20%
    m = worlds.place(linear, q)
    assert m["yes"] == pytest.approx(-1.0)
    assert 0.2 * (1 + m["yes"]) + 0.8 * (1 + m["no"]) == pytest.approx(1.0)
    assert m["no"] == pytest.approx(1 / 0.8 - 1)


def test_a_price_at_zero_in_every_world_today_carries_cannot_be_placed():
    """No weight on the baseline, and the one world with weight at zero: today's price contradicts it (1.10)."""
    with pytest.raises(worlds.Unplaceable):
        worlds.place({"yes": 0.0, "no": 1.0}, {"yes": 1.0, "no": 0.0})


def test_case_a_a_filled_move_past_minus_100_percent_at_a_high_weight():
    """Case (A): the first factor set at -45% at a weight of 0.95, the second regressing on it at 2.8 left unstated.
    Compounded, the second stands at 0.55^2.8 of its baseline, so its No world is 1 / (0.95 * 0.1875 + 0.05) = 4.38
    times today's price: what the stated shock means at that weight, and no world below zero."""
    var1 = 0.0001
    cov = np.array([[var1, 2.8 * var1], [2.8 * var1, 2.8 ** 2 * var1]])
    q = {"yes": 0.95, "no": 0.05}
    logs = {"yes": worlds.fill(["a", "b"], cov, {"a": math.log(0.55)}), "no": {"a": 0.0, "b": 0.0}}
    m = worlds.place({w: math.exp(logs[w]["b"]) for w in q}, q)
    assert 1 + m["no"] == pytest.approx(1 / (0.95 * 0.55 ** 2.8 + 0.05), abs=1e-12)
    assert round(1 + m["no"], 2) == 4.38
    assert min(m.values()) > -1.0


def test_the_outcome_tree_gives_each_parent_its_ways_averaged_at_their_probabilities():
    """A No side with two ways, the first with two of its own: each parent's probability and weight are its worlds'
    summed, and its P&L their average at their probabilities; the Yes side is one world. By hand: the first way's
    P&L is (0.18 x 100 + 0.12 x 400) / 0.30 = 220, the No side's (0.18 x 100 + 0.12 x 400 + 0.30 x -50) / 0.60 = 85."""
    ws = [{"id": "yes", "side": "yes", "p": 0.4, "parent": None},
          {"id": "a1", "side": "no", "p": 0.18, "parent": "a"}, {"id": "a2", "side": "no", "p": 0.12, "parent": "a"},
          {"id": "b", "side": "no", "p": 0.30, "parent": "no"}]
    parents = {"no": {"side": "no", "parent": None, "ways": ["a", "b"]},
               "a": {"side": "no", "parent": "no", "ways": ["a1", "a2"], "title": "the first way"}}
    A = {"yes": -500.0, "a1": 100.0, "a2": 400.0, "b": -50.0}
    q = {"yes": 0.5, "a1": 0.1, "a2": 0.1, "b": 0.3}
    got = {o["id"]: o for o in worlds.tree(ws, parents, A, q)}
    assert [o["id"] for o in worlds.tree(ws, parents, A, q)] == ["yes", "no", "a", "a1", "a2", "b"]
    assert got["a"] == {"id": "a", "parent": "no", "side": "no", "title": "the first way", "p": pytest.approx(0.30),
                        "q": pytest.approx(0.2), "A": pytest.approx(220.0), "ways": ["a1", "a2"]}
    assert got["no"]["A"] == pytest.approx(85.0) and got["no"]["p"] == pytest.approx(0.6) and got["no"]["q"] == \
        pytest.approx(0.5)
    assert "ways" not in got["yes"] and got["yes"]["A"] == -500.0 and got["a1"]["parent"] == "a"
