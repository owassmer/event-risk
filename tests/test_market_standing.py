"""A market stands as the proposition its question and rules define, wherever the analyst relates one proposition to
another (MATH.md 1.2): in what a chance is given, at either end of an implication, and as a rung of a ladder at the date
its question counts through. Its Yes is that proposition true; one no proposition asks is taken at its own book, and
one a proposition asks stands as that proposition, through the side the link names."""

from __future__ import annotations

from datetime import datetime

import pytest

from breakdown import RETRIEVED, WHY, World, stated
from harness.ledger import ledger_read
from harness.store import StoreError
from harness.tools import Tools
from harness.venues.http import VenueError


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "standing")
    yield world
    world.store.close()


def p(w, pid):
    return w.store.get(pid)["derived"].get("probability", {}).get("value")


def chances(w, pid, *items):
    return w.store.edit(pid, "chances", [stated(c) for c in items])


def test_a_chance_given_a_market_reads_its_price_as_that_propositions_probability(w):
    """The chance of E given the market's Yes, 0.8, and given its No, 0.05, with the market at 0.29 to 0.31: E is
    0.8 x 0.30 + 0.05 x 0.70 = 0.275, each case read at the market's price. The market is kept, and the statement
    returns its question and what resolves it, and its book as the store holds it, with the moment it was read: what
    the analyst judges whether to take its price by."""
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    got = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 0.8}), stated({"given_not": [m], "value": 0.05})])
    assert w.store.type_of(m) == "Market"
    assert p(w, e) == pytest.approx(0.8 * 0.30 + 0.05 * 0.70)
    [terms] = got["markets"]
    assert terms["market"] == m and terms["question"] == "Will it happen?" and set(terms) == {
        "market", "question", "rules", "resolution_source", "close_time", "price", "bid", "ask", "depth",
        "last_trade", "quoted_at", "confirmed_at"}
    assert (terms["bid"], terms["ask"], terms["price"]) == pytest.approx((0.29, 0.31, 0.30))
    assert terms["depth"] == pytest.approx(310.0) and terms["quoted_at"] == terms["confirmed_at"] == RETRIEVED
    assert w.store.get(m)["derived"]["quoted_at"]["value"] == RETRIEVED  # as `get` gives it
    w.link("portfolio", "watches", e)
    [row] = ledger_read(w.store)["rows"]
    assert row["rule"] == "derived" and row["derivation"] == [
        {"given": [m], "given_not": [], "probability": 0.3, "markets": [m], "share": 0.8, "chance": f"{e}.chances[0]"},
        {"given": [], "given_not": [m], "probability": 0.7, "markets": [m], "share": 0.05, "chance": f"{e}.chances[1]"}]


def test_a_books_reading_is_dated_and_its_last_successful_check_moves_with_each_read_that_finds_it(tmp_path):
    """While the as-of is today, a statement returns a market's book with `quoted_at`, when the stored reading was
    taken, and `confirmed_at`, when the book was last read and found to be that reading. A round that finds the quotes
    unchanged keeps no new reading, so `quoted_at` stays and `confirmed_at` moves to the round. A round whose venue
    does not answer leaves the reading and both times, and the return gives the book it holds, saying nothing failed."""
    from harness import readings
    from harness.derive.series import NY

    w = World(tmp_path / "live", asof=datetime.now(NY).date().isoformat())
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    [terms] = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 0.8})])["markets"]
    assert terms["quoted_at"] == terms["confirmed_at"] == RETRIEVED and terms["bid"] == pytest.approx(0.29)

    class Client:
        def __init__(self, fail):
            self.fail = fail

        def books(self, records):
            if self.fail:
                raise VenueError("Polymarket did not answer")
            return {mid: w.books[mid] for mid in records}

    class Venues:
        def __init__(self, fail):
            self.clients = {"polymarket": Client(fail)}

        def client(self, venue):
            return self.clients[venue]

        def market(self, venue, vid):
            return w.venues.market(venue, vid)

    assert readings.refresh(w.store, Venues(fail=False)) == []  # read again, unchanged: nothing kept
    [terms] = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 0.7})])["markets"]
    checked = terms["confirmed_at"]
    assert terms["quoted_at"] == RETRIEVED and checked > RETRIEVED
    readings.refresh(w.store, Venues(fail=True))
    [terms] = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 0.6})])["markets"]
    assert "book_unread" not in terms and terms["bid"] == pytest.approx(0.29)
    assert terms["quoted_at"] == RETRIEVED and terms["confirmed_at"] == checked
    assert Tools(w).get(m)["confirmed_at"] == checked  # as `get` gives a kept market
    w.store.close()


def test_a_market_whose_book_could_not_be_read_returns_why_and_no_figures(w):
    """A market named for the first time has its book read within the statement. Where its venue does not answer, the
    statement stands and its figures wait on the book, and the market's return says why the book is not read, giving
    no bid, ask or price, which would read as a book with no standing quotes."""
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")

    def unanswered(subject, covering):
        raise VenueError("Polymarket did not answer")

    w.store.fetchers["book"] = unanswered
    got = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 0.8})])
    [terms] = got["markets"]
    assert not {"price", "bid", "ask", "depth"} & set(terms)
    assert "could not be read" in terms["book_unread"] and "Polymarket did not answer" in terms["book_unread"]
    assert w.store.get(e)["stated"]["chances"][0]["value"] == 0.8


def test_an_implication_to_or_from_a_market_bounds_the_proposition_from_its_book(w):
    """E implies a market at 0.28 to 0.33: the markets imply E is at most 0.33. A market at 0.10 to 0.15 implies F:
    F is at least 0.10. No judgment enters either: the book bounds the proposition through the link."""
    broad, narrow = w.market("broad", "Broad?", 0.28, 0.33), w.market("narrow", "Narrow?", 0.10, 0.15)
    e, f = w.prop("E"), w.prop("F")
    got = Tools(w).link(e, "implies", broad)
    assert [x["market"] for x in got["markets"]] == [broad]
    w.link(narrow, "implies", f)
    assert tuple(w.store.get(e)["derived"]["range"]["value"]) == pytest.approx((0.0, 0.33))
    assert tuple(w.store.get(f)["derived"]["range"]["value"]) == pytest.approx((0.10, 1.0))
    assert w.store.type_of(narrow) == "Market"  # a market at the source of a link is kept, as one at its target is


def test_a_market_a_proposition_asks_stands_as_that_proposition_through_its_side(w):
    """P asks M with its price not taken, and P's own chance is 0.5: E given M at 0.8 and given not M at 0.05 is
    0.8 x 0.5 + 0.05 x 0.5 = 0.425, M's price not taken because the link says so. Through the market's No side, given
    M is given not P: 0.8 x 0.5 + 0.05 x 0.5 again, with the cases swapped, E at 0.425 with P at 0.5."""
    m, n = w.market("m", "Will it happen?", 0.29, 0.31), w.market("n", "Will it not?", 0.69, 0.71)
    prop, other = w.prop("P", chance=0.5), w.prop("Q", chance=0.2)
    w.link(prop, "asks", m, side="yes", taken=stated(False))
    w.link(other, "asks", n, side="no", taken=stated(False))
    e, f = w.prop("E"), w.prop("F")
    chances(w, e, {"given": [m], "value": 0.8}, {"given_not": [m], "value": 0.05})
    chances(w, f, {"given": [n], "value": 0.8}, {"given_not": [n], "value": 0.05})
    assert p(w, e) == pytest.approx(0.8 * 0.5 + 0.05 * 0.5)
    assert p(w, f) == pytest.approx(0.8 * 0.8 + 0.05 * 0.2)  # n's Yes is Q false, at 0.8
    with pytest.raises(StoreError, match="which asks .* itself"):
        chances(w, prop, {"given": [m], "value": 0.5})


def test_two_markets_at_two_deadlines_stand_as_the_rungs_of_a_ladder_at_the_dates_their_questions_count_through(w):
    """By t1 at 0.20 and by t2 at 0.50, two markets of one question; E by a date two thirds of the way between them,
    even through time: 0.20 + (2/3) x 0.30 = 0.40. The reading says the two and E are one ladder, so the earlier
    implies E and E the later; each market's date is the day its question counts through, as stated."""
    first, second = w.market("t1", "By the first date?", 0.19, 0.21), w.market("t2", "By the second?", 0.49, 0.51)
    e = w.prop("E", end="2027-03-02", start="2026-11-04")  # 2026-12-31 to 2027-04-01 is 91 days; 61 of them
    chances(w, e, {"given": [second], "given_not": [first], "reading": "even",
                   "through": {first: "2026-12-31", second: "2027-04-01"}})
    assert p(w, e) == pytest.approx(0.20 + 61 / 91 * 0.30)
    with pytest.raises(StoreError, match="through"):
        chances(w, e, {"given": [second], "given_not": [first], "reading": "even"})


def test_a_market_no_chance_or_link_names_any_longer_is_not_kept(w):
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    chances(w, e, {"given": [m], "value": 0.8}, {"given_not": [m], "value": 0.05})
    assert w.store.type_of(m) == "Market"
    chances(w, e, {"value": 0.3})
    assert w.store.type_of(m) is None


def test_a_contract_held_on_a_market_that_stands_as_itself_pays_as_often_as_it_holds_in_each_world(tmp_path):
    """E at 0.8 given the market and 0.05 given not it, the market at 0.30, so E is 0.275: a contract held on the
    market's Yes pays in E's Yes world as often as the market holds there, 0.8 x 0.3 / 0.275, and in its No world
    0.2 x 0.3 / 0.725 (MATH.md 1.6, 1.9). The market's probability is its book's, so what the contract pays is fixed."""
    from test_impact import channel, output, world
    w = world(tmp_path, g={"SPY": 10_000.0}, coefficients={"SPY": {}})
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E", baseline="no")
    w.chances(e, {"given": [m], "value": 0.8}, {"given_not": [m], "value": 0.05})
    w.link("portfolio", "watches", e)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    for year, rows in ((2025, []), (2026, [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", str(year), {"results": rows}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    w.store.link("portfolio", "holds", m, {"side": "yes", "quantity": 10})
    out = output(w, e)
    [held] = [h["contract"] for h in out["by_holding"] if h.get("contract")]
    assert held["market"] == m and "waiting" not in out
    assert held["probabilities"] == pytest.approx({"yes": 0.8 * 0.3 / 0.275, "no": 0.2 * 0.3 / 0.725})


def test_a_statement_that_equates_a_proposition_with_a_market_says_so_and_names_what_the_market_stands_as(w):
    """Chances of 1 given a market and 0 given not it are the market implying E and E implying the market: E stands
    at the market's price, and the statement returns that the two have the same outcomes. A chance given the market
    and another proposition is narrower, and says nothing of the kind. A market a proposition asks returns that
    proposition, its side, and whether its price is taken, since it stands as that proposition."""
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    got = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 1.0}), stated({"given_not": [m], "value": 0.0})])
    assert p(w, e) == pytest.approx(0.30)
    [terms] = got["markets"]
    assert terms["claim"] == (f"{e}'s chances of 1 given this market and 0 given not it state that {e} and the "
                              f"market have the same outcomes")
    assert got["propositions"][m] == {"range": [0.29, 0.31], "probability": 0.3}
    b = w.prop("B", chance=0.5)
    got = Tools(w).edit(e, "chances", [stated({"given": [m, b], "value": 1.0}), stated({"given_not": [m], "value": 0.0})])
    assert "claim" not in got["markets"][0]
    q = w.prop("Q", chance=0.4)
    lid = w.link(q, "asks", m, side="yes", taken=stated(False))
    got = Tools(w).edit(e, "chances", [stated({"given": [m], "value": 0.5})])
    assert got["markets"][0]["asked_by"] == {"proposition": q, "link": lid, "side": "yes", "taken": False}
    left = w.store.get(e)["derived"]["left_open"]["value"]  # m stands as Q, at Q's 0.4 and not at m's price
    assert (left["lo"], left["hi"]) == pytest.approx((0.5 * 0.4, 0.5 * 0.4 + 0.6))


def test_a_market_with_no_quote_leaves_what_rests_on_it_open(w):
    """A market standing as itself with no quote on either side bounds nothing, as a market a proposition asks with
    none does: E's chances given it leave E between them, and name it as what E rests on."""
    m = w.market("m", "Will it happen?", None, None)
    e = w.prop("E")
    chances(w, e, {"given": [m], "value": 0.8}, {"given_not": [m], "value": 0.05})
    left = w.store.get(e)["derived"]["left_open"]["value"]
    assert (left["lo"], left["hi"]) == pytest.approx((0.05, 0.8))


def test_an_implication_from_a_market_and_a_contract_held_on_one_return_the_events_they_bear_on(w):
    """A link from a market returns the structure it is made in and the watched event in it; a contract held on a
    market standing as itself returns the event whose structure it stands in."""
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    w.link("portfolio", "watches", e)
    got = Tools(w).link(m, "implies", e)
    assert got["propositions"][e]["range"] == [0.29, 1.0] and [x["market"] for x in got["markets"]] == [m]
    assert [x["id"] for x in got["events"]] == [e]
    got = Tools(w).link("portfolio", "holds", m, {"side": "yes", "quantity": 10})
    assert [x["id"] for x in got["events"]] == [e]


def test_a_market_a_chance_names_is_rewound_with_the_chance(w):
    """A rewind restores the markets the store kept at its checkpoint. A market the chance brought in goes
    with it; one a later chance let go comes back with the chance that named it."""
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    w.store.checkpoint("before")
    chances(w, e, {"given": [m], "value": 0.8}, {"given_not": [m], "value": 0.05})
    w.store.checkpoint("named")
    chances(w, e, {"value": 0.3})
    assert w.store.type_of(m) is None
    w.store.rewind("named")
    assert w.store.type_of(m) == "Market" and p(w, e) == pytest.approx(0.275)
    w.store.rewind("before")
    assert w.store.type_of(m) is None


def test_a_market_a_chance_names_is_not_removed_and_the_refusal_names_the_chance(w):
    m = w.market("m", "Will it happen?", 0.29, 0.31)
    e = w.prop("E")
    chances(w, e, {"given": [m], "value": 0.8})
    with pytest.raises(StoreError, match=f"chance names it; what names it: {e}"):
        w.store.remove(m)
