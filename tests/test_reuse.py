"""Figures reused while nothing they read has changed (ONTOLOGY.md 2: a derived field is computed when it is read, and again
when anything it read changes).

The one silent failure is a figure kept when it should not be. So after every write of an analysis, every figure the
engine serves is what an engine that kept nothing serves from the same store at the same state: the ledger's rows, each
event as it now stands, what cannot hold together, and each event's derived fields. A figure reused may name the
Computation it was worked out from where an engine that kept nothing names another with the same output (a value read
again and come to the same output disturbs nothing that read it), so figures are compared as outputs, by what they
say, and their Computations' ids are set aside."""

from __future__ import annotations

import re

import numpy as np
import pytest

from harness import schema
from harness.derive.engine import Engine
from harness.ledger import check, event_now, ledger
from harness.store import StoreError
from massive_data import closes_from, put_bars, random_returns
from test_impact import channel, event, holding_world, quoted, stated

CMP = re.compile(r"^cmp:\d+$")


def _plain(x):
    """What a figure says, its Computations' ids set aside."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items() if k not in ("basis", "computation", "cid")}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if isinstance(x, str) and CMP.match(x):
        return "cmp"
    return x


def _served(store) -> dict:
    """Every figure the engine serves of the analysis."""
    events = [link["to"] for link in store.links_of("portfolio", "watches")]
    out = {"ledger": ledger(store), "check": check(store)}
    for pid in events:
        out[pid] = {"now": event_now(store, pid),
                    "derived": store.get(pid, exclude_derived=("distribution",))["derived"]}
    return _plain(out)


def _fresh(store) -> dict:
    """What an engine that kept nothing serves now, from the same store."""
    warm = store.engine
    store.engine = Engine(store, warm.registry, warm.judge)
    try:
        return _served(store)
    finally:
        store.engine.close()
        store.engine = warm


def test_after_every_write_every_figure_reused_is_what_an_engine_that_kept_nothing_serves(tmp_path):
    w = holding_world(tmp_path, channels=("USO", "TLT"))
    s = w.store
    spy, oil, tlt = channel(w, "SPY"), channel(w, "USO"), quoted(w, "TLT", 4.2, 17.0)
    steps = []

    def step(name, write):
        write()
        served, fresh = _served(s), _fresh(s)
        steps.append(name)
        assert served == fresh, f"after {name}, the engine serves what an engine that kept nothing does not"

    e, f = {}, {}
    step("watch an event", lambda: e.setdefault("id", event(w, "E", p=0.3)))
    step("a shock on the market", lambda: e.setdefault("spy", s.link(e["id"], "consequence", spy,
                                                                     {"shock": stated(-0.1)})["id"]))
    step("a note about the event", lambda: s.note("Why the market falls.", [e["id"]]))
    step("the shock edited", lambda: s.edit(e["spy"], "shock", stated(-0.15)))
    step("a shock on a name", lambda: s.link(e["id"], "consequence", "name:NVDA", {"shock": stated(-0.2)}))
    step("a second event", lambda: f.setdefault("id", event(w, "F", p=0.4)))
    step("a shock against what oil follows", lambda: s.link(f["id"], "consequence", oil,
                                                            {"shock": stated({"value": 0.05, "against": schema.FOLLOWS})}))
    step("a shock against what a company follows", lambda: s.link(f["id"], "consequence", "name:NVDA",
                                                                   {"shock": stated({"value": -0.05,
                                                                                     "against": schema.FOLLOWS})}))
    step("a shock on the yield, in points", lambda: s.link(f["id"], "consequence", tlt, {"shock": stated(0.25)}))
    step("the event's chance", lambda: s.edit(e["id"], "chances", [stated(0.35)]))
    s.checkpoint("turn 2")
    rng = np.random.default_rng(11)
    step("a price landing", lambda: put_bars(s, "NVDA", closes_from(random_returns(rng, 0.02))))

    def refused():
        with pytest.raises(StoreError, match="is a fraction of its price"):
            s.link(f["id"], "consequence", "name:FUND", {"shock": stated(-5.0)})
    step("a statement refused and rolled back", refused)
    step("the name's shock removed", lambda: s.remove(next(
        link["id"] for link in s.links_of(e["id"], "consequence") if link["to"] == "name:NVDA")))
    step("rewound to turn 2", lambda: s.rewind("turn 2"))
    step("an event unwatched", lambda: s.remove(next(
        link["id"] for link in s.links_of("portfolio", "watches") if link["to"] == f["id"])))
    assert len(steps) == 15


def test_a_note_works_nothing_out_again_and_a_shock_works_out_its_event_alone(tmp_path):
    """What a write costs is what it changes: a note about an event leaves every figure standing, read from what was
    kept; a shock edited on one event works out that event's figures again and leaves the other's standing."""
    w = holding_world(tmp_path, channels=("USO",))
    s = w.store
    spy, oil = channel(w, "SPY"), channel(w, "USO")
    e, f = event(w, "E", p=0.3), event(w, "F", p=0.4)
    shock = s.link(e, "consequence", spy, {"shock": stated(-0.1)})["id"]
    s.link(f, "consequence", oil, {"shock": stated(0.2)})
    _served(s)
    worked = []
    compute = s.engine._compute

    def counted(key):
        worked.append(key)
        return compute(key)

    s.engine._compute = counted
    s.note("Why the market falls.", [e])
    _served(s)
    assert worked == []
    s.edit(shock, "shock", stated(-0.12))
    _served(s)
    assert {k for k in worked if k[0] == "impact"} == {("impact", e)}


def test_a_value_whose_gathering_reads_what_the_producers_give_is_read_again_when_their_products_change(make_store):
    """A store's read made inside a gathering can rest on what the producers give (which Names exist: Store._name_exists
    reads the Names the producers of the look-through give). Those products are a value the gathering read: when a producer gives another Name,
    the value that asked whether it exists is worked out again, though nothing it read of the store itself changed."""
    from harness.derive import Derivation, Registry
    from test_store import RETRIEVED

    def made(ctx, subject):
        return {"says": ctx.data("k", "P")}

    def names(m, load):
        return {"objects": {"name:ZZZ": {"type": "Name", "fields": {}}} if load(m["says"])["make"] else {}}

    def asks(ctx, subject):
        return {"exists": ctx._store.type_of("name:ZZZ") is not None}

    registry = Registry([Derivation("made", 1, "Portfolio", made, names, produces=("Name", "looks_through_to")),
                         Derivation("asks", 1, "Portfolio", asks, lambda m, load: {"exists": m["exists"]})])
    store = make_store(registry_=registry)
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    store.put_data("k", "P", {"make": False}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
    assert store.engine.value("asks", "portfolio").output == {"exists": False}
    store.put_data("k", "P", {"make": True}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
    assert store.engine.value("asks", "portfolio").output == {"exists": True} == \
        Engine(store, registry).value("asks", "portfolio").output
