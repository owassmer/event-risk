"""The range the markets imply and the judge's checks of the breakdown, on hand-built structures against
hand-computed ranges (MATH.md 1.2): the least and most each proposition can be with every link kept and each proposition
within the bid and ask of every market taken on it. Each market's book is one level a side, so its bid and ask are the
test's. No bound is scaled by a check, and a claim whose texts differ still bounds."""

from __future__ import annotations

import json

import pytest

from breakdown import ASOF, World
from harness.derive import evidence as ev
from harness.derive.checks import entails, proposition_reading, within
from harness.derive.engine import Inputs
from harness.derive.structure import subject
from harness.ledger import check
from harness.store import StoreError

M1Q = "Will the US raise tariffs by March 31?"
M2Q = "Will the US effective tariff rate be above 30% on March 31, 2027?"
M3Q = "Will the US effective tariff rate be above 20% on March 31, 2027?"


@pytest.fixture
def make(tmp_path):
    made = []

    def build(answers=None, default=0.9):
        w = World(tmp_path / f"scratch-w{len(made)}", answers, default)
        made.append(w)
        return w

    yield build
    for w in made:
        w.store.close()


def worked_row(w):
    """The worked row's breakdown: T watched; L implies T; T implies R, which M1 asks; A30 and A20 ask what M2 and M3 ask; A30
    implies L, and L implies A20."""
    t = w.prop("T", ">=", 25, "hitting")
    l = w.prop("L", ">=", 25, "level_at_date")
    r = w.prop("R")
    a30 = w.prop("A30", ">", 30, "level_at_date")
    a20 = w.prop("A20", ">", 20, "level_at_date")
    w.link("portfolio", "watches", t)
    links = {"L>T": w.link(l, "implies", t), "T>R": w.link(t, "implies", r),
             "R": w.link(r, "asks", w.market("1", M1Q, 0.28, 0.33)),
             "A30": w.link(a30, "asks", w.market("2", M2Q, 0.10, 0.15)),
             "A20": w.link(a20, "asks", w.market("3", M3Q, 0.86, 0.90)),
             "A30>L": w.link(a30, "implies", l), "L>A20": w.link(l, "implies", a20)}
    return {"T": t, "L": l, "R": r, "A30": a30, "A20": a20}, links


def reading_of(w, pid: str) -> dict:
    return w.store.engine.value("structure", subject(w.store, pid)).output["props"][pid]


def test_the_worked_rows_interval_by_logic(make):
    w = make({("implication", "T", "R"): 0.97})
    p, links = worked_row(w)
    assert w.interval(p["R"]) == pytest.approx((0.28, 0.33))  # [bid, ask], and T's floor of 0.10 is under it
    assert w.interval(p["A30"]) == pytest.approx((0.10, 0.15))
    assert w.interval(p["L"]) == pytest.approx((0.10, 0.33))  # floored by A30, which implies it; capped through T
    assert w.interval(p["T"]) == pytest.approx((0.10, 0.33))  # at most R's ask, at least what A30 and L lift
    assert w.interval(p["A20"]) == pytest.approx((0.86, 0.90))  # A20 bounds L from above, which bounds T from neither side
    # The judge's 0.97 is the check of the claim, and scales no bound.
    assert w.derived(links["T>R"], "check") == 0.97 and w.derived(links["T>R"], "terms") is None
    ends = reading_of(w, p["T"])["range"]  # each end with the statements it rests on
    assert set(ends["lo_rests"]) == {links["L>T"], links["A30>L"], f"{links['A30']}:book"}
    assert set(ends["hi_rests"]) == {links["T>R"], f"{links['R']}:book"}
    assert w.derived(links["L>T"], "terms")["holds"] is True and w.derived(links["L>T"], "check") is None  # no question
    assert ("implication", "L", "T") not in w.judge.calls and ("implication", "A30", "L") not in w.judge.calls
    assert (w.derived(links["R"], "bid"), w.derived(links["R"], "ask"), w.derived(links["R"], "price")) == \
        (0.28, 0.33, pytest.approx(0.305))


def test_the_complement_gives_one_minus_each_side(make):
    w = make()
    a = w.prop("A")
    lid = w.link(a, "asks", w.market("1", "Will it not happen?", 0.70, 0.75), side="no")
    assert w.interval(a) == pytest.approx((0.25, 0.30))
    assert (w.derived(lid, "bid"), w.derived(lid, "ask"), w.derived(lid, "price")) == \
        (pytest.approx(0.25), pytest.approx(0.30), pytest.approx(0.275))
    q = [c for c in w.judge.calls if c[0] == "market_forward"]
    assert q == [("market_forward", "A", "Will it not happen?")]


def test_a_side_of_the_book_with_no_orders_bounds_nothing(make):
    w = make()
    a = w.prop("A")
    w.link(a, "asks", w.market("1", "Q?", None, 0.40))
    assert w.interval(a) == pytest.approx((0.0, 0.40))


def test_check_lists_each_link_whose_two_texts_describe_different_outcomes_while_its_claim_stands(make):
    """ONTOLOGY.md 2.2; TOOLS.md 6: a link whose two texts describe different outcomes says what differs, in one
    sentence, and check() lists it with that sentence for as long as the claim stands; the claim still bounds, since it
    is the analyst's. A market that can resolve its own side where its proposition is false, or the other side where it
    is true; a first proposition that can be true where the second is false. A note about the link clears nothing."""
    w = make({("implication", "A", "B"): 0.2, ("market_converse", "B", "Q?"): 0.3,
              ("market_forward", "C", "R?"): 0.1})
    a, b, c = w.prop("A"), w.prop("B"), w.prop("C")
    w.link(a, "asks", w.market("1", "P?", 0.40, 0.45))
    implied = w.link(a, "implies", b)
    q = w.market("2", "Q?", 0.20, 0.60)
    quoted = w.link(b, "asks", q)
    r = w.market("3", "R?", 0.30, 0.35)
    against = w.link(c, "asks", r, side="no")
    assert w.interval(b) == pytest.approx((0.40, 0.60))  # the analyst's claim, whatever its texts describe
    want = [{"link": implied, "relation": "implies", "from": a, "to": b, "differs": f"{a} can be true where {b} is false."},
            {"link": quoted, "relation": "asks", "from": b, "to": q, "differs": f"{q} can resolve Yes where {b} is false."},
            {"link": against, "relation": "asks", "from": c, "to": r, "differs": f"{r} can resolve Yes where {c} is true."}]
    assert check(w.store)["differs"] == want
    w.store.note("The market asks what this proposition says.", [quoted])
    assert check(w.store)["differs"] == want


def test_changing_a_claim_whose_texts_differ_reads_them_again(make):
    w = make({("implication", "A", "B"): 0.2, ("implication", "A", "B2"): 0.95})
    a, b = w.prop("A"), w.prop("B")
    lid = w.link(a, "implies", b)
    assert check(w.store)["differs"][0]["link"] == lid
    w.store.edit(b, "title", "B2")
    assert w.derived(lid, "check") == 0.95 and check(w.store)["differs"] == []


def test_nothing_the_analyst_is_returned_carries_a_reading_of_two_texts(make):
    """Every tool's result as the analyst reads it: a link names what differs where its texts describe different
    outcomes, and no return carries the reading itself, its figures, or the id of a Judgment (ONTOLOGY.md 2; TOOLS.md 5,
    6)."""
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = make({("market_converse", "E", "Q?"): 0.3})
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))
    e = w.prop("E")
    q = w.market("1", "Q?", 0.20, 0.60)
    ok, linked = tools.call("link", {"from_id": e, "relation": "asks", "to_id": q})
    assert ok and linked["differs"] == f"{q} can resolve Yes where {e} is false."
    assert not {"forward", "converse"} & set(linked["derived"])
    seen = [linked]
    for name, args in (("get", {"id": e}), ("get", {"id": q}), ("get", {"id": linked["id"]}), ("check", {}),
                       ("neighbors", {"id": e}), ("query", {"type": "Proposition"})):
        ok, got = tools.call(name, args)
        assert ok, got
        seen.append(got)
    text = json.dumps(seen)
    assert "forward" not in text and "converse" not in text and "jdg:" not in text and "disputed" not in text
    assert seen[4]["differs"][0]["differs"] == linked["differs"]
    for read in (seen[1], seen[2]):  # the link as listed under its proposition and under its market
        (listed,) = [x for x in read["links"] if x["id"] == linked["id"]]
        assert listed["differs"] == linked["differs"]
    jid = w.store._one("SELECT id FROM judgment LIMIT 1")["id"]
    assert tools.call("get", {"id": jid}) == (False, {"error": f"{jid} is not in the store", "dependents": []})
    ok, refused = tools.call("query", {"type": "Judgment"})
    assert not ok and "Judgment" not in refused["error"].split(";", 1)[1]


def test_and_and_or_bound_by_what_their_parts_allow_and_by_what_each_implies(make):
    w = make()
    b, c = w.prop("B"), w.prop("C")
    w.link(b, "asks", w.market("1", "B?", 0.60, 0.70))
    w.link(c, "asks", w.market("2", "C?", 0.50, 0.80))
    both, either = w.prop("B and C"), w.prop("B or C")
    for a, relation in ((both, "and"), (either, "or")):
        w.link(a, relation, b)
        w.link(a, relation, c)
    assert w.interval(both) == pytest.approx((0.10, 0.70))  # max(0, 0.6 + 0.5 - 1), min(0.7, 0.8)
    assert w.interval(either) == pytest.approx((0.60, 1.0))  # max(0.6, 0.5), min(1, 0.7 + 0.8)
    # The composite of and implies its components: a floor on it lifts each. Each component of or implies its
    # composite: a ceiling on it caps each.
    x, y, z = w.prop("X"), w.prop("Y"), w.prop("Z")
    w.link(x, "asks", w.market("3", "X?", 0.55, 0.58))
    w.link(y, "asks", w.market("4", "Y?", 0.30, 0.90))
    w.link(x, "and", y)
    w.link(x, "and", z)
    assert w.interval(y) == pytest.approx((0.55, 0.90)) and w.interval(z) == pytest.approx((0.55, 1.0))
    o, u, v = w.prop("O"), w.prop("U"), w.prop("V")
    w.link(o, "asks", w.market("5", "O?", 0.20, 0.30))
    w.link(o, "or", u)
    w.link(o, "or", v)
    assert w.interval(u) == pytest.approx((0.0, 0.30)) and w.interval(v) == pytest.approx((0.0, 0.30))


def test_a_part_of_and_is_capped_and_a_part_of_or_floored_by_its_composite_and_the_other_part(make):
    """A = B and C at [0.10, 0.15], C at [0.80, 0.90]: B can be true without A only where C is false, so
    hi_B = 1 + 0.15 - 0.80 = 0.35, and A lifts it to 0.10. O = U or V at [0.70, 0.80], V at [0.20, 0.30]: O can be
    true without U only where V is, so lo_U = 0.70 - 0.30 = 0.40, and O caps it at 0.80."""
    w = make()
    a, b, c = w.prop("A"), w.prop("B"), w.prop("C")
    w.link(a, "asks", w.market("1", "A?", 0.10, 0.15))
    w.link(c, "asks", w.market("2", "C?", 0.80, 0.90))
    to_b = w.link(a, "and", b)
    w.link(a, "and", c)
    assert w.interval(b) == pytest.approx((0.10, 0.35)) and w.interval(c) == pytest.approx((0.80, 0.90))
    assert w.interval(a) == pytest.approx((0.10, 0.15))
    assert to_b in reading_of(w, b)["range"]["hi_rests"]
    o, u, v = w.prop("O"), w.prop("U"), w.prop("V")
    w.link(o, "asks", w.market("3", "O?", 0.70, 0.80))
    w.link(v, "asks", w.market("4", "V?", 0.20, 0.30))
    to_u = w.link(o, "or", u)
    w.link(o, "or", v)
    assert w.interval(u) == pytest.approx((0.40, 0.80)) and w.interval(v) == pytest.approx((0.20, 0.30))
    assert to_u in reading_of(w, u)["range"]["lo_rests"]


def test_then_bounds_by_its_first_step_and_implies_both_parts(make):
    w = make()
    b, c = w.prop("B"), w.prop("C")
    w.link(b, "asks", w.market("1", "B?", 0.60, 0.70))
    w.link(c, "asks", w.market("2", "C?", 0.40, 0.50))
    sequence = w.prop("B then C")
    w.link(sequence, "then", b, step=1)
    assert w.interval(sequence) == pytest.approx((0.0, 0.70))  # lo = 0, hi = hi_B
    w.link(sequence, "then", c, step=2)
    assert w.interval(sequence) == pytest.approx((0.0, 0.50))  # and it implies C, which comes after B


def test_a_then_link_without_a_step_or_with_a_step_the_composite_has_is_refused(make):
    w = make()
    b, c, other = w.prop("B"), w.prop("C"), w.prop("D")
    sequence = w.prop("B then C")
    with pytest.raises(StoreError, match="then requires step: 1 for the part that comes first, 2 for the part that follows"):
        w.link(sequence, "then", b)
    with pytest.raises(StoreError, match="step is 1 for the part that comes first"):
        w.link(sequence, "then", b, step=3)
    first = w.link(sequence, "then", b, step=1)
    with pytest.raises(StoreError) as refused:
        w.link(sequence, "then", other, step=1)
    assert first in refused.value.message and refused.value.dependents == [first]
    second = w.link(sequence, "then", c, step=2)
    with pytest.raises(StoreError) as refused:
        w.store.edit(second, "step", 1)
    assert refused.value.dependents == [first]
    assert [(l["to"], l["stated"]["step"]) for l in w.store.links_of(sequence, "then")] == [(b, 1), (c, 2)]


def test_books_that_cannot_hold_through_a_link_give_way_and_the_range_is_read_within_them(make):
    """A at 0.60 to 0.65 implies B at 0.20 to 0.30: no set keeps the link within both books. A's book, $400 deep
    against B's $300, holds; B's is stretched 0.30 above its ask, and each range is read within the stretched books:
    A at most B, at most 0.60, so both are 0.60. Removing the link leaves each book its own."""
    w = make()
    a, b = w.prop("A"), w.prop("B")
    w.link(a, "asks", w.market("1", "A?", 0.60, 0.65))
    qb = w.link(b, "asks", w.market("2", "B?", 0.20, 0.30))
    implied = w.link(a, "implies", b)
    assert w.interval(a) == pytest.approx((0.60, 0.60)) and w.interval(b) == pytest.approx((0.60, 0.60))
    [found] = check(w.store)["stretched"]
    assert found["link"] == qb and found["above"] == pytest.approx(0.30) and found["below"] == 0.0
    w.store.remove(implied)
    assert w.interval(a) == pytest.approx((0.60, 0.65)) and check(w.store)["stretched"] == []


def test_removing_a_link_removes_its_bound(make):
    """Removing a link removes what it carried; what the fields carry stays. A30 and L are levels on one metric on
    one date, so the fields place them on one line whether or not the analyst links them (MATH.md 1.2, "A ladder"):
    removing A30 implies L leaves T's floor at A30's bid. L implies T, a level and a deadline, is the analyst's link
    alone: removing it leaves no market lifting T's floor, and removing T implies R then leaves nothing capping it."""
    w = make()
    p, links = worked_row(w)
    w.store.remove(links["A30>L"])
    assert w.interval(p["T"]) == pytest.approx((0.10, 0.33))  # the line still holds A30 within L
    w.store.remove(links["L>T"])
    assert w.interval(p["T"]) == pytest.approx((0.0, 0.33))  # no market now lifts T's floor
    w.store.remove(links["T>R"])
    assert w.interval(p["T"]) == pytest.approx((0.0, 1.0))  # nothing caps T
    assert w.interval(p["L"]) == pytest.approx((0.10, 0.90))  # L between A30 and A20 on their line


# ---------------------------------------------------------------- what the fields decide


def reading(relation, value, kind="level_at_date", end="2027-03-31", start=None, source="Yale Budget Lab"):
    stated = {"metric": "metric:x", "comparison": relation, "value": value, "kind": kind, "window_end": end}
    if start:
        stated["window_start"] = start
    return proposition_reading(stated, source, ASOF)


def test_strict_relations_in_the_arithmetic_of_a_proposition_pair():
    assert entails(reading(">", 30), reading(">=", 25)) and entails(reading(">", 30), reading(">", 30))
    assert entails(reading(">=", 25), reading(">", 20)) and not entails(reading(">=", 25), reading(">", 25))
    assert entails(reading("<", 20), reading("<=", 20)) and entails(reading("<", 20), reading("<", 25))
    assert not entails(reading("<=", 20), reading("<", 20)) and not entails(reading("<", 20), reading(">", 10))
    assert entails(reading("==", 22), reading(">", 20)) and not entails(reading(">", 20), reading("==", 22))


def _within(a_rel, a, b_rel, b, **upper):
    return within({"relation": a_rel, "value": a, **upper}, {"relation": b_rel, "value": b})


def test_the_fields_decide_thresholds_dates_and_windows():
    assert _within(">=", 25, ">=", 20) and _within(">=", 25, ">", 20) and not _within(">=", 25, ">", 25)
    assert _within(">", 25, ">=", 25) and _within(">", 25, ">", 25) and not _within(">=", 20, ">=", 25)
    assert _within("<=", 3, "<", 4) and not _within("<=", 4, "<", 4) and not _within(">=", 25, "<=", 30)
    # a band, 2.5 < x <= 3, within the rung below its lower end and below its upper end's complement, not above it
    assert _within(">", 2.5, ">", 2.5, upper="<=", upper_value=3) and _within(">", 2.5, "<=", 3, upper="<=", upper_value=3)
    assert not _within(">", 2.5, ">", 3, upper="<=", upper_value=3) and not _within(">", 2.5, "<", 3, upper="<=", upper_value=3)
    level, hit = reading(">=", 25), reading(">=", 25, "hitting", start=ASOF)
    assert entails(level, hit) and not entails(hit, level)  # a level on a date implies hitting over a window with it
    earlier = reading(">=", 25, "hitting", end="2027-01-31", start=ASOF)
    assert entails(earlier, hit) and not entails(hit, earlier)  # hitting by an earlier date implies by a later one
    assert not entails(reading(">=", 25, end="2027-02-26"), level)  # another date
    assert not entails(reading(">=", 25, source="Peterson Institute"), reading(">=", 20))  # another source


# ---------------------------------------------------------------- the checks, as the judge reads them


def test_a_market_check_is_a_plain_question_in_the_words_of_the_texts_it_compares(make):
    w = make()
    a = w.prop("A")
    w.link(a, "asks", w.market("1", "Will it happen?", 0.40, 0.45))
    w.link(a, "asks", w.market("2", "Will it fail?", 0.55, 0.60), side="no")
    asked = {(k[0], k[2]): e for k, e in zip(w.judge.calls, w.judge.asked)}
    statements = [r for r in w.judge.requests]
    assert statements == [[("market_forward", "A", "Will it happen?"), ("market_converse", "A", "Will it happen?")],
                          [("market_forward", "A", "Will it fail?"), ("market_converse", "A", "Will it fail?")]]
    from harness.derive.checks import market_questions
    forward, converse = market_questions("no")
    assert forward[1] == "Does the market resolve No, under its rules, in every outcome in which the proposition is true?"
    assert converse[1] == "Is the proposition true in every outcome in which the market resolves No under its rules?"
    evidence = [e["content"] for e in asked[("market_forward", "Will it happen?")]]
    assert evidence[0] == {"date": ASOF}
    assert evidence[1]["proposition"] == "A" and evidence[1]["says"] == "A: the proposition"
    assert evidence[2]["market"] == "Will it happen?" and evidence[2]["still open"] is True
    text = json.dumps(evidence)
    for notation in ("\"P\"", "M'", "worlds", "role"):
        assert notation not in text


def test_the_judge_reads_neither_notes_nor_searches(make):
    w = make()
    a = w.prop("A")
    w.store.note("A secret argument for A.", [a])
    w.store.record_search("search_markets", {"text": "a"}, ["market:polymarket:1"], proposition_id=a, query="a")
    w.link(a, "asks", w.market("1", "Q?", 0.40, 0.45))
    sent = json.dumps(w.judge.asked)
    assert "secret argument" not in sent
    with pytest.raises(ev.NotSerialized, match="Note"):
        ev.proposition_content(Inputs(w.store.engine), "note:1")


def test_two_thresholds_on_a_listed_tickers_price_that_state_no_source_are_decided_by_the_fields(tmp_path):
    from test_impact import world
    w = world(tmp_path)

    def prop(title, value, source=None):
        return w.store.add("Proposition", {"title": title, "text": f"SPY closes at or above {value} on March 31, 2027.",
                                           "metric": "metric:price:SPY", "comparison": ">=", "value": value,
                                           "window_end": "2027-03-31", "kind": "level_at_date",
                                           **({"resolution_source": source} if source else {})})["id"]
    a, b, c = prop("A", 700), prop("B", 650), prop("C", 700, "NYSE Arca")
    found = w.store.engine.value("implication_check", w.store.link(a, "implies", b)["id"]).output
    assert found["terms"]["holds"] is True and "check" not in found  # the metric fixes what is measured
    stated = w.store.engine.value("implication_check", w.store.link(c, "implies", b)["id"]).output
    assert "terms" not in stated and "check" in stated  # a proposition that states a source is compared by it


def test_an_implication_the_fields_decide_asks_no_question(make):
    w = make()
    t = w.prop("T", ">=", 25, "hitting")
    l = w.prop("L", ">=", 25, "level_at_date")
    lid = w.link(l, "implies", t)
    assert w.derived(lid, "terms")["holds"] is True and w.judge.calls == []


def test_the_fields_decide_both_ways_on_one_metric_and_source(make):
    """Two propositions on one metric and resolution source are compared by their fields both ways (MATH.md 1.4): a claim
    the fields give holds, one they do not give is listed by check() with the values compared, and the judge is asked
    about neither. The containment a composite claims is tested the same way; a market's strike is never compared."""
    w = make()
    at_least_25 = w.prop("x >= 25 on Mar 31", ">=", 25, "level_at_date")
    at_least_20 = w.prop("x >= 20 on Mar 31", ">=", 20, "level_at_date")
    by_june = w.prop("x >= 25 by Jun 30", ">=", 25, "hitting", end="2027-06-30")
    by_march = w.prop("x >= 25 by Mar 31", ">=", 25, "hitting")
    given = w.link(at_least_25, "implies", at_least_20)
    reversed_ = w.link(at_least_20, "implies", at_least_25)
    later = w.link(by_june, "implies", by_march)  # hitting by a later date does not imply hitting by an earlier one
    on_date = w.link(at_least_25, "implies", by_june)  # a level on a date lies within a window of hitting containing it
    both = w.prop("both")
    part = w.link(both, "and", at_least_20)  # untested: both states no level
    either = w.prop("x >= 20 on Mar 31, or more", ">=", 20, "level_at_date")
    narrower = w.link(either, "or", at_least_25)  # a component of or lies within its composite
    wider = w.link(at_least_25, "or", at_least_20)  # x >= 20 does not lie within x >= 25
    assert w.derived(given, "terms")["holds"] is True and w.derived(on_date, "terms")["holds"] is True
    assert w.derived(narrower, "terms")["holds"] is True and w.derived(part, "terms") is None
    assert w.judge.calls == []
    listed = {c["link"]: c for c in check(w.store)["fields_contradict"]}
    assert set(listed) == {reversed_, later, wider}
    assert (listed[reversed_]["within"]["value"], listed[reversed_]["of"]["value"]) == (20, 25)
    assert (listed[later]["within"]["end"], listed[later]["of"]["end"]) == ("2027-06-30", "2027-03-31")
    market = w.link(at_least_25, "asks", w.market("1", "Above 25 on March 31?", 0.4, 0.5))
    assert "terms" not in w.store.get(market)["derived"]
