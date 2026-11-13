"""The analyst's sixteen tools: five write structure, four bring the world into the store,
seven read. Each is the store's or the world's own call, with its signature, its return, and its
refusals: a refused call returns an error and writes nothing.

`Tools.call(name, input)` is how the runtime runs one: it numbers the call, writes the store's rows with
that number, and writes the call to the store's trace with its input and its result or error.
"""

from __future__ import annotations

import logging
import traceback
import re
from concurrent.futures import wait
from datetime import date, datetime, timezone
from typing import Any, Callable

from harness import ledger as ledger_queries
from harness import schema
from harness.derive.channels import window_subject
from harness.derive.impact import FOLLOWED, MOVE
from harness.derive.markets import taken
from harness.derive.outcomes import root_of
from harness.derive.structure import store_component
from harness.derive.valuation import name_window_subject
from harness.derive.worlds import in_its_terms
from harness.derive.engine import Unavailable, Waiting
from harness.store import StandAside, Store, StoreError
from harness.trace import Trace, now
from harness.world import World, _price_fields

# Each tool: its parameters in order, as (name, JSON schema, required, what it is), and what it does.
OBJECT = {"type": "object"}
STRING = {"type": "string"}
ANY = {}

SPECS: dict[str, dict] = {
    "add": {
        "params": [("type", STRING, True, "the object type: Portfolio, Proposition, Metric, or Factor"),
                   ("fields", OBJECT, True, "the object's stated fields (ONTOLOGY.md, 2.1). A `Portfolio` also "
                                            "takes `holdings`, a list, each `{\"ticker\", and one of \"quantity\", "
                                            "\"dollars\" or \"weight\"}`, cash `{\"cash\", \"currency\"}`, or a "
                                            "contract `{\"market\", \"side\", \"quantity\"}`.")],
        "does": "Creates an object (2.1). Returns the object. A `Portfolio` returns the date its horizon ends, and "
                "what it owns (3.1): each holding with its value, weight and day change, and what it is; each fund's "
                "largest holdings, and how it divides by listing and by kind; the portfolio's weight by company, by "
                "industry and by country; and the news of each company that is 5% or more of it. A `Factor` returns "
                "how it is built from the factors before it, its residual's largest month (3.2), the history every "
                "proxy shares, and the portfolio's dollar beta to it, with each watched event's row.",
    },
    "link": {
        "params": [("from_id", STRING, True, "the id the link runs from"),
                   ("relation", STRING, True, "the relation, by ONTOLOGY.md's link table"),
                   ("to_id", STRING, True, "the id the link runs to; a market as market:{venue}:{id}"),
                   ("fields", OBJECT, False, "the link's stated fields (ONTOLOGY.md, 2.2).")],
        "does": "Creates a link (2.2). Returns the link, and what it now implies. An `asks`, a link of the breakdown, "
                "or a `way`: its structure's ranges and probabilities, with each watched event of that structure as "
                "it now stands; an `asks` or an `implies` also says what differs between its two texts, where they "
                "describe different outcomes. A `consequence`, a `worth`, or a contract held: the event it is on, as "
                "it now stands. A holding: what the portfolio owns, as it now stands, with each watched event's row.",
    },
    "note": {
        "params": [("text", STRING, True, "the reason, in words"),
                   ("about_ids", {"type": "array", "items": STRING}, False, "the ids the note concerns")],
        "does": "Writes the analyst's reasoning about one or more objects or links. Returns the note.",
    },
    "edit": {
        "params": [("id", STRING, True, "the object, link, or fetched page"),
                   ("field", STRING, True, "a stated field, or definition for a proposition's definition whole"),
                   ("value", ANY, True, "the field's new value, whole; null clears a field that is not required. For "
                                        "definition, an object of the proposition's title, its text and the fields "
                                        "code can compare, each as add takes it. Required fields must be included; an "
                                        "optional definition field left out is cleared."),
                   ("source_id", STRING, False, "the page a fact about the world cites")],
        "does": "Changes a stated field. With the field `definition`, it replaces a proposition's definition as a "
                "whole (2.1): its title, its text and the fields code can compare. Required fields must be included; "
                "an optional definition field left out is cleared. Its chances, its links and what is said of its "
                "worlds stay as they are. Returns what `add` or `link` returns for it.",
    },
    "remove": {
        "params": [("id", STRING, True, "the object or link")],
        "does": "Removes an object or a link, with everything derived from it (2.3). Returns what was removed, and "
                "each watched event it moved, as it now stands, or each event's row where it reached every event; a "
                "holding removed also returns what the portfolio owns.",
    },
    "search_web": {
        "params": [("text", STRING, True, "the query"),
                   ("params", OBJECT, False, "the search tool's own parameters, as DATA.md lists"),
                   ("proposition_id", STRING, False, "the proposition the search is for; recorded on it")],
        "does": "Searches the web. Returns each page's address, title and date, with the passages quoted; where a "
                "proposition is given, the search is recorded on it.",
    },
    "fetch_source": {
        "params": [("url", STRING, True, "the page's http or https URL"),
                   ("text", STRING, False, "words to find in the page, returning the passages that contain them")],
        "does": "Reads a page whole. Returns its id, its length and an excerpt, and, where a text is given, the "
                "passages that contain it; `get` reads it whole, or the passages that contain another text.",
    },
    "search_company": {
        "params": [("ticker", STRING, True, "the company's ticker"),
                   ("kind", STRING, True, "news, current_reports, risk_factors, annual_report, earnings, guidance, "
                                          "or ratings"),
                   ("params", OBJECT, False, "the kind's own parameters, as DATA.md lists them")],
        "does": "Reads a company's own record, from its listing date to the as-of: its news, filings, results, "
                "guidance, and analysts' ratings. Returns a list as compact rows, with who published it, and each "
                "document as a page.",
    },
    "search_markets": {
        "params": [("text", STRING, True, "what you are looking for, in your own words, with dates where they belong "
                                          "to the event"),
                   ("proposition_id", STRING, True, "the proposition the markets would bear on; the search is recorded "
                                                    "on it")],
        "does": "Searches every venue, in the analyst's own words, for markets that bear on a proposition, and "
                "records the search on it. Returns each venue's most relevant families of markets open at the "
                "as-of, the venues taking turns; a large family as each market's question, a ladder as its first and "
                "last question and its strikes, read whole with `get`.",
    },
    "window_returns": {
        "params": [("start", STRING, True, "the window's first date, YYYY-MM-DD: moves run from its close"),
                   ("end", STRING, True, "the window's last date, YYYY-MM-DD: moves run to its close"),
                   ("names", {"type": "array", "items": STRING}, False,
                    "companies or funds to read over the window as well, each by its id, name:{TICKER}")],
        "does": "Returns how the factors moved over a past window: each proxy's move and each factor's residual over "
                "it (3.2), expressed in percentage points of yield for a factor quoted as a yield. For each company "
                "or fund named, in the terms a shock on it is stated in: its move over the window (3.2), its response "
                "to the factors' moves using betas fitted on its own history up to the window's start, and its "
                "residual. For a foreign listing, those moves are in its quote currency, with the exchange rate's "
                "move beside them. A fund valued by its terms (1.6) gives its move over the window (3.2), with its "
                "reference and multiple, or its contracts. Naming a factor's proxy returns its move and identifies "
                "the factor.",
    },
    "get": {
        "params": [("id", STRING, True, "any id in the store"),
                   ("text", STRING, False, "for a page: words to find in it, returning the passages that contain them")],
        "does": "Returns one object or link whole: its stated fields, its derived fields and its links. The "
                "portfolio is read as `add` returns it, as it now stands, with its dollar betas and its "
                "volatility. A watched event is read as it now stands, with the distribution of the portfolio's "
                "P&L at the horizon's end. A page is read whole, or by the passages that contain `text`. A market "
                "or a family still at its venue is read from there.",
    },
    "query": {
        "params": [("type", STRING, True, "an object type"),
                   ("where", OBJECT, False, "field filters: a value, or {op: value} with op eq, ne, lt, le, gt, "
                                            "ge, in, or contains")],
        "does": "Returns the objects of a type that match.",
    },
    "neighbors": {
        "params": [("id", STRING, True, "any id in the store"),
                   ("relation", STRING, False, "one relation, or all"),
                   ("direction", STRING, False, "out, in, or both (the default)")],
        "does": "Returns the objects linked to one.",
    },
    "ledger": {
        "params": [("together", {"type": "array", "items": STRING}, False, "the ids of the events to read as one "
                                                                          "set of worlds"),
                   ("considering", OBJECT, False, "a contract to value in those worlds, `{\"market\", \"side\", "
                                                  "\"quantity\"}`: its market's id, its side, `yes` or `no`, and the "
                                                  "number of contracts")],
        "does": "Returns every event's row (9), in the order the events were made. With `together`, the events "
                "named are read as one set of worlds (1.9). With `considering`, a contract, named by its market, "
                "its side and a quantity, is valued in those worlds beside the portfolio: what it pays in each, "
                "less what buying it costs, with what it offsets and what it leaves.",
    },
    "check": {
        "params": [],
        "does": "Returns what cannot hold together, and each link whose two texts describe different outcomes (6).",
    },
    "exposure": {
        "params": [],
        "does": "Returns the portfolio's dollar beta and beta to each factor, and for a factor quoted as a yield "
                "its dollars for each percentage point that yield rises; the largest month of each factor's "
                "residual (3.2), in percentage points for a factor quoted as a yield, and that month applied to the "
                "dollar beta; and each proxy's price and day change.",
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


def _structure(store: Store, pid: str) -> dict:
    """What the statements of a proposition's structure now fix (MATH.md 1.2; TOOLS.md 5): each proposition's range the
    markets imply, and its probability, or the range the statements leave with the chances that would settle it; and
    what cannot hold together, and each taken book stretched, where there are any."""
    props = store_component(store, pid)
    found = store.engine.value("structure", props[0])
    if found.output is None and found.refused is not None:  # a structure the solver cannot read: the call fails
        raise StoreError(found.cause, list(found.refused))
    out = found.output or {}
    shown = {}
    nodes = [x for x in out.get("props") or {} if schema.MARKET_ID_RE.match(x)]  # a market standing as itself
    for p in (*props, *nodes):
        node = (out.get("props") or {}).get(p) or {}
        item: dict = {}
        if "range" in node:
            item.update(range=[node["range"]["lo"], node["range"]["hi"]])
        if "p" in node:
            item["probability"] = node["p"]
        if "open" in node:
            item["left_open"] = {k: node["open"][k] for k in ("lo", "hi", "settled_by", "with") if k in node["open"]}
        shown[p] = item
    got: dict = {"propositions": shown}
    if found.output is None:
        got["why"] = found.cause
    for key in ("cannot_hold", "stretched"):
        if out.get(key):
            got[key] = out[key]
    return got


def _watched_in(store: Store, pid: str) -> list[str]:
    """The watched events of a proposition's structure: each its root's, where the portfolio watches that root."""
    roots = (root_of(store.read_links, x) for x in store_component(store, pid))
    return list(dict.fromkeys(r for r in roots if store.watched(r)))


ROW_FIELDS = schema.OBJECTS["Proposition"].watched  # a watched proposition's row, among its derived fields


def _named(item: Any) -> list[str]:
    """Every market a statement's fields name, at any depth, in the order they are named."""
    if isinstance(item, str):
        return [item] if schema.MARKET_ID_RE.match(item) else []
    if isinstance(item, dict):
        return [m for v in item.values() for m in _named(v)]
    if isinstance(item, (list, tuple)):
        return [m for v in item for m in _named(v)]
    return []


def market_terms(store: Store, mid: str) -> dict:
    """A market as what a relation that names it rests on (MATH.md 1.2; TOOLS.md 5): its question, with its family's
    title where that differs, and what resolves it, its rules, the sources its venue names for deciding it, its close,
    and the date its question is based on where the venue states one, in its venue's words; and its book as the store
    holds it, its price, bid, ask and depth, its last trade with its time, and the moment its quotes stand at, in the
    form `get` gives a market (world._price_fields), or, where its book could not be read, why, the statement standing
    and its figures waiting on the book. Where a proposition asks it, the market stands as that proposition
    (structure.standing): the proposition, the side it asks through, and whether its price is taken."""
    listed = store.engine.value("market_listing", mid).output or {}
    family = (listed.get("family") or {}).get("title")
    out = {"market": mid, "question": listed.get("question"),
           **({"family": family} if family and family != listed.get("question") else {}),
           **{k: listed.get(k) for k in ("rules", "resolution_source", "close_time")},
           **({"strike_date": listed["strike_date"]} if listed.get("strike_date") else {}),  # where the venue states it
           **_book(store, mid)}
    asked = sorted(store.links_of(mid, "asks", direction="in"), key=lambda x: int(x["id"].split(":")[1]))
    if asked:
        link = asked[0]
        out["asked_by"] = {"proposition": link["from"], "link": link["id"], "side": link["stated"].get("side") or "yes",
                           "taken": taken(link)}
    return out


def _book(store: Store, mid: str) -> dict:
    """A market's book as the store holds it, with `quoted_at`, which dates the stored reading, and, while the as-of
    is today, `confirmed_at`, when the book was last read successfully and found to be the one the store holds
    (readings.confirmed), a read that found its quotes unchanged included: a failed read leaves the reading and both
    times as they were. Where the store holds no book, as for a market named first in a statement whose venue does
    not answer, why it could not be read, never figures that would read as a book with no quotes."""
    found = store.engine.value("market_price", mid)
    if found.output is None:
        return {"book_unread": found.cause or "its book is not yet read"}
    return {**_price_fields(found.output), **_checked(store, mid, found.output)}


def _checked(store: Store, mid: str, price: dict) -> dict:
    """`confirmed_at`, when a kept market's book was last read and found to be the reading the store holds
    (readings.confirmed), for a book read live: one worked out as of a past day was read then, and checked no later."""
    from harness.readings import confirmed
    if price.get("past") or not price.get("quoted_at"):
        return {}
    return {"confirmed_at": confirmed(store, "book", mid, None)}


def _claims(pid: str, stated: dict) -> dict[str, str]:
    """Where a proposition's chances state 1 given a market alone and 0 given not it alone, they state that the two
    have the same outcomes (MATH.md 1.2): the first is the market implying the proposition, the second the proposition
    implying the market. Each such market, with that sentence."""
    ones, zeros = set(), set()
    for c in stated.get("chances") or []:
        if not isinstance(c, dict) or c.get("value") not in (0, 1) or len(c.get("given") or []) + len(c.get("given_not") or []) != 1:
            continue
        if c.get("given") and c["value"] == 1:
            ones.add(c["given"][0])
        elif c.get("given_not") and c["value"] == 0:
            zeros.add(c["given_not"][0])
    return {m: f"{pid}'s chances of 1 given this market and 0 given not it state that {pid} and the market have the "
               f"same outcomes" for m in ones & zeros if schema.MARKET_ID_RE.match(m)}


def _with_markets(store: Store, got: dict, out: dict) -> dict:
    """A statement that names a market returns, under `markets`, each one's question and what resolves it, and what
    the statement claims of it where its chances equate a proposition with it."""
    named = list(dict.fromkeys(_named([got.get("stated"), got.get("from"), got.get("to")])))
    if not named:
        return out
    claims = _claims(got["id"], got.get("stated") or {}) if got.get("type") == "Proposition" else {}
    return {**out, "markets": [{**market_terms(store, m), **({"claim": claims[m]} if m in claims else {})}
                               for m in named]}


def _with_event(store: Store, got: dict, field: str | None = None) -> dict:
    """What a statement now implies (TOOLS.md 5), beside the object or the link with its derived fields. A proposition's
    chances, a market that asks it, and a link of the breakdown or a way give the structure's figures: each
    proposition's range, and its probability or the range left open. Every statement returns, under `events`, each
    watched event whose figures it can move, as it now stands: those of the structure it is made in, the event a
    consequence or a value is stated on, or, for a contract held, those of each structure a proposition its market asks
    is in. A watched proposition's row is that event's, so it is given there once, and not again
    among the proposition's derived fields."""
    if got.get("type") == "Proposition":
        out = {**got, **_structure(store, got["id"])} if field in ("chances", schema.DEFINITION_FIELD, None) \
            else dict(got)
        events = _watched_in(store, got["id"])
        if events and isinstance(out.get("derived"), dict):
            out["derived"] = {k: v for k, v in out["derived"].items() if k not in ROW_FIELDS}
    else:
        relation = got.get("relation")
        if relation in ("consequence", "worth"):
            root = root_of(store.read_links, got["from"])
            out, events = got, [root] if store.watched(root) else []
        elif relation in ("asks", "implies", "and", "or", "then", "way"):
            out, events = {**got, **_structure(store, got["from"])}, _watched_in(store, got["from"])
        elif relation == "watches":
            out, events = got, _watched_in(store, got["to"])
        elif relation == "holds" and str(got.get("to", "")).startswith("market:"):  # a contract held (TOOLS.md 5): each
            out, events = got, _watched_in(store, got["to"])  # event of the structure its market stands in
        else:
            return _with_markets(store, got, got)
    if events:
        out["events"] = [ledger_queries.event_now(store, r) for r in events]
    return _with_markets(store, got, out)


def _with_rows(store: Store, out: dict) -> dict:
    """A statement that reaches every watched event, beside every event's row as it now stands (TOOLS.md 5): a factor's,
    which every holding's betas and every world's factors read, and the portfolio's."""
    rows = ledger_queries.rows_now(store)
    return {**out, "rows": rows} if rows else out


def _reach(store: Store, oid: str) -> list[str] | None:
    """The watched events a removal can move, found before it goes (TOOLS.md 5): None where it reaches every event, as a
    factor and a holding do; the event a consequence or a value is stated on; the events of the structure a
    proposition, a link of the breakdown, or a contract held is in; and no event for a watches link, which takes its
    event out of the ledger, or for what no figure reads."""
    if store.type_of(oid) == "Factor":
        return None
    if store.type_of(oid) == "Proposition":
        return [e for e in _watched_in(store, oid) if e != oid]
    row = store._link_row(oid) if isinstance(oid, str) and oid.startswith("link:") else None
    if row is None:
        return []
    relation, a, b = row["relation"], row["from_id"], row["to_id"]
    if relation == "holds":
        return _watched_in(store, b) if b.startswith("market:") else None
    if relation in ("consequence", "worth"):
        root = root_of(store.read_links, a)
        return [root] if store.watched(root) else []
    if relation in schema.BREAKDOWN:
        return list(dict.fromkeys(_watched_in(store, a) + _watched_in(store, b)))
    return []


def _held_now(store: Store, got: dict) -> dict:
    """A holding of a name or of cash, linked or edited, beside what the portfolio owns now (TOOLS.md 5): its holdings, the
    three lists and the news, as adding the portfolio returns them; and every event's row, which every holding reaches.
    A contract held returns the events it is on (_with_event)."""
    if _holding(got.get("relation"), got.get("to")):
        return _with_rows(store, {**got, "portfolio": ledger_queries.portfolio(store)})
    return got


def _holding(relation: Any, to: Any) -> bool:
    """A holding of a name or of cash: what the portfolio owns, as against a contract held."""
    to = str(to or "")
    return relation == "holds" and bool(schema.NAME_ID_RE.match(to) or schema.CASH_ID_RE.match(to))


# The tools that write: each call's write and the reads its result needs are one transaction, which commits only once
# every read has come back, and otherwise rolls back whole, with what was fetched kept (TOOLS.md 5).
WRITES = ("add", "link", "note", "edit", "remove")

NAMED = 3  # the readings a failed call names; past them, it counts the rest


def unread(names: list[str]) -> str:
    """What a call that needs readings the store could not obtain answers (TOOLS.md 5), nothing written: the readings, each
    by what it reads (harness.store.reading_words), and that the call can be made again. Its causes are logged for us."""
    shown = names[:NAMED] + ([f"{len(names) - NAMED} other readings"] if len(names) > NAMED else [])
    what = " and ".join([", ".join(shown[:-1]), shown[-1]]) if len(shown) > 1 else \
        shown[0] if shown else "a reading this call needs"
    return f"{what[0].upper()}{what[1:]} could not be read just now. Nothing was written, and the call can be made again."


# What a call answers where an error of ours escapes it: the error itself goes to the log and to the trace.
FAILED = "This call failed inside the store, and nothing was written. It can be made again."

log = logging.getLogger("harness.tools")

# The tools that wait on a venue or the web: each holds the store's lock only while it reads or writes the store, so
# the page's readings and renders go on while it waits (harness.world).
OUTSIDE_THE_LOCK = ("search_web", "fetch_source", "search_company", "search_markets", "get")


# What a call answers where an error of ours escapes a tool beyond the sixteen, which writes no store.
FAILED_OUTSIDE = "This call failed: an error of ours, which is logged. It can be made again."


class Tools:
    """The sixteen tools over one store and its world, and any others a conversation is given (`extra`), each a
    function of the call's input to (True, result) or (False, {"error", ...}), run outside the store's lock."""

    def __init__(self, world: World, extra: dict[str, Callable[[dict], tuple[bool, Any]]] | None = None):
        self.world = world
        self.store = world.store
        self.trace = Trace(self.store.trace_path)
        self.extra = dict(extra or {})
        self.session: str | None = None
        self.turn: int | None = None
        self._next: int | None = None
        self.last_call: int | None = None
        self._escaped: str | None = None  # the error of ours that ended the call being made, for its trace

    def rebind(self, world: World) -> None:
        """The tools over another store and its world, the conversation going on: its trace, its session, its turn and
        the numbering of its calls stay."""
        self.world = world
        self.store = world.store

    # ---------------------------------------------------------------- writes

    def _proxy_listed(self, ticker: Any) -> None:
        """A channel's proxy names a listed instrument, or the call is refused before anything is written (TOOLS.md 5):
        its source, read for the store's as-of, says whether it lists one. Where no client here reads the listing,
        or the store has no as-of yet, nothing here can say, and the channel is written as its prices would be read."""
        if not isinstance(ticker, str) or not re.fullmatch(schema.TICKER, ticker):
            return  # the store's own rules refuse it, with theirs
        try:
            listed = self.world.listed(ticker)
        except Unavailable:
            return
        if not listed:
            raise StoreError(f"{ticker} names no listed instrument at the store's as-of: a factor's proxy is a "
                             f"listed fund, share, or currency or crypto pair, whose price measures the factor")

    def _held_listed(self, tickers: list[str], asof: str | None) -> None:
        """Each holding's ticker names a security, or the call is refused before anything is written, naming the
        tickers that name none (MATH.md 3.1; TOOLS.md 5): a listing its reference gives, a US one's or, for a listing
        abroad named by its exchange's symbol, its own market's, or a currency or crypto pair its source quotes, at the
        as-of (World.listed); or else a fund's share class the SEC lists, as a mutual fund's is, which no listing's
        reference gives. `{ISIN}:{country}` is read by its home market's own reads. Where no
        client here reads a listing or the SEC's list, nothing here can say, and the holding's own reads decide."""
        from harness.derive import series
        from harness.derive.identity import FUNDS_FILE, us_ticker
        from harness.derive.portfolio import pseudo
        none, classes = [], None
        for ticker in dict.fromkeys(tickers):
            if not ticker or pseudo(ticker) or (series.is_abroad(ticker) and not series.symbol_abroad(ticker)) or \
                    not (us_ticker(ticker) or series.is_pair(ticker)):
                continue
            try:
                if self.world.listed(ticker, asof):
                    continue
                if series.is_pair(ticker):
                    none.append(ticker)
                    continue
                if classes is None:
                    row = self.store.fetch_data("sec_funds", FUNDS_FILE, covering=(asof, asof))
                    with self.store.lock:
                        listed = self.store.data_payload(row["id"]) or {}
                    at = (listed.get("fields") or []).index("symbol") if "symbol" in (listed.get("fields") or []) else None
                    classes = {str(r[at]).upper() for r in listed.get("data") or []} if at is not None else set()
            except Unavailable:
                return
            if ticker not in classes:
                none.append(ticker)
        if none:
            names = " and ".join([", ".join(none[:-1]), none[-1]] if len(none) > 1 else none)
            raise StoreError(f"{names} {'names' if len(none) == 1 else 'name'} no listed security or fund at {asof}. A "
                             f"holding is given by the ticker of the security or fund held.")

    def _references_ahead(self, fields: dict) -> None:
        """Each holding's reference and the SEC's list of tickers, read side by side before the portfolio is written:
        each holding links to its company's name, which they give (harness.derive.identity.name_of)."""
        from harness.derive.identity import SEC_FILE, us_ticker
        asof = fields["asof"]
        tickers = [str(h.get("ticker") or "").strip().upper() for h in fields.get("holdings") or []
                   if isinstance(h, dict)]
        for kind, subjects in (("ticker_ref", [t for t in tickers if us_ticker(t)]), ("sec_tickers", [SEC_FILE])):
            if subjects and self.store.fetchers.get(kind) is not None:
                try:
                    self.store.prefetch(kind, subjects, (asof, asof))
                except Exception:  # a read that fails here is read again where it is needed, which records why
                    pass

    def add(self, type: str, fields: dict, *, call: int | None = None) -> dict:
        if type == "Factor" and isinstance(fields, dict):
            self._proxy_listed(fields.get("proxy"))
        # A Portfolio's answer is what it owns (TOOLS.md 5): its holdings priced, its funds looked through, and the news,
        # each read before it returns. Its exposures and volatility, which read every name's two years of prices and
        # which nothing in it reads, are computed when a call reads them, or by the page straight after it
        # (harness.server.exposures), and a read they need that fails fails that call, not this one.
        if type == "Portfolio" and isinstance(fields, dict) and isinstance(fields.get("asof"), str):
            self._references_ahead(fields)
            self._held_listed([str(h.get("ticker") or "").strip().upper() for h in fields.get("holdings") or []
                               if isinstance(h, dict)], fields["asof"])
        got = self.store.add(type, fields, call=call, derived=type != "Portfolio")
        if got.get("type") == "Factor":  # every holding's betas, and every world's factors, read it
            return _with_rows(self.store, ledger_queries.factor(self.store, got["id"]))
        if got.get("type") != "Portfolio":
            return got
        return ledger_queries.portfolio(self.store)

    def link(self, from_id: str, relation: str, to_id: str, fields: dict | None = None, *,
             call: int | None = None) -> dict:
        if relation == "holds" and isinstance(to_id, str) and schema.NAME_ID_RE.match(to_id):
            given = (fields or {}).get("ticker") if isinstance(fields, dict) else None
            ticker = str(given or to_id.split(":", 1)[1]).strip().upper()
            self._held_listed([ticker], self.store.meta("asof"))
        return _held_now(self.store, _with_event(self.store, self.store.link(from_id, relation, to_id, fields,
                                                                            call=call)))

    def note(self, text: str, about_ids: list[str] | None = None, *, call: int | None = None) -> dict:
        return self.store.note(text, about_ids or [], call=call)

    def edit(self, id: str, field: str, value: Any, source_id: str | None = None, *, call: int | None = None) -> dict:
        if field == "proxy" and self.store.type_of(id) == "Factor":
            self._proxy_listed(value)
        got = self.store.edit(id, field, value, source_id, call=call,
                              derived=self.store.type_of(id) != "Portfolio")  # read as add returns it, below
        if got.get("type") in ("Factor", "Portfolio"):  # rebuilt as add builds it, and returned as add returns it,
            # beside every event's row where what changed reaches them all: a factor's name is its words alone
            built = ledger_queries.factor(self.store, got["id"]) if got["type"] == "Factor" else \
                ledger_queries.portfolio(self.store)
            return built if got["type"] == "Factor" and field == "name" else _with_rows(self.store, built)
        if got.get("type") == "Source" and "text" in got:  # a page's text is get's; the edit says its length
            got = {k: v for k, v in got.items() if k != "text"} | {"length": len(got["text"])}
        return _held_now(self.store, _with_event(self.store, got, field))

    def remove(self, id: str, *, call: int | None = None) -> dict:
        reach = _reach(self.store, id)  # found before it goes: afterwards its links are gone with it
        row = self.store._link_row(id) if isinstance(id, str) and id.startswith("link:") else None
        held = row is not None and _holding(row["relation"], row["to_id"])
        out = self.store.remove(id, call=call)
        if held:  # a holding removed, as one linked: what the portfolio now owns (_held_now)
            out = {**out, "portfolio": ledger_queries.portfolio(self.store)}
        if reach is None:
            return _with_rows(self.store, out)
        events = [e for e in reach if self.store.watched(e)]
        return {**out, "events": [ledger_queries.event_now(self.store, e) for e in events]} if events else out

    # ---------------------------------------------------------------- the world

    def search_web(self, text: str, params: dict | None = None, proposition_id: str | None = None, *,
                   call: int | None = None) -> dict:
        return self.world.search_web(text, params, proposition_id, call=call)

    def fetch_source(self, url: str, text: str | None = None, *, call: int | None = None) -> dict:
        return self.world.fetch_source(url, text)

    def search_company(self, ticker: str, kind: str, params: dict | None = None, *, call: int | None = None) -> dict:
        return self.world.search_company(ticker, kind, params, call=call)

    def search_markets(self, text: str, proposition_id: str, *, call: int | None = None) -> dict:
        return self.world.search_markets(text, proposition_id, call=call)


    # ---------------------------------------------------------------- reads

    def window_returns(self, start: str, end: str, names: list[str] | None = None, *, call: int | None = None) -> dict:
        start, end = _date("start", start), _date("end", end)
        if start > end:
            raise StoreError(f"start, {start}, is after end, {end}: the window runs from start to end")
        # the factors' window and each name's, each read whose reads are under way standing aside beside the others,
        # so that the call stands aside once for all of their reads, however many names it is given
        waits: list[Waiting] = []
        found = self._beside(lambda: self.store.engine.value("window_returns", window_subject(start, end)), waits)
        if found is not None and found.output is None:  # a call that fails returns its reason (TOOLS.md 5): the factors'
            raise StoreError(found.cause)  # window's, which every name is measured against, before any name's
        read = [self._beside(lambda: self._name_window(start, end, n), waits) for n in dict.fromkeys(names or [])]
        if waits:  # every read met that is under way, stood aside for at once; any other wait, as it came
            if self.store.stood_aside():
                raise StandAside("a read this call needs is under way")
            raise waits[0]
        factors = sorted(self.store.query("Factor"), key=lambda c: (c["stated"]["order"], c["id"]))
        titles = {c["id"]: c["stated"]["name"] for c in factors}
        out = found.output  # each factor's residual is what the ones before it leave: given broad to narrow (3.2)
        ordered = [c["id"] for c in factors if c["id"] in out["own_part"]]
        stated = {c["id"]: c["stated"] for c in factors}

        def terms(c: str, x: float | None) -> float | None:  # in the terms its shocks are stated in (1.3)
            return None if x is None else in_its_terms(stated.get(c) or {}, x)
        return {"window": [start, end], "basis": found.basis,
                "factors": [{"factor": c, "name": titles.get(c, c), "proxy": out["proxies"][c],
                              **({"yield": stated[c]["yield"]} if (stated.get(c) or {}).get("duration") else {}),
                              MOVE: terms(c, out["moves"][c]), FOLLOWED: terms(c, out["own_part"][c])}
                             for c in ordered + [c for c in out["own_part"] if c not in titles]],
                **({"names": read} if read else {}), "closes": out["closes"]}

    def _beside(self, read: Callable[[], Any], waits: list) -> Any:
        """A value read beside others in one call: what it gives, or, where it waits, None, with the wait kept in
        `waits`, so that the call reads the others before it stands aside once for every read they met
        (Store.standing_aside)."""
        try:
            return read()
        except Waiting as e:
            waits.append(e)
            return None

    def _name_window(self, start: str, end: str, nid: str) -> dict:
        """One name over the window (valuation.NAME_WINDOW), in the terms a shock on it is stated in: its move, and,
        where it responds to the factors by its betas, its response and its residual; or the call fails with why, as a
        call does that needs a reading the store could not obtain (TOOLS.md 5)."""
        if not isinstance(nid, str) or not schema.NAME_ID_RE.match(nid):
            raise StoreError(f"{nid} is not a company or a fund: each is named name:{{TICKER}}")
        found = self.store.engine.value("name_window", name_window_subject(start, end, nid))
        if found.output is None:
            raise StoreError(found.cause)
        x = found.output
        measured = {"response": x["response"], FOLLOWED: x["residual"], "betas": x["betas"]} if "residual" in x else {}
        return {"name": x["name"], MOVE: x["move"], **measured,
                **{k: x[k] for k in ("currency", "rate_move", "proxy_of", "terms") if k in x}, "basis": found.basis}

    def get(self, id: str, text: str | None = None, *, call: int | None = None) -> dict:
        if isinstance(id, str) and id.startswith("jdg:"):  # the reading of a claim's texts is the store's own work
            raise StoreError(f"{id} is not in the store")
        if isinstance(id, str) and id.startswith("family:"):
            return self.world.read_family(id)  # read whole from its venue, and not kept
        with self.store.lock:
            if id == "portfolio" and not (isinstance(text, str) and text.strip()) and \
                    self.store.type_of(id) == "Portfolio":  # what it owns, in the form adding it gave
                return ledger_queries.portfolio_whole(self.store)
            if not (isinstance(text, str) and text.strip()) and self.store.type_of(id) == "Proposition" and \
                    self.store.watched(id):  # a watched event as it now stands, with its distribution
                return ledger_queries.event_read(self.store, id)
            if self.store.type_of(id) == "Market":  # its book's last successful check beside its derived fields
                got = self.store.get(id, text)
                return {**got, **_checked(self.store, id, self.store.engine.value("market_price", id).output or {})}
            if not (self.store.type_of(id) is None and isinstance(id, str) and id.startswith("market:")):
                return self.store.get(id, text)
        return self.world.read_market(id)  # read from its venue, and not kept

    def query(self, type: str, where: dict | None = None, *, call: int | None = None) -> list[dict]:
        if type == "Judgment":  # the reading of a claim's texts is the store's own work
            raise StoreError(f"there is no type {type!r}; the types are "
                             f"{', '.join(t for t in schema.OBJECTS if t != 'Judgment')}")
        return self.store.query(type, where)

    def neighbors(self, id: str, relation: str | None = None, direction: str = "both", *,
                  call: int | None = None) -> list[dict]:
        return self.store.neighbors(id, relation, direction)

    def ledger(self, together: list[str] | None = None, considering: dict | None = None, *,
               call: int | None = None) -> dict:
        return ledger_queries.ledger_read(self.store, together, considering)

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
                                 "tool": name, "input": input, "at": now(), "store": self.store.dir.name}
        if name in self.extra:
            return self._call_extra(name, input, entry)
        failed: set = set()  # the reads that failed once, across the call's attempts
        met: dict = {}  # each read it stood aside for, with the read it waited on, across its attempts
        self.store.missing(since=True)
        self._escaped = None
        passes = 0
        with self.store.lock.meter() as lock:  # what the call waited for the store's lock and held it
            while True:  # made again for as long as an attempt stands aside for what it has not met (standing_aside)
                passes += 1
                # the Computations its result names are the conversation's from here, kept before the lock is let go
                #, so that no read between the call and its trace replaces them away
                if name in OUTSIDE_THE_LOCK:  # it reads a venue or the web, and takes the lock only for the store's part
                    ok, out, waits = self._attempt(name, input, n, failed, met)
                    with self.store.lock:
                        self.store.name(out)
                else:
                    with self.store.lock:  # the page reads the store between calls, never inside one
                        ok, out, waits = self._attempt(name, input, n, failed, met)
                        self.store.name(out)
                if not waits:
                    break
                wait(waits)  # holding no lock: the page renders, and calls that need none of these reads answer
        entry["result" if ok else "error"] = out
        if self._escaped:  # ours, for us: the analyst is answered FAILED
            entry["exception"] = self._escaped
        # when it ended, how many attempts it made, and its seconds waiting for the store's lock and holding it: with
        # `at`, the call's time divides into the store's and the rest, its reads from outside among them
        entry.update(ended=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), passes=passes, waited=round(lock["waited"], 3), held=round(lock["held"], 3))
        self.trace.append(entry)
        return ok, out

    def _call_extra(self, name: str, input: dict, entry: dict) -> tuple[bool, Any]:
        """A tool beyond the sixteen, numbered and traced as each of them is."""
        try:
            ok, out = self.extra[name](input)
        except Exception:  # an error of ours: logged and traced, and the call fails
            log.exception("call %s, %s failed", entry["call"], name)
            entry["exception"] = traceback.format_exc()
            ok, out = False, {"error": FAILED_OUTSIDE, "dependents": []}
        entry["result" if ok else "error"] = out
        entry["ended"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.trace.append(entry)
        return ok, out

    def _attempt(self, name: str, input: dict, n: int, failed: set, met: dict) -> tuple[bool, Any, list]:
        """One attempt at a call, standing aside for any read it needs that is not yet in (Store.standing_aside): its
        result, and the reads it stood aside for, which its caller waits for before the next attempt. The first
        read, adding the portfolio, reads its own as it always has: it is what the analyst waits on, and the page
        fills from its stages."""
        if name == "add" and isinstance(input.get("type"), str) and input["type"] == "Portfolio":
            return (*self._run(name, input, n), [])
        with self.store.standing_aside(failed, met) as waits:
            ok, out = self._run(name, input, n)
        return ok, out, list(waits)

    def _run(self, name: str, input: dict, n: int) -> tuple[bool, Any]:
        if name not in SPECS:
            return False, {"error": f"there is no tool {name!r}; the tools are {', '.join(SPECS)}", "dependents": []}
        allowed = [p[0] for p in SPECS[name]["params"]]
        unknown = [k for k in input if k not in allowed]
        missing = [p[0] for p in SPECS[name]["params"] if p[2] and p[0] not in input]
        about = {p[0]: p[3] for p in SPECS[name]["params"]}  # each parameter as its declaration says what it is
        if unknown or missing:
            return False, {"error": f"{signature(name)}: " + "; ".join(
                ([f"no parameter {', '.join(unknown)}"] if unknown else []) +
                [f"{p} is required: {about[p]}" for p in missing]), "dependents": []}
        try:
            with self.store.engine.strict():  # every value the result reads completes, or the call fails
                if name in WRITES:
                    with self.store.transaction():
                        result = getattr(self, name)(**input, call=n)
                        if self.store.stood_aside():  # a read it stood aside for: its write waits for it, rolled back
                            raise StandAside("a read this call needs is under way")
                        return True, ledger_queries.as_read(result)
                return True, ledger_queries.as_read(getattr(self, name)(**input, call=n))
        except StoreError as e:
            return False, e.as_dict()
        except Waiting as e:
            if not self.store.stood_aside():
                log.warning("call %s, %s: %s", n, name, e)
            return False, {"error": unread(self.store.missing()), "dependents": []}
        except Exception:  # an error of ours: logged and traced, and the call fails with nothing written
            log.exception("call %s, %s failed inside the store", n, name)
            self._escaped = traceback.format_exc()
            return False, {"error": FAILED, "dependents": []}
