"""The exposures' fit computed without the store's lock (TOOLS.md 5; Engine): no call and no render of the page waits on
a computation it does not need; a call that needs it waits for the one under way and answers with it; nothing is
computed twice; and nothing is published from a revision that has moved on. Over the first read's synthetic data."""

from __future__ import annotations

import json
import threading
import time

import pytest

from harness import ledger, render
from harness.derive.engine import Unavailable, Waiting
from harness.server import exposures
from test_first_read import ASOF, EVENT, HOLDINGS, answered_with_exposures, first, once  # noqa: F401  (first: fixture)


def held(store, name: str, *, times: int = 1):
    """The derivation's compute held at a gate for its first `times` computations, each counted; restored by the
    returned function."""
    d = store.engine.registry.get(name)
    real = d.compute
    gate, entered, count = threading.Event(), threading.Event(), {"n": 0}

    def compute(m, load):
        count["n"] += 1
        if count["n"] <= times:
            entered.set()
            assert gate.wait(10)
        return real(m, load)

    object.__setattr__(d, "compute", compute)  # a Derivation is frozen: held for one test alone
    return gate, entered, count, lambda: object.__setattr__(d, "compute", real)


def add_portfolio(tools) -> None:
    ok, got = tools.call("add", {"type": "Portfolio", "fields": {"asof": ASOF, "holdings": HOLDINGS}})
    assert ok, got


def test_while_the_page_fits_the_exposures_the_lock_is_free_the_page_renders_and_a_proposition_add_answers(first):
    store, tools = first["store"], first["tools"]
    add_portfolio(tools)
    gate, entered, count, restore = held(store, "betas")
    try:
        page = threading.Thread(target=exposures, args=(store,))
        page.start()
        assert entered.wait(10)  # the fit is under way
        assert store.lock.acquire(timeout=1)  # holding no lock
        store.lock.release()
        with store.lock, render.cache_only(store):
            reader = render.Reader(store, pending=True)
            assert "NVDA" in render.portfolio_panel(reader)
            render.ledger_panel(reader)
        started = time.monotonic()
        ok, got = tools.call("add", {"type": "Proposition", "fields": EVENT})  # needs none of it: answers now
        assert ok and got["id"] == "prop:1", got
        assert time.monotonic() - started < 2 and page.is_alive() and count["n"] == 1
        gate.set()
        page.join(20)
    finally:
        restore()
    assert not page.is_alive() and once(store)
    assert store.engine.value("g", "portfolio").output["g"]


def test_two_readers_of_the_fit_at_once_wait_for_one_computation_and_answer_with_it(first):
    store, tools = first["store"], first["tools"]
    add_portfolio(tools)
    gate, entered, count, restore = held(store, "betas")
    answer: dict = {}
    try:
        page = threading.Thread(target=exposures, args=(store,))
        page.start()
        assert entered.wait(10)
        caller = threading.Thread(target=lambda: answer.update(out=tools.call("exposure", {})))
        caller.start()
        time.sleep(0.3)
        assert caller.is_alive() and page.is_alive()  # both wait for the one fit
        gate.set()
        caller.join(20)
        page.join(20)
    finally:
        restore()
    ok, out = answer["out"]
    assert ok, out
    answered_with_exposures(store, "exposure", out)
    assert count["n"] == 1 and once(store)  # computed once, kept once


def test_a_write_that_changes_the_fit_while_it_computes_is_answered_by_a_fit_of_its_own_never_the_older_one(first):
    """A channel is added while the page fits the exposures without it: the older fit is never kept as the value of
    the store as it now stands, and every value read after it carries the new channel."""
    store, tools = first["store"], first["tools"]
    add_portfolio(tools)
    gate, entered, count, restore = held(store, "betas")  # the page's fit held; the channel's own fit is not
    try:
        page = threading.Thread(target=exposures, args=(store,))
        page.start()
        assert entered.wait(10)
        ok, got = tools.call("add", {"type": "Factor", "fields": {"name": "Long Treasury bonds",
                                                                    "proxy": "TLT", "order": 2}})
        assert ok, got  # the channel's own fit, computed in its transaction while the page's older one waits
        gate.set()
        page.join(20)
    finally:
        restore()
    with store.lock:
        g = store.engine.value("g", "portfolio").output["g"]
        betas = store.engine.value("betas", "portfolio")
    assert set(g) == {"factor:1", "factor:2"}
    kept = [json.loads(r["inputs"]) for r in store.db.execute(
        "SELECT inputs FROM computation WHERE derivation = 'g'")]
    assert all(ref["betas"]["basis"] == betas.basis for ref in kept[-1:])  # the latest g reads the latest fit
    assert once(store)


def test_an_unrelated_write_while_the_fit_computes_leaves_it_computed_once(first):
    store, tools = first["store"], first["tools"]
    add_portfolio(tools)
    gate, entered, count, restore = held(store, "betas")
    try:
        page = threading.Thread(target=exposures, args=(store,))
        page.start()
        assert entered.wait(10)
        ok, got = tools.call("note", {"text": "A note written while the exposures are fitted.",
                                      "about_ids": ["portfolio"]})
        assert ok, got
        gate.set()
        page.join(20)
    finally:
        restore()
    assert count["n"] == 1 and once(store)


def test_an_error_in_a_computation_made_apart_is_raised_to_the_reader_never_held_as_a_value(first):
    store, tools = first["store"], first["tools"]
    add_portfolio(tools)
    d = store.engine.registry.get("betas")
    real = d.compute
    gate = threading.Event()

    def broken(m, load):
        assert gate.wait(10)
        raise RuntimeError("a defect in the fit")

    object.__setattr__(d, "compute", broken)
    try:
        with store.lock, store.engine.placeholders():
            assert store.engine.value("betas", "portfolio").waiting  # started apart: the page's placeholder
        gate.set()
        for f in list(store.engine._running.values()):
            f.exception(10)
        with store.lock, pytest.raises(RuntimeError, match="a defect in the fit"):
            store.engine.value("betas", "portfolio")
    finally:
        object.__setattr__(d, "compute", real)


def test_a_company_whose_reference_is_still_being_read_waits_and_one_with_none_has_no_name(first, monkeypatch):
    store = first["store"]

    def waiting(*a, **k):
        raise Waiting("the reference of NVDA is being read")

    def absent(*a, **k):
        raise Unavailable("no such ticker")

    monkeypatch.setattr(store, "data", waiting)
    with pytest.raises(Waiting):  # a strict read stands aside for it, never answering without the name
        ledger._company(store, "name:NVDA")
    monkeypatch.setattr(store, "data", absent)
    assert ledger._company(store, "name:NVDA") is None
