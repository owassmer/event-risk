"""The ledger, check, and what a store records: queries over the store.

Each reads derived values on read, as `get` does, and computes no number of its own but the sums of what it leaves
out: a row's fields are the watched proposition's derived fields and its links' derived fields, and the contributions,
what each consequence sets, and the names' idiosyncratic terms are read from the impact Computation through the basis
its fields carry. A row names each holding's largest names and sums the shares of the rest, and what the portfolio
owns gives each fund's largest holdings, places, and kinds of asset and sums the others, so that each stays readable;
every name is in the impact Computation, and every holding of a fund in the look-through's.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import weakref
from datetime import date, datetime, timedelta
from typing import Any

from harness import schema
from harness.derive.checks import disputed, implication_question, market_question
from harness.derive.engine import Inputs, Unavailable, Waiting
from harness.derive.outcomes import ways_under
from harness.derive.portfolio import NAME_TYPES, pseudo, reference_ticker, ticker_of
from harness.derive.markets import taken
from harness.derive.structure import bands, store_component, subject
from harness.derive.oneform import same_worlds
from harness.derive.worlds import in_its_terms, points, side_mean
from harness.store import Store, StoreError
from harness.trace import Trace
from harness.venues.massive import answered

log = logging.getLogger("harness.ledger")  # a read that failed past its retries, for us: the analyst never sees it

MOSTLY = 3  # the three names with the largest abs(by_name)
NAMED = 5  # the names a row gives each holding, the largest through it (impact._size)
ROW_FIELDS = ("probability", "left_open", "range", "u_E", "full_impact", "contributors", "by_holding", "yes", "no", "expected",
              "volatility", "worst_outcome", "worlds")
TOL = 1e-9


def _value(derived: dict, field: str) -> Any:
    return (derived.get(field) or {}).get("value")


def _portfolio(store: Store) -> dict:
    _stated(store)
    return store.get("portfolio")


def _stated(store: Store) -> dict:
    """The portfolio's stated fields, with none of what the store derives for it."""
    if store.type_of("portfolio") != "Portfolio":
        raise StoreError("the store has no portfolio: add(\"Portfolio\", ...) adds it")
    return store.stated_of("portfolio")


def watched_events(store: Store) -> list[str]:
    """The events the portfolio watches, in the order they were watched: the ledger's order (TOOLS.md 9), which every
    reader of its rows takes from here."""
    return [link["to"] for link in store.links_of("portfolio", "watches")]


def _title(store: Store, oid: str) -> str | None:
    return store.stated_of(oid).get("title") or store.stated_of(oid).get("name")


def _company(store: Store, nid: str) -> str | None:
    """The name the ticker reference gives. None where the source says there is none; a reference still being read
    raises Waiting, so that a call stands aside for it and the page shows its placeholder, and no row is given
    without the name its reference would give: a successful result is the true state."""
    asof = store.meta("asof")
    ticker = nid.split(":", 1)[1]
    try:
        row = store.data("ticker_ref", ticker, covering=(asof, asof), required=False)
    except Unavailable:
        return None
    if row is None:
        return None
    return ((store.data_payload(row["id"]) or {}).get("results") or {}).get("name")


# ---------------------------------------------------------------- one row


def _ways_of(store: Store, pid: str) -> list[str]:
    """Every way of an event, at any depth, each parent's in the order they were linked."""
    return [w["id"] for w in ways_under(store.read_links, pid, lambda r: bands(Inputs(store.engine), r))]


def outcomes(store: Store, pid: str, impact: dict | None = None) -> list[dict] | None:
    """The event's outcomes as a tree, from its two sides down (MATH.md 1.1; TOOLS.md 9): each by its id with its parent, its
    side, its title (a side's Yes or No, a way's its own), and its probability; with the impact, the weight today's
    prices put on it and the portfolio's P&L there, a parent's the average of its ways'; each world marked, and the
    baseline. None where its worlds have no probabilities."""
    if impact and impact.get("outcomes"):
        found = [dict(o) for o in impact["outcomes"]]
    else:
        tree = store.engine.value("outcomes", pid).output
        if not tree or tree.get("why"):
            return None
        nodes = tree["nodes"]
        found = []

        def visit(n: str) -> None:
            node = nodes[n]
            found.append({"id": n, "parent": node["parent"], "side": node["side"], "p": node["p"],
                          **({"ways": list(node["ways"])} if node["ways"] else {})})
            for k in node["ways"]:
                visit(k)

        for side in ("yes", "no"):
            visit(side)
    base = store.stated_of(pid).get("baseline")
    for o in found:
        o["title"] = outcome_title(store, pid, o["id"])
        o["world"] = "ways" not in o
        if o["id"] == base:
            o["baseline"] = True
    return found


def _decomposition(store: Store, pid: str) -> list[str]:
    return store_component(store, pid)


def reading(store: Store, pid: str) -> tuple[dict, dict]:
    """A proposition's reading in its structure (harness.derive.structure), and the structure's: what cannot hold
    together and each stretched book; empty where it is not yet read."""
    found = store.engine.value("structure", subject(store, pid))
    out = found.output or {}
    return (out.get("props") or {}).get(pid) or {}, {k: out[k] for k in ("cannot_hold", "stretched") if k in out}


def _searches(store: Store, props: list[str]) -> dict:
    """A reviewer's counts over the propositions of the event's decomposition."""
    markets, web, company, seen = [], [], [], set()
    counted = {"search_markets": markets, "search_web": web, "search_company": company}
    for pid in props:
        for s in store._searches_of(pid):  # recorded on it, or returned a market or a page it uses
            if s["id"] not in seen and s["tool"] in counted:  # a store can hold a search by a tool since withdrawn
                seen.add(s["id"])
                counted[s["tool"]].append(s)
    returned = {m for s in markets for m in s["results"]}
    linked = {link["to"] for pid in props for link in store.links_of(pid, "asks")}
    sources = {link["to"] for pid in props for link in store.links_of(pid, "evidence")}
    return {"market_searches": len(markets), "markets_returned": len(returned), "markets_linked": len(linked),
            "web_searches": len(web), "sources_linked": len(sources),
            "queries": [{"search": s["id"], "proposition": s["proposition"], "text": s.get("query"),
                         "returned": len(s["results"])} for s in markets],
            "web_queries": [{"search": s["id"], "proposition": s["proposition"], "query": s.get("query"),
                             "returned": len(s["results"])} for s in web],
            "company_searches": len(company),
            "company_queries": [{"search": s["id"], "ticker": s.get("query"), "kind": s.get("endpoint"),
                                 "params": s["params"], "returned": len(s["results"])} for s in company]}


def _markets(store: Store, props: list[str]) -> list[dict]:
    """Every market linked in the breakdown, with its price, bid, ask, depth, whether it is open, whether its price is
    taken as the probability, the judge's check both ways, and the bound it gives."""
    out = []
    for pid in props:
        for link in store.links_of(pid, "asks"):
            got = store.get(link["id"])["derived"]
            market = store.get(link["to"])["derived"]
            out.append({"link": link["id"], "proposition": pid, "market": link["to"],
                        "question": _value(market, "question"), "close_time": _value(market, "close_time"),
                        "side": link["stated"].get("side", "yes"), "taken": taken(link),
                        **{k: _value(market, k) for k in ("price", "bid", "ask", "depth", "open")},
                        "forward": _value(got, "forward"), "converse": _value(got, "converse"),
                        "bound": _value(got, "bound")})
    return out


def _held_through(store: Store) -> dict[str, list[str]]:
    """Each name the portfolio reaches through its funds, with the funds it is held through (TOOLS.md 9): the portfolio's
    own holdings whose look-through reaches it, at any depth, largest first by the dollars each carries of it. A fund
    inside a held fund is one step down that path, and is not one the member holds."""
    looked = store.engine.value("lookthrough", "portfolio").output or {}
    held = {link["id"]: link["to"] for link in store.links_of("portfolio", "holds")}
    reached: dict[str, dict[str, float]] = {}
    for hid, names in (looked.get("by_holding") or {}).items():
        fund = held.get(hid)
        for nid, dollars in names.items():
            if fund and nid != fund:
                reached.setdefault(nid, {})[fund] = reached.get(nid, {}).get(fund, 0.0) + dollars
    return {nid: sorted(funds, key=lambda f: (-abs(funds[f]), f)) for nid, funds in reached.items()}


def _by_name(store: Store, shares: list[dict] | None) -> list[dict] | None:
    if shares is None:
        return None
    out = []
    held = {link["to"] for link in store.links_of("portfolio", "holds")}
    through = _held_through(store) if shares else {}
    for s in shares[:MOSTLY]:  # sorted by abs(D); every name is in the impact Computation, the row's basis
        nid = s["name"]
        out.append({**s, "company": _company(store, nid) if store.type_of(nid) == "Name" else None,
                    "held_directly": nid in held, "through": through.get(nid, [])})
    return out


def _by_holding(shares: list[dict] | None) -> list[dict] | None:
    """Each holding with the NAMED names of the largest shares through it, ranked in the impact Computation by the largest
    of their full impact and their two outcomes, and
    the rest as their count and summed share, in D and in each world, so the names with the remainders come to the
    holding in each."""
    if shares is None:
        return None
    out = []
    for h in shares:
        named, rest = h["names"][:NAMED], h["names"][NAMED:]
        summed = {"rest": len(rest), **{k: sum(n[k] for n in rest) for k in ("full_impact", "yes", "no")},
                  "worlds": {w: sum(n["worlds"][w] for n in rest) for w in (rest[0]["worlds"] if rest else {})}}
        out.append({**h, "names": named + ([summed] if rest else [])})
    return out


def row(store: Store, pid: str) -> dict:
    """One watched proposition with every field the ledger's sentences read, each null where it has no value."""
    obj = store.get(pid, derived_fields=ROW_FIELDS)
    stated, derived = obj["stated"], obj["derived"]
    out: dict[str, Any] = {"id": pid, **{k: stated.get(k) for k in (
        "title", "text", "metric", "comparison", "value", "window_start", "window_end", "kind", "resolution_source")}}
    for f in ROW_FIELDS:
        out[f] = _value(derived, f)
    out["basis"] = {f: derived[f].get("basis") for f in ROW_FIELDS if derived.get(f, {}).get("basis")}
    impact = store.computation_output(out["basis"]["full_impact"]) if out["basis"].get("full_impact") else {}
    channels = {c["id"]: c["stated"]["name"] for c in store.query("Factor")}
    out["share_yes"], out["share_no"] = impact.get("share_yes"), impact.get("share_no")  # of V, in each world
    out["contributions"] = [{"factor": c, "name": channels.get(c, c), "dollars": v}
                            for c, v in sorted((impact.get("contributions") or {}).items(), key=lambda kv: -abs(kv[1]))]
    if out.get("worst_outcome"):  # the outcome the worst is in, by its own title (MATH.md 1.1; TOOLS.md 9)
        out["worst_outcome"] = {**out["worst_outcome"], "title": outcome_title(store, pid, out["worst_outcome"]["world"])}
    out["baseline"] = stated.get("baseline")
    out["outcomes"] = outcomes(store, pid, impact) if store.watched(pid) else None
    out["spread"] = impact.get("spread")  # each side's spread across its ways, the variance's part within it
    out["priced_in"] = impact.get("priced_in")  # each weight stated for the event or a way, where prices carry another
    # each fund's remainder: what its own move comes to beyond what its rows reach moving as each does (MATH.md 1.2)
    out["remainders"] = impact.get("remainders")
    out["names"] = len(out["contributors"]) if out["contributors"] is not None else None
    out["contributors"] = _by_name(store, out["contributors"])
    out["by_holding"] = _by_holding(out["by_holding"])
    point, whole = reading(store, pid)
    out["point"] = {k: point[k] for k in ("p", "rule", "rests", "target", "terms", "open", "markets", "chances")
                    if k in point}
    out["cannot_hold"] = whole.get("cannot_hold") or []
    out["stretched"] = {lid: s for lid, s in (whole.get("stretched") or {}).items()
                        if any(m["link"] == lid for m in point.get("markets") or [])}
    out["market"] = market_beside(point)
    out["consequences"] = [_consequence(store, link, (impact.get("consequences") or {}).get(link["id"]) or {})
                           for on in (pid, *(n for n in _ways_of(store, pid)))
                           for link in sorted(store.links_of(on, "consequence"), key=lambda link: link["to"])]
    out["d"] = impact.get("d")  # the idiosyncratic terms: each name a consequence on its price sets
    props = _decomposition(store, pid)
    out["decomposition"] = props
    out["markets"] = _markets(store, props)
    out["past"] = any(_quoted_from_history(store, m["market"]) for m in out["markets"])
    out["searches"] = _searches(store, props)
    out["notes"] = [{"id": link["from"], "text": store.stated_of(link["from"]).get("text")}
                    for link in store.links_of(pid, "about", "in")]
    return out


def market_beside(point: dict) -> dict | None:
    """The market's own probability for a proposition, to stand beside ours (TOOLS.md 9): the deepest market that asks
    it with a price, taken or not, its price through the side it asks."""
    priced = [m for m in point.get("markets") or [] if m.get("price") is not None]
    if not priced:
        return None
    best = max(priced, key=lambda m: (m.get("depth") if m.get("depth") is not None else -1.0, -priced.index(m)))
    return {"market": best["market"], "link": best["link"], "probability": best["price"], "taken": best["taken"]}


CONSEQUENCE_FIELDS = ("levels",)


STEP_FIELDS = ("probability", "left_open", "range")  # what a step of a forecast between replies keeps


def _refs(item: Any):
    """Every mapping a manifest holds, at any depth: the data rows and the values it read are among them."""
    if isinstance(item, dict):
        yield item
        for v in item.values():
            yield from _refs(v)
    elif isinstance(item, list):
        for v in item:
            yield from _refs(v)


_LATEST: "weakref.WeakKeyDictionary[Store, dict[str, str | None]]" = weakref.WeakKeyDictionary()


def latest_read(store: Store, cid: str) -> str | None:
    """The latest `retrieved_at` among the data rows a Computation read, at any depth: the moment the figure it gives
    stood as of, which the page shows as a probability's latest reading. Kept per Computation id for as long as the
    store is open: a Computation never changes."""
    memo = _LATEST.setdefault(store, {})
    if cid in memo:
        return memo[cid]
    at: datetime | None = None
    todo, seen = [cid], set()
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        if x != cid and x in memo:
            got = memo[x]
            when = datetime.fromisoformat(got) if got else None
            at = when if when is not None and (at is None or when > at) else at
            continue
        for ref in _refs(store.computation(x)["inputs"]):
            if ref.get("data") and ref.get("retrieved_at"):
                when = datetime.fromisoformat(str(ref["retrieved_at"]).replace("Z", "+00:00"))
                at = when if at is None or when > at else at
            elif str(ref.get("basis") or "").startswith("cmp:"):  # a value it read, by the Computation behind it
                todo.append(ref["basis"])
    memo[cid] = at.isoformat() if at else None
    return memo[cid]


def watched(store: Store) -> dict[str, dict]:
    """Each watched proposition's probability, the range the markets imply, and its worlds, as the engine derives them
    now, for the record's steps between replies: {pid: {probability, range, market, rule, worlds,
    basis, computations, at}}. `market` is the market's own probability beside ours (market_beside), and `rule` what
    gave ours: a derivation, a chance, a weight, a market, or the rest of its structure.

    `worlds` is each world's probability, the weight today's prices put on it, and the portfolio's P&L there,
    `{id: {p, q, A}}` (MATH.md 1.1, 1.5), from the impact's Computation, which `basis["worlds"]` names by id: it is the whole
    portfolio's, and the store keeps a Computation while the record names it, so the record finds it by its id and
    impact.placed places it again at other weights. `computations` holds the Computations behind p, lo and hi and every one they read, at any depth,
    whole, so a step needs no second extraction and can be checked by recomputing it. `at` is the latest
    `retrieved_at` among every row any of them read, the impact's included: when every figure the step holds stood.
    A field not yet derived is None, with no basis. Read through the engine's memo, so after a refresh that landed
    nothing it computes nothing."""
    out: dict[str, dict] = {}
    for pid in watched_events(store):
        derived = store.get(pid, derived_fields=(*STEP_FIELDS, "worlds"))["derived"]
        basis = {f: derived[f]["basis"] for f in STEP_FIELDS
                 if str((derived.get(f) or {}).get("basis") or "").startswith("cmp:")}
        computations: dict[str, dict] = {}
        todo = list(dict.fromkeys(basis.values()))
        while todo:
            cid = todo.pop()
            if cid in computations:
                continue
            computations[cid] = found = store.computation(cid)
            todo += [ref["basis"] for ref in _refs(found["inputs"])
                     if not ref.get("data") and str(ref.get("basis") or "").startswith("cmp:")]
        worlds = None
        got = derived.get("worlds") or {}
        if got.get("value") is not None and str(got.get("basis") or "").startswith("cmp:"):
            basis["worlds"] = got["basis"]
            worlds = {w: {k: v[k] for k in ("p", "q", "A")} for w, v in got["value"].items()}
        times = [t for t in (latest_read(store, cid) for cid in basis.values()) if t]
        point = ((store.computation_output(basis["range"]).get("props") or {}).get(pid) or {}) \
            if basis.get("range") else {}
        out[pid] = {**{f: _value(derived, f) for f in STEP_FIELDS}, "market": market_beside(point),
                    "rule": point.get("rule"), "worlds": worlds, "basis": basis,
                    "computations": computations, "at": max(times, key=datetime.fromisoformat) if times else None}
    return out


EVENT_ROW = ("probability", "range", "yes", "no", "full_impact", "volatility", "worst_outcome", "expected")


# What a statement returns names how each price in a world got its move: the fill's word (MATH.md 1.3; TOOLS.md 5), a factor no
# statement sets moving with the ones that are set; and each price's move against today beside its move against the
# baseline. Each is one token, in one place.
MOVES_WITH = "moves with the stated ones"
TODAY = "against_today"


def event_now(store: Store, pid: str) -> dict:
    """What a watched event now does, from its impact, in every world (TOOLS.md 5): each world with its probability, the
    weight today's prices put on it, and the portfolio's P&L there, in the outcome tree; in each world, each price a
    statement sets there, with the statement (`by`: the world's own, `own`, or one taken from a way or the event above
    it, `parent`, with the proposition it is on and its consequence, and the figure as stated with what it is measured
    against), and each other factor whose move there is not zero, moving with the stated ones (MOVES_WITH, by a past
    episode's co-movement where one is named): each price's move there against the baseline (`move`) and against today
    (TODAY), in the terms a shock on it is stated in (1.3), its proxy's price move as a fraction, or for a factor quoted
    as the yield its proxy pays the move of that yield in percentage points, -100 ln(1 + x) / duration, with its yield
    today; the level there of each price a shock sets, today's beside it: a factor's proxy's price, with its yield for
    a factor quoted as one and each metric that quotes it, and a name's price in its own currency; each holding it
    moves, with its gain or loss there, its full impact and its value today, largest first; and the row. Each price's
    move against what it follows, and the working, are the consequence's (`get` of its link) and the inspector's.

    Where the row waits on its worlds' probabilities, the event gives what each world does against the baseline, which
    reads none (impact.AGAINST_BASELINE): the factors and the names as above, and each holding it moves with its value
    there less its value in the baseline, `against_baseline`, on today's dollars, and each world's in the outcome tree,
    the holdings summed; with why the row and the levels wait. An event the portfolio does not watch has no impact, and
    one with no figures says why."""
    if not store.watched(pid):
        return {"id": pid, "watched": False}
    read = store.get(pid, derived_fields=("worlds", "outcomes"))["derived"]
    found = read.get("worlds") or {}
    if found.get("value") is None:
        why = _why_no_figures(store, pid, read)
        against = _against(store, pid)
        if against is None:
            return {"id": pid, "watched": True, "figures": None, "outcomes": outcomes(store, pid),
                    **({"why": why} if why else {})}
        impact, basis = against.output, against.basis
    else:
        impact, basis = store.computation_output(found["basis"]), found["basis"]
    placed_ = found.get("value") is not None
    channels = sorted(store.query("Factor"), key=lambda c: (c["stated"]["order"], c["id"]))
    world_ids = list(impact["worlds"])
    setting = impact.get("setting") or {}

    episodes = impact.get("episodes") or {}

    placed_moves = impact.get("placed") or {}  # each factor's move against today, where the worlds are placed
    name_levels = {c["target"]: c for c in (impact.get("consequences") or {}).values()
                   if c["target"].startswith("name:") and c.get("levels") and c.get("c")}

    def name_today(n: str, w: str) -> dict:  # a name's move against today, in its own currency, from its level
        got = name_levels.get(n)
        return {TODAY: round(got["levels"][w] / got["c"] - 1.0, 6)} if got and placed_ else {}

    def how(w: str, t: str) -> dict:  # a stated price: the statement that sets it there, with its figure as stated and
        a = (setting.get(w) or {}).get(t)  # what it is measured from; any other moves with the stated ones, by a past
        if a:  # episode's covariance where one is named for the world (1.3), with its window and count of sessions
            return {"by": a["by"], "on": a["on"], "consequence": a["link"],
                    "stated": {"value": a.get("shock"), schema.AGAINST: a.get(schema.AGAINST, schema.BASELINE)}}
        fills = w in episodes and any(not x.startswith("name:") for x in setting.get(w) or {})  # it fills a move
        return {"by": MOVES_WITH, **({"episode": episodes[w]} if fills else {})}  # only where some factor is set

    def stated_as(c: dict, x: float) -> float | None:  # a factor quoted as a yield moves in points of that yield, and
        got = in_its_terms(c["stated"], x)  # one whose proxy stands at zero leaves its yield no level
        return None if got is None else round(got, 6)

    def shown(entry: dict, w: str, t: str) -> bool:  # a world shows a price where a statement sets it or it moves
        return (setting.get(w) or {}).get(t) is not None or entry.get("move") not in (None, 0.0)

    factors = []
    for c in channels:
        if c["id"] not in impact["against_baseline"][world_ids[0]]:
            continue
        worlds_ = {}
        for w in world_ids:
            entry = {"move": stated_as(c, impact["against_baseline"][w][c["id"]]),
                     **({TODAY: stated_as(c, placed_moves[w][c["id"]])} if w in placed_moves else {}), **how(w, c["id"])}
            if shown(entry, w, c["id"]):
                worlds_[w] = entry
        if worlds_:
            factors.append({"factor": c["id"], "name": c["stated"]["name"],
                            **({"yield": c["stated"]["yield"]} if c["stated"].get("duration") is not None else {}),
                            "worlds": worlds_})
    names = []
    for n in impact.get("names_set") or []:
        worth = (impact.get("worth") or {}).get(n)
        if worth:
            baseline = worth["levels"][impact["baseline"]]
            values = {w: {"value": round(worth["levels"][w], 6),
                          "move": round(worth["levels"][w] / baseline - 1, 6) if baseline else None,
                          "by": "worth", "worth": (worth.get("considerations") or {}).get(w)} for w in world_ids}
        else:
            values = {}
            for w in world_ids:
                entry = {"move": round(impact["whole"][n][w], 6), **name_today(n, w), **how(w, n)}
                if shown(entry, w, n):
                    values[w] = entry
        names.append({"name": n, "worlds": values})
    cash = {link["id"]: link["to"].split(":", 1)[1] for link in store.links_of("portfolio", "holds")
            if link["to"].startswith("cash:")}
    held_as = lambda h: ({"cash": cash[h["holding"]]} if h.get("holding") in cash else {} if h.get("contract")  # noqa: E731
                         else {"ticker": (h["name"] or "").split(":", 1)[-1]})  # a contract by its market, below
    contract = lambda h: ({"market": h["contract"]["market"], "side": h["contract"]["side"],  # noqa: E731
                           "quantity": h["contract"]["quantity"]} if h.get("contract") else {})
    if not placed_:  # each world against the baseline, on today's dollars: no level, no row
        moved = [h for h in impact["by_holding"] if any(round(v, 2) != 0.0 for v in h["against_baseline"].values())]
        holdings = [{**held_as(h), "against_baseline": {w: _cents(v) for w, v in h["against_baseline"].items()},
                     **contract(h), "value": _cents(h["value"])}
                    for h in sorted(moved, key=lambda h: (-max(abs(v) for v in h["against_baseline"].values()),
                                                          h["name"] or ""))]
        return {"id": pid, "watched": True, "basis": basis, "baseline": impact["baseline"],
                "outcomes": _open_outcomes(store, pid, impact), "factors": factors, "names": names,
                "holdings": holdings, **_waiting(store, impact), **({"why": why} if why else {})}
    levels = {}
    for c in (impact.get("consequences") or {}).values():
        t = c["target"]
        if t in levels or not (c.get("levels") or c.get("yields")):
            continue
        got = {**({"today": c["c"], "worlds": {w: round(v, 6) for w, v in c["levels"].items()}} if c.get("levels")
                  else {}),
               **({"yield": c["yield"], "yields": {w: round(v, 6) for w, v in c["yields"].items()}}
                  if c.get("yields") else {})}
        quoted = {}
        for q in (store.links_of(t, "quotes", "in") if not t.startswith("name:") else []):
            at = store.engine.value("quotes", q["id"]).output or {}
            if (at.get("levels") or {}).get(pid):
                quoted[q["from"]] = {"today": at["c"],
                                     "worlds": {w: round(v, 6) for w, v in at["levels"][pid].items()}}
        levels[t] = {**got, **({"quoted": quoted} if quoted else {})}
    for n, worth in (impact.get("worth") or {}).items():
        levels[n] = {"today": worth["c"], "worlds": {w: round(v, 6) for w, v in worth["levels"].items()}}
    holdings = [{**held_as(h), "worlds": {w: round(v, 2) for w, v in h["worlds"].items()}, **contract(h),
                 "full_impact": round(h["full_impact"], 2),
                 "value": round(h["at_betas"]["dollars"], 2)}  # its value today, beside its full impact
                for h in sorted(impact["by_holding"], key=lambda h: (-abs(h["full_impact"]), h["name"] or ""))
                if _moves(h)]
    got = row_figures(store, pid)
    return {"id": pid, "watched": True, "basis": basis, "baseline": impact["baseline"],
            "outcomes": outcomes(store, pid, impact), "factors": factors, "names": names, "levels": levels,
            "holdings": holdings, **_waiting(store, impact), "row": got}


def _waiting(store: Store, impact: dict) -> dict:
    """The held contracts an event's figures leave out, by their markets, with why (together._valued)."""
    waiting = impact.get("waiting")
    if not waiting:
        return {}
    markets = {link["id"]: link["to"] for link in store.links_of("portfolio", "holds")}
    return {"waiting": {"contracts": [markets.get(h, h) for h in waiting["holdings"]], "why": waiting["why"]}}


def _against(store: Store, pid: str):
    """What each world of a watched event does against its baseline (impact.AGAINST_BASELINE), or None where it has
    nothing to value."""
    try:
        got = store.engine.value("against_baseline", pid)
    except Unavailable:
        return None
    return got if got.output is not None else None


def _open_outcomes(store: Store, pid: str, against: dict) -> list[dict]:
    """The event's outcomes, each world with what it does to the portfolio against the baseline (`against_baseline`),
    and each outcome's probability where its statements settle it."""
    tree = store.engine.value("outcomes", pid).output or {}
    nodes = tree.get("nodes") or {}
    out = outcomes(store, pid, against) or []
    for o in out:
        p = (nodes.get(o["id"]) or {}).get("p")
        if p is not None:
            o["p"] = _six(p)
        if o.get("against_baseline") is not None:
            o["against_baseline"] = _cents(o["against_baseline"])
    return out


def _moves(h: dict) -> bool:
    """Whether the event moves a holding: its gain or loss in some world, or its full impact, to the cent."""
    return any(round(v, 2) != 0.0 for v in (*h["worlds"].values(), h["full_impact"]))


def _why_no_figures(store: Store, pid: str, read: dict | None = None) -> str | None:
    """Why a watched event has no figures: its worlds' probabilities, and the impact's own reason."""
    read = read if read is not None else store.get(pid, derived_fields=("outcomes",))["derived"]
    tree = (_value(read, "outcomes") or {}).get("why")
    found_impact = store.engine.value("impact", pid)
    cause = found_impact.cause or (found_impact.output or {}).get("why")
    why = [r for r in (tree, cause) if r and not (r is tree and cause and tree in cause)]
    return " ".join(r if r.endswith(".") else f"{r}." for r in why) if why else None


def _cents(x: float | None) -> float | None:
    return None if x is None else round(x, 2) + 0.0  # -0.00 reads as 0.00


def _six(x: float | None) -> float | None:
    return None if x is None else round(x, 6) + 0.0


READ_ROW = ("probability", "left_open", "range", "full_impact", "contributors", "yes", "no", "expected",
            "volatility", "worst_outcome")


def row_read(store: Store, pid: str) -> dict:
    """An event's row as `ledger()` returns it (TOOLS.md 9): its probability, with the range the markets imply, the rule that
    gives it, and the market's own beside it; or the range its statements leave, with what would settle it. Then Yes,
    with its share of the portfolio, No, the full impact, the volatility, the worst outcome by its title, the expected
    value, the three largest contributors with the funds they are held through, and its outcomes, each with its
    probability, the weight today's prices put on it, and the portfolio's P&L there; or, where it has no figures, why.
    Its dollars to the cent, its probabilities and weights to six places. `basis` names the Computation behind the
    probability and the one behind the figures, where the whole account is read; the event's own statements are read
    with get."""
    obj = store.get(pid, derived_fields=READ_ROW)
    derived = obj["derived"]
    basis = {f: derived[f].get("basis") for f in READ_ROW if (derived.get(f) or {}).get("basis")}
    point, _ = reading(store, pid)
    out: dict[str, Any] = {"id": pid, "title": obj["stated"].get("title")}
    p, left = _value(derived, "probability"), _value(derived, "left_open")
    if p is not None:
        out["probability"] = _six(p)
    elif left:
        out["left_open"] = {**left, "lo": _six(left.get("lo")), "hi": _six(left.get("hi"))}
    found = _value(derived, "range")
    out["range"] = [_six(x) for x in found] if found else None
    if point.get("rule"):
        out["rule"] = point["rule"]
    if point.get("rule") == "derived" and point.get("terms"):
        out["derivation"] = _derivation(store, pid, point["terms"])
    beside = market_beside(point)
    if beside:
        out["market"] = {**beside, "probability": _six(beside["probability"])}
    impact = store.computation_output(basis["full_impact"]) if basis.get("full_impact") else {}
    against = None
    if _value(derived, "full_impact") is None:
        why = _why_no_figures(store, pid)
        against = _against(store, pid) if store.watched(pid) else None
        out.update({**({} if against else {"figures": None}), **({"why": why} if why else {})})
    else:
        worst = _value(derived, "worst_outcome")
        out.update({"yes": _cents(_value(derived, "yes")), "share_yes": _six(impact.get("share_yes")),
                    "no": _cents(_value(derived, "no")), "full_impact": _cents(_value(derived, "full_impact")),
                    "volatility": _cents(_value(derived, "volatility")),
                    "worst_outcome": {"pnl": _cents(worst["pnl"]), "world": worst["world"],
                                      "title": outcome_title(store, pid, worst["world"]),
                                      "probability": _six(worst["probability"])} if worst else None,
                    "expected": _cents(_value(derived, "expected"))})
        out["full_impact_parts"] = _full_impact_parts(store, pid, basis["full_impact"], impact)
        out["contributors"] = [{"name": c["name"], "company": c["company"], "full_impact": _cents(c["full_impact"]),
                                "yes": _cents(c["yes"]), "no": _cents(c["no"]), "held_directly": c["held_directly"],
                                "through": c["through"]}
                               for c in _by_name(store, _value(derived, "contributors")) or []]
    out.update(_waiting(store, against.output if against is not None else impact))
    if against is not None:  # each world against the baseline, while the row waits on the probabilities
        out["outcomes"] = _open_outcomes(store, pid, against.output)
    else:
        tree = outcomes(store, pid, impact) if store.watched(pid) else None
        out["outcomes"] = [{**o, **{k: _six(o[k]) for k in ("p", "q") if o.get(k) is not None},
                            **({"A": _cents(o["A"])} if o.get("A") is not None else {})} for o in tree] if tree else None
    out["basis"] = {k: v for k, v in (("probability", basis.get("probability") or basis.get("left_open")),
                                       ("outcomes", basis.get("full_impact") or (against.basis if against else None)))
                    if v}
    return out


def _number(lid: str) -> int:
    head, _, n = lid.partition(":")
    return int(n) if head == "link" and n.isdigit() else 1 << 62


def _full_impact_parts(store: Store, pid: str, cid: str, impact: dict) -> dict:
    """The full impact in its three parts (MATH.md 1.6): each factor's dollar beta times its move between the two sides, in
    factor order; each stated company's own part on the portfolio's exposure to it, in the order its shocks and values
    were stated, and each contract held by what it pays; and what compounding and a holding's terms add, the rest.
    Each to the cent, as every figure is."""
    g = (store.computation(cid).get("inputs") or {}).get("g") or {}
    factors = []
    for c in sorted(store.query("Factor"), key=lambda c: (c["stated"]["order"], c["id"])):
        if c["id"] in (impact.get("contributions") or {}):
            factors.append({"factor": c["id"], "name": c["stated"]["name"], "dollar_beta": _cents(g.get(c["id"], 0.0)),
                            "move": _six(impact["u_E"][c["id"]]), "dollars": _cents(impact["contributions"][c["id"]])})
    stated: dict[str, int] = {}
    for on in (pid, *_ways_of(store, pid)):
        for relation in ("consequence", "worth"):
            for link in store.links_of(on, relation):
                stated[link["to"]] = min(stated.get(link["to"], 1 << 62), _number(link["id"]))
    held = {link["to"]: _number(link["id"]) for link in store.links_of("portfolio", "holds")}
    own, reach, moves = impact.get("own") or {}, impact.get("reach") or {}, impact.get("own_moves") or {}
    parts = []
    for n in sorted(own, key=lambda n: (n.startswith("market:"), stated.get(n, held.get(n, 1 << 62)), n)):
        if n.startswith("market:"):  # a contract held, by what it pays
            parts.append({"market": n, "dollars": _cents(own[n])})
        else:
            parts.append({"name": n, "exposure": _cents(reach.get(n)),
                          "beyond_response": _six((moves.get(n) or {}).get("E")), "dollars": _cents(own[n])})
    rest = impact["full_impact"] - sum(impact["contributions"].values()) - sum(own.values())
    return {"factors": factors, "companies": parts, "compounding": _cents(rest)}


def _derivation(store: Store, pid: str, terms: list[dict]) -> list[dict]:
    """A probability its chances fix, as the sum of its parts (MATH.md 1.2): each case of what those chances are given, its
    probability or the range its statements leave it, the markets that probability rests on, and the share of it where
    the event holds, with the chance that states that share, or `links` where they leave it 0 or 1."""
    asks = {link["id"]: link["to"] for p in _decomposition(store, pid) for link in store.links_of(p, "asks")}
    book = lambda tag: tag.rsplit(":", 1)[0]  # noqa: E731  a book's statement: its asks link's, or a market standing as itself
    out = []
    for t in terms:
        markets = list(dict.fromkeys(asks.get(book(tag)) or book(tag) for tag in t.get("rests") or []
                                     if book(tag) in asks or schema.MARKET_ID_RE.match(book(tag))))
        out.append({"given": t["given"], "given_not": t["given_not"],
                    **({"probability": _six(t["p"])} if t.get("p") is not None else
                       {"range": [_six(t["range"]["lo"]), _six(t["range"]["hi"])]}),
                    "markets": markets, **({"share": _six(t["share"])} if t.get("share") is not None else {}),
                    **({"chance": t["chance"]} if t.get("chance") else {}), **({"by": t["by"]} if t.get("by") else {})})
    return out


def _ledger_by_holding(store: Store) -> list[dict]:
    """The ledger by holding (TOOLS.md 5, 9): each holding as the member gave it, by what it holds (a name, cash in a
    currency, a market's contract), with its value today and its P&L on Yes and on No under each event with figures, in
    the order the events were made; each event's holdings summing to its row's Yes and No."""
    impacts = {}
    for pid in watched_events(store):
        try:
            found = store.engine.value("impact", pid).output or {}
        except Unavailable:
            continue
        if found.get("by_holding"):
            impacts[pid] = {h["holding"]: h for h in found["by_holding"]}
    if not impacts:
        return []
    values = (store.engine.value("holdings", "portfolio").output or {}).get("holds") or {}
    out = []
    for link in sorted(store.links_of("portfolio", "holds"), key=lambda link: _number(link["id"])):
        events = {pid: {"yes": _cents(by[link["id"]]["yes"]), "no": _cents(by[link["id"]]["no"])}
                  for pid, by in impacts.items() if link["id"] in by}
        out.append({"holding": link["to"], "value": _cents((values.get(link["id"]) or {}).get("dollars")),
                    "events": events})
    return out


def ledger_read(store: Store, together: list[str] | None = None, considering: dict | None = None) -> dict:
    """`ledger()` as the analyst reads it (TOOLS.md 5, 9): every event's row with every field section 9 gives it, its dollars
    to the cent, in the order the events were made, with the full impact's parts and, for a probability its chances
    fix, its derivation; the ledger by holding beside the rows; the portfolio's value and its volatility to the end of
    the period the ledger looks over; with `together`, the events named read as one set of worlds, and with
    `considering`, a contract valued in them (1.9). Each row's whole account is in the Computations its basis names."""
    out = _ledger(store, together, considering, row_read, _cents)
    held = _ledger_by_holding(store)
    return {**{k: v for k, v in out.items() if k != "rows"}, "rows": out["rows"], **({"by_holding": held} if held else {})}


def event_read(store: Store, pid: str) -> dict:
    """A watched event as get reads it (TOOLS.md 5): the proposition whole, its stated fields, its links and searches, the
    range the markets imply and its probability or the range its statements leave, each with its
    basis; the event as it now stands, as every statement on it returns it (event_now); and the distribution of the
    portfolio's P&L at the horizon's end. The row the event gives beside its worlds carries its figures, its
    probability and range being read above with their basis. The whole account is in the Computations named."""
    obj = store.get(pid, derived_fields=("range", "probability", "left_open", "distribution"))
    now = event_now(store, pid)
    row = {k: v for k, v in (now.get("row") or {}).items() if k not in ("probability", "range")}
    return {**obj, **{k: v for k, v in now.items() if k not in ("id", "row")}, **({"row": row} if "row" in now else {})}


def row_figures(store: Store, pid: str) -> dict:
    """The row's figures (TOOLS.md 9), the worst outcome by its title."""
    derived = store.get(pid, derived_fields=EVENT_ROW)["derived"]
    out = {f: _value(derived, f) for f in EVENT_ROW}
    if out.get("worst_outcome"):
        out["worst_outcome"] = {**out["worst_outcome"],
                                "title": outcome_title(store, pid, out["worst_outcome"]["world"])}
    return out


def rows_now(store: Store) -> list[dict]:
    """Every watched event's row as it now stands, in the order the events were made (TOOLS.md 5, 9): what a statement that
    reaches every event returns of each, as a factor's, the portfolio's or a holding's does. Each is the row the
    event's own statements end with (event_now), beside the event's id and title; where its figures wait, why."""
    out = []
    for pid in watched_events(store):
        got = {"id": pid, "title": _title(store, pid), **row_figures(store, pid)}
        why = _why_no_figures(store, pid) if got.get("full_impact") is None else None
        out.append({**got, **({"why": why} if why else {})})
    return out


def _consequence(store: Store, link: dict, shown: dict) -> dict:
    """One consequence as the row shows it: the factor or the name whose price it moves; what the analyst states of its
    move with its basis, `shock`, with what it is measured against (MATH.md 1.3; ONTOLOGY.md 2.2); the price today and in each
    world, for a factor quoted as a yield its yield as well, and its move in each world it applies in, each null where
    it has none."""
    got = store.get(link["id"])["derived"]
    states = {"shock": link["stated"]["shock"]}
    return {"link": link["id"], "target": link["to"], **states, "c": shown.get("c"),
            "levels": _value(got, "levels"), "moves": _value(got, "moves"),
            **{k: shown[k] for k in ("yield", "yields") if k in shown}}


def _quoted_from_history(store: Store, mid: str) -> bool:
    """A quote read from history at a past as-of, whose depth is unknown."""
    return bool((store.engine.value("market_price", mid).output or {}).get("past"))


def outcome_title(store: Store, pid: str, world: str) -> str:
    """An outcome by its own title (TOOLS.md 9): a way by the way's title; a side with no ways by the event's side, Yes or
    No."""
    if world in ("yes", "no"):
        return {"yes": "Yes", "no": "No"}[world]
    return _title(store, world) or world


def by_volatility(rows: list[dict]) -> list[dict]:
    """The ledger's ranking: the largest volatility first; a row with no volatility yet after those with one."""
    return sorted(rows, key=lambda r: (r["volatility"] is None, -(r["volatility"] or 0.0), r["id"]))


def by_worst(rows: list[dict]) -> list[dict]:
    """The ledger's other order: the most negative worst first, so the unlikely event with the large loss leads."""
    worst = lambda r: (r.get("worst_outcome") or {}).get("pnl")  # noqa: E731
    return sorted(rows, key=lambda r: (worst(r) is None, worst(r) if worst(r) is not None else 0.0, r["id"]))


def moves(store: Store, names: list[str]) -> list[dict]:
    """What each watched event does to a set of names, such as an industry's or a country's: the names'
    shares summed (1.7, by_name: each name's exposure times its placed move), in the full impact, on Yes and on No and
    in each world, with each name's own share beside the sum, read from the event's impact Computation, its basis. One
    entry for each watched event with figures, in the order the events were made; a name with no share in an event is
    left out of it."""
    wanted = set(names)
    out = []
    for pid in watched_events(store):
        try:
            found = store.engine.value("impact", pid)
        except Unavailable:
            continue
        impact = found.output or {}
        if not impact.get("worlds"):
            continue
        shares = [s for s in impact.get("contributors") or [] if s["name"] in wanted]
        out.append({"event": pid, "title": _title(store, pid), "probability": impact.get("p"),
                    **{k: sum(s[k] for s in shares) for k in ("full_impact", "yes", "no")},
                    "worlds": {w: sum(s["worlds"][w] for s in shares) for w in impact["worlds"]},
                    "names": [{"name": s["name"], **{k: s[k] for k in ("full_impact", "yes", "no")}} for s in shares],
                    "basis": found.basis})
    return out


def ledger(store: Store, together: list[str] | None = None, considering: dict | None = None) -> dict:
    """Every event's row, in the order the events were made, with the portfolio's value and its volatility to the end
    of the period the ledger looks over; with `together`, the events named read as one set of worlds, and with
    `considering`, a contract valued in them (TOOLS.md 5, 9; MATH.md 1.9). Each row carries its whole account, as the page and
    the record read it; ledger_read is what the analyst reads."""
    return _ledger(store, together, considering, row, lambda x: x)


def _ledger(store: Store, together: list[str] | None, considering: dict | None, one, money) -> dict:
    portfolio = _portfolio(store)
    rows = [one(store, pid) for pid in watched_events(store)]
    out = {"asof": portfolio["stated"]["asof"], "value": money(_value(portfolio["derived"], "value")),
           "horizon_end": store.horizon_end(), "volatility": money(_value(portfolio["derived"], "volatility")),
           "rows": rows}
    if considering is not None and together is None:
        raise StoreError("considering names a contract beside the events given in together")
    if together is not None:
        from harness.derive.together import read
        out["together"] = read(store, together, considering)
    return out


# ---------------------------------------------------------------- check


def _components(store: Store) -> list[list[str]]:
    """Each structure, as its propositions in creation order."""
    seen, out = set(), []
    for p in store.query("Proposition"):
        if p["id"] in seen:
            continue
        props = store_component(store, p["id"])
        seen.update(props)
        out.append(props)
    return out


def yield_month(month: dict, duration: float) -> float:
    """A factor quoted as a yield, its largest month read in that yield (MATH.md 3.2): its own part's compounded move over
    the month, a move net of the income its proxy paid (series.window_move), in points, -100 ln(1 + M) / duration, of
    the month's own sign."""
    return -100.0 * math.log1p(month["move"]) / duration


def channel_reach(store: Store) -> dict:
    """Each factor's dollar beta, g; the portfolio's beta to it, g over V; its largest month, M_c, the size of its own
    part's largest move over a month; and that month applied to the dollar beta, abs(g) * M_c: what such a month would
    do to the portfolio, a measured fact (MATH.md 3.2; TOOLS.md 5). A factor quoted as the yield its proxy pays is read in that
    yield, as its shocks are stated (1.3): with its yield today, the dollars the portfolio gains or loses for each
    percentage point the yield rises, -g * duration / 100 (`per_point`), and its largest month as the yield's move over
    that month in points (yield_month), given as a size."""
    portfolio = _portfolio(store)
    value, g = _value(portfolio["derived"], "value"), _value(portfolio["derived"], "dollar_beta")
    if value is None or g is None:
        return {"value": value, "factors": None}
    out = []
    for c in sorted(store.query("Factor"), key=lambda c: c["stated"]["order"]):
        derived = store.get(c["id"])["derived"]
        m_c = _value(derived, "largest_month")
        exposure = g.get(c["id"], 0.0)
        item = {"factor": c["id"], "name": c["stated"]["name"], "proxy": c["stated"]["proxy"],
                "dollar_beta": exposure, "beta": exposure / value if value else None, "largest_month": m_c,
                "largest_month_applied": abs(exposure) * m_c if m_c is not None else None}
        duration = c["stated"].get("duration")
        if duration is not None:
            month = (_value(derived, "orthogonalization") or {}).get("largest_month_sessions")
            moved = yield_month(month, duration) if month else m_c  # no month: a move of 0, or none read yet
            item.update({"yield": c["stated"]["yield"], "per_point": -exposure * duration / 100.0,
                         "largest_month": abs(moved) if moved is not None else None})
        out.append(item)
    return {"value": value, "factors": out}


NAMES_GIVEN = 25  # the largest names after look-through that adding the portfolio returns
# A company at least this share of the portfolio's value, held directly and through its funds together, comes with
# its latest news: the line diversification rules draw for one issuer (MATH.md 3.1).
NEWS_LINE = 0.05
FUND_GIVEN = 10  # a fund's largest holdings, places, and kinds of asset that adding the portfolio returns


def _shown(item: dict, *, keep_null: bool = False) -> dict:
    """An entry as the return gives it: dollars to the cent, weights to six places, and, unless kept, only the fields
    it has."""
    out = {}
    for k, v in item.items():
        if v is None and not keep_null:
            continue
        out[k] = round(v, 2) if k == "dollars" and v is not None else round(v, 6) if k == "weight" and v is not None \
            else v
    return out


def _largest(groups: list[dict]) -> list[dict]:
    """The FUND_GIVEN largest groups, then the others summed, with how many."""
    shown = [_shown(g, keep_null=True) for g in groups[:FUND_GIVEN]]
    rest = groups[FUND_GIVEN:]
    if rest:
        shown.append(_shown({"others": len(rest), "weight": sum(g["weight"] for g in rest),
                             "dollars": sum(g["dollars"] for g in rest)}))
    return shown


def _what_it_is(store: Store, nid: str, fund: dict | None, classified: dict | None = None,
                companies: dict | None = None) -> dict:
    """What a holding is: a fund, with what it holds, its largest holdings, how its dollars divide by where its
    holdings are listed and by kind of asset, and whether the look-through prices them; or a company (MATH.md 3.1): its
    name, its industry and its home as the one classification the portfolio's weight is read by gives them
    (`classified`, the company's entry in the weights' by_company), and what it does, in its reference's words. A fund's
    largest holdings each take their company's industry from that one classification too (`companies`)."""
    company = store.engine.value("reference", nid).output or {}
    found = company.get("company") or {}
    if fund is None:
        if not found and not classified:
            return {}
        c = classified or {}
        return {"company": _shown({"name": c.get("company") or found.get("name"), "does": found.get("does"),
                                   "industry": c.get("industry"), "country": c.get("country")})}
    holds = fund.get("holds") or {}
    out = {"name": found.get("name"), "looked_through": fund.get("expanded"), "reason": fund.get("reason"),
           "holdings_asof": holds.get("holdings_asof"), "holdings": holds.get("count")}
    if holds:
        out.update(largest=[_shown({**h, "industry": ((companies or {}).get(h.get("priced_as")) or {}).get("industry")})
                            for h in holds["named"][:FUND_GIVEN]], by_listing=_largest(holds["by_listing"]),
                   by_kind=_largest(holds["by_kind"]))
    return {"fund": _shown(out)}


def holdings(store: Store) -> dict:
    """What the portfolio owns, as of its as-of, and the end of the period the ledger looks over, the as-of plus its
    horizon's days, by which each event it watches is decided. The holdings as given, each with its value, weight, and
    its price's session move, and
    what it is: a company, with what it does and its industry, or a fund, with its largest holdings and how its
    dollars divide by where its holdings are listed and by kind of asset; the portfolio's move, their sum; and the
    largest names after look-through, each with what is held directly and through which funds; and the latest news
    on each company that is at least NEWS_LINE of the portfolio's value. Nothing about the channels: exposure() gives the portfolio's exposure to
    each. It reads the portfolio's stated fields alone, never what is derived from every name's prices: add returns it
    before the exposures are computed (harness.tools)."""
    stated = _stated(store)
    asof = stated["asof"]
    held = store.engine.value("holdings", "portfolio")
    looked = store.engine.value("lookthrough", "portfolio")
    horizon = store.horizon_end()  # the period the ledger looks over ends here: an event is decided by it
    if held.output is None or looked.output is None:
        return {"asof": asof, "horizon_end": horizon, "value": None, "holdings": None, "names": None}
    funds = looked.output["funds"]
    of_holding = {}  # each holding's own ticker, by its link
    for link in store.links_of("portfolio", "holds"):
        row = held.output["holds"][link["id"]]
        of_holding[link["id"]] = ticker_of(row["name"]) if row.get("cash") else link["to"].split(":", 1)[1]
    names: dict[str, dict] = {}  # each name's exposure after look-through, signed, held directly and through funds
    for hid, reached in looked.output["by_holding"].items():
        for nid, dollars in reached.items():
            if pseudo(nid.split(":", 1)[1]):
                continue  # cash
            found = names.setdefault(nid, {"name": nid.split(":", 1)[1], "exposure": 0.0, "direct": 0.0, "through": {}})
            found["exposure"] += dollars
            if nid.split(":", 1)[1] == of_holding.get(hid):
                found["direct"] += dollars
            else:
                found["through"][of_holding.get(hid)] = found["through"].get(of_holding.get(hid), 0.0) + dollars
    ranked = sorted(names.values(), key=lambda n: (-abs(n["exposure"]), n["name"]))  # by absolute net exposure
    # what each name's reference says it is, which picks the companies whose news comes with it (_companies), read
    # while the weights are worked out, which nothing of it needs
    store.prefetch("ticker_ref", [t for t in (reference_ticker(f"name:{n['name']}") for n in ranked) if t],
                   (asof, asof), wait=False)
    weights = store.engine.value("weights", "portfolio")
    if weights.output is None:
        raise Unavailable(weights.cause or "where the portfolio's weight is, by industry and by country, is not read")
    lists = weights.output
    classified = {c["name"]: c for c in lists["by_company"] if c.get("name")}
    rows = []
    for link in store.links_of("portfolio", "holds"):
        row = held.output["holds"][link["id"]]
        if row.get("cash"):  # cash, an amount in its currency, at its rate in dollars where it is another's
            other = row["cash"] != "USD"
            rows.append({"cash": link["stated"]["cash"], "currency": row["cash"], "dollars": row["dollars"],
                         "weight": row["weight"], **({"rate": row["price"], "rate_at": row.get("price_time") or
                                                       row.get("session"), "session_move": row.get("session_move"),
                                                       "session_move_dollars": row.get("session_move_dollars")}
                                                      if other else {})})
            continue
        ticker = link["stated"].get("ticker") or link["to"].split(":", 1)[1]  # the security held
        item = {"ticker": ticker, **({"name": link["to"]} if link["to"] != f"name:{ticker}" else {}),
                "given": {k: v for k, v in link["stated"].items() if k != "ticker"},
                "dollars": row["dollars"], "quantity": row["quantity"],
                "weight": row["weight"], "price": row["price"], "price_from": row.get("price_from"),
                "price_at": row.get("price_time") or row.get("session"), "session": row.get("session"),
                "previous_close": row.get("previous_close"), "session_move": row.get("session_move"),
                "session_move_dollars": row.get("session_move_dollars")}
        if row.get("contract"):
            listing = store.engine.value("market_listing", link["to"]).output or {}
            item.update(contract=True, market=link["to"], side=link["stated"]["side"],
                        question=listing.get("question"))
        else:
            item.update(_what_it_is(store, link["to"], funds.get(ticker), classified.get(link["to"]), classified))
        rows.append(item)
    # The news looks back from the as-of over the span the horizon looks ahead over.
    since = (date.fromisoformat(asof) - timedelta(days=stated["horizon_days"])).isoformat()
    news = _news(store, _companies(store, ranked, held.output["V"], asof), since, asof)
    return {"asof": asof, "horizon_end": horizon, "value": held.output["V"],
            "session_move": held.output.get("session_move"),
            "session_move_dollars": held.output.get("session_move_dollars"), "holdings": rows,
            "by_company": [_listed(c) for c in _by_company(lists["by_company"], names)],
            "by_industry": [_listed(g) for g in lists["by_industry"]],
            "by_country": [_listed(g) for g in lists["by_country"]], "news": news}


def portfolio(store: Store) -> dict:
    """The portfolio as the analyst reads it wherever it reads it (TOOLS.md 5): its stated fields and what it owns as it now
    stands (holdings), which adding it, editing it and changing a holding return. The account of every row of every
    fund, each company below the floor by itself, stays the store's and the page's: the analyst reads those companies
    summed in by_company, and counted in the industries and countries they belong to."""
    return {"id": "portfolio", "type": "Portfolio", "stated": _stated(store), **holdings(store)}


def portfolio_whole(store: Store) -> dict:
    """The portfolio as get reads it: what it owns as it now stands (portfolio), with the derived figures adding it
    leaves to be read later, its dollar beta to each factor and its volatility to the horizon's end, each with its
    basis; and its links."""
    got = store.get("portfolio", derived_fields=("dollar_beta", "volatility"))
    return {**portfolio(store), "derived": got["derived"], "links": got["links"]}


def _listed(entry: dict) -> dict:
    """An entry of the portfolio's weight by company, industry or country as the analyst reads it: dollars to the cent and
    weights to six places (_shown), each field kept where it has none, so a company no source classifies says so; and an
    industry's or a country's companies once, under each company, not again as a list beside the group."""
    out = _shown({k: v for k, v in entry.items() if k != "names"}, keep_null=True)
    if isinstance(out.get("direct"), float):
        out["direct"] = round(out["direct"], 2)
    if isinstance(out.get("through"), dict):
        out["through"] = {fund: round(x, 2) for fund, x in out["through"].items()}
    return out


def _by_company(listed: list[dict], names: dict[str, dict]) -> list[dict]:
    """The portfolio's weight by company as add returns it: each company a name stands for, with how it is held,
    directly and through which funds; the companies below every fund's floor that no name stands for, summed, with
    how many they are (each is in the weights' by_company, which the page reads whole); and what is not a company, by
    kind."""
    out, rest = [], {"companies": 0, "dollars": 0.0, "weight": 0.0}
    for c in listed:
        if "kind" in c:
            out.append(c)
        elif c.get("name"):
            held = names.get(c["name"]) or {}
            out.append({**c, "direct": held.get("direct", 0.0), "through": held.get("through", {})})
        else:
            rest["companies"] += 1
            rest["dollars"] += c["dollars"]
            rest["weight"] = (rest["weight"] or 0.0) + (c["weight"] or 0.0)
    kinds = [c for c in out if "kind" in c]
    named = [c for c in out if "kind" not in c]
    return named + ([rest] if rest["companies"] else []) + kinds


def _companies(store: Store, ranked: list[dict], value: float | None, asof: str) -> list[str]:
    """Each company that is at least NEWS_LINE of the portfolio's value, held directly and through its funds together,
    largest first, by the ticker of its largest listing: a name is a company's where its ticker reference is a share
    of one (a common stock, an ordinary share, a depositary receipt, a preferred), and a company's listings, its share
    classes, count together by the CIK their references name. The line is on the absolute net exposure after
    look-through, held directly and through funds, long and short netted, the same for every company. A fund's own news is about its flows and the market, so a fund gets none, whether it is booked
    whole or looked through."""
    if not value:
        return []
    companies: dict[str, dict] = {}
    for n in ranked:
        if (store.engine.value("reference", f"name:{n['name']}").output or {}).get("type") not in NAME_TYPES:
            continue
        found = companies.setdefault(_cik(store, n["name"], asof) or n["name"], {"exposure": 0.0, "ticker": n["name"]})
        found["exposure"] += n["exposure"]  # the largest listing comes first, as the names are ranked
    held = sorted(companies.values(), key=lambda c: -abs(c["exposure"]))
    return [c["ticker"] for c in held if abs(c["exposure"]) >= NEWS_LINE * value]


def _cik(store: Store, ticker: str, asof: str) -> str | None:
    """The CIK a listing's ticker reference names, which its company's other share classes name too."""
    row = store.data("ticker_ref", ticker, covering=(asof, asof), required=False)
    payload = store.data_payload(row["id"]) if row else None
    found = (payload.get("results") or {}) if answered(payload) else {}
    return str(found["cik"]).lstrip("0") or None if found.get("cik") else None


def _news(store: Store, tickers: list[str], since: str, asof: str) -> list[dict]:
    """The latest news on the largest companies: each one's most recent articles published from `since` to the
    as-of, read side by side and kept as data, each article once, newest first, with every one of these companies it
    is about; its date, publisher, headline, summary, keywords, and link, as its source writes them. A company with no
    article then has none. A sentiment label is a reading of what the news means, which is the analyst's, so it stays
    out, as do images and logos. A news read that fails past its retries raises, so what the portfolio owns is not read
    without it and the call that asked fails with nothing written (TOOLS.md 5); a read that finds no article is the
    complete answer, none. News no client reads here (the Massive key not set, or its plan without news) is absent."""
    store.prefetch("news", tickers, (since, asof), stage={"stage": "news"})
    articles: dict[str, dict] = {}
    absent: dict[str, str] = {}
    for ticker in tickers:
        row = store.data("news", ticker, covering=(since, asof), required=False)  # a failure raises
        if row is None:
            absent[ticker] = "the news is read with the Massive key, which is not set here"
            continue
        payload = store.data_payload(row["id"]) or {}
        if not answered(payload):
            absent[ticker] = f"Massive answered {payload.get('http_status') or payload.get('status')} for its news"
            continue
        for r in payload.get("results") or []:
            articles.setdefault(r.get("id") or r.get("article_url"), r)
    order = {t: i for i, t in enumerate(tickers)}
    out = [{"published": r.get("published_utc"), "publisher": (r.get("publisher") or {}).get("name"),
            "title": r.get("title"), "summary": r.get("description"), "keywords": r.get("keywords") or [],
            "url": r.get("article_url"),
            "about": sorted((t for t in r.get("tickers") or [] if t in order), key=order.get)}
           for r in articles.values()]
    for ticker, why in absent.items():
        log.info("news: %s: %s", ticker, why)
    return sorted(out, key=lambda a: a["published"] or "", reverse=True)


FACTOR_REACH = ("dollar_beta", "beta", "per_point", "largest_month_applied")


def factor(store: Store, cid: str) -> dict:
    """A factor as adding it returns it (TOOLS.md 5): its stated fields and its proxy's price metric; how it is built from
    the factors before it, its coefficient on each (`built_from`); the history every proxy shares, its first session and
    how many (`history`); its largest month, the move of its own part with its first and last session, for a factor
    quoted as a yield the yield's move in points; and the portfolio's dollar beta to it, its beta, for a factor quoted
    as a yield its dollars for each point that yield rises, and that month applied to the dollar beta, as exposure()
    gives them (3.2). Each name's beta to it is one get away."""
    stated = store.stated_of(cid)
    orth = _value(store.engine.derived(cid, "Factor"), "orthogonalization") or {}
    month = orth.get("largest_month_sessions")
    out = {"id": cid, "type": "Factor", "stated": stated, "proxy_metric": schema.proxy_metric_id(stated["proxy"]),
           "built_from": orth.get("coefficients"),
           "history": {"first": orth["window"][0], "sessions": orth["n"]} if orth.get("window") else None,
           "largest_month": {"move": (yield_month(month, stated["duration"]) if stated.get("duration") is not None
                                      else month["move"]), "start": month["start"], "end": month["end"]}
           if month else None}
    found = next((c for c in channel_reach(store)["factors"] or [] if c["factor"] == cid), None)
    return {**out, **{k: (found or {}).get(k) for k in FACTOR_REACH if k != "per_point" or "duration" in stated}}


def exposure(store: Store) -> dict:
    """The portfolio's dollar beta and beta to each factor the store models, and for a factor quoted as a yield its
    dollars for each percentage point that yield rises; each factor's largest month, in points for a factor quoted as
    a yield, and that month applied to the dollar beta, largest first; and each proxy's price and session move: the
    basis on which what an event does is sized (MATH.md 3.2; TOOLS.md 5). It runs no searches."""
    portfolio = _portfolio(store)
    reach = channel_reach(store)
    proxies = []
    for c in reach["factors"] or []:
        got = store.engine.value("proxy_price", f"metric:price:{c['proxy']}")
        out = got.output or {}
        proxies.append({"ticker": c["proxy"], "factor": c["factor"], "price": out.get("level"),
                        "price_from": out.get("from"), "price_at": out.get("time") or out.get("asof"),
                        "session": out.get("asof"), "previous_close": out.get("previous_close"),
                        "session_move": out.get("session_move")})
    factors = sorted(reach["factors"] or [], key=lambda c: -(c["largest_month_applied"] or 0.0))
    return {"asof": portfolio["stated"]["asof"], "value": reach["value"], "factors": factors, "proxies": proxies}


def _noted(store: Store, oid: str) -> bool:
    """Whether a note is about the object or link: a note about a Judgment answers its reading."""
    return bool(store.links_of(oid, "about", "in"))


def _assumption(store: Store, owner: str, field: str, stated: dict) -> dict:
    """A stated assumption with its basis, as a reply's record keeps it: the page it cites with its passages, and the
    reasoning, the note it names, with its text."""
    value = {k: v for k, v in stated.items() if k not in schema.BASIS}
    basis: dict[str, Any] = {k: stated[k] for k in ("source_id", "passages") if k in stated}
    if stated.get("note"):
        basis["note"] = {"id": stated["note"], "text": (store.stated_of(stated["note"]) or {}).get("text")
                         if store.type_of(stated["note"]) == "Note" else None}
    return {"id": owner, "field": field, "value": value, "basis": basis}


JUDGED = {"asks": ("forward", "converse"), "implies": ("check",)}  # the derived fields a reading of two texts gives


def _sentence(relation: str, first: str, second: str, side: str, off: set[str]) -> str:
    """What differs between a claim and its two texts, in one sentence: a market that can resolve the other side
    where its proposition is true, or its own where it is false; a first proposition that can be true where the
    second is false."""
    if relation == "implies":
        return f"{first} can be true where {second} is false."
    own, other = ("Yes", "No") if side == "yes" else ("No", "Yes")
    parts = ([f"{other} where {first} is true"] if "forward" in off else []) + \
        ([f"{own} where {first} is false"] if "converse" in off else [])
    return f"{second} can resolve {', and '.join(parts)}."


def differs(link: dict) -> str | None:
    """Where a link's two texts describe different outcomes, what differs, in one sentence (ONTOLOGY.md 2.2; TOOLS.md 6): an
    `asks` link whose
    market can resolve the other side where its proposition is true, or its own where the proposition is false; an
    `implies` link whose first proposition can be true where its second is false. None where they support it, or are
    not yet read."""
    fields = JUDGED.get(link.get("relation"))
    if not fields:
        return None
    derived = link.get("derived") or {}
    off = {f for f in fields if disputed((derived.get(f) or {}).get("value"))}
    if not off:
        return None
    return _sentence(link["relation"], link["from"], link["to"], (link.get("stated") or {}).get("side") or "yes", off)


def as_read(result: Any, owner: str | None = None) -> Any:
    """A tool's result as the analyst reads it: each link in it without the fields a reading of its two texts gives,
    saying what differs where those texts describe different outcomes (`differs`). A link read whole names its two
    ends; one listed under an object (`links`) names the other end and its direction from the object, its `owner`."""
    if isinstance(result, list):
        return [as_read(x, owner) for x in result]
    if not isinstance(result, dict):
        return result
    out = {k: as_read(v, result.get("id") if k == "links" else None) for k, v in result.items()}
    fields = JUDGED.get(out.get("relation"))
    if fields and isinstance(out.get("derived"), dict):
        if "from" in result:
            ends = (result["from"], result.get("to"))
        else:
            ends = (owner, result.get("other")) if result.get("direction") == "out" else (result.get("other"), owner)
        said = differs({**result, "from": ends[0], "to": ends[1]}) if None not in ends else None
        out["derived"] = {k: v for k, v in out["derived"].items() if k not in fields}
        if said:
            out["differs"] = said
    return out


def _differs(store: Store) -> list[dict]:
    """Each link whose two texts describe different outcomes, with what differs (TOOLS.md 6), for as long as the claim
    stands: changing it reads its texts again."""
    out = []
    for lid, item in link_states(store).items():
        fields = JUDGED.get(item["relation"])
        checks = item.get("checks") or {}
        off = {f for f in fields or () if disputed((checks.get(f) or {}).get("answer"))}
        if not off:
            continue
        side = (store.link_by_id(lid)["stated"] or {}).get("side") or "yes"
        out.append({"link": lid, "relation": item["relation"], "from": item["from"], "to": item["to"],
                    "differs": _sentence(item["relation"], item["from"], item["to"], side, off)})
    return out


def _disputes(store: Store) -> list[dict]:
    """The judge's checks below one half on claims the analyst has neither changed nor answered, each with the
    Judgment that gave it (link_states). Changing the claim asks again; a note about the Judgment, saying why the
    reading is wrong, answers it, and a note about the link does not, being about the claim rather than the reading."""
    states = link_states(store)
    out = []
    for relation in ("asks", "implies"):
        for lid, item in states.items():
            if item["relation"] != relation:
                continue
            for f, got in (item.get("checks") or {}).items():
                if got["answer"] is None or not disputed(got["answer"]) or got["answered"]:
                    continue
                out.append({"link": lid, "relation": relation, "from": item["from"], "to": item["to"],
                            "check": "implication" if f == "check" else f"market, {f}", "answer": got["answer"],
                            "judgment": got["judgment"], "basis": got["basis"]})
    return out


# ---------------------------------------------------------------- what each link's check says


CHECKED = ("asks", "implies", "and", "or", "then")  # the links whose claims a check reads


def judged_relation(relation: str, answers: dict[str, float | None]) -> str | None:
    """The relation the judge's answers name, in the words of its five relations, where every answer is in. A market's:
    its Yes outcomes (No under the complement), the first, against the proposition's, the second; the forward question
    asks whether the second's lie within the first's, the converse whether the first's lie within the second's, and two
    answers of yes or no cannot tell some outcomes shared from none, so neither within the other is named as that. An
    implication's: whether the first's lie within the second's, which its one question cannot tell from the same."""
    if any(v is None for v in answers.values()):
        return None
    yes = {f: not disputed(v) for f, v in answers.items()}
    if relation == "implies":
        return "the first's within the second's, or the same outcomes" if yes["check"] else \
            "the first's not within the second's"
    if yes["forward"] and yes["converse"]:
        return "the same outcomes"
    if yes["converse"]:
        return "the first's within the second's"
    if yes["forward"]:
        return "the second's within the first's"
    return "neither's within the other's"


def _checks(store: Store, derived: dict, fields: tuple[str, ...]) -> dict[str, dict]:
    """Each of a link's checks by the judge: its answer (None where it has none yet), its Judgment and the Computation
    that read it, and whether a note about that Judgment answers it."""
    out = {}
    for f in fields:
        found = derived.get(f) or {}
        answer, basis = found.get("value"), found.get("basis")
        judgment = None
        if basis is not None and answer is not None:
            output = store.computation_output(basis)
            judgment = output["judgment"] if f == "check" else output["judgments"][f]
        out[f] = {"answer": answer, "judgment": judgment, "basis": basis,
                  "answered": bool(judgment) and _noted(store, judgment)}
    return out


def _question(store: Store, link: dict) -> dict:
    """What a link's check would ask now, as its check asks it (checks.market_question, implication_question): the
    questions, and the evidence they are asked over, the situation as fact included; or why it cannot be put now."""
    ctx = Inputs(store.engine)
    try:
        if link["relation"] == "asks":
            asked, found = market_question(ctx, link["from"], link["to"],
                                           link["stated"].get("side") or "yes")
        else:
            asked, found = implication_question(ctx, link["from"], link["to"])
    except (Unavailable, Waiting) as e:
        return {"unavailable": str(e)}
    return {"questions": [list(q) for q in asked], "evidence": found, "version": 1}


def link_states(store: Store, *, questions: bool = False) -> dict[str, dict]:
    """What each link's check says of the claim it makes, for check() and for a reply's frozen copy:
    every asks, implies, and, or, and then link, in the order made, as {relation, from, to, state, ...}.

    - `fields hold` or `fields contradict`: two propositions on one metric and resolution source, whose fields decide
      the claim (an implication, or the containment a composite claims), with the values compared;
    - `judge allows`, `judge does not allow`, or `answered by a note`: the judge's reading of a market check or of an
      implication the fields do not decide, in the words of the judge's five relations (`reading`), each check with
      its answer and Judgment;
      not allowed and answered where a note about the Judgment says why the reading is wrong;
    - `not yet checked`: a check the judge has not answered, and where `questions`, the question it would ask now,
      the situation as of now included, for asking later exactly as it would have been asked;
    - `a definition`: a composite whose fields do not decide its containment, which is the analyst's definition."""
    out: dict[str, dict] = {}
    marks = ",".join("?" for _ in CHECKED)
    for r in store._all(f"SELECT id FROM link WHERE relation IN ({marks}) ORDER BY rowid", *CHECKED):
        got = store.get(r["id"])
        relation, derived = got["relation"], got["derived"]
        item: dict[str, Any] = {"relation": relation, "from": got["from"], "to": got["to"]}
        tested = _value(derived, "terms")
        if tested is not None:
            item.update(state="fields hold" if tested["holds"] else "fields contradict", within=tested["within"],
                        of=tested["of"])
        elif relation in ("and", "or", "then"):
            item["state"] = "a definition"
        else:
            fields = ("forward", "converse") if relation == "asks" else ("check",)
            checks = _checks(store, derived, fields)
            refused = [c for c in checks.values() if c["answer"] is not None and disputed(c["answer"])]
            if any(not c["answered"] for c in refused):
                state = "judge does not allow"
            elif refused:
                state = "answered by a note"
            elif any(c["answer"] is None for c in checks.values()):
                state = "not yet checked"
            else:
                state = "judge allows"
            item.update(state=state, reading=judged_relation(relation, {f: c["answer"] for f, c in checks.items()}),
                        checks=checks)
            if questions and state == "not yet checked":
                item["question"] = _question(store, store.link_by_id(r["id"]))
        out[r["id"]] = item
    return out


def _wrong(store: Store) -> dict:
    """What cannot hold together (MATH.md 1.10): in each structure, each smallest set of links and chances that cannot
    hold, with the structure's probabilities waiting, and each taken book stretched past its bid or its ask, with how
    far; for each watched event, weights today's prices are stated to put on its outcomes that cannot hold, and each
    price at zero in every world those prices carry."""
    cannot, stretched = [], []
    for props in _components(store):
        found = store.engine.value("structure", props[0])
        out = found.output or {}
        for c in out.get("cannot_hold") or []:
            cannot.append({**c, "structure": props, "basis": found.basis})
        for lid, st in (out.get("stretched") or {}).items():
            stretched.append({"link": lid, **st, "basis": found.basis})
        for lid, st in (out.get("implied_stretched") or {}).items():
            if lid not in (out.get("stretched") or {}):
                stretched.append({"link": lid, **st, "links_alone": True, "basis": found.basis})
    return {"cannot_hold": cannot, "stretched": stretched, "weights_cannot_hold": _weights_wrong(store),
            "values_contradict_price": _values_wrong(store), **_worlds_wrong(store),
            "shocks_differ": _shocks_differ(store)}


def _shocks_differ(store: Store) -> list[dict]:
    """Differing shocks stated on two events that are true in the same worlds (MATH.md 1.9, 1.10): two watched events whose
    structure's links and fields leave them true in the same cases, each with a shock stated on one price, a factor's
    or a name's, that moves it differently between their Yes and their No. Each event's move is read from its row: the
    price's level on Yes over its level on No, each side's worlds at their probabilities, in the terms a shock on it is
    stated in, so that events stated against different baselines, or with ways, are compared on one footing. With the
    shocks and the Computations they are read from. An event whose figures wait has its pairs wait with them."""
    watched, out = set(watched_events(store)), []
    factors = {c["id"]: c["stated"] for c in store.query("Factor")}
    for props in _components(store):
        events = [p for p in props if p in watched]
        if len(events) < 2:
            continue
        found = store.engine.value("structure", props[0])
        if found.output is None or found.output.get("cannot_hold"):
            continue
        manifest = store.computation(found.basis)["inputs"]
        for a, b in same_worlds(manifest, list(itertools.combinations(events, 2))):
            read = {}
            for e in (a, b):
                try:
                    got = store.engine.value("impact", e)
                except Unavailable:
                    got = None
                if got is None or not (got.output or {}).get("worlds"):
                    break
                read[e] = got
            if len(read) < 2:
                continue
            stated = {e: _stated_on(read[e].output) for e in (a, b)}
            for target in [t for t in stated[a] if t in stated[b]]:
                moves = {e: _yes_over_no(read[e].output, target) for e in (a, b)}
                if abs(moves[a] / moves[b] - 1.0) <= 1e-9:
                    continue
                terms = factors.get(target) or {}
                out.append({"events": [a, b], "price": target,
                            "moves": {e: in_its_terms(terms, moves[e] - 1.0) for e in (a, b)},
                            "shocks": {e: stated[e][target] for e in (a, b)},
                            "basis": {e: read[e].basis for e in (a, b)}})
    return out


def _stated_on(impact: dict) -> dict[str, list[str]]:
    """Each price a shock is stated on somewhere in an event's worlds, with the consequences that state it."""
    out: dict[str, list[str]] = {}
    for setting in (impact.get("setting") or {}).values():
        for target, a in setting.items():
            if a["link"] not in out.setdefault(target, []):
                out[target].append(a["link"])
    return out


def _yes_over_no(impact: dict, target: str) -> float:
    """A price's level on an event's Yes over its level on its No, each side's worlds at their probabilities, from
    its move against the baseline in each world: a factor's (against_baseline) or a name's whole move."""
    ws = [{"id": w, "side": v["side"], "p": v["p"]} for w, v in impact["worlds"].items()]
    level = {w["id"]: 1.0 + (impact["against_baseline"][w["id"]][target] if not target.startswith("name:")
                             else impact["whole"][target][w["id"]]) for w in ws}
    return side_mean(ws, level, "yes") / side_mean(ws, level, "no")


def _values_wrong(store: Store) -> list[dict]:
    """Each holding whose values stated in an event's worlds put the weight its price implies outside 0 to 1 (1.6,
    1.10): the holding, the event, the weight, its price today and its values there."""
    out = []
    for pid in watched_events(store):
        try:
            found = store.engine.value("impact", pid)
        except Unavailable:
            continue
        for n, worth in ((found.output or {}).get("worth") or {}).items():
            implied = worth.get("implied")
            if implied is not None and not -1e-10 <= implied <= 1 + 1e-10:
                out.append({"id": pid, "name": n, "implied": implied, "price": worth["c"], "values": worth["levels"],
                            "basis": found.basis})
    return out


def _weights_wrong(store: Store) -> list[dict]:
    """Each watched event whose weights today's prices are stated to put on its outcomes cannot hold together (1.5)."""
    out = []
    for pid in watched_events(store):
        try:
            impact = store.engine.value("impact", pid)
        except Unavailable:
            continue
        if (impact.output or {}).get("weights_cannot_hold"):
            out.append({"id": pid, **impact.output["weights_cannot_hold"], "basis": impact.basis})
    return out


def _worlds_wrong(store: Store) -> dict:
    """Each watched event's prices that are at zero in every world today's prices carry, so that no scale brings them
    to today's price and the event has no figures (1.5, 1.10)."""
    zero = []
    for pid in watched_events(store):
        try:
            found = store.engine.value("impact", pid)
        except Unavailable:
            continue
        out = found.output or {}
        zero += [{"id": pid, "price": u["price"], "levels": u["levels"], "weights": u["weights"], "basis": found.basis}
                 for u in out.get("unplaceable") or []]
    return {"price_at_zero_where_priced": zero}


def _fields_contradict(store: Store) -> list[dict]:
    """Claims between two propositions on one metric and resolution source that their fields do not give, with the
    values compared (link_states): an implication, and the containment a composite of and, or, or then claims. A
    proposition's fields count its outcomes as stated, so a claim they do not give is one they contradict; it is
    answered by changing the claim or the propositions, since a note does not change arithmetic."""
    return [{"link": lid, "relation": item["relation"], "from": item["from"], "to": item["to"],
             "within": item["within"], "of": item["of"]}
            for lid, item in link_states(store).items() if item["state"] == "fields contradict"]


def check(store: Store) -> dict:
    """What the analyst cannot see from the return of its own statement (TOOLS.md 6): what cannot hold together (1.10),
    with the figures involved; each claim between two propositions that their fields contradict, with the values
    compared; and the judge's readings that do not allow a claim, until the claim changes or a note answers the
    reading. What an event lacks, a probability or a shock that sets something, is in its row and in the return of
    each statement about it. It gates nothing."""
    return {"fields_contradict": _fields_contradict(store), "differs": _differs(store), **_wrong(store)}


# ---------------------------------------------------------------- what a store records


def records(store: Store, trace: list[dict] | None = None) -> dict:
    """What a store records, read back from the store and its trace: each conversation; checks by kind and by model;
    markets searched and linked; the assumptions stated, with their bases; the
    consequences, channels, and metrics added; the links removed."""
    entries = trace if trace is not None else Trace(store.trace_path).entries()
    sessions: dict[str, dict] = {}
    removed = []
    for e in entries:
        s = sessions.setdefault(e.get("session"), {"session": e.get("session"), "turns": 0, "calls": 0,
                                                   "errors": 0, "tokens": {}})
        if e["type"] == "user":
            s["turns"] += 1
        elif e["type"] == "assistant":
            for k, v in (e.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    s["tokens"][k] = s["tokens"].get(k, 0) + v
        elif e["type"] == "call":
            s["calls"] += 1
            s["errors"] += "error" in e
            if e["tool"] == "remove" and "result" in e:
                got = e["result"]
                for link in [got["removed"], *got.get("cascaded", [])]:
                    if "relation" in link:
                        removed.append({"call": e["call"], **link})
    assumptions = [_assumption(store, p["id"], key, v) for p in store.query("Proposition")
                   for key, v in schema.assumptions("Proposition", p["stated"]).items()]
    based = [r for r in schema.RELATIONS if any(f.kind in schema.BASED for f in schema.RELATIONS[r].stated)]
    assumptions += [_assumption(store, link["id"], key, v)
                    for link in (store.link_by_id(r["id"]) for r in store._all(
                        f"SELECT id FROM link WHERE relation IN ({','.join('?' * len(based))}) ORDER BY rowid",
                        *based))
                    for key, v in schema.assumptions(link["relation"], link["stated"]).items()]
    by_kind: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for r in store._all("SELECT kind, model, count(*) AS n FROM judgment GROUP BY kind, model ORDER BY kind, model"):
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["n"]
        by_model[r["model"]] = by_model.get(r["model"], 0) + r["n"]
    searched = [dict(r) for r in store._all("SELECT id, tool, venue, endpoint, query, proposition_id, results, "
                                            "created_call FROM search ORDER BY rowid")]
    for s in searched:
        s["returned"] = len(json.loads(s.pop("results")))
    linked = [{"link": link["id"], "proposition": link["from"], "market": link["to"]}
              for link in (store.link_by_id(r["id"]) for r in store._all(
                  "SELECT id FROM link WHERE relation = 'asks' ORDER BY rowid"))]
    title = lambda stated: stated.get("title") or stated.get("name")  # noqa: E731
    added = {kind: [{"id": r["id"], "title": title(json.loads(r["stated"])), "created_call": r["created_call"]}
                    for r in store._all("SELECT id, stated, created_call FROM object WHERE type = ? AND origin = "
                                        "'analyst' ORDER BY rowid", kind)]
             for kind in ("Factor", "Metric")}
    consequences = [{"link": r["id"], "event": r["from_id"], "target": r["to_id"], "created_call": r["created_call"]}
                    for r in store._all("SELECT id, from_id, to_id, created_call FROM link WHERE relation = "
                                        "'consequence' ORDER BY rowid")]
    return {"conversations": [s for s in sessions.values() if s["session"] is not None],
            "judgments": {"by_question": by_kind, "by_model": by_model},
            "markets": {"searches": searched, "linked": linked},
            "added": {"channels": added["Factor"], "metrics": added["Metric"],
                      "consequences": consequences},
            "assumptions": assumptions, "removed_links": removed}

