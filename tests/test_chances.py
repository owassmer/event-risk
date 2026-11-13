"""A proposition's probability through the store (MATH.md 1.2): the price of the deepest market it takes, read with every
other book of its structure; the chances the analyst states, each given other propositions or stated on its own, read
with the links as one set of probabilities; and a chance that follows a market. The nine ways one probability is read
from others, each stated as chances, on hand-built structures against hand-computed figures. Each market's book is one
level a side, so its price is the midpoint the test sets, and its depth the smaller side's cost, min(ask, 1 - bid) x
size."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from breakdown import World, stated
from harness import store as store_module
from harness.derive.structure import subject
from harness.ledger import check, row
from harness.store import StoreError


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "scratch-chances")
    yield world
    world.store.close()


def priced(w, title, bid, ask, vid=None, size=1000.0):
    """A proposition a market asks, its price the midpoint of bid and ask."""
    pid = w.prop(title)
    mid = w.market(vid or title, f"{title}?", bid, ask)
    w.set_book(mid, bid, ask, size=size)
    w.link(pid, "asks", mid)
    return pid


def node(w, pid: str) -> dict:
    return w.store.engine.value("structure", subject(w.store, pid)).output["props"][pid]


def stretched(w) -> dict:
    return {s["link"]: s for s in check(w.store)["stretched"] if not s.get("links_alone")}


# ---------------------------------------------------------------- markets


def test_a_market_that_asks_the_proposition_gives_its_price(w):
    e = priced(w, "E", 0.30, 0.34)
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.32)
    out = node(w, e)
    assert out["rule"] == "market" and out["target"]["link"] == w.store.links_of(e, "asks")[0]["id"]


def test_of_several_markets_that_ask_it_the_deepest_gives_the_price_and_the_others_bound_it(w):
    """A deep book at 0.40 to 0.44 and a shallow one at 0.38 to 0.46: E reads the deep book's price, 0.42."""
    e = priced(w, "E", 0.38, 0.46)
    w.link(e, "asks", w.market("deep", "E, deeper?", 0.40, 0.44))
    w.set_book("market:polymarket:deep", 0.40, 0.44, size=50000.0)
    w.store.engine.reset()
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.42) and w.interval(e) == pytest.approx((0.40, 0.44))


def test_of_two_books_that_cross_the_shallower_gives_way(w):
    """A deep book at 0.40 to 0.44 ($22,000) and a shallow one at 0.30 to 0.34 ($340): no probability lies within
    both. The deep book holds, the shallow one is stretched 0.06 above its ask, and E reads 0.40, the deep book's bid,
    where the two now meet."""
    e = priced(w, "E", 0.30, 0.34)
    w.link(e, "asks", w.market("deep", "E, deeper?", 0.40, 0.44))
    w.set_book("market:polymarket:deep", 0.40, 0.44, size=50000.0)
    w.store.engine.reset()
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.40)
    shallow = w.store.links_of(e, "asks")[0]["id"]
    assert list(stretched(w)) == [shallow] and stretched(w)[shallow]["above"] == pytest.approx(0.06)
    assert w.interval(e) == pytest.approx((0.40, 0.40))  # the range the markets imply, read within the stretched books


def test_a_directly_priced_event_keeps_its_price_when_neighbouring_markets_agree_with_it(w):
    """E at 0.32, implied by A at 0.11 and implying B at 0.55: every price holds with the links, so each proposition
    reads its own, and E's range is its book's."""
    e = priced(w, "E", 0.30, 0.34)
    w.link("portfolio", "watches", e)
    before = w.point(e)
    b = priced(w, "B", 0.50, 0.60)
    w.link(e, "implies", b)
    a = priced(w, "A", 0.10, 0.12)
    w.link(a, "implies", e)
    assert w.point(e) == before == pytest.approx(0.32)
    assert w.interval(e) == pytest.approx((0.30, 0.34))
    assert (w.point(a), w.point(b)) == pytest.approx((0.11, 0.55))


def test_both_markets_inform_both_probabilities_through_a_chance(w):
    """E at 0.30 to 0.34 implies B at 0.40 to 0.50, and E given B is 0.6, so E = 0.6 B, at most 0.30 within B's book:
    the only set within both books is B at its ask, 0.50, and E at its bid, 0.30."""
    e, b = priced(w, "E", 0.30, 0.34), priced(w, "B", 0.40, 0.50)
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.chances(e, {"given": [b], "value": 0.6})
    assert (w.point(e), w.point(b)) == pytest.approx((0.30, 0.50))
    assert not stretched(w)


def test_a_chance_stated_on_its_own_stands_over_a_book_that_gives_way(w):
    """As above with a chance of 0.40 stated on its own in place of the chance given B: E is 0.40, its book stretched
    0.06 above its ask, and B, which E implies, reads its price, 0.45. With no chance, E is its book's 0.32 again."""
    e, b = priced(w, "E", 0.30, 0.34), priced(w, "B", 0.40, 0.50)
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.chances(e, {"value": 0.40})
    assert (w.point(e), w.point(b)) == pytest.approx((0.40, 0.45)) and node(w, e)["rule"] == "chance"
    lid = w.store.links_of(e, "asks")[0]["id"]
    assert stretched(w)[lid]["above"] == pytest.approx(0.06)
    w.store.edit(e, "chances", None)
    assert w.point(e) == pytest.approx(0.32) and node(w, e)["rule"] == "market" and not stretched(w)


def test_a_market_whose_price_is_not_taken_is_shown_and_neither_bounds_nor_gives_the_probability(w):
    """Two markets ask E: a deep one at 0.20 to 0.24 and a shallow one at 0.30 to 0.34. Both taken, they cross, the
    shallow one gives way, and E is 0.24. Not taken, the deep one gives nothing: E is the shallow one's 0.32 within its
    0.30 to 0.34. With neither taken, E is left open, its range is the unit interval, and the row still shows both
    books. A market not taken is stated so with its basis."""
    e = priced(w, "E", 0.30, 0.34)
    deep = w.market("deep", "E, deeper?", 0.20, 0.24)
    w.set_book(deep, 0.20, 0.24, size=50000.0)
    lid = w.link(e, "asks", deep)
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.24)
    with pytest.raises(StoreError, match="taken is false with its basis"):
        w.store.edit(lid, "taken", False)
    w.store.edit(lid, "taken", {"value": False, "note": "This book's quotes have stood unchanged for weeks."})
    assert w.point(e) == pytest.approx(0.32) and w.interval(e) == pytest.approx((0.30, 0.34))
    got = w.store.get(lid)["derived"]
    assert (got["bid"]["value"], got["ask"]["value"]) == pytest.approx((0.20, 0.24)) and "bound" not in got
    reason = w.store.stated_of(lid)["taken"]["note"]
    assert [n["from"] for n in w.store.links_of(lid, "about", "in")] == [reason]
    shallow = next(x["id"] for x in w.store.links_of(e, "asks") if x["id"] != lid)
    w.store.edit(shallow, "taken", stated(False))
    assert w.point(e) is None and w.interval(e) == (0.0, 1.0) and w.left_open(e)["hi"] == 1.0
    r = row(w.store, e)
    assert [(m["link"], m["taken"], m["price"]) for m in r["markets"]] == [
        (shallow, False, pytest.approx(0.32)), (lid, False, pytest.approx(0.22))]


# ---------------------------------------------------------------- the nine ways, as chances


def test_narrower_e_implies_b_and_its_chance_given_b(w):
    """E implies B, B at 0.45, and the chance of E given B is 0.6: E = 0.6 x 0.45, and its terms open to the
    arithmetic: B at 0.45 with the share 0.6, the chance; not B at 0.55 with the share 0, the link."""
    e, b = w.prop("E"), priced(w, "B", 0.40, 0.50)
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.chances(e, {"given": [b], "value": 0.6})
    assert w.point(e) == pytest.approx(0.27) and node(w, e)["rule"] == "derived"
    by_b, not_b = node(w, e)["terms"]
    assert (by_b["p"], by_b["share"], by_b["chance"]) == (pytest.approx(0.45), pytest.approx(0.6), f"{e}.chances[0]")
    assert (not_b["p"], not_b["share"]) == (pytest.approx(0.55), 0.0) and "chance" not in not_b
    assert (by_b["joint"], not_b["joint"]) == (pytest.approx(0.27), pytest.approx(0.0, abs=1e-9))  # what each gives E


def test_broader_a_implies_e_and_its_chance_given_not_a(w):
    e, a = w.prop("E"), priced(w, "A", 0.18, 0.22)
    w.link(a, "implies", e)
    w.link("portfolio", "watches", e)
    w.chances(e, {"given_not": [a], "value": 0.1})
    assert w.point(e) == pytest.approx(0.2 + 0.1 * 0.8)


def test_between_reads_two_markets_and_moving_either_moves_the_probability(w):
    e, a, b = w.prop("E"), priced(w, "A", 0.08, 0.12), priced(w, "B", 0.45, 0.55)
    w.link(a, "implies", e)
    w.link(e, "implies", b)
    w.link("portfolio", "watches", e)
    w.chances(e, {"given": [b], "given_not": [a], "value": 0.25})
    assert w.point(e) == pytest.approx(0.1 + 0.25 * (0.5 - 0.1))
    w.set_book("market:polymarket:A", 0.18, 0.22)
    w.reread("market:polymarket:A")
    assert w.point(e) == pytest.approx(0.2 + 0.25 * (0.5 - 0.2))  # A moved
    w.set_book("market:polymarket:B", 0.65, 0.75)
    w.reread("market:polymarket:B")
    assert w.point(e) == pytest.approx(0.2 + 0.25 * (0.7 - 0.2))  # B moved
    assert w.interval(e) == pytest.approx((0.18, 0.75))


def test_a_driver_reaches_e_through_both_of_its_outcomes(w):
    """D at 0.30, E given D 0.8 and given not D 0.1: E = 0.3 x 0.8 + 0.7 x 0.1. D moving to 0.60 moves E. No link
    relates them, so D bounds nothing. A link saying E implies D cannot hold with E given not D at 0.1: it is listed,
    with the chance, and the probabilities wait."""
    e, d = w.prop("E"), priced(w, "D", 0.28, 0.32)
    w.link("portfolio", "watches", e)
    w.chances(e, {"given": [d], "value": 0.8}, {"given_not": [d], "value": 0.1})
    assert w.point(e) == pytest.approx(0.3 * 0.8 + 0.7 * 0.1)
    w.set_book("market:polymarket:D", 0.58, 0.62)
    w.reread("market:polymarket:D")
    assert w.point(e) == pytest.approx(0.6 * 0.8 + 0.4 * 0.1)
    assert w.interval(e) == (0.0, 1.0)
    lid = w.link(e, "implies", d)
    assert w.point(e) is None
    [found] = check(w.store)["cannot_hold"]
    assert set(found["statements"]) == {f"{e}.chances[1]", lid}


def test_part_e_is_b_and_c_or_b_then_c(w):
    """E = B and C, with B at 0.45 and C given B at 0.5: E = 0.225, and C itself is left open, 0.225 to 0.775, settled
    by its chance given not B. E = B then C, with the chance of E given B at 0.3: E = 0.135."""
    b, c = priced(w, "B", 0.40, 0.50), w.prop("C")
    both = w.prop("B and C")
    w.link(both, "and", b)
    w.link(both, "and", c)
    w.link("portfolio", "watches", both)
    w.chances(c, {"given": [b], "value": 0.5})
    assert w.point(both) == pytest.approx(0.45 * 0.5)
    o = w.left_open(c)
    assert (o["lo"], o["hi"]) == pytest.approx((0.225, 0.775)) and [{"of": c, "given": [], "given_not": [b]}] in \
        o["settled_by"]
    seq = w.prop("B then C2")
    c2 = w.prop("C2")
    w.link(seq, "then", b, step=1)
    w.link(seq, "then", c2, step=2)
    w.link("portfolio", "watches", seq)
    w.chances(seq, {"given": [b], "value": 0.3})
    assert w.point(seq) == pytest.approx(0.45 * 0.3)


def test_either_e_is_b_or_c_with_c_given_b(w):
    """E = B or C, B at 0.45 and C at 0.30, C given B 0.2: E = 0.45 + 0.30 - 0.45 x 0.2 = 0.66; with C given B at 0,
    they cannot both happen, and E is 0.75."""
    b, c = priced(w, "B", 0.40, 0.50), priced(w, "C", 0.28, 0.32)
    e = w.prop("B or C")
    w.link(e, "or", b)
    w.link(e, "or", c)
    w.link("portfolio", "watches", e)
    w.chances(c, {"given": [b], "value": 0.2})
    assert w.point(e) == pytest.approx(0.45 + 0.3 - 0.45 * 0.2)
    w.chances(c, {"given": [b], "value": 0.0})
    assert w.point(e) == pytest.approx(0.75)


def test_other_e_is_b_or_c_with_c_given_not_b(w):
    b, c = priced(w, "B", 0.40, 0.50), w.prop("C")
    e = w.prop("B or C")
    w.link(e, "or", b)
    w.link(e, "or", c)
    w.link("portfolio", "watches", e)
    w.chances(c, {"given_not": [b], "value": 0.1})
    assert w.point(e) == pytest.approx(0.45 + 0.55 * 0.1)


def test_a_chain_of_chances_reads_each_proposition_with_the_others(w):
    """A at 0.20 implies B, and B given not A is 0.5, so B = 0.6; E implies B and E given B is 0.5, so E = 0.3. Every
    proposition of the structure has its probability, and one alone has its market's."""
    a = priced(w, "A", 0.18, 0.22)
    b, e = w.prop("B"), w.prop("E")
    w.link(a, "implies", b)
    w.link(e, "implies", b)
    w.chances(b, {"given_not": [a], "value": 0.5})
    w.link("portfolio", "watches", e)
    w.chances(e, {"given": [b], "value": 0.5})
    assert (w.point(e), w.point(b), w.point(a)) == pytest.approx((0.3, 0.6, 0.2))
    c = priced(w, "C", 0.50, 0.60)
    assert w.point(c) == pytest.approx(0.55)


# ---------------------------------------------------------------- a chance stated on its own, and none


def test_a_proposition_nothing_else_bears_on_takes_its_chance_stated_on_its_own(w):
    e = w.prop("E", chance=0.04)
    w.link("portfolio", "watches", e)
    assert w.point(e) == 0.04 and w.interval(e) == (0.0, 1.0)
    with pytest.raises(StoreError, match="a chance, from 0 to 1"):
        w.chances(e, {"value": 1.2})
    with pytest.raises(StoreError, match=r"chances\[0\] and chances\[1\] are both stated on their own"):
        w.store.edit(e, "chances", [stated(0.04), stated(0.05)])
    d = w.prop("D")
    with pytest.raises(StoreError, match=rf"given {d} both true and false"):
        w.chances(e, {"given": [d], "given_not": [d], "value": 0.5})
    with pytest.raises(StoreError, match=rf"{e}.chances\[1\] is given {e} itself"):
        w.chances(e, {"given": [e], "value": 0.5})
    with pytest.raises(StoreError, match=r"is given prop:99, which is not a proposition in the store"):
        w.chances(e, {"given": ["prop:99"], "value": 0.5})


def test_a_watched_proposition_nothing_fixes_is_left_open_and_its_row_shows_it(w):
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    assert "probability" not in w.store.get(e)["derived"]
    r = row(w.store, e)
    assert r["probability"] is None and r["left_open"] == {"lo": 0.0, "hi": 1.0, "settled_by": []}


def test_a_chance_given_a_proposition_it_is_not_linked_to_leaves_it_open_with_the_chance_that_would_close_it(w):
    """T given R at 0.62 with R at 0.305 and no link between them: T is open, 0.62 x 0.305 = 0.1891 to that plus
    0.695, and its chance given not R would settle it."""
    t, r = w.prop("T"), priced(w, "R", 0.28, 0.33)
    w.link("portfolio", "watches", t)
    w.chances(t, {"given": [r], "value": 0.62})
    o = w.left_open(t)
    assert (o["lo"], o["hi"]) == pytest.approx((0.1891, 0.8841)) and \
        o["settled_by"] == [[{"of": t, "given": [], "given_not": [r]}]]


def test_a_chance_cites_its_page_with_the_passages_that_bear(w):
    sid = w.store.put_page("https://example.org/odds", "Forecasters put the chance at about four percent.",
                           retrieved_at="2026-11-04T15:00:00+00:00")
    e = w.prop("E")
    with pytest.raises(StoreError, match=r"chances\[0\] is stated with its basis: your reasoning"):
        w.store.edit(e, "chances", [{"value": 0.04}])
    with pytest.raises(StoreError, match="word for word"):
        w.store.edit(e, "chances", [{"value": 0.04, "source_id": sid, "passages": ["about five percent"]}])
    assert w.store.stated_of(e).get("chances") is None and w.store.query("Note") == []
    w.store.edit(e, "chances", [{"value": 0.04, "source_id": sid, "passages": ["the chance at about four percent"]}])
    assert w.store.get(sid)["kept"] is True and w.store.citations_of(e) == {"chances[0]": sid}
    w.store.edit(e, "chances", [stated(0.05)])  # the page's last citation goes, and so does its keeping
    assert w.store.citations_of(e) == {} and w.store.source_row(sid)["kept"] is False


def test_each_chance_carries_its_own_basis(w):
    sid = w.store.put_page("https://example.org/path", "Given a raise, the rate reaches 25% in most scenarios.",
                           retrieved_at="2026-11-04T15:00:00+00:00")
    e, d = w.prop("E"), priced(w, "D", 0.28, 0.32)
    paged = {"given": [d], "value": 0.8, "source_id": sid, "passages": ["reaches 25% in most scenarios"]}
    with pytest.raises(StoreError, match=r"chances\[1\] is stated with its basis"):
        w.store.edit(e, "chances", [paged, {"given_not": [d], "value": 0.1}])
    w.store.edit(e, "chances", [paged, {"given_not": [d], "value": 0.1,
                                        "note": "Without a raise the rate stays near where it is."}])
    chances = w.store.stated_of(e)["chances"]
    assert w.store.citations_of(e) == {"chances[0]": sid} and "note" not in chances[0]
    assert w.store.stated_of(chances[1]["note"]) == {"text": "Without a raise the rate stays near where it is."}
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.8 * 0.3 + 0.1 * 0.7)


# ---------------------------------------------------------------- a chance that follows a market (MATH.md 1.2)


def later(minutes: float) -> str:
    """A moment after now: a book read after the chance was stated."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def read_at(w, mid: str, bid, ask, at: str) -> None:
    """The market's book read at `at`."""
    w.set_book(mid, bid, ask)
    w.store.put_data("book", mid, w.books[mid], {"endpoint": "/books"}, retrieved_at=at)


def following(w, e) -> dict:
    return next(c for c in node(w, e)["chances"] if c.get("follow"))["follow"]


def test_a_chance_that_follows_a_market_moves_with_it_from_its_price_when_stated(w, monkeypatch):
    """MATH.md 1.2's illustration: the market at 0.40 when the analyst states 0.30, following half of its moves. At 0.50
    ours is 0.344; at 0.25, 0.233; at 0.10, 0.149. Restated with the market at 0.10, the chance rests on that reading:
    a responsiveness of 0 keeps 0.30, and of 1 with the market back at 0.50 keeps the odds ratio, 3/7 x 9 = 27/7 odds,
    27/34. The market it follows is not taken, whatever its link says: it informs E through the chance."""
    e = w.prop("E")
    m = w.market("M", "E?", 0.39, 0.41)
    w.link(e, "asks", m)
    w.link("portfolio", "watches", e)
    w.chances(e, {"value": 0.30, "follow": m, "responsiveness": 0.5})
    assert w.point(e) == pytest.approx(0.30) and node(w, e)["markets"][0]["followed"] is True
    for minutes, (bid, ask), expected in ((1, (0.49, 0.51), 0.344), (2, (0.24, 0.26), 0.233),
                                          (3, (0.09, 0.11), 0.149)):
        read_at(w, m, bid, ask, later(minutes))
        assert round(w.point(e), 3) == expected
    f = following(w, e)
    assert f["q_then"] == pytest.approx(0.40) and f["q_now"] == pytest.approx(0.10)
    assert f["stated_at"] == w.store.stated_at(e, "chances:follow")
    restated = later(3.5)  # after the reading at 0.10, which the restated chance rests on
    monkeypatch.setattr(store_module, "_moment", lambda: restated)
    w.chances(e, {"value": 0.30, "follow": m, "responsiveness": 0.0})
    assert w.point(e) == pytest.approx(0.30)
    w.chances(e, {"value": 0.30, "follow": m, "responsiveness": 1.0})
    assert following(w, e)["q_then"] == pytest.approx(0.10)
    read_at(w, m, 0.49, 0.51, later(4))  # from 0.10 to 0.50: odds 3/7 x 9 = 27/7
    assert w.point(e) == pytest.approx(27 / 34)


def test_stating_another_chance_leaves_a_following_chance_where_it_was_stated(w, monkeypatch):
    """E follows M from 0.40, stated at 0.30 with a responsiveness of 1; M goes to 0.50. Stating the chance of D given
    E later leaves E's chance resting on M's price when it was stated, so E stays at 3/7 x 1.5 odds, 9/23."""
    e, d = w.prop("E"), w.prop("D")
    m = w.market("M", "E?", 0.39, 0.41)
    w.link(e, "asks", m)
    w.chances(e, {"value": 0.30, "follow": m, "responsiveness": 1.0})
    stamp = w.store.stated_at(e, "chances:follow")
    read_at(w, m, 0.49, 0.51, later(1))
    assert w.point(e) == pytest.approx(9 / 23)
    monkeypatch.setattr(store_module, "_moment", lambda: later(2))
    w.chances(e, {"given": [d], "value": 0.0})  # a second chance of E, given D
    assert w.store.stated_at(e, "chances:follow") == stamp and following(w, e)["q_then"] == pytest.approx(0.40)


def test_a_chance_rests_on_its_markets_last_priced_reading_while_its_book_lacks_a_side(w):
    """The chance is stated at 0.30 with the market at 0.40, following all of its moves. The market goes to 0.50
    (ours 0.391), then its asks go: it has no price, and the chance stays at the reading it rests on, whose time it
    shows. When the asks return at 0.20 to 0.22, it moves again: odds 3/7 x (0.21/0.79) / (0.4/0.6) = 0.1709, so
    0.146."""
    e = w.prop("E")
    m = w.market("M", "E?", 0.39, 0.41)
    w.link(e, "asks", m)
    w.link("portfolio", "watches", e)
    w.chances(e, {"value": 0.30, "follow": m, "responsiveness": 1.0})
    priced_at = later(1)
    read_at(w, m, 0.49, 0.51, priced_at)
    assert w.point(e) == pytest.approx(0.391, abs=5e-4)
    read_at(w, m, 0.48, None, later(2))
    assert w.derived(m, "price") is None
    assert w.point(e) == pytest.approx(0.391, abs=5e-4) and following(w, e)["now_at"] == priced_at
    read_at(w, m, 0.20, 0.22, later(3))
    odds = 0.3 / 0.7 * (0.21 / 0.79) / (0.4 / 0.6)
    assert w.point(e) == pytest.approx(odds / (1 + odds))


def test_a_chance_follows_only_a_market_the_proposition_asks_and_that_market_stays_while_it_does(w):
    e, m = w.prop("E"), w.market("M", "E?", 0.39, 0.41)
    with pytest.raises(StoreError, match="is not linked to it"):
        w.chances(e, {"value": 0.3, "follow": m, "responsiveness": 0.5})
    with pytest.raises(StoreError, match="responsiveness is 0 or more"):
        w.chances(e, {"value": 0.3, "follow": m, "responsiveness": -1})
    with pytest.raises(StoreError, match="a chance that follows a market is stated on its own"):
        w.chances(e, {"given": [w.prop("D")], "value": 0.3, "follow": m, "responsiveness": 0.5})
    lid = w.link(e, "asks", m)
    w.chances(e, {"value": 0.3, "follow": m, "responsiveness": 0.5})
    with pytest.raises(StoreError, match="follows market:polymarket:M") as refused:
        w.store.remove(lid)
    assert refused.value.dependents == [f"{e}.chances[0]"]


def test_the_store_keeps_when_each_stated_field_was_written(w):
    e = w.prop("E", chance=0.2)
    first = w.store.stated_at(e, "chances")
    assert first and w.store.stated_at(e, "title") and w.store.stated_at(e, "chances:follow") is None
    w.store.edit(e, "chances", [stated(0.2)])  # restated: written again, at its own moment
    assert w.store.stated_at(e, "chances") > first
    w.store.edit(e, "chances", None)
    assert w.store.stated_at(e, "chances") is None
