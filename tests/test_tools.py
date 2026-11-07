"""The fifteen tools: their signatures, their contracts, and every refusal TOOLS.md lists, each an error
that writes nothing; exposure; the ledger's order; check's items; and what a store records. The venues run on fake openers and the judge is the fixture; nothing touches the network."""

from __future__ import annotations

import pytest

from conftest import snapshot
from harness.derive import default_registry
from harness.ledger import channel_reach, check, holdings, ledger, records
from harness.tools import SPECS, Tools, signature
from harness.trace import Trace
from harness.web import Web
from harness.world import World
from massive_data import ASOF
from test_impact import holding_world, world as impact_world
from test_markets import Opener, fetch_route

SIGNATURES = ["add(type, fields)", "link(from_id, relation, to_id, fields)", "note(text, about_ids)",
             "edit(id, field, value, source_id)", "remove(id)", "search_markets(venue, endpoint, params, proposition_id)",
             "search_web(text, params, proposition_id)", "fetch_source(url)", "search_company(ticker, kind, params)",
             "window_returns(start, end)", "get(id, text)",
             "query(type, where)", "neighbors(id, relation, direction)", "ledger()", "check()", "exposure()"]


def test_fifteen_tools_with_their_signatures():
    assert sorted(signature(n) for n in SPECS) == sorted(SIGNATURES)


# ---------------------------------------------------------------- contracts and refusals


def proposition(tools, title="T", **fields):
    return tools.add("Proposition", {"title": title, "text": f"{title}: the proposition", "relation": "occurs",
                                     "window_end": "2027-03-31", "kind": "hitting", **fields})["id"]


def page(tools, name, text):
    tools.world.web = Web(key="test", opener=Opener(fetch_route(text)))
    sid = tools.fetch_source(f"https://example.org/{name}")["id"]
    tools.edit(sid, "dated", "2026-11-01", sid)
    return sid


@pytest.fixture
def tools(world):  # noqa: F811
    world.today = lambda: ASOF
    t = Tools(world)
    t.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "CASH", "dollars": 1000.0}]})
    return t


def refused(tools, name, input, match):
    """The call returns an error naming the reason, and every table is as it was."""
    before = snapshot(tools.store)
    ok, out = tools.call(name, input)
    assert not ok and set(out) == {"error", "dependents"}, out
    assert match in out["error"], out["error"]
    assert snapshot(tools.store) == before, f"{name} wrote on refusal"
    return out


def test_add_returns_the_object_and_refuses_a_missing_field_or_source(tools):
    ok, got = tools.call("add", {"type": "Proposition", "fields": {
        "title": "T", "text": "The rate reaches 25%", "relation": "occurs", "window_end": "2027-03-31",
        "kind": "hitting"}})
    assert ok and got["id"].startswith("prop:") and got["stated"]["title"] == "T" and "derived" in got
    refused(tools, "add", {"type": "Proposition", "fields": {"title": "U", "relation": "occurs",
                                                             "window_end": "2027-03-31", "kind": "hitting"}},
            "Proposition requires text")
    refused(tools, "add", {"type": "Metric", "fields": {"id": "wti", "name": "the WTI price", "unit": "USD", "current_value": 70}},
            "is a fact about the world")
    refused(tools, "add", {"type": "Metric", "fields": {"id": "wti", "name": "the WTI price", "unit": "USD",
                                                        "current_value": {"value": 70, "source_id": "src:99"}}},
            "src:99 is not a fetched page")
    refused(tools, "add", {"type": "Market", "fields": {"venue": "polymarket", "id": "1"}}, "comes from its venue")


def test_link_returns_the_link_with_its_derived_fields_and_refuses_what_breaks_a_rule(tools):
    t = proposition(tools)
    ok, got = tools.call("link", {"from_id": "portfolio", "relation": "watches", "to_id": t})
    assert ok and got["relation"] == "watches" and "derived" in got
    sid = page(tools, "rate", "The Budget Lab expects the rate to rise to about 24% by March.")
    refused(tools, "link", {"from_id": t, "relation": "evidence", "to_id": sid,
                            "fields": {"passages": ["the rate to rise to 25%"]}}, "not in " + sid + " word for word")
    refused(tools, "link", {"from_id": t, "relation": "evidence", "to_id": sid}, "evidence requires passages")
    late = proposition(tools, "Late", window_end="2027-06-30")
    refused(tools, "link", {"from_id": "portfolio", "relation": "watches", "to_id": late}, "past the horizon")
    b, c = proposition(tools, "B"), proposition(tools, "C")
    assert tools.call("link", {"from_id": t, "relation": "then", "to_id": b, "fields": {"step": 1}})[0]
    out = refused(tools, "link", {"from_id": t, "relation": "then", "to_id": c, "fields": {"step": 1}},
                  "one link of each step")
    assert out["dependents"]
    refused(tools, "link", {"from_id": t, "relation": "implies", "to_id": "prop:999"}, "not in the store")


def test_a_consequence_that_would_set_a_channel_through_a_second_metric_is_refused_naming_the_first(tools):
    store = tools.store
    tools.add("Channel", {"name": "crude oil", "proxy_ticker": "USO", "order": 1})
    oil = store.query("Channel")[0]["id"]
    for mid in ("wti", "brent"):
        sid = page(tools, mid, f"The {mid} level is 70.")
        tools.add("Metric", {"id": mid, "name": f"the {mid} price", "unit": "USD", "current_value": {"value": 70.0, "source_id": sid}})
        tools.link(f"metric:{mid}", "tracks", oil, {"conversion": "price"})
    e = proposition(tools, "E")
    tools.link(e, "consequence", "metric:wti", {"value": 5.0, "meaning": "gap"})
    out = refused(tools, "link", {"from_id": e, "relation": "consequence", "to_id": "metric:brent",
                                  "fields": {"value": 5.0, "meaning": "gap"}}, "metric:wti already sets it")
    assert out["dependents"] == ["metric:wti"]
    refused(tools, "link", {"from_id": e, "relation": "consequence", "to_id": "metric:brent",
                            "fields": {"value": 5.0, "meaning": "move"}}, "meaning is one of")
    refused(tools, "link", {"from_id": e, "relation": "consequence", "to_id": "metric:brent",
                            "fields": {"value": 80.0, "meaning": "level_if_not"}}, "not traded")


def test_a_stated_chance_or_derivation_that_breaks_a_rule_is_refused(tools):
    e, b = proposition(tools, "E"), proposition(tools, "B")
    refused(tools, "edit", {"id": e, "field": "chance", "value": 1.5}, "a chance, from 0 to 1")
    refused(tools, "edit", {"id": e, "field": "derivation", "value": {
        "reads": [b], "chances": [{"given": [b], "value": 0.5}]}}, "no link says")
    refused(tools, "edit", {"id": e, "field": "derivation", "value": {"reads": [b]}}, "derivation is")
    refused(tools, "edit", {"id": e, "field": "derivation", "value": {
        "reads": [b], "chances": [{"given": [b], "value": 1.2}]}}, "a chance, from 0 to 1")
    tools.link(e, "implies", b)
    ok, got = tools.call("edit", {"id": e, "field": "derivation", "value": {
        "reads": [b], "chances": [{"given": [b], "value": 0.5}]}})
    assert ok and got["stated"]["derivation"]["chances"][0]["of"] == e
    refused(tools, "edit", {"id": e, "field": "chance", "value": 0.2}, "not both")
    out = refused(tools, "remove", {"id": b}, "edit or remove those first")
    assert out["dependents"] == [f"{e}.derivation"]


def test_note_edit_and_remove(tools):
    t = proposition(tools)
    ok, n = tools.call("note", {"text": "The rate is the question.", "about_ids": [t]})
    assert ok and n["links"][0]["relation"] == "about" and n["links"][0]["other"] == t
    refused(tools, "note", {"text": "x", "about_ids": ["prop:999"]}, "not in the store")
    ok, edited = tools.call("edit", {"id": t, "field": "title", "value": "T2"})
    assert ok and edited["stated"]["title"] == "T2"
    sid = page(tools, "level", "The rate is 22 percent.")
    dated = tools.call("edit", {"id": sid, "field": "dated", "value": "2026-11-02", "source_id": sid})[1]
    assert dated["dated"] == "2026-11-02" and "text" not in dated and dated["length"] == len("The rate is 22 percent.")
    tools.add("Metric", {"id": "rate", "name": "the rate", "unit": "pct", "current_value": {"value": 22.0, "source_id": sid}})
    refused(tools, "edit", {"id": "metric:rate", "field": "current_value", "value": 23.0}, "takes a source_id")
    refused(tools, "edit", {"id": t, "field": "lo", "value": 0.2}, "derived")
    out = refused(tools, "remove", {"id": sid}, "a stated fact keeps its source")
    assert out["dependents"] == ["metric:rate.current_value"]
    u = proposition(tools, "U", relation=">=", value=25.0, metric="metric:rate", kind="level_at_date")
    out = refused(tools, "remove", {"id": "metric:rate"}, "edit or remove those first")
    assert out["dependents"] == [f"{u}.metric"]
    ok, gone = tools.call("remove", {"id": n["id"]})
    assert ok and gone["removed"]["id"] == n["id"]


def test_remove_refuses_a_market_and_what_is_derived(tools):
    t = proposition(tools)
    lid = tools.link(t, "quoted_by", "market:polymarket:516710")["id"]
    out = refused(tools, "remove", {"id": "market:polymarket:516710"}, "comes from its venue")
    assert out["dependents"] == [lid]
    tools.add("Channel", {"name": "crude oil", "proxy_ticker": "USO", "order": 1})
    refused(tools, "remove", {"id": "metric:price:USO"}, "derived")
    refused(tools, "remove", {"id": "jdg:1"}, "not in the store")


def test_the_world_tools_and_their_refusals(tools):
    t = proposition(tools)
    ok, found = tools.call("search_markets", {"venue": "polymarket", "endpoint": "/public-search",
                                              "params": {"q": "tariffs"}, "proposition_id": t})
    assert ok and found["events"][0]["markets"][0]["id"] == "market:polymarket:516710"
    assert tools.get(t)["searches"][0]["params"] == {"q": "tariffs"}
    refused(tools, "search_markets", {"venue": "nyse", "endpoint": "/x"}, "the venues are")
    refused(tools, "search_markets", {"venue": "polymarket", "endpoint": "/public-search", "proposition_id": "x:1"},
            "is not a proposition")
    refused(tools, "search_web", {"text": " "}, "the query is empty")
    refused(tools, "fetch_source", {"url": "file:///etc/passwd"}, "http or https URL")
    tools.world.web = Web(key="test", opener=Opener(fetch_route("Published October 30, 2026. The rate is 22.")))
    ok, fetched = tools.call("fetch_source", {"url": "https://example.org/fresh"})
    assert ok and fetched["kept"] is False and fetched["excerpt"].startswith("Published")
    assert tools.store.db.execute("SELECT count(*) FROM source WHERE url IS NOT NULL").fetchone()[0] == 0


def test_a_propositions_searches_include_those_that_found_its_markets_and_its_pages(tools):
    """A market is usually found before the proposition it bears on is written."""
    from test_analyst import web_route
    tools.world.web = Web(key="test", opener=Opener(web_route))
    _, before = tools.call("search_markets", {"venue": "polymarket", "endpoint": "/public-search",
                                              "params": {"q": "tariffs"}})
    _, web = tools.call("search_web", {"text": "tariff rate"})
    _, fetched = tools.call("fetch_source", {"url": "https://example.org/rate"})
    t = proposition(tools)
    other = proposition(tools, "Other")
    _, own = tools.call("search_markets", {"venue": "polymarket", "endpoint": "/public-search",
                                           "params": {"q": "tariff rate"}, "proposition_id": t})
    assert [s["id"] for s in tools.get(t)["searches"]] == [own["search"]]
    lid = tools.link(other, "quoted_by", "market:polymarket:516710")["id"]
    ev = tools.link(other, "evidence", fetched["id"], {"passages": ["The effective tariff rate is 22 percent"]})["id"]
    found = tools.get(other)["searches"]
    assert [(s["id"], s["recorded"], s.get("quoted_by"), s.get("evidence")) for s in found] == [
        (before["search"], False, ["market:polymarket:516710"], None), (web["search"], False, None, [fetched["id"]]),
        (own["search"], False, ["market:polymarket:516710"], None)]
    assert tools.get(t)["searches"][0]["recorded"] is True
    tools.link("portfolio", "watches", other)
    tools.link(t, "implies", other)  # t's own search also returned other's market: one search, counted once
    counts = ledger(tools.store)["rows"][0]["searches"]
    assert (counts["market_searches"], counts["web_searches"]) == (2, 1)
    tools.call("remove", {"id": lid})
    tools.call("remove", {"id": ev})
    assert tools.get(other)["searches"] == []  # the attribution goes with the link


def test_the_reads_and_their_refusals(tools):
    t = proposition(tools)
    assert tools.call("get", {"id": t})[1]["id"] == t
    refused(tools, "get", {"id": "prop:999"}, "not in the store")
    assert [x["id"] for x in tools.call("query", {"type": "Proposition", "where": {"title": "T"}})[1]] == [t]
    refused(tools, "query", {"type": "Thing"}, "there is no type")
    refused(tools, "query", {"type": "Proposition", "where": {"colour": "red"}}, "has no field")
    tools.link("portfolio", "watches", t)
    assert tools.call("neighbors", {"id": t, "relation": "watches", "direction": "in"})[1][0]["object"]["id"] == \
        "portfolio"
    refused(tools, "neighbors", {"id": t, "direction": "sideways"}, "out, in, or both")
    refused(tools, "window_returns", {"start": "2025-04-03", "end": "2025-04-02"}, "is after end")
    refused(tools, "window_returns", {"start": "April 2", "end": "2025-04-03"}, "is a date")


def test_a_call_names_its_tool_and_parameters_and_is_written_to_the_trace(tools):
    refused(tools, "write_estimate", {"id": "prop:1", "p": 0.4}, "there is no tool")
    refused(tools, "get", {}, "id is required")
    refused(tools, "get", {"id": "portfolio", "depth": 2}, "no parameter depth")
    t = proposition(tools)
    tools.call("get", {"id": t})
    calls = [e for e in Trace(tools.store.trace_path).entries() if e["type"] == "call"]
    assert [c["call"] for c in calls] == list(range(1, len(calls) + 1))
    assert calls[-1]["tool"] == "get" and calls[-1]["input"] == {"id": t} and calls[-1]["result"]["id"] == t
    assert calls[0]["error"]["error"].startswith("there is no tool")


def test_the_calls_number_is_on_what_it_wrote(tools):
    ok, got = tools.call("add", {"type": "Proposition", "fields": {
        "title": "T", "text": "T", "relation": "occurs", "window_end": "2027-03-31", "kind": "hitting"}})
    n = tools.last_call
    row = tools.store.db.execute("SELECT created_call FROM object WHERE id = ?", (got["id"],)).fetchone()
    assert row[0] == n


def test_window_returns_are_how_the_channels_moved_over_any_dates(tmp_path):
    w = impact_world(tmp_path, registry=default_registry())
    tools = Tools(World(w.store, w.venues))
    out = tools.window_returns("2026-10-01", "2026-10-05")
    names = [c["name"] for c in out["channels"]]
    assert names[:2] == ["S&P 500", "short-term Treasuries"] and out["channels"][0]["proxy"] == "SPY"
    first, second = out["channels"][0], out["channels"][1]
    assert first["raw"] == first["orthogonalized"]  # channel 1 is its proxy's return
    beta = w.store.engine.value("orthogonalize", second["channel"]).output["coefficients"][first["channel"]]
    assert second["orthogonalized"] == pytest.approx(second["raw"] - beta * first["raw"])
    closes = out["closes"]["SPY"]
    assert first["raw"] == pytest.approx(closes["2026-10-05"] / closes["2026-10-01"] - 1)  # SPY pays no dividend here


# ---------------------------------------------------------------- the ledger's order and check's gaps


# Seven events, each moving the S&P 500 channel by x through one consequence on a metric at 100 that tracks it, the
# gap 100 x, on g = 100,000, with p each event's stated chance: D = G x.
EVENTS = {"E1": (0.5, -0.01), "E2": (0.1, -0.10), "E3": (0.3, 0.02), "E4": (0.02, -0.30), "E5": (0.6, -0.005),
          "E6": (0.4, -0.03), "E7": (0.2, 0.012)}
G = 100_000.0


def expected(p, x):
    d = G * x
    return {"D": d, "emove": 2 * p * (1 - p) * abs(d), "worst": min((1 - p) * d, -p * d)}


def test_the_ledger_ranks_by_emove_with_the_worst_order_beside_them(events):
    w, ids = events
    found = ledger(w.store)
    want = {e: expected(*EVENTS[e]) for e in EVENTS}
    by_emove = sorted(EVENTS, key=lambda e: -want[e]["emove"])
    assert [r["title"] for r in found["rows"]] == by_emove == ["E2", "E6", "E4", "E3", "E1", "E7", "E5"]
    for r in found["rows"]:
        assert r["D"] == pytest.approx(want[r["title"]]["D"]) and r["emove"] == pytest.approx(want[r["title"]]["emove"])
        assert r["coverage"] == 0.0 and r["p"] == EVENTS[r["title"]][0] and r["point"]["rule"] == "chance"
        p, x = EVENTS[r["title"]]
        [c] = r["consequences"]
        assert (c["metric"], c["figure"], c["meaning"], c["c"], c["traded"]) == \
            ("metric:spy_usd", {"value": 100 * x}, "gap", 100.0, True)
        assert c["gap"] == pytest.approx(100 * x)
        assert (c["L_yes"], c["L_no"]) == (pytest.approx(100 + (1 - p) * 100 * x), pytest.approx(100 - p * 100 * x))
        assert c["sets"][0]["channel"] == r["primary_channel"]["channel"] and "reasons" not in c
        assert r["contributions"][0]["name"] == "S&P 500" and r["primary_channel"]["name"] == "S&P 500"
        assert r["d"] == {}  # no consequence on a name's price: no idiosyncratic term
    worst = sorted(EVENTS, key=lambda e: want[e]["worst"])
    assert [ids[e] for e in worst] == found["worst"]  # every row, the most negative worst first
    assert worst == ["E4", "E2", "E6", "E3", "E1", "E7", "E5"]


def test_check_lists_exactly_what_the_work_is_missing_or_contradicts(events):
    w, ids = events
    store = w.store
    gaps = check(store)
    assert set(gaps) == {"watched_without_point", "watched_without_consequence", "assumptions_without_basis",
                         "disputed", "crossed", "outside_interval"}
    # Each event's chance and figure has no basis yet.
    assert len(gaps["assumptions_without_basis"]) == 14
    for e in ids.values():
        store.note("Our reading of the episode.", [e])
    for link in store._all("SELECT id FROM link WHERE relation = 'consequence'"):
        store.note("What the last such episode did.", [link["id"]])
    gaps = check(store)
    assert all(not v for v in gaps.values()), gaps  # nothing missing or contradicted
    bare = w.prop("Bare")
    w.link("portfolio", "watches", bare)
    w.judge.answers[("market_converse", "X", "Market A?")] = 0.3
    x = w.prop("X")
    la = w.link(x, "quoted_by", w.market("MA", "Market A?", 0.60, 0.65))
    w.link(x, "implies", ids["E4"])  # X at least 0.60, and E4, which X implies, at 0.02 below: a point outside
    gaps = check(store)
    assert [g["id"] for g in gaps["watched_without_point"]] == [bare]
    assert [g["id"] for g in gaps["watched_without_consequence"]] == [bare]
    assert [(g["link"], g["check"]) for g in gaps["disputed"]] == [(la, "market, converse")]
    assert [(g["id"], g["p"]) for g in gaps["outside_interval"]] == [(ids["E4"], 0.02)]
    assert gaps["crossed"] == [] and gaps["assumptions_without_basis"] == []


def test_a_channels_exposure_is_shown_with_its_largest_month_and_their_product_for_every_channel(tmp_path):
    w = impact_world(tmp_path, g={"SHY": 60_000.0, "IBIT": 4_000.0, "USO": 1_000.0})
    reach = channel_reach(w.store)["channels"]
    assert len(reach) == 10  # every channel, with no bar
    for c in reach:
        orth = w.store.get(c["channel"])["derived"]["orthogonalization"]["value"]
        assert c["M_c"] == orth["largest_month"] and c["g_times_M_c"] == pytest.approx(abs(c["g"]) * c["M_c"])
    assert {c["name"]: c["proxy"] for c in reach}["bitcoin"] == "IBIT"


def test_the_portfolio_holdings_and_exposure_are_facts_and_run_no_search(tmp_path):
    w = holding_world(tmp_path)
    venues = w.venues
    tools = Tools(World(w.store, venues))
    got = holdings(w.store)
    assert got["V"] == pytest.approx(9000.0 + 1000.0)
    held = {h["ticker"]: h for h in got["holdings"]}
    assert held["NVDA"]["given"] == {"dollars": 5000.0} and held["NVDA"]["dollars"] == 5000.0
    closes = w.store.get("name:NVDA")["derived"]["prices"]["value"]
    assert held["NVDA"]["session_move"] == pytest.approx(closes["price"] / closes["previous_close"] - 1)
    assert held["FUND"]["fund"]["looked_through"] is True and held["CASH"]["dollars"] == 1000.0
    names = {n["name"]: n for n in got["names"]}
    assert names["NVDA"]["e"] == pytest.approx(5000 + 4000 * 0.6) and names["NVDA"]["direct"] == 5000.0
    assert names["NVDA"]["through"] == {"FUND": pytest.approx(2400.0)} and names["XOM"]["direct"] == 0.0
    assert got["names"][0]["name"] == "NVDA"  # largest first
    ok, got = tools.call("exposure", {})
    assert ok
    months = [c["g_times_M_c"] for c in got["channels"]]
    assert len(months) == 10 and months == sorted(months, reverse=True)
    assert [p["ticker"] for p in got["proxies"]] == ["SPY", "SHY", "TLT", "UUP", "USO", "EFA", "EEM", "GLD", "HYG",
                                                     "IBIT"]
    assert all(p["session_move"] is not None for p in got["proxies"])
    assert w.store.db.execute("SELECT count(*) FROM search").fetchone()[0] == 0  # no search


def test_what_a_store_records_reads_back_from_the_store_and_its_trace(events):
    w, ids = events
    tools = Tools(World(w.store, w.venues))
    tools.session = "s1"
    tools.call("get", {"id": ids["E1"]})
    ok, n = tools.call("note", {"text": "why", "about_ids": [ids["E1"]]})
    tools.call("remove", {"id": next(link["id"] for link in w.store.links_of(ids["E7"], "consequence"))})
    got = records(w.store)
    assert got["conversations"][0]["session"] == "s1" and got["conversations"][0]["calls"] == 3
    assert [r["relation"] for r in got["removed_links"]] == ["consequence"]
    assert got["coverage"]["mean"] == 0.0 and len(got["coverage"]["by_event"]) == 7
    assert len(got["added"]["consequences"]) == 6
    assert {c["metric"] for c in got["added"]["consequences"]} == {"metric:spy_usd"}
    assert [m["id"] for m in got["added"]["metrics"]] == ["metric:us_effective_tariff_rate_pct", "metric:spy_usd"]
    chances = [a for a in got["assumptions"] if a["field"] == "chance"]
    assert len(chances) == 7 and [a["noted"] for a in chances] == [True] + [False] * 6
    assert len([a for a in got["assumptions"] if a["field"] == "value"]) == 6


def test_a_row_names_the_three_largest_shares_and_counts_them_all(events):
    from harness.ledger import MOSTLY, _by_name
    w, ids = events
    shares = [{"name": f"name:X{i}", "D": -1000.0 * (5 - i)} for i in range(5)]
    kept = _by_name(w.store, shares)
    assert MOSTLY == 3 and [k["name"] for k in kept] == ["name:X0", "name:X1", "name:X2"]
    row = ledger(w.store)["rows"][0]
    assert row["names"] == len(w.store.computation_output(row["basis"]["by_name"])["by_name"])


def test_get_reads_a_market_the_store_does_not_hold_from_its_venue_and_keeps_nothing(tools):
    before = snapshot(tools.store)
    ok, got = tools.call("get", {"id": "market:polymarket:516710"})
    assert ok and got["kept"] is False and got["question"].startswith("Will the US effective tariff rate")
    assert got["rules"].startswith("Resolves Yes if the Yale Budget Lab") and got["close_time"] == "2027-03-31"
    # The fixture's as-of, 2026-11-04, is before the read: the price is the history price at the as-of.
    assert (got["bid"], got["ask"], got["price"]) == (pytest.approx(0.41), pytest.approx(0.43), 0.42)
    assert got["price_date"] == "2026-10-01" and got["depth"] is None
    assert snapshot(tools.store)["tables"]["market"] == before["tables"]["market"] == []
    assert snapshot(tools.store)["tables"]["data"] == before["tables"]["data"]
    refused(tools, "get", {"id": "market:polymarket:999999"}, "Polymarket lists no market 999999")
