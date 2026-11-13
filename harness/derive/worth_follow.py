"""A chance that follows the weight a holding's price implies on an event (MATH.md 1.2, 1.6).

The weight a holding's price implies is `(c_h - N) / (Y - N)`, its stated values averaged over each side at the weights
today's prices carry (1.6). Cash stays as stated in a world; shares of another name, cash in another currency, and the
company on its own stand where that world's prices stand, which the weights place (1.5). So the values are read in two
steps, as the impact reads every price: what each world does against its baseline, which no probability enters
(`unplaced`, through impact.Moves, the impact's own steps 2 and 3), and its placing at the weights, by arithmetic
(`values_at`). A chance that follows the holding places the same moves at the weights its structure's candidate
probabilities give, so the event's impact is never read at a candidate probability, and values of cash alone read
nothing of the portfolio's exposures.
"""
from __future__ import annotations

import math

from harness.derive import episode, impact, outcomes, portfolio, structure, valuation, worlds
from harness.derive.channels import ordered_channels
from harness.derive.engine import Derivation, Inputs, Refused, Unavailable


class _At:
    """Read every component of a historical mark from what the store then held."""

    def __init__(self, ctx, at):
        self.ctx, self.at = ctx, at

    def __getattr__(self, name):
        return getattr(self.ctx, name)

    def data(self, kind, subject, **kw):
        return self.ctx.data(kind, subject, **{**kw, "at": self.at})


def _security(ctx: Inputs, nid: str) -> str:
    """The security a holding is priced through: the one the portfolio holds it by, else its own listing."""
    held = next((h["stated"].get("ticker") for h in ctx.links("portfolio", "holds") if h["to"] == nid), None)
    return held or portfolio.ticker_of(nid)


def subject(event: str, name: str) -> str:
    return f"{event}|{name}"


def _ways(ctx: Inputs, pid: str) -> list[dict]:
    return outcomes.ways_under(lambda oid: ctx.links(oid, "way", "both"), pid, lambda r: structure.bands(ctx, r))


def _unplaced_inputs(ctx: Inputs, key: str) -> dict:
    """The holding's stated values in the event's worlds, each with what it reads (valuation.worth_inputs), and, where
    a value moves with that world's prices, what the event's worlds do against its baseline: its shocks, the factors'
    covariance, its episodes, and the betas of the names its values hold."""
    pid, nid = key.split("|", 1)
    way_nodes = _ways(ctx, pid)
    ways = [w["id"] for w in way_nodes]
    shape = outcomes.shape(ctx, pid, way_nodes)
    items = [i for i in valuation.worth_inputs(ctx, pid, ways, {nid: {"security": _security(ctx, nid)}})
             if i["target"] == nid]
    if not items:
        raise Unavailable(f"{nid} has no value stated in {pid}'s worlds")
    m = {"event": pid, "name": nid, "shape": {"nodes": shape["nodes"], "worlds": shape["worlds"]}, "worth": items}
    if not any("shares" in i["value"] or "return" in i["value"] or i.get("fx") for i in items):
        return m  # cash and amounts alone: nothing they are worth moves with the world's prices
    baseline = ctx.object(pid)["stated"].get("baseline")
    if baseline is None:
        raise Unavailable(f"{pid} names no baseline, the world its other worlds are measured from")
    channels = [c["id"] for c in ordered_channels(ctx)]
    fits = {c: ctx.value("orthogonalize", c)["output"] for c in channels}
    m.update(baseline=baseline, channels=channels, yields=impact.quoted_yields(ctx),
             coefficients={c: fits[c]["coefficients"] for c in channels},
             variances={c: fits[c]["variance"] for c in channels},
             consequences=[{"link": link["id"], "on": on, "target": link["to"], **impact.stated_move(link)}
                           for on in (pid, *ways) for link in impact.consequences(ctx, on)],
             episodes=episode.for_worlds(ctx, pid, shape))
    if any("shares" in i["value"] or i.get("fx") for i in items):  # a name its values hold moves by its betas
        m["betas"] = ctx.value("betas", "portfolio", ref=True)
    return m


def _unplaced_compute(m: dict, load) -> dict:
    """Each world's value of the holding in its parts (MATH.md 1.6): a fixed amount in today's dollars (an amount, and cash
    in dollars discounted at the curve), each amount that moves with another name's price there (shares, cash in
    another currency at its rate), and the company on its own, carried from its date, with its betas; and the moves
    against the baseline those parts read: each such name's, and each factor's."""
    nodes, W = m["shape"]["nodes"], m["shape"]["worlds"]
    ws = [{"id": w, "side": nodes[w]["side"], "parent": nodes[w]["parent"]} for w in W]
    tree = {n: {"side": v["side"], "parent": v["parent"], "ways": v["ways"]} for n, v in nodes.items() if v["ways"]}
    claims = valuation._worth_worlds({"event": m["event"], "worlds": ws, "tree": nodes, "worth": m["worth"]})[m["name"]]
    values: dict[str, dict] = {}
    for w, item in claims.items():
        value = item["value"]
        terms = {"fixed": float(value.get("amount", 0.0)), "placed": [], "anchor": None, "link": item["link"]}
        if "cash" in value:
            t = valuation.years(item["asof"], value["date"])
            pv = float(value["cash"]) * (valuation.discount_at(item["curves"], load, item["asof"], t) if t > 0 else 1.0)
            if item.get("fx"):
                if item["fx"]["c"] is None:
                    raise Unavailable(f"the payment needs the rate of {value.get('currency')} in dollars")
                terms["placed"].append({"name": item["fx"]["name"], "amount": pv * item["fx"]["c"]})
            else:
                terms["fixed"] += pv
        if "shares" in value:
            if item["share_price"]["c"] is None:
                raise Unavailable(f"{value['shares']['name']}'s price is needed for the shares paid")
            terms["placed"].append({"name": value["shares"]["name"],
                                    "amount": value["shares"]["quantity"] * item["share_price"]["c"]})
        if "return" in value:
            fit = load(item["anchor"])
            terms["anchor"] = {"amount": fit["carried"] * (1 + value["return"]), "raw_betas": fit["raw_betas"]}
        values[w] = terms
    out = {"event": m["event"], "name": m["name"], "worlds": ws, "tree": tree, "values": values, "names": {},
           "factors": {}}
    if "channels" in m:
        mv = impact.Moves({**m, "worlds": ws, "tree": tree}, load(m["betas"]) if m.get("betas") else {})
        for n in sorted({p["name"] for t in values.values() for p in t["placed"]}):
            out["names"][n] = mv.against(n)
        if any(t["anchor"] for t in values.values()):
            out["factors"] = {c: {w: math.exp(mv.logs[w][c]) for w in W} for c in mv.ids}
    return out


UNPLACED = Derivation("unplaced", 3, "Proposition", _unplaced_inputs, _unplaced_compute,
                      applies=lambda store, key: False)
DERIVATIONS = (UNPLACED,)


def values_at(u: dict, q: dict[str, float]) -> dict[str, float]:
    """The holding's value in each world at the weights q (MATH.md 1.5, 1.6): each moving amount at its name's price
    there, placed, `r_w / sum_v q_v r_v`, and the company on its own by its betas on the factors' placed moves."""
    def placed(r: dict[str, float]) -> dict[str, float]:
        total = sum(q[v] * r[v] for v in r)
        if total <= 0:
            raise Unavailable("a price the holding's values read stands at zero in every world today's prices carry")
        return {w: r[w] / total for w in r}

    names = {n: placed(r) for n, r in u["names"].items()}
    factors = {c: placed(r) for c, r in u["factors"].items()}
    out = {}
    for w, t in u["values"].items():
        y = t["fixed"] + sum(p["amount"] * names[p["name"]][w] for p in t["placed"])
        if t["anchor"]:
            y += t["anchor"]["amount"] * valuation.raw_response(t["anchor"]["raw_betas"],
                                                                {c: factors[c][w] - 1.0 for c in factors})
        out[w] = y
    return out


def follow_inputs(ctx: Inputs, pid: str, nid: str, at: str | None) -> dict:
    """What a chance that follows a holding's implied weight reads: the holding's values, unplaced; the weights stated
    for the event and its ways; and the holding's price when the chance was stated and now."""
    ticker = _security(ctx, nid)
    asof = portfolio.asof_of(ctx)
    then_asof = min(asof, portfolio.read_date(at)) if at else asof
    ways = [w["id"] for w in _ways(ctx, pid)]
    weights = {("yes" if x == pid else x): got for x in (pid, *ways)
               if (got := impact._weight(ctx, x, ctx.object(x)["stated"].get("priced_in"))) is not None}
    return {"event": pid, "name": nid, "unplaced": ctx.value("unplaced", subject(pid, nid), ref=True),
            "priced_in": weights, "then": portfolio.dollar_inputs(_At(ctx, at), ticker, then_asof, at=at),
            "now": portfolio.dollar_inputs(ctx, ticker, asof)}


def _mark(m: dict, load, nid: str) -> dict:
    found = portfolio.dollar_compute(m, load)
    if not found or found.get("price") is None:
        raise Unavailable(f"{nid}'s observed price is needed to follow it")
    return {"price": found["price"], "at": found.get("time") or found.get("session")}


def follow_value(manifest: dict, load, props: dict, *, responsiveness: float = 1.0) -> dict:
    """The weight the holding's price implies when the chance was stated and now, both read through the values stated
    now (MATH.md 1.2), at the probabilities `props` gives the event's worlds; or why it has none. A responsiveness of 0
    keeps the stated chance whatever the price implies, so then nothing waits on it."""
    u, nid = load(manifest["unplaced"]), manifest["name"]
    event = u["event"]

    def p_of(w: str) -> float | None:
        p = (props.get(event) or {}).get("p")
        if w in ("yes", "no"):
            return None if p is None else (p if w == "yes" else 1.0 - p)
        return (props.get(w) or {}).get("p")

    ws = [{**w, "p": p_of(w["id"])} for w in u["worlds"]]
    then, now = _mark(manifest["then"], load, nid), _mark(manifest["now"], load, nid)
    open_ = [w["id"] for w in ws if w["p"] is None]
    moving = bool(u["names"] or u["factors"])
    sides = {s: [w["id"] for w in ws if w["side"] == s] for s in ("yes", "no")}
    fixed = {w: t["fixed"] for w, t in u["values"].items()}
    if open_ and (moving or any(max(fixed[w] for w in ids) - min(fixed[w] for w in ids) > 1e-12
                                for ids in sides.values())):
        why = "the holding's values in the event's worlds need their probabilities"
        return {"then": {**then, "q": None}, "now": {**now, "q": None}, "why": why}
    if open_:  # each side's values are one figure: its mean needs no share of its ways
        means = {s: fixed[ids[0]] for s, ids in sides.items()}
        spread = means["yes"] - means["no"]
        implied = lambda price: (price - means["no"]) / spread if abs(spread) > 1e-12 else None  # noqa: E731
        levels = fixed
    else:
        try:
            q = worlds.weights(ws, u["tree"], {n: v["q"] for n, v in manifest["priced_in"].items()})
        except worlds.Unholdable as e:
            raise Refused(f"{nid}'s price cannot be followed: {e}", (event, nid)) from None
        levels = values_at(u, q)
        implied = lambda price: valuation.implied_from_values(levels, price, ws, q)  # noqa: E731
    readings = {"then": {**then, "q": implied(then["price"])}, "now": {**now, "q": implied(now["price"])}}
    out: dict = {**readings, "values": levels}
    for key, reading in readings.items():
        q_ = reading["q"]
        if q_ is not None and not -1e-10 <= q_ <= 1 + 1e-10:  # values that contradict the price: check() lists them
            reading["q"] = None
            out["why"] = (f"{nid}'s price {key} lies outside the values stated in {event}'s worlds, so it implies no "
                          f"weight on {event}")
            out["outside"] = True
        elif q_ is not None:
            reading["q"] = min(1.0, max(0.0, q_))
    if readings["now"]["q"] is None and "why" not in out:
        out["why"] = "the holding has the same value on both sides"
    if responsiveness == 0:  # a responsiveness of 0 keeps the stated chance whatever the price implies
        out.pop("why", None)
        out.pop("outside", None)
    return out
