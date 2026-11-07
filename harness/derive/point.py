"""The point of a watched proposition, or of one a derivation reads (MATH.md, the point).

A proposition's point is the price of a market that asks it; otherwise it is the analyst's derivation of it,
evaluated by its row of the table (harness.derive.logic), each proposition it reads at its own point; otherwise, where
nothing reaches it, the chance the analyst states. Where several markets ask it, the point is the price of the one with
the most depth. A market the derivation does not read bounds the proposition and forecasts nothing of its own. Nothing
clamps the point: one outside the interval its markets allow means an input is wrong, and check() lists it. A
derivation whose arithmetic leaves 0 to 1 gives no point, with the reason. The point is derived for each watched
proposition and each proposition a derivation reads.
"""

from __future__ import annotations

from harness.derive import logic
from harness.derive.engine import Derivation, Inputs, Unavailable
from harness.derive.propagate import ctx_links

TOL = 1e-12


def _markets(ctx: Inputs, pid: str) -> list[dict]:
    out = []
    for link in ctx.links(pid, "quoted_by"):
        try:
            q = ctx.value("quoted", link["id"])
        except Unavailable as e:
            out.append({"link": link["id"], "market": link["to"], "price": None, "reason": str(e)})
            continue
        found = q["output"]
        out.append({"link": link["id"], "market": link["to"], "price": found["price"], "depth": found.get("depth"),
                    "basis": q["basis"], **({} if found["price"] is not None else
                                            {"reason": "the market shows no price: its book is wide or empty and it "
                                                       "has not traded"})})
    return out


def _point_inputs(ctx: Inputs, pid: str) -> dict:
    markets = _markets(ctx, pid)
    priced = [m for m in markets if m["price"] is not None]
    if priced:
        best = max(priced, key=lambda m: (m["depth"] if m["depth"] is not None else -1.0, -priced.index(m)))
        return {"proposition": pid, "rule": "market", "market": best, "markets": markets}
    stated = ctx.object(pid)["stated"]
    d = stated.get("derivation")
    if d is not None:
        try:
            found = logic.match(ctx_links(ctx), pid, d)
        except logic.NoRule as e:
            raise Unavailable(str(e)) from None
        points = {}
        for r in d["reads"]:
            try:
                got = ctx.value("point", r)
            except Unavailable as e:
                raise Unavailable(f"{pid}'s derivation reads {r}, which has no point: {e}") from None
            points[r] = {"p": got["output"]["p"], "basis": got["basis"]}
        return {"proposition": pid, "rule": "derivation", "derivation": d, "row": found["row"],
                "roles": found["roles"], "by_role": found["chances"], "points": points, "markets": markets}
    if stated.get("chance") is not None:
        return {"proposition": pid, "rule": "chance", "chance": stated["chance"], "markets": markets}
    why = "; ".join(m["reason"] for m in markets if m.get("reason"))
    raise Unavailable(f"nothing gives {pid} a point: " + (f"the market that asks it has no price ({why}), and " if why
                                                          else "no market asks it, and ")
                      + "it states no derivation or chance")


def _point_compute(m: dict, load) -> dict:
    rule = m["rule"]
    out = {"rule": rule, "markets": m["markets"]}
    if rule == "market":
        return {**out, "p": m["market"]["price"], "market": m["market"]["market"], "link": m["market"]["link"]}
    if rule == "chance":
        return {**out, "p": m["chance"]["value"], "chance": m["chance"]}
    found = {"row": m["row"], "roles": m["roles"], "chances": m["by_role"]}
    p = logic.evaluate(found, {r: v["p"] for r, v in m["points"].items()})
    out.update(row=m["row"], formula=logic.ROWS[m["row"]], roles=m["roles"],
               reads={r: v["p"] for r, v in m["points"].items()}, chances=m["by_role"])
    if not -TOL <= p <= 1.0 + TOL:
        return {**out, "value": p, "unavailable": {"p": f"the derivation gives {p:.4g}, which is not a chance: its "
                                                         f"stated chances contradict the points it reads"}}
    return {**out, "p": min(max(p, 0.0), 1.0)}  # within rounding of 0 to 1


def pointed(store, pid: str) -> bool:
    """A point is derived for a watched proposition and for one a derivation reads."""
    return store.watched(pid) or store.read_by_derivation(pid)


POINT = Derivation("point", 5, "Proposition", _point_inputs, _point_compute, fields=("p",), applies=pointed)

DERIVATIONS = (POINT,)
