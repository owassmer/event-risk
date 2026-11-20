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
from typing import Any, Callable, Sequence

from harness import ledger as ledger_queries
from harness import schema
from harness.derive.channels import window_subject
from harness.derive.impact import FOLLOWED, MOVE
from harness.derive.markets import taken
from harness.derive.outcomes import named_by, root_of
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
                   ("fields", OBJECT, True, "the object's stated fields (ONTOLOGY.md, 2.1). A `Portfolio` also takes "
                                            "`holdings`, a list, each `{\"ticker\", and one of \"quantity\", "
                                            "\"dollars\" or \"weight\"}`, cash `{\"cash\", \"currency\"}`, or a "
                                            "contract `{\"market\", \"side\", \"quantity\"}`. A `Proposition` takes "
                                            "its definition where it is new, or the `id` of one that exists, which "
                                            "takes no definition fields beside it. Its other fields are optional: "
                                            "`chances`, `baseline`, `priced_in`, `episode`. Its links go under their "
                                            "relation's name (ONTOLOGY.md, 2.2) as a list, each `{\"to\": ...}` for "
                                            "a link from this proposition or `{\"from\": ...}` for a link to it, "
                                            "with the link's stated fields. The other end is an id, an alias, or "
                                            "another proposition in this same form. `as` gives a proposition an "
                                            "alias for this call only: one word with no colon or space, other than "
                                            "`yes`, `no` and `portfolio`, given once in the statement. The alias "
                                            "stands for the proposition's id in a link's other end, in a chance's "
                                            "`given` and `given_not`, and in `baseline`. Later calls use the id the "
                                            "call returns.")],
        "does": "Creates an object (2.1). For a `Proposition`, one call states it together with what is said of it: "
                "its definition, and any of its chances, its `baseline`, `priced_in` and `episode`, and its links "
                "(2.2). Links go under their relation's name, each with its other end and its stated fields; the "
                "other end is an id or another proposition stated in the same form. For a proposition that exists, "
                "give its `id` in place of a definition. Its definition stays as it is; `edit` with the field "
                "`definition` replaces it. Each field given is written as `edit` writes it: a chance replaces the one "
                "given the same propositions or is added (2.1), and `baseline`, `priced_in` and `episode` each "
                "replace their current value. A field left out is unchanged. Each link given is added, as `link` "
                "does; a relation left out, or given an empty list, removes nothing. The whole statement, with every "
                "proposition stated inside it, is written in one transaction. If the store refuses any part, nothing "
                "is written, and the refusal names that part by its path in `fields`. Returns the object. A "
                "`Portfolio` returns the date its horizon ends, and what it owns (3.1): each holding with its value, "
                "weight and day change, and what it is; each company the look-through names, by its id, its name and "
                "its weight, and those it does not name together, with how many; each industry and country that is at "
                "least 0.05% of the portfolio, by its id, with its weight, and those under it together, with how "
                "many; and the reports of each company that is 5% or more of it. A `Factor` returns how it is built "
                "from the factors before it, its residual's largest month (3.2), the history every proxy shares, and "
                "the portfolio's dollar beta to it, with each watched event's row. A `Proposition` returns one result "
                "after the whole statement is written: its structure's ranges and probabilities, with the range each "
                "chance it states can take; the row of each watched event of that structure, with the other factors "
                "that move where it states a shock; each market the statement names; each `asks`, `implies` or `way` "
                "link it made whose two texts describe different outcomes, with what differs; and the id of each "
                "proposition and link it made, by its path in `fields`.",
    },
    "link": {
        "params": [("from_id", STRING, True, "the id the link runs from"),
                   ("relation", STRING, True, "the relation, by ONTOLOGY.md's link table"),
                   ("to_id", STRING, True, "the id the link runs to; a market as market:{venue}:{id}"),
                   ("fields", OBJECT, False, "the link's stated fields (ONTOLOGY.md, 2.2).")],
        "does": "Creates a link (2.2). Returns the link, and what it now implies. An `asks`, a link of the breakdown, "
                "or a `way`: its structure's ranges and probabilities, with the row of each watched event of that "
                "structure; an `asks`, an `implies` or a `way` also says what differs between its two texts, where "
                "they describe different outcomes. A `consequence`, a `worth`, or a contract held: the row of the "
                "event it is on, and for a `consequence` the other factors that move in the worlds it applies in. A "
                "holding: what the portfolio owns, as it now stands, with each watched event's row.",
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
                "worlds stay as they are. With the field `chances`, each chance given replaces the one given the same "
                "propositions or is added (2.1). Returns what `add` or `link` returns for it.",
    },
    "remove": {
        "params": [("id", STRING, True, "the object or link")],
        "does": "Removes an object or a link, with everything derived from it (2.3). Returns what was removed, and "
                "the row of each watched event it moved, or of every event where it reached every event; a holding "
                "removed also returns what the portfolio owns.",
    },
    "search_web": {
        "params": [("text", STRING, True, "the query"),
                   ("params", OBJECT, False, "the search tool's own parameters, as DATA.md lists"),
                   ("proposition_id", STRING, False, "the proposition the search is for; recorded on it")],
        "does": "Searches the web. Returns each page's address, title and date, with the passages quoted, under the "
                "query that found it; where a proposition is given, the search is recorded on it.",
    },
    "fetch_source": {
        "params": [("url", STRING, True, "the page's http or https URL"),
                   ("text", STRING, False, "words to find in the page, returning the passages that contain them")],
        "does": "Reads a page whole, and a report at the SEC with the Exhibit 99s of its filing. Returns its id, its "
                "length and an excerpt, and, where a text is given, the passages that contain it; `get` reads it "
                "whole, or the passages that contain another text.",
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
        "params": [("start", STRING, True, "the window's first date, YYYY-MM-DD: moves run from the last close on or before it"),
                   ("end", STRING, True, "the window's last date, YYYY-MM-DD: moves run to the last close on or before it"),
                   ("names", {"type": "array", "items": STRING}, False,
                    "companies or funds to read over the window as well, each by its id, name:{TICKER}")],
        "does": "Returns how the factors moved over a past window: each proxy's move and each factor's residual over "
                "it (3.2), with the two closes each move runs between, expressed in percentage points of yield for "
                "a factor quoted as a yield. For each company "
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
                "portfolio is read as `add` returns it, as it now stands, with its dollar betas and its volatility. "
                "An industry or a country is read by its id: its weight, and the companies in it, each one the "
                "look-through names by its id, its name and its weight, and the others together, with how many. A "
                "watched "
                "event is read as it now stands, with the distribution of the portfolio's P&L on its date. "
                "A joint world is returned with the combination of its events. A page is read whole, or by the "
                "passages that contain `text`. A market or a family still at its venue is read from there.",
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
        "does": "Returns every event's row (9), in the order the events were made. With `together`, it returns "
                "instead the events named, read as one set of worlds (1.9). With `considering`, a contract, named by "
                "its market, its side and a quantity, is valued in those worlds beside the portfolio: what it pays in "
                "each, less what buying it costs, with what it offsets and what it leaves.",
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


def _structure(store: Store, pid: str, chances_of: Sequence[str] = ()) -> dict:
    """What the statements of a proposition's structure now fix (MATH.md 1.2; TOOLS.md 5): each proposition's range the
    markets imply, and its probability, or the range the statements leave with the chances that would settle it; and
    what cannot hold together, each chance in it by what it is given (structure.cannot_hold_shown), and each taken book
    stretched, where there are any. For each proposition a statement wrote chances of (`chances_of`), its chances as
    they now stand, in the order of its list, each with the range it can take (_chances_now); none where the last
    was removed."""
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
            item.update(range=[_six(node["range"]["lo"]), _six(node["range"]["hi"])])
        if "p" in node:
            item["probability"] = _six(node["p"])
        if "open" in node:
            item["left_open"] = {k: _six(node["open"][k]) if k in ("lo", "hi") else node["open"][k]
                                 for k in ("lo", "hi", "settled_by", "with") if k in node["open"]}
        shown[p] = item
    written = [p for p in dict.fromkeys(chances_of) if p in shown]
    if written:
        ranges = (store.engine.value("chance_ranges", props[0]).output or {}).get("chances") or {}
        for p in written:
            shown[p]["chances"] = _chances_now(store, p, (out.get("props") or {}).get(p) or {}, ranges)
    got: dict = {"propositions": shown}
    if found.output is None:
        got["why"] = found.cause
    if out.get("cannot_hold"):
        got["cannot_hold"] = ledger_queries.cannot_hold_read(out["cannot_hold"])
    if out.get("stretched"):
        got["stretched"] = {lid: ledger_queries.stretched_read(s) for lid, s in out["stretched"].items()}
    return got


def _six(x: Any) -> Any:
    """A share as a statement returns it, to six places (TOOLS.md 9); anything else as it is."""
    return round(x, 6) + 0.0 if isinstance(x, float) else x


def _chances_now(store: Store, pid: str, node: dict, ranges: dict) -> list[dict]:
    """A proposition's chances as they now stand (TOOLS.md 5, MATH.md 1.2), in the order of its list: each by what it is given, as
    stated, a market by its id; its figure as stated, or, for a reading of a ladder, the reading and the share it now
    gives, and for a chance that follows a market or a holding's price, the figure as stated, what it follows and the
    figure it now stands at, each under the name the analyst states it by; and the range that figure can take, null
    where the other statements cannot hold or leave what it is given no probability (structure.CHANCE_RANGES). A
    chance with no figure now, as one following a market with no priced reading has none, has no range: the structure
    reads it as no statement until it has one."""
    read = {c["key"]: c for c in node.get("chances") or []}
    out = []
    for i, c in enumerate((store.stated_of(pid) or {}).get("chances") or []):
        key = f"{pid}.chances[{i}]"  # the structure's own key for it, which the analyst never reads
        now = (read.get(key) or {}).get("value")
        item: dict = {"given": list(c.get("given") or []), "given_not": list(c.get("given_not") or [])}
        if "reading" in c:
            item.update(reading=c["reading"] if isinstance(c["reading"], str) else "calendar",
                        share=None if now is None else round(now, 6))
        elif c.get("follow"):
            item.update(value=c["value"], follow=c["follow"], now=None if now is None else round(now, 6))
        else:
            item["value"] = c["value"]
        if now is not None:  # a range is of a figure
            got = ranges.get(key)
            item["range"] = None if got is None else [round(x, 6) for x in got]
        out.append(item)
    return out


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


# What a proposition's statement returns of it: what the store derives, the statement itself being what the analyst sent,
# and its links and searches `get` reads (TOOLS.md 5).
ECHOED = ("stated", "links", "searches", "citations", "origin")


def _row_shown(row: Any) -> Any:
    """A row as a statement returns it (TOOLS.md 9): its dollars to the cent and its probabilities to six places."""
    if not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        if k == "worst_outcome" and isinstance(v, dict):
            v = {**v, **{x: round(v[x], 2 if x == "pnl" else 6) for x in ("pnl", "probability")
                         if isinstance(v.get(x), float)}}
        elif k in ("probability", "range"):
            v = [round(x, 6) if isinstance(x, float) else x for x in v] if isinstance(v, list) else \
                round(v, 6) if isinstance(v, float) else v
        elif isinstance(v, float):
            v = round(v, 2)
        out[k] = v
    return out


def _changed(store: Store, pid: str, links: set[str] = frozenset()) -> dict:
    """A watched event as a statement that can move it returns it (TOOLS.md 5): its row; where its figures wait, why, and
    each world's P&L against the baseline; and, for each consequence the statement made or changed (`links`), in each
    world that consequence sets a price in, each factor the store moved there with the stated ones (MOVES_WITH), with
    its move against the baseline and against today. The whole event, each world's prices, holdings and attribution,
    is get's (ledger.event_read)."""
    e = ledger_queries.event_now(store, pid)
    if not e.get("watched"):
        return e
    out = {"id": pid, **({"row": _row_shown(e["row"])} if "row" in e else {})}
    for k in ("figures", "why"):
        if k in e:
            out[k] = e[k]
    if "row" not in e and e.get("outcomes") is not None:
        out["outcomes"] = e["outcomes"]
    if links:
        set_in = {w for entry in [*(e.get("factors") or []), *(e.get("names") or [])]
                  for w, x in (entry.get("worlds") or {}).items() if isinstance(x, dict) and x.get("consequence") in links}
        moving: dict[str, list[dict]] = {}
        for c in e.get("factors") or []:
            for w, x in (c.get("worlds") or {}).items():
                if w in set_in and x.get("by") == ledger_queries.MOVES_WITH:
                    moving.setdefault(w, []).append({"factor": c["factor"], "name": c["name"], "move": x.get("move"),
                                                     **({k: x[k] for k in (ledger_queries.TODAY,) if k in x})})
        if moving:
            out["moving"] = moving
    return out


def _with_event(store: Store, got: dict, field: str | None = None, links: set[str] = frozenset(),
                chances_of: Sequence[str] = ()) -> dict:
    """What a statement now implies (TOOLS.md 5), beside the object or the link with its derived fields. A proposition's
    chances, a market that asks it, and a link of the breakdown or a way give the structure's figures: each
    proposition's range, and its probability or the range left open. Every statement returns, under `events`, each
    watched event whose figures it can move, as it now stands: those of the structure it is made in, the event a
    consequence or a value is stated on, or, for a contract held, those of each structure a proposition its market asks
    is in. What is stated of a joint world, a consequence, a value or its episode, returns under `together` the events
    it names read together (_joint_view). A watched proposition's row is that event's, so it is given there once, and
    not again among the proposition's derived fields. Each proposition the statement wrote chances of (`chances_of`)
    is given with its chances as they now stand, each with the range it can take (_structure)."""
    if got.get("type") == "JointWorld":
        return {**got, "together": _joint_view(store, got["id"])}
    if got.get("type") == "Proposition":
        out = {**got, **_structure(store, got["id"], chances_of)} \
            if field in ("chances", schema.DEFINITION_FIELD, None) else dict(got)
        events = _watched_in(store, got["id"])
        if events and isinstance(out.get("derived"), dict):
            out["derived"] = {k: v for k, v in out["derived"].items() if k not in ROW_FIELDS}
    else:
        relation = got.get("relation")
        if relation in ("consequence", "worth") and schema.JOINT_ID_RE.match(str(got["from"])):
            out, events = {**ledger_queries.consequence_read(got), "together": _joint_view(store, got["from"])}, []
        elif relation in ("consequence", "worth"):
            root = root_of(store.read_links, got["from"])
            out, events = ledger_queries.consequence_read(got), [root] if store.watched(root) else []
        elif relation in ("asks", "implies", "and", "or", "then", "way"):
            out, events = {**got, **_structure(store, got["from"])}, _watched_in(store, got["from"])
        elif relation == "watches":
            out, events = got, _watched_in(store, got["to"])
        elif relation == "holds" and str(got.get("to", "")).startswith("market:"):  # a contract held (TOOLS.md 5): each
            out, events = got, _watched_in(store, got["to"])  # event of the structure its market stands in
        else:
            return _with_markets(store, got, got)
    if got.get("type") == "Proposition":
        out = {k: v for k, v in out.items() if k not in ECHOED}
    elif got.get("relation") == "consequence":
        links = {*links, got["id"]}
    if events:
        out["events"] = [_changed(store, r, links) for r in events]
    return _with_markets(store, got, out)


def _with_rows(store: Store, out: dict) -> dict:
    """A statement that reaches every watched event, beside every event's row as it now stands (TOOLS.md 5): a factor's,
    which every holding's betas and every world's factors read, and the portfolio's."""
    rows = ledger_queries.rows_now(store)
    return {**out, "rows": rows} if rows else out


def _joint_view(store: Store, jid: str) -> dict:
    """The events a joint world names, read together (MATH.md 1.9; together.read), as what is stated of it returns them:
    those the portfolio still watches. Where they cannot be read together, why, in place of the view, since the
    statement itself stands."""
    from harness.derive.together import read
    events = [e for e in named_by(jid) if store.watched(e)]
    if not events:
        return {"events": [], "why": f"{jid} names no event the portfolio watches"}
    try:
        return ledger_queries.together_shown(read(store, events))
    except StoreError as e:
        return {"events": events, "why": e.message}


def _joint_of(store: Store, oid: str) -> str | None:
    """The joint world something is stated of: a joint world itself, or a consequence or a value on one."""
    if isinstance(oid, str) and schema.JOINT_ID_RE.match(oid):
        return oid
    row = store._link_row(oid) if isinstance(oid, str) and oid.startswith("link:") else None
    if row is not None and row["relation"] in ("consequence", "worth") and schema.JOINT_ID_RE.match(row["from_id"]):
        return row["from_id"]
    return None


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


# ---------------------------------------------------------------- a proposition stated whole

# What a statement of a proposition can say of it besides its definition (ONTOLOGY.md 2.1): its chances and what is said of
# its worlds; and its links, each relation the analyst makes that has a proposition at one end (ONTOLOGY.md 2.2).
SAID = tuple(f.name for f in schema.OBJECTS["Proposition"].stated if f.name not in schema.DEFINITION)
LINKED = tuple(r.name for r in schema.RELATIONS.values()
               if r.creator == schema.ANALYST and "Proposition" in (*r.sources, *r.targets))
# The order the store writes a statement's parts in, so that each meets its checks with what it rests on in place: the
# breakdown and the ways, then the markets and the pages, the event watched, the values a holding has in its worlds,
# the chances (a chance follows a market only one asks, or a holding valued in the worlds), the baseline (one of the
# worlds the ways make), the shocks against it, and the weights and episodes. Each proposition's definition is written
# first, as the statement is read.
ORDER = {"way": 0, "implies": 0, "and": 0, "or": 0, "then": 0, "asks": 1, "evidence": 1, "watches": 2,
         "worth": 3, "chances": 4, "baseline": 5, "consequence": 6, "priced_in": 7, "episode": 7}
assert set(ORDER) == set(SAID) | set(LINKED), "each part a statement can hold has its place in the order of writes"
NOTHING_WRITTEN = "Nothing was written."  # what a statement's refusal ends with: the rollback took every part
RESERVED = ("yes", "no", "portfolio")  # words a statement already reads: the sides, and the portfolio's id
NAME = re.compile(r"[^:\s]+")


class _Placed(StoreError):
    """A refusal of a part of a statement that names its own place within it, finer than the part's."""


class _Statement:
    """A proposition stated whole (TOOLS.md 5): by its definition where it is new, or by its `id` where it exists, with its
    chances and what is said of its worlds, and its links, each under its relation's name, `{"to": ...}` where it runs
    from the proposition and `{"from": ...}` where it runs to it, with the link's stated fields; each other end an id, an
    alias a proposition of the statement is given by `as` for this call, or a proposition stated the same way.

    Each part means what its own statement means: a field as `edit` states it, replacing what the proposition had, its
    chances each in place of the one given the same propositions or beside the others (Store._merged_chances), and
    a link as `link` makes it. The store writes the parts in its own order (ORDER), through the same write paths, so each
    meets the checks it meets stated alone; all in the call's one transaction, so the statement stands whole or nothing
    is written. A part the store refuses is named by its place in `fields`, and so is each proposition and link the
    statement made, which its rollback took back; where what the refusal lacks is a part the statement can hold
    (StoreError.wants), it is said as that part, at the place of the proposition it belongs to. The commit's own check,
    that each company a shock or a value names has a price, is made before the statement returns, so that it is named
    by place too."""

    def __init__(self, store: Store, call: int | None):
        self.store, self.call = store, call
        self.names: dict[str, str] = {}  # each alias a proposition is given by `as`: its id
        self.made: dict[str, str] = {}  # each part the statement wrote, by its place: its id
        self.given: dict[str, str] = {}  # each proposition the statement gives by its id: its place
        self.parts: list[tuple[int, int, str, Callable[[], None], Callable | None]] = []  # rank, order, place, write, hint
        self.judged: dict[str, str] = {}  # each link written whose two texts the judge reads, by its place: its id
        self.chances_of: list[str] = []  # each proposition the statement writes chances of, in the order written

    # ------------------------------------------------------------ reading the statement

    def proposition(self, item: Any, place: str) -> str:
        """A proposition of the statement, its id: one that exists, by its `id`, or one written now from its
        definition; and each part said of it, to be written in the store's order."""
        if not isinstance(item, dict):
            raise StoreError(f"{place}: a proposition is an object, with its definition or its `id`, what is said of "
                             f"it, and its links")
        known = (*schema.DEFINITION, *SAID, *LINKED, "as", "id")
        other = [k for k in item if k not in known]
        if other:
            raise StoreError(f"{place}: a proposition has no part {_quoted(other)}. Its parts are its definition "
                             f"({', '.join(schema.DEFINITION)}), or the `id` of one that exists; its chances and what "
                             f"is said of its worlds ({', '.join(SAID)}); its links, each under its relation's name "
                             f"({', '.join(LINKED)}); and `as`, its alias in this call")
        definition = {k: v for k, v in item.items() if k in schema.DEFINITION}
        if "id" in item:
            pid = item["id"]
            if not isinstance(pid, str) or self.store.type_of(pid) != "Proposition":
                raise StoreError(f"{place}.id: {pid!r} is no proposition in the store")
            if definition:
                raise StoreError(f"{place}: {pid} is given by its id, which takes no definition fields beside it "
                                 f"({_quoted(list(definition))}). edit({pid}, definition, ...) replaces its definition",
                                 [pid])
            self.given.setdefault(pid, place)
        else:
            pid = self.part(place, lambda: self.store._add("Proposition", definition, origin="analyst",
                                                            call=self.call))
            self.made[place] = pid
        if "as" in item:
            self.name(item["as"], pid, f"{place}.as")
        for relation in LINKED:
            if relation in item:
                self.linked(pid, relation, item[relation], f"{place}.{relation}")
        for field in SAID:
            if field in item:
                self.later(field, f"{place}.{field}", lambda pid=pid, field=field, value=item[field], at=place:
                           self.said(pid, field, self.resolved(field, value, at)))
        return pid

    def said(self, pid: str, field: str, value: Any) -> None:
        """A field said of a proposition, written as edit writes it; each chance it states kept for the return."""
        self.store.edit(pid, field, value, call=self.call, derived=False)
        if field == "chances":
            self.chances_of.append(pid)

    def name(self, name: Any, pid: str, place: str) -> None:
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in RESERVED:
            raise StoreError(f"{place}: an alias is one word with no colon or space, other than "
                             f"{', '.join(RESERVED[:-1])} and {RESERVED[-1]}")
        if name in self.names:
            raise StoreError(f"{place}: {name} is already the alias of another proposition in this statement")
        self.names[name] = pid

    def linked(self, pid: str, relation: str, items: Any, place: str) -> None:
        """A proposition's links of one relation, each to be written in the store's order."""
        if not isinstance(items, list):
            raise StoreError(f"{place}: links are a list, each {{\"to\": ...}} for a link from this proposition or "
                             f"{{\"from\": ...}} for a link to it, with the link's stated fields")
        for i, link in enumerate(items):
            at = f"{place}[{i}]"
            if not isinstance(link, dict) or ("to" in link) == ("from" in link):
                raise StoreError(f"{at}: a link names its other end once, {{\"to\": ...}} for a link from this "
                                 f"proposition or {{\"from\": ...}} for a link to it")
            way = "to" if "to" in link else "from"
            end = link[way]
            if isinstance(end, dict):
                end = self.proposition(end, f"{at}.{way}")
            elif not isinstance(end, str):
                raise StoreError(f"{at}.{way}: a link's other end is an id, an alias, or a proposition")
            fields = {k: v for k, v in link.items() if k != way}

            def write(end=end, way=way, fields=fields, at=at):
                other = self.end(end, f"{at}.{way}")
                a, b = (pid, other) if way == "to" else (other, pid)
                lid = self.store._link(a, relation, b, fields, origin="analyst", call=self.call)
                self.made[at] = lid
                if relation in ledger_queries.JUDGED:
                    self.judged[at] = lid
            hint = self.second_parent(pid) if relation == "way" and way == "from" else None
            self.later(relation, at, write, hint)

    def second_parent(self, pid: str) -> Callable[[StoreError], str | None]:
        """What a refusal of a way written `{"from": ...}` adds where the proposition is a way already, one of its
        dependents its way link: which end of a `way` link is the way."""
        def hint(e: StoreError) -> str | None:
            rows = [self.store._link_row(d) for d in e.dependents if isinstance(d, str) and d.startswith("link:")]
            if any(r is not None and r["relation"] == "way" and r["to_id"] == pid for r in rows):
                return ("Under `way`, {\"to\": ...} names a way of this proposition, and {\"from\": ...} the "
                        "proposition it is a way of.")
            return None
        return hint

    def later(self, part: str, place: str, write: Callable[[], Any], hint: Callable | None = None) -> None:
        self.parts.append((ORDER[part], len(self.parts), place, write, hint))

    def end(self, x: str, place: str) -> str:
        """A link's other end, or a proposition a chance is given: an alias given by `as`, or an id."""
        if x in self.names:
            return self.names[x]
        if ":" not in x and x not in RESERVED:
            raise _Placed(f"{place}: {x} is no alias given in this statement. A proposition stated here takes one "
                          f"with `as`")
        return x

    def resolved(self, field: str, value: Any, place: str) -> Any:
        """A field's value with each alias given by `as` read as its proposition's id: the propositions a chance is
        given, and the way the baseline names."""
        if field == "baseline" and isinstance(value, str):
            return value if value in RESERVED else self.end(value, f"{place}.baseline")
        if field == "chances" and isinstance(value, list):
            return [{**c, **{k: [self.end(x, f"{place}.chances[{i}].{k}") if isinstance(x, str) else x
                                 for x in c[k]] for k in ("given", "given_not") if isinstance(c.get(k), list)}}
                    if isinstance(c, dict) else c for i, c in enumerate(value)]
        return value

    # ------------------------------------------------------------ writing it

    def write(self, fields: dict) -> str:
        """The statement written: its propositions as read, then each part in the store's order, then the commit's own
        check of the companies its shocks and values name. Returns the root's id."""
        pid = self.proposition(fields, "fields")
        for _, _, place, write, hint in sorted(self.parts, key=lambda p: (p[0], p[1])):
            self.part(place, write, hint)
        pending = list(self.store._shocked_names)
        if pending:
            try:
                self.store._check_named(pending)
            except StoreError as e:  # at the first part of the statement it names
                raise self.refused(next((self.places()[d] for d in e.dependents if d in self.places()), "fields"),
                                   e) from None
        windowed = list(self.store._windowed)
        if windowed:  # each past window a shock in it is sized from, as the commit checks it (Store._check_windows)
            try:
                self.store._check_windows(windowed)
            except StoreError as e:  # at the part that states the window
                raise self.refused(next((self.places()[d] for d in e.dependents if d in self.places()), "fields"),
                                   e) from None
        return pid

    def part(self, place: str, write: Callable[[], Any], hint: Callable | None = None) -> Any:
        """One part written, or the statement refused: the store's refusal, named by the part's place, with each
        proposition and link the statement made named by its place, and what the refusal lacks said as a part of this
        statement."""
        try:
            return write()
        except _Placed:
            raise
        except StoreError as e:
            raise self.refused(place, e, hint) from None

    def refused(self, place: str, e: StoreError, hint: Callable | None = None) -> StoreError:
        """The store's refusal of a part, led by one path, the most specific it gives: its words alone where they
        begin with the part's place or a path under it, and otherwise the part's place before them."""
        words, added = self.placed(self.remedied(e)), hint(e) if hint is not None else None
        if added:
            words = f"{words.rstrip('.')}. {added}"
        led = words.startswith(place) and words[len(place):len(place) + 1] in ("", ".", "[", ":", " ")
        return StoreError(words if led else f"{place}: {words}", [self.dependent(d) for d in e.dependents],
                          wants=e.wants)

    def places(self) -> dict[str, str]:
        return {i: p for p, i in self.made.items()}

    def dependent(self, d: Any) -> Any:
        """A refusal's dependent, by its place where the statement made it: its id, or a field of it."""
        if not isinstance(d, str):
            return d
        head, dot, rest = d.partition(".")
        place = self.places().get(head)
        return d if place is None else f"{place}{dot}{rest}"

    def placed(self, text: str) -> str:
        """A refusal's words with each proposition and link the statement made named by its place, the rollback
        having taken its id; and a field of one, or of a proposition the statement gives by its id, as
        `prop:7.chances[0]`, read as the path the analyst wrote it at. A proposition given by its id is in the store,
        and its id alone names it."""
        places = self.places()
        paths = {**self.given, **places}
        if paths:
            stated = "|".join(re.escape(i) for i in sorted(paths, key=len, reverse=True))
            text = re.sub(rf"(?<![\w:])({stated})((?:\.[A-Za-z_]\w*(?:\[\d+\])*)+)",
                          lambda m: f"{paths[m.group(1)]}{m.group(2)}", text)
        if not places:
            return text
        ids = "|".join(re.escape(i) for i in sorted(places, key=len, reverse=True))

        def at(i: str) -> str:
            return f"the {'link' if i.startswith('link:') else 'proposition'} at {places[i]}"
        text = re.sub(rf"(?<![\w:])({ids})'s (\w+)", lambda m: f"the {m.group(2)} of {at(m.group(1))}", text)
        return re.sub(rf"(?<![\w:])({ids})(?![\w])", lambda m: at(m.group(1)), text)

    def remedied(self, e: StoreError) -> str:
        """A refusal whose remedy is a part the statement can hold, said as that part, at the place of the proposition
        it belongs to; where that proposition is in the store and not in the statement, the store's own remedy, a call
        on an id that stays."""
        if not e.wants:
            return e.message
        kind, of, *what = e.wants
        if kind == "once":  # a link the statement repeats is stated once; one already in the store is edited
            return f"{e.reason}, and two things have one link of a relation. State it once" if of in self.places() \
                else e.message
        at = self.places().get(of) or self.given.get(of)
        if at is None:
            return e.message
        # the proposition the remedy belongs to, by what it is to the refused part: the event a shock is measured in,
        # or the proposition whose chance or weight it is
        role = "this event" if kind == "field" and what[0] == "baseline" else "this proposition"
        reason = re.sub(rf"(?<![\w:]){re.escape(of)}(?![\w])", role, e.reason)
        if kind == "field":
            return f"{reason}. State it at {at}.{what[0]}"
        relation, other = what
        if relation == "asks":
            return f"{reason}. Link it at {at}.asks: {{\"to\": \"{other}\"}}"
        return f"{reason}. State them at {at}.{relation}: {{\"to\": \"{other}\", ...}}"

    def returned(self, pid: str, fields: dict) -> dict:
        """What the statement now implies (TOOLS.md 5), once: the proposition with its derived fields and its structure's
        figures, each watched event of that structure as it now stands, each market it names with its terms and its
        book, each link whose two texts describe different outcomes with what differs, by place, and `made`, the id of
        each part, by place."""
        root = self.store.get(pid, exclude_derived=("distribution",))
        shocks = {i for i in self.made.values() if i.startswith("link:")
                  and (row := self.store._link_row(i)) is not None and row["relation"] == "consequence"}
        out = _with_event(self.store, dict(root), None, shocks, self.chances_of)
        out.pop("markets", None)
        named = list(dict.fromkeys(_named(fields)))
        if named:
            claims = _claims(pid, root.get("stated") or {})
            out["markets"] = [{**market_terms(self.store, m), **({"claim": claims[m]} if m in claims else {})}
                              for m in named]
        differing = {}
        for place, lid in self.judged.items():
            said = ledger_queries.differs(self.store.get(lid))
            if said:
                differing[place] = {"link": lid, "differs": said}
        if differing:
            out["links"] = differing
        return {**out, "made": dict(self.made)}


def _quoted(keys: list[str]) -> str:
    return ", ".join(f"`{k}`" for k in keys)


# The tools that write: each call's write and the reads its result needs are one transaction, which commits only once
# every read has come back, and otherwise rolls back whole, with what was fetched kept (TOOLS.md 5).
WRITES = ("add", "link", "note", "edit", "remove")

NAMED = 3  # the readings a failed call names; past them, it counts the rest


def unread(missing: list[dict]) -> str:
    """What a call that needs readings the store could not obtain answers (TOOLS.md 5), nothing written: the readings, each
    by what it reads (harness.store.reading_words), and what their sources did: refused the request, where every one
    answered with a refusal, which asking again does not change; otherwise did not answer, in the longest time one was
    asked for, to the second, where it was a second or more. Its causes are logged for us."""
    names = [m["what"] for m in missing]
    shown = names[:NAMED] + ([f"{len(names) - NAMED} other readings"] if len(names) > NAMED else [])
    what = " and ".join([", ".join(shown[:-1]), shown[-1]]) if len(shown) > 1 else \
        shown[0] if shown else "a reading this call needs"
    what = f"{what[0].upper()}{what[1:]}"
    several = len(missing) > 1
    source = "their sources" if several else "its source"
    if missing and all(m["refused"] for m in missing):
        return f"{what} could not be read: {source} refused the request. Nothing was written."
    longest = round(max((m["seconds"] for m in missing if not m["refused"]), default=0.0))
    answered = f"did not answer in {longest} second{'' if longest == 1 else 's'}" if longest >= 1 else "did not answer"
    return (f"{what} could not be read: {source} {answered}. Nothing was written, and making the call again asks "
            f"{'them' if several else 'the source'} again.")


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
        if type == "Proposition":  # stated whole: its definition or its id, what is said of it, and its links
            statement = _Statement(self.store, call)
            try:
                with self.store.transaction():
                    pid = statement.write(fields)
            except StoreError as e:  # the statement is written whole or not at all, and the refusal says so
                raise StoreError(f"{e.message.rstrip('.')}. {NOTHING_WRITTEN}", e.dependents, wants=e.wants,
                                 reason=e.reason) from None
            return statement.returned(pid, fields if isinstance(fields, dict) else {})
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
        got = self.store.note(text, about_ids or [], call=call)  # a market it names is kept (MATH.md 3.3), and returned
        named = list(dict.fromkeys(_named(about_ids or [])))  # with its terms and its book, as any statement's (TOOLS.md 5)
        return {**got, "markets": [market_terms(self.store, m) for m in named]} if named else got

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
        chances_of = [id] if field == "chances" and got.get("type") == "Proposition" else []
        return _held_now(self.store, _with_event(self.store, got, field, chances_of=chances_of))

    def remove(self, id: str, *, call: int | None = None) -> dict:
        id = self.store.joint_name(id)
        reach = _reach(self.store, id)  # found before it goes: afterwards its links are gone with it
        joint = _joint_of(self.store, id)
        row = self.store._link_row(id) if isinstance(id, str) and id.startswith("link:") else None
        held = row is not None and _holding(row["relation"], row["to_id"])
        out = self.store.remove(id, call=call)
        if held:  # a holding removed, as one linked: what the portfolio now owns (_held_now)
            out = {**out, "portfolio": ledger_queries.portfolio(self.store)}
        if joint is not None:  # what is stated of a joint world: the events it names, read together as they now stand
            return {**out, "together": _joint_view(self.store, joint)}
        if reach is None:
            return _with_rows(self.store, out)
        events = [e for e in reach if self.store.watched(e)]
        return {**out, "events": [_changed(self.store, e) for e in events]} if events else out

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
                              "sessions": out["sessions"][c],
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
        return {"name": x["name"], "sessions": x["sessions"], MOVE: x["move"], **measured,
                **{k: x[k] for k in ("currency", "rate_move", "proxy_of", "terms") if k in x}, "basis": found.basis}

    def get(self, id: str, text: str | None = None, *, call: int | None = None) -> dict:
        id = self.store.joint_name(id)  # a joint world, under the one name it is kept by, in any order given
        with self.store.lock:
            unstated = self.store.unstated_joint(id)
        if unstated is not None:  # a joint world nothing is stated of: itself, with its events read together
            return {"id": unstated, "type": "JointWorld", "stated": {}, "links": [],
                    "together": _joint_view(self.store, unstated)}
        if isinstance(id, str) and id.startswith("jdg:"):  # the reading of a claim's texts is the store's own work
            raise StoreError(f"{id} is not in the store")
        if isinstance(id, str) and id.startswith("family:"):
            return self.world.read_family(id)  # read whole from its venue, and not kept
        with self.store.lock:
            if id == "portfolio" and not (isinstance(text, str) and text.strip()) and \
                    self.store.type_of(id) == "Portfolio":  # what it owns, in the form adding it gave
                return ledger_queries.portfolio_whole(self.store)
            if isinstance(id, str) and id.startswith(("industry:", "country:")):  # a part of the portfolio's weight
                return ledger_queries.group(self.store, id)
            if not (isinstance(text, str) and text.strip()) and self.store.type_of(id) == "Proposition" and \
                    self.store.watched(id):  # a watched event as it now stands, with its distribution
                return ledger_queries.event_read(self.store, id)
            if self.store.type_of(id) == "JointWorld" and not (isinstance(text, str) and text.strip()):
                return {**self.store.get(id), "together": _joint_view(self.store, id)}  # what it does, with the rest
            if self.store.type_of(id) == "Market":  # its book's last successful check beside its derived fields
                got = self.store.get(id, text)
                return {**got, **_checked(self.store, id, self.store.engine.value("market_price", id).output or {})}
            if not (self.store.type_of(id) is None and isinstance(id, str) and id.startswith("market:")):
                return ledger_queries.consequence_read(self.store.get(id, text))  # a consequence's figures at their places
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
