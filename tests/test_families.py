"""Families that hold families (MATH.md 3.3; TOOLS.md 5): every family names the family that holds it, with its title
and how many open families it holds, in a search's rows and in get; a family that holds families is read whole at its
level, a large one as its count and span. The venues run on the recorded routes of test_markets; nothing touches the
network."""

from __future__ import annotations

import json

import pytest

from test_markets import KS_EVENT, KS_MARKET, PM_EVENT, PM_MARKET, SERIES_EVENTS, adapter, flat, polymarket_route, proposition

SERIES = {"family": "family:kalshi:series:KXFED", "title": "Fed funds rate", "open_families": 2}


def test_a_searchs_family_names_the_family_that_holds_it_with_how_many_open_families_it_holds(world):
    found = world.search_markets("Fed December", proposition(world.store)["id"])
    kalshi = next(f for f in found["families"] if f["venue"] == "kalshi")
    assert kalshi["within"] == SERIES
    assert "within" not in next(f for f in found["families"] if f["venue"] == "polymarket")  # an event in no series


def test_a_family_read_whole_names_the_family_that_holds_it(world):
    found = world.read_family("family:kalshi:KXFED-26DEC")
    assert found["within"] == SERIES
    market = world.read_market("market:kalshi:KXFED-26DEC-T4.50")
    assert market["family"]["within"] == {"id": "family:kalshi:series:KXFED", "title": "Fed funds rate"}


def test_a_family_that_holds_families_is_read_whole_at_its_level(world):
    found = world.read_family("family:kalshi:series:KXFED")  # series, event, market: three levels from the top
    assert (found["title"], found["open_families"]) == ("Fed funds rate", 2)
    assert [f["family"] for f in found["families"]] == ["family:kalshi:KXFED-26DEC", "family:kalshi:KXFED-27JAN"]
    rows = [flat({"columns": found["columns"], "markets": f["markets"]})[0] for f in found["families"]]
    assert [r["id"] for r in rows] == ["market:kalshi:KXFED-26DEC-T4.50", "market:kalshi:KXFED-27JAN-T4.50"]
    assert [r["closes"] for r in rows] == ["2026-12-09", "2027-01-27"] and rows[0]["price"] == pytest.approx(0.335)
    assert world.store.db.execute("SELECT count(*) FROM data WHERE kind IN ('market', 'book')").fetchone()[0] == 0


def test_a_family_of_more_families_than_a_list_holds_names_each_one_to_read_whole(world, monkeypatch):
    """Past twelve families, each family in it comes by its id, title, close and how many open markets it holds,
    without its markets or their books, so every one is reachable with get."""
    events = [({**KS_EVENT, "event_ticker": f"KXFED-{i:02d}", "title": f"Meeting {i}"},
               {**KS_MARKET, "ticker": f"KXFED-{i:02d}-T4.50", "event_ticker": f"KXFED-{i:02d}",
                "close_time": f"2027-{(i % 12) + 1:02d}-15T18:55:00Z"}) for i in range(14)]
    monkeypatch.setitem(SERIES_EVENTS, "KXFED", events)
    found = world.read_family("family:kalshi:series:KXFED")
    assert found["open_families"] == 14 and len(found["families"]) == 14 and "span" not in found
    assert found["families"][13] == {"family": "family:kalshi:KXFED-13", "venue": "kalshi", "title": "Meeting 13",
                                     "closes": "2027-02-15", "open_markets": 1}
    assert "columns" not in found and not any("markets" in f for f in found["families"])
    assert not any(p.endswith("/orderbooks") for _, p, _, _ in world.seen["kalshi"].seen)  # no book is read
    assert world.read_family("family:kalshi:KXFED-26DEC")["within"]["open_families"] == 14


def test_an_event_and_its_series_can_share_a_ticker_and_their_ids_name_their_level(world, monkeypatch):
    same = {**KS_EVENT, "event_ticker": "KXFED", "series_ticker": "KXFED"}
    monkeypatch.setitem(SERIES_EVENTS, "KXFED", [(same, {**KS_MARKET, "event_ticker": "KXFED"})])
    found = world.read_family("family:kalshi:series:KXFED")
    assert found["families"][0]["family"] == "family:kalshi:KXFED"  # the event, at its own level


def test_a_polymarket_event_in_a_series_names_it_and_the_series_reads_whole(world):
    in_series = {**PM_EVENT, "series": [{"id": "77", "title": "US tariffs by", "recurrence": "annual"}]}

    def route(method, path, query, body):
        if path == "gamma-api.polymarket.com/series/77":
            return 200, {"id": "77", "title": "US tariffs by", "events": [{"id": "9", "closed": False},
                                                                          {"id": "10", "closed": True}]}
        if path == "gamma-api.polymarket.com/events/9":
            return 200, {**in_series, "markets": [PM_MARKET]}
        if path == "gamma-api.polymarket.com/events" and "id" in query and query["id"] == ["9"]:
            return 200, [{**in_series, "markets": [PM_MARKET]}]
        return polymarket_route(method, path, query, body)
    world.venues.clients["polymarket"] = adapter("polymarket", route)[0]
    event = world.read_family("family:polymarket:9")
    assert event["within"] == {"family": "family:polymarket:series:77", "title": "US tariffs by", "open_families": 1}
    series = world.read_family("family:polymarket:series:77")
    assert [f["family"] for f in series["families"]] == ["family:polymarket:9"] and series["open_families"] == 1
