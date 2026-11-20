"""The forecast record: each version's outcome from the record that made it."""

from __future__ import annotations

import pytest

from conftest import ASOF, BookFetcher, StubVenues, fixture_judge
from harness import record
from harness.derive import default_registry
from harness.judge import FixtureJudge
from breakdown import stated


def allowing() -> FixtureJudge:
    """A judge whose reading allows every claim: the cases where what a market asks is the proposition linked to it."""
    return FixtureJudge(default=lambda kind, statement, evidence, levels=None: 0.9)

PROPOSITION = {"text": "The US raises its tariffs by the window's end", "comparison": "occurs", "window_start": ASOF,
               "window_end": "2027-03-31", "kind": "hitting"}


def test_a_forecast_whose_event_was_later_removed_still_gets_its_outcome(make_store):
    venues = StubVenues({"market:kalshi:M1": {"question": "Will the US raise tariffs by March 31?"}})
    store = make_store(registry_=default_registry(), judge=allowing(), venues=venues,
                       fetchers={"book": BookFetcher({"market:kalshi:M1": {"bid": 0.28, "ask": 0.33}})})
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"cash": 1000.0}]})
    e = store.add("Proposition", {**PROPOSITION, "title": "Tariffs of 25% or more", "chances": [stated(0.2)]})["id"]
    r = store.add("Proposition", {**PROPOSITION, "title": "A raise occurs"})["id"]
    store.link(e, "implies", r)
    store.link(r, "asks", "market:kalshi:M1")
    store.link("portfolio", "watches", e)
    first = record.reply(store, session="s", turn=1, stopped=False)
    # the analyst removes the event and its breakdown, and the market with them; then the market settles No
    store.remove(e)
    store.remove(r)
    assert "market:kalshi:M1" not in store.ids_of("Market")
    record.reply(store, session="s", turn=2, stopped=False)
    assert "market:kalshi:M1" in record.markets(store)  # its settlement is still read

    found = record.record_outcomes(store, {"market:kalshi:M1": 0})
    [got] = found["recorded"]
    assert (got["proposition"], got["outcome"], got["records"]) == (e, 0, [first])
    assert [m["market"] for m in got["markets"]] == ["market:kalshi:M1"]

    [version] = [v for v in record.history(store) if v["proposition"] == e]
    assert version["outcome"]["outcome"] == 0
    assert version["ended"]["how"] == "no longer watched" and version["ended"]["turn"] == 2
    [forecast] = version["forecasts"]
    assert forecast["p"] == 0.2 and abs(forecast["brier"] - 0.04) < 1e-12
    # it stood until it was no longer watched, and nothing of it stood when its outcome became known
    assert forecast["until"] == version["ended"]["at"]
    assert version["last_before_outcome"] is None


def _settling(make_store, **link):
    """E watched, asked by M1 through the link given; the record made once."""
    venues = StubVenues({"market:kalshi:M1": {"question": "Will the US raise tariffs by March 31?"}})
    store = make_store(registry_=default_registry(), judge=allowing(), venues=venues,
                       fetchers={"book": BookFetcher({"market:kalshi:M1": {"bid": 0.28, "ask": 0.33}})})
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"cash": 1000.0}]})
    e = store.add("Proposition", {**PROPOSITION, "title": "A raise occurs"})["id"]
    store.link(e, "asks", "market:kalshi:M1", link)
    store.link("portfolio", "watches", e)
    record.reply(store, session="s", turn=1, stopped=False)
    return store, e


def test_a_market_whose_price_is_not_taken_still_settles_what_it_asks(make_store):
    """Whether a price is taken is a judgment about its traders; a settlement says whether the outcomes the market asks
    happened, so it decides its proposition taken or not."""
    store, e = _settling(make_store, taken=stated(False, "its traders lean on one source"))
    [got] = record.record_outcomes(store, {"market:kalshi:M1": 1})["recorded"]
    assert (got["proposition"], got["outcome"]) == (e, 1)


def test_a_market_asked_through_its_no_side_that_settles_yes_makes_its_proposition_false(make_store):
    store, e = _settling(make_store, side="no")
    [got] = record.record_outcomes(store, {"market:kalshi:M1": 1})["recorded"]
    assert (got["proposition"], got["outcome"]) == (e, 0)
    assert got["markets"] == [{"market": "market:kalshi:M1", "link": store.links_of(e, "asks")[0]["id"], "result": 1}]


@pytest.mark.parametrize("asked_by_no", [False, True], ids=["standing as itself", "asked through its No side"])
@pytest.mark.parametrize("direction, result, outcome", [
    ("market implies event", 1, 1), ("market implies event", 0, None),
    ("event implies market", 0, 0), ("event implies market", 1, None)])
def test_a_settled_market_at_an_end_of_an_implication_decides_what_the_implication_carries(
        make_store, direction, result, outcome, asked_by_no):
    """MATH.md 1.2: a market stands as the proposition its question and rules define. M implies E, with M settled
    Yes, decides E true; E implies M, with M settled No, decides E false; each settled the other way decides nothing.
    Where P asks M through M's No side, M stands as P false, and P is decided from M's result through that link: the
    same outcomes, read through the record's own links as the store's structure reads them."""
    m = "market:kalshi:M1"
    venues = StubVenues({m: {"question": "Will the US raise tariffs by March 31?"}})
    store = make_store(registry_=default_registry(), judge=allowing(), venues=venues,
                       fetchers={"book": BookFetcher({m: {"bid": 0.28, "ask": 0.33}})})
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"cash": 1000.0}]})
    e = store.add("Proposition", {**PROPOSITION, "title": "E", "chances": [stated(0.2)]})["id"]
    if asked_by_no:
        q = store.add("Proposition", {**PROPOSITION, "title": "P", "chances": [stated(0.7)]})["id"]
        store.link(q, "asks", m, {"side": "no"})
    store.link(*((m, "implies", e) if direction == "market implies event" else (e, "implies", m)))
    store.link("portfolio", "watches", e)
    record.reply(store, session="s", turn=1, stopped=False)
    got = [r for r in record.record_outcomes(store, {m: result})["recorded"] if r["proposition"] == e]
    if outcome is None:
        assert got == []
        return
    [found] = got
    assert found["outcome"] == outcome and [x["market"] for x in found["markets"]] == [m]
    assert ("link" in found["markets"][0]) == asked_by_no  # a market standing as itself is decided on no link


def test_a_row_that_cannot_be_worked_out_leaves_every_other_row_of_the_reply_recorded_as_it_stands(tmp_path):
    """When a reply ends, the store records each watched proposition's row as it stands. Each row is worked
    out on its own: one whose derivation raises inside the engine is left out of the record's rows, the trace names it
    with the record it is missing from, and every other row of the reply is recorded as the same state records it with
    nothing raising. The record's copy of what the analyst had written still holds the event."""
    from dataclasses import replace

    from test_impact import channel, event, fixture_registry, world
    from harness.derive import Registry
    from harness.trace import Trace

    failing: set[str] = set()

    def raising(d):
        def inputs(ctx, subject):
            if subject in failing:
                raise RuntimeError(f"a defect in the impact of {subject}")
            return d.inputs(ctx, subject)
        return replace(d, inputs=inputs)

    real = fixture_registry()
    registry = Registry([raising(real.get(n)) if n == "impact" else real.get(n) for n in real.versions()])
    w = world(tmp_path, registry=registry, g={"SPY": 10_000.0, "UUP": 5_000.0}, coefficients={"UUP": {"SPY": 1.4}})
    market, dollar = channel(w, "SPY"), channel(w, "UUP")
    events = []
    for title, p, on, shock in (("first", 0.2, market, 0.10), ("second", 0.3, dollar, 0.05), ("third", 0.4, market, 0.02)):
        e = event(w, title=title, p=p)
        w.link(e, "consequence", on, shock=shock)
        events.append(e)

    failing.add(events[0])  # the first row raises, and the others are worked out after it
    w.store.engine.reset()
    left_out = record.reply(w.store, session="s", turn=1, stopped=False)
    failing.clear()
    w.store.engine.reset()
    whole = record.reply(w.store, session="s", turn=2, stopped=False)

    assert left_out is not None and whole is not None
    first, second = w.store.records()
    assert [r["id"] for r in second["rows"]] == events and all(r["full_impact"] is not None for r in second["rows"])
    assert first["rows"] == second["rows"][1:]
    assert any(link["relation"] == "watches" and link["to_id"] == events[0] for link in first["written"]["link"])
    errors = [x for x in Trace(w.store.trace_path).entries() if x["type"] == "record_error"]
    assert [(x["proposition"], x["record"], x["turn"]) for x in errors] == [(events[0], left_out, 1)]
    assert "a defect in the impact of" in errors[0]["error"]


def test_a_band_restated_at_its_upper_end_asks_another_question(tmp_path):
    """A proposition's definition is every field that says what would make it true (ONTOLOGY.md 2.1), a band's upper
    end among them: a forecast is scored on the question it asked, so a band whose upper end is
    restated is a new version, and its row carries that end as it carries the lower one."""
    from breakdown import World
    from harness import ledger, schema
    w = World(tmp_path / "scratch-band")
    band = w.prop("Rate from 20 to 25", ">=", 20.0, kind="level_at_date", upper_comparison="<", upper_value=25.0,
                  chance=0.3)
    w.link("portfolio", "watches", band)
    record.reply(w.store, session="s", turn=1, stopped=False)
    assert (ledger.row(w.store, band)["upper_comparison"], ledger.row(w.store, band)["upper_value"]) == ("<", 25.0)
    definition = {k: v for k, v in w.store.stated_of(band).items() if k in schema.DEFINITION}
    w.store.edit(band, "definition", {**definition, "upper_value": 30.0})
    record.reply(w.store, session="s", turn=2, stopped=False)
    versions = [v for v in record.history(w.store) if v["proposition"] == band]
    assert [v["stated"]["upper_value"] for v in versions] == [25.0, 30.0]
    assert versions[0]["ended"]["how"] == "redefined" and versions[1]["ended"] is None


@pytest.mark.parametrize("settled, outcome", [({"M30": 1}, 1), ({"M20": 0}, 0), ({"M30": 0}, None)],
                         ids=["a narrower rung settles Yes", "a broader rung settles No", "a narrower rung settles No"])
def test_a_ladder_decides_its_rung_by_the_fields_with_no_link_made(tmp_path, settled, outcome):
    """A ladder is a structure, read as any other, whether or not the analyst links its rungs (MATH.md 1.2): the
    rate above 30 on a date lies within the rate at 25 or more on it, and that within the rate above 20. So where a
    rung's market settles, the record decides the watched rung through the fields, as its probability was read."""
    from breakdown import World
    w = World(tmp_path / "scratch-ladder")
    watched = w.prop("Rate at 25 or more on March 31", ">=", 25.0, kind="level_at_date", chance=0.3)
    for vid, value, bid, ask in (("M30", 30.0, 0.10, 0.15), ("M20", 20.0, 0.86, 0.90)):
        rung = w.prop(f"Rate above {value:g} on March 31", ">", value, kind="level_at_date")
        w.link(rung, "asks", w.market(vid, f"Above {value:g}% on March 31?", bid, ask))
    w.link("portfolio", "watches", watched)
    record.reply(w.store, session="s", turn=1, stopped=False)
    got = record.record_outcomes(w.store, {f"market:polymarket:{k}": v for k, v in settled.items()})["recorded"]
    assert [(r["proposition"], r["outcome"]) for r in got] == ([] if outcome is None else [(watched, outcome)])


@pytest.mark.parametrize("settled, outcome", [({"M1": 1}, 1), ({"M2": 0}, 0), ({"M1": 0}, None)],
                         ids=["the earlier rung settles Yes", "the later rung settles No", "the earlier settles No"])
def test_a_deadline_reading_decides_the_date_between_its_rungs(tmp_path, settled, outcome):
    """A deadline reading states that the proposition and its rungs concern the same occurrence, counted from the same
    start, by their dates, and the store places them by those dates (MATH.md 1.2): the earlier rung implies the
    proposition and the proposition the later rung. An occurrence its text names alone is on no line, so it is the
    reading that places it, and the record decides it through that placing as the probability was read."""
    from breakdown import World
    w = World(tmp_path / "scratch-reading")
    deal = "The two countries sign a trade agreement"
    early = w.prop(f"{deal} by December 31", end="2026-12-31")
    late = w.prop(f"{deal} by March 31", end="2027-03-31")
    w.link(early, "asks", w.market("M1", "A trade agreement by December 31?", 0.20, 0.24))
    w.link(late, "asks", w.market("M2", "A trade agreement by March 31?", 0.48, 0.52))
    watched = w.prop(f"{deal} by February 15", end="2027-02-15")
    w.chances(watched, {"given": [late], "given_not": [early], "reading": "even"})
    w.link("portfolio", "watches", watched)
    record.reply(w.store, session="s", turn=1, stopped=False)
    got = record.record_outcomes(w.store, {f"market:polymarket:{k}": v for k, v in settled.items()})["recorded"]
    assert [(r["proposition"], r["outcome"]) for r in got] == ([] if outcome is None else [(watched, outcome)])


def _reply_at(monkeypatch, store, at: str, turn: int) -> str:
    """A reply's record made at a given moment, the clock the store writes by set to it."""
    import harness.store
    monkeypatch.setattr(harness.store, "_now", lambda: at)
    rid = record.reply(store, session="s", turn=turn, stopped=False)
    monkeypatch.undo()
    return rid


def _settles(store, mid: str, result: int, at: str, read: str) -> None:
    """A market's settlement as the store keeps it: the venue's result and its time, read at `read`."""
    store.put_data("settlement", mid, {"settled": True, "result": result, "at": at}, {"endpoint": "/markets"},
                   retrieved_at=read, range=(read[:10], read[:10]))


def test_a_forecast_counts_until_its_outcome_was_known_not_until_the_store_recorded_it(tmp_path, monkeypatch):
    """A forecast counts while its outcome was not yet publicly known under its rules, and when it became known is one
    fact on the outcome record. A deadline that is met is known when the market that asks it settles;
    a forecast made after that is not scored, and the one standing then ends there, not when this store happened to
    write the outcome."""
    from breakdown import World
    w = World(tmp_path / "scratch-known")
    e = w.prop("A raise occurs", chance=0.4)
    w.link(e, "asks", w.market("M1", "Will the US raise tariffs by March 31?", 0.38, 0.42))
    w.link("portfolio", "watches", e)
    first = _reply_at(monkeypatch, w.store, "2027-01-10T15:00:00+00:00", 1)
    _reply_at(monkeypatch, w.store, "2027-01-20T15:00:00+00:00", 2)
    _settles(w.store, "market:polymarket:M1", 1, "2027-01-15T18:00:00+00:00", "2027-01-25T09:00:00+00:00")
    [got] = record.record_outcomes(w.store, {"market:polymarket:M1": 1})["recorded"]
    [version] = record.history(w.store)
    assert version["outcome"]["known_at"] == "2027-01-15T18:00:00+00:00"
    early, late = version["forecasts"]
    assert early["until"] == "2027-01-15T18:00:00+00:00" and early["rule"] == "chance"
    assert "brier" not in late  # made once the outcome was known
    assert version["last_before_outcome"]["record"] == first


def test_a_decision_on_a_date_is_known_from_that_date_whenever_its_market_settles(tmp_path, monkeypatch):
    """Under its rules, a level on a date is known on that date: a forecast made that day is made once the outcome
    can be known, though the market settles a day later."""
    from breakdown import World
    w = World(tmp_path / "scratch-dated")
    e = w.prop("Rate at 25 or more on March 31", ">=", 25.0, kind="level_at_date", chance=0.3)
    w.link(e, "asks", w.market("M25", "At 25% or more on March 31?", 0.28, 0.32))
    w.link("portfolio", "watches", e)
    first = _reply_at(monkeypatch, w.store, "2027-03-30T15:00:00+00:00", 1)
    _reply_at(monkeypatch, w.store, "2027-03-31T15:00:00+00:00", 2)
    _settles(w.store, "market:polymarket:M25", 0, "2027-04-01T16:00:00+00:00", "2027-04-02T09:00:00+00:00")
    record.record_outcomes(w.store, {"market:polymarket:M25": 0})
    [version] = record.history(w.store)
    assert version["outcome"]["known_at"] == "2027-03-31T04:00:00+00:00"  # the date's start in New York
    assert version["forecasts"][0]["until"] == "2027-03-31T04:00:00+00:00"
    assert "brier" not in version["forecasts"][1]
    assert version["last_before_outcome"]["record"] == first


def test_a_version_dropped_after_its_outcome_was_known_stood_at_it(tmp_path, monkeypatch):
    """A version no longer watched from a reply after its outcome became known was standing when it did."""
    from breakdown import World
    w = World(tmp_path / "scratch-dropped")
    e = w.prop("A raise occurs", chance=0.4)
    w.link(e, "asks", w.market("M1", "Will the US raise tariffs by March 31?", 0.38, 0.42))
    watching = w.link("portfolio", "watches", e)
    first = _reply_at(monkeypatch, w.store, "2027-01-10T15:00:00+00:00", 1)
    w.store.remove(watching)
    _reply_at(monkeypatch, w.store, "2027-01-20T15:00:00+00:00", 2)
    _settles(w.store, "market:polymarket:M1", 1, "2027-01-15T18:00:00+00:00", "2027-01-25T09:00:00+00:00")
    record.record_outcomes(w.store, {"market:polymarket:M1": 1})
    [version] = record.history(w.store)
    assert version["ended"]["how"] == "no longer watched"
    assert version["forecasts"][0]["until"] == "2027-01-15T18:00:00+00:00"
    assert version["last_before_outcome"]["record"] == first


def test_a_version_watched_again_stands_in_two_spells_and_has_not_ended(tmp_path, monkeypatch):
    """Each version is scored for the time it stood: one no longer watched stands no longer, and one
    watched again under the same definition stands again, its earlier forecast ending where it was dropped."""
    from breakdown import World
    w = World(tmp_path / "scratch-again")
    e = w.prop("A raise occurs", chance=0.4)
    w.link(e, "asks", w.market("M1", "Will the US raise tariffs by March 31?", 0.38, 0.42))
    watching = w.link("portfolio", "watches", e)
    _reply_at(monkeypatch, w.store, "2027-01-10T15:00:00+00:00", 1)
    w.store.remove(watching)
    _reply_at(monkeypatch, w.store, "2027-01-12T15:00:00+00:00", 2)
    w.link("portfolio", "watches", e)
    again = _reply_at(monkeypatch, w.store, "2027-01-14T15:00:00+00:00", 3)
    _settles(w.store, "market:polymarket:M1", 1, "2027-01-15T18:00:00+00:00", "2027-01-16T09:00:00+00:00")
    record.record_outcomes(w.store, {"market:polymarket:M1": 1})
    [version] = record.history(w.store)
    first, last = version["forecasts"]
    assert first["until"] == "2027-01-12T15:00:00+00:00"  # dropped at the second reply
    assert last["until"] == "2027-01-15T18:00:00+00:00"
    assert version["ended"] is None and version["last_before_outcome"]["record"] == again


@pytest.mark.parametrize("kept", [True, False], ids=["the record keeps the checks", "a record made before it kept them"])
def test_a_claim_the_fields_contradict_carries_no_settlement(tmp_path, monkeypatch, kept):
    """A claim stands because it is the analyst's, and check() lists it while the fields contradict it (MATH.md 1.2).
    Scored, it would grade a forecast by a relation the arithmetic finds false: the rate at 20 or more is claimed to
    imply the rate at 25 or more, so the market on 25 settling No would decide 20 false. The record reads the claim's
    check as it stood, or, where it kept none, tests the fields of its own copy, and the claim carries nothing."""
    from breakdown import World
    w = World(tmp_path / "scratch-contradicted")
    watched = w.prop("Rate at 20 or more on March 31", ">=", 20.0, kind="level_at_date", chance=0.8)
    wider = w.prop("Rate at 25 or more on March 31", ">=", 25.0, kind="level_at_date")
    w.link(wider, "asks", w.market("M25", "At 25% or more on March 31?", 0.28, 0.32))
    w.link(watched, "implies", wider)
    w.link("portfolio", "watches", watched)
    if not kept:
        monkeypatch.setattr(record, "link_states", _unread)
    record.reply(w.store, session="s", turn=1, stopped=False)
    assert ("checks" in w.store.records()[0]["written"]) == kept
    assert record.record_outcomes(w.store, {"market:polymarket:M25": 0})["recorded"] == []


@pytest.mark.parametrize("kept", [True, False], ids=["the record keeps the checks", "a record made before it kept them"])
def test_a_market_whose_texts_the_judge_reads_as_different_settles_nothing(tmp_path, monkeypatch, kept):
    """Where the judge's reading of a market and the proposition linked to it is one the claim does not allow, the
    market can resolve Yes where the proposition is false: its settlement does not say whether the
    proposition happened, and the record decides nothing by it. A record made before it kept each claim's check reads
    the judge's answers its rows hold for the proposition's own markets."""
    from breakdown import World
    w = World(tmp_path / "scratch-differs", default=0.2)
    e = w.prop("A raise occurs", chance=0.4)
    w.link(e, "asks", w.market("M1", "Will the US raise tariffs on one country by March 31?", 0.38, 0.42))
    w.link("portfolio", "watches", e)
    if not kept:
        monkeypatch.setattr(record, "link_states", _unread)
    record.reply(w.store, session="s", turn=1, stopped=False)
    assert record.record_outcomes(w.store, {"market:polymarket:M1": 1})["recorded"] == []
    allowed = World(tmp_path / "scratch-same", default=0.9)  # the same, with a reading the claim allows
    e = allowed.prop("A raise occurs", chance=0.4)
    allowed.link(e, "asks", allowed.market("M1", "Will the US raise tariffs by March 31?", 0.38, 0.42))
    allowed.link("portfolio", "watches", e)
    record.reply(allowed.store, session="s", turn=1, stopped=False)
    [got] = record.record_outcomes(allowed.store, {"market:polymarket:M1": 1})["recorded"]
    assert got["outcome"] == 1


def _unread(store):
    raise RuntimeError("a record made before the record kept each claim's check")


def test_a_record_names_the_schema_its_copy_of_the_statements_is_in(make_store):
    """A record keeps what the analyst wrote as written, in the units of the schema it was written under, so
    it says which schema that was."""
    from harness.store import SCHEMA_VERSION
    import json
    store = make_store(registry_=default_registry(), judge=fixture_judge())
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"cash": 1000.0}]})
    rid = record.reply(store, session="s", turn=1, stopped=False)
    assert rid is not None
    found = store._one("SELECT versions FROM record WHERE id = ?", rid)
    assert json.loads(found["versions"])["schema"] == SCHEMA_VERSION


def test_definition_versions_cover_strictness_added_upper_end_null_equivalence_and_page_history(tmp_path, monkeypatch):
    """A proposition's definition is schema.DEFINITION whole, read from the record's written copy, with a null optional
    field and an absent one treated as one question."""
    import json
    import re
    from html import unescape
    from breakdown import World
    from harness import ledger, schema
    from harness.render import Reader, event_view

    w = World(tmp_path / "scratch-defs")
    p = w.prop("Tariff band", ">=", 20.0, kind="level_at_date", start=None, chance=0.25)
    w.link("portfolio", "watches", p)
    record.reply(w.store, session="s", turn=1, stopped=False)

    # Giving an upper end to a single-threshold proposition starts a second version (1b).
    d1 = {k: v for k, v in w.store.stated_of(p).items() if k in schema.DEFINITION}
    w.store.edit(p, "definition", {**d1, "upper_comparison": "<", "upper_value": 25.0})
    w.chances(p, {"given": [], "given_not": [], "value": 0.40}, keep=False)
    record.reply(w.store, session="s", turn=2, stopped=False)

    # Restating strictness (< to <=) starts a third version (1a), recorded with pre-repair rows lacking upper_*.
    d2 = {k: v for k, v in w.store.stated_of(p).items() if k in schema.DEFINITION}
    w.store.edit(p, "definition", {**d2, "upper_comparison": "<="})
    w.chances(p, {"given": [], "given_not": [], "value": 0.55}, keep=False)
    orig_row = ledger.row
    monkeypatch.setattr(record, "row",
                        lambda st, pid: {k: v for k, v in orig_row(st, pid).items()
                                         if k not in ("upper_comparison", "upper_value")})
    record.reply(w.store, session="s", turn=3, stopped=False)

    # Explicit null optional field ("window_start": None) in written["object"] is the same question as absent.
    w.chances(p, {"given": [], "given_not": [], "value": 0.65}, keep=False)
    orig_written = w.store.written

    def written_with_null():
        wr, tx = orig_written()
        for obj in wr["object"]:
            if obj["id"] == p:
                obj["stated"] = {**obj["stated"], "window_start": None}
        return wr, tx

    monkeypatch.setattr(w.store, "written", written_with_null)
    record.reply(w.store, session="s", turn=4, stopped=False)
    monkeypatch.undo()

    versions = [v for v in record.history(w.store) if v["proposition"] == p]
    assert len(versions) == 3
    assert [len(v["forecasts"]) for v in versions] == [1, 1, 2]

    found = re.search(r'data-history="([^"]+)"', event_view(Reader(w.store), p)["html"])
    assert found is not None
    pts = [round(val, 4) for _, val in json.loads(unescape(found.group(1)))["ours"]]
    assert pts[:2] == [0.55, 0.65]  # pre-repair rows without upper_* stayed in version 3 via written["object"]


def test_ladders_bands_strictness_sources_and_conflicting_settlements(tmp_path):
    """Band outcomes from two rungs or falsified by one (2a), strict vs non-strict (2b), unlinked deadline ladders
    (2c), distinct resolution sources (2e), and settlements contradicting field order (2g)."""
    from breakdown import World

    # 2a, 2b, 2e, 2g on one level_at_date setup
    def run_band(settled: dict[str, int]):
        w = World(tmp_path / f"band-{'_'.join(f'{k}{v}' for k, v in sorted(settled.items()))}")
        ge25 = w.prop("At least 25", ">=", 25.0, kind="level_at_date", start=None, chance=0.5)
        gt25 = w.prop("Above 25", ">", 25.0, kind="level_at_date", start=None)
        gt30 = w.prop("Above 30", ">", 30.0, kind="level_at_date", start=None)
        band = w.prop("Band (25, 30]", ">", 25.0, kind="level_at_date", start=None, upper_comparison="<=",
                      upper_value=30.0, chance=0.3)
        other_src = w.prop("Above 30 other source", ">", 30.0, kind="level_at_date", start=None,
                           resolution_source="Another publisher")
        w.link(gt25, "asks", w.market("M25", "Above 25%?", 0.45, 0.50))
        w.link(gt30, "asks", w.market("M30", "Above 30%?", 0.15, 0.20))
        w.link(other_src, "asks", w.market("M_OTHER", "Other source >30%?", 0.15, 0.20))
        w.link("portfolio", "watches", ge25)
        w.link("portfolio", "watches", band)
        record.reply(w.store, session="s", turn=1, stopped=False)
        res = record.record_outcomes(w.store, {f"market:polymarket:{k}": v for k, v in settled.items()})
        return {r["proposition"]: r["outcome"] for r in res["recorded"]}, res["cannot_hold"], ge25, band

    got, bad, ge25, band = run_band({"M25": 1, "M30": 0})
    assert got == {ge25: 1, band: 1} and bad == []  # 2a, 2b
    got, bad, ge25, band = run_band({"M25": 1})
    assert got == {ge25: 1} and bad == []  # band still open on M25=1 alone
    got, bad, ge25, band = run_band({"M25": 0})
    assert got == {band: 0} and bad == []  # 2b: >=25 stays open when >25 is No; 2a: band is false
    got, bad, ge25, band = run_band({"M30": 1})
    assert got == {ge25: 1, band: 0} and bad == []
    got, bad, _, _ = run_band({"M_OTHER": 1})
    assert got == {} and bad == []  # 2e: different resolution source decides nothing
    got, bad, _, _ = run_band({"M25": 0, "M30": 1})
    assert got == {} and len(bad) == 2  # 2g: higher rung Yes and lower rung No cannot hold

    # 2c: unlinked deadline ladder on one occurrence metric and source
    for key, val, expected_early, expected_late in (("M_DEC", 1, 1, 1), ("M_MAR", 0, 0, 0)):
        w = World(tmp_path / f"deadlines-{key}-{val}")
        occ = w.store.add("Metric", {"id": "deal", "name": "Trade deal", "kind": "occurrence",
                                     "resolution_source": "USTR"})["id"]
        early = w.store.add("Proposition", {"title": "Deal by Dec", "text": "Signed by Dec 31", "metric": occ,
                                            "comparison": "occurs", "window_start": ASOF, "window_end": "2026-12-31",
                                            "kind": "hitting", "chances": [stated(0.2)]})["id"]
        late = w.store.add("Proposition", {"title": "Deal by Mar", "text": "Signed by Mar 31", "metric": occ,
                                           "comparison": "occurs", "window_start": ASOF, "window_end": "2027-03-31",
                                           "kind": "hitting", "chances": [stated(0.5)]})["id"]
        w.link(early, "asks", w.market("M_DEC", "Deal by Dec 31?", 0.18, 0.22))
        w.link(late, "asks", w.market("M_MAR", "Deal by Mar 31?", 0.48, 0.52))
        w.link("portfolio", "watches", early)
        w.link("portfolio", "watches", late)
        record.reply(w.store, session="s", turn=1, stopped=False)
        got = {r["proposition"]: r["outcome"]
               for r in record.record_outcomes(w.store, {f"market:polymarket:{key}": val})["recorded"]}
        assert got == {early: expected_early, late: expected_late}


def test_a_reading_with_market_rungs_and_a_numeric_chance_on_a_conflicting_driver(tmp_path):
    """A deadline reading whose rungs are markets with `through` dates places and settles the proposition even after
    removal (2f), while a plain numeric chance given an unrelated driver with conflicting markets does not block E."""
    from breakdown import World

    w = World(tmp_path / "scratch-through-and-driver")
    m_early = w.market("M_EARLY", "By Dec 31?", 0.20, 0.24)
    m_late = w.market("M_LATE", "By Jun 30?", 0.58, 0.62)
    e = w.prop("By Mar 31", end="2027-03-31")
    d = w.prop("Driver D", ">=", 25.0, kind="level_at_date", start=None)
    d_hi = w.prop("Driver above 30", ">", 30.0, kind="level_at_date", start=None)
    m_d25 = w.market("MD25", "D >= 25?", 0.40, 0.45)
    m_d30 = w.market("MD30", "D > 30?", 0.15, 0.20)
    w.link(d, "asks", m_d25)
    w.link(d_hi, "asks", m_d30)
    w.chances(e,
              {"given": [d], "given_not": [], "value": 0.7},
              {"given": [m_late], "given_not": [m_early], "reading": "even",
               "through": {m_early: "2026-12-31", m_late: "2027-06-30"}})
    watching = w.link("portfolio", "watches", e)
    record.reply(w.store, session="s", turn=1, stopped=False)
    w.store.remove(watching)
    w.store.remove(e)
    record.reply(w.store, session="s", turn=2, stopped=False)
    assert {m_early, m_late} <= set(record.markets(w.store))

    # D's markets conflict (MD30=1 while MD25=0), yet E is decided cleanly from M_EARLY=1 without cannot_hold.
    res = record.record_outcomes(w.store, {m_early: 1, m_d25: 0, m_d30: 1})
    assert res["cannot_hold"] == []
    assert [(r["proposition"], r["outcome"]) for r in res["recorded"]] == [(e, 1)]


def test_known_at_uses_earliest_deciding_prefix_of_settlements_and_fallbacks(tmp_path, monkeypatch):
    """Conjunctive outcomes become known when the last required market settles; disjunctive outcomes when the first
    sufficient market settles (3c); a settlement with no venue timestamp falls back to retrieved_at and a bare
    settlement to recorded strictly (3d); and an unmet hitting window bounds known_at at the day after window_end
    while recording nothing when no market settled (3f)."""
    from breakdown import World

    # 3c conjunctive: E = A and B, MA settles Jan 10, MB settles Mar 20 -> known Mar 20, Feb 1 forecast scored
    w = World(tmp_path / "scratch-conj")
    a, b, both = w.prop("A", chance=0.5), w.prop("B", chance=0.6), w.prop("A and B")
    ma = w.market("MA", "Will A happen?", 0.48, 0.52)
    mb = w.market("MB", "Will B happen?", 0.58, 0.62)
    w.link(a, "asks", ma)
    w.link(b, "asks", mb)
    w.link(both, "and", a)
    w.link(both, "and", b)
    w.chances(both, {"given": [a], "given_not": [], "value": 0.6})
    w.link("portfolio", "watches", both)
    _reply_at(monkeypatch, w.store, "2027-01-05T15:00:00+00:00", 1)
    w.chances(both, {"given": [a], "given_not": [], "value": 0.8}, keep=False)
    r2 = _reply_at(monkeypatch, w.store, "2027-02-01T15:00:00+00:00", 2)
    _settles(w.store, ma, 1, "2027-01-10T15:00:00+00:00", "2027-01-11T09:00:00+00:00")
    _settles(w.store, mb, 1, "2027-03-20T15:00:00+00:00", "2027-03-21T09:00:00+00:00")
    record.record_outcomes(w.store, {ma: 1, mb: 1})
    [ver] = record.history(w.store)
    assert ver["outcome"]["known_at"] == "2027-03-20T15:00:00+00:00"
    assert ver["outcome"]["known_from"] == mb
    assert ver["forecasts"][1]["until"] == "2027-03-20T15:00:00+00:00"
    assert ver["last_before_outcome"]["record"] == r2

    # 3c disjunctive: M1 -> E settles Jan 10, M2 -> E settles Mar 20 -> known Jan 10 regardless of pivot order
    w_or = World(tmp_path / "scratch-disj")
    e_or = w_or.prop("Either suffices", chance=0.4)
    m1 = w_or.market("M1", "Narrower 1?", 0.18, 0.22)
    m2 = w_or.market("M2", "Narrower 2?", 0.20, 0.24)
    w_or.link(m2, "implies", e_or)  # linked first so LP sees M2 before M1
    w_or.link(m1, "implies", e_or)
    w_or.link("portfolio", "watches", e_or)
    r_or1 = _reply_at(monkeypatch, w_or.store, "2027-01-05T15:00:00+00:00", 1)
    _reply_at(monkeypatch, w_or.store, "2027-02-01T15:00:00+00:00", 2)
    _settles(w_or.store, m1, 1, "2027-01-10T15:00:00+00:00", "2027-01-11T09:00:00+00:00")
    _settles(w_or.store, m2, 1, "2027-03-20T15:00:00+00:00", "2027-03-21T09:00:00+00:00")
    record.record_outcomes(w_or.store, {m1: 1, m2: 1})
    [ver_or] = record.history(w_or.store)
    assert ver_or["outcome"]["known_at"] == "2027-01-10T15:00:00+00:00"
    assert ver_or["outcome"]["known_from"] == m1
    assert "brier" not in ver_or["forecasts"][1] and ver_or["last_before_outcome"]["record"] == r_or1

    # 3d: venue gives no settlement time -> retrieved_at; no settlement row in same second -> strict > scores forecast
    w_nd = World(tmp_path / "scratch-nodate")
    e_nd = w_nd.prop("Undated settlement", chance=0.35)
    m_nd = w_nd.market("MND", "Undated?", 0.33, 0.37)
    w_nd.link(e_nd, "asks", m_nd)
    w_nd.link("portfolio", "watches", e_nd)
    _reply_at(monkeypatch, w_nd.store, "2027-01-10T15:00:00+00:00", 1)
    _settles(w_nd.store, m_nd, 1, None, "2027-01-18T09:00:00+00:00")
    record.record_outcomes(w_nd.store, {m_nd: 1})
    assert record.history(w_nd.store)[0]["outcome"]["known_at"] == "2027-01-18T09:00:00+00:00"

    w_ss = World(tmp_path / "scratch-samesecond")
    e_ss = w_ss.prop("Same second", chance=0.35)
    m_ss = w_ss.market("MSS", "Same second?", 0.33, 0.37)
    w_ss.link(e_ss, "asks", m_ss)
    w_ss.link("portfolio", "watches", e_ss)
    import harness.store
    monkeypatch.setattr(harness.store, "_now", lambda: "2027-01-10T15:00:00+00:00")
    r_ss = record.reply(w_ss.store, session="s", turn=1, stopped=False)
    record.record_outcomes(w_ss.store, {m_ss: 1})
    monkeypatch.undo()
    [ver_ss] = record.history(w_ss.store)
    assert "brier" in ver_ss["forecasts"][0] and ver_ss["last_before_outcome"]["record"] == r_ss

    # 3f: unmet hitting window with no market settles nothing; with late-settling market, bounded at window_end + 1 day
    w_hit = World(tmp_path / "scratch-unmet-hitting")
    e_hit = w_hit.prop("Hitting unmet", end="2027-03-31", chance=0.25)
    m_hit = w_hit.market("MHIT", "Hit by Mar 31?", 0.23, 0.27)
    w_hit.link(e_hit, "asks", m_hit)
    w_hit.link("portfolio", "watches", e_hit)
    _reply_at(monkeypatch, w_hit.store, "2027-03-20T15:00:00+00:00", 1)
    _reply_at(monkeypatch, w_hit.store, "2027-04-01T15:00:00+00:00", 2)  # after window closed
    assert record.record_outcomes(w_hit.store, {})["recorded"] == []
    _settles(w_hit.store, m_hit, 0, "2027-04-03T12:00:00+00:00", "2027-04-03T13:00:00+00:00")
    record.record_outcomes(w_hit.store, {m_hit: 0})
    [ver_hit] = record.history(w_hit.store)
    assert ver_hit["outcome"]["known_at"] == "2027-04-01T04:00:00+00:00"
    assert ver_hit["outcome"]["known_from"] == "definition"
    assert "brier" not in ver_hit["forecasts"][1]


def test_an_open_reply_ends_the_earlier_forecast_and_a_rewind_reports_each_spell_whole(tmp_path, monkeypatch):
    """A reply that leaves a probability open ends the standing forecast without carrying it forward (3a), and a
    rewind in trace.jsonl ends the replaced turn's spell and opens a second spell on the restored turn."""
    from breakdown import World
    from harness.trace import Trace

    # 3a: priced -> open -> priced, then priced -> open -> resolution
    w = World(tmp_path / "scratch-open")
    e = w.prop("Open middle", chance=0.30)
    m = w.market("M", "Will it happen?", 0.28, 0.32)
    b = w.prop("Unpriced driver")
    w.link(e, "asks", m, taken=stated(False, "thin book"))
    w.link("portfolio", "watches", e)
    _reply_at(monkeypatch, w.store, "2027-01-10T09:00:00+00:00", 1)
    w.chances(e, {"given": [b], "given_not": [], "value": 0.6}, keep=False)  # leaves e open (p is None)
    _reply_at(monkeypatch, w.store, "2027-01-10T10:00:00+00:00", 2)
    w.chances(e, {"given": [], "given_not": [], "value": 0.55}, keep=False)
    r3 = _reply_at(monkeypatch, w.store, "2027-01-10T10:30:00+00:00", 3)
    w.chances(e, {"given": [b], "given_not": [], "value": 0.6}, keep=False)  # open again before resolution
    _reply_at(monkeypatch, w.store, "2027-01-10T11:00:00+00:00", 4)
    _settles(w.store, m, 1, "2027-01-10T12:00:00+00:00", "2027-01-10T13:00:00+00:00")
    record.record_outcomes(w.store, {m: 1})
    [ver] = record.history(w.store)
    f1, f2, f3, f4 = ver["forecasts"]
    assert f1["until"] == "2027-01-10T10:00:00+00:00"
    assert "until" not in f2 and "brier" not in f2
    assert f3["until"] == "2027-01-10T11:00:00+00:00" and r3 == f3["record"]
    assert "until" not in f4 and ver["last_before_outcome"] is None

    # Rewind restoring Turn 1 across a second spell (and ignoring scratch-store rewinds)
    w_rw = World(tmp_path / "scratch-rewind")
    e_rw = w_rw.prop("Rewound event", chance=0.25)
    m_rw = w_rw.market("MRW", "Rewound?", 0.23, 0.27)
    w_rw.link(e_rw, "asks", m_rw, taken=stated(False, "thin"))
    w_rw.link("portfolio", "watches", e_rw)
    r_t1 = _reply_at(monkeypatch, w_rw.store, "2027-01-10T09:00:00+00:00", 1)
    w_rw.chances(e_rw, {"given": [], "given_not": [], "value": 0.80}, keep=False)
    _reply_at(monkeypatch, w_rw.store, "2027-01-10T10:00:00+00:00", 2)
    Trace(w_rw.store.trace_path).append({"type": "rewind", "session": "s", "turn": 1, "reason": "edit",
                                         "store": "other-scratch", "at": "2027-01-10T10:05:00+00:00"})
    Trace(w_rw.store.trace_path).append({"type": "rewind", "session": "s", "turn": 2, "reason": "retry",
                                         "store": w_rw.store.dir.name, "at": "2027-01-10T10:15:00+00:00"})
    _settles(w_rw.store, m_rw, 0, "2027-01-10T12:00:00+00:00", "2027-01-10T13:00:00+00:00")
    record.record_outcomes(w_rw.store, {m_rw: 0})
    [ver_rw] = record.history(w_rw.store)
    ft1, ft2 = ver_rw["forecasts"]
    assert ft2["spells"] == [{"from": "2027-01-10T10:00:00+00:00", "until": "2027-01-10T10:15:00+00:00"}]
    assert ft2["until"] == "2027-01-10T10:15:00+00:00"
    assert ft1["spells"] == [{"from": "2027-01-10T09:00:00+00:00", "until": "2027-01-10T10:00:00+00:00"},
                             {"from": "2027-01-10T10:15:00+00:00", "until": "2027-01-10T12:00:00+00:00"}]
    assert ft1["until"] == "2027-01-10T12:00:00+00:00"
    assert ver_rw["last_before_outcome"]["record"] == r_t1

    # Same-second reply -> rewind -> retry preserves causal order even at 1-second timestamp precision.
    w_ss = World(tmp_path / "scratch-rewind-samesec")
    e_ss = w_ss.prop("Same-sec rewind", chance=0.20)
    m_ss = w_ss.market("MRWSS", "Same-sec?", 0.18, 0.22)
    w_ss.link(e_ss, "asks", m_ss, taken=stated(False, "thin"))
    w_ss.link("portfolio", "watches", e_ss)
    ts = "2027-01-10T10:00:00+00:00"
    _reply_at(monkeypatch, w_ss.store, ts, 1)
    w_ss.chances(e_ss, {"given": [], "given_not": [], "value": 0.90}, keep=False)
    _reply_at(monkeypatch, w_ss.store, ts, 2)
    Trace(w_ss.store.trace_path).append({"type": "rewind", "session": "s", "turn": 2, "reason": "retry",
                                         "store": w_ss.store.dir.name, "at": ts})
    w_ss.chances(e_ss, {"given": [], "given_not": [], "value": 0.35}, keep=False)
    r_retry = _reply_at(monkeypatch, w_ss.store, ts, 2)
    _settles(w_ss.store, m_ss, 1, "2027-01-10T12:00:00+00:00", "2027-01-10T13:00:00+00:00")
    record.record_outcomes(w_ss.store, {m_ss: 1})
    [ver_ss] = record.history(w_ss.store)
    assert ver_ss["last_before_outcome"]["record"] == r_retry
    assert ver_ss["forecasts"][1]["until"] == ts and ver_ss["forecasts"][2]["until"] == "2027-01-10T12:00:00+00:00"


def test_disputed_and_note_answered_claims_stand_aside_and_partially_disputed_groups_keep_individual_claims(tmp_path):
    """A disputed claim, even when answered by a note on its Judgment, carries nothing and causes no contradiction
    beside a sound path (4a, 4c); and when one member of a `way`, `and`, or `or` group is disputed, the group's joint
    exhaustiveness is dropped while each sound sibling keeps its individual implication (4d)."""
    from breakdown import World

    # 4a & 4c: disputed asks answered by a note (M1=0) beside sound narrower N implies E (M2=1) -> E=1, no cannot_hold
    answers = {("market_forward", "E", "Broader or different?"): 0.9,
               ("market_converse", "E", "Broader or different?"): 0.15}
    w = World(tmp_path / "scratch-beside", answers=answers)
    e, n = w.prop("E", chance=0.3), w.prop("N")
    m1 = w.market("M1", "Broader or different?", 0.40, 0.45)
    m2 = w.market("M2", "Narrower N?", 0.18, 0.22)
    disputed_asks = w.link(e, "asks", m1, taken=stated(False, "different"))
    w.link(n, "asks", m2)
    w.link(n, "implies", e)
    jdg = record.ledger.link_states(w.store)[disputed_asks]["checks"]["converse"]["judgment"]
    w.store.note("The difference shouldn't arise.", [jdg])
    assert record.ledger.link_states(w.store)[disputed_asks]["state"] == "answered by a note"
    w.link("portfolio", "watches", e)
    record.reply(w.store, session="s", turn=1, stopped=False)
    res = record.record_outcomes(w.store, {m1: 0, m2: 1})
    assert res["cannot_hold"] == [] and [(r["proposition"], r["outcome"]) for r in res["recorded"]] == [(e, 1)]

    # 4d: partially-disputed No-side way group (sound below-20 + contradicted hitting-reaches-20)
    for m_val, expected in ((0, []), (1, [0])):
        wg = World(tmp_path / f"scratch-way-group-{m_val}")
        ev_p = wg.prop("At least 25", ">=", 25.0, kind="level_at_date", start=None, chance=0.3)
        below = wg.prop("Below 20", "<", 20.0, kind="level_at_date", start=None)
        reaches = wg.prop("Reaches 20", ">=", 20.0)  # hitting: contradicts No-side exclusion
        m_lt20 = wg.market("MLT20", "Below 20 on date?", 0.30, 0.35)
        wg.link(below, "asks", m_lt20)
        wg.link(ev_p, "way", below, side="no")
        wg.link(ev_p, "way", reaches, side="no")
        wg.link("portfolio", "watches", ev_p)
        record.reply(wg.store, session="s", turn=1, stopped=False)
        got = [r["outcome"] for r in record.record_outcomes(wg.store, {m_lt20: m_val})["recorded"]
               if r["proposition"] == ev_p]
        assert got == expected

    # Partially-disputed `and` and `or` composites
    for rel, m_val, expected in (("and", 1, []), ("and", 0, [0]), ("or", 0, []), ("or", 1, [1])):
        wc = World(tmp_path / f"scratch-{rel}-{m_val}")
        comp = wc.prop("Composite", ">=", 25.0, kind="level_at_date", start=None, chance=0.3)
        sound_part = wc.prop("Sound part", ">=", 20.0 if rel == "and" else 30.0, kind="level_at_date", start=None)
        bad_part = wc.prop("Bad part", ">=", 30.0 if rel == "and" else 20.0, kind="level_at_date", start=None)
        mp = wc.market("MP", "Sound part?", 0.30, 0.35)
        wc.link(sound_part, "asks", mp)
        wc.link(comp, rel, sound_part)
        wc.link(comp, rel, bad_part)  # fields contradict
        wc.link("portfolio", "watches", comp)
        record.reply(wc.store, session="s", turn=1, stopped=False)
        got = [r["outcome"] for r in record.record_outcomes(wc.store, {mp: m_val})["recorded"]
               if r["proposition"] == comp]
        assert got == expected


def test_an_unread_claim_asks_its_kept_question_at_outcome_time_and_carries_nothing_while_unread(tmp_path):
    """A claim unread when its record was made keeps its question with the record: while no judge answers it, it
    carries no settlement; once a judge answers it, that reading decides whether it carries or stands disputed (4b)."""
    from breakdown import World

    for final_answer, expected in ((0.15, []), (0.92, [1])):
        answers = {("market_forward", "S", "Will S happen?"): None, ("market_converse", "S", "Will S happen?"): None}
        w = World(tmp_path / f"scratch-unread-{final_answer}", answers=answers)
        s = w.prop("S", chance=0.4)
        m = w.market("MS", "Will S happen?", 0.38, 0.42)
        w.link(s, "asks", m)
        watching = w.link("portfolio", "watches", s)
        record.reply(w.store, session="s", turn=1, stopped=False)
        # Remove the event in Turn 2 so only the frozen question in written["checks"] holds it as it stood.
        w.store.remove(watching)
        w.store.remove(s)
        record.reply(w.store, session="s", turn=2, stopped=False)

        # Still unread (judge leaves it unanswered): carries nothing rather than committing an unverified outcome.
        assert record.record_outcomes(w.store, {m: 1})["recorded"] == []

        # Judge now answers the kept question: disputed (0.15) carries nothing; allowed (0.92) records outcome 1.
        w.judge.answers.update({("market_forward", "S", "Will S happen?"): final_answer,
                                ("market_converse", "S", "Will S happen?"): final_answer})
        got = [r["outcome"] for r in record.record_outcomes(w.store, {m: 1})["recorded"] if r["proposition"] == s]
        assert got == expected

    # A record whose kept checks had "not yet checked" with no `question` (e.g. link_states(store) without questions)
    # falls back to _judged over the frozen evidence rather than holding the claim back forever.
    for final_answer, expected in ((0.15, []), (0.92, [1])):
        answers = {("market_forward", "S", "Will S happen?"): None, ("market_converse", "S", "Will S happen?"): None}
        w_noq = World(tmp_path / f"scratch-unread-noq-{final_answer}", answers=answers)
        s = w_noq.prop("S", chance=0.4)
        m = w_noq.market("MS", "Will S happen?", 0.38, 0.42)
        w_noq.link(s, "asks", m)
        w_noq.link("portfolio", "watches", s)
        orig_ls = record.link_states
        record.link_states = lambda st, questions=False: orig_ls(st, questions=False)
        try:
            record.reply(w_noq.store, session="s", turn=1, stopped=False)
        finally:
            record.link_states = orig_ls
        assert "question" not in next(iter(w_noq.store.records()[0]["written"]["checks"].values()))
        assert record.record_outcomes(w_noq.store, {m: 1})["recorded"] == []
        w_noq.judge.answers.update({("market_forward", "S", "Will S happen?"): final_answer,
                                    ("market_converse", "S", "Will S happen?"): final_answer})
        got = [r["outcome"] for r in record.record_outcomes(w_noq.store, {m: 1})["recorded"] if r["proposition"] == s]
        assert got == expected


def test_older_records_without_kept_checks_match_full_proposition_evidence_and_ordered_ends(tmp_path, monkeypatch):
    """For a record made before written['checks'], _judged_by compares the proposition's full evidence
    (evidence.stated_proposition) and the ordered ends of the question, across asks, way, and market-end implies."""
    from breakdown import World

    # 1. Threshold edited under the same title/text/window_end does not match the earlier Judgment.
    w = World(tmp_path / "scratch-legacy-prop", default=0.9)
    a = w.prop("Tariff rise", ">=", 20.0, kind="level_at_date", start=None, chance=0.3)
    b = w.prop("Broad raise", end="2027-03-31")
    mb = w.market("MB", "Broad raise?", 0.45, 0.50)
    w.link(b, "asks", mb)
    w.link(a, "implies", b)  # caches allowing Judgment (0.9) for level >= 20
    # Edit `a`'s threshold to >= 10 under the exact same title, text, and window_end, with no judge reachable now.
    defn = {k: v for k, v in w.store.stated_of(a).items() if k in record.DEFINITION}
    w.judge.default = lambda *args, **kwargs: None  # new threshold's check stays unanswered
    w.store.edit(a, "definition", {**defn, "value": 10.0})
    w.link("portfolio", "watches", a)
    monkeypatch.setattr(record, "link_states", _unread)
    record.reply(w.store, session="s", turn=1, stopped=False)
    monkeypatch.undo()
    # The old >= 20 Judgment does not certify the >= 10 claim while unread; once the judge disputes >= 10 (0.1), still [].
    assert record.record_outcomes(w.store, {mb: 0})["recorded"] == []
    w.judge.default = lambda *args, **kwargs: 0.1
    assert record.record_outcomes(w.store, {mb: 0})["recorded"] == []

    # 2. B implies A's allowing Judgment does not certify A implies B, and legacy disputed way / market-implies are caught.
    answers = {("implication", "Q", "P"): 0.95, ("implication", "P", "Q"): 0.12,
               ("exclusion", "Late", "Event"): 0.15, ("market_forward", "Event", "Wrong market?"): 0.10}
    w2 = World(tmp_path / "scratch-legacy-order", answers=answers)
    p, q = w2.prop("P", chance=0.3), w2.prop("Q")
    mq = w2.market("MQ", "Will Q happen?", 0.40, 0.45)
    w2.link(q, "asks", mq)
    rev = w2.link(q, "implies", p)
    w2.store.remove(rev)
    w2.link(p, "implies", q)  # caches 0.12 for (P, Q) beside 0.95 for (Q, P)
    ev_p, late = w2.prop("Event", chance=0.4), w2.prop("Late")
    mlate = w2.market("MLATE", "Late?", 0.30, 0.35)
    mwrong = w2.market("MWRONG", "Wrong market?", 0.20, 0.25)
    w2.link(late, "asks", mlate)
    w2.link(ev_p, "way", late, side="no")
    w2.link(ev_p, "implies", mwrong)
    w2.link("portfolio", "watches", p)
    w2.link("portfolio", "watches", ev_p)
    monkeypatch.setattr(record, "link_states", _unread)
    record.reply(w2.store, session="s", turn=1, stopped=False)
    monkeypatch.undo()
    assert record.record_outcomes(w2.store, {mq: 0, mlate: 1, mwrong: 0})["recorded"] == []


def test_dated_resolution_evidence_records_unsettled_outcomes_and_replaces_start_of_day_definition_bound(tmp_path, monkeypatch):
    """Dated resolution evidence records outcomes that markets do not decide, replaces the start-of-day definition
    placeholder (`known_from == 'definition'`) with the announcement's own timestamp when dated evidence arrives, and
    caps an unmet hitting window at the close of its window."""
    from breakdown import World

    w = World(tmp_path / "scratch-dated-res", default=0.9)
    store = w.store

    # 1. Event with no market: scheduled decision on 2026-12-09 (announced at 14:00 EST = 19:00:00Z).
    p_unmarketed = store.add("Proposition", {
        "title": "ECB cuts deposit rate on Dec 9",
        "text": "ECB cuts on Dec 9, 2026",
        "comparison": "occurs",
        "window_end": "2026-12-09",
        "kind": "scheduled_decision",
        "resolution_source": "ECB press release",
        "chances": [stated(0.70)],
    })["id"]
    # 2. Event asked by M1 (which settles late on Dec 11), also a scheduled decision on 2026-12-09.
    p_marketed = store.add("Proposition", {
        "title": "FOMC cuts on Dec 9",
        "text": "FOMC cuts on Dec 9, 2026",
        "comparison": "occurs",
        "window_end": "2026-12-09",
        "kind": "scheduled_decision",
        "resolution_source": "Federal Reserve statement",
    })["id"]
    m_fomc = w.market("MFOMC", "Will FOMC cut on Dec 9?", 0.65, 0.70)
    w.link(p_marketed, "asks", m_fomc)
    # 3. Unmet hitting window closing 2026-12-08 (midnight NY end = 2026-12-09T05:00:00+00:00).
    p_hit = store.add("Proposition", {
        "title": "Emergency cut by Dec 8",
        "text": "Emergency cut through Dec 8, 2026",
        "comparison": "occurs",
        "window_start": ASOF,
        "window_end": "2026-12-08",
        "kind": "hitting",
        "resolution_source": "Federal Reserve",
        "chances": [stated(0.15)],
    })["id"]
    for pid in (p_unmarketed, p_marketed, p_hit):
        w.link("portfolio", "watches", pid)

    # Reply 1 at 10:00 EST on Dec 9 (15:00Z — after 00:00 EST start-of-day, BEFORE 14:00 EST announcement!)
    r1 = _reply_at(monkeypatch, store, "2026-12-09T15:00:00+00:00", 1)

    # Reply 2 at 15:00 EST on Dec 9 (20:00Z — AFTER 14:00 EST announcement!)
    store.edit(p_unmarketed, "chances", [stated(0.99)])
    _reply_at(monkeypatch, store, "2026-12-09T20:00:00+00:00", 2)

    # Market settles late on Dec 11: before resolver evidence, p_marketed uses start-of-day (05:00Z), dropping r1!
    _settles(store, m_fomc, 1, "2026-12-11T12:00:00+00:00", "2026-12-11T12:05:00+00:00")
    record.record_outcomes(store, {m_fomc: {"result": 1, "at": "2026-12-11T12:00:00+00:00"}})
    hist_before = {h["proposition"]: h for h in record.history(store)}
    assert hist_before[p_marketed]["outcome"]["known_at"] == "2026-12-09T05:00:00+00:00"
    assert hist_before[p_marketed]["outcome"]["known_from"] == "definition"
    assert hist_before[p_marketed]["last_before_outcome"] is None

    # Now record dated resolution evidence at 19:00:00Z (14:00 EST) for both Dec 9 decisions, and Dec 12 recap for p_hit=0
    defs = {pid: record.digest({k: v for k, v in store.stated_of(pid).items() if k in record.DEFINITION and v is not None})
            for pid in (p_unmarketed, p_marketed, p_hit)}
    res = record.record_resolutions(store, [
        {"proposition": p_unmarketed, "definition": defs[p_unmarketed], "stated": hist_before[p_unmarketed]["stated"],
         "status": "resolved", "outcome": 1, "known_at": "2026-12-09T19:00:00+00:00",
         "known_from": "https://ecb.europa.eu/press/20261209", "intraday": True, "p_true": 0.98, "p_false": 0.01,
         "model": "fixture"},
        {"proposition": p_marketed, "definition": defs[p_marketed], "stated": hist_before[p_marketed]["stated"],
         "status": "resolved", "outcome": 1, "known_at": "2026-12-09T19:00:00+00:00",
         "known_from": "https://federalreserve.gov/monetary20261209a.htm", "intraday": True,
         "p_true": 0.99, "p_false": 0.01, "model": "fixture"},
        {"proposition": p_hit, "definition": defs[p_hit], "stated": hist_before[p_hit]["stated"],
         "status": "resolved", "outcome": 0, "known_at": "2026-12-12T12:00:00+00:00",
         "known_from": "https://reuters.com/recap", "intraday": True, "p_true": 0.01, "p_false": 0.97,
         "model": "fixture"},
    ])
    assert [x["proposition"] for x in res["recorded"]] == [p_unmarketed, p_hit]
    assert [x["proposition"] for x in res["refined_timing"]] == [p_marketed]

    hist_after = {h["proposition"]: h for h in record.history(store)}
    # Both Dec 9 scheduled decisions now cut off at 19:00:00Z, scoring the 15:00Z morning forecast and excluding 20:00Z!
    for pid in (p_unmarketed, p_marketed):
        assert hist_after[pid]["outcome"]["known_at"] == "2026-12-09T19:00:00+00:00"
        assert hist_after[pid]["last_before_outcome"]["record"] == r1
        assert "brier" in hist_after[pid]["forecasts"][0]
        assert "brier" not in hist_after[pid]["forecasts"][1]
    # Unmet hitting window (ended 2026-12-08) is capped at 2026-12-09T05:00:00+00:00 despite Dec 12 recap article
    assert hist_after[p_hit]["outcome"]["known_at"] == "2026-12-09T05:00:00+00:00"
    assert hist_after[p_hit]["last_before_outcome"] is None


