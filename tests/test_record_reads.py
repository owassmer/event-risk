"""What the record reads from the engine: each watched proposition's probability and range with the
Computations behind them whole, dated when their inputs stood (ledger.watched), for its steps between replies; and
what each link's check says of its claim (ledger.link_states), which check() and a reply's frozen copy read alike."""

from __future__ import annotations

import pytest

from breakdown import ASOF, RETRIEVED, World
from harness import ledger

LATER = "2026-11-04T19:02:00+00:00"


def _book(w: World, mid: str, bid: float, ask: float, at: str) -> None:
    w.set_book(mid, bid, ask)
    w.store.put_data("book", mid, w.books[mid], {"endpoint": "/books"}, retrieved_at=at)


def test_a_step_keeps_the_probability_and_range_with_their_computations_whole_dated_when_their_inputs_stood(tmp_path):
    w = World(tmp_path / "scratch-watched")
    t = w.prop("T")
    m1 = w.market("M1", "Will T happen?", 0.28, 0.33)
    w.link(t, "asks", m1)
    w.link("portfolio", "watches", t)
    got = ledger.watched(w.store)[t]
    assert (got["probability"], *got["range"]) == (pytest.approx(0.305), pytest.approx(0.28), pytest.approx(0.33))
    assert set(got["basis"]) == {"probability", "range"} and set(got["basis"].values()) <= set(got["computations"])
    # whole: every Computation the figures read, down to the book they read, each with its version and manifest
    books = [ref for c in got["computations"].values() for ref in ledger._refs(c["inputs"])
             if ref.get("kind") == "book" and ref.get("data")]
    assert books and all(ref["subject"] == m1 for ref in books)
    assert all({"derivation", "version", "inputs", "output"} <= set(c) for c in got["computations"].values())
    assert got["at"] == RETRIEVED
    # read again with nothing landed: the engine computes nothing, and the step is the same
    made = w.store._one("SELECT count(*) AS n FROM computation")["n"]
    assert ledger.watched(w.store)[t] == got
    assert w.store._one("SELECT count(*) AS n FROM computation")["n"] == made
    # a later book moves the probability, and dates the step when it was read
    _book(w, m1, 0.40, 0.44, LATER)
    moved = ledger.watched(w.store)[t]
    assert moved["probability"] == pytest.approx(0.42) and moved["at"] == LATER
    assert moved["basis"]["probability"] != got["basis"]["probability"]


def test_an_unwatched_proposition_has_no_step(tmp_path):
    w = World(tmp_path / "scratch-unwatched")
    w.link(w.prop("R"), "asks", w.market("M1", "Will R happen?", 0.28, 0.33))
    assert ledger.watched(w.store) == {}


def test_each_links_check_says_what_it_says_of_the_claim_in_spec_3_4s_words(tmp_path):
    answers = {
        ("market_forward", "R", "Will R happen?"): 0.96, ("market_converse", "R", "Will R happen?"): 0.94,
        ("market_forward", "R", "Will R or more happen?"): 0.9, ("market_converse", "R", "Will R or more happen?"): 0.2,
        ("market_forward", "S", "Will S happen?"): None, ("market_converse", "S", "Will S happen?"): None,
        ("implication", "A", "B"): 0.92, ("implication", "B", "C"): 0.1,
    }
    w = World(tmp_path / "scratch-links", answers=answers)
    r, s, a, b, c = (w.prop(x) for x in ("R", "S", "A", "B", "C"))
    same = w.link(r, "asks", w.market("M1", "Will R happen?", 0.28, 0.33))
    wider = w.link(r, "asks", w.market("M2", "Will R or more happen?", 0.40, 0.45))
    unchecked = w.link(s, "asks", w.market("M3", "Will S happen?", 0.10, 0.12))
    holds = w.link(a, "implies", b)
    refused = w.link(b, "implies", c)
    above = w.prop("Rate at least 25", relation=">=", value=25.0, kind="level_at_date", start=None)
    hits = w.prop("Rate reaches 20", relation=">=", value=20.0)
    misses = w.prop("Rate reaches 30", relation=">=", value=30.0)
    given = w.link(above, "implies", hits)
    contradicted = w.link(above, "implies", misses)
    both = w.prop("R and S")
    composite = w.link(both, "and", r)
    states = ledger.link_states(w.store)
    assert states[same]["state"] == "judge allows" and states[same]["reading"] == "the same outcomes"
    # the market's Yes outcomes are the first and the proposition's the second: the forward question asks whether
    # the proposition's lie within the market's, and only that is answered yes
    assert states[wider]["state"] == "judge does not allow"
    assert states[wider]["reading"] == "the second's within the first's"
    converse = states[wider]["checks"]["converse"]
    assert converse["answer"] == pytest.approx(0.2) and converse["judgment"].startswith("jdg:")
    assert states[unchecked]["state"] == "not yet checked" and states[unchecked]["reading"] is None
    assert states[holds]["state"] == "judge allows"
    assert states[holds]["reading"] == "the first's within the second's, or the same outcomes"
    assert states[refused]["state"] == "judge does not allow"
    assert states[given]["state"] == "fields hold" and "within" in states[given]
    assert states[contradicted]["state"] == "fields contradict"
    assert states[contradicted]["of"] != states[contradicted]["within"]
    assert states[composite]["state"] == "a definition"
    assert list(states) == [same, wider, unchecked, holds, refused, given, contradicted, composite]
    # check() lists from the same reading: what the judge does not allow, and what the fields contradict
    listed = ledger.check(w.store)
    assert {d["link"] for d in listed["differs"]} == {wider, refused}  # the analyst's: what differs, and no reading
    assert [d["link"] for d in listed["fields_contradict"]] == [contradicted]
    # a note about the link is about the claim; one about the Judgment answers the reading
    w.store.note("The market counts more than R.", [wider])
    assert ledger.link_states(w.store)[wider]["state"] == "judge does not allow"
    w.store.note("The judge read the rules' first clause alone.", [converse["judgment"]])
    assert ledger.link_states(w.store)[wider]["state"] == "answered by a note"
    assert any(d["link"] == wider for d in ledger.check(w.store)["differs"])  # listed while the claim stands


def test_a_ways_place_is_checked_by_its_fields_or_by_the_judge_and_listed_where_it_does_not_hold(tmp_path):
    """A way claims its outcomes lie within its parent's, or, on an event's No side, within those where the event is
    false (MATH.md 1.1). On one metric and resolution source the fields decide it: "30 or more on the date" within
    "25 or more" holds and "20 or more" does not; on the No side, "below 20" and "25 or more" on one date share no
    value, and "20 or more" shares some, as "reaches 20" can be true with it. Otherwise the judge reads it, the way
    first: whether the parent is true wherever the way is, or false for a way of the No side. check() lists each that
    does not hold, and the record carries no settlement through one."""
    from harness import record
    answers = {("implication", "A happens early", "A happens"): 0.9, ("exclusion", "A happens late", "A happens"): 0.2}
    w = World(tmp_path / "scratch-ways", answers=answers)
    level = dict(relation=">=", kind="level_at_date", start=None)
    e = w.prop("Rate at least 25", value=25.0, **level)
    higher, lower = w.prop("Rate at least 30", value=30.0, **level), w.prop("Rate at least 20", value=20.0, **level)
    below = w.prop("Rate below 20", relation="<", value=20.0, kind="level_at_date", start=None)
    reaches = w.prop("Rate reaches 20", relation=">=", value=20.0)
    within = w.link(e, "way", higher, side="yes")
    outside = w.link(e, "way", lower, side="yes")
    apart = w.link(e, "way", below, side="no")
    shared = w.link(e, "way", reaches, side="no")
    a, early, late = w.prop("A happens"), w.prop("A happens early"), w.prop("A happens late")
    allowed = w.link(a, "way", early, side="yes")
    refused = w.link(a, "way", late, side="no")
    states = ledger.link_states(w.store)
    assert [states[x]["state"] for x in (within, outside, apart, shared)] == \
        ["fields hold", "fields contradict", "fields hold", "fields contradict"]
    assert states[apart]["side"] == states[shared]["side"] == "no" and "side" not in states[within]
    assert states[allowed]["state"] == "judge allows"
    assert states[allowed]["reading"] == "the first's within the second's, or the same outcomes"
    assert states[refused]["state"] == "judge does not allow" and states[refused]["reading"] == "some shared"
    listed = ledger.check(w.store)
    assert [(d["link"], d.get("side")) for d in listed["fields_contradict"]] == [(outside, None), (shared, "no")]
    assert [(d["link"], d["differs"]) for d in listed["differs"]] == [(refused, f"{late} can be true where {a} is true.")]
    # the record keeps each way's check, and reads no settlement through one the fields contradict
    w.link("portfolio", "watches", e)
    record.reply(w.store, session="s", turn=1, stopped=False)
    [frozen] = [v["frozen"] for v in record.replies(w.store)]
    assert {outside, shared} <= frozen.disputed() and not {within, apart} & frozen.disputed()
    v = World(tmp_path / "scratch-way-record")
    event, wider = v.prop("Rate at least 25", value=25.0, **level), v.prop("Rate at least 20", value=20.0, **level)
    v.link(event, "way", wider, side="yes")
    m20 = v.market("M20", "Rate at least 20 on the date?", 0.6, 0.7)
    v.link(wider, "asks", m20)
    v.link("portfolio", "watches", event)
    record.reply(v.store, session="s", turn=1, stopped=False)
    [frozen] = [r["frozen"] for r in record.replies(v.store)]
    assert record.determine(frozen, {m20: 1}) == []  # read through the way, it would be true


def test_an_implication_with_a_market_at_an_end_is_read_by_the_judge_as_the_market_check_reads_it(tmp_path):
    """A market's claim is the judge's to read whatever its fields, and a market at an end of an implication
    stands as its Yes (MATH.md 1.2). From a proposition to a market, the claim is the market check's forward question; from
    a market to a proposition, its converse; each over the date, the proposition and the market, so the claim has one
    reading whichever link states it. Between two markets, whether the second resolves Yes wherever the first does.
    check() lists each the judge does not allow, saying what differs in the market's own terms."""
    answers = {("market_forward", "P", "Will M happen?"): 0.2, ("market_converse", "Q", "Will N happen?"): 0.9,
               ("market_implication", "Will M happen?", "Will N happen?"): 0.1}
    w = World(tmp_path / "scratch-market-ends", answers=answers)
    m, n = w.market("M", "Will M happen?", 0.3, 0.4), w.market("N", "Will N happen?", 0.5, 0.6)
    p, q = w.prop("P"), w.prop("Q")
    to_market, from_market, markets = w.link(p, "implies", m), w.link(n, "implies", q), w.link(m, "implies", n)
    states = ledger.link_states(w.store)
    assert [states[x]["state"] for x in (to_market, from_market, markets)] == \
        ["judge does not allow", "judge allows", "judge does not allow"]
    asked = ledger.link_states(w.store, questions=True)
    assert all("question" not in asked[x] for x in (to_market, from_market, markets))
    assert [(d["link"], d["differs"]) for d in ledger.check(w.store)["differs"]] == [
        (to_market, f"{p} can be true where {m} resolves No."), (markets, f"{m} can resolve Yes where {n} resolves No.")]
    # P asks M puts the same forward question over the same evidence: one claim, one reading
    w.store.remove(to_market)
    same = w.link(p, "asks", m)
    assert ledger.link_states(w.store)[same]["checks"]["forward"]["answer"] == pytest.approx(0.2)


def test_a_band_reads_to_the_judge_from_its_lower_end_to_its_upper(tmp_path):
    """The judge reads a proposition as the analyst wrote it, so a band reads both its thresholds; a
    proposition with one threshold reads as it always has, so no question already asked of it moves. A record's copy
    gives the same reading from its stated fields (evidence.stated_proposition)."""
    from harness.derive import evidence
    from harness.derive.engine import Inputs
    w = World(tmp_path / "scratch-band-reading")
    band = w.prop("Band", relation=">", value=2.5, kind="level_at_date", start=None, upper_comparison="<=",
                  upper_value=3.0)
    above = w.prop("Above", relation=">=", value=25.0, kind="level_at_date", start=None)
    ctx = Inputs(w.store.engine)
    read = evidence.proposition_content(ctx, band)
    assert read["level"] == "above 2.5 and at or below 3"
    assert evidence.proposition_content(ctx, above)["level"] == "at or above 25"
    stated = w.store.stated_of(band)
    assert evidence.stated_proposition(stated, evidence.metric(ctx, stated["metric"])) == read


def test_a_link_not_yet_checked_carries_the_question_its_check_would_ask_now(tmp_path):
    answers = {("market_forward", "S", "Will S happen?"): None, ("market_converse", "S", "Will S happen?"): None}
    w = World(tmp_path / "scratch-unchecked", answers=answers)
    lid = w.link(w.prop("S"), "asks", w.market("M3", "Will S happen?", 0.10, 0.12))
    assert "question" not in ledger.link_states(w.store)[lid]
    asked = ledger.link_states(w.store, questions=True)[lid]["question"]
    assert [q[0] for q in asked["questions"]] == ["market_forward", "market_converse"]
    date, prop, market = asked["evidence"]
    assert date["content"] == {"date": ASOF}
    assert prop["content"]["proposition"] == "S" and market["content"]["market"] == "Will S happen?"
    assert market["content"]["still open"] is True  # the situation as fact, as of the as-of
    # the question is the check's own: answered later by a judge, its Judgments are the ones the check reads, as a
    # tool's call reads a check that waited
    w.judge.answers.update({("market_forward", "S", "Will S happen?"): 0.97,
                            ("market_converse", "S", "Will S happen?"): 0.95})
    got = w.store.engine.ask_all([tuple(q) for q in asked["questions"]], asked["evidence"], version=asked["version"])
    with w.store.engine.strict():
        state = ledger.link_states(w.store)[lid]
    assert state["state"] == "judge allows" and state["reading"] == "the same outcomes"
    assert [state["checks"][f]["judgment"] for f in ("forward", "converse")] == [a["judgment"] for a in got]
