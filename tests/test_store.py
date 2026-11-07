"""The store: its writes, refusals, removal, keeping what is
used, and the derivation engine with its cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from conftest import (ASOF, FIXTURES, RETRIEVED, BookFetcher, StubVenues, fixture_judge, count, fx_lookthrough,
                      registry, snapshot)
from harness.derive import Derivation, Registry
from harness.store import Store, StoreError, store_dir

PROPOSITION = {"title": "T", "text": "The US effective tariff rate reaches 25% by 2027-03-31",
               "relation": "occurs", "window_start": ASOF, "window_end": "2027-03-31", "kind": "hitting"}


def add_portfolio(store, holdings=None, **fields):
    holdings = holdings or [{"ticker": "QQQ", "dollars": 40000.0}, {"ticker": "NVDA", "dollars": 15000.0}]
    return store.add("Portfolio", {"asof": ASOF, "holdings": holdings, **fields})


def add_proposition(store, **over):
    return store.add("Proposition", {**PROPOSITION, **over})


def fetch_page(store, url="https://example.org/effective-rate", text="The effective rate is 22 percent.",
               dated="2026-10-30"):
    return store.put_page(url, text, retrieved_at=RETRIEVED, dated=dated)


def add_metric(store, mid="us_effective_tariff_rate_pct", source=None, **over):
    source = source or fetch_page(store)
    return store.add("Metric", {"id": mid, "name": "the US effective tariff rate", "unit": "pct", "current_value": {"value": 22, "source_id": source},
                                "resolution_source": "Yale Budget Lab", **over})


def add_channel(store, name="SEMI", proxy="SOXX", order=6):
    return store.add("Channel", {"name": name, "proxy_ticker": proxy, "order": order})


def refused(store, call, *, contains=None):
    """The call is refused, and the store is exactly as it was."""
    before = snapshot(store)
    with pytest.raises(StoreError) as e:
        call()
    assert snapshot(store) == before
    if contains:
        assert contains in e.value.message, e.value.message
    return e.value


def market_store(make_store, **kw):
    judge = fixture_judge()
    venues = StubVenues({"market:kalshi:M1": {"question": "Will the US raise tariffs by March 31?"},
                         "market:kalshi:M3": {"question": "Effective tariff rate above 20% on March 31?"}})
    books = BookFetcher({"market:kalshi:M1": {"bid": 0.28, "ask": 0.33},
                         "market:kalshi:M3": {"bid": 0.30, "ask": 0.90}})
    store = make_store(judge=judge, venues=venues, fetchers={"book": books}, **kw)
    return store, judge, venues, books


# ---------------------------------------------------------------- 1. no path writes a derived field


def test_no_path_writes_a_derived_field_or_creates_what_the_store_derives(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store)
    t = add_proposition(store)
    u = add_proposition(store, title="U")
    channel = add_channel(store)
    metric = add_metric(store)
    calls = [
        lambda: add_proposition(store, lo=0.3),
        lambda: add_proposition(store, p=0.5),
        lambda: store.add("Portfolio", {"asof": ASOF, "V": 100.0}),
        lambda: store.edit(t["id"], "p", 0.5),
        lambda: store.edit(t["id"], "coverage", 1.0),
        lambda: store.edit("portfolio", "g", [1.0]),
        lambda: store.link(u["id"], "implies", t["id"], {"check": 1.0}),
        lambda: store.link(t["id"], "quoted_by", "market:kalshi:M1", {"forward": 1.0}),
        lambda: store.link(t["id"], "quoted_by", "market:kalshi:M1", {"conditional_no": 0.1}),
        lambda: store.link(t["id"], "consequence", metric["id"], {"value": 1.0, "meaning": "gap", "gap": 1.0}),
        lambda: store.link("name:QQQ", "looks_through_to", "name:NVDA"),
        lambda: store.link("name:NVDA", "loads_on", channel["id"]),
        lambda: store.link(t["id"], "basis", "cmp:1"),
        lambda: store.link(metric["id"], "sourced_by", metric["citations"]["current_value"]),
    ]
    for kind in ("Judgment", "Computation", "Source", "Market", "Name", "Note"):
        calls.append(lambda kind=kind: store.add(kind, {}))
    for call in calls:
        refused(store, call)
    # the cache keeps its rows as they were written: no update or delete reaches them
    store.link("portfolio", "watches", t["id"])
    store.link(t["id"], "quoted_by", "market:kalshi:M1")
    store.get(t["id"])
    assert count(store, "computation") and count(store, "judgment")
    for statement in ("UPDATE computation SET output = '{}'", "DELETE FROM computation",
                      "UPDATE judgment SET answer = 0.99", "DELETE FROM judgment"):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(statement)


def test_add_creates_no_judgment_computation_source_market_or_name(make_store):
    store = make_store(registry_=Registry())
    before = {t: count(store, t) for t in ("judgment", "computation", "source", "market")}
    add_portfolio(store)
    add_proposition(store)
    add_channel(store)
    assert {t: count(store, t) for t in before} == before
    assert {r["type"] for r in store.db.execute("SELECT type FROM object")} == {"Portfolio", "Proposition", "Channel"}


# ---------------------------------------------------------------- 2. citations, and refusals write nothing


def test_a_metric_the_analyst_adds_has_a_name_in_plain_words(make_store):
    """A metric is named as the page shows it; one added without a name is refused, and the name can be edited."""
    store = make_store()
    page = fetch_page(store)
    fields = {"id": "wti_usd", "unit": "USD", "current_value": {"value": 70, "source_id": page}}
    refused(store, lambda: store.add("Metric", fields), contains="Metric requires name")
    metric = store.add("Metric", {**fields, "name": "the WTI price"})
    assert metric["stated"]["name"] == "the WTI price"
    assert store.edit(metric["id"], "name", "the price of WTI crude")["stated"]["name"] == "the price of WTI crude"


def test_a_cited_field_without_its_source_fails_and_writes_nothing(make_store):
    store = make_store()
    page = fetch_page(store)
    base = {"id": "wti_usd", "name": "the WTI price", "unit": "USD"}
    refused(store, lambda: store.add("Metric", {**base, "current_value": 70}), contains="fact about the world")
    refused(store, lambda: store.add("Metric", {**base, "current_value": {"value": 70}}))
    refused(store, lambda: store.add("Metric", {**base, "current_value": {"value": 70, "source_id": "src:99"}}),
            contains="not a fetched page")
    refused(store, lambda: store.add("Metric", {**base, "unit": {"value": "USD", "source_id": page}}),
            contains="takes no source")
    metric = store.add("Metric", {**base, "current_value": {"value": 70, "source_id": page}})
    assert metric["citations"] == {"current_value": page}
    refused(store, lambda: store.edit(metric["id"], "current_value", 74.2), contains="takes a source_id")
    refused(store, lambda: store.edit(metric["id"], "unit", "USD/bbl", source_id=page), contains="takes no source")


def test_any_refused_call_writes_nothing(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store, holdings=[{"ticker": "QQQ", "weight": 0.4}], total=100000)
    metric = add_metric(store)
    t = add_proposition(store, metric=metric["id"], relation=">=", value=25)
    store.link("portfolio", "watches", t["id"])
    channel = add_channel(store)
    page = fetch_page(store, url="https://example.org/unused")
    calls = [
        lambda: store.add("Proposition", {**PROPOSITION, "relation": ">="}),  # a comparison needs metric and value
        lambda: store.add("Proposition", {**PROPOSITION, "window_start": "2027-04-01"}),
        lambda: store.add("Proposition", {**PROPOSITION, "metric": "us_effective_tariff_rate_pct",
                                          "relation": ">=", "value": 25}),  # names the metric by its bare id
        lambda: store.add("Nonsense", {}),
        lambda: store.add("Channel", {"name": "SEMI", "proxy_ticker": "SMH", "order": 7}),
        lambda: store.add("Channel", {"name": "SEMI2", "proxy_ticker": "SMH", "order": 6, "carries": "x"}),
        lambda: store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct"}),
        lambda: store.link("portfolio", "watches", t["id"]),  # a second identical link
        lambda: store.link(t["id"], "implies", t["id"]),
        lambda: store.link(t["id"], "quoted_by", "market:kalshi:NOPE"),  # the venue lists no such market
        lambda: store.link(t["id"], "quoted_by", "market:nasdaq:X"),
        lambda: store.link(t["id"], "evidence", "src:1", {"passages": ["x"]}),  # no page src:1 was fetched
        lambda: store.link(t["id"], "watches", "portfolio"),
        lambda: store.link("portfolio", "holds", "name:SPY"),  # a holding gives one amount
        lambda: store.link(metric["id"], "tracks", channel["id"], {"conversion": "yield(-1)"}),
        lambda: store.note("about a page not kept", [page]),
        lambda: store.note("about nothing here", ["prop:99"]),
        lambda: store.edit("portfolio", "asof", "2026-11-05"),
        lambda: store.edit(metric["id"], "id", "renamed"),
        lambda: store.edit("portfolio", "total", None),  # holdings are weights of the total
        lambda: store.edit(t["id"], "window_end", "2027-12-31"),  # past the horizon while watched
        lambda: store.edit("portfolio", "horizon_days", 30),
        lambda: store.edit("name:QQQ", "ticker", "QQQM"),
        lambda: store.remove("name:QQQ"),
        lambda: store.remove(metric["id"]),
        lambda: store.remove("cmp:1"),
        lambda: store.remove(page),
        lambda: store.remove("src:1"),
        lambda: store.record_search("search_markets", {}, [], venue="nasdaq", endpoint="/markets"),
        lambda: store.put_data("book", "market:kalshi:M9", {}, {"endpoint": "/book"}, retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", [], {"endpoint": "/aggs", "apiKey": "x"}, retrieved_at=RETRIEVED),
    ]
    for call in calls:
        refused(store, call)


# ---------------------------------------------------------------- 3. one portfolio


def test_a_second_portfolio_is_refused_and_the_as_of_is_fixed(make_store):
    store = make_store()
    add_portfolio(store)
    refused(store, lambda: store.add("Portfolio", {"asof": ASOF}), contains="holds one")
    store.remove("portfolio")
    refused(store, lambda: store.add("Portfolio", {"asof": "2026-12-01"}), contains="as of 2026-11-04")
    assert add_portfolio(store)["id"] == "portfolio"


# ---------------------------------------------------------------- 4. removal refusals name their dependents


def test_removing_what_is_derived_listed_cited_or_named_is_refused_naming_dependents(make_store):
    store, judge, venues, books = market_store(make_store)
    store.put_data("constituents", "name:QQQ", {"NVDA": 0.08, "AAPL": 0.07}, {"endpoint": "/constituents"},
                   retrieved_at=RETRIEVED)
    add_portfolio(store)
    channel = add_channel(store)
    page = fetch_page(store)
    metric = add_metric(store, source=page)
    t = add_proposition(store, metric=metric["id"], relation=">=", value=25)
    q = store.link(t["id"], "quoted_by", "market:kalshi:M1")
    store.link("portfolio", "watches", t["id"])
    store.get(t["id"])

    e = refused(store, lambda: store.remove("name:AAPL"), contains="derived")
    e = refused(store, lambda: store.remove("name:QQQ"), contains="derived")
    assert e.dependents == ["link:1"]  # the holds link
    refused(store, lambda: store.remove("looks_through_to:name:QQQ>name:AAPL"), contains="derived")
    e = refused(store, lambda: store.remove("metric:price:SOXX"), contains="derived")
    assert e.dependents == [channel["id"]]
    refused(store, lambda: store.remove(f"tracks:metric:price:SOXX>{channel['id']}"), contains="derived")
    e = refused(store, lambda: store.remove("market:kalshi:M1"), contains="venue")
    assert e.dependents == [q["id"]]
    e = refused(store, lambda: store.remove(page), contains="cited")
    assert e.dependents == [f"{metric['id']}.current_value"]
    citation = store.db.execute("SELECT id FROM link WHERE relation = 'sourced_by'").fetchone()[0]
    e = refused(store, lambda: store.remove(citation), contains="keeps its source")
    assert e.dependents == [f"{metric['id']}.current_value"]
    e = refused(store, lambda: store.remove(metric["id"]), contains="named by")
    assert e.dependents == [f"{t['id']}.metric"]
    refused(store, lambda: store.remove("cmp:1"), contains="follows from structure")
    refused(store, lambda: store.remove("jdg:1"), contains="follows from structure")
    # with its dependents removed first, the metric goes, and its page with it
    store.remove(t["id"])
    record = store.remove(metric["id"])
    assert [r["id"] for r in record["released"]] == [page]


# ---------------------------------------------------------------- 5. removal returns values to the same cache rows


def test_removing_a_link_returns_every_derived_value_to_its_prior_cache_rows_without_the_judge(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store)
    t = add_proposition(store, title="T")
    level = add_proposition(store, title="L", kind="level_at_date", window_start="2027-03-31")
    store.link("portfolio", "watches", t["id"])
    store.link(level["id"], "implies", t["id"])
    m1 = store.link(t["id"], "quoted_by", "market:kalshi:M1")
    before_t, before_l, before_m1 = store.get(t["id"]), store.get(level["id"]), store.get(m1["id"])
    assert before_t["derived"]["lo"]["value"] == 0.28 and before_t["derived"]["hi"]["value"] == 0.33
    assert before_m1["derived"]["bound"]["value"] == [0.28, 0.33]

    m3 = store.link(level["id"], "quoted_by", "market:kalshi:M3")
    during = store.get(t["id"])["derived"]
    assert during["lo"]["value"] == 0.30 and during["lo"]["basis"] != before_t["derived"]["lo"]["basis"]
    assert during["p"]["basis"] != before_t["derived"]["p"]["basis"]

    asked, computations, judgments = len(judge.calls), count(store, "computation"), count(store, "judgment")
    record = store.remove(m3["id"])
    assert record["removed"]["id"] == m3["id"]
    assert [r["id"] for r in record["released"]] == ["market:kalshi:M3"]
    assert store.get(t["id"])["derived"] == before_t["derived"]
    assert store.get(level["id"])["derived"] == before_l["derived"]
    assert store.get(m1["id"])["derived"] == before_m1["derived"]
    assert len(judge.calls) == asked
    assert (count(store, "computation"), count(store, "judgment")) == (computations, judgments)


# ---------------------------------------------------------------- 6. a changed input re-derives; an unchanged one hits


def test_a_changed_input_re_derives_and_an_unchanged_one_hits_the_cache(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store)
    t = add_proposition(store)
    other = add_proposition(store, title="other")
    store.link("portfolio", "watches", t["id"])
    store.link(t["id"], "quoted_by", "market:kalshi:M1")
    first = store.get(t["id"])["derived"]
    rows, asked = count(store, "computation"), len(judge.calls)

    assert store.get(t["id"])["derived"] == first
    store.edit(other["id"], "title", "another")
    assert store.get(t["id"])["derived"] == first
    assert (count(store, "computation"), len(judge.calls)) == (rows, asked)

    store.edit(t["id"], "text", "The US effective tariff rate reaches 25% at any point by 2027-03-31")
    changed = store.get(t["id"])["derived"]
    assert changed["lo"] == first["lo"]  # the interval does not read the text
    assert changed["p"]["basis"] != first["p"]["basis"] and len(judge.calls) == asked + 1
    assert books.calls == ["market:kalshi:M1"]  # the book was fetched on first read, once


# ---------------------------------------------------------------- 7. derived ids are stable


def test_derived_ids_are_stable_across_recomputation(make_store):
    store = make_store()
    store.put_data("constituents", "name:QQQ", {"NVDA": 0.08, "AAPL": 0.07, "MSFT": 0.06},
                   {"endpoint": "/etf-global/v1/constituents", "composite_ticker": "QQQ"}, retrieved_at=RETRIEVED)
    add_portfolio(store)
    names = {n["id"] for n in store.query("Name")}
    assert names == {"name:QQQ", "name:NVDA", "name:AAPL", "name:MSFT"}
    through = {n["link"] for n in store.neighbors("name:QQQ", "looks_through_to")}
    assert through == {"looks_through_to:name:QQQ>name:NVDA", "looks_through_to:name:QQQ>name:AAPL",
                       "looks_through_to:name:QQQ>name:MSFT"}
    aapl = store.get("name:AAPL")
    assert aapl["because"] == ["looked through"] and aapl["derived"]["e"]["value"] == pytest.approx(2800.0)
    assert store.get("name:NVDA")["derived"]["e"]["value"] == pytest.approx(15000.0 + 3200.0)
    named = store.note("AAPL is in QQQ", ["name:AAPL"])

    holding = next(n["link"] for n in store.neighbors("portfolio", "holds") if n["object"]["id"] == "name:QQQ")
    store.edit(holding, "dollars", 50000.0)
    assert {n["id"] for n in store.query("Name")} == names
    assert {n["link"] for n in store.neighbors("name:QQQ", "looks_through_to")} == through
    again = store.get("name:AAPL")
    assert again["derived"]["e"]["value"] == pytest.approx(3500.0)
    assert again["derived"]["e"]["basis"] != aapl["derived"]["e"]["basis"]
    assert store.get(named["id"])["links"][0]["other"] == "name:AAPL"

    # without the fund, a Name the analyst named persists, and one nothing names goes
    store.remove(holding)
    assert store.get("name:AAPL")["because"] == ["named"]
    assert "e" not in store.get("name:AAPL")["derived"]
    with pytest.raises(StoreError):
        store.get("name:MSFT")


# ---------------------------------------------------------------- 8. pages and markets are kept while used


def test_a_page_is_kept_while_used_and_one_never_used_is_not_kept(make_store, tmp_path):
    store = make_store("scratch-pages")
    unused = fetch_page(store, url="https://example.org/unused")
    used = fetch_page(store, url="https://example.org/used")
    assert fetch_page(store, url="https://example.org/used") == used
    metric = add_metric(store, source=used)
    assert store.get(used)["kept"] is True and store.get(unused)["kept"] is False
    assert count(store, "source") == 1  # the cited page
    store.close()

    store = Store.open(tmp_path / "scratch-pages", registry=registry())
    with pytest.raises(StoreError):
        store.get(unused)
    t = add_proposition(store)
    store.link(t["id"], "evidence", used, {"passages": ["22 percent"]})
    note = store.note("why this page matters", [used])
    second = fetch_page(store, url="https://example.org/second")
    store.edit(metric["id"], "current_value", 23, source_id=second)
    assert store.get(used)["kept"] is True  # still evidence for t
    record = store.remove(next(link["id"] for link in store.get(t["id"])["links"] if link["relation"] == "evidence"))
    assert [r["id"] for r in record["released"]] == [used]
    page = store.get(used)
    assert page["kept"] is False and page["url"] == "https://example.org/used"  # again a fetched page, held
    assert store.get(note["id"])["links"] == []  # the note's about link went with the page
    assert store.get(second)["cited_for"] == [f"{metric['id']}.current_value"]
    assert fetch_page(store, url="https://example.org/used") == used  # held: not fetched again
    store.link(t["id"], "evidence", used, {"passages": ["22 percent"]})  # and citable under the same id
    assert store.get(used)["kept"] is True
    store.close()


def test_a_page_whose_last_use_goes_is_again_a_fetched_page_citable_under_its_id(make_store):
    """A page cited for a consequence's figure, whose figure then rests on a note instead, is not kept;
    it is a fetched page again, and linking it as evidence keeps it under the same id."""
    store = make_store("scratch-release")
    metric = add_metric(store)
    t = add_proposition(store)
    page = fetch_page(store, url="https://example.org/episode", text="Stocks fell 12 percent in that episode.")
    c = store.link(t["id"], "consequence", metric["id"], {"value": {"value": -1.0, "source_id": page,
                                                                    "passages": ["fell 12 percent"]}, "meaning": "gap"})
    assert store.get(page)["kept"] is True
    store.edit(c["id"], "value", -1.5)  # the figure now rests on a note
    assert store.get(page)["kept"] is False and store.type_of(page) == "Source"
    store.link(t["id"], "evidence", page, {"passages": ["Stocks fell 12 percent"]})
    assert store.get(page)["kept"] is True and store.get(page)["text"] == "Stocks fell 12 percent in that episode."

    other = fetch_page(store, url="https://example.org/other", text="Another page.")
    store.link(t["id"], "evidence", other, {"passages": ["Another page."]})
    record = store.remove(other)  # removing a page: its evidence links go, and it is held again
    assert record["removed"] == {"id": other, "type": "Source", "url": "https://example.org/other", "kept": False}
    assert store.get(other)["kept"] is False

    with pytest.raises(RuntimeError):  # a transaction that keeps a held page and fails leaves it held
        with store.transaction():
            store.link(t["id"], "evidence", other, {"passages": ["Another page."]})
            raise RuntimeError
    assert store.get(other)["kept"] is False and count(store, "source") == 2
    evidence = next(x["id"] for x in store.get(t["id"])["links"] if x["relation"] == "evidence")
    with pytest.raises(RuntimeError):  # and one that releases a kept page and fails leaves it kept, and not held
        with store.transaction():
            store.remove(evidence)
            assert store.get(page)["kept"] is False
            raise RuntimeError
    assert store.get(page)["kept"] is True and page not in store._pending
    store.close()


def test_a_market_is_kept_while_a_link_or_a_note_uses_it_and_its_data_stays(make_store):
    store, judge, venues, books = market_store(make_store)
    t = add_proposition(store)
    q = store.link(t["id"], "quoted_by", "market:kalshi:M1")
    assert store.get(q["id"])["derived"]["bound"]["value"] == [0.28, 0.33]
    market = store.get("market:kalshi:M1")
    assert [d["kind"] for d in market["data"]] == ["market", "book"]
    note = store.note("the closest market to T", ["market:kalshi:M1"])
    store.remove(q["id"])
    assert store.get("market:kalshi:M1")["links"][0]["id"] == note["links"][0]["id"]
    record = store.remove(note["id"])
    assert [r["id"] for r in record["released"]] == ["market:kalshi:M1"]
    assert count(store, "market") == 0
    # fetched data is kept, never taken with the market: a checkpoint that kept it reads it again
    assert store.db.execute("SELECT count(*) FROM data WHERE subject = 'market:kalshi:M1'").fetchone()[0] == 2
    assert count(store, "computation") > 0
    # the market enters again when a link names it, reading what was fetched, with no second call to the venue
    q = store.link(t["id"], "quoted_by", "market:kalshi:M1")
    assert venues.calls == ["market:kalshi:M1"] and books.calls == ["market:kalshi:M1"]
    assert store.get(q["id"])["derived"]["bound"]["value"] == [0.28, 0.33]
    assert [d["kind"] for d in store.get("market:kalshi:M1")["data"]] == ["market", "book"]


# ---------------------------------------------------------------- the rules across objects


def test_a_watched_window_stays_within_the_horizon(make_store):
    store = make_store()
    add_portfolio(store)
    late = add_proposition(store, window_end="2027-06-01")
    e = refused(store, lambda: store.link("portfolio", "watches", late["id"]), contains="past the horizon 2027-05-03")
    assert e.dependents == [late["id"]]
    t = add_proposition(store)
    store.link("portfolio", "watches", t["id"])
    refused(store, lambda: store.edit(t["id"], "window_end", "2027-05-04"), contains="horizon")
    refused(store, lambda: store.edit("portfolio", "horizon_days", 120), contains="horizon")
    store.edit("portfolio", "horizon_days", 210)  # to 2027-06-02
    store.link("portfolio", "watches", late["id"])


def test_an_events_consequences_set_each_channel_through_one_metric(make_store):
    store = make_store()
    oil = add_channel(store, "OIL", "USO", 5)
    wti = add_metric(store, "wti_usd", unit="USD")
    brent = add_metric(store, "brent_usd", source=fetch_page(store, url="https://example.org/brent"), unit="USD")
    store.link(wti["id"], "tracks", oil["id"], {"conversion": "price"})
    event = add_proposition(store)
    store.link(event["id"], "consequence", wti["id"], {"value": 1.0, "meaning": "gap"})
    e = refused(store, lambda: store.link(event["id"], "consequence", "metric:price:USO", {"value": 1.0, "meaning": "gap"}),
                contains="already sets")
    assert e.dependents == [wti["id"]]
    store.link(event["id"], "consequence", brent["id"], {"value": 1.0, "meaning": "gap"})  # brent tracks no channel yet
    e = refused(store, lambda: store.link(brent["id"], "tracks", oil["id"], {"conversion": "price"}),
                contains="already sets")
    assert e.dependents == [wti["id"]]
    other = add_proposition(store, title="Another event")
    store.link(other["id"], "consequence", "metric:price:USO", {"value": 1.0, "meaning": "gap"})  # each event its own


def test_a_consequence_states_a_figure_and_what_it_means(make_store):
    store = make_store()
    wti = add_metric(store, "wti_usd", unit="USD")
    event = add_proposition(store)
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"]), contains="requires value")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"value": 5.0}), contains="requires meaning")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"value": "five", "meaning": "gap"}))
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"value": 5.0, "meaning": "the gap"}),
            contains="meaning is one of gap, level_if_happens, level_if_not")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"value": 80.0, "meaning":
                                                                              "level_if_happens"}), contains="not traded")
    lid = store.link(event["id"], "consequence", wti["id"], {"value": 5.0, "meaning": "gap"})["id"]
    assert store.get(lid)["stated"] == {"value": {"value": 5.0}, "meaning": "gap"}
    refused(store, lambda: store.edit(lid, "value", 6.0, "src:1"), contains="its basis rides in its value")
    store.edit(lid, "value", -3.0)
    assert store.get(lid)["stated"]["value"] == {"value": -3.0}
    p = add_proposition(store, title="P")["id"]
    refused(store, lambda: store.link(event["id"], "consequence", p, {"value": 1.0, "meaning": "gap"}),
            contains="runs to Metric")


def test_unique_links_channels_and_identity(make_store):
    store = make_store()
    add_portfolio(store)
    t = add_proposition(store)
    first = store.link("portfolio", "watches", t["id"])
    e = refused(store, lambda: store.link("portfolio", "watches", t["id"]), contains="edit it instead")
    assert e.dependents == [first["id"]]
    semi = add_channel(store)
    e = refused(store, lambda: add_channel(store, name="SEMI", proxy="SMH", order=7))
    assert e.dependents == [semi["id"]]
    refused(store, lambda: add_channel(store, name="SEMI2", proxy="SMH", order=6))
    store.edit(semi["id"], "order", 6.5)
    add_channel(store, name="SEMI2", proxy="SMH", order=6)
    refused(store, lambda: store.edit("portfolio", "asof", "2026-11-05"), contains="different store")
    metric = add_metric(store)
    refused(store, lambda: store.edit(metric["id"], "id", "other"), contains="identity")
    refused(store, lambda: store.edit("market:kalshi:M1", "id", "M2"))


def test_edits_of_links_and_holdings(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store, holdings=[{"ticker": "qqq", "weight": 0.4}, {"ticker": "CASH", "weight": 0.6}],
                  total=100000)
    holding = store.neighbors("portfolio", "holds")[0]["link"]
    assert store.get(holding)["to"] == "name:QQQ"
    store.edit(holding, "dollars", 41000.0)
    assert store.get(holding)["stated"] == {"dollars": 41000.0}
    t = add_proposition(store)
    q = store.link(t["id"], "quoted_by", "market:kalshi:M1")
    assert q["stated"] == {"transform": "identity"}
    flipped = store.edit(q["id"], "transform", "complement")
    assert flipped["derived"]["bound"]["value"] == [pytest.approx(0.67), pytest.approx(0.72)]
    refused(store, lambda: store.edit(q["id"], "forward", 1.0), contains="derived")
    refused(store, lambda: store.edit(q["id"], "transform", "inverse"))


# ---------------------------------------------------------------- records, the cache, and the layout


def test_searches_are_recorded_on_their_proposition_and_go_with_it(make_store):
    store = make_store()
    t = add_proposition(store)
    s1 = store.record_search("search_markets", {"q": "tariff", "status": "open"}, ["market:kalshi:M1"],
                             proposition_id=t["id"], venue="kalshi", endpoint="/markets", call=3)
    s2 = store.record_search("search_web", {"allowed_domains": ["budgetlab.yale.edu"]},
                             [{"url": "https://budgetlab.yale.edu/x", "title": "x", "date": "2026-10-30"}],
                             proposition_id=t["id"], query="effective tariff rate trajectory")
    store.record_search("search_markets", {"q": "oil"}, [], venue="polymarket", endpoint="/public-search")
    searches = store.get(t["id"])["searches"]
    assert [s["id"] for s in searches] == [s1, s2]
    assert searches[0]["venue"] == "kalshi" and searches[0]["results"] == ["market:kalshi:M1"]
    assert count(store, "market") == 0  # search results are not kept
    record = store.remove(t["id"])
    assert record["searches"] == [s1, s2]
    assert count(store, "search") == 1


def test_an_unavailable_input_is_a_null_value_with_its_reason_and_is_retried(make_store):
    store = make_store(venues=StubVenues({"market:kalshi:M1": {}}))
    t = add_proposition(store)
    q = store.link(t["id"], "quoted_by", "market:kalshi:M1")
    rows = count(store, "computation")
    derived = store.get(t["id"])["derived"]
    assert derived["lo"]["value"] is None and "cannot read it here" in derived["lo"]["reason"]
    assert count(store, "computation") == rows  # an unavailable value is not cached
    store.fetchers["book"] = BookFetcher({"market:kalshi:M1": {"bid": 0.4, "ask": 0.5}})
    store.edit(q["id"], "transform", "identity")  # any write; the next read tries again
    assert store.get(t["id"])["derived"]["lo"]["value"] == 0.4


def test_a_basis_chain_opens_to_its_inputs(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store)
    t = add_proposition(store)
    store.link("portfolio", "watches", t["id"])
    store.link(t["id"], "quoted_by", "market:kalshi:M1")
    point = store.get(t["id"])["derived"]["p"]
    cmp = store.get(point["basis"])
    assert cmp["type"] == "Computation" and cmp["derivation"] == "fx_point"
    judgment = store.get(cmp["inputs"]["judgment"]["judgment"])
    assert judgment["type"] == "Judgment" and judgment["model"] == "fixture"
    interval = store.get(cmp["inputs"]["interval"]["basis"])
    book = store.get(interval["inputs"]["quotes"][0]["book"]["data"])
    assert book["type"] == "data" and book["payload"] == {"bid": 0.28, "ask": 0.33}


def test_the_same_question_is_the_same_judgment_key_in_any_store(make_store):
    a, b = make_store("scratch-a", judge=fixture_judge()), make_store("scratch-b", judge=fixture_judge())
    fetch_page(b, url="https://example.org/padding")  # the page and the proposition get other ids in b
    add_proposition(b, title="padding")
    for store in (a, b):
        page = fetch_page(store, url="https://example.org/evidence", text="same page")
        t = add_proposition(store)
        store.link(t["id"], "evidence", page, {"passages": ["same page"]})
        add_portfolio(store)
        store.link("portfolio", "watches", t["id"])
        store.get(t["id"])
    keys = [{r[0] for r in s.db.execute("SELECT input_hash FROM judgment")} for s in (a, b)]
    assert keys[0] == keys[1] and len(keys[0]) == 1


def test_the_initial_contents_are_channels_and_load_as_the_analysts(make_store):
    store = make_store(initial=FIXTURES / "initial")
    channels = {o["stated"]["name"]: o["id"] for o in store.query("Channel")}
    assert set(channels) == {"MKT", "OIL"} and all(store.get(c)["origin"] == "initial" for c in channels.values())
    assert {o["id"] for o in store.query("Metric")} == {"metric:price:SPY", "metric:price:USO"}  # derived prices
    record = store.remove(channels["OIL"])  # the initial contents are the analyst's to remove
    assert record["removed"]["id"] == channels["OIL"] and store.type_of("metric:price:USO") is None


def test_initial_contents_that_break_a_rule_load_nothing(make_store, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "channels.yaml").write_text((FIXTURES / "initial" / "channels.yaml").read_text() +
                                       "  - name: MKT\n    proxy_ticker: VTI\n    order: 9\n")
    store = make_store("scratch-bad")
    before = snapshot(store)
    with pytest.raises(StoreError, match="each channel's name is its own"):
        store.load_initial(bad)
    assert snapshot(store) == before


def test_a_rolled_back_call_leaves_a_fetched_page_held(make_store):
    store = make_store()
    page = fetch_page(store)
    refused(store, lambda: store.add("Metric", {"id": "x_pct", "name": "x", "unit": "pct", "current_value":
                                                {"value": 1, "source_id": page}, "traded": "yes"}))
    with pytest.raises(RuntimeError):
        with store.transaction():
            store._keep_page(page)
            raise RuntimeError("a failure after the page was kept")
    assert store.get(page)["kept"] is False and count(store, "source") == 0
    add_metric(store, "x_pct", source=page)
    assert store.get(page)["kept"] is True


def test_the_store_layout(make_store, tmp_path):
    store = make_store("scratch-layout")
    add_portfolio(store)
    assert store.path == tmp_path / "scratch-layout" / "store.sqlite"
    assert store.trace_path.exists()
    store.close()
    reopened = Store.open(tmp_path / "scratch-layout")
    assert reopened.get("portfolio")["stated"]["asof"] == ASOF
    reopened.close()
    with pytest.raises(StoreError):
        Store.create(tmp_path / "scratch-layout")


def test_a_notes_targets_go_with_the_records_they_name(make_store):
    store, judge, venues, books = market_store(make_store)
    t = add_proposition(store)
    search = store.record_search("search_markets", {"q": "tariff"}, [], proposition_id=t["id"], venue="kalshi",
                                 endpoint="/markets")
    store.link(t["id"], "quoted_by", "market:kalshi:M1")
    store.get(t["id"])
    data = [d["id"] for d in store.get("market:kalshi:M1")["data"]]
    note = store.note("what the search and the book showed", [search, data[-1]])
    assert len(store.get(note["id"])["links"]) == 2
    store.remove(t["id"])
    assert [link["other"] for link in store.get(note["id"])["links"]] == [data[-1]]  # the search went; the data stays
    assert count(store, "market") == 0


def test_a_store_whose_initial_contents_fail_to_load_is_not_left_behind(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "channels.yaml").write_text("channels:\n  - {name: MKT, order: 1}\n")
    with pytest.raises(StoreError, match="requires proxy_ticker"):
        Store.create(tmp_path / "scratch-failed", initial=bad)
    assert not (tmp_path / "scratch-failed" / "store.sqlite").exists()


def test_each_produced_field_carries_the_basis_of_its_own_computation(make_store):
    def inputs(ctx, subject):
        return {"channels": [c["id"] for c in ctx.links("portfolio", "holds")]}

    def compute(m, load):
        return {"objects": {"name:QQQ": {"type": "Name", "fields": {"b": [1.0, 0.2]}}},
                "links": {"loads_on:name:QQQ>channel:1": {"relation": "loads_on", "from": "name:QQQ",
                                                          "to": "channel:1", "fields": {"beta": 1.0}}}}

    betas = Derivation("fx_betas", 1, "Portfolio", inputs, compute, produces=("Name", "loads_on"))
    store = make_store(registry_=Registry([fx_lookthrough(), betas]))
    add_channel(store, "MKT", "SPY", 1)
    add_portfolio(store)
    derived = store.get("name:QQQ")["derived"]
    assert derived["e"]["value"] == 40000.0 and derived["b"]["value"] == [1.0, 0.2]
    e_basis, b_basis = store.get(derived["e"]["basis"]), store.get(derived["b"]["basis"])
    assert (e_basis["derivation"], b_basis["derivation"]) == ("fx_lookthrough", "fx_betas")
    loads = store.get("loads_on:name:QQQ>channel:1")
    assert loads["derived"]["beta"] == {"value": 1.0, "basis": b_basis["id"]}
    assert [n["link"] for n in store.neighbors("channel:1", "loads_on")] == ["loads_on:name:QQQ>channel:1"]


def test_clearing_a_field_with_a_default_restores_the_default(make_store):
    store = make_store()
    add_portfolio(store, horizon_days=90)
    assert store.edit("portfolio", "horizon_days", None)["stated"]["horizon_days"] == 180
    metric = add_metric(store, traded=True)
    assert store.edit(metric["id"], "traded", None)["stated"]["traded"] is False


def test_store_names(tmp_path):
    assert store_dir("2026-11-04") == Path("stores/2026-11-04")
    assert store_dir("scratch-dev", root=tmp_path) == tmp_path / "scratch-dev"
    with pytest.raises(StoreError):
        store_dir("../elsewhere")


def test_no_request_or_payload_is_recorded_with_a_credential(make_store, monkeypatch):
    secret = "sk-test-0123456789abcdef"
    monkeypatch.setenv("MASSIVE_API_KEY", secret)
    store = make_store()
    t = add_proposition(store)
    aggs = "https://api.massive.com/v2/aggs/ticker/SPY/range/1/day/2025-01-01/2026-11-04"
    for call in [
        lambda: store.put_data("bars", "SPY", [], {"url": f"{aggs}?adjusted=true&apiKey=abcdef123456"},
                               retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", [], {"url": f"{aggs}?api_key=abcdef123456&sort=asc"},
                               retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", [], {"url": aggs, "params": {"apiKey": "abcdef123456"}},
                               retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", [], {"url": aggs, "headers": ["Authorization: Bearer abcdef123456"]},
                               retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", [], {"url": aggs, "headers": {"Accept": "Bearer abcdef123456789"}},
                               retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", [], {"url": f"{aggs}?k={secret}"}, retrieved_at=RETRIEVED),
        lambda: store.put_data("bars", "SPY", {"echo": f"key {secret}"}, {"url": aggs}, retrieved_at=RETRIEVED),
        lambda: store.record_search("search_markets", {"q": "tariff", "next": "https://x.org/m?page=2&apikey=zz99"},
                                    [], proposition_id=t["id"], venue="kalshi", endpoint="/markets"),
        lambda: store.record_search("search_web", {}, [{"url": f"https://x.org/?k={secret}"}], query="tariffs"),
        lambda: store.put_page("https://api.massive.com/v3/reference/tickers/QQQ?apiKey=abcdef123456", "QQQ",
                               retrieved_at=RETRIEVED),
        lambda: store.put_page("https://example.org/leak", f"the key is {secret}", retrieved_at=RETRIEVED),
    ]:
        refused(store, call, contains="without credentials")
    # what only resembles a credential is kept: a token id, and a page that describes keys
    store.put_data("bars", "SPY", [], {"url": aggs, "params": {"token_id": "71321", "sort": "asc"}},
                   retrieved_at=RETRIEVED)
    page = store.put_page("https://massive.com/docs/rest/quickstart",
                          "Include your key as ?apiKey=YOUR_API_KEY or Authorization: Bearer YOUR_API_KEY.",
                          retrieved_at=RETRIEVED)
    assert store.get(page)["kept"] is False


def test_prefetch_fetches_side_by_side_keeps_rows_in_the_order_asked_and_leaves_a_failure_to_the_read(make_store):
    import threading
    import time
    from harness.derive.engine import Unavailable
    active, peak, asked = [0], [0], []
    lock = threading.Lock()

    def fetch(subject, covering):
        with lock:
            asked.append(subject)
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        if subject == "BAD":
            raise RuntimeError("the source answered 500")
        return {"payload": {"ticker": subject}, "request": {"url": f"https://example.org/{subject}"},
                "retrieved_at": "2026-11-04T21:00:00+00:00", "range": ("2026-11-04", "2026-11-04")}

    store = make_store(fetchers={"ticker_ref": fetch})
    store.put_data("ticker_ref", "HELD", {"ticker": "HELD"}, {"url": "x"}, retrieved_at="2026-11-04T21:00:00+00:00",
                   range=("2026-11-04", "2026-11-04"))
    t = time.monotonic()
    kept = store.prefetch("ticker_ref", ["A", "B", "HELD", "BAD", "C", "D"], ("2026-11-04", "2026-11-04"))
    assert kept == 4 and peak[0] > 1 and time.monotonic() - t < 0.25  # side by side, not 5 x 0.05 s
    assert "HELD" not in asked  # what the store holds is not fetched again
    rows = [r[0] for r in store.db.execute("SELECT subject FROM data WHERE kind = 'ticker_ref' ORDER BY rowid")]
    assert rows == ["HELD", "A", "B", "C", "D"]
    with pytest.raises(Unavailable, match="could not be read just now"):
        store.data("ticker_ref", "BAD", covering=("2026-11-04", "2026-11-04"))  # read again: the reason recorded
