"""The forecast record: each version's outcome from the record that made it."""

from __future__ import annotations

import pytest

from conftest import ASOF, BookFetcher, StubVenues, fixture_judge
from harness import record
from harness.derive import default_registry
from breakdown import stated

PROPOSITION = {"text": "The US raises its tariffs by the window's end", "comparison": "occurs", "window_start": ASOF,
               "window_end": "2027-03-31", "kind": "hitting"}


def test_a_forecast_whose_event_was_later_removed_still_gets_its_outcome(make_store):
    venues = StubVenues({"market:kalshi:M1": {"question": "Will the US raise tariffs by March 31?"}})
    store = make_store(registry_=default_registry(), judge=fixture_judge(), venues=venues,
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
    assert version["last_before_outcome"]["record"] == first


def _settling(make_store, **link):
    """E watched, asked by M1 through the link given; the record made once."""
    venues = StubVenues({"market:kalshi:M1": {"question": "Will the US raise tariffs by March 31?"}})
    store = make_store(registry_=default_registry(), judge=fixture_judge(), venues=venues,
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
    store = make_store(registry_=default_registry(), judge=fixture_judge(), venues=venues,
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
