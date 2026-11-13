"""Each structure's probabilities (MATH.md 1.2), as the store reads them.

A structure is the propositions the analyst relates to one another, by a link of the breakdown (`implies`, `and`,
`or`, `then`, `way`) or by a chance, which ties the proposition it is the chance of to each proposition it is given.
One derivation reads each structure whole, through `oneform.read`, and gives every proposition in it:

- `range`: what the markets it takes imply through the links (MATH.md 1.2, "the range the markets imply");
- `probability`, where the links, the chances and the taken markets fix it;
- `left_open`, where they leave it open: the range they leave, with each priced proposition at its probability, and
  the chances that would settle it.

A market's book enters where its price is taken and no chance follows it: a chance that follows a market takes the
market's place, carrying it forward from its price when the chance was stated (MATH.md 1.2). The structure's Computation
also holds, for each proposition, every market that asks it and every chance it states, and for the structure, what
cannot hold together and each stretched book, which check() lists (MATH.md 1.10).
"""

from __future__ import annotations

import math
from collections import deque
from typing import Callable

from harness import schema
from harness.derive import checks, oneform
from harness.derive import evidence as ev
from harness.derive.engine import Derivation, Inputs, Refused, Unavailable
from harness.derive.markets import probability, taken

LINKS = ("implies", "and", "or", "then", "way")  # the links of a breakdown
FOLLOW_STAMP = "chances:follow"  # when a chance that follows a market was stated (Store._stamped)


def _seq(pid: str) -> tuple[int, str]:
    m = schema.SEQ_ID_RE.match(pid)
    return (int(m.group(2)), pid) if m else (10 ** 12, pid)


def market(x: str) -> bool:
    return bool(schema.MARKET_ID_RE.match(x))


def component(links_of: Callable[[str], list[dict]], chances_of: Callable[[str], list[dict]],
              given_by: Callable[[str], list[str]], pid: str,
              partners: Callable[[str], list[str]] = lambda p: []) -> list[str]:
    """Every proposition related to pid by a link of the breakdown, by a chance, or by the fields that place them on
    one line, at any distance, in creation order (reach)."""
    return reach(links_of, chances_of, given_by, pid, partners)[0]


def reach(links_of: Callable[[str], list[dict]], chances_of: Callable[[str], list[dict]],
          given_by: Callable[[str], list[str]], pid: str,
          partners: Callable[[str], list[str]] = lambda p: []) -> tuple[list[str], list[str]]:
    """The propositions related to pid, at any distance, in creation order, and the markets they are related through.
    A market stands as the proposition its question and rules define (MATH.md 1.2), so a chance given it, an implication
    to or from it, and a proposition that asks it all relate through it."""
    seen, queue = {pid}, deque([pid])
    while queue:
        here = queue.popleft()
        near = [link["to"] if link["from"] == here else link["from"] for link in links_of(here)
                if link["relation"] in schema.BREAKDOWN]  # a market asked by a proposition is that proposition
        if not market(here):
            near += [x for c in chances_of(here) for x in (*c["given"], *c["given_not"])]
            near += partners(here)
        near += given_by(here)
        for other in near:
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return sorted((x for x in seen if not market(x)), key=_seq), sorted(x for x in seen if market(x))


def lines(ctx: Inputs, pids: list[str]) -> dict[str, tuple[tuple, list]]:
    """Each proposition the fields place on a line, with its line and its set there (checks.line_of)."""
    asof = ev.asof(ctx)
    out = {}
    for p in pids:
        found = checks.line_of(checks.reading(ctx, p, asof))
        if found is not None:
            out[p] = found
    return out


def _complement(s: list) -> list[list]:
    """The values a set leaves out, as one or two sets."""
    lo, lo_c, hi, hi_c = s
    return ([[None, False, lo, not lo_c]] if lo is not None else []) + \
        ([[hi, not hi_c, None, False]] if hi is not None else [])


def _partition(target: list, others: dict[str, list]) -> list[str] | None:
    """The finest set of others that makes up target exactly, in order from its lower end: at each step the one that
    starts where the last ended and ends soonest within target; None where none starts there."""
    out, (lo, lo_c) = [], (target[0], target[1])
    end = lambda s: (float("inf") if s[2] is None else s[2], 1 if s[3] else 0)  # noqa: E731
    while True:
        starts = [(p, s) for p, s in others.items() if s[0] == lo and (lo is None or s[1] == lo_c)
                  and end(s) <= end(target) and p not in out]
        if not starts:
            return None
        p, s = min(starts, key=lambda x: end(x[1]))
        out.append(p)
        if end(s) == end(target):
            return out
        lo, lo_c = s[2], not s[3]


def bands(ctx: Inputs, root: str) -> dict[str, list[str]]:
    """The worlds a ladder's bands give a level on a date (MATH.md 1.2, "Bands"): each side of it that its bands make up
    (`possible_bands`) and that the analyst states something of, as it would of a way: a consequence or a value on one
    of them, a weight or an episode stated for one, or the level's baseline naming one. A side nothing is stated of
    stays whole: its bands would each take its own figures."""
    possible = possible_bands(ctx, root)
    if not possible:
        return {}
    base = ctx.object(root)["stated"].get("baseline")
    return {side: got for side, got in possible.items() if base in got or any(_said_of(ctx, b) for b in got)}


def _said_of(ctx: Inputs, pid: str) -> bool:
    """Whether the analyst states something of a band as it would of a way (MATH.md 1.2)."""
    stated = ctx.object(pid)["stated"]
    return bool(ctx.links(pid, "consequence") or ctx.links(pid, "worth") or stated.get("priced_in")
                or stated.get("episode"))


def possible_bands(ctx: Inputs, root: str) -> dict[str, list[str]]:
    """On each side of a level on a date, the propositions its line holds that make that side up exactly, in order, the
    bands between two thresholds and the rungs beyond them, the finest such set (MATH.md 1.2). A side they do not make up
    whole has none."""
    placed = lines(ctx, [o["id"] for o in ctx.objects("Proposition")])
    if root not in placed or placed[root][0][0] != "level":
        return {}
    key, mine = placed[root]
    others = {p: s for p, (k, s) in placed.items() if k == key and p != root}
    out = {}
    for side, pieces in (("yes", [mine]), ("no", _complement(mine))):
        got: list[str] | None = []
        for piece in pieces:
            part = _partition(piece, others)
            if part is None:
                got = None
                break
            got += part
        if got:
            out[side] = got
    return out


def _partners(placed: dict[str, tuple[tuple, list]]) -> Callable[[str], list[str]]:
    by_line: dict[tuple, list[str]] = {}
    for p, (key, _) in placed.items():
        by_line.setdefault(key, []).append(p)
    return lambda p: [q for q in by_line.get(placed[p][0], []) if q != p] if p in placed else []


def store_component(store, pid: str) -> list[str]:
    placed = lines(Inputs(store.engine), [o["id"] for o in store.query("Proposition")])
    return component(lambda o: store.links_of(o, schema.BREAKDOWN, "both"),
                     lambda o: (store.stated_of(o) or {}).get("chances") or [],
                     lambda o: [p for p, _ in store.given_by(o)], pid, _partners(placed))


def ctx_component(ctx: Inputs, pid: str) -> list[str]:
    return ctx_reach(ctx, pid)[0]


def ctx_reach(ctx: Inputs, pid: str) -> tuple[list[str], list[str]]:
    placed = lines(ctx, [o["id"] for o in ctx.objects("Proposition")])
    return reach(lambda o: ctx.links(o, schema.BREAKDOWN, "both"),
                 lambda o: ctx.object(o)["stated"].get("chances") or [],
                 lambda o: [p for p, _ in ctx.given_by(o)], pid, _partners(placed))


def subject(store, pid: str) -> str:
    """The structure a proposition is read in, named by its first-created proposition."""
    link = store.link_by_id(pid)
    return store_component(store, link["from"] if link else pid)[0]


# ---------------------------------------------------------------- what each proposition brings


def markets(ctx: Inputs, pid: str) -> list[dict]:
    """Every market that asks the proposition: its bid, ask and price through the side it asks, its depth, and whether
    its price is taken."""
    out = []
    for link in ctx.links(pid, "asks"):
        took = taken(link)
        try:
            q = ctx.value("quoted", link["id"])
        except Unavailable:  # a market that has no quote to read: no price
            out.append({"link": link["id"], "market": link["to"], "price": None, "bid": None, "ask": None,
                        "depth": None, "taken": took})
            continue
        found = q["output"]
        out.append({"link": link["id"], "market": link["to"], "price": found["price"], "bid": found["bid"],
                    "ask": found["ask"], "depth": found.get("depth"), "taken": took, "basis": q["basis"]})
    return out


def followed(c_then: float, r: float, q_then: float, q_now: float) -> float:
    """A chance that follows a market (MATH.md 1.2): logit(c_now) = logit(c_then) + r (logit(q_now) - logit(q_then)).
    A responsiveness of 0, or a market where it stood, keeps the stated chance; a stated 0 or 1 stays; and for a
    responsiveness above 0 the chance goes to 0 or 1 with the market."""
    if r == 0.0 or q_now == q_then or c_then in (0.0, 1.0):
        return c_then
    if q_now in (0.0, 1.0):
        return q_now
    if q_then in (0.0, 1.0):  # a market that stood at a certainty when the chance was stated gives it no odds to move by
        return c_then
    logit = lambda x: math.log(x / (1.0 - x))  # noqa: E731
    z = logit(c_then) + r * (logit(q_now) - logit(q_then))
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def _reading(ctx: Inputs, pid: str, c: dict) -> dict:
    """A reading of a ladder of deadlines (MATH.md 1.2), as its dates give it: theta, how far the deadline t lies from the
    earlier rung t1 (or the line's start, before the first rung) toward the later rung t2; and, even through time or
    by the calendar, the share s itself. At a constant hazard the share reads the rungs' probabilities, so it is worked
    out with the structure (_hazard)."""
    from datetime import date as day
    asof = ev.asof(ctx)
    through = c.get("through") or {}  # a market's date as a rung: the day its question counts through, as stated
    on = lambda x: (day.fromisoformat(through[x]).toordinal() if x in through  # noqa: E731
                    else day.fromisoformat(ctx.object(x)["stated"]["window_end"]).toordinal())  # a deadline's date
    stated = ctx.object(pid)["stated"]
    t2 = on(c["given"][0])
    t1 = on(c["given_not"][0]) if c["given_not"] else \
        day.fromisoformat(stated.get("window_start") or asof).toordinal()
    t = on(pid)
    theta = (t - t1) / (t2 - t1)
    r = c["reading"]
    out = {"kind": "calendar" if isinstance(r, dict) else r, "theta": theta, "first": not c["given_not"],
           "t1": c["given_not"][0] if c["given_not"] else None, "t2": c["given"][0]}
    if r == "even":
        out["share"] = theta
    elif isinstance(r, dict):
        weight = lambda lo, hi: sum(d["weight"] for d in r["calendar"]  # noqa: E731
                                    if lo < day.fromisoformat(d["date"]).toordinal() <= hi)
        out["share"] = weight(t1, t) / weight(t1, t2)
    return out


def _hazard(reading: dict, p: dict) -> float | None:
    """The share at a constant hazard (MATH.md 1.2), from the rungs' probabilities as the structure gives them: between
    two rungs, 1 - F(t) = (1 - F1) ((1 - F2) / (1 - F1))^theta and s = (F(t) - F1) / (F2 - F1); before the first,
    the chance given it, (1 - (1 - F1)^theta) / F1. Where the rungs leave nothing between them, any share gives the
    same, and the share is theta; None where a rung's probability is open."""
    theta = reading["theta"]
    if reading["first"]:
        f1 = p.get(reading["t2"])
        if f1 is None:
            return None
        return theta if f1 <= 1e-12 else (1.0 - (1.0 - f1) ** theta) / f1
    f1, f2 = p.get(reading["t1"]), p.get(reading["t2"])
    if f1 is None or f2 is None:
        return None
    if f2 - f1 <= 1e-12 or f1 >= 1.0 - 1e-12:
        return theta
    f = 1.0 - (1.0 - f1) * ((1.0 - f2) / (1.0 - f1)) ** theta
    return (f - f1) / (f2 - f1)


def _chance(ctx: Inputs, pid: str, i: int, c: dict) -> dict:
    """A stated chance as the structure reads it; where it follows a market, the market's probability when the chance
    was stated and now, each at its latest priced reading, with that reading's time (markets.probability); where it
    names a reading of a ladder, the reading as its dates give it."""
    out = {"key": f"{pid}.chances[{i}]", "of": pid, "given": list(c["given"]), "given_not": list(c["given_not"]),
           "value": c.get("value")}
    if "reading" in c:
        got = _reading(ctx, pid, c)
        return {**out, "value": got.get("share"), "reading": got}
    mid = c.get("follow")
    if not mid:
        return out
    stated_at = ctx.stated_at(pid, FOLLOW_STAMP)
    if mid.startswith("name:"):
        from harness.derive.worth_follow import follow_inputs
        return {**out, "follow": {"name": mid, "responsiveness": c["responsiveness"], "stated_at": stated_at,
                                   "manifest": follow_inputs(ctx, pid, mid, stated_at), "value": c["value"]}}
    lid = next((x["id"] for x in ctx.links(pid, "asks") if x["to"] == mid), None)
    if lid is None:  # the store refuses removing it while the chance follows it
        raise Unavailable(f"{pid}'s chance follows {mid}, which does not ask it")
    return {**out, "follow": {"market": mid, "link": lid, "responsiveness": c["responsiveness"],
                              "stated_at": stated_at, "then": probability(ctx, lid, at=stated_at),
                              "now": probability(ctx, lid)}}


def standing(asks_in: Callable[[str], list[dict]], mids: list[str], members: set[str]) -> dict[str, tuple[str, bool]]:
    """Where a proposition asks a market, the market stands as that proposition (MATH.md 1.2): its Yes is the proposition
    true, or false through the link's No side. By the first such link; every other market stands as itself.
    `asks_in(mid)` gives the asks links into a market, from the store or from a record's copy of it."""
    out = {}
    for mid in mids:
        asked = sorted((x for x in asks_in(mid) if x["relation"] == "asks" and x["to"] == mid and x["from"] in members),
                       key=lambda x: _seq(x["id"]))
        if asked:
            out[mid] = (asked[0]["from"], (asked[0]["stated"].get("side") or "yes") == "yes")
    return out


def breakdown_links(candidates: list[dict], members: set[str], mids: list[str],
                    stands: dict[str, tuple[str, bool]]) -> list[dict]:
    """The links of the breakdown among a structure's propositions and the markets it reaches, as the one form reads
    them (MATH.md 1.2): each end that is a market a proposition asks is that proposition, true or, through the asks
    link's No side, false (`from_true`, `to_true`); a market no proposition asks is itself. Each link once, in the
    order given. The store's structure and the record's outcomes read market ends through this one construction."""
    out, seen = [], set()
    for link in candidates:
        rel = link["relation"]
        if rel not in LINKS or link["id"] in seen:
            continue
        ends = [link["from"], link["to"]]
        if not all(x in members or x in mids for x in ends):
            continue
        seen.add(link["id"])
        (a, fa), (b, tb) = (_as(stands, x) for x in ends)
        out.append({"id": link["id"], "relation": rel, "from": a, "to": b,
                    **({} if fa else {"from_true": False}), **({} if tb else {"to_true": False}),
                    **({"step": link["stated"].get("step")} if rel == "then" else {}),
                    **({"side": link["stated"]["side"]} if rel == "way" and link["stated"].get("side") else {})})
    return out


def _as(stands: dict[str, tuple[str, bool]], x: str) -> tuple[str, bool]:
    """A proposition, or a market as the proposition it stands as, and whether it is that proposition true."""
    return stands.get(x, (x, True))


def _market_book(ctx: Inputs, mid: str) -> dict:
    """A market that stands as itself, taken at its own book through its Yes side (MATH.md 1.2): its bid, ask, price and
    depth, as a market a proposition asks gives them; where its book cannot be read, it has no price, as a market a
    proposition asks has none then (markets), and bounds nothing."""
    try:
        found = ctx.value("market_price", mid)
    except Unavailable:
        return {"link": mid, "market": mid, "price": None, "bid": None, "ask": None, "depth": None, "taken": True}
    out = found["output"]
    return {"link": mid, "market": mid, "price": out.get("price"), "bid": out.get("bid"), "ask": out.get("ask"),
            "depth": out.get("depth"), "taken": True, "basis": found["basis"]}


def _structure_inputs(ctx: Inputs, first: str) -> dict:
    props, mids = ctx_reach(ctx, first)
    members = set(props)
    stands = standing(lambda mid: ctx.links(mid, "asks", direction="in"), mids, members)
    nodes = [m for m in mids if m not in stands]  # a market no proposition asks stands as itself
    chances, books, listed = [], [], {}
    links = breakdown_links([link for here in (*props, *mids)
                             for link in ctx.links(here, direction="both" if market(here) else "out")],
                            members, mids, stands)
    for mid in nodes:
        book = _market_book(ctx, mid)
        listed[mid] = [book]
        if book["bid"] is not None or book["ask"] is not None:
            books.append({k: book[k] for k in ("bid", "ask", "price", "depth")} | {"link": mid, "on": mid})
    for pid in props:
        stated = ctx.object(pid)["stated"]
        following = {c["follow"] for c in stated.get("chances") or [] if c.get("follow")}
        here = markets(ctx, pid)
        listed[pid] = [{**m, **({"followed": True} if m["market"] in following else {})} for m in here]
        for m in here:  # a taken book bounds its proposition; one a chance follows informs it through that chance
            if m["taken"] and m["market"] not in following and (m["bid"] is not None or m["ask"] is not None):
                books.append({"link": m["link"], "on": pid, "bid": m["bid"], "ask": m["ask"], "price": m["price"],
                              "depth": m["depth"]})
        for i, c in enumerate(stated.get("chances") or []):
            got = _chance(ctx, pid, i, _standing_as(stands, c))
            chances.append(got)
            links += _rungs(stands, got, c)
    placed = lines(ctx, props)
    by_line: dict[tuple, dict[str, list]] = {}
    for p in props:
        if p in placed:
            by_line.setdefault(placed[p][0], {})[p] = placed[p][1]
    on_lines = [{"members": members} for members in by_line.values() if len(members) > 1]
    return {"props": [*props, *nodes], "links": links, "chances": chances, "books": books, "markets": listed,
            **({"lines": on_lines} if on_lines else {}), **({"standing": stands} if stands else {})}


def _standing_as(stands: dict[str, tuple[str, bool]], c: dict) -> dict:
    """A chance given markets, each as the proposition it stands as: given a market a proposition asks through its No
    side is given not that proposition. A market's date as a rung goes with it."""
    if not stands:
        return c
    given, given_not = [], []
    for x in c["given"]:
        p, yes = _as(stands, x)
        (given if yes else given_not).append(p)
    for x in c["given_not"]:
        p, yes = _as(stands, x)
        (given_not if yes else given).append(p)
    through = {_as(stands, m)[0]: d for m, d in (c.get("through") or {}).items()}
    return {**c, "given": given, "given_not": given_not, **({"through": through} if through else {})}


def _rungs(stands: dict[str, tuple[str, bool]], got: dict, c: dict) -> list[dict]:
    """A deadline reading states that the proposition and its rungs concern the same occurrence or threshold, counted
    from the same start, by their dates (MATH.md 1.2): so the earlier rung implies the proposition, the proposition the
    later, and the earlier the later. That order is structure, part of the range the markets imply as every link is;
    only the share the reading chooses is the analyst's number. Each is named by the reading's chance (`{key}.rungs`),
    so that what cannot hold names it, and kept apart from the chance's own value, which the range leaves out."""
    if "reading" not in got:
        return []
    later = got["given"][0]
    earlier = got["given_not"][0] if got["given_not"] else None
    pairs = [(got["of"], later)] + ([(earlier, got["of"]), (earlier, later)] if earlier else [])
    return [{"id": f"{got['key']}.rungs", "relation": "implies", "from": a, "to": b} for a, b in pairs]

def _now(c: dict) -> float | None:
    """A chance's figure now: as stated, or, following a market, carried forward with it."""
    f = c.get("follow")
    if not f:
        return c["value"]
    if "name" in f:
        return f["value"]
    return followed(c["value"], f["responsiveness"], f["then"]["q"], f["now"]["q"])


def _shown(c: dict) -> dict:
    """A chance as a proposition's reading shows it; a reading of a ladder with its share now, the chance it gives."""
    out = {"key": c["key"], "given": c["given"], "given_not": c["given_not"], "value": _now(c)}
    if c.get("reading"):
        out["reading"] = {k: c["reading"][k] for k in ("kind", "theta")}
    f = c.get("follow")
    if f:
        source = {"name": f["name"]} if "name" in f else {"market": f["market"], "link": f["link"]}
        out.update(stated=c["value"], follow={**source,
                                              "responsiveness": f["responsiveness"], "stated_at": f["stated_at"],
                                              "q_then": (f.get("then") or {}).get("q"),
                                              "then_at": (f.get("then") or {}).get("at"),
                                              "q_now": (f.get("now") or {}).get("q"),
                                              "now_at": (f.get("now") or {}).get("at"),
                                              **({"why": f["why"]} if f.get("why") else {})})
    return out


def _figure(m: dict, statement: str) -> dict:
    """A statement of a set that cannot hold, with its figures (MATH.md 1.10): a chance with its value now and the
    propositions it is given; a link with its relation and its ends; a proposition's fields with the set of values,
    or of dates, they place it at on its line."""
    if statement.endswith(".fields"):
        pid = statement[:-len(".fields")]
        for line in m.get("lines") or []:
            if pid in line["members"]:
                return {"id": statement, "of": pid, "on_line": line["members"][pid]}
    for c in m["chances"]:
        if c["key"] == statement:
            return {"id": statement, "of": c["of"], "given": c["given"], "given_not": c["given_not"], "value": _now(c)}
    for link in m["links"]:
        if link["id"] == statement:
            return {"id": statement, **{k: v for k, v in link.items() if k != "id" and v is not None}}
    return {"id": statement}


def _structure_compute(m: dict, load) -> dict:
    """The structure read whole. Where the solver cannot finish it, the statement that made it so is refused with
    why, naming the structure's propositions (TOOLS.md 5)."""
    chances = [{**c, **({"follow": dict(c["follow"])} if c.get("follow") else {})} for c in m["chances"]]

    def read() -> dict:
        given = [{k: c[k] for k in ("key", "of", "given", "given_not")} | {"value": _now(c)} for c in chances
                 if _now(c) is not None]
        return oneform.read({"props": m["props"], "links": m["links"], "books": m["books"], "lines": m.get("lines"),
                             "chances": given})

    hazards = [c for c in chances if (c.get("reading") or {}).get("kind") == "hazard"]
    names = [c for c in chances if (c.get("follow") or {}).get("name")]
    if names:
        from harness.derive.worth_follow import follow_value
    try:
        for c in hazards:  # a constant hazard reads the rungs as the structure gives them: read until it holds still
            c["value"] = c["reading"]["theta"]
        found = read()
        previous_step = {}
        damping = 1.0
        for _ in range(200):
            if not hazards and not names:
                break
            p = {q: node.get("p") for q, node in found["props"].items()}
            moved = 0.0
            updates = []
            for c in hazards:
                s = _hazard(c["reading"], p)
                updates.append((c["key"], c, "value", s))
            for c in names:
                f = c["follow"]
                got = follow_value(f["manifest"], load, found["props"], responsiveness=f["responsiveness"])
                if got.get("outside"):  # values that contradict the price: the chance has no figure, and the
                    raise Unavailable(got["why"])  # structure waits on a statement changing (1.10); none is refused
                f.pop("why", None)
                f.update(got)
                q_then, q_now = got["then"]["q"], got["now"]["q"]
                s = (c["value"] if f["responsiveness"] == 0 or c["value"] in (0.0, 1.0) else
                     None if q_then is None or q_now is None else
                     followed(c["value"], f["responsiveness"], q_then, q_now))
                updates.append((c["key"], f, "value", s))
            for key, obj, field, s in updates:
                old = obj[field]
                delta = s - old if s is not None and old is not None else None
                moved = max(moved, abs(delta) if delta is not None else float(s != old))
                if delta is not None and delta * previous_step.get(key, 0.0) < 0:
                    damping = min(damping, .5)
                previous_step[key] = delta or 0.0
                obj[field] = old + damping * delta if delta is not None else s
            if moved <= 1e-12:
                break
            found = read()
        else:
            dependent = ", ".join(c["key"] for c in (*hazards, *names))
            raise Refused(f"{m['props'][0]}'s structure could not be read: {dependent} each take their value from the "
                          f"probabilities they help set, a constant hazard's or a holding's price, and no set of "
                          f"probabilities holds them all", m["props"])
        # The shown chances, source weights and propositions all describe the same final reading.
        found = read()
    except oneform.Unfinished as e:
        raise Refused(f"{m['props'][0]}'s structure could not be read: {e}", m["props"]) from None
    props = {}
    for p in m["props"]:
        props[p] = {**found["props"][p], "markets": m["markets"][p],
                    "chances": [_shown(c) for c in chances if c["of"] == p]}
    stretched = {s["link"]: {k: v for k, v in s.items() if k != "link"} for s in found["stretched"]}
    implied = {s["link"]: {k: v for k, v in s.items() if k != "link"} for s in found.get("implied_stretched") or []}
    cannot = [{**c, "figures": [_figure({**m, "chances": chances}, s) for s in c["statements"]]}
              for c in found["cannot_hold"]]
    return {"props": props, "cannot_hold": cannot, "stretched": stretched,
            **({"implied_stretched": implied} if implied else {})}


def _pick(out: dict, pid: str) -> dict:
    """A proposition's range, and its probability or the range its statements leave."""
    node = out["props"].get(pid) or {}
    got: dict = {}
    if "range" in node:
        got["range"] = [node["range"]["lo"], node["range"]["hi"]]
    if "p" in node:
        got["probability"] = node["p"]
    if "open" in node:
        got["left_open"] = {k: node["open"][k] for k in ("lo", "hi", "settled_by", "with") if k in node["open"]}
    return got


STRUCTURE = Derivation("structure", 7, "Proposition", _structure_inputs, _structure_compute,
                       fields=("range", "probability", "left_open"), subject_of=subject, pick=_pick)


def node(ctx: Inputs, pid: str) -> dict:
    """A proposition's reading in its structure, with the structure's basis: {output, basis}."""
    found = ctx.value("structure", ctx_component(ctx, pid)[0])
    return {"output": found["output"]["props"][pid], "basis": found["basis"],
            "cannot_hold": found["output"]["cannot_hold"]}


# ---------------------------------------------------------------- the words for what a structure leaves


def _said(of: str, given, given_not) -> str:
    cond = [*given, *(f"not {x}" for x in given_not)]
    return f"the chance of {of} given {' and '.join(cond)}"


def settlers(o: dict) -> str:
    """What would settle a probability left open, in words: each alternative, and what it rests on that is open."""
    ways = [" and ".join(_said(c["of"], c["given"], c["given_not"]) for c in alt) for alt in o.get("settled_by") or []]
    if o.get("with"):
        ways.append(f"what settles {' and '.join(o['with'])}, which it rests on")
    return ", or ".join(ways)


def left_open_text(pid: str, o: dict) -> str:
    """A probability left open, in words: the range its statements leave, and what would settle it."""
    out = f"{pid}'s probability is left open, from {o['lo']:.6g} to {o['hi']:.6g}."
    said = settlers(o)
    if said:
        first, _, rest = said.partition(", or ")
        out += f" {first[0].upper()}{first[1:]} would settle it" + (f", or {rest}" if rest else "") + "."
    return out


def cannot_hold_text(sets: list[dict]) -> str:
    """What cannot hold together in a structure, in words."""
    out = []
    for s in sets:
        named = ", ".join(s["statements"])
        if s.get("leaves_no_probability"):
            case = " and ".join(p if v else f"not {p}" for p, v in s["leaves_no_probability"].items())
            out.append(f"{named}: the case {case} has no probability where they hold, and a chance given it is "
                       f"said of it")
        else:
            out.append(named)
    return "; ".join(out)


DERIVATIONS = (STRUCTURE,)
