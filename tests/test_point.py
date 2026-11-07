"""The point: the price of a market that asks the proposition, else the analyst's derivation by its row of
the table, each proposition it reads at its own point, else the stated chance. Each row on a hand-built graph against
a hand-computed point. Each market's book is one level a side, so its price is the midpoint the test sets."""

from __future__ import annotations

import pytest

from breakdown import World
from harness.derive import logic
from harness.ledger import check
from harness.store import StoreError


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "scratch-point")
    yield world
    world.store.close()


def priced(w, title, bid, ask, vid=None):
    """A proposition a market asks, its price the midpoint of bid and ask."""
    pid = w.prop(title)
    w.link(pid, "quoted_by", w.market(vid or title, f"{title}?", bid, ask))
    return pid


def given(*ids):
    return {"given": list(ids)}


def test_a_market_that_asks_the_proposition_gives_its_price(w):
    e = priced(w, "E", 0.30, 0.34)
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.32)
    out = w.store.engine.value("point", e).output
    assert out["rule"] == "market" and out["market"] == "market:polymarket:E"


def test_of_several_markets_that_ask_it_the_deepest_gives_the_price(w):
    e = priced(w, "E", 0.30, 0.34)
    w.link(e, "quoted_by", w.market("deep", "E, deeper?", 0.40, 0.44))
    w.set_book("market:polymarket:deep", 0.40, 0.44, size=50000.0)
    w.store.engine.reset()
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.42)


def test_a_directly_priced_event_gains_no_new_forecast_when_a_neighbouring_market_is_linked(w):
    e = priced(w, "E", 0.30, 0.34)
    w.link("portfolio", "watches", e)
    before = w.point(e)
    b = priced(w, "B", 0.50, 0.60)
    w.link(e, "implies", b)
    a = priced(w, "A", 0.10, 0.12)
    w.link(a, "implies", e)
    assert w.point(e) == before == pytest.approx(0.32)  # the neighbours bound E and forecast nothing of their own
    assert w.interval(e) == pytest.approx((0.30, 0.34))


def test_narrower_reads_b_which_e_implies(w):
    e, b = w.prop("E"), priced(w, "B", 0.40, 0.50)
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.derive(e, [b], {"of": e, **given(b), "value": 0.6})
    assert w.point(e) == pytest.approx(0.6 * 0.45)
    out = w.store.engine.value("point", e).output
    assert out["row"] == "narrower" and out["reads"] == {b: pytest.approx(0.45)}


def test_broader_reads_a_which_implies_e(w):
    e, a = w.prop("E"), priced(w, "A", 0.18, 0.22)
    w.link(a, "implies", e)
    w.link("portfolio", "watches", e)
    w.derive(e, [a], {"given_not": [a], "value": 0.1})  # of omitted: the proposition itself
    assert w.point(e) == pytest.approx(0.2 + 0.1 * 0.8)
    assert w.store.stated_of(e)["derivation"]["chances"][0]["of"] == e


def test_between_reads_two_markets_and_moving_either_moves_the_point(w):
    e, a, b = w.prop("E"), priced(w, "A", 0.08, 0.12), priced(w, "B", 0.45, 0.55)
    w.link(a, "implies", e)
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.derive(e, [a, b], {"given": [b], "given_not": [a], "value": 0.25})
    assert w.point(e) == pytest.approx(0.1 + 0.25 * (0.5 - 0.1))
    w.set_book("market:polymarket:A", 0.18, 0.22)
    w.reread("market:polymarket:A")
    assert w.point(e) == pytest.approx(0.2 + 0.25 * (0.5 - 0.2))  # A moved
    w.set_book("market:polymarket:B", 0.65, 0.75)
    w.reread("market:polymarket:B")
    assert w.point(e) == pytest.approx(0.2 + 0.25 * (0.7 - 0.2))  # B moved
    assert w.interval(e) == pytest.approx((0.18, 0.75))


def test_a_driver_reads_d_through_both_of_its_outcomes(w):
    e, d = w.prop("E"), priced(w, "D", 0.28, 0.32)
    w.link("portfolio", "watches", e)
    w.derive(e, [d], {**given(d), "value": 0.8}, {"given_not": [d], "value": 0.1})
    assert w.point(e) == pytest.approx(0.3 * 0.8 + 0.7 * 0.1)
    w.set_book("market:polymarket:D", 0.58, 0.62)
    w.reread("market:polymarket:D")
    assert w.point(e) == pytest.approx(0.6 * 0.8 + 0.4 * 0.1)  # the driver's other outcome weighs more now
    assert w.interval(e) == (0.0, 1.0)  # the driver bounds nothing: no link relates it to E


def test_part_reads_b_where_e_is_b_and_c_or_b_then_c(w):
    b, c = priced(w, "B", 0.40, 0.50), w.prop("C")
    both = w.prop("B and C")
    w.link(both, "and", b)
    w.link(both, "and", c)
    w.link("portfolio", "watches", both)
    w.derive(both, [b], {"of": c, **given(b), "value": 0.5})
    assert w.point(both) == pytest.approx(0.45 * 0.5)
    seq = w.prop("B then C")
    w.link(seq, "then", b, step=1)
    w.link(seq, "then", c, step=2)
    w.link("portfolio", "watches", seq)
    w.derive(seq, [b], {"of": c, **given(b), "value": 0.3})
    assert w.point(seq) == pytest.approx(0.45 * 0.3)
    with pytest.raises(StoreError, match="the part that follows given the part that comes first"):
        w.derive(seq, [c], {"of": b, **given(c), "value": 0.3})


def test_either_reads_b_and_c_where_e_is_b_or_c(w):
    b, c = priced(w, "B", 0.40, 0.50), priced(w, "C", 0.28, 0.32)
    e = w.prop("B or C")
    w.link(e, "or", b)
    w.link(e, "or", c)
    w.link("portfolio", "watches", e)
    w.derive(e, [b, c], {"of": c, **given(b), "value": 0.2})
    assert w.point(e) == pytest.approx(0.45 + 0.3 - 0.45 * 0.2)
    w.derive(e, [b, c], {"of": c, **given(b), "value": 0.0})  # they cannot both happen
    assert w.point(e) == pytest.approx(0.75)


def test_other_reads_b_where_e_is_b_or_c(w):
    b, c = priced(w, "B", 0.40, 0.50), w.prop("C")
    e = w.prop("B or C")
    w.link(e, "or", b)
    w.link(e, "or", c)
    w.link("portfolio", "watches", e)
    w.derive(e, [b], {"of": c, "given_not": [b], "value": 0.1})
    assert w.point(e) == pytest.approx(0.45 + 0.55 * 0.1)


def test_each_input_is_at_its_own_point_through_a_chain_of_derivations(w):
    a = priced(w, "A", 0.18, 0.22)
    b, e = w.prop("B"), w.prop("E")
    w.link(a, "implies", b)
    w.link(e, "implies", b)
    w.derive(b, [a], {"given_not": [a], "value": 0.5})  # p_B = 0.2 + 0.5 * 0.8 = 0.6
    w.link("portfolio", "watches", e)
    w.derive(e, [b], {"of": e, **given(b), "value": 0.5})
    assert w.point(e) == pytest.approx(0.5 * 0.6)
    assert w.point(b) == pytest.approx(0.6)  # derived because a derivation reads it
    assert w.point(a) == pytest.approx(0.2)  # and A, which B's derivation reads
    c = priced(w, "C", 0.50, 0.60)
    assert "p" not in w.store.get(c)["derived"]  # C is neither watched nor read: it has no point field


def test_where_nothing_reaches_it_the_point_is_the_stated_chance(w):
    e = w.prop("E", chance={"value": 0.04})
    w.link("portfolio", "watches", e)
    assert w.point(e) == 0.04 and w.interval(e) == (0.0, 1.0)
    assert w.derived(e, "coverage") == 0.0
    with pytest.raises(StoreError, match="its derivation or its chance, not both"):
        w.derive(e, [w.prop("B")], {"given": ["prop:2"], "value": 0.5})
    with pytest.raises(StoreError, match="a chance, from 0 to 1"):
        w.store.edit(e, "chance", 1.2)


def test_a_watched_proposition_with_nothing_to_give_it_a_point_says_why_and_check_lists_it(w):
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    assert w.point(e) is None and "no market asks it, and it states no derivation or chance" in w.reason(e, "p")
    assert [x["id"] for x in check(w.store)["watched_without_point"]] == [e]


def test_a_derivation_that_fits_no_rule_is_refused_with_the_rule_it_needs(w):
    e, b = w.prop("E"), priced(w, "B", 0.40, 0.50)
    with pytest.raises(StoreError, match="no link says prop:1 implies prop:2"):
        w.derive(e, [b], {**given(b), "value": 0.6})  # P(E given B) needs E to imply B
    with pytest.raises(StoreError, match="fits no rule of the point"):
        w.derive(e, [b], {"of": b, "given": [e], "value": 0.6})
    with pytest.raises(StoreError, match="prop:99, which is not a proposition"):
        w.derive(e, ["prop:99"], {"given": ["prop:99"], "value": 0.6})
    w.link(b, "implies", e)
    with pytest.raises(StoreError, match="the links say prop:2 implies prop:1"):
        w.derive(e, [b], {**given(b), "value": 0.8}, {"given_not": [b], "value": 0.1})  # a driver neither way
    assert w.store.stated_of(e).get("derivation") is None  # nothing written


def test_a_derivation_whose_links_go_has_no_point_and_says_why(w):
    e, b = w.prop("E"), priced(w, "B", 0.40, 0.50)
    lid = w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.derive(e, [b], {**given(b), "value": 0.6})
    w.store.remove(lid)
    assert w.point(e) is None and "no link says prop:1 implies prop:2" in w.reason(e, "p")
    with pytest.raises(StoreError) as refused:
        w.store.remove(b)  # a proposition a derivation names stays while it names it
    assert refused.value.dependents == [f"{e}.derivation"]


def test_a_cycle_of_derivations_is_refused(w):
    a, b = w.prop("A"), w.prop("B")
    w.derive(a, [b], {**given(b), "value": 0.5}, {"given_not": [b], "value": 0.1})
    with pytest.raises(StoreError, match="a point cannot read itself: prop:2 reads prop:1 reads prop:2"):
        w.derive(b, [a], {**given(a), "value": 0.5}, {"given_not": [a], "value": 0.1})


def test_a_point_outside_its_interval_stands_and_check_lists_it(w):
    e, b = w.prop("E"), priced(w, "B", 0.40, 0.50)
    w.link(e, "quoted_by", w.market("cap", "E capped?", None, 0.10))  # a market caps E at 0.10 and shows no price
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.derive(e, [b], {**given(b), "value": 0.6})
    assert w.point(e) == pytest.approx(0.27) and w.interval(e) == pytest.approx((0.0, 0.10))  # nothing clamps it
    assert [(x["id"], x["p"], x["hi"]) for x in check(w.store)["outside_interval"]] == [(e, pytest.approx(0.27), 0.10)]


def test_a_derivation_whose_arithmetic_leaves_zero_to_one_gives_no_point(w):
    b, c = priced(w, "B", 0.65, 0.75), priced(w, "C", 0.55, 0.65)
    e = w.prop("B or C")
    w.link(e, "or", b)
    w.link(e, "or", c)
    w.link("portfolio", "watches", e)
    w.derive(e, [b, c], {"of": c, **given(b), "value": 0.0})  # 0.7 + 0.6 - 0 is not a chance
    assert w.point(e) is None and "gives 1.3, which is not a chance" in w.reason(e, "p")


def test_a_stated_chance_cites_its_page_with_the_passages_that_bear_or_check_lists_it(w):
    sid = w.store.put_page("https://example.org/odds", "Forecasters put the chance at about four percent.",
                           retrieved_at="2026-11-04T15:00:00+00:00")
    e = w.prop("E", chance={"value": 0.04})
    assert [(x["id"], x["field"]) for x in check(w.store)["assumptions_without_basis"]] == [(e, "chance")]
    with pytest.raises(StoreError, match="word for word"):
        w.store.edit(e, "chance", {"value": 0.04, "source_id": sid, "passages": ["about five percent"]})
    w.store.edit(e, "chance", {"value": 0.04, "source_id": sid, "passages": ["the chance at about four percent"]})
    assert w.store.get(sid)["kept"] is True and w.store.citations_of(e) == {"chance": sid}
    assert check(w.store)["assumptions_without_basis"] == []
    w.store.edit(e, "chance", 0.05)  # the page's last citation goes, and so does its keeping
    assert w.store.citations_of(e) == {} and w.store.source_row(sid)["kept"] is False
    assert check(w.store)["assumptions_without_basis"][0]["field"] == "chance"
    w.store.note("Our reading of the forecasts.", [e])
    assert check(w.store)["assumptions_without_basis"] == []


def test_a_derivations_chances_each_carry_their_basis(w):
    sid = w.store.put_page("https://example.org/path", "Given a raise, the rate reaches 25% in most scenarios.",
                           retrieved_at="2026-11-04T15:00:00+00:00")
    e, d = w.prop("E"), priced(w, "D", 0.28, 0.32)
    w.derive(e, [d], {**given(d), "value": 0.8, "source_id": sid, "passages": ["reaches 25% in most scenarios"]},
             {"given_not": [d], "value": 0.1})
    assert w.store.citations_of(e) == {"derivation.chances[0]": sid}
    missing = check(w.store)["assumptions_without_basis"]
    assert [(x["id"], x["field"]) for x in missing] == [(e, "derivation.chances[1]")]


def test_the_rows_arithmetic_on_its_own():
    p = {"A": 0.1, "B": 0.5, "C": 0.3, "D": 0.3}
    rows = [("narrower", {"B": "B"}, {"E given B": 0.6}, 0.3), ("broader", {"A": "A"}, {"E given not A": 0.2}, 0.28),
            ("between", {"A": "A", "B": "B"}, {"E given B and not A": 0.5}, 0.3),
            ("driver", {"D": "D"}, {"E given D": 0.9, "E given not D": 0.2}, 0.41),
            ("part", {"B": "B", "C": "C"}, {"C given B": 0.4}, 0.2),
            ("either", {"B": "B", "C": "C"}, {"C given B": 0.5}, 0.55),
            ("other", {"B": "B", "C": "C"}, {"C given not B": 0.2}, 0.6)]
    for row, roles, chances, expected in rows:
        found = {"row": row, "roles": roles, "chances": {k: {"value": v} for k, v in chances.items()}}
        assert logic.evaluate(found, p) == pytest.approx(expected), row


def test_a_derivation_reads_across_a_composite_by_what_it_implies(w):
    """A component of or implies its composite, and the composite does not imply it: E = B or C reads B by the rule
    for a proposition that implies E, and not by the one for a proposition E implies."""
    b, c = priced(w, "B", 0.18, 0.22), w.prop("C")
    e = w.prop("B or C")
    w.link(e, "or", b)
    w.link(e, "or", c)
    w.link("portfolio", "watches", e)
    w.derive(e, [b], {"given_not": [b], "value": 0.1})  # B implies E
    assert w.point(e) == pytest.approx(0.2 + 0.1 * 0.8)
    with pytest.raises(StoreError, match="no link says prop:3 implies prop:1"):
        w.derive(e, [b], {"given": [b], "value": 0.5})  # E does not imply B


def test_a_driver_is_refused_where_the_event_implies_it(w):
    e, d = w.prop("E"), priced(w, "D", 0.28, 0.32)
    w.link(e, "implies", d)
    with pytest.raises(StoreError, match="the links say prop:1 implies prop:2"):
        w.derive(e, [d], {"given": [d], "value": 0.8}, {"given_not": [d], "value": 0.1})
