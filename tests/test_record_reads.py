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
