"""The store: its writes, refusals, removal, keeping what is
used, and the derivation engine with its cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from conftest import (ASOF, FIXTURES, RETRIEVED, BookFetcher, StubVenues, fixture_judge, count, fx_lookthrough, masked,
                      registry, snapshot)
from harness.derive import Derivation, Registry
from harness.store import Store, StoreError, store_dir
from breakdown import stated

PROPOSITION = {"title": "T", "text": "The US effective tariff rate reaches 25% by 2027-03-31",
               "comparison": "occurs", "window_start": ASOF, "window_end": "2027-03-31", "kind": "hitting"}


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
    return store.add("Metric", {"id": mid, "name": "the US effective tariff rate", "unit": "pct", "kind": "level",
                                "level": {"value": 22, "source_id": source}, "resolution_source": "Yale Budget Lab",
                                **over})


def add_channel(store, name="SEMI", proxy="SOXX", order=6):
    return store.add("Factor", {"name": name, "proxy": proxy, "order": order})


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
        lambda: store.edit(t["id"], "range", [0.0, 1.0]),
        lambda: store.edit("portfolio", "g", [1.0]),
        lambda: store.link(u["id"], "implies", t["id"], {"check": 1.0}),
        lambda: store.link(t["id"], "asks", "market:kalshi:M1", {"forward": 1.0}),
        lambda: store.link(t["id"], "asks", "market:kalshi:M1", {"conditional_no": 0.1}),
        lambda: store.link(t["id"], "consequence", channel["id"], {"shock": stated(0.01), "levels": {"yes": 1.0}}),
        lambda: store.link("name:QQQ", "looks_through_to", "name:NVDA"),
        lambda: store.link("name:NVDA", "beta_to", channel["id"]),
        lambda: store.link(t["id"], "basis", "cmp:1"),
        lambda: store.link(metric["id"], "cites", metric["citations"]["level"]),
    ]
    for kind in ("Judgment", "Computation", "Source", "Market", "Name", "Note"):
        calls.append(lambda kind=kind: store.add(kind, {}))
    for call in calls:
        refused(store, call)
    # the cache keeps its rows as they were written: none is changed, and no Judgment is removed (a Computation goes
    # only by the rule of what the cache keeps, test_kept)
    store.link("portfolio", "watches", t["id"])
    store.link(t["id"], "asks", "market:kalshi:M1")
    store.get(t["id"])
    assert count(store, "computation") and count(store, "judgment")
    for statement in ("UPDATE computation SET output = '{}'", "UPDATE judgment SET answer = 0.99",
                      "DELETE FROM judgment"):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(statement)


def test_add_creates_no_judgment_computation_source_market_or_name(make_store):
    store = make_store(registry_=Registry())
    before = {t: count(store, t) for t in ("judgment", "computation", "source", "market")}
    add_portfolio(store)
    add_proposition(store)
    add_channel(store)
    assert {t: count(store, t) for t in before} == before
    assert {r["type"] for r in store.db.execute("SELECT type FROM object")} == {"Portfolio", "Proposition", "Factor"}


# ---------------------------------------------------------------- 2. citations, and refusals write nothing


def test_a_metric_the_analyst_adds_has_a_name_in_plain_words(make_store):
    """A metric is named as the page shows it; one added without a name is refused, and the name can be edited."""
    store = make_store()
    page = fetch_page(store)
    fields = {"id": "wti_usd", "unit": "USD", "kind": "price", "level": {"value": 70, "source_id": page}}
    refused(store, lambda: store.add("Metric", fields), contains="Metric requires name")
    metric = store.add("Metric", {**fields, "name": "the WTI price"})
    assert metric["stated"]["name"] == "the WTI price"
    assert store.edit(metric["id"], "name", "the price of WTI crude")["stated"]["name"] == "the price of WTI crude"


def test_a_cited_field_without_its_source_fails_and_writes_nothing(make_store):
    store = make_store()
    page = fetch_page(store)
    base = {"id": "wti_usd", "name": "the WTI price", "unit": "USD", "kind": "price"}
    refused(store, lambda: store.add("Metric", {**base, "level": 70}), contains="fact about the world")
    refused(store, lambda: store.add("Metric", {**base, "level": {"value": 70}}))
    refused(store, lambda: store.add("Metric", {**base, "level": {"value": 70, "source_id": "src:99"}}),
            contains="not a fetched page")
    refused(store, lambda: store.add("Metric", {**base, "unit": {"value": "USD", "source_id": page}}),
            contains="takes no source")
    metric = store.add("Metric", {**base, "level": {"value": 70, "source_id": page}})
    assert metric["citations"] == {"level": page}
    refused(store, lambda: store.edit(metric["id"], "level", 74.2), contains="takes a source_id")
    refused(store, lambda: store.edit(metric["id"], "unit", "USD/bbl", source_id=page), contains="takes no source")


def test_a_metric_stands_without_a_level_and_its_asof_is_the_date_its_level_stands_at(make_store):
    """ONTOLOGY.md 2.1: a metric's level is given where it has one, with the page it was read from; asof is the date
    that level stands at, a plain date the level's page covers, so it takes no source of its own."""
    store = make_store()
    page = fetch_page(store)
    base = {"id": "cms_rate_pct", "name": "the 2028 Medicare Advantage rate change", "unit": "percent",
            "kind": "level", "resolution_source": "CMS"}
    bare = store.add("Metric", base)  # a figure not yet published: no level, and propositions can be decided on it
    assert "level" not in bare["stated"] and "asof" not in bare["stated"] and not bare.get("citations")
    store.add("Proposition", {**PROPOSITION, "metric": bare["id"], "comparison": ">=", "value": 4.0})
    given = store.add("Metric", {**base, "id": "tariff_pct", "level": {"value": 22.0, "source_id": page},
                                 "asof": "2026-10-30"})
    assert given["stated"]["asof"] == "2026-10-30" and given["citations"] == {"level": page}
    refused(store, lambda: store.add("Metric", {**base, "id": "other_pct", "asof": {"value": "2026-10-30",
                                                                                   "source_id": page}}),
            contains="takes no source")
    refused(store, lambda: store.add("Metric", {**base, "id": "other_pct", "asof": "October 30"}))
    assert store.edit(given["id"], "asof", "2026-11-06")["stated"]["asof"] == "2026-11-06"
    refused(store, lambda: store.edit(given["id"], "asof", "2026-11-07", source_id=page), contains="takes no source")
    assert store.edit(bare["id"], "level", 3.7, source_id=page)["stated"]["level"] == 3.7


def test_any_refused_call_writes_nothing(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store, holdings=[{"ticker": "QQQ", "weight": 0.4}], total=100000)
    metric = add_metric(store)
    t = add_proposition(store, metric=metric["id"], comparison=">=", value=25)
    store.link("portfolio", "watches", t["id"])
    channel = add_channel(store)
    page = fetch_page(store, url="https://example.org/unused")
    calls = [
        lambda: store.add("Proposition", {**PROPOSITION, "comparison": ">="}),  # a comparison needs metric and value
        lambda: store.add("Proposition", {**PROPOSITION, "window_start": "2027-04-01"}),
        lambda: store.add("Proposition", {**PROPOSITION, "metric": "us_effective_tariff_rate_pct",
                                          "comparison": ">=", "value": 25}),  # names the metric by its bare id
        lambda: store.add("Nonsense", {}),
        lambda: store.add("Factor", {"name": "SEMI", "proxy": "SMH", "order": 7}),
        lambda: store.add("Factor", {"name": "SEMI2", "proxy": "SMH", "order": 6, "carries": "x"}),
        lambda: store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct"}),
        lambda: store.link("portfolio", "watches", t["id"]),  # a second identical link
        lambda: store.link(t["id"], "implies", t["id"]),
        lambda: store.link(t["id"], "asks", "market:kalshi:NOPE"),  # the venue lists no such market
        lambda: store.link(t["id"], "asks", "market:nasdaq:X"),
        lambda: store.link(t["id"], "evidence", "src:1", {"passages": ["x"]}),  # no page src:1 was fetched
        lambda: store.link(t["id"], "watches", "portfolio"),
        lambda: store.link("portfolio", "holds", "name:SPY"),  # a holding gives one amount
        lambda: store.link(metric["id"], "quotes", channel["id"], {"duration": 17.0}),  # quotes states nothing
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
        lambda: store.record_search("search_markets", {"text": " "}, [], query=" "),
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
    t = add_proposition(store, metric=metric["id"], comparison=">=", value=25)
    q = store.link(t["id"], "asks", "market:kalshi:M1")
    store.link("portfolio", "watches", t["id"])
    store.get(t["id"])

    e = refused(store, lambda: store.remove("name:AAPL"), contains="derived")
    e = refused(store, lambda: store.remove("name:QQQ"), contains="derived")
    assert e.dependents == ["link:1"]  # the holds link
    refused(store, lambda: store.remove("looks_through_to:name:QQQ>name:AAPL"), contains="derived")
    e = refused(store, lambda: store.remove("metric:price:SOXX"), contains="derived")
    assert e.dependents == [channel["id"]]
    e = refused(store, lambda: store.remove("market:kalshi:M1"), contains="venue")
    assert e.dependents == [q["id"]]
    e = refused(store, lambda: store.remove(page), contains="cited")
    assert e.dependents == [f"{metric['id']}.level"]
    citation = store.db.execute("SELECT id FROM link WHERE relation = 'cites'").fetchone()[0]
    e = refused(store, lambda: store.remove(citation), contains="keeps its source")
    assert e.dependents == [f"{metric['id']}.level"]
    e = refused(store, lambda: store.remove(metric["id"]), contains="named by")
    assert e.dependents == [f"{t['id']}.metric"]
    kept = store.get(t["id"])["derived"]["range"]["basis"]  # a Computation the cache keeps
    refused(store, lambda: store.remove(kept), contains="follows from structure")
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
    m1 = store.link(t["id"], "asks", "market:kalshi:M1")
    before_t, before_l, before_m1 = store.get(t["id"]), store.get(level["id"]), store.get(m1["id"])
    assert before_t["derived"]["range"]["value"][0] == 0.28 and before_t["derived"]["range"]["value"][1] == 0.33
    assert [before_m1["derived"][k]["value"] for k in ("bid", "ask")] == [0.28, 0.33]

    m3 = store.link(level["id"], "asks", "market:kalshi:M3")
    during = store.get(t["id"])["derived"]
    assert during["range"]["value"][0] == 0.30 and during["range"]["basis"] != before_t["derived"]["range"]["basis"]
    assert during["probability"]["basis"] != before_t["derived"]["probability"]["basis"]

    asked, computations, judgments = len(judge.calls), count(store, "computation"), count(store, "judgment")
    record = store.remove(m3["id"])
    assert record["removed"]["id"] == m3["id"]
    assert [r["id"] for r in record["released"]] == ["market:kalshi:M3"]
    assert masked(store.get(t["id"])["derived"]) == masked(before_t["derived"])
    assert masked(store.get(level["id"])["derived"]) == masked(before_l["derived"])
    assert masked(store.get(m1["id"])["derived"]) == masked(before_m1["derived"])
    assert len(judge.calls) == asked and count(store, "judgment") == judgments  # the judge is not asked again
    # T's range as it stood with M3 linked was replaced, and nothing names it: the cache no longer holds it
    assert store.type_of(during["range"]["basis"]) is None and computations


def test_a_listing_abroad_whose_symbol_carries_an_ampersand_is_a_name_the_look_through_reaches(make_store):
    """ONTOLOGY.md 2; MATH.md 3.1: a listing abroad is named by its exchange's own symbol as its market writes it,
    Mahindra & Mahindra on the NSE as M&M.NS, so a Name id holds every character a symbol's form can carry
    (series.SYMBOL), and a fund's row that reaches it reads."""
    from harness import schema
    from harness.derive import series
    for symbol in ("M&M.NS", "L&TFH.NS", "J&KBANK.NS", "BAJAJ-AUTO.NS", "NOVO-B.CO", "0700.HK", "005930.KS"):
        assert series.SYMBOL.match(symbol) and schema.NAME_ID_RE.match(f"name:{symbol}"), symbol
    store = make_store(registry_=Registry([fx_lookthrough()]))
    store.put_data("constituents", "name:QQQ", {"M&M.NS": 0.1, "NVDA": 0.08}, {"endpoint": "/constituents"},
                   retrieved_at=RETRIEVED)
    add_portfolio(store)
    got = store.get("name:M&M.NS")
    assert got["derived"]["exposure"]["value"] == pytest.approx(4000.0)
    assert store.get("looks_through_to:name:QQQ>name:M&M.NS")["derived"]["weight"]["value"] == 0.1


# ---------------------------------------------------------------- 6. a changed input re-derives; an unchanged one hits


def test_a_changed_input_re_derives_and_an_unchanged_one_hits_the_cache(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store)
    t = add_proposition(store)
    other = add_proposition(store, title="other")
    store.link("portfolio", "watches", t["id"])
    store.link(t["id"], "asks", "market:kalshi:M1")
    first = store.get(t["id"])["derived"]
    rows, asked = count(store, "computation"), len(judge.calls)

    assert store.get(t["id"])["derived"] == first
    store.edit(other["id"], "title", "another")
    assert store.get(t["id"])["derived"] == first
    assert (count(store, "computation"), len(judge.calls)) == (rows, asked)

    store.edit(t["id"], "text", "The US effective tariff rate reaches 25% at any point by 2027-03-31")
    changed = store.get(t["id"])["derived"]
    assert changed["range"] == first["range"]  # the interval does not read the text
    assert changed["probability"]["basis"] != first["probability"]["basis"] and len(judge.calls) == asked + 1
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
    assert aapl["because"] == ["looked through"] and aapl["derived"]["exposure"]["value"] == pytest.approx(2800.0)
    assert store.get("name:NVDA")["derived"]["exposure"]["value"] == pytest.approx(15000.0 + 3200.0)
    named = store.note("AAPL is in QQQ", ["name:AAPL"])

    holding = next(n["link"] for n in store.neighbors("portfolio", "holds") if n["object"]["id"] == "name:QQQ")
    store.edit(holding, "dollars", 50000.0)
    assert {n["id"] for n in store.query("Name")} == names
    assert {n["link"] for n in store.neighbors("name:QQQ", "looks_through_to")} == through
    again = store.get("name:AAPL")
    assert again["derived"]["exposure"]["value"] == pytest.approx(3500.0)
    assert again["derived"]["exposure"]["basis"] != aapl["derived"]["exposure"]["basis"]
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
    assert store.get(unused)["kept"] is False  # held across the restart under its id, and still not kept
    t = add_proposition(store)
    store.link(t["id"], "evidence", used, {"passages": ["22 percent"]})
    note = store.note("why this page matters", [used])
    second = fetch_page(store, url="https://example.org/second")
    store.edit(metric["id"], "level", 23, source_id=second)
    assert store.get(used)["kept"] is True  # still evidence for t
    record = store.remove(next(link["id"] for link in store.get(t["id"])["links"] if link["relation"] == "evidence"))
    assert [r["id"] for r in record["released"]] == [used]
    page = store.get(used)
    assert page["kept"] is False and page["url"] == "https://example.org/used"  # again a fetched page, held
    assert store.get(note["id"])["links"] == []  # the note's about link went with the page
    assert store.get(second)["cited_for"] == [f"{metric['id']}.level"]
    assert fetch_page(store, url="https://example.org/used") == used  # held: not fetched again
    store.link(t["id"], "evidence", used, {"passages": ["22 percent"]})  # and citable under the same id
    assert store.get(used)["kept"] is True
    store.close()


def test_a_page_whose_last_use_goes_is_again_a_fetched_page_citable_under_its_id(make_store):
    """A page cited for a consequence's figure, whose figure then rests on a note instead, is not kept;
    it is a fetched page again, and linking it as evidence keeps it under the same id."""
    store = make_store("scratch-release")
    market = add_channel(store, "the market", "VTI", 1)
    t = add_proposition(store, baseline="no")
    page = fetch_page(store, url="https://example.org/episode", text="Stocks fell 12 percent in that episode.")
    c = store.link(t["id"], "consequence", market["id"], {"shock": {"value": -0.12, "source_id": page,
                                                                    "passages": ["fell 12 percent"]}})
    assert store.get(page)["kept"] is True
    store.edit(c["id"], "shock", stated(-0.15))  # the figure now rests on a note
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
    assert store.get(other)["kept"] is False and count(store, "source") == 1  # the episode page, kept as evidence
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
    q = store.link(t["id"], "asks", "market:kalshi:M1")
    assert [store.get(q["id"])["derived"][k]["value"] for k in ("bid", "ask")] == [0.28, 0.33]
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
    q = store.link(t["id"], "asks", "market:kalshi:M1")
    assert venues.calls == ["market:kalshi:M1"] and books.calls == ["market:kalshi:M1"]
    assert [store.get(q["id"])["derived"][k]["value"] for k in ("bid", "ask")] == [0.28, 0.33]
    assert [d["kind"] for d in store.get("market:kalshi:M1")["data"]] == ["market", "book"]


# ---------------------------------------------------------------- the rules across objects


def test_a_watched_window_stays_within_the_horizon(make_store):
    store = make_store()
    add_portfolio(store)
    late = add_proposition(store, window_end="2027-06-01")
    e = refused(store, lambda: store.link("portfolio", "watches", late["id"]), contains="after the horizon's end, 2027-05-03")
    assert e.dependents == [late["id"]]
    t = add_proposition(store)
    store.link("portfolio", "watches", t["id"])
    refused(store, lambda: store.edit(t["id"], "window_end", "2027-05-04"), contains="horizon")
    refused(store, lambda: store.edit("portfolio", "horizon_days", 120), contains="horizon")
    store.edit("portfolio", "horizon_days", 210)  # to 2027-06-02
    store.link("portfolio", "watches", late["id"])


def test_removing_a_factor_takes_the_shocks_stated_on_it_with_their_reasoning(make_store):
    """A consequence runs to the factor whose price it moves (MATH.md 1.3; ONTOLOGY.md 2.2), once for each event or way.
    Removing the factor removes the consequences that run to it, with the note each was stated with, and releases the
    page one cited (2.3); another factor's shock stays."""
    store = make_store()
    oil = add_channel(store, "OIL", "USO", 5)
    semi = add_channel(store)
    event = add_proposition(store, baseline="no")
    page = fetch_page(store, url="https://example.org/oil", text="Crude fell a fifth in the last episode.")
    on_oil = store.link(event["id"], "consequence", oil["id"], {"shock": {"value": -0.2, "source_id": page,
                                                                          "passages": ["Crude fell a fifth"]}})["id"]
    other = add_proposition(store, title="Another event", baseline="no")
    reasoned = store.link(other["id"], "consequence", oil["id"], {"shock": stated(0.05)})["id"]
    note = store.stated_of(reasoned)["shock"]["note"]
    kept = store.link(event["id"], "consequence", semi["id"], {"shock": stated(-0.1)})["id"]
    refused(store, lambda: store.link(event["id"], "consequence", oil["id"], {"shock": stated(0.01)}),
            contains="edit it instead")
    record = store.remove(oil["id"])
    assert {d["id"] for d in record["cascaded"]} >= {on_oil, reasoned}
    assert store.type_of(note) is None and store.get(page)["kept"] is False
    assert [link["id"] for link in store.links_of(event["id"], "consequence")] == [kept]


def test_a_consequence_states_its_shock(make_store):
    store = make_store()
    wti = add_channel(store, "OIL", "USO", 5)
    event = add_proposition(store, baseline="no")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"]), contains="consequence requires shock")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"],
                                      {"shock": stated({"value": 0.05, "against": "today"})}),
            contains="against is `baseline`, the whole move, or `what_it_follows`, the residual")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"shock": "five"}))
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"shock": stated(0.05), "meaning": "gap"}),
            contains="'meaning' is not one")
    refused(store, lambda: store.link(event["id"], "consequence", wti["id"], {"shock": 0.05}),
            contains="shock is stated with its basis")
    lid = store.link(event["id"], "consequence", wti["id"], {"shock": stated(0.05)})["id"]
    shock = store.get(lid)["stated"]["shock"]
    assert shock["value"] == 0.05 and store.stated_of(shock["note"]) == {"text": stated(0.05)["note"]}
    refused(store, lambda: store.edit(lid, "shock", 6.0, "src:1"), contains="its basis is stated in its value")
    store.edit(lid, "shock", stated(-0.03))
    assert store.get(lid)["stated"]["shock"]["value"] == -0.03
    p = add_proposition(store, title="P")["id"]
    refused(store, lambda: store.link(event["id"], "consequence", p, {"shock": stated(0.01)}),
            contains="runs to Factor or Name")
    rate = add_metric(store)
    refused(store, lambda: store.link(event["id"], "consequence", rate["id"], {"shock": stated(0.01)}),
            contains="runs to Factor or Name")


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
    add_portfolio(store, holdings=[{"ticker": "qqq", "weight": 0.4}, {"cash": 60000.0}],
                  total=100000)
    holding = store.neighbors("portfolio", "holds")[0]["link"]
    assert store.get(holding)["to"] == "name:QQQ"
    store.edit(holding, "dollars", 41000.0)
    assert store.get(holding)["stated"] == {"dollars": 41000.0, "ticker": "QQQ"}
    t = add_proposition(store)
    q = store.link(t["id"], "asks", "market:kalshi:M1")
    assert q["stated"] == {"side": "yes", "taken": {"value": True}}  # linking takes the market's price (MATH.md 1.2)
    flipped = store.edit(q["id"], "side", "no")
    assert [flipped["derived"][k]["value"] for k in ("bid", "ask")] == [pytest.approx(0.67), pytest.approx(0.72)]
    refused(store, lambda: store.edit(q["id"], "forward", 1.0), contains="derived")
    refused(store, lambda: store.edit(q["id"], "side", "inverse"))


# ---------------------------------------------------------------- records, the cache, and the layout


def test_searches_are_recorded_on_their_proposition_and_go_with_it(make_store):
    store = make_store()
    t = add_proposition(store)
    s1 = store.record_search("search_markets", {"text": "tariff"}, ["market:kalshi:M1"],
                             proposition_id=t["id"], query="tariff", call=3)
    s2 = store.record_search("search_web", {"allowed_domains": ["budgetlab.yale.edu"]},
                             [{"url": "https://budgetlab.yale.edu/x", "title": "x", "date": "2026-10-30"}],
                             proposition_id=t["id"], query="effective tariff rate trajectory")
    store.record_search("search_markets", {"text": "oil"}, [], query="oil")
    searches = store.get(t["id"])["searches"]
    assert [s["id"] for s in searches] == [s1, s2]
    assert searches[0]["query"] == "tariff" and searches[0]["returned"] == 1 and searches[0]["uses"] == []
    assert store.get(s1)["results"] == ["market:kalshi:M1"]  # read by the search's id
    assert count(store, "market") == 0  # search results are not kept
    record = store.remove(t["id"])
    assert record["searches"] == [s1, s2]
    assert count(store, "search") == 1


def test_an_input_no_client_here_reads_is_absent_and_read_once_one_is(make_store):
    store = make_store(venues=StubVenues({"market:kalshi:M1": {}}))
    t = add_proposition(store)
    q = store.link(t["id"], "asks", "market:kalshi:M1")
    rows = count(store, "computation")
    derived = store.get(t["id"])["derived"]
    assert not {"range", "coverage"} & set(derived)  # absent: not there, with no reason
    assert count(store, "computation") == rows  # an absent value is not cached
    store.fetchers["book"] = BookFetcher({"market:kalshi:M1": {"bid": 0.4, "ask": 0.5}})
    store.edit(q["id"], "side", "yes")  # any write; the next read tries again
    assert store.get(t["id"])["derived"]["range"]["value"][0] == 0.4


def test_a_basis_chain_opens_to_its_inputs(make_store):
    store, judge, venues, books = market_store(make_store)
    add_portfolio(store)
    t = add_proposition(store)
    store.link("portfolio", "watches", t["id"])
    store.link(t["id"], "asks", "market:kalshi:M1")
    point = store.get(t["id"])["derived"]["probability"]
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
    channels = {o["stated"]["name"]: o["id"] for o in store.query("Factor")}
    assert set(channels) == {"MKT", "OIL"} and all(store.get(c)["origin"] == "initial" for c in channels.values())
    assert {o["id"] for o in store.query("Metric")} == {"metric:price:SPY", "metric:price:USO"}  # derived prices
    record = store.remove(channels["OIL"])  # the initial contents are the analyst's to remove
    assert record["removed"]["id"] == channels["OIL"] and store.type_of("metric:price:USO") is None


def test_initial_contents_that_break_a_rule_load_nothing(make_store, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "channels.yaml").write_text((FIXTURES / "initial" / "channels.yaml").read_text() +
                                       "  - name: MKT\n    proxy: VTI\n    order: 9\n")
    store = make_store("scratch-bad")
    before = snapshot(store)
    with pytest.raises(StoreError, match="each factor's name is its own"):
        store.load_initial(bad)
    assert snapshot(store) == before


def test_a_rolled_back_call_leaves_a_fetched_page_held(make_store):
    store = make_store()
    page = fetch_page(store)
    refused(store, lambda: store.add("Metric", {"id": "x_pct", "name": "x", "unit": "pct", "level":
                                                {"value": 1, "source_id": page}, "kind": "rate"}))
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
    search = store.record_search("search_markets", {"text": "tariff"}, [], proposition_id=t["id"], query="tariff")
    store.link(t["id"], "asks", "market:kalshi:M1")
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
    with pytest.raises(StoreError, match="requires proxy"):
        Store.create(tmp_path / "scratch-failed", initial=bad)
    assert not (tmp_path / "scratch-failed" / "store.sqlite").exists()


def test_each_produced_field_carries_the_basis_of_its_own_computation(make_store):
    def inputs(ctx, subject):
        return {"channels": [c["id"] for c in ctx.links("portfolio", "holds")]}

    def compute(m, load):
        return {"objects": {"name:QQQ": {"type": "Name", "fields": {"betas": [1.0, 0.2]}}},
                "links": {"beta_to:name:QQQ>factor:1": {"relation": "beta_to", "from": "name:QQQ",
                                                          "to": "factor:1", "fields": {"beta": 1.0}}}}

    betas = Derivation("fx_betas", 1, "Portfolio", inputs, compute, produces=("Name", "beta_to"))
    store = make_store(registry_=Registry([fx_lookthrough(), betas]))
    add_channel(store, "MKT", "SPY", 1)
    add_portfolio(store)
    derived = store.get("name:QQQ")["derived"]
    assert derived["exposure"]["value"] == 40000.0 and derived["betas"]["value"] == [1.0, 0.2]
    e_basis, b_basis = store.get(derived["exposure"]["basis"]), store.get(derived["betas"]["basis"])
    assert (e_basis["derivation"], b_basis["derivation"]) == ("fx_lookthrough", "fx_betas")
    loads = store.get("beta_to:name:QQQ>factor:1")
    assert loads["derived"]["beta"] == {"value": 1.0, "basis": b_basis["id"]}
    assert [n["link"] for n in store.neighbors("factor:1", "beta_to")] == ["beta_to:name:QQQ>factor:1"]


def test_clearing_a_field_with_a_default_restores_the_default(make_store):
    store = make_store()
    add_portfolio(store, horizon_days=90)
    assert store.edit("portfolio", "horizon_days", None)["stated"]["horizon_days"] == 180


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
        lambda: store.record_search("search_markets", {"text": "tariff", "next": "https://x.org/m?page=2&apikey=zz99"},
                                    [], proposition_id=t["id"], query="tariff"),
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
    from harness.derive.engine import Waiting
    active, peak, asked = [0], [0], []
    lock, beside = threading.Lock(), threading.Event()

    def fetch(subject, covering):  # each answers once another is in flight beside it: one at a time, none would
        with lock:
            asked.append(subject)
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            if active[0] > 1:
                beside.set()
        assert beside.wait(10), "the fetches ran one at a time"
        with lock:
            active[0] -= 1
        if subject == "BAD":
            raise RuntimeError("the source answered 500")
        return {"payload": {"ticker": subject}, "request": {"url": f"https://example.org/{subject}"},
                "retrieved_at": "2026-11-04T21:00:00+00:00", "range": ("2026-11-04", "2026-11-04")}

    store = make_store(fetchers={"ticker_ref": fetch})
    store.put_data("ticker_ref", "HELD", {"ticker": "HELD"}, {"url": "x"}, retrieved_at="2026-11-04T21:00:00+00:00",
                   range=("2026-11-04", "2026-11-04"))
    kept = store.prefetch("ticker_ref", ["A", "B", "HELD", "BAD", "C", "D"], ("2026-11-04", "2026-11-04"))
    assert kept == 4 and peak[0] > 1  # side by side
    assert "HELD" not in asked  # what the store holds is not fetched again
    rows = [r[0] for r in store.db.execute("SELECT subject FROM data WHERE kind = 'ticker_ref' ORDER BY rowid")]
    assert rows == ["HELD", "A", "B", "C", "D"]
    with pytest.raises(Waiting, match="could not be read: the source answered 500"):
        store.data("ticker_ref", "BAD", covering=("2026-11-04", "2026-11-04"))  # read again: it waits, its cause logged


def test_a_call_that_stands_aside_finds_again_what_its_first_attempt_worked_out(make_store):
    """TOOLS.md 5: a call that stands aside for what is under way rolls its attempt back, writing nothing of what it
    stated; what it read and what it worked out are kept, each Computation under its id, which the Computations after
    it name, so the call made again finds them by their inputs and goes on from there."""
    computed = []

    def words(m, load):
        computed.append("words")
        return {"n": len(m["text"])}

    def twice(m, load):
        computed.append("twice")
        return {"t": 2 * load(m["w"])["n"]}

    registry_ = Registry([Derivation("words", 1, "Note", lambda ctx, n: {"text": ctx.object(n)["stated"]["text"]}, words),
                          Derivation("twice", 1, "Note", lambda ctx, n: {"w": ctx.value("words", n, ref=True)}, twice)])
    store = make_store(registry_=registry_)
    note = store.note("abc")["id"]
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.edit(note, "text", "abcdef")
            first = store.engine.value("twice", note)
            assert first.output == {"t": 12} and computed == ["words", "twice"]
            raise RuntimeError("it stood aside")
    assert store.stated_of(note)["text"] == "abc"  # what it stated is undone
    assert store.computation(first.basis)["output"] == {"t": 12}  # what it worked out is kept, under its id
    computed.clear()
    with store.transaction():
        store.edit(note, "text", "abcdef")
        again = store.engine.value("twice", note)
    assert again.basis == first.basis and again.output == {"t": 12} and computed == []
    assert store.note("next")["id"]  # ids go on past the kept ones
    assert count(store, "computation") == len({r[0] for r in store.db.execute("SELECT id FROM computation")})


def test_the_last_computation_of_a_value_is_read_whatever_the_store_holds_now_and_never_waits_on_its_lock(make_store):
    """A value's last Computation, by its derivation and subject, is what the page shows while the value is worked out
    again: read on the reader's own connection, it answers at once while a call holds the store's lock with a newer
    Computation not yet committed, with the one last committed; once the call commits, the newer."""
    import threading
    store = make_store()
    first = store.put_computation("price", 7, "name:NVDA", "h1", {}, {"prices": {"price": 180.0}})
    assert store.last_computation("price", "name:NVDA") == {
        "id": first, "derivation": "price", "subject": "name:NVDA", "version": 7, "output": {"prices": {"price": 180.0}},
        "created_at": store.computation(first)["created_at"]}
    assert store.last_computation("price", "name:AMD") is None  # never worked out
    entered, release, got = threading.Event(), threading.Event(), {}

    def call():  # a call holding the store's lock, its write open, works the value out again
        with store.lock, store.transaction():
            store.put_computation("price", 7, "name:NVDA", "h2", {}, {"prices": {"price": 181.0}})
            entered.set()
            release.wait(10)

    held = threading.Thread(target=call)
    held.start()
    assert entered.wait(10)
    reader = threading.Thread(target=lambda: got.update(last=store.last_computation("price", "name:NVDA")))
    reader.start()
    reader.join(5)
    assert not reader.is_alive() and got["last"]["id"] == first  # at once, the value last committed
    release.set()
    held.join(10)
    assert store.last_computation("price", "name:NVDA")["output"] == {"prices": {"price": 181.0}}


def test_a_values_answer_is_the_computation_the_engine_last_gave_or_none_and_the_cache_keeps_it(make_store):
    """A value's answer is what the engine last gave as it: a Computation found again by its inputs' hash,
    though a later one of the value exists, is the answer, which the cache keeps and the one it replaced goes; a value
    the engine reads absent answers none, with why; an answer given inside a write that rolls back goes with it, while
    a Computation made there is kept by its content, for the call made again to find."""
    store = make_store()
    back = store.put_computation("price", 7, "name:NVDA", "h1", {}, {"prices": {"price": 180.0}})
    store.name({"rests on": back})  # named by the conversation: kept when replaced
    moved = store.put_computation("price", 7, "name:NVDA", "h2", {}, {"prices": {"price": 181.0}})
    assert store.last_computation("price", "name:NVDA")["id"] == moved
    with store.lock:
        store.answered("price", "name:NVDA", back)  # the mark returns to its earlier reading: found by its hash
    assert store.last_computation("price", "name:NVDA")["id"] == back
    assert store._one("SELECT 1 FROM computation WHERE id = ?", moved) is None  # replaced, and nothing keeps it
    with store.lock:
        store.answered("price", "name:NVDA", back)  # the same answer: nothing written
        store.answered("price", "name:NVDA", None, "NVDA has no price on or before 2026-11-04")
    assert store.last_computation("price", "name:NVDA") == {
        "id": None, "derivation": "price", "subject": "name:NVDA", "absent": "NVDA has no price on or before 2026-11-04"}
    assert store._one("SELECT 1 FROM computation WHERE id = ?", back) is not None  # named: it stays
    with pytest.raises(RuntimeError):
        with store.lock, store.transaction():
            again = store.put_computation("price", 7, "name:NVDA", "h3", {}, {"prices": {"price": 182.0}})
            store.answered("price", "name:AMD", again)
            raise RuntimeError("the call stood aside")
    assert store.last_computation("price", "name:NVDA")["absent"]  # the answers are as they were before the write
    assert store.last_computation("price", "name:AMD") is None
    assert store.computation_by_hash("price", "h3")["id"] == again  # kept by its content, for the call made again


def test_a_value_worked_out_around_a_wait_is_kept_by_its_content_and_is_not_the_values_answer(make_store):
    """A Computation worked out from an input held at an earlier moment (put with answer=False) is kept, so a
    figure read from it opens to it, and is no answer: the value's answer and its Computation stay as they were. The
    value's next answer, worked out again or found by its inputs' hash, replaces it, and it goes."""
    store = make_store()
    with store.lock:
        answer = store.put_computation("top", 1, "portfolio", "h1", {}, {"value": 1.0})
        held = store.put_computation("top", 1, "portfolio", "h2", {}, {"value": 1.5}, answer=False)
    assert store.last_computation("top", "portfolio")["id"] == answer  # the answer the engine last gave stands
    assert store.get(held)["output"] == {"value": 1.5} and store.get(answer)["output"] == {"value": 1.0}  # both kept
    with store.lock:
        store.answered("top", "portfolio", answer)  # the same answer found again by its hash: nothing changes
    assert store._one("SELECT 1 FROM computation WHERE id = ?", held) is not None
    with store.lock:
        again = store.put_computation("top", 1, "portfolio", "h3", {}, {"value": 2.0})  # the value answers anew
    assert store.last_computation("top", "portfolio")["id"] == again
    assert store._one("SELECT 1 FROM computation WHERE id IN (?, ?)", held, answer) is None  # replaced, nothing keeps them
    with store.lock:
        aside = store.put_computation("top", 1, "portfolio", "h4", {}, {"value": 2.5}, answer=False)
        store.answered("top", "portfolio", None, "the portfolio's holdings are not read")  # answered absent
    assert store._one("SELECT 1 FROM computation WHERE id IN (?, ?)", aside, again) is None


def test_a_store_made_before_values_had_answers_takes_each_values_latest_as_its_answer(tmp_path):
    from harness.derive import default_registry
    store = Store.create(tmp_path / "scratch-answers", registry=default_registry())
    first = store.put_computation("price", 7, "name:NVDA", "h1", {}, {"prices": {"price": 180.0}})
    latest = store.put_computation("price", 7, "name:NVDA", "h2", {}, {"prices": {"price": 181.0}})
    with store.transaction():
        store.db.execute("DROP TABLE answer")
    store.close()
    store = Store.open(tmp_path / "scratch-answers", registry=default_registry())
    assert store.last_computation("price", "name:NVDA")["id"] == latest
    assert store._one("SELECT count(*) AS n FROM answer")["n"] == 1 and first != latest
    store.close()
