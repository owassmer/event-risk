"""Joint probability direction, row-preserving prices, and executable protection."""
import itertools

import numpy as np
import pytest

from harness import schema
from harness.derive.markets import order_cost
from harness.derive.together import joint_levels, price_weights, probabilities, read
from harness.ledger import ledger
from harness.tools import Tools
from harness.store import StoreError
from test_impact import channel, event, world
from test_worth import holding, worth


def chance(of, value, yes=(), no=()):
    return {"key": str((of, yes, no)), "of": of, "value": value, "given": list(yes), "given_not": list(no)}


def test_common_effect_does_not_correlate_its_two_causes():
    m = {"props": ["A", "B", "C"], "links": [], "chances": []}
    for a, b in itertools.product((False, True), repeat=2):
        m["chances"].append(chance("C", {(1, 1): .9, (1, 0): .5, (0, 1): .4, (0, 0): .1}[a, b],
                                    [p for p, v in (("A", a), ("B", b)) if v],
                                    [p for p, v in (("A", a), ("B", b)) if not v]))
    atoms, p, _ = probabilities(m, {"A": .3, "B": .4, "C": None})
    assert sum(v for a, v in zip(atoms, p) if a["A"] and a["B"]) == pytest.approx(.12)


def test_events_within_a_third_are_independent_given_it():
    m = {"props": ["A", "B", "C"], "chances": [], "links": [
        {"id": "a", "relation": "implies", "from": "A", "to": "C"},
        {"id": "b", "relation": "implies", "from": "B", "to": "C"}]}
    atoms, p, _ = probabilities(m, {"A": .2, "B": .3, "C": .4})
    assert sum(v for a, v in zip(atoms, p) if a["A"] and a["B"]) == pytest.approx(.15)


def test_factorized_read_matches_full_directed_atoms_and_retains_composite_observations():
    rng = np.random.default_rng(711)
    for _ in range(6):
        props = list("ABCDEFG")
        m = {"props": props, "links": [], "chances": []}
        points = {p: float(rng.uniform(.1, .9)) if i < 2 else None for i, p in enumerate(props)}
        for i, p in enumerate(props[2:], 2):
            parents = list(rng.choice(props[:i], 2, replace=False))
            for values in itertools.product((False, True), repeat=2):
                m["chances"].append(chance(p, float(rng.uniform(.05, .95)),
                                          [a for a, v in zip(parents, values) if v],
                                          [a for a, v in zip(parents, values) if not v]))
        atoms, probabilities_, _ = probabilities(m, points)
        reduced, values, _ = probabilities(m, points, keep={"A", "G"})
        for state, value in zip(reduced, values):
            wanted = sum(p for a, p in zip(atoms, probabilities_) if all(a[k] == v for k, v in state.items()))
            assert value == pytest.approx(wanted, abs=2e-10)
    m = {"props": ["A", "B", "both"], "chances": [], "links": [
        {"id": "x", "relation": "and", "from": "both", "to": "A"}, {"id": "y", "relation": "and", "from": "both", "to": "B"}]}
    atoms, p, _ = probabilities(m, {"A": .3, "B": .4, "both": .2}, keep={"A", "B"})
    assert sum(v for a, v in zip(atoms, p) if a["A"] and a["B"]) == pytest.approx(.2)


def test_sparse_long_chain_is_read_without_materializing_every_history():
    props = [f"p{i}" for i in range(24)]
    m = {"props": props, "links": [], "chances": []}
    for previous, node in zip(props, props[1:]):
        m["chances"] += [chance(node, .8, [previous]), chance(node, .2, no=[previous])]
    atoms, p, _ = probabilities(m, {node: .5 for node in props}, keep={props[0], props[-1]})
    assert len(atoms) == 4
    same = sum(v for a, v in zip(atoms, p) if a[props[0]] == a[props[-1]])
    assert same == pytest.approx(.5 + .5 * .6 ** 23)


def test_exclusive_events_keep_their_conditional_price_ratios():
    events = [{"worlds": ["yes", "no"], "baseline": "no"}] * 2
    cells = [("yes", "no"), ("no", "yes"), ("no", "no")]
    p = np.array([.3, .3, .4])
    v = joint_levels(events, cells, p, [{"yes": .9, "no": 1}, {"yes": 1.1, "no": 1}])
    assert v[0] / ((.3 * v[1] + .4) / .7) == pytest.approx(.9)
    assert v[1] / ((.3 * v[0] + .4) / .7) == pytest.approx(1.1)
    assert v[:2] == pytest.approx([.9249189324020978, 1.0646046395609985])


def test_pricing_weights_keep_both_marginals_and_the_odds_ratio():
    cells = list(itertools.product(("yes", "no"), repeat=2))
    p = np.array([.2, .1, .2, .5])
    events = [{"worlds": ["yes", "no"]}] * 2
    outputs = [{"worlds": {"yes": {"q": .25}, "no": {"q": .75}}},
               {"worlds": {"yes": {"q": .4}, "no": {"q": .6}}}]
    q = price_weights(cells, p, events, outputs).reshape(2, 2)
    assert q.sum(1) == pytest.approx([.25, .75])
    assert q.sum(0) == pytest.approx([.4, .6])
    assert q[0, 0] * q[1, 1] / q[0, 1] / q[1, 0] == pytest.approx(5)


def test_buying_yes_and_no_walks_the_executable_side():
    book = {"asks": [[.31, 500], [.33, 800]], "bids": [[.29, 800], [.27, 800]]}
    free = {"taker_rate": 0}  # a market that charges no fee
    assert order_cost(book, "yes", 1000, free)["cost"] == pytest.approx(320)
    assert order_cost(book, "no", 1000, free)["cost"] == pytest.approx(714)


def test_joint_view_uses_the_store_valuation_and_keeps_single_rows(tmp_path):
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    a, b = event(w, "A", .3), event(w, "B", .4)
    spy = channel(w, "SPY")
    w.link(a, "consequence", spy, shock=-0.1)
    w.link(b, "consequence", spy, shock=0.2)
    alone = ledger(w.store)
    got = ledger(w.store, [a, b])
    assert [r["id"] for r in got["rows"]] == [a, b]
    assert got["rows"] == alone["rows"]
    joint = got["together"]
    assert "why" not in joint
    assert len(joint["worlds"]) == 4
    assert joint["expected"] == pytest.approx(0, abs=1e-7)
    both = next(v for v in joint["worlds"] if all(x == "yes" for x in v["outcomes"].values()))
    assert both["p"] == pytest.approx(.12)
    assert both["A"] == pytest.approx(10000 * (.9 * 1.2 / (.97 * 1.08) - 1))
    assert w.store.computation(joint["basis"])["derivation"] == "together"
    assert joint["distribution"]["variance"]["total"] >= 0


def test_single_event_joint_view_preserves_a_company_shock_inside_a_fund(tmp_path):
    from massive_data import put_reference, put_constituents
    w = world(tmp_path, levels={"AAA": 100, "FUND": 100})
    put_reference(w.store, "AAA", "CS")
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("AAA", 1., "Equity")])
    w.store.fixture["betas"] = {"AAA": {"SPY": 1}, "FUND": {}}
    w.store.link("portfolio", "holds", "name:FUND", {"dollars": 1000})
    e = event(w, p=.5)
    shock = w.link(e, "consequence", "name:AAA", shock=-0.5)
    alone = w.store.engine.value("impact", e).output
    joint = read(w.store, [e])
    assert "why" not in joint
    for state in joint["worlds"]:
        assert state["A"] == pytest.approx(alone["worlds"][state["outcomes"][e]]["A"])
    w.store.edit(shock, "shock", {"value": -1, "note": "The company is worth zero in this world."})
    w.link(e, "consequence", channel(w, "SPY"), shock=1.0)
    floored = w.store.engine.value("impact", e).output
    joint = read(w.store, [e])
    assert floored["expected"] > 0
    assert joint["expected"] == pytest.approx(floored["expected"])


def test_mutation_and_ledger_receipts_read_centres_without_eager_horizon_draws(tmp_path):
    from harness.world import World
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    e = event(w, p=.4)
    tools = Tools(World(w.store, w.venues))
    ok, result = tools.call("link", {"from_id": e, "relation": "consequence", "to_id": channel(w, "SPY"),
                                    "fields": {"shock": {"value": -0.1, "note": "The price movement."}}})
    assert ok and result["events"][0]["row"]["full_impact"] < 0
    assert ledger(w.store)["rows"][0]["full_impact"] < 0
    assert not w.store._all("SELECT id FROM computation WHERE derivation='distribution'")
    full = tools.get(e)
    assert full["derived"]["distribution"]["value"]["variance"]["total"] > 0
    assert w.store._all("SELECT id FROM computation WHERE derivation='distribution'")


def test_a_factor_quoted_as_a_yield_moves_in_points_of_that_yield_in_the_joint_worlds(tmp_path):
    """MATH.md 1.3, 1.9: long Treasuries quoted as a yield at a duration of 17; A, at 0.3, states +0.5 points on it and B,
    at 0.4, -0.2 points, each against its No side, with nothing stated between them. Read together, each joint world's
    move against the one where neither happens compounds the rows' moves, so in points of the yield they add: +0.3
    where both happen, +0.5 and -0.2 where one does, 0 where neither does. The view gives the factor in those points,
    named with its yield; the Computation keeps its proxy's price move, e^(-0.051) - 1 where both happen."""
    import math

    from test_impact import quoted
    w = world(tmp_path, g={"TLT": 50_000.0}, coefficients={"TLT": {}}, levels={"TLT": 90.0})
    tlt = quoted(w, "TLT", 4.2, 17.0)
    a, b = event(w, "A", .3), event(w, "B", .4)
    w.link(a, "consequence", tlt, shock=0.5)
    w.link(b, "consequence", tlt, shock=-0.2)
    got = read(w.store, [a, b])
    assert "why" not in got
    order = sorted(w.store.query("Factor"), key=lambda c: (c["stated"]["order"], c["id"]))
    assert [f["factor"] for f in got["factors"]] == [c["id"] for c in order]  # broad to narrow
    assert [f.get("yield") for f in got["factors"] if f["factor"] == tlt] == [4.2]
    assert all("yield" not in f for f in got["factors"] if f["factor"] != tlt)
    moved = {(v["outcomes"][a], v["outcomes"][b]): v["factors"][tlt] for v in got["worlds"]}
    assert moved == {("yes", "yes"): pytest.approx(0.3), ("yes", "no"): pytest.approx(0.5),
                     ("no", "yes"): pytest.approx(-0.2), ("no", "no"): pytest.approx(0.0, abs=1e-12)}
    kept = w.store.computation(got["basis"])["output"]["worlds"]
    both = next(v for v in kept if v["outcomes"] == {a: "yes", b: "yes"})
    assert both["factors"][tlt] == pytest.approx(math.exp(-0.051) - 1)


def test_check_lists_differing_shocks_on_two_events_true_in_the_same_worlds(tmp_path):
    """MATH.md 1.9, 1.10: events the links leave true in the same worlds have one move of each price between their Yes and
    their No, which check() compares on the prices both state a shock on. A states -10% on the market against its No;
    B -20%; C +10% against its Yes, which is 1/1.1 - 1 = -9.09% from No to Yes; E has two ways on its Yes at 0.5
    each, -5% and -15%, which come to -10% on its Yes. A, B, C and E each imply the next and E implies A, so they
    are true in the same worlds; D is implied by A only, and its -30% is no conflict. Every pair but A and E differs."""
    from harness.ledger import check
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    spy = channel(w, "SPY")
    a = event(w, "A", .3)
    b, c, d, e = (w.prop(t, baseline=base) for t, base in (("B", "no"), ("C", "yes"), ("D", "no"), ("E", "no")))
    for x in (b, c, d, e):
        w.link("portfolio", "watches", x)
    for x, y in ((a, b), (b, c), (c, e), (e, a), (a, d)):
        w.link(x, "implies", y)
    w.chances(d, {"given": [a], "value": 1.0}, {"given_not": [a], "value": 0.5})
    first, second = w.prop("E, mildly"), w.prop("E, sharply")
    w.link(e, "way", first, side="yes")
    w.link(e, "way", second, side="yes")
    w.chances(first, {"given": [e], "value": 0.5})
    shocks = {x: [w.link(x, "consequence", spy, shock=s)] for x, s in ((a, -0.1), (b, -0.2), (c, 0.1), (d, -0.3))}
    shocks[e] = [w.link(first, "consequence", spy, shock=-0.05), w.link(second, "consequence", spy, shock=-0.15)]
    moves = {a: -0.1, b: -0.2, c: 1 / 1.1 - 1, e: -0.1}
    got = check(w.store)["shocks_differ"]
    assert {tuple(x["events"]) for x in got} == {(a, b), (a, c), (b, c), (b, e), (c, e)}
    for x in got:
        assert x["price"] == spy
        assert x["moves"] == {y: pytest.approx(moves[y]) for y in x["events"]}
        assert x["shocks"] == {y: shocks[y] for y in x["events"]}
        assert x["basis"] == {y: w.store.engine.value("impact", y).basis for y in x["events"]}


def test_every_ordinary_price_keeps_its_row_ratios_when_events_exclude_one_another(tmp_path):
    w = world(tmp_path, coefficients={"SPY": {}})
    holding(w, "BETA", 100, 1000)
    w.store.fixture["betas"] = {"BETA": {"SPY": 3}}
    a, b = event(w, "A", .3), event(w, "B", .3)
    both = w.prop("both", chance=0)
    w.link(both, "and", a)
    w.link(both, "and", b)
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.5)
    w.link(b, "consequence", channel(w, "SPY"), shock=0.5)
    got = read(w.store, [a, b])
    assert "why" not in got
    worlds = got["worlds"]
    for e, expected in ((a, .125), (b, 3.375)):
        averages = {}
        for side in ("yes", "no"):
            chosen = [v for v in worlds if v["outcomes"][e] == side]
            averages[side] = sum(v["p"] * (1000 + v["A"]) for v in chosen) / sum(v["p"] for v in chosen)
        assert averages["yes"] / averages["no"] == pytest.approx(expected)


def test_a_stated_joint_world_moves_the_portfolio_and_the_others_stay_where_the_rows_put_them(tmp_path):
    """MATH.md 1.9, evaluation's case: A at 0.30 and B at 0.40 with nothing stated between them, A's row moving the market
    -10% and B's +10%, a $10,000 holding at a beta of 1. Both happening is stated at -5%, then -20%, against the joint
    world where neither does: it stands as stated, the others where the rows alone put them, and every price is placed
    at the joint weights, so the portfolio's P&L moves with the statement (by hand: sum q (1 + s) = 1.004 at -5%)."""
    want = {-0.05: [-537.85, -1035.86, 956.18, -39.84], -0.2: [-1886.41, -872.21, 1156.19, 141.99]}
    for stated, figures in want.items():
        w = world(tmp_path / str(-stated), g={"SPY": 10_000})
        a, b = event(w, "A", .3), event(w, "B", .4)
        spy = channel(w, "SPY")
        w.link(a, "consequence", spy, shock=-0.1)
        w.link(b, "consequence", spy, shock=0.1)
        both = w.prop("both")
        w.link(both, "and", a)
        w.link(both, "and", b)
        w.link(both, "consequence", spy, shock=stated)
        got = read(w.store, [a, b])
        pnl = {tuple(state["outcomes"][e] for e in (a, b)): state["A"] for state in got["worlds"]}
        assert [round(pnl[k], 2) for k in (("yes", "yes"), ("yes", "no"), ("no", "yes"), ("no", "no"))] == figures
        # what is stated of the joint world is shown as stated; every other world as the rows give it
        assert all(isinstance(state["by"], dict) == (set(state["outcomes"].values()) == {"yes"}) for state in got["worlds"])
        assert read(w.store, [b, a])["basis"] == got["basis"]  # the set of events, in any order, is one reading


def test_joint_values_keep_cash_absolute_and_revalue_received_shares(tmp_path):
    w = world(tmp_path, coefficients={"SPY": {}})
    holding(w, "DEAL", 30, 3000)
    holding(w, "BUYER", 100, 1000)
    w.store.fixture["betas"] = {"DEAL": {}, "BUYER": {}}
    a, b = event(w, "deal", .3), event(w, "buyer", .4)
    worth(w, a, "name:DEAL", "yes", {"cash": 10, "date": "2026-11-04", "shares": {"name": "name:BUYER", "quantity": .2}})
    worth(w, a, "name:DEAL", "no", {"amount": 20})
    w.link(b, "consequence", "name:BUYER", shock=0.2)
    got = read(w.store, [a, b])
    assert "why" not in got
    both = next(v for v in got["worlds"] if v["outcomes"][a] == v["outcomes"][b] == "yes")
    buyer = 100 * 1.2 / 1.08
    assert both["A"] == pytest.approx(100 * (10 + .2 * buyer - 30) + 10 * (buyer - 100))


def test_absolute_worth_on_a_joint_world_leaves_the_other_worlds_at_the_rows(tmp_path):
    w = world(tmp_path, coefficients={"SPY": {}})
    holding(w, "COMPANY", 100, 1000)
    w.store.fixture["betas"] = {"COMPANY": {}}
    a, b = event(w, "A", .3), event(w, "B", .4)
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(b, "consequence", channel(w, "SPY"), shock=0.2)
    both = w.prop("both")
    w.link(both, "and", a)
    w.link(both, "and", b)
    w.store.link(both, "worth", "name:COMPANY", {"value": {"value": {"amount": 150}, "note": "the joint value"}})
    got = read(w.store, [a, b])
    assert "why" not in got
    for state in got["worlds"]:
        assert state["A"] == pytest.approx(500 if all(v == "yes" for v in state["outcomes"].values()) else 0, abs=1e-8)


def test_shared_contract_truth_keeps_yes_and_no_complementary(tmp_path):
    from harness.derive.engine import Inputs
    from harness.derive.together import contract_states
    w = world(tmp_path, coefficients={"SPY": {}})
    a, b = event(w, "A", .3), event(w, "B", .4)
    both = w.prop("both")
    w.link(both, "and", a)
    w.link(both, "and", b)
    market = w.market("both", "Both happen?", .1, .14)
    w.link(both, "asks", market, side="yes", taken=False)
    yes = w.store.link("portfolio", "holds", market, {"side": "yes", "quantity": 10})["id"]
    no = w.store.link("portfolio", "holds", market, {"side": "no", "quantity": 10})["id"]
    tree = w.store.engine.value("outcomes", a).output
    states = contract_states(Inputs(w.store.engine), a, tree)
    for outcomes in states.values():
        assert sum(v["p"] for v in outcomes) == pytest.approx(1)
        assert all(v["payoffs"][yes] + v["payoffs"][no] == 1 for v in outcomes)
    assert sum(v["p"] * v["payoffs"][yes] for v in states["yes"]) == pytest.approx(.4)
    assert sum(v["p"] * v["payoffs"][yes] for v in states["no"]) == 0


def test_fee_schedule_is_applied_to_the_executable_fills():
    book = {"asks": [[.31, 500], [.33, 800]], "bids": []}
    got = order_cost(book, "yes", 1000, {"taker_rate": .07, "rounding": "up_to_cent"})  # 0.07 x 217.5, up to the cent
    assert got["fees"] == pytest.approx(15.23) and got["cost"] == pytest.approx(335.23)
    assert order_cost(book, "yes", 1000, {"taker_rate": .095})["fees"] == pytest.approx(20.6625)
    with pytest.raises(ValueError, match="book offers"):
        order_cost(book, "yes", 1301, {"taker_rate": 0})


def test_considered_protection_runs_through_the_tool_and_uses_book_and_curve(tmp_path):
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    a, b = event(w, "A", .3), event(w, "B", .4)
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(b, "consequence", channel(w, "SPY"), shock=0.2)
    market = w.market("protect", "A?", .29, .31)
    w.venues.records[market]["market"]["feesEnabled"] = False
    w.books[market]["asks"] = [{"price": ".31", "size": "500"}, {"price": ".33", "size": "800"}]
    w.link(a, "asks", market, side="yes", taken=False)
    for year, rows in ((2025, []), (2026, [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", str(year), {"results": rows}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    result = Tools(w).ledger(together=[a, b], considering={"market": market, "side": "yes", "quantity": 1000})
    joint = result["together"]
    protection = joint["considering"]
    assert protection["cost"] == pytest.approx(320)
    assert 0 < protection["discount"] < 1
    for state in joint["worlds"]:
        value = protection["worlds"][state["id"]]
        expected = 1000 * protection["discount"] if state["outcomes"][a] == "yes" else 0
        assert value["payout"] == pytest.approx(expected)
        assert value["combined"] == pytest.approx(state["A"] + expected - 320)


def test_a_contract_that_cannot_be_valued_leaves_the_joint_view_and_one_held_returns_its_event(tmp_path):
    """MATH.md 1.9; TOOLS.md 5 (evaluation's 47ae): a contract considered whose fees its market does not state says why
    in its own place, and the joint worlds asked for stand; a contract held returns each event its market bears on, as
    it now stands, the event its proposition is in."""
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    a, b = event(w, "A", .3), event(w, "B", .4)
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(b, "consequence", channel(w, "SPY"), shock=0.2)
    market = w.market("protect", "A?", .29, .31)
    w.venues.records[market]["market"].pop("feesEnabled", None)  # its fees not stated
    w.link(a, "asks", market, side="yes", taken=False)
    joint = Tools(w).ledger(together=[a, b], considering={"market": market, "side": "yes", "quantity": 100})["together"]
    assert len(joint["worlds"]) == 4 and "Treasury" in joint["considering"]["why"]  # no curve read: the view stands
    for year, rows in ((2025, []), (2026, [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", str(year), {"results": rows}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    joint = Tools(w).ledger(together=[a, b], considering={"market": market, "side": "yes", "quantity": 100})["together"]
    assert len(joint["worlds"]) == 4 and "why" not in joint
    assert joint["considering"]["market"] == market and "fee" in joint["considering"]["why"]
    held = Tools(w).link("portfolio", "holds", market, {"side": "yes", "quantity": 100})
    assert [e["id"] for e in held["events"]] == [a]


def test_analyst_can_build_ways_joint_values_and_protection_through_tool_receipts(tmp_path):
    from harness.world import World
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    holding(w, "DEAL", 30, 3000)
    w.store.fixture["betas"] = {"DEAL": {}}
    for year, rows in ((2025, []), (2026, [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", str(year), {"results": rows}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    market = w.market("flow", "Both developments?", .1, .14)
    w.venues.records[market]["market"]["feesEnabled"] = False
    tools = Tools(World(w.store, w.venues))
    def call(name, **args):
        ok, out = tools.call(name, args)
        assert ok, (name, out)
        return out
    def prop(title, p=None, baseline=None):
        fields = {"title": title, "text": title, "comparison": "occurs", "kind": "hitting", "window_end": "2027-03-31"}
        if p is not None:
            fields["chances"] = [{"value": p, "note": "The assessed probability."}]
        if baseline:
            fields["baseline"] = baseline
        return call("add", type="Proposition", fields=fields)["id"]
    a, b = prop("Development A", .3, "no"), prop("Development B", .4, "no")
    for e in (a, b):
        call("link", from_id="portfolio", relation="watches", to_id=e)
    severe, mild = prop("A severe", .1), prop("A mild", .2)
    for e in (severe, mild):
        call("link", from_id=a, relation="way", to_id=e, fields={"side": "yes"})
    for e, shock in ((severe, -0.2), (mild, -0.05), (b, 0.1)):
        call("link", from_id=e, relation="consequence", to_id=channel(w, "SPY"),
             fields={"shock": {"value": shock, "note": "The price in this outcome."}})
    for side, amount in (("yes", 40), ("no", 20)):
        receipt = call("link", from_id=b, relation="worth", to_id="name:DEAL",
                       fields={"world": side, "value": {"value": {"amount": amount}, "note": "The consideration."}})
    assert receipt["events"][0]["levels"]["name:DEAL"]["worlds"] == {"yes": 40, "no": 20}
    joint = prop("A and B")
    for e in (a, b):
        call("link", from_id=joint, relation="and", to_id=e)
    call("link", from_id=joint, relation="worth", to_id="name:DEAL",
         fields={"value": {"value": {"amount": 50}, "note": "The joint consideration."}})
    call("link", from_id=joint, relation="asks", to_id=market,
         fields={"side": "yes", "taken": {"value": False, "note": "The stated event assessments give the probability."}})
    call("link", from_id="portfolio", relation="holds", to_id=market, fields={"side": "yes", "quantity": 20})
    call("note", text="The developments and payment are read as one state.", about_ids=[a, b, joint])
    for e in (a, b, joint, "portfolio"):
        call("get", id=e)
    considering = {"market": market, "side": "yes", "quantity": 100}
    found = call("ledger", together=[a, b], considering=considering)["together"]
    assert "why" not in found and len(found["worlds"]) == 6
    overridden = [v for v in found["worlds"] if v["outcomes"][a] in (severe, mild) and v["outcomes"][b] == "yes"]
    assert len(overridden) == 2 and all(isinstance(v["by"], dict) for v in overridden)
    assert found["considering"]["cost"] == pytest.approx(14)
    assert found["distribution"]["variance"]["total"] > 0
    call("check")


def test_a_fund_stated_against_what_it_follows_is_read_together_as_its_row_settles_it(tmp_path):
    """MATH.md 1.9, 1.6: a fund the look-through expands, stated against what it follows, stands in each joint world where
    its rows settle it; read alone as a joint view its worlds are its row's, and read with another event the call
    answers (the run's IWM, stated as a residual, refused a joint view)."""
    from massive_data import put_reference, put_constituents
    w = world(tmp_path, levels={"AAA": 100, "FUND": 100})
    put_reference(w.store, "AAA", "CS")
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("AAA", 1., "Equity")])
    w.store.fixture["betas"] = {"AAA": {"SPY": 1}, "FUND": {"SPY": 1}}
    w.store.link("portfolio", "holds", "name:FUND", {"dollars": 1000})
    e, other = event(w, p=.5), event(w, "Other", p=.3)
    spy = channel(w, "SPY")
    w.link(e, "consequence", spy, shock=-0.2)
    w.store.link(e, "consequence", "name:FUND", {"shock": {"value": -0.1, "against": schema.FOLLOWS,
                                                          "note": "Beyond what it follows."}})
    w.link(other, "consequence", spy, shock=0.1)
    alone = w.store.engine.value("impact", e).output
    joint = read(w.store, [e])
    assert "why" not in joint
    for state in joint["worlds"]:
        assert state["A"] == pytest.approx(alone["worlds"][state["outcomes"][e]]["A"])
    both = read(w.store, [e, other])
    assert "why" not in both and len(both["worlds"]) == 4


def test_a_fund_valued_by_its_terms_keeps_a_rows_statement_on_it_when_read_together(tmp_path):
    """MATH.md 1.9, 1.6: a fund on one reference stated against what it follows in a row is worth, in each joint world,
    what its terms give there times what the statement does to it: read alone as a joint view, its worlds are its
    row's, the statement kept."""
    from harness.derive.together import read as read_together
    from test_value_and_exposure import store_with
    from rows_fixture import cash, on_reference
    from breakdown import stated
    store = store_with(tmp_path, {"LEV": [on_reference(3.0, [("A", 0.4), ("B", 0.6)], "IDX"), cash(1.0)]},
                       [{"ticker": "LEV", "dollars": 10_000.0}], {"LEV": "ETF", "IDX": "ETF", "A": "CS", "B": "CS"},
                       seed=7)
    try:
        e = store.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                      "window_end": "2027-03-31", "chances": [stated(0.3)], "baseline": "no"})["id"]
        store.link("portfolio", "watches", e)
        spy = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "SPY")
        store.link(e, "consequence", spy, {"shock": stated(-0.2)})
        store.link(e, "consequence", "name:LEV", {"shock": {"value": -0.1, "against": schema.FOLLOWS,
                                                             "note": "Beyond its terms."}})
        alone = store.engine.value("impact", e).output
        joint = read_together(store, [e])
        assert "why" not in joint
        for state in joint["worlds"]:
            assert state["A"] == pytest.approx(alone["worlds"][state["outcomes"][e]]["A"])
    finally:
        store.close()


def test_a_deadline_reading_at_a_constant_hazard_is_read_together_at_the_share_its_structure_gives(tmp_path):
    """MATH.md 1.9, 1.2: a chance that names a reading has no figure of its own; read together, it is the share its
    structure gives, here at a constant hazard between two rungs, and the event keeps its probability alone."""
    w = world(tmp_path, g={"SPY": 10000}, coefficients={"SPY": {}})
    r1 = w.prop("by t1", end="2026-12-31", chance={"value": 0.2})
    r2 = w.prop("by t2", end="2027-03-31", chance={"value": 0.5})
    t = w.prop("by t", end="2027-02-28", baseline="no")
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": "hazard"})
    w.link("portfolio", "watches", t)
    b = event(w, "B", .4)
    spy = channel(w, "SPY")
    w.link(t, "consequence", spy, shock=-0.1)
    w.link(b, "consequence", spy, shock=0.05)
    joint = read(w.store, [t, b])
    assert "why" not in joint
    for e in (t, b):
        on = sum(v["p"] for v in joint["worlds"] if v["outcomes"][e] == "yes")
        assert on == pytest.approx(w.point(e), abs=1e-9)


def test_a_fund_no_event_states_moves_by_its_response_alone_when_dependent_events_are_read_together(tmp_path):
    """MATH.md 1.9, 1.6: read together, every price compounds as its rows give it, a name's response as well as its price,
    so a company no event states has no move beyond its response in any joint world, as in a row, and a fund carrying it
    adds nothing for it. Two events that exclude each other, a fund no event states holding two companies of different
    betas: each event's sides, averaged over the joint worlds, are its row's."""
    from massive_data import put_reference, put_constituents
    w = world(tmp_path, coefficients={"SPY": {}}, levels={"AAA": 100, "BBB": 100, "FUND": 100})
    for t in ("AAA", "BBB"):
        put_reference(w.store, t, "CS")
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("AAA", .5, "Equity"), ("BBB", .5, "Equity")])
    w.store.fixture["betas"] = {"AAA": {"SPY": 2}, "BBB": {"SPY": .5}, "FUND": {"SPY": 1.25}}
    w.store.link("portfolio", "holds", "name:FUND", {"dollars": 1000})
    a, b = event(w, "A", .3), event(w, "B", .3)
    both = w.prop("both", chance=0)
    w.link(both, "and", a)
    w.link(both, "and", b)
    w.link(a, "consequence", channel(w, "SPY"), shock=-0.3)
    w.link(b, "consequence", channel(w, "SPY"), shock=0.2)
    got = read(w.store, [a, b])
    assert "why" not in got
    for e in got["events_read"]:
        for side in ("yes", "no", "full_impact"):
            assert e["together"][side] == pytest.approx(e["alone"][side], abs=1e-6)
