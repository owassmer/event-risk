"""The first read (TOOLS.md 5): add(Portfolio) answers with what the portfolio owns once its own reads are in, and the
exposures, which nothing in that answer reads, are computed after it, on the page's thread or by the first call that
needs them. A call that needs them and arrives while they are under way waits for them and answers with them, and
nothing is computed twice; where a read they need has failed, it fails with nothing written and can be made again,
while every call that does not need them answers. The channels' estimability is still decided at the portfolio's
commit. Every test runs with no keys and no network, over synthetic Massive data."""

from __future__ import annotations

import re

import json
import threading

import numpy as np
import pytest

from conftest import fixture_judge
from harness.derive import default_registry
from harness.server import exposures
from harness.store import Store
from harness.tools import Tools
from harness.venues import Venues
from harness.world import World
from massive_data import ASOF, KNOWLEDGE, SESSIONS, closes_from, put_bars, put_market, put_reference, random_returns
from test_analyst import Data
from breakdown import stated

HOLDINGS = [{"ticker": "NVDA", "dollars": 60000.0}, {"cash": 40000.0}]
EVENT = {"title": "SPY ends March at or below $500", "text": "SPY closes at or below $500 on March 31, 2027",
         "metric": "metric:price:SPY", "comparison": "<=", "value": 500.0, "window_end": "2027-03-31",
         "kind": "level_at_date", "chances": [stated(0.2)], "baseline": "no"}
# Each call whose answer reads the exposures, as the analyst makes it once the portfolio and its event are in: what
# the exposures are now, and the event as it now stands; and a write that changes what they are, whose answer needs a
# fit that does not exist when its wait begins. Every call reaches its wait by one path (Tools.call).
NEEDS = {
    "exposure": ("exposure", {}),
    "a consequence": ("link", {"from_id": "prop:1", "relation": "consequence", "to_id": "factor:1",
                               "fields": {"shock": stated(-0.05)}}),
    "a channel's add": ("add", {"type": "Factor", "fields": {"name": "Long Treasury bonds", "proxy": "TLT",
                                                              "order": 2}}),
}


@pytest.fixture
def first(tmp_path):
    """A store over synthetic Massive data, the market's proxies and NVDA, with its tools."""
    rng = np.random.default_rng(29)
    source = Store.create(tmp_path / "massive-data")
    put_market(source, rng)
    put_reference(source, "TLT", "ETF", name="iShares 20+ Year Treasury Bond ETF")
    put_reference(source, "NVDA", "CS", sic="3674", name="NVIDIA Corp")
    put_bars(source, "NVDA", closes_from(random_returns(rng, 0.02)))
    data = Data(source)
    store = Store.create(tmp_path / "scratch-first-read", initial=KNOWLEDGE, registry=default_registry(),
                         judge=fixture_judge(), fetchers=data.fetchers())
    tools = Tools(World(store, Venues({}), today=lambda: ASOF))
    yield {"store": store, "tools": tools, "data": data, "source": source}
    store.close()
    source.close()


def standing_aside(monkeypatch) -> threading.Event:
    """Set once a call stands aside for a read it needs, as it begins to wait for it (Tools.call), holding no lock."""
    import harness.tools
    aside, real = threading.Event(), harness.tools.wait

    def wait(fs, *a, **kw):
        aside.set()
        return real(fs, *a, **kw)

    monkeypatch.setattr(harness.tools, "wait", wait)
    return aside


def held_up(store: Store, kind: str, subject: str, *, fails: bool = False, gate: threading.Event | None = None,
            entered: threading.Event | None = None) -> None:
    """The client's read of one subject made to fail past its retries, or held until `gate` opens."""
    real = store.fetchers[kind]

    def fetch(s, covering):
        if s == subject:
            if entered is not None:
                entered.set()
            if fails:
                raise TimeoutError("the source did not answer")
            if gate is not None:
                gate.wait(10)
        return real(s, covering)

    store.fetchers[kind] = fetch


def computed(store: Store, derivation: str) -> int:
    return store._one("SELECT count(*) AS n FROM computation WHERE derivation = ?", derivation)["n"]


def once(store: Store) -> bool:
    """No Computation computed twice: each derivation's inputs, hashed, kept once."""
    return store._one("SELECT count(*) AS n FROM (SELECT derivation, input_hash FROM computation "
                      "GROUP BY derivation, input_hash HAVING count(*) > 1)")["n"] == 0


def written(store: Store) -> list:
    return [tuple(r) for t in ("object", "link") for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")]


def first_read(tools: Tools) -> None:
    """The portfolio added, and an event watched: no call here reads the exposures."""
    ok, got = tools.call("add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": HOLDINGS}})
    assert ok and [h.get("ticker") or h.get("currency") for h in got["holdings"]] == ["NVDA", "USD"], got  # priced
    ok, got = tools.call("add", {"type": "Proposition", "fields": EVENT})
    assert ok and got["id"] == "prop:1", got
    ok, got = tools.call("link", {"from_id": "portfolio", "relation": "watches", "to_id": "prop:1"})
    assert ok, got


def answered_with_exposures(store: Store, call: str, out: dict) -> None:
    """The call's answer carries the exposures the store kept."""
    if call == "exposure":
        g = json.loads(store._one("SELECT output FROM computation WHERE derivation = 'g' ORDER BY rowid DESC LIMIT 1")
                       ["output"])["g"]
        assert {c["factor"]: c["dollar_beta"] for c in out["factors"]} == pytest.approx(g)
    elif call == "a consequence":
        assert out["events"][0]["factors"] and out["events"][0]["holdings"]
    else:
        assert out["dollar_beta"] is not None


def test_add_answers_with_what_it_owns_and_every_call_that_does_not_need_the_exposures_answers_while_they_wait(first):
    """NVDA's dividends, which its returns read and its price does not, cannot be read: add answers, and so does each
    call whose answer reads none of the exposures: a proposition's add, a note, a link that is not a consequence, a
    metric's and an unwatched proposition's get, a query of propositions and of names, and a proposition's
    neighbors. A watched event's get reads its full impact, and so its exposures."""
    store, tools = first["store"], first["tools"]
    held_up(store, "dividends", "NVDA", fails=True)
    first_read(tools)
    assert computed(store, "betas") == computed(store, "g") == computed(store, "volatility") == 0
    ok, got = tools.call("add", {"type": "Proposition", "fields": {**EVENT, "title": "A part of it"}})
    assert ok and got["id"] == "prop:2", got
    for name, given in (("note", {"text": "Why the event matters.", "about_ids": ["prop:1"]}),
                        ("link", {"from_id": "prop:2", "relation": "implies", "to_id": "prop:1"}),
                        ("get", {"id": "metric:price:SPY"}), ("get", {"id": "prop:2"}),
                        ("query", {"type": "Proposition"}), ("query", {"type": "Name"}),
                        ("neighbors", {"id": "prop:1"})):
        ok, out = tools.call(name, given)
        assert ok, (name, given, out)
    assert computed(store, "betas") == 0


@pytest.mark.parametrize("call", NEEDS)
def test_a_call_that_needs_the_exposures_fails_where_a_read_they_need_has_failed_and_can_be_made_again(first, call):
    """NVDA's dividends cannot be read: the call fails, with nothing written and nothing the analyst must act on;
    once the read lands, the same call answers with the exposures."""
    store, tools = first["store"], first["tools"]
    real = store.fetchers["dividends"]
    held_up(store, "dividends", "NVDA", fails=True)
    first_read(tools)
    before = written(store)
    name, given = NEEDS[call]
    ok, out = tools.call(name, given)
    assert not ok and out["dependents"] == [] and re.fullmatch(  # the reading named by what it reads, nothing more
        r"The dividends of NVDA from \d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d could not be read just now\. Nothing was "
        r"written, and the call can be made again\.", out["error"])
    assert written(store) == before
    store.fetchers["dividends"] = real
    ok, out = tools.call(name, given)
    assert ok, out
    answered_with_exposures(store, call, out)


@pytest.mark.parametrize("call", NEEDS)
def test_a_call_that_arrives_while_the_exposures_are_read_waits_for_them_and_answers_with_them(first, call, monkeypatch):
    """add answers while NVDA's dividends are still being read; the page's thread waits for that read to compute the
    exposures, and the call, made meanwhile, waits for it too and answers with them once it lands: one read, and
    nothing computed twice."""
    store, tools, data = first["store"], first["tools"], first["data"]
    gate, entered = threading.Event(), threading.Event()
    held_up(store, "dividends", "NVDA", gate=gate, entered=entered)
    first_read(tools)
    page = threading.Thread(target=exposures, args=(store,))
    page.start()
    assert entered.wait(10)  # the read is under way
    aside = standing_aside(monkeypatch)
    answer: dict = {}
    name, given = NEEDS[call]
    caller = threading.Thread(target=lambda: answer.update(out=tools.call(name, given)))
    caller.start()
    assert aside.wait(10) and caller.is_alive()  # it waits for the read the exposures need
    gate.set()
    caller.join(10)
    page.join(10)
    ok, out = answer["out"]
    assert ok, out
    answered_with_exposures(store, call, out)
    assert data.calls.count(("dividends", "NVDA")) == 1 and once(store)


@pytest.mark.parametrize("call", NEEDS)
def test_a_call_that_arrives_while_the_page_computes_the_exposures_waits_and_answers_with_them(first, call):
    """The page's thread is fitting the exposures, holding the store's lock, when the call arrives: the call waits
    for the fit and answers with what it kept, computing none of it again."""
    store, tools = first["store"], first["tools"]
    first_read(tools)
    gate, entered = threading.Event(), threading.Event()
    betas = store.engine.registry.get("betas")
    real = betas.compute

    def held(m, load):  # the betas' fit, held until the gate opens, once its reads are all in
        entered.set()
        gate.wait(10)
        return real(m, load)

    object.__setattr__(betas, "compute", held)  # a Derivation is frozen: held for this test alone
    try:
        page = threading.Thread(target=exposures, args=(store,))
        page.start()
        assert entered.wait(10)
        answer: dict = {}
        name, given = NEEDS[call]
        caller = threading.Thread(target=lambda: answer.update(out=tools.call(name, given)))
        blocked, acquire = threading.Event(), store.lock.acquire

        def asked(*a, **kw):  # the call reaching the lock the page holds while it fits
            if threading.current_thread() is caller:
                blocked.set()
            return acquire(*a, **kw)

        store.lock.acquire = asked
        caller.start()
        assert blocked.wait(10) and caller.is_alive()
        gate.set()
        caller.join(10)
        page.join(10)
    finally:
        object.__setattr__(betas, "compute", real)
        store.lock.__dict__.pop("acquire", None)
    ok, out = answer["out"]
    assert ok, out
    answered_with_exposures(store, call, out)
    assert once(store)


def test_while_exposure_waits_on_a_read_the_lock_is_free_the_page_renders_and_another_call_answers(first, monkeypatch):
    """add returns, exposure() is called at once, and NVDA's dividends, which the exposures read, are still being
    read: the call waits for them holding no lock, so the page renders its panels and a proposition's add answers
    meanwhile; once the read lands, exposure() answers with the exposures, computed once."""
    from harness import render
    store, tools, data = first["store"], first["tools"], first["data"]
    gate, entered = threading.Event(), threading.Event()
    held_up(store, "dividends", "NVDA", gate=gate, entered=entered)
    ok, got = tools.call("add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": HOLDINGS}})
    assert ok, got
    aside = standing_aside(monkeypatch)
    answer: dict = {}
    caller = threading.Thread(target=lambda: answer.update(out=tools.call("exposure", {})))
    caller.start()
    assert entered.wait(10)  # the read the exposures need is under way
    assert aside.wait(10) and caller.is_alive()  # exposure() waits for it
    assert store.lock.acquire(timeout=1)  # holding no lock
    store.lock.release()
    with store.lock, render.cache_only(store):  # the page renders, as it does between calls
        reader = render.Reader(store, pending=True)
        assert "NVDA" in render.portfolio_panel(reader)
        render.ledger_panel(reader)
    ok, got = tools.call("add", {"type": "Proposition", "fields": EVENT})  # a call that needs none of it answers
    assert ok and got["id"] == "prop:1", got
    assert caller.is_alive()
    gate.set()
    caller.join(10)
    ok, out = answer["out"]
    assert ok, out
    answered_with_exposures(store, "exposure", out)
    assert data.calls.count(("dividends", "NVDA")) == 1 and once(store)


def test_a_portfolio_whose_as_of_leaves_the_channels_unestimable_is_refused_before_anything_is_written(first):
    """A channel written before the portfolio, as the initial contents are, whose proxy has two sessions' returns at
    the portfolio's as-of: the channels' covariance cannot be measured over so few, and add refuses the portfolio at
    its commit, with the reason and the channel concerned, and writes nothing."""
    store, tools = first["store"], first["tools"]
    put_reference(first["source"], "YNG", "ETF", name="A young fund")
    put_bars(first["source"], "YNG", closes_from(random_returns(np.random.default_rng(3), 0.01, SESSIONS[-2:])))
    with store.transaction():
        store.add("Factor", {"name": "A young fund", "proxy": "YNG", "order": 2})
    before = written(store)
    ok, got = tools.call("add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": HOLDINGS}})
    assert not ok and got["error"].startswith(f"at the as-of {ASOF}, the factors' proxies share")
    assert "factor:" in " ".join(got["dependents"])
    assert written(store) == before and store.type_of("portfolio") is None and store.meta("asof") is None
