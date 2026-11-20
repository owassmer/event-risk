"""A proposition stated whole (TOOLS.md 5, `add`): by its definition where it is new, or by its id where it exists, with
its chances, what is said of its worlds and its links, each under its relation's name, in one call. Each part means what
its own statement means, so the store it leaves is the store the same statements leave made one at a time, with the same
row; a part the store refuses is named by its place in the statement, and nothing is written."""
from __future__ import annotations

import re

import pytest

from breakdown import stated
from conftest import snapshot
from harness import ledger
from harness.tools import Tools
from harness.world import World
from test_impact import channel, event, holding_world, section_10

ROW = ("probability", "yes", "no", "full_impact", "volatility", "expected")


def ok(w, name, **input):
    done, out = w.tools.call(name, input)
    assert done, out
    return out


def refused(w, name, **input) -> dict:
    before = snapshot(w.store)
    done, out = w.tools.call(name, input)
    assert not done, out
    assert snapshot(w.store) == before  # nothing written
    return out


def shape(store) -> tuple[dict, list]:
    """What the analyst stated, with each proposition by its title and each note by its text: the propositions' stated
    fields, and every link the analyst made with its stated fields."""
    titles = {p["id"]: p["stated"]["title"] for p in store.query("Proposition")}
    notes = {n["id"]: n["stated"]["text"] for n in store.query("Note")}

    def named(x):
        if isinstance(x, str):
            return titles.get(x, notes.get(x, x))
        if isinstance(x, list):
            return [named(v) for v in x]
        if isinstance(x, dict):
            return {k: named(v) for k, v in x.items()}
        return x
    props = {titles[p["id"]]: named(p["stated"]) for p in store.query("Proposition")}
    links = sorted(repr(named([r["relation"], r["from_id"], r["to_id"], __import__("json").loads(r["stated"])]))
                   for r in store._all("SELECT relation, from_id, to_id, stated FROM link WHERE origin = 'analyst' "
                                       "AND relation != 'about'"))
    return props, links


def row(store, pid) -> dict:
    got = ledger.event_now(store, pid)["row"]
    return {k: round(got[k], 6) for k in ROW} | {"range": [round(x, 6) for x in got["range"]],
                                                   "worst": round(got["worst_outcome"]["pnl"], 6)}


def markets(w):
    m1 = w.market("1", "Will the US raise tariffs by March 31?", 0.28, 0.33, size=14_000 / 0.33)
    m2 = w.market("2", "Will the US effective tariff rate be above 30% on March 31, 2027?", 0.10, 0.15)
    m3 = w.market("3", "Will the US effective tariff rate be above 20% on March 31, 2027?", 0.86, 0.90)
    return m1, m2, m3


def one_at_a_time(w) -> str:
    """The worked row's event, one statement to a call, as the worked row states it."""
    def prop(*args):
        return ok(w, "add", type="Proposition", fields=w.prop(*args))["id"]
    t, l, r = prop("T", ">=", 25), prop("L", ">=", 25, "level_at_date"), prop("R")
    a30, a20 = prop("A30", ">", 30, "level_at_date"), prop("A20", ">", 20, "level_at_date")
    m1, m2, m3 = markets(w)
    ok(w, "link", from_id="portfolio", relation="watches", to_id=t)
    ok(w, "link", from_id=r, relation="asks", to_id=m1)
    ok(w, "link", from_id=t, relation="implies", to_id=r)
    ok(w, "link", from_id=a30, relation="asks", to_id=m2)
    ok(w, "link", from_id=a20, relation="asks", to_id=m3)
    for a, b in ((a30, l), (l, a20), (l, t)):
        ok(w, "link", from_id=a, relation="implies", to_id=b)
    ok(w, "edit", id=t, field="chances", value=[stated({"given": [r], "value": 0.62})])
    ok(w, "edit", id=t, field="baseline", value="no")
    semi = ok(w, "add", type="Factor", fields={"name": "semiconductors", "proxy": "SOXX", "order": 2})["id"]
    ok(w, "link", from_id=t, relation="consequence", to_id=channel(w, "SPY"), fields={"shock": stated(-0.033)})
    ok(w, "link", from_id=t, relation="consequence", to_id=semi, fields={"shock": stated(-0.063)})
    return t


def said_of_t(w, semi: str) -> dict:
    """Everything the worked row says of T beyond its definition, in one statement: the breakdown with its markets, each
    proposition stated at the link that relates it, the chance given R by R's name, the baseline and the shocks."""
    m1, m2, m3 = markets(w)
    return {"watches": [{"from": "portfolio"}],
            "implies": [{"to": {**w.prop("R"), "as": "R", "asks": [{"to": m1}]}},
                        {"from": {**w.prop("L", ">=", 25, "level_at_date"),
                                  "implies": [{"from": {**w.prop("A30", ">", 30, "level_at_date"),
                                                        "asks": [{"to": m2}]}},
                                              {"to": {**w.prop("A20", ">", 20, "level_at_date"),
                                                      "asks": [{"to": m3}]}}]}}],
            "chances": [stated({"given": ["R"], "value": 0.62})], "baseline": "no",
            "consequence": [{"to": channel(w, "SPY"), "shock": stated(-0.033)},
                            {"to": semi, "shock": stated(-0.063)}]}


def test_the_worked_rows_event_stated_whole_leaves_the_store_and_the_row_its_statements_leave_one_at_a_time(tmp_path):
    a = section_10(tmp_path / "a")
    t = one_at_a_time(a)
    b = section_10(tmp_path / "b")
    semi = ok(b, "add", type="Factor", fields={"name": "semiconductors", "proxy": "SOXX", "order": 2})["id"]
    out = ok(b, "add", type="Proposition", fields={**b.prop("T", ">=", 25), **said_of_t(b, semi)})
    assert shape(b.store) == shape(a.store)
    assert row(b.store, out["id"]) == row(a.store, t)
    assert row(b.store, out["id"])["probability"] == pytest.approx(0.1891)
    # each part's id by its place: every proposition and link it made
    assert out["made"]["fields"] == out["id"] and len(out["made"]) == 5 + 10  # 5 propositions; 10 links
    assert out["made"]["fields.implies[0].to"] == next(p["id"] for p in b.store.query("Proposition")
                                                         if p["stated"]["title"] == "R")
    assert all(re.fullmatch(r"(prop|link):\d+", i) for i in out["made"].values())
    # the event once, each market once with its terms and book
    assert [e["id"] for e in out["events"]] == [out["id"]]
    assert [m["market"] for m in out["markets"]] == ["market:polymarket:1", "market:polymarket:2",
                                                     "market:polymarket:3"]
    assert all(m["question"] and "bid" in m for m in out["markets"])


def test_a_proposition_given_by_its_id_takes_the_rest_of_what_is_said_of_it_in_one_statement(tmp_path):
    """The flow the record shows: the proposition is added, the venues searched on it, and the rest said afterwards."""
    a = section_10(tmp_path / "a")
    t = one_at_a_time(a)
    b = section_10(tmp_path / "b")
    t_b = ok(b, "add", type="Proposition", fields=b.prop("T", ">=", 25))["id"]
    semi = ok(b, "add", type="Factor", fields={"name": "semiconductors", "proxy": "SOXX", "order": 2})["id"]
    out = ok(b, "add", type="Proposition", fields={"id": t_b, **said_of_t(b, semi)})
    assert out["id"] == t_b and "fields" not in out["made"]  # it made no proposition at the root
    assert shape(b.store) == shape(a.store) and row(b.store, t_b) == row(a.store, t)


def test_given_by_its_id_each_field_replaces_what_the_proposition_had_and_each_link_is_added(tmp_path):
    w = section_10(tmp_path)
    t = one_at_a_time(w)
    r = next(p["id"] for p in w.store.query("Proposition") if p["stated"]["title"] == "R")
    out = ok(w, "add", type="Proposition", fields={
        "id": t, "chances": [stated({"given": [r], "value": 0.5})],
        "way": [{"to": {**w.prop("T by a law"), "as": "law"}, "side": "yes"},
                {"to": {**w.prop("T by an order"), "chances": [stated({"given": [t], "value": 0.4})]}, "side": "yes"}],
        "baseline": "no"})
    assert [c["value"] for c in w.store.stated_of(t)["chances"]] == [0.5]  # the list replaced, as edit replaces it
    assert len(w.store.links_of(t, "implies")) == 1 and len(w.store.links_of(t, "consequence")) == 2  # kept
    assert [x["to"] for x in w.store.links_of(t, "way")] == [out["made"]["fields.way[0].to"],
                                                             out["made"]["fields.way[1].to"]]
    assert row(w.store, t)["probability"] == pytest.approx(0.5 * 0.305)


def test_a_name_stands_for_its_proposition_wherever_the_statement_names_one_before_or_after_it(tmp_path):
    w = section_10(tmp_path)
    ok(w, "add", type="Factor", fields={"name": "semiconductors", "proxy": "SOXX", "order": 2})
    m1, _, _ = markets(w)
    out = ok(w, "add", type="Proposition", fields={
        **w.prop("T", ">=", 25), "watches": [{"from": "portfolio"}],
        "baseline": "quiet",  # a way named before it is stated
        "chances": [stated({"given": ["R"], "value": 0.6})],
        "way": [{"to": {**w.prop("T quietly"), "as": "quiet", "chances": [stated({"given": ["T"], "value": 0.7})]},
                 "side": "yes"},
                {"to": {**w.prop("T loudly"),
                        "consequence": [{"to": channel(w, "SPY"), "shock": stated(-0.1)}]}, "side": "yes"}],
        "implies": [{"to": {**w.prop("R"), "as": "R", "asks": [{"to": m1}]}}], "as": "T"})
    t = out["id"]
    assert w.store.stated_of(t)["baseline"] == out["made"]["fields.way[0].to"]
    quiet = w.store.stated_of(out["made"]["fields.way[0].to"])
    assert quiet["chances"][0]["given"] == [t]
    assert w.store.stated_of(t)["chances"][0]["given"] == [out["made"]["fields.implies[0].to"]]


ALIAS = "fields.as: an alias is one word with no colon or space, other than yes, no and portfolio. Nothing was written."


@pytest.mark.parametrize("name", ["a:b", "a b", "yes", "no", "portfolio"])
def test_an_alias_is_one_word_with_no_colon_or_space_that_the_statement_does_not_already_read(tmp_path, name):
    w = section_10(tmp_path)
    out = refused(w, "add", type="Proposition", fields={**w.prop("T"), "as": name})
    assert out["error"] == ALIAS


def test_a_name_given_twice_or_never_given_is_refused_by_its_place(tmp_path):
    w = section_10(tmp_path)
    out = refused(w, "add", type="Proposition", fields={**w.prop("T"), "as": "t",
                                                        "implies": [{"to": {**w.prop("R"), "as": "t"}}]})
    assert out["error"] == ("fields.implies[0].to.as: t is already the alias of another proposition in this "
                            "statement. Nothing was written.")
    out = refused(w, "add", type="Proposition", fields={**w.prop("T"), "chances": [stated({"given": ["R"],
                                                                                          "value": 0.5})]})
    assert out["error"] == ("fields.chances[0].given: R is no alias given in this statement. A proposition stated "
                            "here takes one with `as`. Nothing was written.")


def test_a_part_the_store_refuses_is_named_by_its_place_and_nothing_is_written(tmp_path):
    w = section_10(tmp_path)
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "watches": [{"from": "portfolio"}], "baseline": "no",
        "way": [{"to": w.prop("T by a law"), "side": "yes"}],
        "consequence": [{"to": channel(w, "SPY"), "shock": stated(-0.1)},
                        {"to": channel(w, "SPY"), "shock": stated(-0.2)}]})
    assert out["error"] == ("fields.consequence[1]: the link at fields.consequence[0] already links the proposition "
                            "at fields to factor:1 by consequence, and two things have one link of a relation. State "
                            "it once. Nothing was written.")
    assert out["dependents"] == ["fields.consequence[0]"]  # by its place: the rollback took its id


def test_what_a_refusal_lacks_is_said_as_the_part_of_the_statement_that_states_it(tmp_path):
    w = section_10(tmp_path)
    m1, _, _ = markets(w)
    spy = channel(w, "SPY")
    # a shock with no baseline named
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "watches": [{"from": "portfolio"}], "consequence": [{"to": spy, "shock": stated(-0.1)}]})
    assert out["error"] == ("fields.consequence[0]: a shock is stated against its event's baseline, and this event "
                            "states none. State it at fields.baseline. Nothing was written.")
    # a chance that follows a market no `asks` links
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "chances": [stated({"value": 0.3, "follow": m1, "responsiveness": 0.5})]})
    assert out["error"] == (f"fields.chances: a chance follows a market that asks this proposition, and {m1} is not "
                            f"linked to it. Link it at fields.asks: {{\"to\": \"{m1}\"}}. Nothing was written.")
    # a weight read from a market no `asks` links, on a way
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "watches": [{"from": "portfolio"}], "baseline": "no", "chances": [stated({"value": 0.3})],
        "way": [{"to": {**w.prop("T by a law"), "priced_in": stated({"value": {"market": m1}})}, "side": "yes"}]})
    assert out["error"] == (f"fields.way[0].to.priced_in: priced_in reads the probability of a market that asks this "
                            f"proposition, and {m1} is not linked to it. Link it at fields.way[0].to.asks: "
                            f"{{\"to\": \"{m1}\"}}. Nothing was written.")
    # a chance that follows a holding's implied weight, with no values stated in the event's worlds
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "chances": [stated({"value": 0.3, "follow": "name:NVDA", "responsiveness": 0.5})]})
    assert out["error"] == ("fields.chances: a chance that follows name:NVDA reads the weight its price implies from its "
                            "values in the worlds of this proposition, and none is stated. State them at fields.worth: "
                            "{\"to\": \"name:NVDA\", ...}. Nothing was written.")
    # a proposition given by its id: the remedy is at its place in the statement
    t = ok(w, "add", type="Proposition", fields=w.prop("T2"))["id"]
    ok(w, "link", from_id="portfolio", relation="watches", to_id=t)
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T2 by a law"), "way": [{"from": {"id": t}, "side": "yes"}],
        "consequence": [{"to": spy, "shock": stated(-0.1)}]})
    assert out["error"] == ("fields.consequence[0]: a shock is stated against its event's baseline, and this event "
                            "states none. State it at fields.way[0].from.baseline. Nothing was written.")
    # one that is in the store and not in the statement: the store's own remedy, a call on an id that stays
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T2 by a law"), "way": [{"from": t, "side": "yes"}], "consequence": [{"to": spy, "shock": stated(-0.1)}]})
    assert out["error"] == (f"fields.consequence[0]: a shock is stated against its event's baseline, and {t} states "
                            f"none. State it with edit({t}, baseline, ...). Nothing was written.")


def test_a_way_written_the_wrong_way_round_says_which_end_of_a_way_link_is_the_way(tmp_path):
    w = section_10(tmp_path)
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "way": [{"from": w.prop("T by a law"), "side": "yes"},
                               {"from": w.prop("T by an order"), "side": "yes"}]})
    assert out["error"] == (
        "fields.way[1]: the proposition at fields is a way of the proposition at fields.way[0].from (the link at "
        "fields.way[0]), and a way has one parent. Under `way`, {\"to\": ...} names a way of this proposition, and "
        "{\"from\": ...} the proposition it is a way of. Nothing was written.")


def test_a_part_a_proposition_has_not_is_refused_with_the_parts_it_has(tmp_path):
    w = section_10(tmp_path)
    out = refused(w, "add", type="Proposition", fields={**w.prop("T"), "consequences": []})
    assert out["error"] == (
        "fields: a proposition has no part `consequences`. Its parts are its definition (title, text, metric, "
        "comparison, value, upper_comparison, upper_value, window_start, window_end, kind, resolution_source), or the "
        "`id` of one that exists; its chances and what is said of its worlds (chances, baseline, episode, priced_in); its "
        "links, each under its relation's name (watches, implies, and, or, then, way, asks, evidence, consequence, "
        "worth); and `as`, its alias in this call. Nothing was written.")


def test_a_proposition_given_by_its_id_takes_no_definition_beside_it(tmp_path):
    w = section_10(tmp_path)
    t = ok(w, "add", type="Proposition", fields=w.prop("T"))["id"]
    out = refused(w, "add", type="Proposition", fields={"id": t, "title": "T, again"})
    assert out["error"] == (f"fields: {t} is given by its id, which takes no definition fields beside it (`title`). "
                            f"edit({t}, definition, ...) replaces its definition. Nothing was written.")
    out = refused(w, "add", type="Proposition", fields={"id": "prop:99", "baseline": "no"})
    assert out["error"] == "fields.id: 'prop:99' is no proposition in the store. Nothing was written."


def test_a_company_with_no_price_is_refused_at_the_part_that_shocks_it(tmp_path):
    """The commit's own check, made before the statement returns, named by the place of the part that names it."""
    from harness.derive.series import lookback
    from massive_data import put_dividends
    w = holding_world(tmp_path)
    w.store.put_data("bars", "ZZZZ", {"status": "NOT_FOUND", "message": "no such ticker"},
                     {"url": "https://api.massive.com/v2/aggs/ticker/ZZZZ"}, retrieved_at="2026-11-04T21:00:00+00:00",
                     range=(lookback("2026-11-04"), "2026-11-04"))
    put_dividends(w.store, "ZZZZ", {})
    tools = Tools(World(w.store, w.venues))
    e = event(w, p=0.3)
    before = snapshot(w.store)
    done, out = tools.call("add", {"type": "Proposition", "fields": {
        "id": e, "consequence": [{"to": channel(w, "SPY"), "shock": stated(-0.1)},
                                 {"to": "name:ZZZZ", "shock": stated(-0.2)}]}})
    assert not done and snapshot(w.store) == before
    assert out["error"].startswith("fields.consequence[1]: name:ZZZZ has no price") and \
        out["dependents"] == ["fields.consequence[1]"]


def test_each_link_whose_two_texts_describe_different_outcomes_says_what_differs_by_its_place(tmp_path):
    w = section_10(tmp_path, readings={("implication", "T", "R"): 0.1})  # the judge reads T as reaching past R
    m1, _, _ = markets(w)
    out = ok(w, "add", type="Proposition", fields={
        **w.prop("T", ">=", 25), "implies": [{"to": {**w.prop("R"), "asks": [{"to": m1}]}}]})
    t, r, lid = out["id"], out["made"]["fields.implies[0].to"], out["made"]["fields.implies[0]"]
    assert out["links"] == {"fields.implies[0]": {"link": lid, "differs": f"{t} can be true where {r} is false."}}
    assert [x["link"] for x in ok(w, "check")["differs"]] == [lid]  # what check() lists, read once


def test_a_statement_that_stands_aside_for_a_read_is_made_again_and_writes_each_part_once(tmp_path, monkeypatch):
    """A whole statement is one call: where a read it needs is under way (a company's prices, here), it stands aside,
    its writes rolled back, and is made again once the read is in, writing each part once."""
    import numpy as np
    from harness.trace import Trace
    from massive_data import ms, put_dividends, put_reference
    from test_impact import closes_from, random_returns
    from test_tools import stood_aside
    w = holding_world(tmp_path)
    put_reference(w.store, "XYZ", "CS")
    put_dividends(w.store, "XYZ", {})
    closes = closes_from(random_returns(np.random.default_rng(7), 0.02))
    gate, read = stood_aside(monkeypatch), []

    def bars(subject, covering):
        read.append(subject)
        assert gate.wait(10)
        return {"payload": {"ticker": subject, "adjusted": True, "status": "OK",
                            "results": [{"t": ms(d), "o": c, "h": c, "l": c, "c": c, "v": 1000.0}
                                        for d, c in sorted(closes.items())]},
                "request": {"url": f"https://example.test/{subject}"}, "retrieved_at": "2026-11-04T21:00:00+00:00",
                "range": covering}
    monkeypatch.setitem(w.store.fetchers, "bars", bars)
    tools = Tools(World(w.store, w.venues))
    e = event(w, p=0.3)
    done, out = tools.call("add", {"type": "Proposition", "fields": {
        "id": e, "consequence": [{"to": channel(w, "SPY"), "shock": stated(-0.1)},
                                 {"to": "name:XYZ", "shock": stated(-0.2)}]}})
    assert done, out
    entry = [x for x in Trace(w.store.trace_path).entries() if x["type"] == "call"][-1]
    assert entry["passes"] >= 2 and read.count("XYZ") == 1  # it stood aside, and the read was made once
    assert [x["to"] for x in w.store.links_of(e, "consequence")] == [channel(w, "SPY"), "name:XYZ"]  # each once
    assert out["made"] == {"fields.consequence[0]": w.store.links_of(e, "consequence")[0]["id"],
                           "fields.consequence[1]": w.store.links_of(e, "consequence")[1]["id"]}


def test_a_refusal_leads_with_the_path_the_analyst_wrote(tmp_path):
    """A refusal of a proposition stated whole leads with one path, the most specific the store gives: a field of a
    proposition the statement made, or of one it gives by its id, reads as the path the analyst wrote it at, and stands
    alone where it lies under the part refused. A proposition given by its id keeps its id elsewhere in the words."""
    w = section_10(tmp_path)
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "as": "t", "chances": [{"given": ["t"], "value": 0.5, "note": "given itself"}]})
    assert out["error"] == ("fields.chances[0] is given the proposition at fields itself: a chance of a proposition is "
                            "given others. Nothing was written.")
    pid = ok(w, "add", type="Proposition", fields=w.prop("T"))["id"]
    out = refused(w, "add", type="Proposition", fields={
        "id": pid, "chances": [{"given": [pid], "value": 0.5, "note": "given itself"}]})
    assert out["error"] == (f"fields.chances[0] is given {pid} itself: a chance of a proposition is given others. "
                            f"Nothing was written.")


def test_a_chance_sent_with_its_basis_inside_its_value_is_refused_with_the_chance_put_right(tmp_path):
    """A chance's value is a number from 0 to 1, and its basis stands beside the value on the chance (ONTOLOGY.md 2.1). A
    chance sent as `priced_in` and `episode` are, its figure and note nested in its value, is refused at its value's
    path, with the chance in the shape the store takes: its own figure lifted out and its basis beside it. Inside a
    statement the path is the one the analyst wrote, for a proposition it makes and for one it gives by its id; in
    a single edit it is the proposition's id."""
    w = section_10(tmp_path)
    nested = {"given": [], "value": {"value": 0.97, "note": "the basis"}}
    out = refused(w, "add", type="Proposition", fields={**w.prop("T"), "chances": [nested]})
    assert out["error"] == ("fields.chances[0].value: a chance's value is a number from 0 to 1, with its basis beside "
                            "it: {\"given\": [...], \"value\": 0.97, \"note\": \"...\"}. Nothing was written.")
    pid = ok(w, "add", type="Proposition", fields=w.prop("T"))["id"]
    out = refused(w, "add", type="Proposition", fields={"id": pid, "chances": [nested]})
    assert out["error"] == ("fields.chances[0].value: a chance's value is a number from 0 to 1, with its basis beside "
                            "it: {\"given\": [...], \"value\": 0.97, \"note\": \"...\"}. Nothing was written.")
    out = refused(w, "edit", id=pid, field="chances", value=[{"value": "high", "source_id": "src:1",
                                                             "passages": ["a passage"]}])
    assert out["error"].startswith(f"{pid}.chances[0].value: a chance's value is a number from 0 to 1, with its "
                                   "basis beside it: {\"value\": ..., \"source_id\": \"...\", \"passages\": [...]}")
