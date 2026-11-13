"""The fifteen tools: their signatures, their contracts, and every refusal TOOLS.md lists, each an error
that writes nothing; exposure; the ledger's order; check's items; and what a store records. The venues run on fake openers and the judge is the fixture; nothing touches the network."""

from __future__ import annotations

from datetime import date, timedelta

import math

import pytest

from conftest import snapshot
from harness.derive import default_registry
from harness.derive.impact import FOLLOWED
from harness.ledger import by_volatility as ledger_by_volatility, by_worst, channel_reach, check, holdings, ledger, records, row
from harness.tools import SPECS, Tools, signature
from harness.trace import Trace
from harness.web import Web
from harness.world import World
from massive_data import ASOF
from test_impact import holding_world, world as impact_world
from test_markets import Opener, fetch_route
from breakdown import WHY, stated

SIGNATURES = ["add(type, fields)", "link(from_id, relation, to_id, fields)", "note(text, about_ids)",
             "edit(id, field, value, source_id)", "remove(id)", "search_markets(text, proposition_id)",
             "search_web(text, params, proposition_id)", "fetch_source(url, text)", "search_company(ticker, kind, params)",
             "window_returns(start, end, names)", "get(id, text)",
             "query(type, where)", "neighbors(id, relation, direction)", "ledger(together, considering)", "check()", "exposure()"]


def test_sixteen_tools_with_their_signatures():
    assert sorted(signature(n) for n in SPECS) == sorted(SIGNATURES)


# ---------------------------------------------------------------- contracts and refusals


def proposition(tools, title="T", **fields):
    return tools.add("Proposition", {"title": title, "text": f"{title}: the proposition", "comparison": "occurs",
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
    t.add("Portfolio", {"asof": ASOF, "holdings": [{"cash": 1000.0}]})
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
        "title": "T", "text": "The rate reaches 25%", "comparison": "occurs", "window_end": "2027-03-31",
        "kind": "hitting"}})
    assert ok and got["id"].startswith("prop:") and got["stated"]["title"] == "T" and "derived" in got
    refused(tools, "add", {"type": "Proposition", "fields": {"title": "U", "comparison": "occurs",
                                                             "window_end": "2027-03-31", "kind": "hitting"}},
            "Proposition requires text")
    refused(tools, "add", {"type": "Metric", "fields": {"id": "wti", "name": "the WTI price", "unit": "USD", "kind": "price", "level": 70}},
            "is a fact about the world")
    refused(tools, "add", {"type": "Metric", "fields": {"id": "wti", "name": "the WTI price", "unit": "USD", "kind": "price",
                                                        "level": {"value": 70, "source_id": "src:99"}}},
            "src:99 is not a fetched page")
    refused(tools, "add", {"type": "Market", "fields": {"venue": "polymarket", "id": "1"}}, "comes from its venue")
    tools.store.put_data("ticker_ref", "ZZZQ", {"status": "NOT_FOUND", "message": "Ticker not found."},
                         {"url": "https://api.massive.com/v3/reference/tickers/ZZZQ"},
                         retrieved_at=f"{ASOF}T21:00:00+00:00", range=(ASOF, ASOF))  # the source lists no such ticker
    refused(tools, "add", {"type": "Factor", "fields": {"name": "a fund no one lists", "proxy": "ZZZQ",
                                                         "order": 11}}, "ZZZQ names no listed instrument")


def test_a_holding_whose_ticker_names_no_security_is_refused_by_name_and_a_mutual_funds_class_is_not(world):
    """MATH.md 3.1; TOOLS.md 5: a holding is the ticker of the security held. One that no listing's reference gives and
    the SEC lists no fund's share class under (a typo, a made-up symbol) is refused before anything is written, naming
    it; a mutual fund's share class, which the SEC lists and no listing's reference gives, is no such refusal."""
    world.today = lambda: ASOF
    tools = Tools(world)
    for ticker in ("APPL", "ZZZQX", "VFIAX"):
        tools.store.put_data("ticker_ref", ticker, {"status": "NOT_FOUND", "message": "Ticker not found."},
                             {"url": f"https://api.massive.com/v3/reference/tickers/{ticker}"},
                             retrieved_at=f"{ASOF}T21:00:00+00:00", range=(ASOF, ASOF))
    tools.store.put_data("sec_funds", "company_tickers_mf", {"status": "OK", "fields": ["cik", "seriesId", "classId", "symbol"],
                                                             "data": [[36405, "S000002839", "C000007773", "VFIAX"]]},
                         {"url": "https://www.sec.gov/files/company_tickers_mf.json"},
                         retrieved_at=f"{ASOF}T21:00:00+00:00", range=(ASOF, ASOF))
    refused(tools, "add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": [
        {"ticker": "APPL", "dollars": 5000.0}, {"cash": 5000.0}]}},
        f"APPL names no listed security or fund at {ASOF}. A holding is given by the ticker of the security or fund held.")
    refused(tools, "add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": [
        {"ticker": "appl", "dollars": 5000.0}, {"ticker": "ZZZQX", "dollars": 5000.0}]}},
        f"APPL and ZZZQX name no listed security or fund at {ASOF}")
    ok, out = tools.call("add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": [
        {"cash": 5000.0}]}})
    assert ok, out
    refused(tools, "link", {"from_id": "portfolio", "relation": "holds", "to_id": "name:APPL",
                            "fields": {"dollars": 100.0}}, f"APPL names no listed security or fund at {ASOF}")
    ok, out = tools.call("link", {"from_id": "portfolio", "relation": "holds", "to_id": "name:VFIAX",
                                  "fields": {"dollars": 100.0}})
    assert ok or "names no listed security" not in out["error"], out
    # a listing abroad by its exchange's symbol is a ticker as any other: its own market's reference says it is listed
    tools.store.put_data("ticker_ref", "ZZZZ.HK", {"status": "NOT_FOUND", "ticker": "ZZZZ.HK"}, {"url": "chart"},
                         retrieved_at=f"{ASOF}T21:00:00+00:00", range=(ASOF, ASOF))
    tools.store.put_data("ticker_ref", "0700.HK", {"status": "OK", "results": {"ticker": "0700.HK", "type": "CS",
                                                                               "market": "home"}}, {"url": "chart"},
                         retrieved_at=f"{ASOF}T21:00:00+00:00", range=(ASOF, ASOF))
    refused(tools, "link", {"from_id": "portfolio", "relation": "holds", "to_id": "name:ZZZZ.HK",
                            "fields": {"dollars": 100.0}}, f"ZZZZ.HK names no listed security or fund at {ASOF}")
    tools._held_listed(["0700.HK"], ASOF)  # not refused


def test_link_returns_the_link_with_its_derived_fields_and_refuses_what_breaks_a_rule(tools):
    t = proposition(tools)
    ok, got = tools.call("link", {"from_id": "portfolio", "relation": "watches", "to_id": t})
    assert ok and got["relation"] == "watches" and "derived" in got
    sid = page(tools, "rate", "The Budget Lab expects the rate to rise to about 24% by March.")
    refused(tools, "link", {"from_id": t, "relation": "evidence", "to_id": sid,
                            "fields": {"passages": ["the rate to rise to 25%"]}}, "not in " + sid + " word for word")
    refused(tools, "link", {"from_id": t, "relation": "evidence", "to_id": sid}, "evidence requires passages")
    late = proposition(tools, "Late", window_end="2027-06-30")
    refused(tools, "link", {"from_id": "portfolio", "relation": "watches", "to_id": late}, "after the horizon's end")
    b, c = proposition(tools, "B"), proposition(tools, "C")
    assert tools.call("link", {"from_id": t, "relation": "then", "to_id": b, "fields": {"step": 1}})[0]
    out = refused(tools, "link", {"from_id": t, "relation": "then", "to_id": c, "fields": {"step": 1}},
                  "one link of each step")
    assert out["dependents"]
    refused(tools, "link", {"from_id": t, "relation": "implies", "to_id": "prop:999"}, "not in the store")


def test_a_consequence_runs_to_a_factor_or_a_name_and_a_shock_on_it_is_stated_once(tools):
    """A consequence runs from an event to the factor or the name whose price it moves (MATH.md 1.3; ONTOLOGY.md 2.2):
    once for each, edited where it changes; a metric is no target, and a field it does not have is refused."""
    store = tools.store
    tools.add("Factor", {"name": "crude oil", "proxy": "USO", "order": 1})
    oil = store.query("Factor")[0]["id"]
    sid = page(tools, "wti", "The wti level is 70.")
    tools.add("Metric", {"id": "wti", "name": "the wti price", "unit": "USD", "kind": "price",
                         "level": {"value": 70.0, "source_id": sid}})
    e = proposition(tools, "E", baseline="no")
    tools.link(e, "consequence", oil, {"shock": stated(0.05)})
    out = refused(tools, "link", {"from_id": e, "relation": "consequence", "to_id": oil,
                                  "fields": {"shock": stated(0.06)}}, "edit it instead")
    assert out["dependents"]
    refused(tools, "link", {"from_id": e, "relation": "consequence", "to_id": "metric:wti",
                            "fields": {"shock": stated(0.05)}}, "consequence runs to Factor or Name")
    refused(tools, "link", {"from_id": e, "relation": "consequence", "to_id": oil,
                            "fields": {"shock": stated(0.05), "meaning": "gap"}}, "'meaning' is not one")


def test_a_stated_chance_that_breaks_a_rule_is_refused_and_one_that_holds_returns_its_structure(tools):
    """A chance outside 0 to 1, a list that is not one, and a chance given the proposition itself are refused; a chance
    of E given B, with nothing said of B, returns E left open, resting on B; and B stays while a chance is given it."""
    e, b = proposition(tools, "E"), proposition(tools, "B")
    refused(tools, "edit", {"id": e, "field": "chances", "value": [stated(1.5)]}, "a chance, from 0 to 1")
    refused(tools, "edit", {"id": e, "field": "chances", "value": [{"given": [b], "value": 1.2}]},
            "a chance, from 0 to 1")
    refused(tools, "edit", {"id": e, "field": "chances", "value": stated(0.5)}, "chances is a list of chances")
    refused(tools, "edit", {"id": e, "field": "chances", "value": [stated({"given": [e], "value": 0.5})]},
            "is given prop:1 itself")
    ok, got = tools.call("edit", {"id": e, "field": "chances", "value": [stated({"given": [b], "value": 0.5})]})
    assert ok and got["stated"]["chances"][0]["given"] == [b]
    o = got["propositions"][e]["left_open"]  # E rests on B, itself open: fixing B is what closes E
    assert set(got["propositions"]) == {e, b} and o["settled_by"] == [] and o["with"] == [b]
    out = refused(tools, "remove", {"id": b}, "edit or remove those first")
    assert out["dependents"] == [f"{e}.chances[0]"]


def test_a_statement_whose_structure_the_solver_cannot_read_in_time_fails_with_why_and_writes_nothing(tools, monkeypatch):
    """TOOLS.md 5: a call that cannot complete what it returns fails with its reason and leaves the store as it was. With
    the solver held to no time at all, stating a chance of E given B cannot be read: the call says so, naming how many
    propositions one group ties together, and nothing is written."""
    from harness.derive import oneform
    e, b = proposition(tools, "E"), proposition(tools, "B")
    monkeypatch.setattr(oneform, "LIMIT", 0.0)
    out = refused(tools, "edit", {"id": e, "field": "chances", "value": [stated({"given": [b], "value": 0.5})]},
                  f"{e}'s structure could not be read: its links and chances tie 2 propositions into one group of 4 "
                  "cases, more than can be read. A chance given fewer propositions makes smaller groups.")
    assert set(out["dependents"]) == {e, b}
    monkeypatch.setattr(oneform, "LIMIT", 2.0)
    ok, got = tools.call("edit", {"id": e, "field": "chances", "value": [stated({"given": [b], "value": 0.5})]})
    assert ok and got["propositions"][e]["left_open"]["with"] == [b]


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
    tools.add("Metric", {"id": "rate", "name": "the rate", "unit": "pct", "kind": "level", "level": {"value": 22.0, "source_id": sid}})
    refused(tools, "edit", {"id": "metric:rate", "field": "level", "value": 23.0}, "takes a source_id")
    refused(tools, "edit", {"id": t, "field": "range", "value": [0.2, 0.3]}, "derived")
    out = refused(tools, "remove", {"id": sid}, "a stated fact keeps its source")
    assert out["dependents"] == ["metric:rate.level"]
    u = proposition(tools, "U", comparison=">=", value=25.0, metric="metric:rate", kind="level_at_date")
    out = refused(tools, "remove", {"id": "metric:rate"}, "edit or remove those first")
    assert out["dependents"] == [f"{u}.metric"]
    ok, gone = tools.call("remove", {"id": n["id"]})
    assert ok and gone["removed"]["id"] == n["id"]


def test_remove_refuses_a_market_and_what_is_derived(tools):
    t = proposition(tools)
    lid = tools.link(t, "asks", "market:polymarket:516710")["id"]
    out = refused(tools, "remove", {"id": "market:polymarket:516710"}, "comes from its venue")
    assert out["dependents"] == [lid]
    tools.add("Factor", {"name": "crude oil", "proxy": "USO", "order": 1})
    refused(tools, "remove", {"id": "metric:price:USO"}, "derived")
    refused(tools, "remove", {"id": "jdg:1"}, "not in the store")


def test_the_world_tools_and_their_refusals(tools):
    t = proposition(tools)
    ok, found = tools.call("search_markets", {"text": "tariffs", "proposition_id": t})
    assert ok and found["families"][0]["markets"][0][0] == "market:polymarket:516710"
    assert tools.get(t)["searches"][0]["query"] == "tariffs"
    refused(tools, "search_markets", {"text": " ", "proposition_id": t}, "your own words")
    refused(tools, "search_markets", {"text": "tariffs"},
            "search_markets(text, proposition_id): proposition_id is required: the proposition the markets would bear "
            "on; the search is recorded on it")
    refused(tools, "search_markets", {"text": "tariffs", "proposition_id": "x:1"}, "is not a proposition")
    refused(tools, "search_web", {"text": " "}, "the query is empty")
    refused(tools, "fetch_source", {"url": "file:///etc/passwd"}, "http or https URL")
    tools.world.web = Web(key="test", opener=Opener(fetch_route("Published October 30, 2026. The rate is 22.")))
    ok, fetched = tools.call("fetch_source", {"url": "https://example.org/fresh"})
    assert ok and fetched["kept"] is False and fetched["excerpt"].startswith("Published")
    assert tools.store.db.execute("SELECT count(*) FROM source WHERE url IS NOT NULL").fetchone()[0] == 0


def test_a_propositions_searches_include_those_that_found_its_markets_and_its_pages(tools):
    """A proposition's searches are those made for it, and those made for another that found the market it links or the
    page it holds as evidence."""
    from test_analyst import web_route
    tools.world.web = Web(key="test", opener=Opener(web_route))
    first = proposition(tools, "First")
    _, before = tools.call("search_markets", {"text": "tariffs", "proposition_id": first})
    _, web = tools.call("search_web", {"text": "tariff rate"})
    _, fetched = tools.call("fetch_source", {"url": "https://example.org/rate"})
    t = proposition(tools)
    other = proposition(tools, "Other")
    _, own = tools.call("search_markets", {"text": "tariff rate", "proposition_id": t})
    assert [s["id"] for s in tools.get(t)["searches"]] == [own["search"]]
    lid = tools.link(other, "asks", "market:polymarket:516710")["id"]
    ev = tools.link(other, "evidence", fetched["id"], {"passages": ["The effective tariff rate is 22 percent"]})["id"]
    found = tools.get(other)["searches"]
    assert [(s["id"], s["uses"]) for s in found] == [
        (before["search"], ["market:polymarket:516710"]), (web["search"], [fetched["id"]]),
        (own["search"], ["market:polymarket:516710"])]
    assert all("results" not in s and s["returned"] >= 1 for s in found)
    assert tools.get(t)["searches"][0]["uses"] == []  # recorded on it, its results used by none of its statements
    third = proposition(tools, "Third")  # a market it names in a chance is one it uses, as one it links by implies
    tools.call("edit", {"id": third, "field": "chances", "value": [
        {"given": ["market:polymarket:516710"], "value": 0.5, "note": "Half of the cases it holds."}]})
    assert [s["uses"] for s in tools.get(third)["searches"]][:1] == [["market:polymarket:516710"]]
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
    refused(tools, "get", {"id": "SPY"}, "SPY is not an id: a company or a fund is name:SPY, and a factor is factor:N")
    refused(tools, "edit", {"id": t, "field": "baseline", "value": True},
            "baseline is the world its event's other worlds are measured from: yes or no for a side left whole, or "
            "a way's id")


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


def test_a_calls_entry_says_when_it_ended_its_attempts_and_its_seconds_waiting_for_the_store_and_holding_it(tools,
                                                                                                            monkeypatch):
    """A call's time divides into its time at the store, waiting for its lock and holding it, and the rest: what it reads
    from outside. The entry says so for every tool, one that holds the lock for its whole run or one that takes it only
    for its store's part. A reader holds the store from before the call asks for it until 0.3 s after, however soon
    the call comes to ask."""
    import threading
    import time
    from datetime import datetime
    t = proposition(tools)
    caller, asked = threading.current_thread(), threading.Event()

    class Asked:  # the store's lock, noting when the call asks for it
        def __init__(self, lock):
            self.lock = lock

        def acquire(self, *a, **kw):
            if threading.current_thread() is caller:
                asked.set()
            return self.lock.acquire(*a, **kw)

        def release(self):
            self.lock.release()
    monkeypatch.setattr(tools.store.lock, "_lock", Asked(tools.store.lock._lock))
    for tool, input in (("note", {"text": "while the page reads", "about_ids": [t]}), ("get", {"id": t})):
        holding = threading.Event()
        asked.clear()

        def page():  # a reader holding the store until 0.3 s after the call has asked for it
            with tools.store.lock:
                holding.set()
                assert asked.wait(10)
                time.sleep(0.3)

        threading.Thread(target=page).start()
        holding.wait()
        assert tools.call(tool, input)[0]
        c = [e for e in Trace(tools.store.trace_path).entries() if e["type"] == "call"][-1]
        assert c["tool"] == tool and c["passes"] == 1
        # held to the millisecond: a call whose reads are kept holds the store for less than one (Engine._check)
        assert 0.3 <= c["waited"] < 5 and c["held"] >= 0
        assert datetime.fromisoformat(c["ended"]) >= datetime.fromisoformat(c["at"])


def bars_answering(fetched: list, to: str | None = None, gate=None):
    """A client of daily prices, each read noted as it is made, answering the range asked or one ending at `to`; with a
    `gate`, once it opens."""
    def bars(subject, covering):
        fetched.append(subject)
        if gate is not None:
            assert gate.wait(10)
        return {"payload": {"status": "OK", "results": []}, "request": {"url": f"https://example.test/{subject}"},
                "retrieved_at": "2026-11-04T21:00:00+00:00", "range": (covering[0], to or covering[1])}
    return bars


def stood_aside(monkeypatch):
    """A gate that opens while a call stands aside, holding no lock, and shuts when it is made again: a read gated by
    it lands once its attempt has stood aside for it, as a read from a source takes time."""
    import threading
    import harness.tools
    aside, real_wait = threading.Event(), harness.tools.wait

    def wait(fs, *a, **kw):
        aside.set()
        try:
            return real_wait(fs, *a, **kw)
        finally:
            aside.clear()
    monkeypatch.setattr(harness.tools, "wait", wait)
    return aside


def test_a_call_is_made_again_for_as_long_as_each_attempt_meets_a_read_it_has_not_met(tools, monkeypatch):
    """TOOLS.md 5: a call whose reads are asked one after another, each standing aside until it lands, is made again
    for as long as each attempt meets a read it has not met. Here it asks 30, more than the attempts a call once had
    (a window over nine names and six factors ran out of them with every read answered): each is read once, and the
    call answers on its 31st attempt."""
    from harness.tools import Tools
    subjects, window, fetched = [f"T{i:02d}" for i in range(30)], ("2026-01-02", "2026-11-03"), []
    monkeypatch.setitem(tools.store.fetchers, "bars", bars_answering(fetched, gate=stood_aside(monkeypatch)))

    def exposure(self, call=None):  # a tool whose reads are asked one after another
        return {s: self.store.data("bars", s, covering=window)["id"] for s in subjects}
    monkeypatch.setattr(Tools, "exposure", exposure)
    ok, out = tools.call("exposure", {})
    assert ok, out
    assert list(out) == subjects and fetched == subjects  # each read once, in the order asked
    entry = [e for e in Trace(tools.store.trace_path).entries() if e["type"] == "call"][-1]
    assert entry["passes"] == len(subjects) + 1 and "exception" not in entry


def test_a_read_the_call_waited_for_that_the_store_does_not_find_once_kept_fails_it_as_an_error_of_ours(tools,
                                                                                                       monkeypatch):
    """A read a call stood aside for lands and is kept, and the store holds nothing answering it when the call asks for
    it again: here a client that answers a shorter range than it was asked. The call is not made again without end,
    and does not say the read could not be obtained: it fails inside the store, as an error of ours does, nothing
    written, the error in its trace."""
    from harness.tools import FAILED, Tools
    window, fetched = ("2026-01-02", "2026-11-03"), []
    monkeypatch.setitem(tools.store.fetchers, "bars", bars_answering(fetched, to="2026-06-30",
                                                                     gate=stood_aside(monkeypatch)))

    def exposure(self, call=None):  # the first read taken as it lands, a second stood aside for, the first asked again
        return [self.store.data("bars", s, covering=window)["id"] for s in ("AAA", "BBB", "AAA")]
    monkeypatch.setattr(Tools, "exposure", exposure)
    ok, out = tools.call("exposure", {})
    assert not ok and out == {"error": FAILED, "dependents": []}
    entry = [e for e in Trace(tools.store.trace_path).entries() if e["type"] == "call"][-1]
    assert entry["passes"] == 3 and "read again by the call that waited for it to land" in entry["exception"]
    assert fetched == ["AAA", "BBB"]  # nothing read again for it


def test_a_read_that_failed_past_one_more_try_gives_its_sources_answer_wherever_the_call_asks_for_it_again(
        tools, monkeypatch):
    """A read that fails, and fails again once more, fails the call with its source's answer. A call that reads
    several values beside one another (as window_returns reads each name) goes on to the others, and is made again
    while they stand aside: asked for again, the failed read gives the same answer, read no more, and the call fails
    with it once nothing else is under way."""
    from harness.derive.engine import Waiting
    from harness.store import StandAside
    from harness.tools import Tools
    window, fetched, gate = ("2026-01-02", "2026-11-03"), [], stood_aside(monkeypatch)
    answering = bars_answering(fetched, gate=gate)

    def bars(subject, covering):
        if subject == "BAD":
            fetched.append(subject)
            assert gate.wait(10)
            raise RuntimeError("the source answered 500")
        return answering(subject, covering)
    monkeypatch.setitem(tools.store.fetchers, "bars", bars)

    def exposure(self, call=None):  # BAD beside a chain of reads, each asked once the one before it is held
        waits = []
        for s in ("BAD", "T1", "T2", "T3"):
            try:
                self.store.data("bars", s, covering=window)
            except Waiting as e:
                waits.append(e)
                if s != "BAD":
                    break
        if waits:
            if self.store.stood_aside():
                raise StandAside("a read this call needs is under way")
            raise waits[0]
        return "read"
    monkeypatch.setattr(Tools, "exposure", exposure)
    ok, out = tools.call("exposure", {})
    assert not ok and out["error"].startswith("The daily prices of BAD from 2026-01-02 to 2026-11-03 could not be read")
    entry = [e for e in Trace(tools.store.trace_path).entries() if e["type"] == "call"][-1]
    assert entry["passes"] == 4 and "exception" not in entry  # its fourth attempt asked BAD after it failed twice
    assert fetched.count("BAD") == 2 and [s for s in fetched if s != "BAD"] == ["T1", "T2", "T3"]


def test_a_write_whose_required_read_fails_writes_nothing_and_fails_the_call_until_the_read_lands(tmp_path):
    """A call's write and the reads its result needs are one unit: a market's book that does not come back past its
    client's retries fails the link, with nothing written and no reason; what was fetched is kept; the same call made
    once the read lands writes the link with its quote (TOOLS.md 5)."""
    from breakdown import World as Scratch
    w = Scratch(tmp_path / "scratch-unit")
    tools = Tools(World(w.store, w.venues))
    r, m = w.prop("R"), w.market("M1", "Will it happen?", 0.28, 0.33)
    book = w.store.fetchers["book"]

    def down(subject, covering):
        raise TimeoutError("the venue did not answer")

    w.store.fetchers["book"] = down
    version = w.store.version
    ok, got = tools.call("link", {"from_id": r, "relation": "asks", "to_id": m})
    assert not ok and got == {"error": f"The book of {m} could not be read just now. Nothing was written, and the call "
                                       f"can be made again.", "dependents": []}  # the reading, by what it reads
    assert w.store.links_of(r, "asks") == [] and not w.store._market_kept(m)  # nothing written
    assert w.store.version == version  # rolled back: no version, and every figure reads the store as it was
    w.store.fetchers["book"] = book
    ok, got = tools.call("link", {"from_id": r, "relation": "asks", "to_id": m})
    assert ok and (got["derived"]["bid"]["value"], got["derived"]["ask"]["value"]) == (0.28, 0.33)
    assert [x["id"] for x in w.store.links_of(r, "asks")] == [got["id"]]


def test_the_calls_number_is_on_what_it_wrote(tools):
    ok, got = tools.call("add", {"type": "Proposition", "fields": {
        "title": "T", "text": "T", "comparison": "occurs", "window_end": "2027-03-31", "kind": "hitting"}})
    n = tools.last_call
    row = tools.store.db.execute("SELECT created_call FROM object WHERE id = ?", (got["id"],)).fetchone()
    assert row[0] == n


def test_window_returns_are_how_the_channels_moved_over_any_dates(tmp_path):
    w = impact_world(tmp_path, registry=default_registry(), channels=("SHY",))
    tools = Tools(World(w.store, w.venues))
    out = tools.window_returns("2026-10-01", "2026-10-05")
    names = [c["name"] for c in out["factors"]]
    assert names[:2] == ["S&P 500", "short-term Treasuries"] and out["factors"][0]["proxy"] == "SPY"
    first, second = out["factors"][0], out["factors"][1]
    assert first["move"] == pytest.approx(first[FOLLOWED], rel=1e-12)  # channel 1 is its proxy's return
    beta = w.store.engine.value("orthogonalize", second["factor"]).output["coefficients"][first["factor"]]
    # its residual, compounded: ln(1 + its return) less its coefficient on the first's residual (1.3)
    assert math.log1p(second[FOLLOWED]) == pytest.approx(math.log1p(second["move"]) - beta * math.log1p(first["move"]))
    closes = out["closes"]["SPY"]
    assert first["move"] == pytest.approx(closes["2026-10-05"] / closes["2026-10-01"] - 1)  # SPY pays no dividend here
    for row in list(w.store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends')")):  # read back to 2013
        w.store.put_data(row["kind"], row["subject"], w.store.data_payload(row["id"]), {"url": "test:history"},
                         retrieved_at="2026-11-05T02:00:00+00:00", range=("2013-01-01", "2026-11-04"))
    ok, got = tools.call("window_returns", {"start": "2019-01-02", "end": "2019-02-01"})
    assert not ok and got["error"].startswith("no factor's proxy has a close at both ends of the window: SPY has no "
                                              "close from 2019-01-02 to 2019-02-01")
    ok, got = tools.call("window_returns", {"start": "2026-10-03", "end": "2026-10-05"})  # a Saturday's close
    assert not ok and "SPY has no close on 2026-10-03" in got["error"]


# ---------------------------------------------------------------- the ledger's order and check's gaps


# Seven events, each moving the S&P 500 factor by x through one consequence, its proxy at 100, the shock 100 x, on
# g = 100,000, with p each event's stated chance: D = G x / (1 + p x).
EVENTS = {"E1": (0.5, -0.01), "E2": (0.1, -0.10), "E3": (0.3, 0.02), "E4": (0.02, -0.30), "E5": (0.6, 0.005),
          "E6": (0.4, -0.03), "E7": (0.2, 0.012)}
G = 100_000.0


def expected(p, x):
    """A shock x on the market's proxy against the event not happening, placed at p: its two worlds x / (1 + p x) of
    today's price apart (MATH.md 1.5)."""
    d = G * x / (1 + p * x)
    return {"full_impact": d, "volatility": math.sqrt(p * (1 - p)) * abs(d), "worst": min((1 - p) * d, -p * d)}


def test_the_ledger_keeps_creation_order_and_its_rows_rank_by_volatility_and_worst_outcome(events):
    """TOOLS.md 5, 9: every row, in the order the events were made, and no ranking beside them; the page sorts the rows by
    the figure its reader picks, here through the engine's own orders."""
    w, ids = events
    found = ledger(w.store)
    want = {e: expected(*EVENTS[e]) for e in EVENTS}
    by_volatility = sorted(EVENTS, key=lambda e: -want[e]["volatility"])
    assert [r["title"] for r in found["rows"]] == list(EVENTS)
    assert set(found) == {"asof", "value", "horizon_end", "volatility", "rows"}
    assert [r["id"] for r in ledger_by_volatility(found["rows"])] == [ids[e] for e in by_volatility]
    assert by_volatility == ["E4", "E2", "E6", "E3", "E1", "E7", "E5"]
    for r in found["rows"]:
        assert r["full_impact"] == pytest.approx(want[r["title"]]["full_impact"]) and r["volatility"] == pytest.approx(want[r["title"]]["volatility"])
        assert "coverage" not in r and r["range"] == [0.0, 1.0] and r["probability"] == EVENTS[r["title"]][0] \
            and r["point"]["rule"] == "chance"
        p, x = EVENTS[r["title"]]
        [c] = r["consequences"]
        assert (c["target"], c["shock"]["value"], c["c"]) == (r["contributions"][0]["factor"], x,
                                                             pytest.approx(100.0))
        assert c["levels"] == {"yes": pytest.approx(100 * (1 + x) / (1 + p * x)), "no": pytest.approx(100 / (1 + p * x))}
        assert c["moves"]["yes"]["move"] == pytest.approx(x) and "reasons" not in c
        assert r["contributions"][0]["name"] == "S&P 500"
        assert r["d"] == {}  # no consequence on a name's price: no idiosyncratic term
    worst = sorted(EVENTS, key=lambda e: want[e]["worst"])
    assert [ids[e] for e in worst] == [r["id"] for r in by_worst(found["rows"])]  # the most negative worst first
    assert worst == ["E4", "E2", "E6", "E3", "E1", "E5", "E7"]


def test_check_lists_exactly_what_the_work_is_missing_or_contradicts(events):
    w, ids = events
    store = w.store
    gaps = check(store)
    assert set(gaps) == {"fields_contradict", "differs", "cannot_hold", "stretched", "weights_cannot_hold", "values_contradict_price",
                         "price_at_zero_where_priced", "shocks_differ"}
    assert all(not v for v in gaps.values()), gaps  # nothing missing or contradicted
    bare = w.prop("Bare")
    w.link("portfolio", "watches", bare)
    w.judge.answers[("market_converse", "X", "Market A?")] = 0.3
    x = w.prop("X")
    la = w.link(x, "asks", w.market("MA", "Market A?", 0.60, 0.65))
    w.link(x, "implies", ids["E4"])  # X at least 0.60 implies E4, at a chance of 0.02: X's book gives way
    gaps = check(store)
    assert row(store, bare)["probability"] is None and row(store, bare)["full_impact"] is None
    market = store.link_by_id(la)["to"]
    assert [(g["link"], g["differs"]) for g in gaps["differs"]] == [(la, f"{market} can resolve Yes where {x} is false.")]
    assert [(g["link"], g["below"]) for g in gaps["stretched"]] == [(la, pytest.approx(0.58))]
    assert gaps["cannot_hold"] == []


def test_a_channels_exposure_is_shown_with_its_largest_month_and_their_product_for_every_channel(tmp_path):
    w = impact_world(tmp_path, g={"SHY": 60_000.0, "IBIT": 4_000.0, "USO": 1_000.0})
    reach = channel_reach(w.store)["factors"]
    assert len(reach) == 4  # every factor, the market and the three g names, with no bar
    for c in reach:
        derived = w.store.get(c["factor"])["derived"]
        assert c["largest_month"] == derived["largest_month"]["value"] == \
            derived["orthogonalization"]["value"]["largest_month"]
        assert c["largest_month_applied"] == pytest.approx(abs(c["dollar_beta"]) * c["largest_month"])
    assert {c["name"]: c["proxy"] for c in reach}["bitcoin"] == "IBIT"


def test_the_portfolio_holdings_and_exposure_are_facts_and_run_no_search(tmp_path):
    w = holding_world(tmp_path, channels=("TLT", "USO"))
    venues = w.venues
    tools = Tools(World(w.store, venues))
    got = holdings(w.store)
    assert got["value"] == pytest.approx(9000.0 + 1000.0)
    stated = w.store.stated_of("portfolio")  # the period the ledger looks over ends at the as-of plus its days
    assert got["horizon_end"] == (date.fromisoformat(stated["asof"]) + timedelta(days=stated["horizon_days"])).isoformat()
    held = {h["ticker"]: h for h in got["holdings"] if "ticker" in h}
    cash = [h for h in got["holdings"] if "cash" in h]
    assert held["NVDA"]["given"] == {"dollars": 5000.0} and held["NVDA"]["dollars"] == 5000.0
    closes = w.store.get("name:NVDA")["derived"]["prices"]["value"]
    assert held["NVDA"]["session_move"] == pytest.approx(closes["price"] / closes["previous_close"] - 1)
    assert held["FUND"]["fund"]["looked_through"] is True and cash == [{"cash": 1000.0, "currency": "USD",
                                                                        "dollars": 1000.0, "weight": 0.1}]
    names = {n["name"]: n for n in got["by_company"] if n.get("name")}
    assert names["name:NVDA"]["dollars"] == pytest.approx(5000 + 4000 * 0.6) and names["name:NVDA"]["direct"] == 5000.0
    assert names["name:NVDA"]["through"] == {"FUND": pytest.approx(2400.0)} and names["name:XOM"]["direct"] == 0.0
    assert got["by_company"][0]["name"] == "name:NVDA"  # largest first
    assert sum(c.get("dollars", 0.0) for c in got["by_company"]) == pytest.approx(got["value"])  # it sums to the whole
    for listed in (got["by_industry"], got["by_country"]):
        assert sum(c["dollars"] for c in listed) == pytest.approx(got["value"])
    ok, got = tools.call("exposure", {})
    assert ok
    months = [c["largest_month_applied"] for c in got["factors"]]
    assert len(months) == 3 and months == sorted(months, reverse=True)
    assert [p["ticker"] for p in got["proxies"]] == ["SPY", "TLT", "USO"]  # in the factors' order
    assert all(p["session_move"] is not None for p in got["proxies"])
    assert w.store.db.execute("SELECT count(*) FROM search").fetchone()[0] == 0  # no search


def test_the_portfolio_reads_as_what_it_owns_wherever_the_analyst_reads_it(tmp_path):
    """TOOLS.md 5: get on the portfolio, an edit of it, and a holding linked or edited each give what the portfolio owns as
    adding it does, in one form: the companies below every fund's floor summed with how many they are (XYZ, 0.1% of
    FUND), never listed one by one; each industry's and country's companies once, under each company; dollars to the
    cent. get adds the dollar beta and the volatility, each with its basis, and the links."""
    import numpy as np
    from massive_data import closes_from, put_bars, put_constituents, put_reference, random_returns
    rng = np.random.default_rng(5)
    w = impact_world(tmp_path, registry=default_registry())  # the market alone: its form holds on any factors
    for ticker in ("NVDA", "XOM", "XYZ", "AAPL"):
        put_reference(w.store, ticker, "CS")
        put_bars(w.store, ticker, closes_from(random_returns(rng, 0.02)))
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("NVDA", 0.6, "Equity"), ("XOM", 0.35, "Equity"), ("XYZ", 0.001, "Equity")])
    put_bars(w.store, "FUND", closes_from(random_returns(rng, 0.015)))
    for ticker, dollars in (("NVDA", 5000.0), ("FUND", 4000.0)):
        w.store.link("portfolio", "holds", f"name:{ticker}", {"dollars": dollars})
    tools = Tools(World(w.store, w.venues))
    first = holdings(w.store)
    ok, got = tools.call("get", {"id": "portfolio"})
    assert ok, got
    assert {k: got[k] for k in first} == first  # what add gave, as it now stands
    assert set(got["derived"]) == {"dollar_beta", "volatility"} and got["derived"]["volatility"]["basis"]
    assert {link["relation"] for link in got["links"]} >= {"holds"}
    rest = [c for c in got["by_company"] if "companies" in c]
    assert [(r["companies"], r["dollars"]) for r in rest] == [(1, 4.0)]  # XYZ, summed: 0.001 of FUND's $4,000
    assert "XYZ" not in str(got["by_company"])
    for listed in (got["by_company"], got["by_industry"], got["by_country"]):
        assert all("names" not in g and g["dollars"] == round(g["dollars"], 2) for g in listed)
    [nvda] = [c for c in got["by_company"] if c.get("name") == "name:NVDA"]
    assert nvda["through"] == {"FUND": 2400.0} and nvda["direct"] == 5000.0
    ok, linked = tools.call("link", {"from_id": "portfolio", "relation": "holds", "to_id": "name:AAPL",
                                     "fields": {"dollars": 1000.0}})
    assert ok, linked
    assert linked["relation"] == "holds" and "AAPL" in {h.get("ticker") for h in linked["portfolio"]["holdings"]}
    assert linked["portfolio"]["value"] == pytest.approx(first["value"] + 1000.0)
    ok, edited = tools.call("edit", {"id": linked["id"], "field": "dollars", "value": 2000.0})
    assert ok, edited
    assert edited["portfolio"]["value"] == pytest.approx(first["value"] + 2000.0)
    ok, horizon = tools.call("edit", {"id": "portfolio", "field": "horizon_days", "value": 90})
    assert ok, horizon
    assert horizon == {"id": "portfolio", "type": "Portfolio", "stated": w.store.stated_of("portfolio"),
                       **holdings(w.store)} and horizon["stated"]["horizon_days"] == 90


def test_what_a_store_records_reads_back_from_the_store_and_its_trace(events):
    w, ids = events
    tools = Tools(World(w.store, w.venues))
    tools.session = "s1"
    tools.call("get", {"id": ids["E1"]})
    ok, n = tools.call("note", {"text": "why", "about_ids": [ids["E1"]]})
    tools.call("remove", {"id": next(link["id"] for link in w.store.links_of(ids["E7"], "consequence"))})
    got = records(w.store)
    assert got["conversations"][0]["session"] == "s1" and got["conversations"][0]["calls"] == 3
    assert [r["relation"] for r in got["removed_links"]] == ["consequence", "about"]  # its basis note's tie, with it
    assert "coverage" not in got
    assert len(got["added"]["consequences"]) == 6
    assert {c["target"] for c in got["added"]["consequences"]} == {next(f["id"] for f in w.store.query("Factor")
                                                                        if f["stated"]["proxy"] == "SPY")}
    assert [m["id"] for m in got["added"]["metrics"]] == ["metric:us_effective_tariff_rate_pct"]
    chances = [a for a in got["assumptions"] if a["field"] == "chances[0]"]
    assert len(chances) == 7 and all(a["basis"]["note"]["text"] == WHY for a in chances)
    assert len([a for a in got["assumptions"] if a["field"] == "shock"]) == 6


def test_a_row_names_the_three_largest_shares_and_counts_them_all(events):
    from harness.ledger import MOSTLY, _by_name
    w, ids = events
    shares = [{"name": f"name:X{i}", "full_impact": -1000.0 * (5 - i)} for i in range(5)]
    kept = _by_name(w.store, shares)
    assert MOSTLY == 3 and [k["name"] for k in kept] == ["name:X0", "name:X1", "name:X2"]
    row = ledger(w.store)["rows"][0]
    assert row["names"] == len(w.store.computation_output(row["basis"]["contributors"])["contributors"])


def test_get_reads_a_market_the_store_does_not_hold_from_its_venue_and_keeps_nothing(tools):
    before = snapshot(tools.store)
    ok, got = tools.call("get", {"id": "market:polymarket:516710"})
    assert ok and got["kept"] is False and got["question"].startswith("Will the US effective tariff rate")
    assert got["rules"].startswith("Resolves Yes if the Yale Budget Lab") and got["close_time"] == "2027-03-31"
    # The fixture's as-of, 2026-11-04, is before the read: Polymarket's history gives the day's price alone, no quote.
    assert (got["bid"], got["ask"], got["price"], got["history_price"]) == (None, None, None, 0.42)
    assert got["price_date"] == "2026-10-01" and got["depth"] is None
    assert snapshot(tools.store)["tables"]["market"] == before["tables"]["market"] == []
    assert snapshot(tools.store)["tables"]["data"] == before["tables"]["data"]
    refused(tools, "get", {"id": "market:polymarket:999999"}, "Polymarket lists no market 999999")


def test_a_failed_call_names_each_reading_it_could_not_obtain_and_an_error_of_ours_names_none(tmp_path, caplog):
    """TOOLS.md 5: a call that needs readings the store could not obtain names them, three at most and the rest counted;
    an error of ours that escapes a call fails it with nothing written and that sentence alone, the error itself in the
    trace and the log."""
    from breakdown import World as Scratch
    from harness.tools import unread
    tail = " could not be read just now. Nothing was written, and the call can be made again."
    assert unread(["the daily prices of NVDA from 2024-11-12 to 2026-11-12"]) == \
        "The daily prices of NVDA from 2024-11-12 to 2026-11-12" + tail
    assert unread(["the holdings of SPY", "the book of market:kalshi:K"]) == \
        "The holdings of SPY and the book of market:kalshi:K" + tail
    assert unread([f"the reference for T{k}" for k in range(5)]) == \
        "The reference for T0, the reference for T1, the reference for T2 and 2 other readings" + tail
    w = Scratch(tmp_path / "scratch-escape")
    tools = Tools(World(w.store, w.venues))
    r = w.prop("R")
    before = w.store.links_of(r, "asks")

    def broken(*args, **kwargs):
        raise KeyError("an error of ours")

    tools.store.link = broken
    ok, got = tools.call("link", {"from_id": r, "relation": "asks", "to_id": w.market("M1", "Will it?", 0.2, 0.3)})
    assert not ok and got == {"error": "This call failed inside the store, and nothing was written. It can be made "
                                       "again.", "dependents": []}
    entry = [e for e in tools.trace.entries() if e.get("type") == "call"][-1]
    assert entry["error"] == got and "KeyError: 'an error of ours'" in entry["exception"]
    assert "failed inside the store" in caplog.text and w.store.links_of(r, "asks") == before
