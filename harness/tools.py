"""The analyst's sixteen tools: five write structure, four bring the world into the store,
seven read. Each is the store's or the world's own call, with its signature, its return, and its
refusals: a refused call returns an error and writes nothing.

`Tools.call(name, input)` is how the runtime runs one: it numbers the call, writes the store's rows with
that number, and writes the call to the store's trace with its input and its result or error.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from harness import ledger as ledger_queries
from harness.derive.channels import window_subject
from harness.store import Store, StoreError
from harness.trace import Trace, now
from harness.world import World

# Each tool: its parameters in order, as (name, JSON schema, required, what it is), and what it does.
OBJECT = {"type": "object"}
STRING = {"type": "string"}
ANY = {}

SPECS: dict[str, dict] = {
    "add": {
        "params": [("type", STRING, True, "the object type: Portfolio, Proposition, Metric, or Channel"),
                   ("fields", OBJECT, True, "the stated fields; a fact about the world is {\"value\": ..., "
                                            "\"source_id\": \"src:N\"}; a stated chance or derivation carries its "
                                            "basis as TOOLS.md shows; a Portfolio also takes holdings")],
        "does": "Creates an object; the derivations of ONTOLOGY.md follow. Returns the object as get returns it; a "
                "Portfolio returns what it owns: each holding as given, with its value, weight, and move, and what "
                "it is, a company with what it does and its industry, or a fund with its largest holdings by name, "
                "what each is, and where it is listed, and how its dollars divide by where its holdings are listed "
                "and by kind of asset; and the largest names after look-through, each with what is held directly "
                "and through which funds, and the latest news on the ten largest companies. A Channel returns "
                "how it is built from the earlier channels, its largest month, and the portfolio's dollar beta "
                "and beta to it; each name's beta to it is one get away.",
    },
    "link": {
        "params": [("from_id", STRING, True, "the id the link runs from"),
                   ("relation", STRING, True, "the relation, by ONTOLOGY.md's link table"),
                   ("to_id", STRING, True, "the id the link runs to; a market as market:{venue}:{id}"),
                   ("fields", OBJECT, False, "the link's stated fields: transform for quoted_by, conversion for "
                                             "tracks, step for then, passages for evidence, value and meaning "
                                             "for consequence")],
        "does": "Creates a link; the derivations follow. Returns the link with its derived fields.",
    },
    "note": {
        "params": [("text", STRING, True, "the reason, in words"),
                   ("about_ids", {"type": "array", "items": STRING}, False, "the ids the note concerns")],
        "does": "Creates a Note linked to the objects and links it concerns. Returns the Note.",
    },
    "edit": {
        "params": [("id", STRING, True, "the object, link, or fetched page"),
                   ("field", STRING, True, "a stated field"),
                   ("value", ANY, True, "the new value; null clears a field that is not required"),
                   ("source_id", STRING, False, "the page a fact about the world cites")],
        "does": "Changes one stated field. Returns the object or link with its derived fields; a Channel as add "
                "returns it.",
    },
    "remove": {
        "params": [("id", STRING, True, "the object or link")],
        "does": "Removes an object or link with the values derived from it and their basis links. Returns what "
                "went: the removed, the links cascaded, and the markets and pages released.",
    },
    "search_web": {
        "params": [("text", STRING, True, "the query"),
                   ("params", OBJECT, False, "the search tool's own parameters, as DATA.md lists"),
                   ("proposition_id", STRING, False, "the proposition the search is for; recorded on it")],
        "does": "One web search: the URLs, titles, dates, and the passages quoted from each.",
    },
    "fetch_source": {
        "params": [("url", STRING, True, "the page's http or https URL")],
        "does": "Fetches the page's full text into the store; returns its id with an excerpt. The page is kept as a "
                "Source once a stated fact cites it or an evidence link quotes it.",
    },
    "search_company": {
        "params": [("ticker", STRING, True, "the company's ticker"),
                   ("kind", STRING, True, "news, current_reports, risk_factors, annual_report, earnings, guidance, "
                                          "or ratings"),
                   ("params", OBJECT, False, "the kind's own parameters, as DATA.md lists them")],
        "does": "One kind of a company's own record: its news, with each article's full text where it has one; from "
                "its SEC filings, its 8-Ks, its risk factors, or its 10-K's sections; or its earnings, guidance, or "
                "analysts' ratings. A list comes back as compact rows, each document in it held as a page, read by "
                "passages with get(id, text) and kept as a Source once cited.",
    },
    "search_markets": {
        "params": [("venue", STRING, True, "polymarket, kalshi, or hyperliquid"),
                   ("endpoint", STRING, True, "one of the venue's search or listing endpoints, as DATA.md lists"),
                   ("params", OBJECT, False, "the venue's own parameters for that endpoint"),
                   ("proposition_id", STRING, False, "the proposition the search is for; recorded on it")],
        "does": "Passes the parameters to the venue's endpoint and returns what it returns, in its order and with "
                "its paging, each market with its quote, depth, and close time.",
    },
    "window_returns": {
        "params": [("start", STRING, True, "the window's first date, YYYY-MM-DD: returns run from its close"),
                   ("end", STRING, True, "the window's last date, YYYY-MM-DD: returns run to its close")],
        "does": "Raw and orthogonalized proxy returns over the window, for looking at how the channels moved in a "
                "past episode.",
    },
    "get": {
        "params": [("id", STRING, True, "any id in the store"),
                   ("text", STRING, False, "for a page: words to find in it, returning the passages that contain them")],
        "does": "The object or link with its stated fields, derived fields (each with its basis or the reason it "
                "has no value), and links. A page with its whole text, or, given text, the passages that contain "
                "it, each the page's own words to quote. A name with what its company does; a fund with what it "
                "holds, each holding by name down to the look-through floor and the rest summed by where they are "
                "listed and by kind of asset. A Computation or Judgment with its inputs and output.",
    },
    "query": {
        "params": [("type", STRING, True, "an object type"),
                   ("where", OBJECT, False, "field filters: a value, or {op: value} with op eq, ne, lt, le, gt, "
                                            "ge, in, or contains")],
        "does": "The objects of a type whose fields match the filters.",
    },
    "neighbors": {
        "params": [("id", STRING, True, "any id in the store"),
                   ("relation", STRING, False, "one relation, or all"),
                   ("direction", STRING, False, "out, in, or both (the default)")],
        "does": "The objects linked to the id, each with the link, its relation, and its direction.",
    },
    "ledger": {
        "params": [],
        "does": "Every watched proposition with every field, ranked by emove, with the worst order beside them.",
    },
    "check": {
        "params": [],
        "does": "What the work is missing or contradicts. It gates nothing.",
    },
    "exposure": {
        "params": [],
        "does": "The portfolio's dollar beta g to each channel the store models, with the channel's largest "
                "one-month move M_c and abs(g) * M_c, and each proxy's price and move: the basis on which what an "
                "event does is sized. It runs no searches.",
    },
}

WRITES = ("add", "link", "note", "edit", "remove")
WORLD = ("search_web", "fetch_source", "search_company", "search_markets")
READS = ("window_returns", "get", "query", "neighbors", "ledger", "check", "exposure")
assert set(SPECS) == set(WRITES + WORLD + READS) and len(SPECS) == 16


def signature(name: str) -> str:
    """The tool's signature as TOOLS.md writes it."""
    return f"{name}({', '.join(p[0] for p in SPECS[name]['params'])})"


def input_schema(name: str) -> dict:
    params = SPECS[name]["params"]
    props = {p: {**schema, "description": doc} for p, schema, _, doc in params}
    return {"type": "object", "properties": props, "required": [p for p, _, req, _ in params if req],
            "additionalProperties": False}


def _date(name: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise StoreError(f"{name} is a date, YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise StoreError(f"{name} is a date, YYYY-MM-DD") from None


def _with_event(store: Store, got: dict) -> dict:
    """A consequence as link returns it: the link with its derived fields, and what its event now does."""
    if got.get("relation") != "consequence":
        return got
    return {**got, "event": ledger_queries.event_now(store, got["from"])}


class Tools:
    """The sixteen tools over one store and its world."""

    def __init__(self, world: World):
        self.world = world
        self.store = world.store
        self.trace = Trace(self.store.trace_path)
        self.session: str | None = None
        self.turn: int | None = None
        self._next: int | None = None
        self.last_call: int | None = None

    # ---------------------------------------------------------------- writes

    def add(self, type: str, fields: dict, *, call: int | None = None) -> dict:
        got = self.store.add(type, fields, call=call)
        if got.get("type") == "Channel":
            return ledger_queries.channel(self.store, got["id"])
        if got.get("type") != "Portfolio":
            return got
        return {"id": got["id"], "type": got["type"], "stated": got["stated"], **ledger_queries.holdings(self.store)}

    def link(self, from_id: str, relation: str, to_id: str, fields: dict | None = None, *,
             call: int | None = None) -> dict:
        return _with_event(self.store, self.store.link(from_id, relation, to_id, fields, call=call))

    def note(self, text: str, about_ids: list[str] | None = None, *, call: int | None = None) -> dict:
        return self.store.note(text, about_ids or [], call=call)

    def edit(self, id: str, field: str, value: Any, source_id: str | None = None, *, call: int | None = None) -> dict:
        got = self.store.edit(id, field, value, source_id, call=call)
        if got.get("type") == "Channel":  # rebuilt as add builds it, and returned as add returns it
            return ledger_queries.channel(self.store, got["id"])
        if got.get("type") == "Source" and "text" in got:  # a page's text is get's; the edit says its length
            got = {k: v for k, v in got.items() if k != "text"} | {"length": len(got["text"])}
        return _with_event(self.store, got)

    def remove(self, id: str, *, call: int | None = None) -> dict:
        return self.store.remove(id, call=call)

    # ---------------------------------------------------------------- the world

    def search_web(self, text: str, params: dict | None = None, proposition_id: str | None = None, *,
                   call: int | None = None) -> dict:
        return self.world.search_web(text, params, proposition_id, call=call)

    def fetch_source(self, url: str, *, call: int | None = None) -> dict:
        return self.world.fetch_source(url)

    def search_company(self, ticker: str, kind: str, params: dict | None = None, *, call: int | None = None) -> dict:
        return self.world.search_company(ticker, kind, params, call=call)

    def search_markets(self, venue: str, endpoint: str, params: dict | None = None,
                       proposition_id: str | None = None, *, call: int | None = None) -> dict:
        return self.world.search_markets(venue, endpoint, params, proposition_id, call=call)

    # ---------------------------------------------------------------- reads

    def window_returns(self, start: str, end: str, *, call: int | None = None) -> dict:
        start, end = _date("start", start), _date("end", end)
        if start > end:
            raise StoreError(f"start, {start}, is after end, {end}: the window runs from start to end")
        found = self.store.engine.value("window_returns", window_subject(start, end))
        if found.output is None:
            return {"window": [start, end], "returns": None, "reason": found.reason}
        names = {c["id"]: c["stated"]["name"] for c in self.store.query("Channel")}
        out = found.output
        return {"window": [start, end], "basis": found.basis,
                "channels": [{"channel": c, "name": names.get(c, c), "proxy": out["proxies"][c],
                              "raw": out["returns"][c], "orthogonalized": out["orthogonalized"][c]}
                             for c in out["orthogonalized"]],
                "closes": out["closes"]}

    def get(self, id: str, text: str | None = None, *, call: int | None = None) -> dict:
        if self.store.type_of(id) is None and isinstance(id, str) and id.startswith("market:"):
            return self.world.read_market(id)  # read from its venue, and not kept
        return self.store.get(id, text)

    def query(self, type: str, where: dict | None = None, *, call: int | None = None) -> list[dict]:
        return self.store.query(type, where)

    def neighbors(self, id: str, relation: str | None = None, direction: str = "both", *,
                  call: int | None = None) -> list[dict]:
        return self.store.neighbors(id, relation, direction)

    def ledger(self, *, call: int | None = None) -> dict:
        return ledger_queries.ledger(self.store)

    def check(self, *, call: int | None = None) -> dict:
        return ledger_queries.check(self.store)

    def exposure(self, *, call: int | None = None) -> dict:
        return ledger_queries.exposure(self.store)

    # ---------------------------------------------------------------- one call

    def call(self, name: str, input: dict | None = None) -> tuple[bool, Any]:
        """Run one tool by name: (True, result) or (False, {"error", "dependents"}). The call is
        numbered, and written to the trace with its input and its result or error."""
        input = dict(input or {})
        if self._next is None:
            self._next = self.trace.next_call()
        n, self._next = self._next, self._next + 1
        self.last_call = n
        entry: dict[str, Any] = {"type": "call", "session": self.session, "turn": self.turn, "call": n,
                                 "tool": name, "input": input, "at": now()}
        with self.store.lock:  # the page reads the store between calls, never inside one
            ok, out = self._run(name, input, n)
        entry["result" if ok else "error"] = out
        self.trace.append(entry)
        return ok, out

    def _run(self, name: str, input: dict, n: int) -> tuple[bool, Any]:
        if name not in SPECS:
            return False, {"error": f"there is no tool {name!r}; the tools are {', '.join(SPECS)}", "dependents": []}
        allowed = [p[0] for p in SPECS[name]["params"]]
        unknown = [k for k in input if k not in allowed]
        missing = [p[0] for p in SPECS[name]["params"] if p[2] and p[0] not in input]
        if unknown or missing:
            return False, {"error": f"{signature(name)}: " + "; ".join(
                ([f"no parameter {', '.join(unknown)}"] if unknown else []) +
                ([f"{', '.join(missing)} is required"] if missing else [])), "dependents": []}
        try:
            return True, getattr(self, name)(**input, call=n)
        except StoreError as e:
            return False, e.as_dict()
