"""A store of propositions and markets for the breakdown's tests: Polymarket records with one-level books, whose best
bid and ask are the test's, and the fixture judge keyed by the texts each check compares."""

from __future__ import annotations

import json

from harness.derive import default_registry
from harness.judge import FixtureJudge
from harness.store import Store

ASOF = "2026-11-04"
RETRIEVED = "2026-11-04T15:00:00+00:00"
TARIFF = "metric:us_effective_tariff_rate_pct"
YALE = "Resolves to the Yale Budget Lab's published average effective tariff rate."


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


class World:
    """A store as of ASOF holding cash, with markets whose books the test sets, and the fixture judge: an answer from
    `answers` by key, else `default`."""

    def __init__(self, path, answers=None, default=0.9, asof=ASOF, registry=None):
        self.judge = FixtureJudge(answers or {}, default=lambda kind, statement, evidence: default, key=key)
        self.venues = Venues()
        self.books: dict[str, dict] = {}
        fetchers = {
            "book": lambda subject, covering: {"payload": self.books[subject], "request": {"endpoint": "/books"},
                                               "retrieved_at": RETRIEVED},
            "history": lambda subject, covering: {"payload": {"data": [], "pagination": {}},
                                                  "request": {"endpoint": "/prices-history"}, "retrieved_at": RETRIEVED},
        }
        self.store = Store.create(path, registry=registry or default_registry(), judge=self.judge, venues=self.venues,
                                  fetchers=fetchers)
        self.store.add("Metric", {"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "pct",
                                  "resolution_source": "Yale Budget Lab"})
        self.store.add("Portfolio", {"asof": asof, "holdings": [{"ticker": "CASH", "dollars": 1000.0}]})

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
        fields = {"title": title, "text": f"{title}: the proposition", "relation": relation, "window_end": end,
                  "kind": kind, **more}
        if value is not None:
            fields.update(value=value, metric=metric or TARIFF)
        if start and kind == "hitting":
            fields["window_start"] = start
        return self.store.add("Proposition", fields)["id"]

    def link(self, a, relation, b, **fields) -> str:
        return self.store.link(a, relation, b, fields or None)["id"]

    def derived(self, oid: str, field: str):
        return self.store.get(oid)["derived"][field].get("value")

    def reason(self, oid: str, field: str):
        return self.store.get(oid)["derived"][field].get("reason")

    def interval(self, pid: str) -> tuple:
        d = self.store.get(pid)["derived"]
        return d["lo"]["value"], d["hi"]["value"]

    def point(self, pid: str):
        return self.derived(pid, "p")

    def derive(self, pid: str, reads: list[str], *chances: dict) -> dict:
        return self.store.edit(pid, "derivation", {"reads": reads, "chances": list(chances)})
