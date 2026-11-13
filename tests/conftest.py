"""Fixtures shared by the tests: the fixture judge, a stub venue, fixture derivations, a world on the three venues'
recorded routes, and seven events of known volatility and worst.

The fixture derivations exercise the engine apart from the store's own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.derive import Derivation, Registry
from harness.judge import FixtureJudge
from harness.store import TABLES, Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def read_only_payloads(monkeypatch, request):
    """What a derivation reads is read-only (Engine.read): every test ends by checking each payload an engine of its
    holds parsed against the hash of the content it was read from, so a derivation that changed what it read fails
    the test that ran it. Engine.check_reads, set on that test, finds the read that changed it. A test that changes
    one on purpose is marked `changes_reads`."""
    from harness.derive.engine import Engine, canonical, text_hash
    made: list[Engine] = []
    init = Engine.__init__

    def tracked(self, *a, **kw):
        init(self, *a, **kw)
        made.append(self)

    monkeypatch.setattr(Engine, "__init__", tracked)
    yield
    if request.node.get_closest_marker("changes_reads"):
        return
    changed = [did for engine in made for (did, sha), (got, _) in list(engine._parsed.items())
               if text_hash(canonical(got)) != sha]
    assert not changed, (f"{', '.join(changed)} changed after it was read: what a derivation reads is read-only; "
                         "set Engine.check_reads on this test to find the read that changed it")


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    """Every test runs with no key and no token in its environment, so nothing it builds reaches the network."""
    from harness.credentials import SECRET_ENV, SECRET_FILES
    for key in SECRET_ENV + SECRET_FILES:
        monkeypatch.delenv(key, raising=False)


# Each venue's page on what it pays on a position, as the venue words it (harness.venues.paid), the figures this suite's
# own: Kalshi's beside a rate of its perps under a label of its own, Polymarket's with its emphasis.
KALSHI_PAGE = ("<html><body><h1>APY on Kalshi</h1><ul><li>Prediction Market Interest</li></ul>"
               '<div><h1 id="a"><b>Prediction Market Interest</b></h1></div><div><p><b>Rate:</b> 3.50% APY. This rate '
               "is variable.</p></div><div><h1><b>Perpetuals (Perps) Interest</b></h1></div><div><p><b>Rate:</b> 3.25% "
               "APY. This rate is variable.</p></div></body></html>")
POLYMARKET_PAGE = ("### Holding Rewards\n\nPolymarket pays a **4.00% annualized** Holding Reward based on your total "
                   "position value in eligible markets. The reward is distributed daily.\n")


def paid(pages: dict[str, str] | None = None, status: int = 200):
    """The venues' pages on what each pays on a position, answered from `pages` by URL, these by default; with the
    requests made."""
    import httpx

    from harness.venues import MODULES, NAMES
    from harness.venues.paid import Paid
    answered = pages if pages is not None else {MODULES["kalshi"].PAID["url"]: KALSHI_PAGE,
                                                MODULES["polymarket"].PAID["url"]: POLYMARKET_PAGE}
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        text = answered.get(str(request.url))
        return httpx.Response(status if text is not None else 404, text=text or "")
    found = Paid({v: MODULES[v].PAID for v in MODULES}, NAMES, http=httpx.Client(transport=httpx.MockTransport(answer)))
    found.asked = asked
    return found


@pytest.fixture
def world(tmp_path):
    """A store and its world on the venues' recorded routes, the web on a test key."""
    from harness.derive import default_registry
    from harness.venues import Venues
    from harness.web import Web
    from harness.world import World
    from test_markets import adapter, kalshi_route, polymarket_route
    store = Store.create(tmp_path / "scratch-markets", registry=default_registry())
    (pm, pm_seen), (ks, ks_seen) = adapter("polymarket", polymarket_route), adapter("kalshi", kalshi_route)
    venues = Venues({"polymarket": pm, "kalshi": ks}, paid())
    w = World(store, venues, Web(key="test"))
    w.seen = {"polymarket": pm_seen, "kalshi": ks_seen}
    yield w
    store.close()


@pytest.fixture
def events(tmp_path):
    """Seven watched events, each moving the S&P 500 channel through one consequence, of known volatility and worst."""
    from test_impact import channel, world as impact_world
    from test_tools import EVENTS, G
    w = impact_world(tmp_path, g={"SPY": G, "USO": 0.0, "EEM": 0.0}, levels={"SPY": 100.0})
    spy = channel(w, "SPY")
    ids = {}
    for e, (p, x) in EVENTS.items():
        ids[e] = w.prop(e, chance={"value": p}, baseline="no")
        w.link(ids[e], "consequence", spy, shock=x)
        w.link("portfolio", "watches", ids[e])
    yield w, ids
    w.store.close()


ASOF = "2026-11-04"
RETRIEVED = "2026-11-04T15:00:00+00:00"


def fixture_judge() -> FixtureJudge:
    """The fixture judge, answering every question by a rule over its evidence: a noul its probability, and a
    score that probability on its lowest level and the rest on its highest."""
    def rule(kind, statement, evidence, levels=None):
        p = round(0.30 + 0.01 * len(evidence) + 0.001 * (len(statement) % 10), 4)
        return p if levels is None else [p] + [0.0] * (len(levels) - 2) + [round(1 - p, 4)]
    return FixtureJudge(default=rule, key=lambda kind, statement, evidence, about=None: (kind, statement))


class StubVenues:
    """A venue client that lists the markets it is given."""

    def __init__(self, markets: dict[str, dict]):
        self.markets = markets
        self.calls: list[str] = []

    def market(self, venue, venue_id):
        key = f"market:{venue}:{venue_id}"
        self.calls.append(key)
        if key not in self.markets:
            raise LookupError("the venue lists no such market")
        return {"payload": self.markets[key], "request": {"endpoint": f"/{venue}/markets/{venue_id}"},
                "retrieved_at": RETRIEVED}


class BookFetcher:
    """Fetches a kept market's book on its first read."""

    def __init__(self, books: dict[str, dict]):
        self.books = books
        self.calls: list[str] = []

    def __call__(self, subject, covering):
        self.calls.append(subject)
        return {"payload": self.books[subject], "request": {"endpoint": "/book", "market": subject},
                "retrieved_at": RETRIEVED}


def fx_interval() -> Derivation:
    """A proposition's interval from its quoted markets' books and the propositions that imply it."""

    def inputs(ctx, pid):
        quotes = [{"link": link, "book": ctx.data("book", link["to"])} for link in ctx.links(pid, "asks")]
        implied = [ctx.value("fx_interval", link["from"]) for link in ctx.links(pid, "implies", direction="in")]
        return {"proposition": pid, "quotes": quotes, "implied": implied}

    def compute(m, load):
        lo, hi, bounds = 0.0, 1.0, {}
        for q in m["quotes"]:
            book = load(q["book"])
            bid, ask = book["bid"], book["ask"]
            if q["link"]["stated"].get("side") == "no":
                bid, ask = 1 - ask, 1 - bid
            bounds[q["link"]["id"]] = [bid, ask]
            lo, hi = max(lo, bid), min(hi, ask)
        for v in m["implied"]:
            lo = max(lo, v["output"]["lo"])
        return {"lo": lo, "hi": hi, "bounds": bounds}

    return Derivation("fx_interval", 1, "Proposition", inputs, compute, fields=("range",),
                      pick=lambda out, pid: {"range": [out["lo"], out["hi"]]})


def fx_bound() -> Derivation:
    """The bid and ask an asks link gives its proposition, read from the proposition's interval."""

    def inputs(ctx, lid):
        link = ctx.link(lid)
        return {"link": lid, "interval": ctx.value("fx_interval", link["from"])}

    def compute(m, load):
        bid, ask = m["interval"]["output"]["bounds"].get(m["link"]) or (None, None)
        return {"bid": bid, "ask": ask}

    return Derivation("fx_bound", 1, "asks", inputs, compute, fields=("bid", "ask"))


def fx_point() -> Derivation:
    """A watched proposition's point: the judge's answer over its fields, interval, and evidence, clamped."""

    def inputs(ctx, pid):
        p = ctx.object(pid)
        interval = ctx.value("fx_interval", pid)
        evidence = [{"ref": pid, "content": {"proposition": p["stated"]}},
                    {"ref": interval["basis"], "content": {"lo": interval["output"]["lo"],
                                                           "hi": interval["output"]["hi"]}}]
        for link in ctx.links(pid, "evidence"):
            s = ctx.source(link["to"])
            evidence.append({"ref": s["source"], "content": {"url": s["url"], "dated": s["dated"],
                                                             "sha256": s["sha256"]}})
        return {"interval": interval, "judgment": ctx.ask("point", f"{p['stated']['text']} is true.", evidence)}

    def compute(m, load):
        lo, hi = m["interval"]["output"]["lo"], m["interval"]["output"]["hi"]
        return {"p": min(max(m["judgment"]["answer"], lo), hi)}

    return Derivation("fx_point", 1, "Proposition", inputs, compute, fields=("probability",),
                      pick=lambda out, pid: {"probability": out["p"]},
                      applies=lambda store, pid: store.watched(pid))


def fx_lookthrough() -> Derivation:
    """Names and looks_through_to links from holdings in dollars and the funds' constituents."""

    def inputs(ctx, subject):
        holds = ctx.links("portfolio", "holds")
        return {"portfolio": ctx.object("portfolio"), "holds": holds,
                "constituents": {h["to"]: ctx.data("constituents", h["to"], required=False) for h in holds}}

    def compute(m, load):
        objects, links = {}, {}

        def add(nid, dollars):
            objects.setdefault(nid, {"type": "Name", "fields": {"exposure": 0.0}})["fields"]["exposure"] += dollars

        total = m["portfolio"]["stated"].get("total") or 0.0
        for h in m["holds"]:
            dollars = h["stated"].get("dollars", h["stated"].get("weight", 0.0) * total)
            ref = m["constituents"][h["to"]]
            if ref["data"] is None:
                add(h["to"], dollars)
                continue
            add(h["to"], 0.0)
            for ticker, weight in load(ref).items():
                nid = f"name:{ticker}"
                add(nid, dollars * weight)
                lid = f"looks_through_to:{h['to']}>{nid}"
                links[lid] = {"relation": "looks_through_to", "from": h["to"], "to": nid,
                              "fields": {"weight": weight, "dollars": dollars * weight}}
        return {"objects": objects, "links": links}

    return Derivation("fx_lookthrough", 1, "Portfolio", inputs, compute, produces=("Name", "looks_through_to"))


def registry() -> Registry:
    return Registry([fx_interval(), fx_bound(), fx_point(), fx_lookthrough()])


def snapshot(store: Store) -> dict:
    """Every row of every table but the cache of Computations and the count its ids run to, and the pages held: what a
    refused call must leave. A call keeps what it worked out whatever becomes of it, as it keeps what it read and what
    the judge answered (Store._keep_fetched)."""
    tables = {t: [tuple(r) for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in TABLES
              if t != "computation"}
    tables["seq"] = [r for r in tables["seq"] if r[0] != "cmp"]
    return {"tables": tables, "pending": dict(store._pending)}


def count(store: Store, table: str) -> int:
    return store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def masked(obj):
    """Values as they read, each Computation's id masked: a value computed again where the cache no longer holds the
    Computation it had is the same value with a new basis. A Judgment's id stays: a new one is the judge
    asked again."""
    return json.loads(re.sub(r'"cmp:\d+"', '"cmp"', json.dumps(obj, default=str, sort_keys=True)))


@pytest.fixture
def make_store(tmp_path):
    opened: list[Store] = []

    def make(name: str = "scratch-test", *, registry_=None, **kw) -> Store:
        store = Store.create(tmp_path / name, registry=registry() if registry_ is None else registry_, **kw)
        opened.append(store)
        return store

    yield make
    for store in opened:
        try:
            store.close()
        except Exception:
            pass
