"""The interval and the judge's checks of the breakdown, on hand-built graphs against
hand-computed intervals. Each market's book is one level a side, so its bid and ask are the test's. Logic carries the
prices: no bound is scaled by a check, and a disputed claim still bounds."""

from __future__ import annotations

import json

import pytest

from breakdown import ASOF, World
from harness.derive import evidence as ev
from harness.derive.checks import entails, proposition_reading, within
from harness.derive.engine import Inputs
from harness.derive.propagate import solve
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
             "R": w.link(r, "quoted_by", w.market("1", M1Q, 0.28, 0.33)),
             "A30": w.link(a30, "quoted_by", w.market("2", M2Q, 0.10, 0.15)),
             "A20": w.link(a20, "quoted_by", w.market("3", M3Q, 0.86, 0.90)),
             "A30>L": w.link(a30, "implies", l), "L>A20": w.link(l, "implies", a20)}
    return {"T": t, "L": l, "R": r, "A30": a30, "A20": a20}, links


def test_the_worked_rows_interval_by_logic(make):
    w = make({("implication", "T", "R"): 0.97})
    p, links = worked_row(w)
    assert w.interval(p["R"]) == pytest.approx((0.28, 0.33))  # [bid, ask], and T's floor of 0.10 is under it
    assert w.interval(p["A30"]) == pytest.approx((0.10, 0.15))
    assert w.interval(p["L"]) == pytest.approx((0.10, 0.33))  # floored by A30, which implies it; capped through T
    assert w.interval(p["T"]) == pytest.approx((0.10, 0.33))  # at most R's ask, at least what A30 and L lift
    assert w.derived(p["T"], "coverage") == pytest.approx(0.77)
    assert w.interval(p["A20"]) == pytest.approx((0.86, 0.90))  # A20 bounds L from above, which bounds T from neither side
    # The judge's 0.97 is the check of the claim, and scales no bound.
    assert w.derived(links["T>R"], "check") == 0.97 and w.derived(links["T>R"], "by_fields") is False
    assert w.derived(links["T>R"], "bound") == {"implying": p["T"], "implied": p["R"], "ceiling": pytest.approx(0.33),
                                                "floor": pytest.approx(0.10)}
    assert w.derived(links["L>T"], "by_fields") is True and w.derived(links["L>T"], "check") is None  # no question
    assert ("implication", "L", "T") not in w.judge.calls and ("implication", "A30", "L") not in w.judge.calls
    assert w.derived(links["R"], "bound") == {"lo": 0.28, "hi": 0.33}
    assert (w.derived(links["R"], "bid"), w.derived(links["R"], "ask"), w.derived(links["R"], "price")) == \
        (0.28, 0.33, pytest.approx(0.305))


def test_the_complement_gives_one_minus_each_side(make):
    w = make()
    a = w.prop("A")
    lid = w.link(a, "quoted_by", w.market("1", "Will it not happen?", 0.70, 0.75), transform="complement")
    assert w.interval(a) == pytest.approx((0.25, 0.30))
    assert (w.derived(lid, "bid"), w.derived(lid, "ask"), w.derived(lid, "price")) == \
        (pytest.approx(0.25), pytest.approx(0.30), pytest.approx(0.275))
    q = [c for c in w.judge.calls if c[0] == "market_forward"]
    assert q == [("market_forward", "A", "Will it not happen?")]


def test_a_side_of_the_book_with_no_orders_bounds_nothing(make):
    w = make()
    a = w.prop("A")
    w.link(a, "quoted_by", w.market("1", "Q?", None, 0.40))
    assert w.interval(a) == pytest.approx((0.0, 0.40))


def test_a_disputed_claim_still_bounds_and_check_lists_it_until_a_note_answers_it(make):
    w = make({("implication", "A", "B"): 0.2, ("market_converse", "B", "Q?"): 0.3})
    a, b = w.prop("A"), w.prop("B")
    w.link(a, "quoted_by", w.market("1", "P?", 0.40, 0.45))
    implied = w.link(a, "implies", b)
    quoted = w.link(b, "quoted_by", w.market("2", "Q?", 0.20, 0.60))
    assert w.interval(b) == pytest.approx((0.40, 0.60))  # the analyst's claim, whatever the judge reads
    disputed = check(w.store)["disputed"]
    assert [(d["link"], d["check"], d["answer"]) for d in disputed] == [
        (quoted, "market, converse", 0.3), (implied, "implication", 0.2)]
    w.store.note("The judge reads the market's 'any' as 'all'; its rules say any.", [quoted])
    assert [d["link"] for d in check(w.store)["disputed"]] == [implied]  # a note about the link answers it


def test_changing_a_disputed_claim_asks_again(make):
    w = make({("implication", "A", "B"): 0.2, ("implication", "A", "B2"): 0.95})
    a, b = w.prop("A"), w.prop("B")
    lid = w.link(a, "implies", b)
    assert check(w.store)["disputed"][0]["link"] == lid
    w.store.edit(b, "title", "B2")
    assert w.derived(lid, "check") == 0.95 and check(w.store)["disputed"] == []


def test_and_and_or_bound_by_their_table_and_by_what_each_implies(make):
    w = make()
    b, c = w.prop("B"), w.prop("C")
    w.link(b, "quoted_by", w.market("1", "B?", 0.60, 0.70))
    w.link(c, "quoted_by", w.market("2", "C?", 0.50, 0.80))
    both, either = w.prop("B and C"), w.prop("B or C")
    for a, relation in ((both, "and"), (either, "or")):
        w.link(a, relation, b)
        w.link(a, relation, c)
    assert w.interval(both) == pytest.approx((0.10, 0.70))  # max(0, 0.6 + 0.5 - 1), min(0.7, 0.8)
    assert w.interval(either) == pytest.approx((0.60, 1.0))  # max(0.6, 0.5), min(1, 0.7 + 0.8)
    # The composite of and implies its components: a floor on it lifts each. Each component of or implies its
    # composite: a ceiling on it caps each.
    x, y, z = w.prop("X"), w.prop("Y"), w.prop("Z")
    w.link(x, "quoted_by", w.market("3", "X?", 0.55, 0.58))
    w.link(y, "quoted_by", w.market("4", "Y?", 0.30, 0.90))
    w.link(x, "and", y)
    w.link(x, "and", z)
    assert w.interval(y) == pytest.approx((0.55, 0.90)) and w.interval(z) == pytest.approx((0.55, 1.0))
    o, u, v = w.prop("O"), w.prop("U"), w.prop("V")
    w.link(o, "quoted_by", w.market("5", "O?", 0.20, 0.30))
    w.link(o, "or", u)
    w.link(o, "or", v)
    assert w.interval(u) == pytest.approx((0.0, 0.30)) and w.interval(v) == pytest.approx((0.0, 0.30))


def test_then_bounds_by_its_first_step_and_implies_both_parts(make):
    w = make()
    b, c = w.prop("B"), w.prop("C")
    w.link(b, "quoted_by", w.market("1", "B?", 0.60, 0.70))
    w.link(c, "quoted_by", w.market("2", "C?", 0.40, 0.50))
    sequence = w.prop("B then C")
    w.link(sequence, "then", b, step=1)
    assert w.interval(sequence) == pytest.approx((0.0, 0.70))  # lo = 0, hi = hi_B
    w.link(sequence, "then", c, step=2)
    assert w.interval(sequence) == pytest.approx((0.0, 0.50))  # and it implies C, which comes after B
    links = [{"id": "qb", "relation": "quoted_by", "from": "B", "to": "mb", "quote": {"bid": 0.60, "ask": 0.70}},
             {"id": "t1", "relation": "then", "from": "A", "to": "B", "step": 1}]
    lo, hi, _, src_hi = solve(["A", "B"], links)
    assert (lo["A"], hi["A"]) == pytest.approx((0.0, 0.70)) and src_hi["A"] == {"t1", "qb"}


def test_a_then_link_without_a_step_or_with_a_step_the_composite_has_is_refused(make):
    w = make()
    b, c, other = w.prop("B"), w.prop("C"), w.prop("D")
    sequence = w.prop("B then C")
    with pytest.raises(StoreError, match="then requires step: 1 for B, the part that comes first"):
        w.link(sequence, "then", b)
    with pytest.raises(StoreError, match="step is 1 for B"):
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


def test_bounds_that_cross_leave_the_proposition_with_no_interval_and_check_lists_what_set_each_side(make):
    w = make()
    a, b = w.prop("A"), w.prop("B")
    qa = w.link(a, "quoted_by", w.market("1", "A?", 0.60, 0.65))
    qb = w.link(b, "quoted_by", w.market("2", "B?", 0.20, 0.30))
    implied = w.link(a, "implies", b)  # A at least 0.60 and B at most 0.30: one of the three is wrong
    got = w.store.get(a)["derived"]
    assert got["lo"]["value"] is None and got["hi"]["value"] is None and got["coverage"]["value"] is None
    assert "its bounds cross: at least 0.6, set by " + qa in got["lo"]["reason"]
    crossed = {c["id"]: c for c in check(w.store)["crossed"]}
    assert crossed[a]["lo"] == pytest.approx(0.60) and crossed[a]["hi"] == pytest.approx(0.30)
    assert crossed[a]["lo_from"] == [qa] and crossed[a]["hi_from"] == [qb, implied]
    assert set(crossed) == {a, b}  # B's floor from A crosses B's ceiling too
    w.store.remove(implied)
    assert w.interval(a) == pytest.approx((0.60, 0.65)) and check(w.store)["crossed"] == []


def test_removing_a_link_removes_its_bound(make):
    w = make()
    p, links = worked_row(w)
    w.store.remove(links["A30>L"])
    assert w.interval(p["T"]) == pytest.approx((0.0, 0.33))  # no market now lifts T's floor
    w.store.remove(links["T>R"])
    assert w.interval(p["T"]) == pytest.approx((0.0, 1.0))  # nothing caps T: L, capped by A20, only lifts T's floor
    assert w.interval(p["L"]) == pytest.approx((0.0, 0.90))


# ---------------------------------------------------------------- what the fields decide


def reading(relation, value, kind="level_at_date", end="2027-03-31", start=None, source="Yale Budget Lab"):
    stated = {"metric": "metric:x", "relation": relation, "value": value, "kind": kind, "window_end": end}
    if start:
        stated["window_start"] = start
    return proposition_reading(stated, source, ASOF)


def test_strict_relations_in_the_arithmetic_of_a_proposition_pair():
    assert entails(reading(">", 30), reading(">=", 25)) and entails(reading(">", 30), reading(">", 30))
    assert entails(reading(">=", 25), reading(">", 20)) and not entails(reading(">=", 25), reading(">", 25))
    assert entails(reading("<", 20), reading("<=", 20)) and entails(reading("<", 20), reading("<", 25))
    assert not entails(reading("<=", 20), reading("<", 20)) and not entails(reading("<", 20), reading(">", 10))
    assert entails(reading("==", 22), reading(">", 20)) and not entails(reading(">", 20), reading("==", 22))


def test_the_fields_decide_thresholds_dates_and_windows():
    assert within(">=", 25, ">=", 20) and within(">=", 25, ">", 20) and not within(">=", 25, ">", 25)
    assert within(">", 25, ">=", 25) and within(">", 25, ">", 25) and not within(">=", 20, ">=", 25)
    assert within("<=", 3, "<", 4) and not within("<=", 4, "<", 4) and not within(">=", 25, "<=", 30)
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
    w.link(a, "quoted_by", w.market("1", "Will it happen?", 0.40, 0.45))
    w.link(a, "quoted_by", w.market("2", "Will it fail?", 0.55, 0.60), transform="complement")
    asked = {(k[0], k[2]): e for k, e in zip(w.judge.calls, w.judge.asked)}
    statements = [r for r in w.judge.requests]
    assert statements == [[("market_forward", "A", "Will it happen?"), ("market_converse", "A", "Will it happen?")],
                          [("market_forward", "A", "Will it fail?"), ("market_converse", "A", "Will it fail?")]]
    from harness.derive.checks import market_questions
    forward, converse = market_questions("A: the proposition", "Will it fail?", "complement")
    assert forward[1] == "If \u201cA: the proposition\u201d is true, does the market \u201cWill it fail?\u201d resolve No?"
    assert converse[1] == "If the market \u201cWill it fail?\u201d resolves No, is \u201cA: the proposition\u201d true?"
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
    w.store.record_search("search_markets", {"q": "a"}, ["market:polymarket:1"], proposition_id=a,
                          venue="polymarket", endpoint="/public-search")
    w.link(a, "quoted_by", w.market("1", "Q?", 0.40, 0.45))
    sent = json.dumps(w.judge.asked)
    assert "secret argument" not in sent and "public-search" not in sent
    with pytest.raises(ev.NotSerialized, match="Note"):
        ev.proposition_content(Inputs(w.store.engine), "note:1")


def test_an_implication_the_fields_decide_asks_no_question(make):
    w = make()
    t = w.prop("T", ">=", 25, "hitting")
    l = w.prop("L", ">=", 25, "level_at_date")
    lid = w.link(l, "implies", t)
    assert w.derived(lid, "by_fields") is True and w.judge.calls == []
