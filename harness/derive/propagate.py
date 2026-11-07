"""The interval: the markets' bounds carried through the breakdown to a fixed point (MATH.md, propagation).

Logic carries the markets' prices through the analyst's breakdown into each proposition's interval, what the markets
alone say about it. A quoted_by link gives its proposition [bid, ask], or [1 - ask, 1 - bid] under the complement: a
side of the book with no orders bounds nothing. An implies link from A to B caps A at B's ceiling and lifts B to A's
floor. A composite of and, or, or then is bounded by its components by the table:

    A = B and C     lo = max(0, lo_B + lo_C - 1), hi = min(hi_B, hi_C)
    A = B or C      lo = max(lo_B, lo_C),         hi = min(1, hi_B + hi_C)
    A = B then C    lo = 0,                        hi = hi_B

and a composite of and or then implies each of its components, and each component of or implies its composite, so
each such link also bounds as that implies does. No bound is scaled: each is what the claim says if it holds. Whether
it holds is the judge's to check, and a disputed claim still bounds, because the claim is the analyst's.

A proposition's component is every proposition connected to it by implies, and, or, or then; the rules are applied
over the component from [0, 1] to a fixed point. Bounds that cross mean a claim or a market is wrong: the proposition
has no interval, and what set each side is kept for check() to list.
"""

from __future__ import annotations

from collections import deque

from harness import schema
from harness.derive.engine import Derivation, Inputs, Unavailable

STRUCTURE = ("implies", "and", "or", "then")  # the links of a breakdown
TOL = 1e-12  # bounds closer than this are equal
ROUNDS = 1000  # the fixed point is reached in a few; a cap so a malformed graph cannot spin


def _seq(pid: str) -> tuple[int, str]:
    m = schema.SEQ_ID_RE.match(pid)
    return (int(m.group(2)), pid) if m else (10 ** 12, pid)


def component(read_links, pid: str) -> list[str]:
    """Every proposition connected to pid by implies, and, or, or then, in creation order."""
    seen, queue = {pid}, deque([pid])
    while queue:
        here = queue.popleft()
        for link in read_links(here):
            if link["relation"] in STRUCTURE:
                other = link["to"] if link["from"] == here else link["from"]
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
    return sorted(seen, key=_seq)


def store_links(store):
    return lambda oid: store.links_of(oid, direction="both")


def ctx_links(ctx: Inputs):
    return lambda oid: ctx.links(oid, direction="both")


def _component_of(store, entity: str) -> str:
    """The subject of the propagation: the first-created proposition of the entity's component."""
    link = store.link_by_id(entity)
    return component(store_links(store), link["from"] if link else entity)[0]


# ---------------------------------------------------------------- a market's price, as its proposition reads it


def transformed(price: dict, transform: str) -> dict:
    """The proposition's bid, ask, and price from its market's: the same under the identity, and under the complement
    one minus each, bid and ask exchanged."""
    bid, ask, p = price.get("bid"), price.get("ask"), price.get("price")
    if transform != "complement":
        return {"bid": bid, "ask": ask, "price": p}
    flip = lambda x: None if x is None else 1.0 - x  # noqa: E731
    return {"bid": flip(ask), "ask": flip(bid), "price": flip(p)}


def _quoted_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    return {"link": lid, "transform": link["stated"].get("transform") or "identity",
            "market": ctx.value("market_price", link["to"])}


def _quoted_compute(m: dict, load) -> dict:
    found = m["market"]["output"]
    return {**transformed(found, m["transform"]), "depth": found.get("depth"), "past": bool(found.get("past"))}


QUOTED = Derivation("quoted", 1, "quoted_by", _quoted_inputs, _quoted_compute, fields=("bid", "ask", "price"))


# ---------------------------------------------------------------- the fixed point


def _value_or_reason(ctx: Inputs, name: str, subject: str) -> dict:
    try:
        return ctx.value(name, subject)
    except Unavailable as e:
        return {"derivation": name, "subject": subject, "output": None, "reason": str(e)}


def _propagate_inputs(ctx: Inputs, first: str) -> dict:
    props = component(ctx_links(ctx), first)
    links = []
    for pid in props:
        for link in ctx.links(pid):
            rel = link["relation"]
            if rel in ("implies", "and", "or") and link["to"] in props:
                links.append({"id": link["id"], "relation": rel, "from": pid, "to": link["to"]})
            elif rel == "then" and link["to"] in props:
                links.append({"id": link["id"], "relation": rel, "from": pid, "to": link["to"],
                              "step": link["stated"]["step"]})
            elif rel == "quoted_by":
                q = _value_or_reason(ctx, "quoted", link["id"])
                links.append({"id": link["id"], "relation": rel, "from": pid, "to": link["to"],
                              "quote": {k: q["output"][k] for k in ("bid", "ask")} if q["output"] else None,
                              "basis": q.get("basis"), **({"reason": q["reason"]} if q.get("reason") else {})})
    return {"component": props, "links": links}


def _groups(links: list[dict]) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Each composite of and or or, with the relation, to its (link, component) pairs."""
    out: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for link in links:
        if link["relation"] in ("and", "or"):
            out.setdefault((link["from"], link["relation"]), []).append((link["id"], link["to"]))
    return out


def _composite(relation: str, lows: list[float], highs: list[float]) -> dict:
    """A composite's bounds from its components': and, lo = max(0, sum of lo - (n - 1)), hi = min of hi; or,
    lo = max of lo, hi = min(1, sum of hi)."""
    if relation == "and":
        return {"lo": max(0.0, sum(lows) - (len(lows) - 1)), "hi": min(highs)}
    return {"lo": max(lows), "hi": min(1.0, sum(highs))}


def solve(props: list[str], links: list[dict]):
    """The interval's rules (MATH.md, propagation) to a fixed point from [0, 1]; each bound with the links that set
    it."""
    lo = dict.fromkeys(props, 0.0)
    hi = dict.fromkeys(props, 1.0)
    src_lo = {p: frozenset() for p in props}
    src_hi = {p: frozenset() for p in props}
    groups = _groups(links)

    def up(p, value, src):
        if value > lo[p] + TOL:
            lo[p], src_lo[p] = value, frozenset(src)
            return True
        return False

    def down(p, value, src):
        if value < hi[p] - TOL:
            hi[p], src_hi[p] = value, frozenset(src)
            return True
        return False

    def implication(a, b, lid):  # a implies b: a's ceiling is b's, b's floor is a's
        return down(a, hi[b], {lid} | src_hi[b]) | up(b, lo[a], {lid} | src_lo[a])

    for _ in range(ROUNDS):
        changed = False
        for link in links:
            rel, lid, a, b = link["relation"], link["id"], link["from"], link["to"]
            if rel == "quoted_by":
                if link["quote"]:
                    if link["quote"]["bid"] is not None:
                        changed |= up(a, link["quote"]["bid"], {lid})
                    if link["quote"]["ask"] is not None:
                        changed |= down(a, link["quote"]["ask"], {lid})
            elif rel in ("implies", "and", "then"):
                changed |= implication(a, b, lid)
            elif rel == "or":
                changed |= implication(b, a, lid)
        for (a, relation), parts in groups.items():
            comps = [c for _, c in parts]
            ids = {lid for lid, _ in parts}
            if relation == "and":  # the floor an and's components give it; its ceiling comes through implies
                value = _composite("and", [lo[c] for c in comps], [hi[c] for c in comps])["lo"]
                if value > 0:
                    changed |= up(a, value, ids.union(*(src_lo[c] for c in comps)))
            else:  # the ceiling an or's components give it; its floor comes through implies
                value = _composite("or", [lo[c] for c in comps], [hi[c] for c in comps])["hi"]
                changed |= down(a, value, ids.union(*(src_hi[c] for c in comps)))
        if not changed:
            break
    return lo, hi, src_lo, src_hi


def _propagate_compute(m: dict, load) -> dict:
    props, links = m["component"], m["links"]
    lo, hi, src_lo, src_hi = solve(props, links)
    nodes = {}
    for p in props:
        sides = {"lo_from": sorted(src_lo[p], key=_seq), "hi_from": sorted(src_hi[p], key=_seq)}
        if lo[p] > hi[p] + TOL:  # crossed: no interval, and what set each side
            nodes[p] = {"lo": None, "hi": None, "coverage": None, "crossed": {"lo": lo[p], "hi": hi[p], **sides}}
            continue
        low = min(lo[p], hi[p])
        nodes[p] = {"lo": low, "hi": hi[p], "coverage": 1.0 - (hi[p] - low), **sides}
    edges = {}
    for link in links:
        rel, a, b = link["relation"], link["from"], link["to"]
        if rel == "quoted_by":
            q = link["quote"] or {}
            edges[link["id"]] = {"bound": {"lo": q.get("bid"), "hi": q.get("ask")}, "on": a,
                                 **({"reason": link["reason"]} if link.get("reason") else {})}
        else:  # A implies B, a composite of and or then implies its component, a component of or its composite
            x, y = (b, a) if rel == "or" else (a, b)
            edges[link["id"]] = {"bound": {"implying": x, "implied": y, "ceiling": hi[y], "floor": lo[x]}}
    for (a, relation), comps in _groups(links).items():  # the composite's own rule over its components
        rule = _composite(relation, [lo[c] for _, c in comps], [hi[c] for _, c in comps])
        for lid, _ in comps:
            edges[lid]["bound"]["composite"] = {"of": a, "relation": relation, **rule}
    return {"nodes": nodes, "edges": edges}


def _no_interval(node: dict) -> str:
    c = node["crossed"]
    return (f"its bounds cross: at least {c['lo']:.4g}, set by {', '.join(c['lo_from'])}, and at most {c['hi']:.4g}, "
            f"set by {', '.join(c['hi_from'])}; a claim or a market among them is wrong")


def _pick_node(out: dict, pid: str) -> dict:
    node = out["nodes"][pid]
    if node.get("crossed"):
        return {}
    return {k: node[k] for k in ("lo", "hi", "coverage")}


def _pick_link(out: dict, lid: str) -> dict:
    found = out["edges"].get(lid)
    return {"bound": found["bound"]} if found else {}


def _crossed(out: dict, pid: str) -> dict:
    node = out["nodes"].get(pid) or {}
    return {f: _no_interval(node) for f in ("lo", "hi", "coverage")} if node.get("crossed") else {}


PROPAGATE = Derivation(
    "propagate", 2, "Proposition", _propagate_inputs, _propagate_compute,
    fields=("lo", "hi", "coverage"), subject_of=_component_of, pick=_pick_node,
    extra=tuple((rel, ("bound",), _pick_link) for rel in ("quoted_by", "implies", "and", "or", "then")),
    missing=_crossed,
)


DERIVATIONS = (QUOTED, PROPAGATE)
