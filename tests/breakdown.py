"""A store of propositions and markets for the breakdown's tests: Polymarket records with one-level books, whose best
bid and ask are the test's, and the fixture judge keyed by the texts each check compares."""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from pathlib import Path

from harness import schema
from harness.derive import default_registry
from harness.judge import FixtureJudge
from harness.store import Store

ASOF = "2026-11-04"
RETRIEVED = "2026-11-04T15:00:00+00:00"
TARIFF = "metric:us_effective_tariff_rate_pct"
YALE = "Resolves to the Yale Budget Lab's published average effective tariff rate."
WHY = "the case this test states"  # the reasoning each stated assumption is written with (ONTOLOGY.md 2; TOOLS.md 5)


def stated(value, why: str = WHY) -> dict:
    """An assumption as the analyst states it: its value, or its value's fields, with its basis, here the reasoning."""
    return {**value, "note": why} if isinstance(value, dict) else {"value": value, "note": why}


def _based(v) -> bool:
    return isinstance(v, dict) and bool(set(v) & set(schema.BASIS))


def with_basis(kind: str, fields: dict | None) -> dict | None:
    """The fields of an object or a link of this type or relation, each assumption a test gives as a bare figure stated
    with its basis; a market's price taken states none."""
    t = schema.OBJECTS.get(kind) or schema.RELATIONS.get(kind)
    if not fields or t is None:
        return fields
    out = dict(fields)
    for f in t.stated:
        v = out.get(f.name)
        if v is None or f.kind not in schema.BASED or _based(v) or v is True:
            continue
        if f.kind == schema.CHANCES:
            out[f.name] = [c if _based(c) else stated(c) for c in v]
        else:
            out[f.name] = stated(v)
    return out


def key(kind: str, statement: str, evidence: list[dict]) -> tuple:
    """A check named by its kind and the titles or questions it compares."""
    content = [e["content"] for e in evidence]
    props = [c["proposition"] for c in content if "proposition" in c]
    if kind.startswith("market"):
        return (kind, props[0], next(c["market"] for c in content if "market" in c))
    return (kind, props[0], props[1])


class Venues:
    """Polymarket records by market id."""

    def __init__(self):
        self.records: dict[str, dict] = {}

    def market(self, venue, venue_id):
        return {"payload": self.records[f"market:{venue}:{venue_id}"], "request": {"endpoint": f"/markets/{venue_id}"},
                "retrieved_at": RETRIEVED}

    def fetchers(self, store):
        return {}


_BUILT: dict[tuple, Path] = {}  # each base written in this process, by the base and the as-of: its store


def _built(base, asof: str, name: str) -> Path:
    """The store `base` writes into a new World as of `asof`, written once in this process and closed, its directory
    removed when the process ends."""
    if (base, asof) not in _BUILT:
        root = Path(tempfile.mkdtemp(prefix="sl-test-base-"))
        atexit.register(shutil.rmtree, root, True)
        made = World(root / name, asof=asof)
        base(made.store)
        made.store.close()
        _BUILT[(base, asof)] = root / name
    return _BUILT[(base, asof)]


class World:
    """A store as of ASOF holding cash, with markets whose books the test sets, and the fixture judge: an answer from
    `answers` by key, else `default`."""

    def __init__(self, path, answers=None, default=0.9, asof=ASOF, registry=None, base=None):
        """`base`, where given, writes into the store what every test of its kind begins from: it is written once in a
        process, and each World given it begins from a copy of that store."""
        self.judge = FixtureJudge(answers or {}, default=lambda kind, statement, evidence: default, key=key)
        self.venues = Venues()
        self.books: dict[str, dict] = {}
        fetchers = {
            "book": lambda subject, covering: {"payload": self.books[subject], "request": {"endpoint": "/books"},
                                               "retrieved_at": RETRIEVED},
            "history": lambda subject, covering: {"payload": {"data": [], "pagination": {}},
                                                  "request": {"endpoint": "/prices-history"}, "retrieved_at": RETRIEVED},
        }
        opened = dict(registry=registry or default_registry(), judge=self.judge, venues=self.venues, fetchers=fetchers)
        if base is not None:
            shutil.copytree(_built(base, asof, Path(path).name), path)
            self.store = Store.open(path, **opened)
            return
        self.store = Store.create(path, **opened)
        self.store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct",
                                  "kind": "level", "resolution_source": "Yale Budget Lab"})
        self.store.add("Portfolio", {"asof": asof, "holdings": [{"cash": 1000.0}]})

    def market(self, vid: str, question: str, bid: float | None, ask: float | None, rules: str = YALE,
               last: float | None = None) -> str:
        mid = f"market:polymarket:{vid}"
        self.venues.records[mid] = {"market": {"id": vid, "question": question, "description": rules,
                                               "endDate": "2027-04-01T03:59:00Z", "closed": False,
                                               "clobTokenIds": json.dumps([f"Y{vid}", f"N{vid}"]),
                                               "outcomes": json.dumps(["Yes", "No"])}, "tags": []}
        self.set_book(mid, bid, ask, last)
        return mid

    def set_book(self, mid: str, bid: float | None, ask: float | None, last: float | None = None,
                 size: float = 1000.0) -> None:
        """The book the store reads next: a market's data is read once, so a new book is a new read."""
        self.books[mid] = {"bids": [{"price": str(bid), "size": str(size)}] if bid is not None else [],
                           "asks": [{"price": str(ask), "size": str(size)}] if ask is not None else [],
                           **({"last_trade_price": str(last)} if last is not None else {})}

    def reread(self, mid: str) -> None:
        """The market's book read again, as a later read would."""
        payload = self.books[mid]
        self.store.put_data("book", mid, payload, {"endpoint": "/books"}, retrieved_at=RETRIEVED)

    def prop(self, title: str, relation: str = "occurs", value: float | None = None, kind: str = "hitting",
             end: str = "2027-03-31", start: str | None = ASOF, metric: str | None = None, **more) -> str:
        if "chance" in more:  # a chance stated on its own, its probability
            item = more["chance"] if isinstance(more["chance"], dict) else {"value": more["chance"]}
            more = {**{k: v for k, v in more.items() if k != "chance"}, "chances": [item]}
        fields = {"title": title, "text": f"{title}: the proposition", "comparison": relation, "window_end": end,
                  "kind": kind, **more}
        if value is not None:
            fields.update(value=value, metric=metric or TARIFF)
        elif metric is not None:  # the occurrence a proposition that occurs names
            fields["metric"] = metric
        if start and kind == "hitting":
            fields["window_start"] = start
        return self.store.add("Proposition", with_basis("Proposition", fields))["id"]

    def link(self, a, relation, b, **fields) -> str:
        return self.store.link(a, relation, b, with_basis(relation, fields) or None)["id"]

    def derived(self, oid: str, field: str):
        return self.store.get(oid)["derived"].get(field, {}).get("value")

    def interval(self, pid: str) -> tuple:
        d = self.store.get(pid)["derived"]
        return tuple(d["range"]["value"])

    def point(self, pid: str):
        return self.derived(pid, "probability")

    def chances(self, pid: str, *items: dict, keep: bool = True) -> dict:
        """State chances of pid, each {given, given_not, value}; with `keep`, beside those it states, any given the same
        replaced."""
        cond = lambda c: (sorted(c.get("given") or []), sorted(c.get("given_not") or []))  # noqa: E731
        new = [{"given": [], "given_not": [], **c} for c in items]
        held = [c for c in (self.store.stated_of(pid).get("chances") or []) if keep and
                all(cond(c) != cond(n) for n in new)]
        return self.store.edit(pid, "chances", held + with_basis("Proposition", {"chances": new})["chances"])

    def left_open(self, pid: str):
        return self.derived(pid, "left_open")
