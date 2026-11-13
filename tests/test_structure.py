"""Each structure's probabilities through the store (MATH.md 1.2, 1.10): an event and its ways at every depth, the
markets the analyst takes and the chances it states, read as one set of probabilities by harness.derive.structure.
The arithmetic is oneform's, proven in test_oneform; these prove what the store gives it and what it gives back.
Figures by hand.

Where the event and its ways are read from markets and no bound binds, the gap between the event's price and its
ways' summed prices, g, is shared in proportion to each book's squared half-spread: with r = g / (1 + sum h_W^2 / h_E^2),
the event reads p_E + r and each way p_W - r h_W^2 / h_E^2."""

from __future__ import annotations

import pytest

from breakdown import World, stated
from harness.ledger import check, event_now, row, watched as steps


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "scratch-structure")
    yield world
    world.store.close()


def asked(w, title: str, bid: float, ask: float, size: float = 1000.0) -> str:
    pid = w.prop(title)
    mid = w.market(title, f"{title}?", bid, ask)
    w.set_book(mid, bid, ask, size=size)
    w.link(pid, "asks", mid)
    return pid


def way(w, parent: str, pid: str, *, side: str | None = None, share: float | None = None) -> str:
    """A way of parent; with a share, the way's chance given its parent (given not the event on its No side)."""
    lid = w.link(parent, "way", pid, **({"side": side} if side else {}))
    if share is not None:
        case = {"given_not": [parent]} if side == "no" else {"given": [parent]}
        w.chances(pid, {**case, "value": share})
    return lid


def p(w, pid: str):
    return w.point(pid)


def _node(w, pid: str) -> dict:
    from harness.derive.structure import subject
    return w.store.engine.value("structure", subject(w.store, pid)).output["props"][pid]


def watched(w, title: str, bid: float | None = None, ask: float | None = None) -> str:
    e = asked(w, title, bid, ask) if bid is not None else w.prop(title)
    w.link("portfolio", "watches", e)
    return e


def test_an_event_and_its_ways_each_asked_by_a_market_read_nearest_their_prices_in_half_spreads(w):
    """E at 0.30 to 0.36 (0.33), its ways A at 0.16 to 0.24 (0.20) and B at 0.18 to 0.22 (0.20): the ways' prices come
    to 0.40, above E's ask. r = 0.07 / (1 + (0.04^2 + 0.02^2) / 0.03^2) = 0.0217241: E 0.3517241, A 0.2 - 0.0016/0.0009 r
    = 0.1613793, B 0.2 - 0.0004/0.0009 r = 0.1903448; each within its book, each read off its price with the others, and
    the No side 1 - E."""
    e = watched(w, "E", 0.30, 0.36)
    a, b = asked(w, "A", 0.16, 0.24), asked(w, "B", 0.18, 0.22)
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    r = 0.07 / (1 + (0.04 ** 2 + 0.02 ** 2) / 0.03 ** 2)
    assert (p(w, e), p(w, a), p(w, b)) == pytest.approx((0.33 + r, 0.2 - 0.0016 / 0.0009 * r,
                                                         0.2 - 0.0004 / 0.0009 * r))
    assert p(w, a) + p(w, b) == pytest.approx(p(w, e), abs=1e-9)
    nodes = w.store.engine.value("outcomes", e).output["nodes"]
    assert nodes["no"]["p"] == pytest.approx(1 - p(w, e)) and nodes["no"]["rule"] == "complement"
    assert {n: nodes[n]["rule"] for n in ("yes", a, b)} == {"yes": "read", a: "read", b: "read"}
    assert w.interval(e) == pytest.approx((0.34, 0.36))  # A's and B's bids lift E's floor through the ways
    assert not check(w.store)["stretched"] and not check(w.store)["cannot_hold"]


def test_a_book_holds_its_proposition_within_its_bid_and_ask(w):
    """As above with A at 0.17 to 0.25 (0.21): unbounded, A would read 0.21 - 0.0016/0.0009 x 0.08/3.2222 = 0.1659,
    below its bid. It stands at its bid, 0.17, and E and B share the rest: B = (2500 x 0.20 + 1111.1 x 0.16) / 3611.1 =
    0.1876923, E = 0.17 + B."""
    e = watched(w, "E", 0.30, 0.36)
    a, b = asked(w, "A", 0.17, 0.25), asked(w, "B", 0.18, 0.22)
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    wb, we = 1 / 0.02 ** 2, 1 / 0.03 ** 2
    b_ = (wb * 0.20 + we * (0.33 - 0.17)) / (wb + we)
    assert (p(w, a), p(w, b), p(w, e)) == pytest.approx((0.17, b_, 0.17 + b_))


def test_a_chance_stated_on_one_way_is_its_probability_and_its_siblings_markets_take_the_rest(w):
    """A at a chance stated on its own, 0.10; B at 0.18 to 0.22 and C at 0.04 to 0.08; E at 0.30 to 0.36. B and C come
    to 0.26 with A's 0.10, above E's 0.33 by 0.03: r = 0.03 / (1 + 0.0008 / 0.0009) = 0.0158824. E 0.3458824,
    B 0.1929412, C 0.0529412, A 0.10."""
    e = watched(w, "E", 0.30, 0.36)
    a = w.prop("A", chance=0.10)
    b, c = asked(w, "B", 0.18, 0.22), asked(w, "C", 0.04, 0.08)
    for x in (a, b, c):
        way(w, e, x, side="yes")
    r = 0.03 / (1 + 0.0008 / 0.0009)
    assert (p(w, a), p(w, b), p(w, c), p(w, e)) == pytest.approx(
        (0.10, 0.2 - 0.0004 / 0.0009 * r, 0.06 - 0.0004 / 0.0009 * r, 0.33 + r))
    assert p(w, a) == 0.10 and _node(w, a)["rule"] == "chance"


def test_an_event_is_read_from_its_ways_up_and_a_market_on_it_informs_its_ways(w):
    """E states nothing and no market asks it; its Yes side is A at 0.10 to 0.14 and B at 0.18 to 0.22, so E is 0.32,
    derived, and its total is A's and B's. A market on E at 0.33 to 0.35 (0.34) then informs all three: the gap 0.02 is
    shared by squared half-spreads, l = 0.02 / (0.01^2 + 0.02^2 + 0.02^2): E = 0.34 - 0.0001 l = 0.3377778,
    A = 0.12 + 0.0004 l = 0.1288889, B = 0.2088889, each within its book."""
    e = watched(w, "E")
    a, b = asked(w, "A", 0.10, 0.14), asked(w, "B", 0.18, 0.22)
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    assert p(w, e) == pytest.approx(0.32) and _node(w, e)["rule"] == "derived"
    assert w.interval(e) == pytest.approx((0.28, 0.36))
    w.link(e, "asks", w.market("Eq", "E?", 0.33, 0.35))
    lam = 0.02 / (0.01 ** 2 + 0.02 ** 2 + 0.02 ** 2)
    assert (p(w, e), p(w, a), p(w, b)) == pytest.approx((0.34 - 0.0001 * lam, 0.12 + 0.0004 * lam,
                                                         0.20 + 0.0004 * lam))
    assert w.interval(e) == pytest.approx((0.33, 0.35))


def test_a_way_with_ways_each_level_with_a_market(w):
    """E at 0.40 to 0.50; A at 0.25 to 0.35 with its ways A1 at 0.08 to 0.12 and A2 at 0.13 to 0.17; B with no source.
    B takes what E leaves, so E reads its own 0.45 and A is read with its ways: r = -0.05 / (1 + 0.0008 / 0.0025) =
    -0.0378788, A 0.2621212, A1 0.1060606, A2 0.1560606, and B 0.45 - A, derived."""
    e = watched(w, "E", 0.40, 0.50)
    a, b = asked(w, "A", 0.25, 0.35), w.prop("B")
    a1, a2 = asked(w, "A1", 0.08, 0.12), asked(w, "A2", 0.13, 0.17)
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    way(w, a, a1)
    way(w, a, a2)
    r = -0.05 / (1 + 0.0008 / 0.0025)
    expected = {e: 0.45, a: 0.30 + r, a1: 0.10 - 0.0004 / 0.0025 * r, a2: 0.15 - 0.0004 / 0.0025 * r}
    assert {x: p(w, x) for x in expected} == pytest.approx(expected)
    assert p(w, b) == pytest.approx(0.45 - expected[a])
    assert _node(w, b)["rule"] == "derived" and _node(w, e)["rule"] == "market"


def test_a_ways_share_is_its_chance_given_its_event(w):
    """A way lies within the event's Yes side, so the chance of A given E, 0.6, gives A = 0.6 x 0.33, and B the
    rest of E. A's terms say so: E at 0.33 with the share 0.6, and not E at 0.67 with the share 0, set by the way."""
    e = watched(w, "E", 0.30, 0.36)
    a, b = w.prop("A"), w.prop("B")
    way(w, e, a, side="yes", share=0.6)
    way(w, e, b, side="yes")
    assert (p(w, a), p(w, b)) == pytest.approx((0.198, 0.132))
    given_e, given_not_e = _node(w, a)["terms"]
    assert (given_e["p"], given_e["share"], given_e["chance"]) == (pytest.approx(0.33), pytest.approx(0.6),
                                                                   f"{a}.chances[0]")
    assert (given_not_e["p"], given_not_e["share"]) == (pytest.approx(0.67), pytest.approx(0.0))
    assert "chance" not in given_not_e
    assert w.store.get(w.store.links_of(e, "way")[0]["id"])["stated"] == {"side": "yes"}  # the link states its side


def test_a_ways_share_on_the_no_side_is_its_chance_given_not_the_event(w):
    """E at a chance of 0.25; N1 and N2 are its No side's ways, N1 at 0.8 of it: N1 = 0.8 x 0.75 = 0.60, N2 0.15."""
    e = w.prop("E", chance=0.25)
    w.link("portfolio", "watches", e)
    n1, n2 = w.prop("N1"), w.prop("N2")
    way(w, e, n1, side="no", share=0.8)
    way(w, e, n2, side="no")
    assert (p(w, n1), p(w, n2)) == pytest.approx((0.60, 0.15))
    worlds = w.store.engine.value("outcomes", e).output
    assert worlds["worlds"] == ["yes", n1, n2] and worlds["nodes"]["no"]["p"] == pytest.approx(0.75)


def test_chances_that_tie_an_event_to_a_proposition_tied_to_its_own_way_are_read_together(w):
    """A is a way of E at a chance of 0.2; Q depends on A, 0.9 given A and 0.1 given not; E depends on Q, 0.8 given Q
    and 0.2 given not. Read together: Q = 0.9 x 0.2 + 0.1 x 0.8 = 0.26, E = 0.8 x 0.26 + 0.2 x 0.74 = 0.356, and B,
    E's other way, 0.156. A circle of chances is one set of conditions, solved at once."""
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    a, b, q = w.prop("A", chance=0.2), w.prop("B"), w.prop("Q")
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    w.chances(q, {"given": [a], "value": 0.9}, {"given_not": [a], "value": 0.1})
    w.chances(e, {"given": [q], "value": 0.8}, {"given_not": [q], "value": 0.2})
    assert (p(w, q), p(w, e), p(w, b), p(w, a)) == pytest.approx((0.26, 0.356, 0.156, 0.2))


def test_chances_that_cannot_hold_are_listed_and_the_structure_waits(w):
    """As above with E given Q at 0.5: A within E puts P(E and Q) at least P(A and Q) = 0.9 x 0.2 = 0.18, and
    0.5 x 0.26 = 0.13 is less. The set that cannot hold is listed, every probability of the structure waits, and so do
    the event's figures, saying why."""
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    a, b, q = w.prop("A", chance=0.2), w.prop("B"), w.prop("Q")
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    w.chances(q, {"given": [a], "value": 0.9}, {"given_not": [a], "value": 0.1})
    w.chances(e, {"given": [q], "value": 0.5}, {"given_not": [q], "value": 0.2})
    assert all(p(w, x) is None for x in (e, a, b, q))
    [found] = check(w.store)["cannot_hold"]
    assert {f"{e}.chances[0]", f"{q}.chances[0]", f"{q}.chances[1]", f"{a}.chances[0]"} <= set(found["statements"])
    figures = {f["id"]: f for f in found["figures"]}  # each statement with its figures (MATH.md 1.10)
    assert [f["id"] for f in found["figures"]] == found["statements"]
    assert figures[f"{e}.chances[0]"] == {"id": f"{e}.chances[0]", "of": e, "given": [q], "given_not": [], "value": 0.5}
    assert figures[f"{a}.chances[0]"]["value"] == 0.2 and figures[f"{q}.chances[1]"]["given_not"] == [a]
    for lid in (s for s in found["statements"] if s.startswith("link:")):
        assert figures[lid]["relation"] == "way" and figures[lid]["from"] == e and figures[lid]["side"] == "yes"
    why = w.store.engine.value("outcomes", e).output["why"]
    assert why.startswith(f"The statements of {e}'s structure cannot hold together")
    assert event_now(w.store, e)["figures"] is None


def test_chances_the_books_cannot_hold_with_stretch_the_book(w):
    """E at a chance of 0.30 and A at 0.9 of it put B, E's other way, at 0.03, below its book's 0.10. The chances
    stand, and B's book gives way: it is stretched 0.07 below its bid, and check() lists it."""
    e = w.prop("E", chance=0.30)
    w.link("portfolio", "watches", e)
    a, b = w.prop("A"), asked(w, "B", 0.10, 0.14)
    way(w, e, a, side="yes", share=0.9)
    way(w, e, b, side="yes")
    assert (p(w, e), p(w, a), p(w, b)) == pytest.approx((0.30, 0.27, 0.03))
    [s] = check(w.store)["stretched"]
    lid = w.store.links_of(b, "asks")[0]["id"]
    assert s["link"] == lid and s["below"] == pytest.approx(0.07) and s["above"] == 0.0
    assert row(w.store, e)["probability"] == pytest.approx(0.30)


def test_books_that_cannot_hold_through_the_ways_give_way_deepest_last(w):
    """E at 0.30 to 0.36, A at 0.22 to 0.26 and B at 0.20 to 0.24: the ways' bids come to 0.42, above E's ask. Each
    book holds 1,000 contracts a side, so its depth is the smaller side's cost, min(ask, 1 - bid) x 1,000: E $360,
    A $260, B $240. E holds, then A, and B, the shallowest, gives way: E = A + B at most 0.36 with A at least 0.22 puts
    B at most 0.14, a stretch of 0.06 below its bid; and the only set then left is E 0.36, A 0.22, B 0.14."""
    e = watched(w, "E", 0.30, 0.36)
    a, b = asked(w, "A", 0.22, 0.26), asked(w, "B", 0.20, 0.24)
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    assert (p(w, e), p(w, a), p(w, b)) == pytest.approx((0.36, 0.22, 0.14))
    found = {s["on"]: s for s in check(w.store)["stretched"] if not s.get("links_alone")}
    assert list(found) == [b] and found[b]["below"] == pytest.approx(0.06) and found[b]["depth"] == pytest.approx(240.0)


def test_a_proposition_alone_reads_its_deepest_markets_price_within_every_market_it_takes(w):
    """A deep book at 0.36 to 0.44 (0.40) and a shallow one at 0.30 to 0.38: the markets imply 0.36 to 0.38, and the
    proposition reads the point of that range nearest the deep book's price, 0.38."""
    e = asked(w, "E", 0.36, 0.44, size=50_000.0)
    w.link(e, "asks", w.market("shallow", "E, shallow?", 0.30, 0.38))
    w.link("portfolio", "watches", e)
    assert p(w, e) == pytest.approx(0.38) and w.interval(e) == pytest.approx((0.36, 0.38))


def test_a_ways_two_chances_hold_together_until_one_is_restated_against_the_other(w):
    """A at a chance of 0.15 stated on its own and 0.5 given E, with E at 0.30: both hold. Restated at 0.20 on its own,
    against half of 0.30, they cannot."""
    e = w.prop("E", chance=0.30)
    w.link("portfolio", "watches", e)
    a, b = w.prop("A", chance=0.15), w.prop("B")
    way(w, e, a, side="yes", share=0.5)
    way(w, e, b, side="yes")
    assert p(w, a) == pytest.approx(0.15) and _node(w, a)["rule"] == "chance"
    w.chances(a, {"value": 0.20})
    assert p(w, a) is None and check(w.store)["cannot_hold"]


def test_the_row_and_the_step_carry_the_markets_probability_beside_ours(w):
    """E's market, not taken, at 0.40 to 0.44, and E at a chance of 0.30: the row and the step carry ours, 0.30, with
    the chance as its rule, and the market's 0.42 beside it."""
    e = w.prop("E", chance=0.30)
    w.link("portfolio", "watches", e)
    m = w.market("M", "E?", 0.40, 0.44)
    lid = w.link(e, "asks", m, taken=False)
    r = row(w.store, e)
    assert r["probability"] == pytest.approx(0.30) and r["point"]["rule"] == "chance"
    assert r["market"] == {"market": m, "link": lid, "probability": pytest.approx(0.42), "taken": False}
    step = steps(w.store)[e]
    assert step["market"]["probability"] == pytest.approx(0.42) and step["rule"] == "chance"


def test_a_proposition_nothing_bears_on_is_left_open_and_names_nothing_that_would_settle_it(w):
    """E with a market it does not take: nothing bears on it, so it is left open, 0 to 1, and nothing is named as what
    would settle it, since its own chance stated on its own never is; its event's figures wait, saying
    so."""
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    w.link(e, "asks", w.market("M", "E?", 0.40, 0.44), taken=False)
    o = w.left_open(e)
    assert (o["lo"], o["hi"]) == (0.0, 1.0) and o["settled_by"] == [] and "with" not in o
    why = w.store.engine.value("outcomes", e).output["why"]
    assert why == f"{e}'s probability is left open, from 0 to 1."


def test_a_driver_nothing_bears_on_names_nothing_and_what_rests_on_it_points_to_it(w):
    """E has chances given D and given not D, and nothing is said of D: E is open and points to D, which it rests on;
    D names nothing that would settle it, as nothing bears on it."""
    e, d = w.prop("E"), w.prop("D")
    w.chances(e, {"given": [d], "value": 0.6}, {"given_not": [d], "value": 0.2})
    oe, od = w.left_open(e), w.left_open(d)
    assert (oe["lo"], oe["hi"]) == pytest.approx((0.2, 0.6)) and oe["settled_by"] == [] and oe["with"] == [d]
    assert (od["lo"], od["hi"]) == (0.0, 1.0) and od["settled_by"] == [] and "with" not in od


def test_two_propositions_nothing_reaches_rest_on_neither_and_name_nothing(w):
    """A implies B, with no market and no chance on either: each is left open, A within 0 to 1 and B too, and neither
    names a closer or points to the other, since they rest only on each other."""
    a, b = w.prop("A"), w.prop("B")
    w.link(a, "implies", b)
    oa, ob = w.left_open(a), w.left_open(b)
    assert oa["settled_by"] == [] and ob["settled_by"] == [] and "with" not in oa and "with" not in ob


def test_a_way_whose_share_is_left_open_names_the_chance_that_would_close_it(w):
    """E at 0.30 with two ways and nothing said of how it divides: each way is open, 0 to 0.30, settled by its chance
    given E, and the worlds wait."""
    e = w.prop("E", chance=0.30)
    w.link("portfolio", "watches", e)
    a, b = w.prop("A"), w.prop("B")
    way(w, e, a, side="yes")
    way(w, e, b, side="yes")
    o = w.left_open(a)
    assert (o["lo"], o["hi"]) == pytest.approx((0.0, 0.30))
    assert [{"of": a, "given": [e], "given_not": []}] in o["settled_by"]
    assert "left open" in w.store.engine.value("outcomes", e).output["why"]


def test_a_chance_given_a_proposition_brings_it_into_the_structure_and_removing_that_one_is_refused(w):
    """A chance of E given D ties D into E's structure, and D cannot be removed while a chance is given it."""
    from harness.store import StoreError
    e, d = w.prop("E"), asked(w, "D", 0.29, 0.31)
    w.chances(e, {"given": [d], "value": 0.6}, {"given_not": [d], "value": 0.2})
    assert p(w, e) == pytest.approx(0.30 * 0.6 + 0.70 * 0.2)
    with pytest.raises(StoreError, match=rf"named by {e}\.chances\[0\], {e}\.chances\[1\]"):
        w.store.remove(d)


def test_restating_a_chance_keeps_its_note_by_id_and_drops_the_note_of_one_restated_on_another_basis(w):
    """A chance's note is kept by its id when the list is stated again, and goes with a chance no longer stated."""
    e, d = w.prop("E"), w.prop("D", chance=0.5)
    w.chances(e, {"given": [d], "value": 0.6}, {"given_not": [d], "value": 0.2})
    first = w.store.stated_of(e)["chances"]
    kept = first[0]["note"]
    w.store.edit(e, "chances", [first[0], stated({"given": [], "given_not": [d], "value": 0.3}, "another reason")])
    after = w.store.stated_of(e)["chances"]
    assert after[0]["note"] == kept and after[1]["note"] != first[1]["note"]
    assert w.store.type_of(first[1]["note"]) is None and w.store.type_of(kept) == "Note"


def test_an_open_probability_says_what_would_settle_it_in_one_sentence():
    """MATH.md 1.2's word, settle: the range the statements leave, each chance given other propositions that would settle
    it, and what it rests on that is open; and with nothing to name, the range alone."""
    from harness.derive.structure import left_open_text
    o = {"lo": 0.125, "hi": 0.305, "settled_by": [[{"of": "prop:1", "given": ["prop:3"], "given_not": []}]],
         "with": ["prop:2"]}
    assert left_open_text("prop:1", o) == ("prop:1's probability is left open, from 0.125 to 0.305. The chance of "
                                           "prop:1 given prop:3 would settle it, or what settles prop:2, which it "
                                           "rests on.")
    assert left_open_text("prop:2", {"lo": 0.0, "hi": 1.0, "settled_by": []}) == \
        "prop:2's probability is left open, from 0 to 1."


def test_an_event_s_reasons_for_having_no_figures_read_as_sentences(w):
    """A watched event nothing bears on: its probability left open, and no shock stated, each said as a sentence."""
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    why = event_now(w.store, e)["why"]
    assert why.startswith(f"{e}'s probability is left open, from 0 to 1. ") and ".;" not in why and why.endswith(".")
