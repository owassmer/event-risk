"""An assumption is stated with its basis in the call that states it (ONTOLOGY.md 2; TOOLS.md 5): the page it cites with
the passages that bear, quoted word for word, or the reasoning, kept as a Note about what was stated, or both. A
statement without one is refused, and leaves the store as it was. The note written with a statement stays while the
statement rests on it, and goes when the statement is restated with another basis, cleared, or removed."""

from __future__ import annotations

import re

from datetime import datetime, timedelta, timezone

import pytest

from breakdown import World
from conftest import snapshot
from harness import store as store_module
from harness.ledger import records
from harness.render import view
from harness.store import SCHEMA_VERSION, Store, StoreError

PAGE = "Forecasters put the chance at about four percent, and most of them see it rising into the spring."
REASON = "The forecasters' figure, read for this event's window, which is the shorter of the two."


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "scratch-basis")
    yield world
    world.store.close()


def page(w, text=PAGE, url="https://example.org/odds") -> str:
    return w.store.put_page(url, text, retrieved_at="2026-11-04T15:00:00+00:00")


def refused(w, call, match: str) -> StoreError:
    """The call is refused with its reason, and nothing is written: no figure, no Note, no citation."""
    before = snapshot(w.store)
    with pytest.raises(StoreError, match=match) as e:
        call()
    assert snapshot(w.store) == before
    return e.value


def factor(w, proxy="XLE") -> str:
    """A factor a shock can be stated on."""
    return w.store.add("Factor", {"name": "energy", "proxy": proxy, "order": 2})["id"]


def is_note_about(w, nid: str, owner: str) -> bool:
    return w.store.type_of(nid) == "Note" and owner in [a["to"] for a in w.store.links_of(nid, "about")]


def test_every_assumption_stated_without_a_basis_is_refused_and_nothing_is_written(w):
    e, b = w.prop("E"), w.prop("B")
    w.link(e, "implies", b)
    m = w.market("M", "E?", 0.39, 0.41)
    asks = w.store.link(e, "asks", m)["id"]  # a market's price taken needs no basis: it states no judgment
    x = factor(w)
    refused(w, lambda: w.store.edit(e, "chances", [{"value": 0.3}]), r"chances\[0\] is stated with its basis: your "
                                                                      r"reasoning")
    refused(w, lambda: w.store.edit(e, "chances", [{"value": 0.3, "follow": m, "responsiveness": 0.5}]),
            r"chances\[0\] is stated with its basis")
    refused(w, lambda: w.store.edit(e, "chances", [{"given": [b], "value": 0.6}]),
            r"chances\[0\] is stated with its basis")
    refused(w, lambda: w.store.edit(e, "priced_in", 0.4), "priced_in is stated with its basis")
    refused(w, lambda: w.store.edit(e, "priced_in", {"value": {"market": m}}), "priced_in is stated with its basis")
    refused(w, lambda: w.store.edit(asks, "taken", False), "taken is false with its basis")
    refused(w, lambda: w.store.link(e, "consequence", x, {"shock": -0.1}), "shock is stated with its basis")
    # A basis that cannot hold refuses the whole statement: the reasoning given beside a passage the page lacks is
    # not written either.
    sid = page(w)
    refused(w, lambda: w.store.edit(e, "chances", [{"value": 0.04, "source_id": sid, "passages": ["about five percent"],
                                                    "note": REASON}]), "word for word")
    refused(w, lambda: w.store.edit(e, "chances", [
        {"given": [b], "value": 0.6, "note": REASON}, {"given_not": [b], "value": 0.1}]),
            r"chances\[1\] is stated with its basis")


def test_a_basis_can_be_a_page_and_the_reasoning_together(w):
    """The worked row's form: the page's passages, and a note on how they carry to the figure."""
    e, sid = w.prop("E"), page(w)
    w.link("portfolio", "watches", e)
    got = w.store.edit(e, "chances", [{"value": 0.04, "source_id": sid,
                                       "passages": ["the chance at about four percent"], "note": REASON}])
    chance = got["stated"]["chances"][0]
    assert (chance["value"], chance["source_id"], chance["passages"]) == (0.04, sid, ["the chance at about four percent"])
    assert w.store.citations_of(e) == {"chances[0]": sid} and w.store.get(sid)["kept"] is True
    assert w.store.stated_of(chance["note"]) == {"text": REASON} and is_note_about(w, chance["note"], e)
    html = view(w.store, e)["html"]  # the reasoning, a note about the event, is in its notes; the chance opens its basis
    assert "shorter of the two" in html.split('data-k="notes"', 1)[1] and f'data-go="x:p:{e}"' in html
    opened = view(w.store, f"x:p:{e}")["html"]
    assert "the chance at about four percent" in opened and "shorter of the two" in opened
    [listed] = [a for a in records(w.store)["assumptions"] if a["id"] == e]
    assert listed["field"] == "chances[0]" and listed["value"] == {"given": [], "given_not": [], "value": 0.04}
    assert listed["basis"] == {"source_id": sid, "passages": ["the chance at about four percent"],
                               "note": {"id": chance["note"], "text": REASON}}


def test_a_propositions_chances_each_rest_on_their_own_basis(w):
    """One chance on a page, the other on reasoning: each opens to its own, and the page shows the passage it cites."""
    e, d = w.prop("E"), w.prop("D")
    w.link(e, "asks", w.market("ME", "E?", 0.01, 0.99))
    w.link(d, "asks", w.market("MD", "D?", 0.28, 0.32))
    sid = page(w, "Given a raise, the rate reaches 25% in most scenarios.", "https://example.org/path")
    w.store.edit(e, "chances", [
        {"given": [d], "value": 0.8, "source_id": sid, "passages": ["reaches 25% in most scenarios"]},
        {"given_not": [d], "value": 0.1, "note": "Without a raise the rate stays near where it is."}])
    w.link("portfolio", "watches", e)
    listed = {a["field"]: a["basis"] for a in records(w.store)["assumptions"] if a["id"] == e}
    note = w.store.stated_of(e)["chances"][1]["note"]
    assert listed == {"chances[0]": {"source_id": sid, "passages": ["reaches 25% in most scenarios"]},
                      "chances[1]": {"note": {"id": note,
                                                         "text": "Without a raise the rate stays near where it is."}}}
    html = view(w.store, f"x:p:{e}")["html"]  # one click from the event's probability, each chance with its own basis
    assert f'data-go="x:p:{e}"' in view(w.store, e)["html"]
    assert "reaches 25% in most scenarios" in html and "stays near where it is" in html


def test_a_restatement_with_another_basis_takes_the_note_written_with_the_one_it_replaces(w, monkeypatch):
    """The note's id, given again, keeps it: a restatement of the figure on the same reasoning, written at its own
    moment. A note the field does not rest on is no basis for it. The note's own text can be edited, which leaves the
    figure as stated, at its moment."""
    e = w.prop("E")
    w.store.edit(e, "chances", [{"value": 0.3, "note": "first reading"}])
    first = w.store.stated_of(e)["chances"][0]["note"]
    at = w.store.stated_at(e, "chances")
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(store_module, "_moment", lambda: later.isoformat(timespec="microseconds"))
    w.store.edit(e, "chances", [{"value": 0.3, "note": first}])
    assert w.store.stated_of(e)["chances"][0]["note"] == first and w.store.stated_at(e, "chances") > at
    free = w.store.note("A note about E, written apart from any figure.", [e])["id"]
    refused(w, lambda: w.store.edit(e, "chances", [{"value": 0.3, "note": free}]),
            re.escape(f"chances[0].note is your reasoning in words, or the id of a note chances already rests on, to "
                      f"keep it; {free} is not one"))
    w.store.edit(e, "priced_in", {"value": 0.2, "note": "today's prices lean the other way"})
    weight = w.store.stated_of(e)["priced_in"]["note"]
    refused(w, lambda: w.store.edit(e, "chances", [{"value": 0.3, "note": weight}]), f"{weight} is not one")
    w.store.edit(e, "chances", [{"value": 0.3, "note": "second reading"}])
    second = w.store.stated_of(e)["chances"][0]["note"]
    assert second != first and w.store.type_of(first) is None and is_note_about(w, second, e)
    assert w.store.type_of(free) == "Note" and w.store.type_of(weight) == "Note"  # a note of its own, and another's
    restated = w.store.stated_at(e, "chances")
    w.store.edit(second, "text", "second reading, put better")
    assert w.store.stated_at(e, "chances") == restated and w.store.stated_of(second)["text"] == "second reading, put better"


def test_clearing_or_removing_a_statement_takes_its_note_and_releases_its_page(w):
    e, a = w.prop("E", baseline="no"), w.prop("A")
    sid = page(w)
    w.store.edit(e, "chances", [{"value": 0.04, "source_id": sid, "passages": ["about four percent"], "note": REASON}])
    note = w.store.stated_of(e)["chances"][0]["note"]
    w.store.edit(e, "chances", None)
    assert w.store.type_of(note) is None and w.store.citations_of(e) == {} and w.store.get(sid)["kept"] is False
    x = factor(w)
    c = w.store.link(e, "consequence", x, {"shock": {"value": -0.1, "note": "the last such episode"}})
    shock = c["stated"]["shock"]["note"]
    gone = w.store.remove(c["id"])
    assert gone["notes"] == [shock] and w.store.type_of(shock) is None
    # Removing a proposition removes its links, and each takes the note it was stated with.
    m = w.market("M", "E?", 0.39, 0.41)
    asks = w.store.link(e, "asks", m, {"taken": {"value": False, "note": "the book is one trader's"}})
    w.store.link(e, "way", a, {"side": "yes"})
    c = w.store.link(e, "consequence", x, {"shock": {"value": -0.1, "note": "the last such episode"}})
    notes = [asks["stated"]["taken"]["note"], c["stated"]["shock"]["note"]]
    gone = w.store.remove(e)
    assert sorted(gone["notes"]) == sorted(notes) and all(w.store.type_of(n) is None for n in notes)


def test_a_note_a_statement_rests_on_stays_while_it_does(w):
    e, other = w.prop("E"), w.prop("Other")
    w.store.edit(e, "chances", [{"value": 0.3, "note": REASON}])
    note = w.store.stated_of(e)["chances"][0]["note"]
    err = refused(w, lambda: w.store.remove(note), re.escape(f"{note} is the basis of {e}.chances[0]: restate it "
                                                              f"with another basis, or remove it, first"))
    assert err.dependents == [f"{e}.chances[0]"]
    [tie] = [a["id"] for a in w.store.links_of(note, "about")]
    refused(w, lambda: w.store.remove(tie), re.escape(f"{note} is the basis of {e}.chances[0], and stays a note "
                                                      f"about {e}"))
    also = w.store.link(note, "about", other)["id"]  # the analyst can say the reasoning bears elsewhere too
    w.store.remove(also)
    w.store.edit(e, "chances", [{"value": 0.3, "source_id": page(w), "passages": ["about four percent"]}])
    assert w.store.type_of(note) is None  # restated on a page, the reasoning it no longer rests on goes


def test_a_rewind_restores_a_statement_with_the_note_it_was_written_with(w):
    """A checkpoint holds the notes with the statements (ONTOLOGY.md 2): rewinding across a restatement brings back the figure
    and the note it rested on, and takes the restatement and its note away."""
    e = w.prop("E")
    w.store.checkpoint("s:0")
    w.store.edit(e, "chances", [{"value": 0.3, "note": "first reading"}])
    first = w.store.stated_of(e)["chances"][0]["note"]
    w.store.checkpoint("s:1")
    held = snapshot(w.store)["tables"]
    w.store.edit(e, "chances", [{"value": 0.25, "note": "second reading"}])
    second = w.store.stated_of(e)["chances"][0]["note"]
    assert w.store.type_of(first) is None
    w.store.rewind("s:1")
    assert w.store.stated_of(e)["chances"] == [{"given": [], "given_not": [], "value": 0.3, "note": first}]
    assert w.store.stated_of(first) == {"text": "first reading"} and is_note_about(w, first, e)
    assert w.store.type_of(second) is None
    assert {t: rows for t, rows in snapshot(w.store)["tables"].items() if t in ("object", "link")} == \
        {t: rows for t, rows in held.items() if t in ("object", "link")}
    w.store.rewind("s:0")
    assert "chances" not in w.store.stated_of(e) and w.store.query("Note") == []


def test_a_market_not_taken_rests_on_a_page_or_its_reason_and_one_taken_on_neither(w):
    e = w.prop("E")
    deep, shallow = w.market("D", "E, deep?", 0.20, 0.24), w.market("S", "E, shallow?", 0.30, 0.34)
    w.set_book(deep, 0.20, 0.24, size=50000.0)
    sid = page(w, "The deep book's quotes have stood unchanged since it listed.", "https://example.org/book")
    one = w.store.link(e, "asks", deep, {"taken": {"value": False, "source_id": sid,
                                                   "passages": ["quotes have stood unchanged"]}})["id"]
    two = w.store.link(e, "asks", shallow, {"taken": True})["id"]
    assert w.store.citations_of(one) == {"taken": sid} and w.store.stated_of(two)["taken"] == {"value": True}
    w.link("portfolio", "watches", e)
    assert w.point(e) == pytest.approx(0.32)  # the deep book, not taken, gives nothing
    w.store.edit(one, "taken", True)  # taken again: nothing to rest on, and its page goes
    assert w.store.citations_of(one) == {} and w.store.get(sid)["kept"] is False
    assert w.point(e) == pytest.approx(0.24)  # the two books cross, and the shallow one gives way to the deep one


def test_a_file_that_holds_no_store_is_refused_as_holding_none(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "store.sqlite").write_bytes(b"")
    with pytest.raises(StoreError, match="holds no store: its store.sqlite has none of a store's tables"):
        Store.open(tmp_path / "empty")


def test_a_store_of_an_earlier_schema_than_the_one_before_this_is_not_opened(tmp_path):
    """A store written before an assumption carried its basis in its value holds figures with none: it is not read
    under rules it was not written to. Only the schema before this one is brought to it (test_fractions)."""
    old = Store.create(tmp_path / "old")
    with old.transaction():
        old._set_meta("schema_version", "9")
    old.close()
    with pytest.raises(StoreError, match=f"is a store of schema 9; this code reads schema {SCHEMA_VERSION}"):
        Store.open(tmp_path / "old")


def test_a_passage_a_shock_a_chance_or_a_market_not_taken_cites_opens_marked_on_its_page(w):
    """The inspector finds each passage by the field that cites it: a shock's, a way's chance given its event, and the
    reason a market is not taken."""
    import hashlib
    e, a = w.prop("E", baseline="no"), w.prop("A")
    sid = page(w, "In the last episode, X fell by a tenth, half of it in one way, and the book was one trader's.",
               "https://example.org/episode")
    cited = {"shock": "X fell by a tenth", "share": "half of it in one way", "taken": "the book was one trader's"}
    w.store.link(e, "consequence", factor(w), {"shock": {"value": -0.1, "source_id": sid, "passages": [cited["shock"]]}})
    w.store.link(e, "way", a, {"side": "yes"})
    w.store.edit(a, "chances", [{"given": [e], "value": 0.5, "source_id": sid, "passages": [cited["share"]]}])
    w.store.link(e, "asks", w.market("M", "E?", 0.39, 0.41), {"taken": {"value": False, "source_id": sid,
                                                                       "passages": [cited["taken"]]}})
    for passage in cited.values():
        html = view(w.store, f"x:passage:{sid}:{hashlib.sha256(passage.encode()).hexdigest()[:12]}")["html"]
        assert f"<mark>{passage}</mark>".replace("'", "&#x27;") in html, passage
