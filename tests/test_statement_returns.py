"""What a statement returns (TOOLS.md 5): what it now implies, the object or the link with its derived fields, and each
watched event it can move, as it now stands. A statement that reaches every event, as a factor's, the portfolio's and a
holding's do, returns every event's row, each the row that event's own statements end with, beside its id and title.
A removal returns the events what went reached, found before it went, as they now stand; a holding's, beside what the
portfolio now owns, as its link does. Seven watched events, each moving the market's factor."""
from __future__ import annotations

from harness import ledger
from harness.tools import Tools
from harness.world import World
from test_impact import channel


def rows_as_events_end(store) -> list[dict]:
    """Every watched event's row as each event as it now stands ends with it, beside its id and title; an event with no
    figures, its probability and range with why it has none, as the event says it."""
    out = []
    for e in ledger.watched_events(store):
        now = ledger.event_now(store, e)
        if "row" in now:
            out.append({"id": e, "title": store.stated_of(e)["title"], **now["row"]})
        else:
            read = store.get(e)["derived"]
            out.append({"id": e, "title": store.stated_of(e)["title"], "probability": read["probability"]["value"],
                        "range": read["range"]["value"], "yes": None, "no": None, "full_impact": None,
                        "volatility": None, "worst_outcome": None, "expected": None, "why": now["why"]})
    return out


def call(tools, name, args):
    ok, out = tools.call(name, args)
    assert ok, out
    return out


def test_a_factor_added_or_edited_returns_every_events_row_as_it_now_stands(events):
    w, ids = events
    tools = Tools(World(w.store, w.venues))
    out = call(tools, "edit", {"id": channel(w, "USO"), "field": "order", "value": 5})
    assert out["id"] == channel(w, "USO") and "dollar_beta" in out  # the factor as add returns it
    assert [r["id"] for r in out["rows"]] == list(ids.values())  # in the order the events were made
    assert out["rows"] == rows_as_events_end(w.store)
    assert {"probability", "range", "yes", "no", "full_impact", "volatility", "worst_outcome", "expected"} <= \
        set(out["rows"][0])
    assert "rows" not in call(tools, "edit", {"id": channel(w, "USO"), "field": "name", "value": "oil"})  # words alone
    out = call(tools, "add", {"type": "Factor", "fields": {"name": "gold", "proxy": "GLD", "order": 11}})
    assert out["rows"] == rows_as_events_end(w.store)


def test_the_portfolio_and_a_holding_return_every_row_and_what_the_portfolio_owns(events):
    w, ids = events
    tools = Tools(World(w.store, w.venues))
    out = call(tools, "edit", {"id": "portfolio", "field": "horizon_days", "value": 200})
    assert out["rows"] == rows_as_events_end(w.store) and len(out["rows"]) == len(ids)
    held = next(x for x in w.store.links_of("portfolio", "holds") if x["to"].startswith("name:"))
    out = call(tools, "edit", {"id": held["id"], "field": "dollars", "value": 90_000.0})
    assert out["portfolio"] == ledger.portfolio(w.store) and out["rows"] == rows_as_events_end(w.store)
    out = call(tools, "remove", {"id": held["id"]})
    assert out["removed"]["id"] == held["id"]
    assert out["portfolio"] == ledger.portfolio(w.store) and out["rows"] == rows_as_events_end(w.store)


def test_a_removal_returns_the_events_what_went_reached_as_they_now_stand(events):
    w, ids = events
    tools = Tools(World(w.store, w.venues))
    e1, e2, e3 = ids["E1"], ids["E2"], ids["E3"]
    (shock,) = w.store.links_of(e1, "consequence")
    out = call(tools, "remove", {"id": shock["id"]})  # the event it was on, now with no consequence
    assert out["events"] == [ledger.event_now(w.store, e1)] and out["events"][0]["figures"] is None
    r = w.prop("R", chance={"value": 0.5})
    link = call(tools, "link", {"from_id": e2, "relation": "implies", "to_id": r})
    out = call(tools, "remove", {"id": link["id"]})  # each event of the structure it was in
    assert [e["id"] for e in out["events"]] == [e2] and out["events"] == [ledger.event_now(w.store, e2)]
    (watch,) = [x for x in w.store.links_of("portfolio", "watches") if x["to"] == e3]
    assert "events" not in call(tools, "remove", {"id": watch["id"]})  # it leaves the ledger, and moves no other
    out = call(tools, "remove", {"id": channel(w, "EEM")})  # a factor reaches every event
    assert out["rows"] == rows_as_events_end(w.store) and e3 not in [x["id"] for x in out["rows"]]
    assert "events" not in out
