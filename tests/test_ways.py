"""Ways (MATH.md 1.1 to 1.6; ONTOLOGY.md 2.2): an event's sides divided into ways, at any depth, each a proposition
known by its own title; the worlds the outcomes with nothing under them; each way's probability its weight times its
parent's, or what the others leave it; each world's shocks from the consequence nearest above it; the baseline any
world; and the row, its outcome tree, and what the event now does, read from the worlds. Figures by hand in each
docstring."""

from __future__ import annotations

import math

import numpy as np
import pytest

from harness.derive import worlds
from harness.ledger import check, event_now, row
from harness.render import ledger_markdown
from harness.store import StoreError
from test_impact import channel, event, holding_world, output, world
from breakdown import stated




def stated_entry(entry: dict) -> dict:
    """A stated world's entry in event_now without its move against today, which depends on the weights."""
    return {k: v for k, v in entry.items() if k != "against_today"}

def way(w, parent, title, *, side=None, weight=None) -> str:
    """A way of `parent`, known by its title: on a side of an event, or under a way; with a weight, its share of its
    parent, stated as its chance given its parent, or given not the event on the event's No side."""
    pid = w.prop(title)
    w.link(parent, "way", pid, **({"side": side} if side else {}))
    if weight is not None:
        w.chances(pid, {"given_not" if side == "no" else "given": [parent], "value": weight})
    return pid


def by_id(items) -> dict:
    return {o["id"]: o for o in items}


def test_the_ways_illustration_through_the_store(tmp_path):
    """MATH.md 1.5's ways: an event at 0.30 whose Yes side has two ways, the first at weight 0.7 and the second stating
    none, so worlds at 0.21, 0.09 and 0.70; the No side the baseline; the first way's shock on the market fund at 600
    -5%, the second's -20%; a dollar beta of $100,000 to it. The fund stands at 600 / 0.9715 = 617.60 if the event does
    not happen, 586.72 in the first way and 494.08 in the second; the worlds' P&L are +$2,933.61, -$2,213.07 and
    -$17,653.11; Yes -$6,845.08, No +$2,933.61, full impact -$9,778.69, worst outcome -$17,653.11 in the second way,
    volatility $5,924.50."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.30)
    first = way(w, e, "the first way", side="yes", weight=0.7)
    second = way(w, e, "the second way", side="yes")
    assert w.derived(first, "probability") == pytest.approx(0.21)
    assert w.derived(second, "probability") == pytest.approx(0.09)  # what the first leaves
    a = w.link(first, "consequence", channel(w, "SPY"), shock=-0.05)
    b = w.link(second, "consequence", channel(w, "SPY"), shock=-0.2)
    levels = w.derived(a, "levels")
    assert levels == w.derived(b, "levels")  # one price, one level in each world
    assert {k: round(v, 2) for k, v in levels.items()} == {first: 586.72, second: 494.08, "no": 617.60}
    r = row(w.store, e)
    assert {k: round(v["A"], 2) for k, v in r["worlds"].items()} == {first: -2213.07, second: -17653.11,
                                                                     "no": 2933.61}
    assert (round(r["yes"], 2), round(r["no"], 2), round(r["full_impact"], 2), round(r["volatility"], 2)) == \
        (-6845.08, 2933.61, -9778.69, 5924.50)
    assert r["worst_outcome"]["world"] == second and r["worst_outcome"]["title"] == "the second way"
    assert round(r["worst_outcome"]["pnl"], 2) == -17653.11 and r["worst_outcome"]["probability"] == \
        pytest.approx(0.09)
    assert r["expected"] == pytest.approx(0.0, abs=1e-9)
    p, D = 0.30, r["full_impact"]  # the variance's two parts, between the sides and within the Yes side
    assert r["volatility"] ** 2 == pytest.approx(p * (1 - p) * D ** 2 + p * r["spread"]["yes"] ** 2)
    tree = r["outcomes"]
    assert [(o["id"], o["title"], o["world"]) for o in tree] == [
        ("yes", "Yes", False), (first, "the first way", True), (second, "the second way", True), ("no", "No", True)]
    assert by_id(tree)["yes"]["A"] == pytest.approx(r["yes"]) and by_id(tree)["yes"]["ways"] == [first, second]
    assert by_id(tree)["no"]["baseline"] and by_id(tree)[first]["parent"] == "yes"
    assert "Worst outcome \u2212$17,653 \u00b7 the second way" in ledger_markdown(w.store)  # the download


def test_a_weight_stated_on_a_way_stands_and_the_worlds_with_none_share_what_it_leaves(tmp_path):
    """MATH.md 1.5's ways, with today's prices putting 0.15 on the second way (our 0.09) and nothing stated for the event.
    The first way and the No side share 0.85 as 0.21 and 0.70 divide it: 0.1961538 and 0.6538462. The fund's baseline
    price is 600 / (0.1961538 x 0.95 + 0.15 x 0.80 + 0.6538462) = 600 / 0.9601923; the worlds' P&L at those weights
    average to nothing, and the row carries an expected value, our probabilities differing from the weights."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.30)
    first = way(w, e, "the first way", side="yes", weight=0.7)
    second = way(w, e, "the second way", side="yes")
    w.link(first, "consequence", channel(w, "SPY"), shock=-0.05)
    w.link(second, "consequence", channel(w, "SPY"), shock=-0.2)
    w.store.edit(second, "priced_in", stated(0.15))
    r = row(w.store, e)
    q = {first: 0.85 * 0.21 / 0.91, second: 0.15, "no": 0.85 * 0.70 / 0.91}
    assert {k: v["q"] for k, v in r["worlds"].items()} == pytest.approx(q)
    base = 1 / (q[first] * 0.95 + q[second] * 0.80 + q["no"])
    assert {k: v["A"] for k, v in r["worlds"].items()} == pytest.approx(
        {first: 100_000 * (0.95 * base - 1), second: 100_000 * (0.80 * base - 1), "no": 100_000 * (base - 1)})
    assert sum(q[k] * v["A"] for k, v in r["worlds"].items()) == pytest.approx(0.0, abs=1e-9)
    assert r["expected"] == pytest.approx(sum(v["p"] * v["A"] for v in r["worlds"].values()))
    assert r["priced_in"] == {second: {"q": 0.15, "stated": True}}
    w.store.edit(e, "priced_in", stated(0.10))  # the event's 0.10 under its second way's 0.15 cannot hold
    assert row(w.store, e)["full_impact"] is None and "come to 0.15" in event_now(w.store, e)["why"]
    assert [x["id"] for x in check(w.store)["weights_cannot_hold"]] == [e]


def test_nested_ways_on_the_baseline_side_with_the_baseline_a_way(tmp_path):
    """An event at 0.40; its No side divided into a first way at weight 0.5 and a second taking the rest, 0.30, the
    first divided again into one at 0.6 of it, 0.18, the baseline, and another taking 0.12. The event's shock, -10% on
    the market fund at 600, reaches the Yes side alone; the second way's own, +5%; the nested way's, +20% on oil at 70,
    which moves with nothing else here. The fund: 600 / (0.4 x 0.9 + 0.30 + 0.3 x 1.05) = 600 / 0.975, so its moves
    are -0.0769231, +0.0256410 and +0.0769231; oil: 70 / (0.12 x 1.2 + 0.88) = 70 / 1.024, so +0.171875 in the nested
    way and -0.0234375 elsewhere. With $100,000 on the fund and $10,000 on oil: Yes -7,926.68, the baseline 2,329.73,
    the nested way 4,282.85, the second way 7,457.93; No (0.18 x 2,329.73 + 0.12 x 4,282.85 + 0.3 x 7,457.93) / 0.6 =
    5,284.46."""
    w = world(tmp_path, g={"SPY": 100_000.0, "USO": 10_000.0}, coefficients={"SPY": {}, "USO": {}},
              levels={"SPY": 600.0, "USO": 70.0})
    e = event(w, p=0.40)
    first = way(w, e, "talks stall", side="no", weight=0.5)
    second = way(w, e, "talks resume", side="no")
    quiet = way(w, first, "quietly", weight=0.6)
    loud = way(w, first, "with a supply scare")
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    on_second = w.link(second, "consequence", channel(w, "SPY"), shock=0.05)
    w.link(loud, "consequence", channel(w, "USO"), shock=0.2)
    assert row(w.store, e)["full_impact"] is None  # the baseline names the No side, which now has ways
    now = event_now(w.store, e)
    assert now["figures"] is None and "which has ways" in now["why"]
    w.store.edit(e, "baseline", quiet)
    r = row(w.store, e)
    A = {k: round(v["A"], 2) for k, v in r["worlds"].items()}
    assert A == {"yes": -7926.68, quiet: 2329.73, loud: 4282.85, second: 7457.93}
    assert (round(r["yes"], 2), round(r["no"], 2)) == (-7926.68, 5284.46)
    assert {k: v["p"] for k, v in r["worlds"].items()} == pytest.approx({"yes": 0.4, quiet: 0.18, loud: 0.12,
                                                                         second: 0.3})
    assert r["expected"] == pytest.approx(0.0, abs=1e-6)
    p = 0.4
    assert r["volatility"] ** 2 == pytest.approx(p * (1 - p) * r["full_impact"] ** 2 +
                                                 (1 - p) * r["spread"]["no"] ** 2)
    now = event_now(w.store, e)
    spy, oil = (next(f for f in now["factors"] if f["factor"] == channel(w, t)) for t in ("SPY", "USO"))
    assert spy["worlds"]["yes"]["by"] == "own" and spy["worlds"]["yes"]["on"] == e
    assert stated_entry(spy["worlds"][second]) == {"move": pytest.approx(0.05), "by": "own", "on": second,
                                                   "consequence": on_second,
                                                   "stated": {"value": 0.05, "against": "baseline"}}
    assert loud not in spy["worlds"] and oil["worlds"][loud]["by"] == "own"  # the market does not move there
    assert all(quiet not in f["worlds"] for f in now["factors"])  # the baseline
    tree = by_id(now["outcomes"])
    assert tree[quiet]["baseline"] and tree[first]["ways"] == [quiet, loud] and tree["no"]["ways"] == [first, second]
    assert tree[first]["A"] == pytest.approx((0.18 * r["worlds"][quiet]["A"] + 0.12 * r["worlds"][loud]["A"]) / 0.3)


def test_a_ways_shock_replaces_its_parents_on_what_it_sets_and_its_ways_take_the_rest(tmp_path):
    """The Yes side's ways: a first way, with a nested way of its own and another, and a second. The event's -10% on
    the fund reaches every Yes world; the first way's -20% replaces it there and in its ways; the nested way's own -30%
    replaces that in it alone. So the fund moves -20% in the first way's other way, taken from its parent, -30% in the
    nested way, its own, and -10% in the second way, taken from the event."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.5)
    first = way(w, e, "a wide war", side="yes", weight=0.5)
    second = way(w, e, "a contained strike", side="yes")
    deep = way(w, first, "with a blockade", weight=0.5)
    other = way(w, first, "without a blockade")
    on_e = w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    on_first = w.link(first, "consequence", channel(w, "SPY"), shock=-0.2)
    on_deep = w.link(deep, "consequence", channel(w, "SPY"), shock=-0.3)
    spy = next(f for f in event_now(w.store, e)["factors"] if f["factor"] == channel(w, "SPY"))
    assert stated_entry(spy["worlds"][other]) == {"move": pytest.approx(-0.20), "by": "parent", "on": first,
                                    "consequence": on_first, "stated": {"value": -0.2, "against": "baseline"}}
    assert stated_entry(spy["worlds"][deep]) == {"move": pytest.approx(-0.30), "by": "own", "on": deep,
                                    "consequence": on_deep, "stated": {"value": -0.3, "against": "baseline"}}
    assert stated_entry(spy["worlds"][second]) == {"move": pytest.approx(-0.10), "by": "parent", "on": e,
                                    "consequence": on_e, "stated": {"value": -0.1, "against": "baseline"}}
    assert "no" not in spy["worlds"]  # the baseline
    # placed by hand: 600 / (0.125 x 0.7 + 0.125 x 0.8 + 0.25 x 0.9 + 0.5) = 600 / 0.9125
    base = 1 / (0.125 * 0.7 + 0.125 * 0.8 + 0.25 * 0.9 + 0.5)
    A = row(w.store, e)["worlds"]
    assert {k: v["A"] for k, v in A.items()} == pytest.approx(
        {deep: 100_000 * (0.7 * base - 1), other: 100_000 * (0.8 * base - 1), second: 100_000 * (0.9 * base - 1),
         "no": 100_000 * (base - 1)})


def test_two_ways_that_set_different_factors_each_fill_what_the_other_states(tmp_path):
    """One way sets the market, -10%, and the other oil, +30%; in each world the factor not set there moves with the one
    that is, in compounded moves: ln(1 + x_oil) = cov(oil, market) / var(market) x ln(0.9) in the first, and the
    market's from oil's ln(1.3) in the second, by the store's own covariance (L diag(var) L')."""
    w = world(tmp_path, g={"SPY": 100_000.0, "USO": 10_000.0}, coefficients={"SPY": {}, "USO": {"SPY": 0.5}})
    e = event(w, p=0.4)
    market = way(w, e, "a demand shock", side="yes", weight=0.5)
    oil = way(w, e, "a supply shock", side="yes")
    w.link(market, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(oil, "consequence", channel(w, "USO"), shock=0.3)
    m = w.store.get(w.store.get(e)["derived"]["full_impact"]["basis"])["inputs"]
    ids = m["channels"]
    lower = np.eye(len(ids))
    for i, c in enumerate(ids):
        for j, beta in m["coefficients"][c].items():
            lower[i, ids.index(j)] = beta
    cov = lower @ np.diag([m["variances"][c] for c in ids]) @ lower.T
    s, u = ids.index(channel(w, "SPY")), ids.index(channel(w, "USO"))
    out = output(w, e)
    assert out["against_baseline"][market][ids[u]] == pytest.approx(math.expm1(cov[u, s] / cov[s, s] * math.log(0.9)))
    assert out["against_baseline"][oil][ids[s]] == pytest.approx(math.expm1(cov[s, u] / cov[u, u] * math.log(1.3)))
    assert out["against_baseline"][market][ids[u]] == pytest.approx(math.expm1(0.5 * math.log(0.9)))
    now = event_now(w.store, e)
    got = {f["factor"]: f["worlds"] for f in now["factors"]}
    assert got[ids[u]][market]["by"] == "moves with the stated ones" and got[ids[s]][oil]["by"] == "moves with the stated ones"


def test_a_name_set_in_one_way_moves_by_its_betas_elsewhere_and_a_factor_worth_nothing_enters_at_its_simple_move(
        tmp_path):
    """NVDA's price -20% in one way; in its sibling oil is stated at -100%, worth nothing there, and nothing else is set,
    so the other factors fill from nothing and stand where the baseline does. NVDA moves by its betas there: compounded
    on the live factors, exp(0) = 1, and oil at its simple move, (1 + b_raw,oil x -1), b_raw = T'b its betas on the raw
    factors. So its whole move there is -b_raw,oil; in the baseline it is 0."""
    w = holding_world(tmp_path)
    e = event(w, p=0.4)
    named = way(w, e, "the chip rules land", side="yes", weight=0.5)
    dead = way(w, e, "the oil fund folds", side="yes")
    w.link(named, "consequence", "name:NVDA", shock=-0.2)
    w.link(dead, "consequence", channel(w, "USO"), shock=-1.0)
    out = output(w, e)
    m = w.store.get(w.store.get(e)["derived"]["full_impact"]["basis"])["inputs"]
    ids = m["channels"]
    lower = np.eye(len(ids))
    for i, c in enumerate(ids):
        for j, beta in m["coefficients"][c].items():
            lower[i, ids.index(j)] = beta
    t = np.linalg.inv(lower)  # the orthogonalized basis from the raw moves, u = T x
    b = w.store.engine.value("betas", "portfolio").output["objects"]["name:NVDA"]["fields"]["betas"]
    raw = t.T @ np.array([b[c] for c in ids])
    oil = ids.index(channel(w, "USO"))
    assert out["whole"]["name:NVDA"][named] == pytest.approx(-0.20)
    assert out["whole"]["name:NVDA"][dead] == pytest.approx(-raw[oil], abs=1e-9)
    assert out["whole"]["name:NVDA"]["no"] == pytest.approx(0.0, abs=1e-12)
    assert all(out["against_baseline"][dead][c] == pytest.approx(0.0, abs=1e-12) for c in ids if c != ids[oil])
    nvda = next(n for n in event_now(w.store, e)["names"] if n["name"] == "name:NVDA")
    assert nvda["worlds"][named]["by"] == "own" and nvda["worlds"][dead]["by"] == "moves with the stated ones"
    for x in out["worlds"]:  # the holdings come to each world's P&L, and the names with the remainders
        assert sum(h["worlds"][x] for h in out["by_holding"]) == pytest.approx(out["worlds"][x]["A"], abs=1e-9)
        assert sum(s["worlds"][x] for s in out["contributors"]) + sum(r["worlds"][x] for r in out["remainders"]) == \
            pytest.approx(out["worlds"][x]["A"], abs=1e-9)


def test_a_way_with_probability_zero_is_a_world_that_moves_nothing_placed(tmp_path):
    """The first way at weight 1, the second left 0. Today's prices carry the second at no weight, so placing reads the
    first way and the No side: 600 / (0.3 x 0.9 + 0.7) = 600 / 0.97; the second way's level is 0.5 of the baseline's,
    and its P&L is shown at probability 0. It is the lowest P&L, and the worst outcome is the first way's, the lowest
    among the worlds that can happen."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.30)
    first = way(w, e, "the first way", side="yes", weight=1.0)
    second = way(w, e, "the second way", side="yes")
    w.link(first, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(second, "consequence", channel(w, "SPY"), shock=-0.5)
    r = row(w.store, e)
    base = 1 / 0.97
    assert r["worlds"][second]["p"] == 0.0
    assert {k: v["A"] for k, v in r["worlds"].items()} == pytest.approx(
        {first: 100_000 * (0.9 * base - 1), second: 100_000 * (0.5 * base - 1), "no": 100_000 * (base - 1)})
    assert r["yes"] == pytest.approx(r["worlds"][first]["A"])  # the side's mean at its worlds' probabilities
    assert r["volatility"] == pytest.approx(math.sqrt(0.3 * 0.7) * abs(r["full_impact"]))
    assert r["worlds"][second]["A"] < r["worlds"][first]["A"]
    assert r["worst_outcome"]["world"] == first and r["worst_outcome"]["probability"] == pytest.approx(0.3)


def test_shares_that_cannot_make_up_their_parent_are_listed_and_leave_no_figures(tmp_path):
    """Shares of 0.7 and 0.5 of one side come to 1.2, more than the side; two ways whose shares no statement fixes
    leave each open. Either way the worlds have no probabilities and the event no figures; the first cannot hold
    together, and check() lists the chances among the statements that cannot; the second names the chances that would
    close each way."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.3)
    a = way(w, e, "the first way", side="yes", weight=0.7)
    b = way(w, e, "the second way", side="yes", weight=0.5)
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.1)
    assert row(w.store, e)["full_impact"] is None and w.derived(a, "probability") is None
    assert "cannot hold together" in event_now(w.store, e)["why"]
    (listed,) = check(w.store)["cannot_hold"]
    assert {f"{a}.chances[0]", f"{b}.chances[0]"} <= set(listed["statements"])
    w.store.edit(b, "chances", None)
    c = way(w, e, "a third way", side="yes")
    why = event_now(w.store, e)["why"]
    assert row(w.store, e)["full_impact"] is None and f"{b}'s probability is left open" in why and \
        f"{c}'s probability is left open" in why and f"The chance of {b} given {e} would settle it" in why
    assert check(w.store)["cannot_hold"] == []
    w.store.remove(c)
    assert row(w.store, e)["full_impact"] is not None and w.derived(b, "probability") == pytest.approx(0.09)


def test_a_way_bounds_its_side_and_its_side_bounds_it(tmp_path):
    """MATH.md 1.2's rule for a side with ways, each side here two ways. A Yes way asked at [0.20, 0.25] lifts the event's
    floor to 0.20; a No way asked at [0.50, 0.60] lifts the No side's to 0.50, which caps the event at 0.50; the other
    ways are bounded by their side less their sibling's floor, at most 0.50 - 0.20 and 0.80 - 0.50. Asked itself at
    [0.30, 0.40], the event bounds them again: the other Yes way at least 0.30 - 0.25 = 0.05 and at most 0.40 - 0.20 =
    0.20; the other No way at most 0.70 - 0.50 = 0.20."""
    w = world(tmp_path)
    e = w.prop("E")
    y1, y2 = way(w, e, "the first way", side="yes"), way(w, e, "the second way", side="yes")
    n1, n2 = way(w, e, "the third way", side="no"), way(w, e, "the fourth way", side="no")
    w.link(y1, "asks", w.market("MY", "Way one?", 0.20, 0.25))
    w.link(n1, "asks", w.market("MN", "Way three?", 0.50, 0.60))
    assert w.interval(e) == pytest.approx((0.20, 0.50))
    assert w.interval(y2) == pytest.approx((0.0, 0.30)) and w.interval(n2) == pytest.approx((0.0, 0.30))
    w.link(e, "asks", w.market("ME", "The event?", 0.30, 0.40))
    assert w.interval(e) == pytest.approx((0.30, 0.40))
    assert w.interval(y1) == pytest.approx((0.20, 0.25)) and w.interval(n1) == pytest.approx((0.50, 0.60))
    assert w.interval(y2) == pytest.approx((0.05, 0.20)) and w.interval(n2) == pytest.approx((0.0, 0.20))
    from harness.derive.structure import subject
    ends = w.store.engine.value("structure", subject(w.store, y2)).output["props"][y2]["range"]
    books = {f"{x['id']}:book" for x in (*w.store.links_of(e, "asks"), *w.store.links_of(y1, "asks"))}
    sides = {x["id"] for x in w.store.links_of(e, "way") if x["stated"]["side"] == "yes"}
    assert set(ends["lo_rests"]) == set(ends["hi_rests"]) == books | sides  # E's book less its sibling's, both ends


def test_what_a_way_is_and_what_cannot_be_one(tmp_path):
    """A way has one parent and lies under it; a way of an event states its side and a way's ways do not; an event the
    portfolio watches is no way; and a shock says how another world stands against the baseline, so none is stated on
    the baseline or on a way that holds it, the baseline names a world, and nothing removes the way it names."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.3)
    a = way(w, e, "the first way", side="no", weight=0.5)
    b = way(w, e, "the second way", side="no")
    a1 = way(w, a, "the first way's first", weight=0.5)
    a2 = way(w, a, "the first way's second")
    with pytest.raises(StoreError, match="one parent"):
        w.link(b, "way", a1)
    with pytest.raises(StoreError, match="a way lies under its parent"):
        w.link(a1, "way", e)
    with pytest.raises(StoreError, match="states its side"):
        w.link(e, "way", w.prop("sideless"))
    with pytest.raises(StoreError, match="lie on its side"):
        w.link(a, "way", w.prop("sided"), side="yes")
    with pytest.raises(StoreError, match="watches"):
        w.link(e, "way", event(w, "watched"), side="yes")
    with pytest.raises(StoreError, match="read in that event's row"):
        w.link("portfolio", "watches", a)
    with pytest.raises(StoreError, match="has ways"):
        w.store.edit(e, "baseline", "no")
    with pytest.raises(StoreError, match="has ways of its own"):
        w.store.edit(e, "baseline", a)
    w.store.edit(e, "baseline", a1)
    with pytest.raises(StoreError, match="holds .*baseline"):
        w.link(a1, "consequence", channel(w, "SPY"), shock=-0.1)
    with pytest.raises(StoreError, match="holds .*baseline"):
        w.link(a, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(b, "consequence", channel(w, "SPY"), shock=-0.1)
    with pytest.raises(StoreError, match="stated on it or on a way that holds it"):
        w.store.edit(e, "baseline", b)
    with pytest.raises(StoreError, match="baseline"):
        w.store.remove(a1)
    with pytest.raises(StoreError, match="baseline"):
        w.store.remove(w.store.links_of(a, "way", "in")[0]["id"])  # it would cut the baseline off from the event
    with pytest.raises(StoreError, match="its event names the baseline"):
        w.store.edit(a, "baseline", "yes")
    w.store.edit(e, "baseline", a2)
    w.store.remove(a1)  # no longer named: it goes, with its links
    assert [o["id"] for o in row(w.store, e)["outcomes"]] == ["yes", "no", a, a2, b]


def test_removing_a_way_removes_its_influence(tmp_path):
    """With its way gone, the event's worlds and figures are what they were before it was given."""
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    before = {k: row(w.store, e)[k] for k in ("yes", "no", "full_impact", "volatility")}
    a = way(w, e, "the first way", side="yes", weight=0.5)
    way(w, e, "the second way", side="yes")
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.3)
    assert row(w.store, e)["full_impact"] != pytest.approx(before["full_impact"])
    for link in w.store.links_of(e, "way"):
        w.store.remove(link["id"])
    after = row(w.store, e)
    assert {k: after[k] for k in before} == pytest.approx(before) and set(after["worlds"]) == {"yes", "no"}


def test_a_way_or_a_ways_shock_returns_its_event_as_it_now_stands(tmp_path):
    """link and edit return, for a way, a consequence on a way, and a way's share, the event the way is of, as it now
    stands (TOOLS.md 5). A first way alone makes up the
    Yes side; stated at 0.7 of it, it cannot, and the return says so; with a second taking the rest, the outcomes have
    their probabilities, 0.21 and 0.09; and once a shock stands, each world its figures."""
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))
    e = event(w, p=0.30)
    first, second = w.prop("the first way"), w.prop("the second way")
    ok, got = tools.call("link", {"from_id": e, "relation": "way", "to_id": first, "fields": {"side": "yes"}})
    assert ok and got["events"][0]["id"] == e and got["propositions"][first]["probability"] == pytest.approx(0.30)
    ok, got = tools.call("edit", {"id": first, "field": "chances", "value": [stated({"given": [e], "value": 0.7})]})
    assert ok and got["cannot_hold"] and "probability" not in got["propositions"][first]
    assert got["events"][0]["why"].startswith(f"The statements of {e}'s structure cannot hold together")
    ok, got = tools.call("link", {"from_id": e, "relation": "way", "to_id": second, "fields": {"side": "yes"}})
    assert ok and {o["id"]: o["p"] for o in got["events"][0]["outcomes"]} == pytest.approx(
        {"yes": 0.3, first: 0.21, second: 0.09, "no": 0.7})
    ok, got = tools.call("link", {"from_id": first, "relation": "consequence", "to_id": channel(w, "SPY"), "fields": {"shock": stated(-0.05)}})
    assert ok and got["events"][0]["id"] == e and got["events"][0]["row"]["full_impact"] is not None
    assert {o["id"] for o in got["events"][0]["outcomes"] if o["world"]} == {first, second, "no"}
    # a share restated once a shock stands moves every figure, and returns them (TOOLS.md 5): evaluation's case
    before = got["events"][0]["row"]["full_impact"]
    ok, got = tools.call("edit", {"id": first, "field": "chances", "value": [stated({"given": [e], "value": 0.4})]})
    assert ok and [x["id"] for x in got["events"]] == [e] and got["events"][0]["holdings"]
    assert {o["id"]: o["p"] for o in got["events"][0]["outcomes"]}[first] == pytest.approx(0.12)
    assert got["events"][0]["row"]["full_impact"] != pytest.approx(before)
    # the watched event's own statement gives its row once, in the event, and not among its derived fields
    ok, got = tools.call("edit", {"id": e, "field": "title", "value": "E, renamed"})
    assert ok and got["events"][0]["row"]["full_impact"] is not None
    assert not {"full_impact", "by_holding", "contributors", "outcomes"} & set(got["derived"])


def test_an_events_ways_make_up_their_side_so_its_worlds_and_their_weights_sum_to_one():
    """MATH.md 1.1, 1.5: an event's ways together make up their side. The structure reads each from one solution, which
    holds a way link's sum to the solver's own tolerance: in one run's store, after a minute's readings, an event at
    0.79 had a way at 0.325 and the other, what the first leaves, at 0.46500000024425625, so its worlds summed to
    1 + 2.4e-10, and a contract a fund holds, valued at weights read from them, refused them, taking the ledger down.
    The ways divide their side's probability as those figures divide it: the worlds sum to one to the float, each way
    within a billionth of where the structure reads it, and the contract is valued."""
    from harness.derive import options
    from harness.derive.outcomes import _outcomes_compute
    read = {"prop:19": 0.79, "prop:20": 0.325, "prop:21": 0.46500000024425625}  # as the reading returned them
    got = _outcomes_compute({"event": "prop:19", "cannot_hold": [],
                             "ways": [{"id": "prop:21", "parent": "yes", "link": "link:141"},
                                      {"id": "prop:20", "parent": "yes", "link": "link:142"}],
                             "nodes": {pid: {"title": pid, "p": p} for pid, p in read.items()}}, None)
    p = {w: got["nodes"][w]["p"] for w in got["worlds"]}
    assert sorted(p) == ["no", "prop:20", "prop:21"]
    assert math.fsum(p.values()) == pytest.approx(1.0, abs=1e-15)
    assert p["prop:20"] == pytest.approx(0.325, abs=1e-9) and p["prop:21"] == pytest.approx(0.465, abs=1e-9)
    assert p["no"] == 1.0 - 0.79
    valued = options.worlds_value(kind="call", style="european", strike=100.0, years=0.5, rate=0.0, dividend_yield=0.0,
                                  underlying_today=100.0, value_today=5.0, weights=worlds.weights(
                                      [{"id": w, "p": x} for w, x in p.items()]),
                                  levels={"no": 104.0, "prop:20": 92.0, "prop:21": 97.0})
    assert math.fsum(x * valued["values"][w] for w, x in p.items()) == pytest.approx(5.0, abs=1e-8)


def test_a_fund_stated_against_both_references_measures_its_residual_from_where_it_stands_unstated(tmp_path):
    """MATH.md 1.4, 1.6: a fund the look-through expands, holding NVDA, stated -12% as its whole move in one way and -5%
    as its residual in the other, where NVDA is stated +12% whole. Its residual is measured from where it stands
    unstated in that way, NVDA's move through it included, whatever another world states: its whole move there is
    that world's unstated move compounded with -5%, as with the same residual and nothing stated against the baseline
    elsewhere; and the fund is placed once."""
    from test_impact import holding_world
    from harness import schema
    w = holding_world(tmp_path)
    e = event(w, p=0.5)
    first = way(w, e, "first", side="yes", weight=0.5)
    second = way(w, e, "second", side="yes")
    w.store.link(second, "consequence", "name:NVDA", {"shock": stated(0.12)})
    residual = {"shock": stated({"value": -0.05, "against": schema.FOLLOWS})}
    on_second = w.store.link(second, "consequence", "name:FUND", residual)["id"]
    alone = w.store.engine.value("impact", e).output  # the residual with nothing against the baseline elsewhere
    w.store.link(first, "consequence", "name:FUND", {"shock": stated(-0.12)})
    both = w.store.engine.value("impact", e).output
    assert both["whole"]["name:FUND"][first] == pytest.approx(-0.12)
    assert both["whole"]["name:FUND"][second] == pytest.approx(alone["whole"]["name:FUND"][second], abs=1e-9)
    assert both["d"]["name:FUND"][second] == pytest.approx(-0.05, abs=1e-9)
    w.store.edit(on_second, "shock", stated({"value": 0.0, "against": schema.FOLLOWS}))
    unstated = w.store.engine.value("impact", e).output["whole"]["name:FUND"][second]
    assert (1 + both["whole"]["name:FUND"][second]) == pytest.approx((1 + unstated) * 0.95)
    nvda_through = w.store.engine.value("impact", e).output["d"]["name:FUND"][second]
    assert nvda_through == pytest.approx(0.0, abs=1e-9)  # at 0 its residual is 0: NVDA's move is in where it stands
